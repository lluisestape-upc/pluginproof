"""Test-signal generators for PluginProof.

Every generator returns ``(signal, samplerate)`` where ``signal`` is a
float32 **mono** numpy array shaped ``(samples,)``, per CONTRACT.md.

Design notes
------------
* All periodic signals get a short raised-cosine fade in/out so that
  plugins with state (filters, dynamics) are not hit with a hard edge,
  and so spectral leakage from truncation stays low.
* Amplitudes are kept at or below 0.5 for analysis signals (headroom for
  plugins that add gain), except :func:`full_scale_extremes`, whose whole
  point is to poke at the +/-1.0 boundaries.
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "sine_sweep",
    "multitone",
    "hf_tone",
    "silence",
    "full_scale_extremes",
]

# Default analysis amplitude: -6 dBFS leaves headroom for gain plugins.
_DEFAULT_AMP = 0.5
_FADE_SECONDS = 0.005  # 5 ms raised-cosine edges


def _fade_edges(x: np.ndarray, sr: int, fade_s: float = _FADE_SECONDS) -> np.ndarray:
    """Apply a raised-cosine fade-in/out in place-safe fashion."""
    n_fade = min(int(sr * fade_s), len(x) // 4)
    if n_fade < 2:
        return x
    ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, n_fade)))
    x = x.copy()
    x[:n_fade] *= ramp
    x[-n_fade:] *= ramp[::-1]
    return x


def _finish(x: np.ndarray) -> np.ndarray:
    """Cast to the contract dtype (float32 mono)."""
    return np.ascontiguousarray(x, dtype=np.float32)


def sine_sweep(sr: int, dur: float = 2.0) -> tuple[np.ndarray, int]:
    """Exponential (log) sine sweep from 20 Hz up to 0.45 * sr.

    Log sweeps put more energy per octave in the low end, which gives the
    Welch transfer-function estimate in ``measurements.frequency_response``
    solid SNR across the whole audio band.
    """
    n = int(round(sr * dur))
    t = np.arange(n) / sr
    f0, f1 = 20.0, 0.45 * sr
    # Phase of an exponential sweep: phi(t) = 2*pi*f0*T/ln(f1/f0) * (e^(t/T*ln(f1/f0)) - 1)
    k = np.log(f1 / f0)
    phase = 2.0 * np.pi * f0 * (dur / k) * (np.exp(t / dur * k) - 1.0)
    sig = _DEFAULT_AMP * np.sin(phase)
    return _finish(_fade_edges(sig, sr)), sr


def multitone(sr: int, dur: float = 2.0) -> tuple[np.ndarray, int]:
    """Sum of log-spaced, non-harmonically-related tones.

    Frequencies are chosen so no tone is an integer multiple of another;
    intermodulation products of a nonlinear plugin therefore land on
    otherwise-empty bins. Phases are fixed (seeded) and randomised to keep
    the crest factor reasonable. Peak is normalised to 0.5.
    """
    n = int(round(sr * dur))
    t = np.arange(n) / sr
    # Prime-ish frequencies, roughly one per octave, capped below 0.4 * Nyquist.
    candidates = np.array([53.0, 127.0, 293.0, 631.0, 997.0, 2503.0, 5011.0, 9973.0])
    freqs = candidates[candidates < 0.4 * (sr / 2.0)]
    rng = np.random.default_rng(1234)  # fixed seed: deterministic signal
    phases = rng.uniform(0.0, 2.0 * np.pi, size=len(freqs))
    sig = np.zeros(n)
    for f, ph in zip(freqs, phases):
        sig += np.sin(2.0 * np.pi * f * t + ph)
    sig *= _DEFAULT_AMP / np.max(np.abs(sig))
    return _finish(_fade_edges(sig, sr)), sr


def hf_tone(sr: int, dur: float = 1.0, freq: float | None = None) -> tuple[np.ndarray, int]:
    """Single high-frequency tone used to expose aliasing.

    Default frequency is 0.41 * Nyquist — deliberately *not* a neat
    divisor of the samplerate, so folded harmonic images land on
    non-harmonic frequencies where :func:`measurements.aliasing_score`
    can see them.
    """
    if freq is None:
        freq = 0.41 * (sr / 2.0)
    if not 0.0 < freq < sr / 2.0:
        raise ValueError(f"hf_tone freq must be inside (0, Nyquist); got {freq} at sr={sr}")
    n = int(round(sr * dur))
    t = np.arange(n) / sr
    sig = _DEFAULT_AMP * np.sin(2.0 * np.pi * freq * t)
    return _finish(_fade_edges(sig, sr)), sr


def silence(sr: int, dur: float = 1.0) -> tuple[np.ndarray, int]:
    """Digital silence — flushes out plugins that self-oscillate, leak
    denormals, or emit DC on zero input."""
    n = int(round(sr * dur))
    return np.zeros(n, dtype=np.float32), sr


def full_scale_extremes(sr: int, dur: float = 1.0) -> tuple[np.ndarray, int]:
    """Stress signal that lives at the +/-1.0 rails.

    Layout (quarters of the buffer):
      1. DC at +1.0            — sustained positive rail
      2. DC at -1.0            — sustained negative rail
      3. ~100 Hz square wave   — repeated full-swing steps
      4. sparse +/-1 impulses  — isolated transients into silence

    Good at flushing out clip/wrap bugs, unstable filters, and NaN
    producers (e.g. log/sqrt of a rail value inside a plugin).
    """
    n = int(round(sr * dur))
    sig = np.zeros(n)
    q = n // 4

    sig[:q] = 1.0
    sig[q:2 * q] = -1.0

    # Full-scale square wave at ~100 Hz.
    t = np.arange(2 * q) / sr
    sig[2 * q:2 * q + len(t) // 2] = np.sign(np.sin(2.0 * np.pi * 100.0 * t[: len(t) // 2]))

    # Sparse alternating impulses every ~10 ms in the last quarter.
    start = 3 * q
    step = max(int(0.010 * sr), 1)
    idx = np.arange(start, n, step)
    sig[idx] = np.where(np.arange(len(idx)) % 2 == 0, 1.0, -1.0)

    return _finish(sig), sr
