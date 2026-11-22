"""Smoothed voice-level envelope for five independent display bars."""
import math
from ui.island.physics import Spring


class Waveform:
    def __init__(self):
        self.bars = [Spring(0, 0.18 + i * 0.016, 0.82) for i in range(5)]

    def step(self, level, dt, now, *, active_floor=0.0):
        level = max(0.0, min(1.0, float(level)))
        effective = max(level, max(0.0, min(0.35, float(active_floor))))
        weights = (0.48, 0.82, 1.0, 0.82, 0.48)
        for i, spring in enumerate(self.bars):
            phase = (
                0.70
                + 0.22 * math.sin(now * 7.8 + i * 1.05)
                + 0.08 * math.cos(now * 13.0 - i * 0.7)
            )
            spring.target = effective * weights[i] * phase
            spring.step(dt)
        return self.values

    @property
    def values(self):
        return [max(0.0, min(1.0, bar.value)) for bar in self.bars]