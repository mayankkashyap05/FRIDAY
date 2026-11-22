"""Transient eye interactions, independent of rendering and wall clock."""
from collections import deque


class PokeMood:
    def __init__(self):
        self.pokes = deque()
        self.dizzy_until = 0.0
        self.boop_until = 0.0

    def poke(self, now):
        while self.pokes and now - self.pokes[0] > 1.2:
            self.pokes.popleft()
        self.pokes.append(now)
        self.boop_until = now + 0.32
        if len(self.pokes) >= 3:
            self.dizzy_until = now + 2.0
            self.pokes.clear()
        return self.is_dizzy(now)

    def is_dizzy(self, now):
        return now < self.dizzy_until

    def is_booped(self, now):
        return now < self.boop_until