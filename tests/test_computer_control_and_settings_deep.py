"""Offline deep tests for computer_control / computer_settings upgrades."""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from actions import computer_control as cc
from actions import computer_settings as cs
from safety import Risk, classify


class FakeClipboard:
    def __init__(self, initial="original"):
        self.value = initial
        self.copies = []

    def paste(self):
        return self.value

    def copy(self, value):
        self.copies.append(value)
        self.value = value


@pytest.fixture
def fake_pg(monkeypatch):
    pg = MagicMock()
    pg.size.return_value = (1000, 500)
    pg.position.return_value = (12, 34)
    monkeypatch.setattr(cc, "_PG_MOD", pg)
    monkeypatch.setattr(cs, "_PG_MOD", pg)
    return pg


# ─────────────────────────── computer_control ────────────────────────────

def test_needs_paste_unicode_newline_and_long_text():
    assert cc._needs_paste("नमस्ते")
    assert cc._needs_paste("line1\nline2")
    assert cc._needs_paste("x" * 21)
    assert not cc._needs_paste("short ascii")


def test_paste_with_restore_restores_clipboard_in_finally(monkeypatch):
    clip = FakeClipboard("keep me")
    monkeypatch.setattr(cc, "pyperclip", clip, raising=False)
    monkeypatch.setattr(cc, "_PYPERCLIP", True)
    keys = []
    monkeypatch.setattr(cc, "_hotkey", lambda *k: keys.append(k))

    result = cc._paste_with_restore("नमस्ते दुनिया")

    assert "Pasted" in result
    assert clip.copies[0] == "नमस्ते दुनिया"
    assert keys == [("ctrl", "v")]
    assert clip.value == "keep me"


def test_paste_restores_even_when_paste_raises(monkeypatch):
    clip = FakeClipboard("keep me")
    monkeypatch.setattr(cc, "pyperclip", clip, raising=False)
    monkeypatch.setattr(cc, "_PYPERCLIP", True)
    monkeypatch.setattr(cc, "_hotkey", lambda *k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        cc._paste_with_restore("hello")
    assert clip.value == "keep me"


def test_type_text_uses_paste_for_unicode_and_typewrite_for_ascii(fake_pg, monkeypatch):
    calls = []
    monkeypatch.setattr(cc, "_PYPERCLIP", True)
    monkeypatch.setattr(cc, "_paste_with_restore", lambda text: calls.append(("paste", text)) or "ok")
    cc._type_text("こんにちは")
    cc._type_text("hi")
    assert calls == [("paste", "こんにちは")]
    fake_pg.typewrite.assert_called_once_with("hi", interval=0.03)


def test_parse_grounding_json_normalised_and_legacy_pixels():
    assert cc._parse_grounding_response('{"x": 500, "y": 250}', 1000, 500) == (500, 125)
    assert cc._parse_grounding_response('{"point": [100, 400]}', 1000, 500) == (100, 200)
    assert cc._parse_grounding_response("120, 80", 1000, 500) == (120, 80)
    assert cc._parse_grounding_response('{"found": false}', 1000, 500) is None
    assert cc._parse_grounding_response("NOT_FOUND", 1000, 500) is None
    assert cc._parse_grounding_response('{"x": 5000, "y": -10}', 1000, 500) == (999, 0)


def test_screen_grounding_uses_generate_with_retry_and_flash_lite(fake_pg, monkeypatch):
    import api_keys
    from config.models import MODEL_FLASH_LITE

    captured = {}

    def fake_generate(model, contents, config=None):
        captured.update(model=model, contents=contents, config=config)
        return MagicMock(text='{"x": 250, "y": 500}')

    monkeypatch.setattr(api_keys, "generate_with_retry", fake_generate)
    image = MagicMock()
    image.save = lambda buffer, format: buffer.write(b"PNGDATA")
    fake_pg.screenshot.return_value = image

    assert cc._analyze_screen_for_element("the OK button") == (250, 250)
    assert captured["model"] == MODEL_FLASH_LITE
    assert "inline_data" in captured["contents"][0]


def test_hotkey_aliases_and_platform_normalisation(monkeypatch):
    monkeypatch.setattr(cc.platform, "system", lambda: "Linux")
    assert cc._normalise_hotkey("cmd+return") == ("super", "enter")
    monkeypatch.setattr(cc.platform, "system", lambda: "Windows")
    assert cc._normalise_hotkey(["control", "escape"]) == ("ctrl", "esc")


def test_wait_mouse_and_drag_are_clamped(fake_pg, monkeypatch):
    slept = []
    monkeypatch.setattr(cc.time, "sleep", lambda s: slept.append(s))
    cc._wait(999)
    cc._wait(-5)
    assert slept == [60.0, 0.0]
    cc._move_mouse(5000, -20, duration=99)
    fake_pg.moveTo.assert_called_with(999, 0, duration=5.0)
    cc._drag(5000, 1, -1, 5000, duration=-3)
    fake_pg.dragTo.assert_called_with(0, 499, duration=0.0)


def test_new_computer_control_actions_dispatch(fake_pg, monkeypatch):
    seen = []
    monkeypatch.setattr(cc, "_click", lambda **kw: seen.append(("click", kw)) or "clicked")
    monkeypatch.setattr(cc, "_mouse_pos", lambda: "Mouse position: (1, 2)")
    monkeypatch.setattr(cc, "_active_window", lambda: "Active window: Notepad")
    monkeypatch.setattr(cc, "_window_action", lambda action, title: f"{action}:{title}")
    monkeypatch.setattr(cc, "_wait_for_image", lambda path, timeout: "found")
    monkeypatch.setattr(cc, "_analyze_screen_for_element", lambda desc: None)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)

    assert "clicked" == cc.computer_control({"action": "triple_click", "x": 5, "y": 6})
    assert seen[-1][1]["clicks"] == 3
    cc.computer_control({"action": "middle_click"})
    assert seen[-1][1]["button"] == "middle"
    assert "1, 2" in cc.computer_control({"action": "mouse_pos"})
    assert "Notepad" in cc.computer_control({"action": "active_window"})
    assert "minimize:Chrome" in cc.computer_control(
        {"action": "window_action", "mode": "minimize", "title": "Chrome"})
    cc.computer_control({"action": "screen_click", "description": "OK", "clear_first": "false"})
    assert cc.computer_control(None) != ""


def test_random_password_uses_secrets_and_indian_locale(monkeypatch):
    calls = []

    def choice(seq):
        calls.append(seq)
        return seq[0]

    monkeypatch.setattr(cc, "_cryptographic_choice", choice)
    monkeypatch.setattr(cc.secrets, "randbelow", lambda n: 0)
    plain = cc.generate_random_data("password")
    india = cc.generate_random_data("password", "IN")
    assert len(plain) == 12 and len(india) == 16
    assert "₹" in "".join(str(c) for c in calls)
    assert all(call for call in calls)


# ─────────────────────────── computer_settings ───────────────────────────

class FakeVolume:
    def __init__(self, level=0.5, muted=False):
        self.level, self.muted = level, muted
        self.scalar_calls = []
        self.mute_calls = []

    def SetMasterVolumeLevelScalar(self, value, unused):
        self.scalar_calls.append(value)
        self.level = value

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMute(self, value, unused):
        self.mute_calls.append(value)
        self.muted = bool(value)

    def GetMute(self):
        return self.muted


@pytest.fixture
def windows_volume(monkeypatch):
    vol = FakeVolume(0.42)
    monkeypatch.setattr(cs, "_OS", "Windows")
    monkeypatch.setattr(cs, "_master_volume", lambda: vol)
    return vol


def test_windows_scalar_volume_zero_is_true_silence(windows_volume):
    cs.volume_set(0)
    assert windows_volume.scalar_calls[-1] == 0.0
    cs.volume_set(75)
    assert windows_volume.scalar_calls[-1] == pytest.approx(0.75)
    assert cs.volume_get() == 75


def test_volume_up_down_get_exact_percentages(windows_volume, monkeypatch):
    monkeypatch.setattr(cs, "volume_set", lambda v: windows_volume.SetMasterVolumeLevelScalar(v / 100, None))
    cs.volume_up(10)
    assert windows_volume.level == pytest.approx(0.52)
    cs.volume_down(20)
    assert windows_volume.level == pytest.approx(0.32)


def test_explicit_mute_unmute_and_toggle(windows_volume):
    cs.mute()
    assert windows_volume.mute_calls[-1] == 1
    cs.unmute()
    assert windows_volume.mute_calls[-1] == 0
    cs.toggle_mute()
    assert windows_volume.mute_calls[-1] == 1


def test_multi_session_app_volume_and_unmute(monkeypatch):
    class Simple:
        def __init__(self):
            self.volumes = []
            self.mutes = []

        def SetMasterVolume(self, value, unused):
            self.volumes.append(value)

        def SetMute(self, value, unused):
            self.mutes.append(value)

    sessions = [MagicMock(SimpleAudioVolume=Simple(), Process=MagicMock()) for _ in range(2)]
    for session in sessions:
        session.Process.name.return_value = "Spotify.exe"
    monkeypatch.setattr(cs, "_OS", "Windows")
    fake_pycaw = ModuleType("pycaw.pycaw")
    fake_pycaw.AudioUtilities = MagicMock()
    fake_pycaw.AudioUtilities.GetAllSessions.return_value = sessions
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw)
    result = cs.app_volume("spotify", 30)
    assert "2 session" in result
    assert all(s.SimpleAudioVolume.volumes == [0.3] for s in sessions)
    cs.app_volume("spotify", None)
    assert all(s.SimpleAudioVolume.mutes == [0, 1] for s in sessions)
    cs.unmute_app("spotify")
    assert all(s.SimpleAudioVolume.mutes[-1] == 0 for s in sessions)
    assert "spotify.exe" in cs.list_audio_apps().lower()


def test_intent_detection_routes_through_generate_with_retry(monkeypatch):
    from config.models import MODEL_FLASH_LITE
    captured = {}

    def fake(model, contents, config=None):
        captured["model"] = model
        return MagicMock(text='{"action": "volume_set", "value": 0, "app_name": null}')

    monkeypatch.setattr(cs, "generate_with_retry", fake)
    detected = cs._detect_action("sesi sıfır yap")
    assert detected["value"] == 0
    assert captured["model"] == MODEL_FLASH_LITE


def test_settings_clamps_scroll_reload_and_string_bools(monkeypatch, fake_pg):
    reloads = []
    scrolls = []
    monkeypatch.setattr(cs, "refresh_page", lambda: reloads.append(1))
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    cs.reload_page_n("99")
    cs.reload_page_n(0)
    assert len(reloads) == 10 + 1
    cs.scroll_up(99999)
    assert fake_pg.scroll.call_args.args[0] == 5000


def test_settings_type_text_restores_clipboard(monkeypatch, fake_pg):
    clip = FakeClipboard("saved")
    monkeypatch.setattr(cs, "pyperclip", clip, raising=False)
    monkeypatch.setattr(cs, "_PYPERCLIP", True)
    calls = []
    monkeypatch.setattr(cs, "paste", lambda: calls.append("paste"))
    cs.type_text("नमस्ते")
    assert calls == ["paste"]
    assert clip.value == "saved"


def test_power_actions_and_safety_classifications(monkeypatch):
    commands = []
    monkeypatch.setattr(cs, "_OS", "Windows")
    monkeypatch.setattr(cs.subprocess, "run", lambda args, **kw: commands.append(args) or MagicMock(returncode=0))
    cs.cancel_shutdown()
    cs.sleep_pc()
    cs.hibernate()
    assert commands[0][:2] == ["shutdown", "/a"]
    assert "powrprof.dll" in " ".join(commands[1])
    assert commands[2][:2] == ["shutdown", "/h"]
    assert classify("computer_settings", {"action": "sleep_pc"}) == Risk.HIGH
    assert classify("computer_settings", {"action": "hibernate"}) == Risk.HIGH
