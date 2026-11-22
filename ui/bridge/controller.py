"""Connect the native view to the in-process Qt bridge."""
from .bridge import UiBridge


class UiController:
    """Small adapter; the backend facade and panel actions remain to be added."""
    def __init__(self, hud, bridge=None):
        self.hud = hud
        self.bridge = bridge or UiBridge()
        self.dispatcher = self.bridge.dispatcher
        self.bridge.state_changed.connect(self._state_changed)
        self.bridge.log_added.connect(self.hud.append_log)
        self.bridge.amplitude_changed.connect(self._amplitude_changed)

    def _state_changed(self, state):
        scenes = {'IDLE': 'resting', 'LISTENING': 'listening', 'THINKING': 'working',
                  'SPEAKING': 'speaking', 'MUTED': 'muted', 'ERROR': 'error'}
        self.hud.set_scene(scenes.get(state, 'resting'))

    def _amplitude_changed(self, level):
        # The current prototype has no waveform yet; keep the latest value on
        # the view so a waveform can consume it without touching producers.
        self.hud.audio_level = level
        self.hud.update()
