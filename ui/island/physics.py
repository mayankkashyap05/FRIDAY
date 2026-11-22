"""Spring and close curve derived from Coucou's core/anim.ts.

The reference clamps frame time at the caller in island/island.ts, not in
Spring.step. This implementation clamps there in step for safer standalone use.
"""
import math


def clamp(value, low, high):
    return max(low, min(high, value))


def cubic_bezier(x1, y1, x2, y2):
    def component(t, a, b):
        return 3 * (1-t)**2 * t * a + 3 * (1-t) * t*t * b + t**3
    def ease(x):
        x = clamp(x, 0, 1)
        if x in (0, 1):
            return float(x)
        lo, hi, t = 0.0, 1.0, x
        for _ in range(12):
            if component(t, x1, x2) < x:
                lo = t
            else:
                hi = t
            t = (lo + hi) / 2
        return component(t, y1, y2)
    return ease


close_curve = cubic_bezier(.45, 0, .2, 1)


class Spring:
    def __init__(self, value, response=.5, damping=.72):
        self.value = self.target = float(value)
        self.velocity = 0.0
        self.configure(response, damping)

    def configure(self, response, damping):
        if response <= 0 or damping < 0:
            raise ValueError('invalid spring parameters')
        self.omega = 2 * math.pi / response
        self.zeta = damping

    @property
    def settled(self):
        return abs(self.target-self.value) < .01 and abs(self.velocity) < .05

    def set(self, value):
        self.value = self.target = float(value)
        self.velocity = 0.0

    def step(self, dt):
        dt = clamp(dt, 0, .05)
        steps = max(1, math.ceil(dt / (1/240)))
        h = dt / steps
        for _ in range(steps):
            acc = self.omega**2 * (self.target-self.value) - 2*self.zeta*self.omega*self.velocity
            self.velocity += acc*h
            self.value += self.velocity*h
        if self.settled:
            self.set(self.target)
        return self.value


class Tracked(Spring):
    """Spring on expansion and follow Coucou's 340 ms curve on contraction."""
    def __init__(self, value):
        super().__init__(value, .42, .74)
        self._curve_elapsed = None
        self._curve_from = self.value
        self._curve_duration = .34

    @property
    def settled(self):
        return self._curve_elapsed is None and super().settled

    def set(self, value):
        self._curve_elapsed = None
        super().set(value)

    def animate_to(self, target):
        target = float(target)
        if target == self.target:
            return
        if target < self.value:
            self._curve_from = self.value
            self._curve_elapsed = 0.0
            self.target = target
            self.velocity = 0.0
        else:
            self._curve_elapsed = None
            self.configure(.42, .74)
            self.target = target

    def step(self, dt):
        if self._curve_elapsed is None:
            return super().step(dt)
        self._curve_elapsed = min(self._curve_duration, self._curve_elapsed + max(0.0, dt))
        progress = self._curve_elapsed / self._curve_duration
        self.value = self._curve_from + (self.target - self._curve_from) * close_curve(progress)
        if progress >= 1:
            self.set(self.target)
        return self.value
