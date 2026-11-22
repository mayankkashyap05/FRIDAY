"""Deterministic port of the timer-based Coucou island FSM.

Call tick() from a UI timer; an injectable clock makes timing testable.
"""
import math
import time


class IslandStateMachine:
    def __init__(self, clock=time.monotonic, on_transition=None):
        self.clock, self.on_transition = clock, on_transition
        self.state = 'hidden'
        self.home_to_petit_delay = 15.0
        self.petit_to_hidden_delay = 60.0
        self.greet_auto_collapse_delay = .6
        self.greet_hover_collapse_delay = 10.0
        self.hover_close_delay = .6
        self.hover_open_delay = .5
        self.open_on_hover = self.pinned = self.opened_by_hover = False
        self._timers = {}

    @property
    def home_collapse_due_at(self):
        return self._timers.get('home')

    def set_home_delay(self, delay):
        if not math.isfinite(delay) or delay < 0 or delay == self.home_to_petit_delay:
            return
        self.home_to_petit_delay = delay
        if self.state == 'home' and 'home' in self._timers:
            self._schedule_home()

    def _transition(self, state):
        old = self.state
        if old != state:
            self.state = state
            if self.on_transition:
                self.on_transition(old, state)

    def _schedule(self, key, delay):
        self._timers[key] = self.clock() + delay

    def cancel_timers(self):
        self._timers.clear()

    def _schedule_home(self):
        self._timers.pop('home', None)
        if not self.pinned:
            self._schedule('home', self.hover_close_delay if self.opened_by_hover else self.home_to_petit_delay)

    def launch(self):
        self.cancel_timers(); self._transition('coucou')

    def mouse_entered(self):
        if self.state == 'hidden':
            self.cancel_timers(); self._transition('petit')
        if self.state == 'petit':
            self._timers.pop('petit', None)
            if self.open_on_hover and not self.pinned:
                self._schedule('hover', self.hover_open_delay)
        elif self.state == 'home':
            self._timers.pop('home', None)
        elif self.state == 'coucou':
            self._schedule('greet', self.greet_hover_collapse_delay)

    def mouse_left(self):
        self._timers.pop('hover', None)
        if self.state == 'petit' and not self.pinned:
            self._schedule('petit', self.petit_to_hidden_delay)
        elif self.state == 'home':
            self._schedule_home()
        elif self.state == 'coucou':
            self._timers.pop('greet', None); self._transition('petit')

    def click(self):
        self.opened_by_hover = False
        if self.state == 'petit':
            self.cancel_timers(); self._transition('home')

    def greet_complete(self):
        if self.state == 'coucou' and 'greet' not in self._timers:
            self._schedule('greet', self.greet_auto_collapse_delay)

    def reveal(self):
        if self.state == 'hidden':
            self.cancel_timers(); self._transition('petit')
            if not self.pinned:
                self._schedule('petit', self.petit_to_hidden_delay)

    def user_interacted(self):
        if self.opened_by_hover:
            self.opened_by_hover = False
            if self.state == 'home' and 'home' in self._timers:
                self._schedule_home()

    def force_home(self):
        self.opened_by_hover = False; self.cancel_timers(); self._transition('home')

    def force_petit(self):
        self.opened_by_hover = False; self.cancel_timers(); self._transition('petit')

    def force_hidden(self):
        self.opened_by_hover = False; self.cancel_timers(); self._transition('hidden')

    def tick(self):
        now = self.clock()
        for key, due in sorted(list(self._timers.items()), key=lambda item: item[1]):
            if due > now or self._timers.get(key) != due:
                continue
            self._timers.pop(key, None)
            if key == 'hover' and self.state == 'petit' and not self.pinned and self.open_on_hover:
                self.cancel_timers(); self.opened_by_hover = True; self._transition('home')
            elif key == 'home' and self.state == 'home' and not self.pinned:
                self.opened_by_hover = False; self._transition('petit')
            elif key == 'petit' and self.state == 'petit' and not self.pinned:
                self._transition('hidden')
            elif key == 'greet' and self.state == 'coucou':
                self._transition('petit')
