import ast
import os
import threading
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
try:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication
except ImportError:
    QCoreApplication = QApplication = None
import ui

@pytest.fixture(autouse=True)
def ui_test_lifetime(request):
    # The non-UI safety tests also run in minimal/headless environments.
    if QApplication is not None:
        request.getfixturevalue('ui_test_lifetime')
    yield


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


@pytest.mark.skipif(QApplication is None, reason='Qt system libraries are required')
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


@pytest.mark.parametrize('approval', ['card', 'voice'])
def test_cmd_confirmation_end_to_end_no_loop_collision(approval, capsys):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import main
    player = MagicMock()
    player.muted = False
    live = main.FridayLive(player)
    live._loop = live.session = live._send_lock = object()
    live._send_text = lambda text: None
    calls = []
    live._tool_handlers['cmd_control'] = (lambda args: calls.append(args.copy()) or 'Windows version', 10)
    args = {'task': 'Check Windows version', 'command': 'ver'}

    async def run():
        fc = lambda a: SimpleNamespace(name='cmd_control', args=a, id='123')
        first = await live._execute_tool(fc(args))
        token = live._pending_card_token
        assert token in first.response['result']
        for _ in range(4):
            rejected = await live._execute_tool(fc({**args, 'confirm': True, 'confirm_token': token}))
            assert 'blocked' in rejected.response['result'].lower()
        assert 'confirmation rejected:' in capsys.readouterr().out
        if approval == 'card':
            assert player.request_confirmation.call_args.args[1](True) == 'Approval recorded.'
        else:
            live._on_text_command('Yes')
        result = await live._execute_tool(fc({**args, 'confirm': True, 'confirm_token': token}))
        assert result.response['result'] == 'Windows version'
        assert calls == [args]
        replay = await live._execute_tool(fc({**args, 'confirm': True, 'confirm_token': token}))
        assert 'blocked' in replay.response['result'].lower()
    asyncio.run(run())


def test_spoken_approval_before_tool_call_turn_complete():
    import asyncio
    from types import SimpleNamespace as NS
    from unittest.mock import MagicMock
    import main
    player = MagicMock()
    player.muted = False
    player.silent_mode = True
    live = main.FridayLive(player)
    live._loop = live._send_lock = object()
    args = {'task': 'Check Windows version', 'command': 'ver'}
    calls = []
    live._tool_handlers['cmd_control'] = (lambda a: calls.append(a.copy()) or 'done', 10)

    async def run():
        await live._dispatch_tool('cmd_control', args.copy())
        token = live._pending_card_token
        fc = NS(name='cmd_control', args={**args, 'confirm': True, 'confirm_token': token}, id='123')
        sc = NS(input_transcription=NS(text='Yes'), output_transcription=None, turn_complete=False)
        class Session:
            async def receive(self):
                yield NS(data=None, server_content=sc, tool_call=None)
                yield NS(data=None, server_content=None, tool_call=NS(function_calls=[fc]))
                raise asyncio.CancelledError()
            async def send_tool_response(self, function_responses):
                assert function_responses[0].response['result'] == 'done'
        live.session = Session()
        with pytest.raises(asyncio.CancelledError):
            await live._receive_audio()
        assert calls == [args]
    asyncio.run(run())


def test_unrelated_low_risk_tool_does_not_discard_pending_approval():
    import asyncio
    from unittest.mock import MagicMock
    import main
    live = main.FridayLive(MagicMock())
    live._tool_handlers['cmd_control'] = (lambda a: 'done', 10)
    live._tool_handlers['web_search'] = (lambda a: 'ok', 10)
    async def run():
        args = {'command': 'ver'}
        await live._dispatch_tool('cmd_control', args.copy())
        token = live._pending_card_token
        live._mark_human_confirmed()
        assert (await live._dispatch_tool('web_search', {'query': 'weather'})).content == 'ok'
        result = await live._dispatch_tool('cmd_control', {**args, 'confirm': True, 'confirm_token': token})
        assert result.content == 'done'
    asyncio.run(run())


def test_first_call_confirm_true_without_token_creates_pending_card_and_succeeds_after_approval():
    import asyncio
    from unittest.mock import MagicMock
    import main

    live = main.FridayLive(MagicMock())
    calls = []
    live._tool_handlers['task_manager'] = (lambda a: calls.append(a.copy()) or 'killed', 10)

    async def run():
        args = {'action': 'kill', 'process_name': 'notepad.exe'}
        first = await live._dispatch_tool('task_manager', {**args, 'confirm': True})
        assert first.error_kind == 'confirmation'
        assert calls == []
        token = live._pending_card_token
        assert token

        live._mark_human_confirmed()
        second = await live._dispatch_tool('task_manager', {**args, 'confirm': True, 'confirm_token': token})
        assert second.content == 'killed'
        assert calls == [args]

    asyncio.run(run())


def test_standby_facade_keeps_dock_dormant_and_mac_parity():
    """Wake-word standby is its own state, not an always-listening dock."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import ui
    import ui_mac

    stub = SimpleNamespace(standby=False, _lock=threading.Lock(),
                           bridge=MagicMock(), _dispatch=MagicMock(),
                           status_text='')
    stub.set_state = lambda value: ui.FridayUI.set_state(stub, value)
    ui.FridayUI.set_standby(stub, True)
    assert stub.standby is True
    assert stub.status_text == 'IDLE'
    stub._dispatch.assert_called_once()
    assert stub._dispatch.call_args.args[1] == 0.0

    # Late LISTENING requests (reconnect, tool result, speech release) are
    # swallowed while dormant, but still work after a wake.
    stub.set_state('LISTENING')
    assert stub.status_text == 'IDLE'
    ui.FridayUI.set_standby(stub, False)
    stub.set_state('LISTENING')
    assert stub.status_text == 'LISTENING'

    # ui_mac is a dynamic facade; the macOS build must expose the same method.
    assert callable(getattr(ui_mac.FridayUI, 'set_standby', None))


def test_main_keeps_reconnect_and_watchdog_out_of_always_listening():
    import ast

    tree = ast.parse(Path('main.py').read_text(encoding='utf-8'))
    live = next(n for n in tree.body if isinstance(n, ast.ClassDef) and
                n.name == 'FridayLive')
    methods = {n.name: ast.unparse(n) for n in live.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert '_watch_wake_idle' in methods
    assert '_maybe_autostart_wake_word' in methods
    assert '_last_activity_ts' in methods['_wake_up']
    assert "_wake_engine_action('resume')" in methods['_enter_standby']
    assert 'awake_for_mic' in methods['_listen_audio']
    assert 'tg.create_task(self._watch_wake_idle())' in methods['run']
