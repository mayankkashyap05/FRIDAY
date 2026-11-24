from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QSlider, QCheckBox, QLabel
from .common import Panel


class SettingsPanel(Panel):
    settings_changed = Signal(dict)
    mute_requested = Signal()
    dock_requested = Signal()
    face_out_requested = Signal()
    quit_requested = Signal()

    def __init__(self, services, store):
        super().__init__(services, 'Settings')
        self.store = store

        self._section('General')
        self.theme = QComboBox()
        self.theme.addItems(['dark', 'light'])
        self.theme.setCurrentText(store.values['theme'])
        self.layout.addWidget(self.theme)

        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(store.values['opacity'])
        self.layout.addWidget(self.opacity)

        self.fullscreen = QCheckBox('Hide over fullscreen apps')
        self.fullscreen.setChecked(store.values['hide_on_fullscreen'])
        self.layout.addWidget(self.fullscreen)

        self.animations = QCheckBox('Animations')
        self.animations.setChecked(store.values['animations'])
        self.layout.addWidget(self.animations)

        self.ui_sounds = QCheckBox('UI sound effects')
        self.ui_sounds.setChecked(store.values.get('ui_sounds', True))
        self.layout.addWidget(self.ui_sounds)

        self._section('Notch colour')
        self.notch_colour = QComboBox()
        for colour in ('#000000', '#141518', '#1d1f23', '#242c3a'):
            self.notch_colour.addItem(colour)
        self.notch_colour.setCurrentText(store.values['notch_colour'])
        self.layout.addWidget(self.notch_colour)

        self._section('Eye colour')
        self.eye_colour = QComboBox()
        for colour in ('#ffffff', '#3882f6', '#34c759', '#ffcc00', '#ff3b30'):
            self.eye_colour.addItem(colour)
        self.eye_colour.setCurrentText(store.values['eye_colour'])
        self.layout.addWidget(self.eye_colour)

        self.theme.currentTextChanged.connect(lambda v: self.save(theme=v))
        self.opacity.valueChanged.connect(lambda v: self.save(opacity=v))
        self.fullscreen.toggled.connect(lambda v: self.save(hide_on_fullscreen=v))
        self.animations.toggled.connect(lambda v: self.save(animations=v))
        self.ui_sounds.toggled.connect(lambda v: self.save(ui_sounds=v))
        self.notch_colour.currentTextChanged.connect(lambda v: self.save(notch_colour=v))
        self.eye_colour.currentTextChanged.connect(lambda v: self.save(eye_colour=v))

        self.buttons([('Dock', self.dock_requested.emit), ('Pull out', self.face_out_requested.emit)])

        self._section('Voice & wake')
        self.buttons([
            ('Mute / unmute', self.mute_requested.emit),
            ('Wake on', lambda: self.action('actions.wake_word', 'wake_word', {'action': 'start'})),
            ('Wake off', lambda: self.action('actions.wake_word', 'wake_word', {'action': 'stop'})),
        ])

        self._section('Dictation')
        self.dictation = QComboBox()
        self.dictation.addItems(['Exact', 'Clean', 'Polished'])
        self.dictation.setCurrentText(store.values['dictation_style'])
        self.layout.addWidget(self.dictation)
        self.dictation.currentTextChanged.connect(lambda v: self.save(dictation_style=v))

        self.paste_mode = QComboBox()
        self.paste_mode.addItems(['paste', 'clipboard'])
        self.paste_mode.setCurrentText(store.values['paste_mode'])
        self.layout.addWidget(self.paste_mode)
        self.paste_mode.currentTextChanged.connect(lambda v: self.save(paste_mode=v))

        self._section('Guardian & health')
        self.buttons([
            ('Report', lambda: self.action('actions.guardian', 'guardian_action', {'action': 'report'})),
            ('On', lambda: self.action('actions.guardian', 'guardian_action', {'action': 'enable'})),
            ('Off', lambda: self.action('actions.guardian', 'guardian_action', {'action': 'disable'})),
        ])

        self._section('System status')
        self.buttons([('Refresh system status', lambda: self.action('actions.system_status', 'system_status', {}))])

        self._section('Advanced')
        self.buttons([('Quit FRIDAY', self.quit_requested.emit)])
        self.layout.addWidget(self.result)

    def _section(self, title: str):
        lbl = QLabel(title)
        lbl.setObjectName('sectionLabel')
        self.layout.addWidget(lbl)

    def save(self, **changes):
        try:
            self.store.update(**changes)
        except OSError as exc:
            self.result.setText(f'Could not save settings: {exc}')
            return
        self.settings_changed.emit(dict(self.store.values))

    def action(self, module, name, params):
        self.services.call_action(module, name, params, self.finish)