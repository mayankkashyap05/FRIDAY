"""Validated, atomically persisted UI settings."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import threading

DEFAULTS = {'opacity': 100, 'theme': 'dark', 'notch_colour': '#000000',
            'eye_colour': '#ffffff', 'hover_to_expand': True,
            'hide_on_fullscreen': True, 'animations': True, 'ui_sounds': False,
            'dictation_style': 'Exact', 'paste_mode': 'paste', 'face_out_positions': {}}


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(__file__).resolve().parent.parent / 'config' / 'ui_settings.json'
        self._lock = threading.RLock()
        self.values = dict(DEFAULTS)
        self.values['face_out_positions'] = {}
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    self._set_validated(key, value)
        except (OSError, ValueError, TypeError):
            pass

    def _set_validated(self, key, value):
        if key not in DEFAULTS:
            return
        if key == 'opacity':
            try: value = max(30, min(100, int(value)))
            except (TypeError, ValueError): return
        elif key == 'theme':
            if value not in ('dark', 'light'): return
        elif key in ('notch_colour', 'eye_colour'):
            from PySide6.QtGui import QColor
            c = QColor(str(value))
            if not c.isValid(): return
            value = c.name()
        elif key in ('hover_to_expand', 'hide_on_fullscreen', 'animations', 'ui_sounds'):
            if not isinstance(value, bool): return
        elif key == 'dictation_style':
            if value not in ('Exact', 'Clean', 'Polished'): return
        elif key == 'paste_mode':
            if value not in ('paste', 'clipboard'): return
        elif key == 'face_out_positions':
            if not isinstance(value, dict): return
            value = {str(k): [int(v[0]), int(v[1])] for k, v in value.items()
                     if isinstance(v, list) and len(v) == 2 and all(isinstance(n, int) for n in v)}
        self.values[key] = value

    def update(self, **values):
        with self._lock:
            for key, value in values.items(): self._set_validated(key, value)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.ui-settings-', suffix='.tmp', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump(self.values, handle, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name): os.unlink(name)
