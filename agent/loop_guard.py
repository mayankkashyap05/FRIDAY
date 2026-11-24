"""
loop_guard.py — Anti-loop protection for Friday's tool calls.
--------------------------------------------------------------
Guards against two failure modes while the model drives tools in a live
session:

  1. Identical tool + arguments called repeatedly (e.g. stuck on retrying the
     same failing action) — blocked after `max_identical` consecutive calls.
  2. Ping-pong loops between exactly two tools (A, B, A, B, ...) — blocked when
     the alternation repeats `ping_pong` times.

When a loop is detected, `check()` returns a short instruction string for the
model ("stop and report the last result"); the caller passes it back as the
tool result so the model can break the cycle instead of apologizing blindly.
"""

import json


class LoopGuard:

    def __init__(self, max_identical: int = 3, ping_pong: int = 2):
        self.max_identical = max(2, int(max_identical))
        self.ping_pong     = max(2, int(ping_pong))
        self._last_sig     = None
        self._same_count   = 0
        self._recent       = []

    @staticmethod
    def _signature(name: str, args: dict) -> tuple:
        try:
            compact = json.dumps(dict(args or {}), sort_keys=True, default=str)
        except Exception:
            compact = str(args)
        return (name, compact[:200])

    def check(self, name: str, args: dict) -> str | None:
        """Record a tool call; return a block instruction string if a loop is
        detected, otherwise None."""
        sig = self._signature(name, args)

        if sig == self._last_sig:
            self._same_count += 1
        else:
            self._last_sig   = sig
            self._same_count = 1

        if self._same_count >= self.max_identical:
            return (
                f"Loop guard: tool '{name}' was called {self._same_count} times "
                f"in a row with identical arguments. This is a loop — do NOT call "
                f"'{name}' again. Stop and report the current state to the user."
            )

        window = 2 * max(2, self.ping_pong)
        self._recent.append(name)
        if len(self._recent) > max(8, window):
            self._recent.pop(0)

        if len(self._recent) >= window:
            tail = self._recent[-window:]
            a, b = tail[0], tail[1]
            if a != b and all(tail[i] == (a if i % 2 == 0 else b) for i in range(window)):
                return (
                    f"Loop guard: detected a ping-pong loop between '{a}' and "
                    f"'{b}'. Stop alternating these tools and report the last "
                    f"successful result to the user."
                )
        return None

    def reset(self) -> None:
        self._last_sig   = None
        self._same_count = 0
        self._recent     = []
