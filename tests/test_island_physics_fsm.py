import pytest
from ui.island.physics import Spring, close_curve
from ui.island.fsm import IslandStateMachine


def test_spring_converges_and_sleeps():
    s = Spring(0); s.target = 100
    for _ in range(240): s.step(1/60)
    assert s.settled and s.value == 100 and s.velocity == 0
    assert s.step(1/60) == 100


def test_spring_frame_clamp_and_curve_endpoints():
    a, b = Spring(0), Spring(0)
    a.target = b.target = 10
    a.step(1); b.step(.05)
    assert a.value == b.value
    assert close_curve(0) == 0 and close_curve(1) == 1
    assert close_curve(.25) < close_curve(.75)


class Clock:
    t = 0
    def __call__(self): return self.t
    def advance(self, duration): self.t += duration


def test_hover_open_close_and_petit_hide():
    clock = Clock(); fsm = IslandStateMachine(clock); fsm.open_on_hover = True
    fsm.mouse_entered(); assert fsm.state == 'petit'
    clock.advance(.49); fsm.tick(); assert fsm.state == 'petit'
    clock.advance(.02); fsm.tick(); assert fsm.state == 'home' and fsm.opened_by_hover
    fsm.mouse_left(); clock.advance(.61); fsm.tick(); assert fsm.state == 'petit'
    fsm.mouse_left(); clock.advance(60); fsm.tick(); assert fsm.state == 'hidden'


def test_click_normal_collapse_and_pinned_timer():
    clock = Clock(); fsm = IslandStateMachine(clock)
    fsm.reveal(); fsm.click(); assert fsm.state == 'home'
    fsm.mouse_left(); fsm.pinned = True; clock.advance(16); fsm.tick(); assert fsm.state == 'home'
    fsm.pinned = False; fsm.mouse_left(); fsm.set_home_delay(2)
    clock.advance(2); fsm.tick(); assert fsm.state == 'petit'


def test_greeting_reveal_and_force():
    clock = Clock(); fsm = IslandStateMachine(clock)
    fsm.launch(); fsm.greet_complete(); clock.advance(.6); fsm.tick(); assert fsm.state == 'petit'
    fsm.force_hidden(); fsm.reveal(); assert fsm.state == 'petit'
    fsm.force_home(); assert fsm.state == 'home'
    fsm.force_petit(); assert fsm.state == 'petit'
