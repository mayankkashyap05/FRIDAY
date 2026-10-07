"""Behavioral tests for Friday's identity, conciseness, clarification, error handling, and confirmation."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from friday_os.actions import WindowsActions, format_action_error
from friday_os.app import FridayApp
from friday_os.assistant import (
     SYSTEM_PROMPT,
    VOICE_RESPONSE_PROMPT,
    AssistantController,
    ConversationStore,
)
from friday_os.commands import Command, Risk
from friday_os.router import CommandRouter, strip_wake_name
from friday_os.screen import VISION_SYSTEM_PROMPT
from friday_os.security import AuditLog, SecureExecutor
from friday_os.speech import EDGE_VOICES, PIPER_VOICES, SentenceBuffer, prepare_for_speech
from friday_os.storage import Database, PermissionRepository, SettingsRepository


class PersonaCleanupTests(unittest.TestCase):
    def test_system_prompt_identifies_as_friday_and_forbids_theatrical_persona(self):
        self.assertIn("You are Friday", SYSTEM_PROMPT)
        self.assertNotIn("You are F.R.I.D.A.Y", SYSTEM_PROMPT)
        for forbidden in ("Jarvis", "Iron Man", "Marvel", "Tony Stark", "protocol", "stasis", "commander", "sir"):
            self.assertIn(forbidden, SYSTEM_PROMPT)

    def test_voice_and_vision_prompts_are_concise_and_question_focused(self):
        self.assertIn("live spoken conversation", VOICE_RESPONSE_PROMPT)
        self.assertIn("fewest words", VOICE_RESPONSE_PROMPT)
        self.assertIn("Do not repeat the user's question", VOICE_RESPONSE_PROMPT)
        self.assertIn("You are Friday", VISION_SYSTEM_PROMPT)
        self.assertNotIn("F.R.I.D.A.Y", VISION_SYSTEM_PROMPT)
        self.assertIn("do not describe the entire screen", VISION_SYSTEM_PROMPT)

    def test_router_strips_friday_wake_word_but_not_jarvis(self):
        router = CommandRouter()
        self.assertEqual(router.route("Friday, open Notepad").action, "open_app")
        self.assertEqual(router.route("Hey Friday, open Notepad").action, "open_app")
        self.assertEqual(strip_wake_name("Jarvis, open Notepad"), "Jarvis, open Notepad")
        self.assertEqual(router.route("Jarvis, open Notepad").action, "chat")
        self.assertEqual(router.route("J.A.R.V.I.S. open Notepad").action, "chat")

    def test_speech_preparation_has_no_jarvis_hack_and_normalizes_friday(self):
        self.assertEqual(prepare_for_speech("F.R.I.D.A.Y is ready"), "Friday is ready.")
        self.assertEqual(prepare_for_speech("Jarvis is a fictional character"), "Jarvis is a fictional character.")
        for description in (*EDGE_VOICES.values(), *PIPER_VOICES.values()):
            self.assertNotIn("F.R.I.D.A.Y character", description)


class ConcisenessAndPipelineTests(unittest.TestCase):
    def test_short_complete_responses_speak_immediately(self):
        for short_reply in ("Done.", "Yes.", "No.", "12.", "Playing.", "Opened."):
            buffer = SentenceBuffer()
            self.assertEqual(buffer.push(short_reply), [short_reply], short_reply)

    def test_filler_openers_still_join_following_sentence(self):
        buffer = SentenceBuffer()
        self.assertEqual(buffer.push("Sure. "), [])
        self.assertEqual(
            buffer.push("Here is the explanation. "),
            ["Sure. Here is the explanation."],
        )

    def test_active_context_window_is_compact_while_full_history_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "friday.db")
            settings_repo = SettingsRepository(db)
            store = ConversationStore(root / "conversation.db")
            for idx in range(12):
                store.append("user", f"question {idx}")
                store.append("assistant", f"answer {idx}")
            provider = Mock()
            provider.reply.return_value = "Short answer."
            executor = Mock()
            controller = AssistantController(executor, store, provider, settings_repo=settings_repo)
            self.assertEqual(controller._memory_limit(), 10)
            controller.process("final question")
            sent_messages = provider.reply.call_args.args[0]
            non_system = [m for m in sent_messages if m["role"] != "system"]
            self.assertEqual(len(non_system), 10)
            self.assertEqual(len(store.recent(limit=100)), 26)

    def test_concise_action_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / "notes.txt"
            target.write_text("hello", encoding="utf-8")
            actions = WindowsActions(home=home, data_dir=home)
            with patch("send2trash.send2trash"):
                deleted = actions.delete_path({"path": "notes.txt"})
            self.assertTrue(deleted.success)
            self.assertEqual(deleted.message, "Deleted notes.txt.")

            with patch("friday_os.actions.os.startfile"):
                opened = actions.open_app({"name": "google chrome"})
            self.assertTrue(opened.success)
            self.assertEqual(opened.message, "Opened Chrome.")


class IntentClarificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = ConversationStore(root / "conversation.db")
        self.provider = Mock()
        self.executor = Mock()
        self.controller = AssistantController(self.executor, self.store, self.provider)

    def tearDown(self):
        self.temp.cleanup()

    def test_incomplete_delete_asks_which_file(self):
        reply = self.controller.process("Delete the file.")
        self.assertEqual(reply.text, "Which file do you want to delete?")
        self.provider.reply.assert_not_called()
        self.executor.execute.assert_not_called()

    def test_incomplete_email_asks_who_to_send_to(self):
        reply = self.controller.process("Send an email.")
        self.assertEqual(reply.text, "Who should I send it to?")
        self.provider.reply.assert_not_called()
        self.executor.execute.assert_not_called()

    def test_incomplete_open_asks_which_app(self):
        for phrase in ("Can you open it?", "Open the app.", "Open"):
            reply = self.controller.process(phrase)
            self.assertEqual(reply.text, "Which app?", phrase)
        self.provider.reply.assert_not_called()
        self.executor.execute.assert_not_called()

    def test_incomplete_find_and_reminder_ask_for_details(self):
        self.assertEqual(self.controller.process("Find the file.").text, "Which file are you looking for?")
        self.assertEqual(self.controller.process("Remind me").text, "What should I remind you about, and when?")
        self.provider.reply.assert_not_called()


class CleanErrorHandlingTests(unittest.TestCase):
    def test_winerror_file_not_found_is_humanized(self):
        win_err = OSError(2, "The system cannot find the file specified")
        msg = format_action_error("open_app", win_err, {"name": "google chrome"})
        self.assertEqual(msg, "Couldn't open Chrome.")
        self.assertNotIn("WinError", msg)

    def test_open_app_catches_oserror_cleanly(self):
        actions = WindowsActions()
        with patch("friday_os.actions.os.startfile", side_effect=FileNotFoundError("[WinError 2] The system cannot find the file specified")):
            result = actions.open_app({"name": "chrome"})
        self.assertFalse(result.success)
        self.assertEqual(result.message, "Couldn't open Chrome.")

    def test_permission_error_is_humanized(self):
        err = PermissionError("[WinError 5] Access is denied")
        self.assertEqual(format_action_error("delete_path", err, {"path": "secret.txt"}), "Permission denied for that action.")


class ConfirmationAndUxTests(unittest.TestCase):
    def test_high_and_medium_risk_actions_require_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "friday.db")
            perms = PermissionRepository(db)
            audit = AuditLog(root / "friday.db")
            backend = Mock()
            confirm = Mock(return_value=False)
            executor = SecureExecutor(backend, audit, confirm, perms)

            result = executor.execute(Command("delete_path", {"path": "notes.txt"}, Risk.HIGH))
            self.assertFalse(result.success)
            confirm.assert_called_once()
            backend.execute.assert_not_called()
            self.assertTrue(audit.verify())

    def test_ui_uses_friday_branding_ready_state_and_ctrl_alt_s(self):
        source = inspect.getsource(FridayApp)
        self.assertIn('self.add_message("Friday", "Ready.")', source)
        self.assertNotIn("Systems online", source)
        self.assertIn("<ctrl>+<alt>+s", source)
        self.assertNotIn("<ctrl>+<alt>+j", source)
        self.assertIn("friday.ico", source)
        self.assertNotIn("jarvis.ico", source)


if __name__ == "__main__":
    unittest.main()
