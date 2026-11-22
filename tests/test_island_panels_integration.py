"""Native panel calls run away from the Qt thread and return to it."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import importlib
import threading
import time
import types
import pytest
pytest.importorskip('PySide6.QtGui', reason='Qt system libraries are required')
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from ui.bridge.controller import UiController
from ui.island.voice_hud import VoiceHUD
from ui.settings_store import SettingsStore

_APP = None

def app():
    global _APP
    _APP = QCoreApplication.instance() or QApplication([])
    return _APP


def wait_for(predicate, timeout=2):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        app().processEvents()
        if predicate(): return
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def window(tmp_path):
    app()
    hud = VoiceHUD(); ctrl = UiController(hud, store=SettingsStore(tmp_path/'settings.json'))
    yield hud, ctrl
    ctrl.shutdown(); hud.close()


def test_text_and_chat_signal_are_queued(window):
    hud, ctrl = window; calls = []; owner = threading.get_ident()
    ctrl.on_text_command = lambda text: calls.append((text, threading.get_ident()))
    panel = hud.panels['write']; panel.input.setText('hello'); panel.submit()
    wait_for(lambda: bool(calls) and bool(hud.chat_lines))
    assert calls[0][0] == 'hello' and calls[0][1] != owner
    assert hud.chat_lines[-1] == 'You: hello'


def test_media_vision_shelf_and_settings_actions_use_worker(window, monkeypatch, tmp_path):
    hud, ctrl = window; calls = []; owner = threading.get_ident(); original = importlib.import_module
    def load(name):
        actions = {'actions.media_control': 'media_control_action', 'actions.screen_processor': 'screen_process',
                   'actions.document_qa': 'document_qa', 'actions.guardian': 'guardian_action',
                   'actions.system_status': 'system_status'}
        if name not in actions: return original(name)
        def fn(params, **kw):
            calls.append((name, params, threading.get_ident()))
            return f'{name} done'
        module = types.SimpleNamespace(); setattr(module, actions[name], fn); return module
    monkeypatch.setattr('ui.panels.common.importlib.import_module', load)
    hud.panels['media'].control('next')
    hud.panels['vision'].question.setText('What is here?'); hud.panels['vision'].ask('screen')
    file = tmp_path/'a.txt'; file.write_text('example')
    shelf = hud.panels['shelf']; shelf.add_files([str(file)]); shelf.files.setCurrentRow(0); shelf.question.setText('What?'); shelf.ask()
    settings = hud.panels['settings']; settings.action('actions.guardian', 'guardian_action', {'action':'report'})
    settings.action('actions.system_status', 'system_status', {})
    wait_for(lambda: len(calls) == 5 and 'done' in shelf.result.text())
    assert all(thread != owner for _, _, thread in calls)
    assert next(p for m,p,_ in calls if m == 'actions.screen_processor') == {'angle':'screen', 'text':'What is here?'}
    assert next(p for m,p,_ in calls if m == 'actions.document_qa')['source'] == str(file)


def test_tasks_refresh_cancel_reminder_and_settings_persistence(window, monkeypatch, tmp_path):
    from agent import task_queue
    hud, ctrl = window; calls = []
    class Queue:
        def get_all_statuses(self): return [{'task_id':'t1', 'goal':'Do work', 'status':'running'}]
        def cancel(self, task_id): calls.append(('cancel', task_id)); return True
    monkeypatch.setattr(task_queue, 'get_queue', lambda: Queue())
    tasks = hud.panels['tasks']; tasks.refresh(); wait_for(lambda: tasks.tasks.count() == 1)
    tasks.tasks.setCurrentRow(0); tasks.cancel(); wait_for(lambda: bool(calls)); assert calls == [('cancel', 't1')]
    original = importlib.import_module
    def load(name):
        if name == 'actions.reminder':
            return types.SimpleNamespace(reminder=lambda params, **kw: calls.append(('reminder', params)) or 'scheduled')
        return original(name)
    monkeypatch.setattr('ui.panels.common.importlib.import_module', load)
    tasks.message.setText('Meeting'); tasks.remind(); wait_for(lambda: len(calls) == 2)
    assert calls[1][1]['message'] == 'Meeting'
    hud.panels['settings'].save(theme='light', opacity=40)
    restored = SettingsStore(tmp_path/'settings.json')
    assert restored.values['theme'] == 'light' and restored.values['opacity'] == 40
    assert hud.theme == 'light'


def test_confirmation_card_displays_full_details_and_answers_once(window):
    hud, _ = window
    panel = hud.panels['chat']; calls = []
    details = 'Delete this file: ' + 'a' * 200
    panel.request_confirmation(details, calls.append)
    assert panel.approval_description.toPlainText() == details
    panel.approve.click(); panel.deny.click()
    assert calls == [True]
    panel.request_confirmation('Other request', calls.append)
    panel.deny.click()
    assert calls == [True, False]
