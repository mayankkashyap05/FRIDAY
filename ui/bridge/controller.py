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
        self.hud.command_requested.connect(self.submit_text)
        self.hud.face_out_moved.connect(self._save_face_out)
        self.hud.mode_requested.connect(self._mode_requested)
        self.hud.panels['settings'].settings_changed.connect(self.apply_settings)
        self.hud.panels['settings'].dock_requested.connect(lambda: self.hud.set_scene('resting'))
        self.hud.panels['settings'].face_out_requested.connect(lambda: self.hud.set_scene('face_out'))
        self.apply_settings(self.store.values)

    def apply_settings(self, settings):
        self.hud.set_theme(settings['theme'])
        self.hud.setWindowOpacity(settings['opacity'] / 100.0)
        self.hud.fsm.open_on_hover = settings['hover_to_expand']
        self.hud.animations_enabled = settings['animations']
        self.hud.sounds.enabled = settings.get('ui_sounds', False)
        self.hud.hide_on_fullscreen = settings['hide_on_fullscreen']
        self.hud.notch_colour = settings['notch_colour']
        self.hud.eye_colour = settings['eye_colour']
        self.hud.face_out_positions = dict(settings['face_out_positions'])
        self.hud.update()

    def _save_face_out(self, screen, x, y):
        positions = dict(self.store.values['face_out_positions'])
        positions[screen] = [x, y]
        try:
            self.store.update(face_out_positions=positions)
            self.hud.face_out_positions = positions
        except OSError as exc: self.bridge.ingest(f'ERR: Cannot save position: {exc}')

    def submit_text(self, text):
        text = str(text or '').strip()
        if not text: return
        callback = self.on_text_command
        if not callback:
            self.bridge.ingest('SYS: Assistant is not connected yet.')
            return
        self.bridge.ingest(f'You: {text}')
        def send():
            return callback(text)
        def done(result, error):
            if error: self.bridge.ingest(f'ERR: Command failed: {error}')
        self.services.run(send, done)

    def _mode_requested(self, mode):
        if self.on_mode_requested: self.on_mode_requested(mode)

    def _state_changed(self, state):
        scenes = {'IDLE': 'resting', 'LISTENING': 'listening', 'THINKING': 'working',
                  'SPEAKING': 'speaking', 'MUTED': 'muted', 'ERROR': 'error'}
        # Do not dismiss a panel or interrupt a user typing on backend activity.
        scene = scenes.get(state, 'resting')
        if self.hud.state in self.hud.panels or self.hud.state == 'hover':
            self.hud._activity_scene = scene
        else:
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
