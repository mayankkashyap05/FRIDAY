"""Subtle, procedural Hey Nixi / Coucou-inspired UI sound effects.

Synthesizes short, click-free 16-bit PCM WAV cues in memory using Python's
standard library. Plays asynchronously via Windows winsound when available and
fails silently on headless, offscreen, or audio-less systems.
"""
from __future__ import annotations

import io
import math
import os
import struct
import sys
import time
import wave

_SAMPLE_RATE = 22050


def _synth_wave(segments: list[tuple[float, float, float, float, float]]) -> bytes:
    """Render tone segments into an in-memory 16-bit mono WAV payload.

    Each segment is (start_s, duration_s, f_start_hz, f_end_hz, amplitude).
    Uses a smooth raised-cosine attack/decay envelope to avoid pops/clicks.
    """
    total_s = max((s + d for s, d, *_ in segments), default=0.05) + 0.01
    n_samples = max(1, int(total_s * _SAMPLE_RATE))
    buf = [0.0] * n_samples

    for start_s, dur_s, f0, f1, amp in segments:
        i0 = max(0, int(start_s * _SAMPLE_RATE))
        count = max(1, int(dur_s * _SAMPLE_RATE))
        i1 = min(n_samples, i0 + count)
        phase = 0.0
        attack = max(1, int(min(0.005, dur_s * 0.25) * _SAMPLE_RATE))
        release = max(1, int(min(0.025, dur_s * 0.65) * _SAMPLE_RATE))
        for idx in range(i0, i1):
            local = idx - i0
            t = local / max(1, count - 1)
            # Exponential frequency glide sounds more organic than linear.
            freq = f0 * ((f1 / f0) ** t) if f0 > 0 and f1 > 0 else f0
            phase += 2.0 * math.pi * freq / _SAMPLE_RATE
            # Fundamental + delicate 2nd harmonic for glassy warmth.
            sample = 0.86 * math.sin(phase) + 0.14 * math.sin(2.0 * phase)
            env = 1.0
            if local < attack:
                env = 0.5 * (1.0 - math.cos(math.pi * local / attack))
            elif local >= count - release:
                rem = max(0, count - 1 - local)
                env = 0.5 * (1.0 - math.cos(math.pi * rem / release))
            buf[idx] += sample * amp * env

    pcm = bytearray(n_samples * 2)
    for i, val in enumerate(buf):
        clamped = max(-0.95, min(0.95, val))
        struct.pack_into('<h', pcm, i * 2, int(clamped * 32767))

    out = io.BytesIO()
    with wave.open(out, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(bytes(pcm))
    return out.getvalue()


_CUE_SPECS: dict[str, list[tuple[float, float, float, float, float]]] = {
    'expand':        [(0.00, 0.055, 480.0, 680.0, 0.14), (0.03, 0.075, 680.0, 860.0, 0.12)],
    'collapse':      [(0.00, 0.075, 620.0, 380.0, 0.13)],
    'hover':         [(0.00, 0.022, 1080.0, 1180.0, 0.045)],
    'click':         [(0.00, 0.036, 740.0, 920.0, 0.12)],
    'listen_start':  [(0.00, 0.065, 523.25, 587.33, 0.14), (0.045, 0.095, 783.99, 820.0, 0.13)],
    'dictate_start': [(0.00, 0.048, 659.25, 680.0, 0.13), (0.052, 0.072, 880.0, 900.0, 0.13)],
    'dictate_stop':  [(0.00, 0.048, 880.0, 840.0, 0.12), (0.052, 0.078, 659.25, 620.0, 0.12)],
    'complete':      [(0.00, 0.060, 587.33, 587.33, 0.13),
                      (0.055, 0.070, 880.00, 880.00, 0.14),
                      (0.115, 0.110, 1174.66, 1185.0, 0.12)],
    'dock':          [(0.00, 0.045, 250.0, 340.0, 0.16), (0.028, 0.065, 520.0, 540.0, 0.11)],
    'detach':        [(0.00, 0.070, 340.0, 620.0, 0.14)],
    'mute':          [(0.00, 0.055, 440.0, 392.0, 0.13), (0.055, 0.080, 311.13, 277.18, 0.13)],
    'error':         [(0.00, 0.065, 310.0, 290.0, 0.15), (0.075, 0.090, 275.0, 240.0, 0.15)],
    'poke':          [(0.00, 0.065, 460.0, 740.0, 0.12)],
}


class UiSoundEngine:
    """Lightweight, non-blocking UI cue player that never raises on audio errors."""

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)
        self._cache: dict[str, bytes] = {}
        self._last_hover = 0.0

    def _can_play(self) -> bool:
        if not self.enabled:
            return False
        if os.environ.get('QT_QPA_PLATFORM', '').lower() == 'offscreen':
            return False
        return sys.platform == 'win32'

    def _get_wav(self, name: str) -> bytes | None:
        spec = _CUE_SPECS.get(name)
        if spec is None:
            return None
        data = self._cache.get(name)
        if data is None:
            data = _synth_wave(spec)
            self._cache[name] = data
        return data

    def play(self, cue: str) -> bool:
        if not self._can_play():
            return False
        if cue == 'hover':
            now = time.monotonic()
            if now - self._last_hover < 0.045:
                return False
            self._last_hover = now
        data = self._get_wav(cue)
        if not data:
            return False
        try:
            import winsound
            flags = winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            winsound.PlaySound(data, flags)
            return True
        except Exception:
            return False