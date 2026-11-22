import pytest
from ui.island.dictation_paste import DictationPaste, Target, QtWindowsAdapter, style_text


class Adapter:
    def __init__(self):
        self.window = Target(123, 'Editor')
        self.data = {'text/plain': b'before', 'text/html': b'<b>before</b>'}
        self.seq = 0
        self.pastes = 0
        self.scheduled = []
    def foreground(self): return self.window
    def snapshot(self): return self.data.copy()
    def sequence(self): return self.seq
    def set_text(self, text): self.data = {'text/plain': text.encode()}; self.seq += 1
    def restore(self, data): self.data = data; self.seq += 1
    def paste(self): self.pastes += 1
    def later(self, ms, fn): self.scheduled.append((ms, fn))


def test_exact_clean_polished_styles():
    assert style_text(' a  b , c ', 'Exact') == ' a  b , c '
    assert style_text(' a  b , c ', 'Clean') == 'a b, c'
    assert style_text(' hello world ', 'Polished') == 'Hello world.'
    with pytest.raises(ValueError): style_text('hi', 'other')


def test_paste_preserves_target_and_all_clipboard_formats():
    a = Adapter(); original = a.data.copy(); flow = DictationPaste(a)
    assert flow.begin() == Target(123, 'Editor')
    assert flow.commit('hello') == 'pasted'
    assert a.pastes == 1 and a.data == {'text/plain': b'hello'}
    assert a.scheduled[0][0] == 700
    a.scheduled.pop()[1]()
    assert a.data == original
    with pytest.raises(RuntimeError): flow.commit('replay')


def test_focus_switch_prevents_paste_without_modifying_clipboard():
    a = Adapter(); original = a.data.copy(); flow = DictationPaste(a); flow.begin()
    a.window = Target(456, 'Other')
    with pytest.raises(RuntimeError, match='no longer focused'): flow.commit('hello')
    assert a.data == original and a.pastes == 0


def test_user_copy_is_not_overwritten_by_delayed_restoration():
    a = Adapter(); flow = DictationPaste(a); flow.begin(); flow.commit('hello')
    a.set_text('user copy'); a.scheduled.pop()[1]()
    assert a.data == {'text/plain': b'user copy'}


def test_failure_restores_clipboard_and_disallows_overlapping_pastes():
    a = Adapter(); original = a.data.copy(); flow = DictationPaste(a); flow.begin()
    def fail(): raise OSError('denied')
    a.paste = fail
    with pytest.raises(OSError): flow.commit('hello')
    assert a.data == original
    a.paste = lambda: None
    flow.begin(); flow.commit('second'); flow.begin()
    with pytest.raises(RuntimeError, match='still restoring'): flow.commit('third')
    a.scheduled.pop()[1]()
    assert flow.commit('third') == 'pasted'


def test_clipboard_mode_copies_without_pasting_or_restoring():
    a = Adapter(); flow = DictationPaste(a); flow.begin()
    assert flow.commit('hello', mode='clipboard') == 'copied'
    assert a.data == {'text/plain': b'hello'} and not a.pastes and not a.scheduled


def test_empty_target_or_text_are_rejected():
    a = Adapter(); a.window = Target(0, ''); flow = DictationPaste(a)
    with pytest.raises(RuntimeError): flow.begin()
    a.window = Target(1, 'Editor'); flow.begin()
    with pytest.raises(ValueError): flow.commit('  ')
    assert a.pastes == 0


def test_production_adapter_is_guarded_off_windows():
    from ui.island import win32_overlay
    if not win32_overlay.IS_WINDOWS:
        with pytest.raises(RuntimeError): QtWindowsAdapter()


def test_hold_and_double_tap_lock_key_logic():
    from ui.island.dictation_paste import DictationKeyLogic
    logic = DictationKeyLogic()
    assert logic.down(0) == 'start'
    assert logic.down(.1) is None
    assert logic.up(1) == 'finish' and logic.phase == 'idle'
    assert logic.down(2) == 'start'
    assert logic.up(2.1) is None
    assert logic.down(2.3) == 'lock'
    assert logic.up(2.4) is None
    assert logic.other_key() is None
    assert logic.down(4) == 'finish' and logic.phase == 'idle'


def test_shortcut_escape_and_tap_timeout_cancel():
    from ui.island.dictation_paste import DictationKeyLogic
    logic = DictationKeyLogic()
    logic.down(0); assert logic.other_key() == 'cancel'
    logic.down(1); logic.up(1.1)
    assert logic.tick(1.6) == 'cancel'
    logic.down(2); logic.up(2.1); assert logic.down(2.8) == 'restart'
    assert logic.escape() == 'cancel' and logic.escape() is None


def test_changed_clipboard_during_snapshot_aborts_without_overwriting():
    a = Adapter(); flow = DictationPaste(a); flow.begin()
    def snapshot():
        copy = a.data.copy(); a.set_text('new clipboard contents'); return copy
    a.snapshot = snapshot
    with pytest.raises(RuntimeError, match='Clipboard changed'): flow.commit('hello')
    assert a.data == {'text/plain': b'new clipboard contents'} and not a.pastes


def test_focus_changed_after_clipboard_write_restores_and_aborts():
    a = Adapter(); original = a.data.copy(); flow = DictationPaste(a); flow.begin()
    write = a.set_text
    def change_focus(text):
        write(text); a.window = Target(456, 'Other')
    a.set_text = change_focus
    with pytest.raises(RuntimeError, match='no longer focused'): flow.commit('hello')
    assert a.data == original and not a.pastes
