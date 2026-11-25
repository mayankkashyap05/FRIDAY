"""FRIDAY's native Qt UI facade.

Qt Widgets is imported when the facade is constructed so importing the module
for configuration and backend contract checks does not create a GUI.
"""
from __future__ import annotations
import json
import os
import sys
import threading
import time
from pathlib import Path


def get_base_dir():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
CONFIG_DIR = BASE_DIR / 'config'
API_FILE = CONFIG_DIR / 'api_keys.json'
THEME_FILE = CONFIG_DIR / 'ui_settings.json'
SYSTEM_NAME = MODEL_BADGE = 'FRIDAY'
THEME_NAMES = ('dark', 'light')
DEFAULT_THEME = 'dark'
THEMES = {name: name for name in THEME_NAMES}


def _load_theme_name():
    try:
        data = json.loads(Path(THEME_FILE).read_text(encoding='utf-8'))
        name = data.get('theme', DEFAULT_THEME)
        return name if name in THEME_NAMES else DEFAULT_THEME
    except (OSError, ValueError, AttributeError):
        return DEFAULT_THEME


def _save_theme_name(name):
    if name not in THEME_NAMES: return
    from .settings_store import SettingsStore
    SettingsStore(THEME_FILE).update(theme=name)


class _RootAdapter:
    def __init__(self, ui):
        self._ui = ui
        self.report_callback_exception = None
        self._cancelled = set()
        self._lock = threading.Lock()

    def after(self, ms, fn=None, *args):
        if fn is None: return ''
        token = f'after#{time.monotonic_ns()}'
        def start():
            from PySide6.QtCore import QTimer
            def invoke():
                with self._lock:
                    if token in self._cancelled:
                        self._cancelled.discard(token); return
                try: fn(*args)
                except Exception:
                    if self.report_callback_exception:
                        self.report_callback_exception(*sys.exc_info())
                    else:
                        import traceback; traceback.print_exc()
            QTimer.singleShot(max(0, int(ms or 0)), invoke)
        self._ui._dispatch(start)
        return token

    def after_cancel(self, token):
        with self._lock: self._cancelled.add(token)
    def state(self): return 'normal' if self._ui.visible else 'withdrawn'
    def withdraw(self): self._ui.hide()
    def deiconify(self): self._ui.show()
    def lift(self): self._ui.raise_window()
    def iconify(self): self._ui.hide()
    def destroy(self): self._ui.quit()
    def quit(self): self._ui.quit()
    def mainloop(self, *_args): return self._ui.run()
    def winfo_screenwidth(self): return self._ui.screen_size()[0]
    def winfo_screenheight(self): return self._ui.screen_size()[1]
    def geometry(self, *_args):
        w, h = self._ui.window_size(); return f'{w}x{h}'
    def update(self): self._ui._dispatch(self._ui.app.processEvents)
    def update_idletasks(self): self.update()


class FridayUI:
    ambient = True
    def __init__(self, face_path='face.png', size=None, *, headless=False):
        if headless:
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtWidgets import QApplication
        existing = QCoreApplication.instance()
        if existing is not None and not isinstance(existing, QApplication):
            raise RuntimeError('FRIDAY requires QApplication, not QCoreApplication')
        self.app = existing or QApplication(sys.argv[:1])
        self.app.setQuitOnLastWindowClosed(False)
        from .island.voice_hud import VoiceHUD
        from .bridge.controller import UiController
        self._closed = threading.Event(); self._loop_entered = False
        self._gui_shutdown = False
        self._api_key_ready = self._api_keys_exist()
        self._headless = bool(headless)
        self._lock = threading.RLock()
        self._pending_cards = []
        self._MAX_PENDING_LOGS = 200
        self.muted = False; self.silent_mode = False; self.speaking = False
        self.connected = False; self.connecting = False
        # Standby is the calm, dormant dock state used by wake-word mode. It is
        # deliberately separate from ``muted`` (an explicit user choice).
        self.standby = False
        self.on_text_command = None; self.on_silent_change = None
        self.on_wake_request = None
        self.vision_preview_open = False; self.arcade_open = False
        self.vision_mode = 'STANDBY'; self.vision_signal = 'Awaiting camera input'
        self.persona_mode = 'normal'; self.persona_mood = 'calm'; self.persona_voice = 'Aoede'
        self.last_event = ''; self.status_text = 'INITIALISING'
        self._visible = not headless
        self.hud = VoiceHUD()
        self.controller = UiController(self.hud, player=self)
        self.bridge = self.controller.bridge
        self.state_machine = self.bridge
        self.controller.on_text_command = self._forward_text_command
        self.controller.on_mode_requested = self._on_mode_requested
        self.controller.on_quit = self.quit
        self.hud.panels['settings'].mute_requested.connect(self._toggle_mute)
        self.hud.panels['settings'].quit_requested.connect(self.quit)
        self.root = _RootAdapter(self)
        self.tray = None
        if not headless:
            from .tray.tray_menu import TrayMenu
            self.tray = TrayMenu(self)
            self.hud.show()
            if not self._api_key_ready:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self._request_api_key)

    @property
    def visible(self): return self._visible

    def _dispatch(self, fn, *args, **kwargs):
        if self._closed.is_set(): return False
        return self.controller.dispatcher.call(fn, *args, **kwargs)
    def _safe_ui(self, fn, *args): return self._dispatch(fn, *args)
    def _forward_text_command(self, text):
        if callable(self.on_text_command): self.on_text_command(text)
    def submit_text(self, text): return self._dispatch(self.controller.submit_text, text)
    def _on_mode_requested(self, mode):
        if mode == 'listening':
            if callable(self.on_wake_request): self.on_wake_request('manual')
            if self.muted: self._toggle_mute()
        if mode == 'dictation': self.write_log('SYS: Dictation target-app integration is not available yet.')

    def set_state(self, state):
        name = str(state or '').upper()
        # Standby must never look like active listening, no matter which
        # producer (reconnect loop, tool result, speech release) asks for it.
        if self.standby and name in ('LISTENING', 'ONLINE', 'PROCESSING', 'THINKING'):
            name = 'IDLE'
        with self._lock:
            self.status_text = name; self.speaking = name == 'SPEAKING'
        self.bridge.set_state(name)

    def set_standby(self, active=True):
        """Park the dock/orb in its calm dormant state (no mic reactivity)."""
        active = bool(active)
        self.standby = active
        if active:
            self._dispatch(self.bridge.setAmplitude, 0.0)
            self.set_state('IDLE')
        return active
    def set_connection(self, ok):
        with self._lock: self.connected = bool(ok); self.connecting = False
        self.bridge.set_connection(ok)
    def set_connecting(self, on=True):
        with self._lock: self.connecting = bool(on)
        self.bridge.set_connecting(on)
    def set_persona(self, mode, mood, voice):
        with self._lock:
            self.persona_mode, self.persona_mood, self.persona_voice = mode or 'normal', mood or 'calm', voice or 'Aoede'
        self.bridge.set_persona(mode, mood, voice)
    def start_speaking(self): self.set_state('SPEAKING')
    def stop_speaking(self): self.set_state('MUTED' if self.muted else 'LISTENING')
    def set_live_transcript(self, text): self.bridge.set_live_transcript(text)
    def write_log(self, text):
        if text is None or self._closed.is_set(): return
        payload = str(text)
        with self._lock:
            if not self._loop_entered:
                self._pending_cards.append(payload)
                if len(self._pending_cards) > self._MAX_PENDING_LOGS:
                    del self._pending_cards[:len(self._pending_cards)-self._MAX_PENDING_LOGS]
                return
            self._ingest(payload)
    def _ingest(self, payload): self.bridge.ingest(payload)
    def _flush_pending_logs(self):
        with self._lock:
            pending, self._pending_cards = self._pending_cards, []
            for line in pending: self._ingest(line)
    def _toggle_mute(self):
        with self._lock: self.muted = not self.muted; muted = self.muted
        self.bridge.set_muted(muted)
        if not muted and self.standby and callable(self.on_wake_request):
            self.on_wake_request('unmute')
    def _draw_mute_button(self): self.bridge.set_muted(self.muted)
    def _toggle_silent_mode(self):
        with self._lock: self.silent_mode = not self.silent_mode; silent = self.silent_mode
        if callable(self.on_silent_change):
            threading.Thread(target=self.on_silent_change, args=(silent,), daemon=True).start()
    def set_theme(self, name):
        if name not in THEME_NAMES: return
        def change():
            self.controller.store.update(theme=name)
            self.controller.apply_settings(self.controller.store.values)
        self._dispatch(change)
    def cycle_theme(self): self.set_theme('light' if self.controller.store.values['theme'] == 'dark' else 'dark')
    def show(self):
        self._visible = True; self._dispatch(self.hud.set_user_visible, True)
    def hide(self):
        self._visible = False; self._dispatch(self.hud.set_user_visible, False)
    def raise_window(self):
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.platformName() != 'offscreen':
            self._dispatch(self.hud.raise_)
    def invoke(self):
        self.standby = False
        self.show(); self.set_state('LISTENING')
    def dismiss(self): self.hide()
    def _shutdown_gui(self):
        if self._gui_shutdown: return
        self._gui_shutdown = True
        if self.tray: self.tray.close()
        self.controller.shutdown()
        self.hud.close()
        self.app.quit()
    def quit(self):
        if self._closed.is_set(): return
        self._closed.set()
        if self.controller.dispatcher.on_ui_thread:
            self._shutdown_gui()
        else:
            self.controller.dispatcher.call(self._shutdown_gui)
    def run(self):
        with self._lock:
            self._loop_entered = True
            self._flush_pending_logs()
        try: return self.app.exec()
        finally:
            self._closed.set()
            self._shutdown_gui()
    mainloop = run
    def screen_size(self):
        screen = self.app.primaryScreen()
        return (screen.size().width(), screen.size().height()) if screen else (1920, 1080)
    def window_size(self): return (self.hud.width(), self.hud.height())

    def _api_keys_exist(self):
        if os.environ.get('FRIDAY_GEMINI_API_KEY', '').strip() or os.environ.get('FRIDAY_GEMINI_API_KEYS', '').strip(): return True
        try: data = json.loads(Path(API_FILE).read_text(encoding='utf-8'))
        except (OSError, ValueError): return False
        if not isinstance(data, dict): return False
        key = data.get('gemini_api_key')
        keys = data.get('gemini_api_keys')
        return bool((isinstance(key, str) and key.strip()) or
                    (isinstance(keys, list) and any(isinstance(k, str) and k.strip() for k in keys)))

    def wait_for_api_key(self):
        while not self._closed.is_set() and not self._api_key_ready:
            if self._api_keys_exist(): self._api_key_ready = True; break
            self._closed.wait(.25)

    def submit_api_key(self, key):
        key = str(key or '').strip()
        if not key: return False
        try:
            from api_keys import add_key
            add_key(key)
        except (ImportError, TypeError, AttributeError):
            return False
        except Exception:
            return False
        self._api_key_ready = self._api_keys_exist()
        return self._api_key_ready

    def _request_api_key(self):
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        text, ok = QInputDialog.getText(None, 'FRIDAY setup', 'Gemini API key:', QLineEdit.Password)
        if ok and self.submit_api_key(text): self.write_log('SYS: API key saved.')
        elif not self._closed.is_set(): self.write_log('SYS: Add a Gemini API key to config/api_keys.json to start FRIDAY.')

    def request_confirmation(self, description, callback):
        if self._headless:
            return
        def show_confirmation():
            self.hud.fsm.pinned = True
            def answered(approved):
                self._dispatch(setattr, self.hud.fsm, 'pinned', False)
                return callback(approved)
            self.hud.panels['chat'].request_confirmation(description, answered)
            self.controller.show_settings('Chat')
            self.hud.set_user_visible(True)
            self._visible = True
        self._dispatch(show_confirmation)

    def set_vision_signal(self, text):
        self.vision_signal = str(text or 'Awaiting camera input')[:160]
        self._dispatch(self.hud.panels['vision'].result.setText, self.vision_signal)
    def set_vision_detections(self, events):
        labels = []
        for event in events or []:
            value = event.get('label') if isinstance(event, dict) else getattr(event, 'label', None)
            if value: labels.append(str(value))
        if labels: self.set_vision_signal(', '.join(labels[:8]))
    def set_vision_frame(self, frame):
        if not self.vision_preview_open or frame is None: return
        try: copied = frame.copy()
        except (AttributeError, TypeError): return
        self._dispatch(self.hud.panels['vision'].set_frame, copied)
    def open_vision_preview(self):
        self.vision_preview_open = True; self.vision_mode = 'PREVIEW'
        self._dispatch(self.controller.show_settings, 'Vision')
    def close_vision_preview(self): self.vision_preview_open = False; self.vision_mode = 'STANDBY'
    def set_arcade_frame(self, frame):
        if self.arcade_open and frame is not None:
            try: copied = frame.copy()
            except (AttributeError, TypeError): return
            self._dispatch(self.hud.panels['vision'].set_frame, copied)
    def set_arcade_signal(self, gesture, x=.5):
        if self.arcade_open: self.set_vision_signal(f'Gesture: {gesture} · {float(x):.2f}')
    def set_arcade_pose(self, arms_raised, lean=0.):
        if self.arcade_open: self.set_vision_signal(f"Arms {'raised' if arms_raised else 'down'} · lean {float(lean):.2f}")
    def open_vision_arcade(self):
        self.arcade_open = True; self.vision_mode = 'ARCADE'; self._dispatch(self.controller.show_settings, 'Vision')
    def close_vision_arcade(self): self.arcade_open = False; self.vision_mode = 'STANDBY'



def make_ui(face_path='face.png', size=None, *, headless=False):
    return FridayUI(face_path, size, headless=headless)

__all__ = ['FridayUI', 'make_ui', 'get_base_dir', 'BASE_DIR', 'CONFIG_DIR', 'API_FILE', 'THEME_FILE', 'THEMES', 'THEME_NAMES', 'DEFAULT_THEME', 'SYSTEM_NAME', 'MODEL_BADGE', '_load_theme_name', '_save_theme_name']
