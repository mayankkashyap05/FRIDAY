"""Connect backend events and panel actions to the Qt island."""
from .bridge import UiBridge
from ui.panels.common import PanelServices
from ui.settings_store import SettingsStore


class UiController:
    def __init__(self, hud, bridge=None, player=None, store=None):
        self.hud = hud
        self._closed = False
        self.bridge = bridge or UiBridge()
        self.dispatcher = self.bridge.dispatcher
        self.store = store or SettingsStore()
        self.services = PanelServices(self.dispatcher, player)
        self.on_text_command = None
        self.on_mode_requested = None
        self.bridge.state_changed.connect(self._state_changed)
        self.bridge.log_added.connect(self.hud.append_log)
        self.bridge.amplitude_changed.connect(self._amplitude_changed)
        self.bridge.transcript_changed.connect(self._transcript_changed)
        self.hud.attach_panels(self.services, self.store)
        from ui.panels.settings_window import SettingsWindow
        from ui.island.search_bar import SearchBarPopup
        from PySide6.QtWidgets import QMenu
        self.settings_window = SettingsWindow(self.hud.panels)
        self.search_popup = SearchBarPopup(self.hud)
        self.context_menu = QMenu(self.hud)
        self._build_context_menu()
        self.hud.search_requested.connect(self.toggle_search)
        self.hud.context_requested.connect(self._show_context_menu)
        self.search_popup.submitted.connect(self._search_submitted)
        self.search_popup.dismissed.connect(lambda: self.hud._play_cue('collapse'))
        self.search_popup.microphone_requested.connect(self._start_listening)
        self.hud.command_requested.connect(self.submit_text)
        self.hud.files_dropped.connect(self._files_dropped)
        self.hud.face_out_moved.connect(self._save_face_out)
        self.hud.mode_requested.connect(self._mode_requested)
        self.hud.panels['settings'].settings_changed.connect(self.apply_settings)
        self.hud.panels['settings'].dock_requested.connect(lambda: self.hud.set_scene('resting'))
        self.hud.panels['settings'].face_out_requested.connect(lambda: self.hud.set_scene('face_out'))
        self.hud.theme_changed.connect(self._theme_changed)
        self.apply_settings(self.store.values)

    def apply_settings(self, settings):
        from PySide6.QtGui import QGuiApplication
        self.hud.set_theme(settings['theme'])
        if QGuiApplication.platformName() != 'offscreen' and not self.hud.preview_mode:
            self.hud.setWindowOpacity(settings['opacity'] / 100.0)
        self.hud.fsm.open_on_hover = False
        self.hud.animations_enabled = settings['animations']
        self.hud.hide_on_fullscreen = settings['hide_on_fullscreen']
        self.hud.set_sound_enabled(settings.get('ui_sounds', True))
        self.hud.notch_colour = settings['notch_colour']
        self.hud.eye_colour = settings['eye_colour']
        self.hud.face_out_positions = dict(settings['face_out_positions'])
        self.hud.update()

    def _theme_changed(self, theme):
        if hasattr(self, 'settings_window'): self.settings_window.set_theme(theme)
        if hasattr(self, 'search_popup'): self.search_popup.set_theme(theme)
        if hasattr(self, 'context_menu'):
            self.context_menu.setStyleSheet(self.hud.styleSheet())

    def show_settings(self, tab='General'):
        self.settings_window.open_tab(tab)

    def toggle_search(self):
        if self.search_popup.isVisible():
            self.search_popup.dismiss()
        else:
            self.hud._play_cue('expand')
            self.search_popup.open_below(self.hud)

    def _search_submitted(self, text):
        self.hud._play_cue('click')
        self.hud.set_scene('working')
        self.submit_text(text)

    def _start_listening(self):
        self.hud.set_scene('listening')
        self._mode_requested('listening')

    def _files_dropped(self, files):
        self.hud.panels['shelf'].add_files(files)
        self.show_settings('Shelf')

    def _build_context_menu(self):
        menu = self.context_menu
        menu.addAction('Search or Ask', self.toggle_search)
        menu.addAction('Pull out / dock', lambda: self.hud.set_scene('resting' if self.hud.state == 'face_out' else 'face_out'))
        menu.addAction('Mute / unmute microphone', self._toggle_mute)
        menu.addAction('Settings & Panels…', lambda: self.show_settings('General'))
        menu.addAction('Hide notch', self._hide_notch)
        menu.addSeparator(); menu.addAction('Quit', self._quit_requested)

    def _quit_requested(self):
        callback = getattr(self, 'on_quit', None)
        if callback: callback()

    def _hide_notch(self):
        player = self.services.player
        if player is not None and hasattr(player, 'hide'):
            player.hide()
        else:
            self.hud.hide()

    def _toggle_mute(self):
        player = self.services.player
        if player is not None and hasattr(player, '_toggle_mute'):
            player._toggle_mute()
        else:
            self._mode_requested('mute')

    def _show_context_menu(self, point):
        self.context_menu.popup(point)

    def _save_face_out(self, screen, x, y):
        positions = dict(self.store.values['face_out_positions'])
        positions[screen] = [x, y]
        try:
            self.store.update(face_out_positions=positions)
            self.hud.face_out_positions = positions
        except OSError as exc:
            self.bridge.ingest(f'ERR: Cannot save position: {exc}')

    def submit_text(self, text):
        text = str(text or '').strip()
        if not text:
            return
        callback = self.on_text_command
        if not callback:
            self.bridge.ingest('SYS: Assistant is not connected yet.')
            return
        self.bridge.ingest(f'You: {text}')

        def send():
            return callback(text)

        def done(result, error):
            if error:
                self.bridge.ingest(f'ERR: Command failed: {error}')

        self.services.run(send, done)

    def _mode_requested(self, mode):
        if self.on_mode_requested:
            self.on_mode_requested(mode)

    def _state_changed(self, state):
        scenes = {
            'IDLE': 'resting',
            'LISTENING': 'listening',
            'THINKING': 'working',
            'SPEAKING': 'speaking',
            'MUTED': 'muted',
            'ERROR': 'error',
        }
        scene = scenes.get(state, 'resting')
        self.hud.set_scene(scene)

    def _transcript_changed(self, text):
        self.hud.live_transcript = text
        if 'chat' in self.hud.panels:
            self.hud.panels['chat'].transcript.setText(text)
        self.hud.update()

    def _amplitude_changed(self, level):
        self.hud.audio_level = level
        self.hud._wake_animation()
        self.hud.update()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.hud.shutdown()
        self.search_popup.hide()
        self.settings_window.hide()
        self.context_menu.hide()
        self.dispatcher.stop()
        if not self.services.close(timeout=2.0):
            import logging
            logging.getLogger(__name__).warning('Panel workers still running after shutdown deadline')
        self.bridge.state_changed.disconnect(self._state_changed)
        self.bridge.log_added.disconnect(self.hud.append_log)
        self.bridge.amplitude_changed.disconnect(self._amplitude_changed)
        self.bridge.transcript_changed.disconnect(self._transcript_changed)
        self.hud.command_requested.disconnect(self.submit_text)
        self.hud.face_out_moved.disconnect(self._save_face_out)
        self.hud.mode_requested.disconnect(self._mode_requested)
        self.hud.panels['settings'].settings_changed.disconnect(self.apply_settings)