from .common import Panel

class MediaPanel(Panel):
    def __init__(self, services):
        super().__init__(services, 'Media controls')
        for row in ((('Previous', 'prev'), ('Play / Pause', 'toggle'), ('Next', 'next')),
                    (('Volume −', 'volume_down'), ('Volume +', 'volume_up'), ('Mute', 'mute'))):
            self.buttons([(label, lambda checked=False, action=action: self.control(action)) for label, action in row])
        self.layout.addWidget(self.result); self.layout.addStretch()
    def control(self, action):
        self.services.call_action('actions.media_control', 'media_control_action', {'action': action}, self.finish)
