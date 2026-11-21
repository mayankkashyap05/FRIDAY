"""Audio band extraction helpers for FRIDAY (NumPy rfft).

Provides zero-allocation-overhead helper `_extract_audio_bands(pcm_int16, sample_rate)`
returning (rms, low, mid, high) clamped to [0.0, 1.0].
"""

import numpy as np
from typing import Tuple

# Cached Hann windows per chunk length (max 2048 for 16kHz/24kHz at 128ms)
_HANN_WINDOWS: dict[int, np.ndarray] = {}


def _get_hann(n: int) -> np.ndarray:
    """Return cached Hann window for length n."""
    if n not in _HANN_WINDOWS:
        _HANN_WINDOWS[n] = np.hanning(n)
    return _HANN_WINDOWS[n]


def _extract_audio_bands(pcm_int16: np.ndarray, sample_rate: int) -> Tuple[float, float, float, float]:
    """Extract RMS and 3-band spectral energies (low, mid, high) from PCM16.

    Args:
        pcm_int16: 1D numpy array of int16 samples.
        sample_rate: Sample rate in Hz (e.g., 16000 or 24000).

    Returns:
        (rms, low, mid, high) clamped to [0.0, 1.0].
    """
    n = len(pcm_int16)
    if n == 0:
        return 0.0, 0.0, 0.0, 0.0

    # RMS normalization
    rms = float(np.sqrt(np.mean(np.square(pcm_int16.astype(np.float32))))) / 32768.0
    rms = min(1.0, max(0.0, rms))

    # Hann window & FFT
    window = _get_hann(n)
    samples = pcm_int16.astype(np.float32) * window
    fft_vals = np.abs(np.fft.rfft(samples))
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)

    # Bin energies (60-260, 260-2200, 2200-7500)
    def _sum_bin(f_min: float, f_max: float) -> float:
        mask = (freqs >= f_min) & (freqs <= f_max)
        if not np.any(mask):
            return 0.0
        return float(np.sum(fft_vals[mask]))

    total_energy = float(np.sum(fft_vals))
    if total_energy == 0.0:
        return rms, 0.0, 0.0, 0.0

    low_e = _sum_bin(60.0, 260.0) / total_energy
    mid_e = _sum_bin(260.0, 2200.0) / total_energy
    high_e = _sum_bin(2200.0, 7500.0) / total_energy

    # Soft-knee compression (tanh) and noise-floor gating
    def _compress(x: float, k: float = 0.15) -> float:
        # tanh(x / k) * k -> smooth compression
        return float(np.tanh(x / k) * k)

    # Noise floor gate (threshold ~0.05)
    def _gate(x: float) -> float:
        return max(0.0, x - 0.05) if x > 0.05 else 0.0

    low = min(1.0, max(0.0, _compress(_gate(low_e))))
    mid = min(1.0, max(0.0, _compress(_gate(mid_e))))
    high = min(1.0, max(0.0, _compress(_gate(high_e))))

    return rms, low, mid, high
