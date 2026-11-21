# FRIDAY — playback release policy
#
# Shared between main.py (the live audio path) and the UI layer so the numbers
# cannot drift apart. Pure stdlib; safe to import anywhere.

#: How long the audio queue must stay empty before the turn is considered
#: finished. The old code released on a single `audio_in_queue.empty()` sample,
#: which is true between *every* chunk at the network's natural pacing — so a
#: reply could be cut off mid-sentence.
AUDIO_END_GRACE_S = 0.35

#: Hard ceiling on the "speaking" flag when a turn never reports completion
#: (e.g. the socket dies mid-reply). The old timeout was 25s, which is longer
#: than a typical spoken reply, so the watchdog fired at the *end* of normal
#: replies rather than as a genuine backstop.
SPEAKING_WATCHDOG_S = 8.0

#: How often the watchdog samples. Kept small so the release is prompt.
SPEAKING_WATCHDOG_TICK_S = 0.5

__all__ = [
    "AUDIO_END_GRACE_S",
    "SPEAKING_WATCHDOG_S",
    "SPEAKING_WATCHDOG_TICK_S",
]
