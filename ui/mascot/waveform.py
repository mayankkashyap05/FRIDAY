"""Smoothed voice-level envelope for five independent display bars."""
import math
from ui.island.physics import Spring


class Waveform:
    def __init__(self):
        self.bars = [Spring(0, .2 + i * .018, .86) for i in range(5)]

    def step(self, level, dt, now):
        level = max(0.0, min(1.0, float(level)))
        for i, spring in enumerate(self.bars):
            phase = .72 + .28 * math.sin(now * 7 + i * .9)
            spring.target = level * (0.5, .8, 1, .8, .5)[i] * phase
            spring.step(dt)
        return self.values

    @property
    def values(self):
        return [max(0.0, bar.value) for bar in self.bars]
