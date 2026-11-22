import ast
import os
import threading
import time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtGui', reason='Qt system libraries are required')
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication
import ui

_APP = None

def get_app():
    global _APP
    _APP = QCoreApplication.instance() or QApplication([])
    return _APP


def test_main_uses_native_ui():
    source = Path('main.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert 'ui' in imports
    assert Path(ui.__file__).resolve() == Path('ui/__init__.py').resolve()
    assert 'headless=args.headless' in source


def test_facade_worker_updates_and_shutdown(monkeypatch):
    get_app(); monkeypatch.setenv('FRIDAY_GEMINI_API_KEY', 'testing')
    ui_instance = ui.FridayUI(headless=True)
    seen = []; owner = threading.get_ident()
    ui_instance.bridge.log_added.connect(lambda event: seen.append((event.text, threading.get_ident())))
    t = threading.Thread(target=lambda: (ui_instance.write_log('You: hello'), ui_instance.set_state('THINKING')))
    t.start(); t.join()
    assert seen == []
    ui_instance._loop_entered = True; ui_instance._flush_pending_logs()
    for _ in range(10): get_app().processEvents()
    assert seen == [('You: hello', owner)]
    assert ui_instance.hud.state == 'working'
    ui_instance.root.after(0, ui_instance.quit)
    for _ in range(10): get_app().processEvents()
    assert ui_instance._closed.is_set()


def test_api_key_check_handles_malformed_values(tmp_path, monkeypatch):
    monkeypatch.delenv('FRIDAY_GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('FRIDAY_GEMINI_API_KEYS', raising=False)
    monkeypatch.setattr(ui, 'API_FILE', tmp_path/'keys.json')
    stub = object()
    for content, expected in [('{}', False), ('{"gemini_api_keys": null}', False), ('{"gemini_api_key":"abc"}', True), ('{"gemini_api_keys":["abc"]}', True), ('{bad', False)]:
        ui.API_FILE.write_text(content)
        assert ui.FridayUI._api_keys_exist(stub) is expected


def test_confirmation_is_bound_to_exact_action_and_single_use():
    import asyncio
    from unittest.mock import MagicMock
    import main
    player = MagicMock()
    live = main.FridayLive(player)
    live.loop_guard.check = lambda *args: None
    live._loop = live.session = live._send_lock = object()
    live._send_text = lambda text: None
    executed = []
    live._tool_handlers['cmd_control'] = (lambda args: executed.append(args['command']) or 'done', 10)

    async def run():
        a = await live._dispatch_tool('cmd_control', {'command': 'one'})
        token_a = live._pending_card_token
        assert a.error_kind == 'confirmation'
        callback_a = player.request_confirmation.call_args.args[1]
        # An early replay cannot consume the token or execute the action.
        blocked = await live._dispatch_tool('cmd_control', {'command': 'one', 'confirm': True, 'confirm_token': token_a})
        assert blocked.error_kind == 'confirmation' and not executed
        await live._dispatch_tool('cmd_control', {'command': 'two'})
        token_b = live._pending_card_token
        callback_b = player.request_confirmation.call_args.args[1]
        assert callback_a(True) == 'This confirmation is no longer current.'
        callback_b(True)
        blocked = await live._dispatch_tool('cmd_control', {'command': 'one', 'confirm': True, 'confirm_token': token_a})
        assert blocked.error_kind == 'confirmation' and not executed
        accepted = await live._dispatch_tool('cmd_control', {'command': 'two', 'confirm': True, 'confirm_token': token_b})
        assert accepted.content == 'done' and executed == ['two']
        replay = await live._dispatch_tool('cmd_control', {'command': 'two', 'confirm': True, 'confirm_token': token_b})
        assert replay.error_kind == 'confirmation' and executed == ['two']
        await live._dispatch_tool('cmd_control', {'command': 'three'})
        token_c = live._pending_card_token
        player.request_confirmation.call_args.args[1](False)
        denied = await live._dispatch_tool('cmd_control', {'command': 'three', 'confirm': True, 'confirm_token': token_c})
        assert denied.error_kind == 'confirmation' and executed == ['two']
    asyncio.run(run())


def test_affirmation_without_request_cannot_authorize():
    from unittest.mock import MagicMock
    import main
    live = main.FridayLive(MagicMock())
    live._mark_human_confirmed()
    assert not live._has_human_confirmation('unrelated')


def test_ambiguous_or_negative_sentences_are_not_approval():
    import main
    assert main.FridayLive._is_explicit_approval('Yes, I approve!')
    assert not main.FridayLive._is_explicit_approval("Yes, but don't do it")
    assert not main.FridayLive._is_explicit_approval('I do not approve')


def test_speaker_pcm_amplitude_and_cancel_reset(monkeypatch):
    import asyncio
    import struct
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import main
    import numpy as numpy
    amplitudes = []
    ui = MagicMock()
    ui.muted = False
    ui.silent_mode = False
    ui.bridge.setAmplitude.side_effect = amplitudes.append
    ui._dispatch.side_effect = lambda fn, *args: fn(*args)
    class Stream:
        def __init__(self, **kwargs): pass
        def start(self): pass
        def write(self, chunk): pass
        def stop(self): pass
        def close(self): pass
    monkeypatch.setattr(main, 'sd', SimpleNamespace(RawOutputStream=Stream))
    monkeypatch.setattr(main, 'np', numpy)
    live = main.FridayLive(ui)
    async def run():
        live.audio_in_queue = asyncio.Queue()
        task = asyncio.create_task(live._play_audio())
        live.audio_in_queue.put_nowait(struct.pack('<128h', *([8192] * 128)))
        for _ in range(100):
            if amplitudes: break
            await asyncio.sleep(.001)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
    asyncio.run(run())
    assert amplitudes[0] == pytest.approx(.75)
    assert amplitudes[-1] == 0
