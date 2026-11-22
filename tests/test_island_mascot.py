import pytest
pytest.importorskip('PySide6.QtGui', reason='Qt system libraries are required')
from ui.mascot.eyes import Blink, gaze
from ui.themes.tokens import readable_eye_colour, contrast


def test_gaze_bounded():
    x, y = gaze(1e6, -1e6)
    assert -1 <= x <= 1 and -1 <= y <= 1


def test_blink_closes_and_reopens():
    blink = Blink(); blink.next_blink = 1
    assert blink.value(1) == 1
    assert blink.value(1.06) < .1
    assert blink.value(1.2) == 1


def test_eye_contrast():
    for bg in ('#000000', '#F5F6F8'):
        assert contrast(readable_eye_colour('#888888', bg), bg) >= 4.5


def test_asymmetric_blink_closes_faster_than_it_opens():
    blink = Blink(); blink.next_blink = 1
    blink.value(1)
    assert blink.value(1.06) == pytest.approx(.08)
    assert blink.value(1.12) == pytest.approx(.54)
    assert blink.value(1.18) == pytest.approx(1)


def test_gaze_spring_smoothly_converges():
    from ui.island.physics import Spring
    spring = Spring(0, .22, .88); spring.target = gaze(260, 0)[0]
    assert 0 < spring.step(1/60) < spring.target
    for _ in range(100): spring.step(1/60)
    assert spring.settled and spring.value == spring.target


def test_poke_mood_tracks_dizzy_window():
    from ui.mascot.moods import PokeMood
    mood = PokeMood()
    assert not mood.poke(0) and not mood.poke(.5)
    assert mood.poke(1.0) and mood.is_dizzy(2.9)
    assert not mood.is_dizzy(3.0)
    assert not mood.poke(5) and not mood.poke(7)


def test_waveform_attack_and_release_are_smoothed():
    from ui.mascot.waveform import Waveform
    waveform = Waveform()
    first = waveform.step(1, 1/60, 0)
    assert 0 < max(first) < 1
    for i in range(30): waveform.step(1, 1/60, i/60)
    previous = max(waveform.values)
    assert 0 < max(waveform.step(0, 1/60, .5)) < previous
    for i in range(180): waveform.step(0, 1/60, .5+i/60)
    assert max(waveform.values) == 0
