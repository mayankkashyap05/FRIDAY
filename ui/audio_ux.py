"""FRIDAY ambient UI — micro-earcons and background media ducking.

Two independent, fully optional concerns:

1. **Earcons** — short synthesized cues ("wake", "listen", "done", "error").
   Generating them with pure stdlib ``wave`` + ``math`` avoids shipping binary
   assets and avoids a numpy dependency at this layer. Playback goes through
   ``winsound`` on Windows (zero-dependency, no device enumeration) with a
   ``sounddevice`` fallback elsewhere.

2. **Ducking** — when FRIDAY speaks or listens, drop every *other* app's audio
   session so it can hear itself / the user. Uses ``pycaw`` (already a project
   dependency) with COM initialised on the calling thread.

Guarantees:
* No function raises. Ever. This layer gates user experience, not correctness.
* Playback never blocks the caller: audio goes to a daemon thread.
* Ducking is idempotent and always restorable via :meth:`AudioUX.stop`.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field

IS_WINDOWS = sys.platform == "win32"


# ─────────────────────────────────────────────────────────────────────────────
# Tone synthesis
# ─────────────────────────────────────────────────────────────────────────────

RATE = 44_100


def _envelope(i: int, n: int, attack: float = 0.12, release: float = 0.35) -> float:
    """Asymmetric raised-cosine envelope — no clicks, no clipping."""
    frac = i / max(1, n - 1)
    a = min(1.0, frac / attack) if attack > 0 else 1.0
    r = min(1.0, (1.0 - frac) / release) if release > 0 else 1.0
    shape = 0.5 - 0.5 * math.cos(math.pi * min(a, r))
    return shape


def synth_earcon(
    freqs: tuple[float, ...],
    duration: float = 0.16,
    gain: float = 0.22,
    sweep: float = 0.0,
    harmonics: tuple[float, ...] = (1.0, 0.35, 0.12),
) -> bytes:
    """Render a short PCM16 mono WAV in memory.

    ``freqs`` are played as a chord; ``sweep`` bends everything upward (Hz)
    across the duration, which is what makes the wake cue feel "alive".
    """
    n = max(64, int(RATE * duration))
    frames = bytearray()
    for i in range(n):
        env = _envelope(i, n)
        if env <= 0.0:
            frames += b"\x00\x00"
            continue
        t = i / RATE
        bend = 1.0 + (sweep * (i / n)) / max(1.0, freqs[0])
        sample = 0.0
        for f in freqs:
            for h in harmonics:
                sample += math.sin(2 * math.pi * f * h * bend * t) / (len(freqs) * len(harmonics))
        value = int(max(-1.0, min(1.0, sample * env * gain)) * 32767)
        frames += struct.pack("<h", value)
    return _wrap_wav(bytes(frames))


def _wrap_wav(pcm: bytes) -> bytes:
    data_len = len(pcm)
    header = (
        b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
        + b"data" + struct.pack("<I", data_len)
    )
    return header + pcm


# Distinct, short, non-annoying cues. Each is < 220 ms.
EARCON_SPECS: dict[str, dict] = {
    # Soft rising pair — "I'm awake"
    "wake":    {"freqs": (523.25, 783.99), "duration": 0.155, "sweep": 90.0, "gain": 0.20},
    # Single low tick — "listening"
    "listen":  {"freqs": (392.00,), "duration": 0.085, "gain": 0.13},
    # Two-step up — "thinking"
    "think":   {"freqs": (659.25,), "duration": 0.070, "gain": 0.10},
    # Major third down — "done"
    "done":    {"freqs": (783.99, 622.25), "duration": 0.130, "gain": 0.14},
    # Tritone — "something went wrong" (deliberately unresolved)
    "error":   {"freqs": (466.16, 659.25), "duration": 0.190, "gain": 0.17},
    # Ultra-soft click — "muted"
    "mute":    {"freqs": (261.63,), "duration": 0.060, "gain": 0.10},
    # Confirmation chime for security prompts
    "confirm": {"freqs": (523.25, 659.25, 987.77), "duration": 0.200, "gain": 0.16},
}


@dataclass
class _Player:
    played: int = 0
    failed: int = 0
    last: str = ""
    cache: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Ducking (pycaw)
# ─────────────────────────────────────────────────────────────────────────────

class _Ducker:
    """Lower every other app's volume while FRIDAY talks.

    pycaw is already a project dependency, but it is Windows-only and COM is
    thread-affine, so all work happens on a dedicated worker thread that
    initialises COM itself.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._levels: dict[str, float] = {}
        self._active = False
        self._available: bool | None = None
        self._reason = ""
        self._pid = os.getpid()

    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = self._probe()
        return bool(self._available)

    def _probe(self) -> bool:
        if not IS_WINDOWS:
            self._reason = f"not windows ({sys.platform})"
            return False
        try:
            import pycaw.pycaw  # noqa: F401
            return True
        except Exception as exc:
            self._reason = str(exc)
            return False

    def _com(self):
        """Initialise COM for this thread; return the AudioUtilities helper."""
        import comtypes
        from pycaw.utils import AudioUtilities

        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        return AudioUtilities

    def duck(self, factor: float = 0.25) -> bool:
        if not self.available:
            return False
        with self._lock:
            if self._active:
                return True
            self._active = True
        self._thread = threading.Thread(
            target=self._run, args=(factor,), daemon=True, name="AudioDuckThread")
        self._thread.start()
        return True

    def _run(self, factor: float) -> None:
        try:
            AudioUtilities = self._com()
            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                try:
                    if session.Process and session.Process.pid == self._pid:
                        continue
                    volume = session.SimpleAudioVolume
                    if volume is None:
                        continue
                    key = str(getattr(session, "SessionIdentifier", id(session)))
                    self._levels[key] = float(volume.GetMasterVolume())
                    volume.SetMasterVolume(max(0.0, min(1.0, self._levels[key] * factor)), None)
                except Exception:
                    continue
        except Exception as exc:
            self._reason = str(exc)
        finally:
            try:
                import comtypes

                comtypes.CoUninitialize()
            except Exception:
                pass

    def restore(self) -> bool:
        with self._lock:
            if not self._active or not self._levels:
                self._active = False
                return False
            levels = dict(self._levels)
            self._levels.clear()
            self._active = False

        def _restore() -> None:
            try:
                AudioUtilities = self._com()
                for session in AudioUtilities.GetAllSessions():
                    try:
                        key = str(getattr(session, "SessionIdentifier", id(session)))
                        if key in levels and session.SimpleAudioVolume is not None:
                            session.SimpleAudioVolume.SetMasterVolume(levels[key], None)
                    except Exception:
                        continue
            except Exception:
                pass
            finally:
                try:
                    import comtypes

                    comtypes.CoUninitialize()
                except Exception:
                    pass

        threading.Thread(target=_restore, daemon=True, name="AudioUnduckThread").start()
        return True

    def status(self) -> dict:
        return {
            "available": self.available,
            "active": self._active,
            "reason": self._reason,
            "sessions": len(self._levels),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Public façade
# ─────────────────────────────────────────────────────────────────────────────

class AudioUX:
    """Earcons + ducking, with global kill-switches.

    ``AudioUX(enabled=True, ducking=True, sounddevice_module=None)``
    """

    def __init__(
        self,
        enabled: bool = True,
        ducking: bool = True,
        duck_factor: float = 0.25,
        sounddevice_module=None,
        respect_dnd: bool = True,
    ) -> None:
        self.enabled = bool(enabled)
        self.ducking_enabled = bool(ducking)
        self.duck_factor = float(duck_factor)
        self.respect_dnd = bool(respect_dnd)
        self._sd = sounddevice_module
        self._player = _Player()
        self._ducker = _Ducker()
        self._tmpdir: str | None = None
        self._lock = threading.Lock()
        self._dnd_fn = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def set_dnd_probe(self, fn) -> None:
        """Inject ``callable() -> bool`` (keeps this module import-light)."""
        self._dnd_fn = fn

    def _suppressed_by_dnd(self) -> bool:
        if not self.respect_dnd or self._dnd_fn is None:
            return False
        try:
            return bool(self._dnd_fn())
        except Exception:
            return False

    # ── earcons ──────────────────────────────────────────────────────────────
    def wav_for(self, name: str) -> bytes | None:
        spec = EARCON_SPECS.get(name)
        if spec is None:
            return None
        with self._lock:
            if name in self._player.cache:
                return self._player.cache[name]
        try:
            data = synth_earcon(spec["freqs"], spec.get("duration", 0.16),
                                spec.get("gain", 0.18), spec.get("sweep", 0.0))
        except Exception:
            return None
        with self._lock:
            self._player.cache[name] = data
        return data

    def play(self, name: str, blocking: bool = False) -> bool:
        """Fire an earcon. Returns False if it was intentionally skipped."""
        if not self.enabled or self._suppressed_by_dnd():
            return False
        data = self.wav_for(name)
        if data is None:
            return False
        if blocking:
            return self._play_blocking(name, data)
        threading.Thread(target=self._play_blocking, args=(name, data),
                         daemon=True, name=f"Earcon-{name}").start()
        return True

    def _play_blocking(self, name: str, data: bytes) -> bool:
        try:
            if IS_WINDOWS:
                import winsound

                path = self._ensure_wav_file(name, data)
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT
                                   | winsound.SND_ASYNC)
                self._player.played += 1
                self._player.last = name
                return True
            sd = self._sd
            if sd is None:
                try:
                    import sounddevice as sd  # type: ignore
                except Exception:
                    return False
            import wave
            import io

            with wave.open(io.BytesIO(data), "rb") as wf:
                frames = wf.readframes(wf.getnframes())
                rate = wf.getframerate()
            import array

            samples = array.array("h")
            samples.frombytes(frames)
            sd.play(samples, rate, blocking=True)
            self._player.played += 1
            self._player.last = name
            return True
        except Exception:
            self._player.failed += 1
            return False

    def _ensure_wav_file(self, name: str, data: bytes) -> str:
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="friday_earcons_")
        path = os.path.join(self._tmpdir, f"{name}.wav")
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(data)
        return path

    # ── speech-state helpers used by the controller ──────────────────────────
    def on_speaking_start(self) -> None:
        if self.ducking_enabled:
            self._ducker.duck(self.duck_factor)

    def on_speaking_end(self) -> None:
        if self.ducking_enabled:
            self._ducker.restore()

    # ── shutdown ─────────────────────────────────────────────────────────────
    def stop(self) -> None:
        try:
            self._ducker.restore()
        except Exception:
            pass
        if self._tmpdir:
            try:
                import shutil

                shutil.rmtree(self._tmpdir, ignore_errors=True)
            except Exception:
                pass
            self._tmpdir = None
        if IS_WINDOWS:
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass

    # ── introspection ────────────────────────────────────────────────────────
    def status(self) -> dict:
        with self._lock:
            played, failed, last = self._player.played, self._player.failed, self._player.last
        return {
            "enabled": self.enabled,
            "played": played,
            "failed": failed,
            "last": last,
            "ducker": self._ducker.status(),
            "cached": sorted(self._player.cache),
        }


__all__ = ["AudioUX", "synth_earcon", "EARCON_SPECS", "RATE"]
