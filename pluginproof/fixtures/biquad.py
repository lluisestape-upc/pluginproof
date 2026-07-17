"""Test fixtures: tiny pure-numpy "plugins" that satisfy the PluginHost Protocol.

These exist so the whole suite (measurements, baselines, CLI, reports) can be
exercised end-to-end without any external VST3/AU binaries installed.

- :class:`BiquadFixture` — an RBJ biquad lowpass with settable cutoff/Q/gain.
  Pass ``buggy=True`` to enable a deliberate defect (a cheap half-wave
  rectification nonlinearity on the output) that produces harmonic distortion
  and aliasing — perfect for demoing a caught regression.
- :class:`PureGainFixture` — multiplies the signal by a constant. Trivial,
  linear, useful as a known-good null case.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import lfilter


def _rbj_lowpass_coeffs(cutoff_hz: float, q: float, samplerate: int) -> tuple[np.ndarray, np.ndarray]:
    """RBJ Audio EQ Cookbook lowpass biquad coefficients (b, a), normalized so a0 == 1."""
    nyquist = samplerate / 2.0
    fc = min(max(float(cutoff_hz), 1.0), nyquist * 0.99)
    w0 = 2.0 * np.pi * fc / samplerate
    alpha = np.sin(w0) / (2.0 * max(float(q), 1e-3))
    cosw0 = np.cos(w0)

    half = (1.0 - cosw0) / 2.0
    b0 = half
    b1 = half
    b2 = half
    a0 = 1.0 + alpha
    a1 = -2.0 * cosw0
    a2 = 1.0 - alpha

    b = np.array([b0, b1, b2], dtype=np.float64) / a0
    a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
    return b, a


class BiquadFixture:
    """A biquad lowpass "plugin" (RBJ cookbook) satisfying the PluginHost Protocol.

    Parameters
    ----------
    cutoff_hz : lowpass cutoff frequency in Hz (default 1000.0).
    q : filter resonance (default 0.7071 ~= Butterworth).
    gain_db : output gain in dB applied after filtering (default 0.0).
    buggy : when True, applies a cheap half-wave-rectification nonlinearity to
        the output (``y = 0.6*y + 0.4*max(y, 0)``). This generates a DC offset,
        even harmonics out to Nyquist, and therefore audible distortion and
        aliasing — a deliberate regression for demos and tests.

    I/O convention: float32 mono ``(samples,)`` in, float32 mono ``(samples,)``
    out, same length.
    """

    def __init__(
        self,
        cutoff_hz: float = 1000.0,
        q: float = 0.7071,
        gain_db: float = 0.0,
        buggy: bool = False,
    ) -> None:
        self.cutoff_hz = float(cutoff_hz)
        self.q = float(q)
        self.gain_db = float(gain_db)
        self.buggy = bool(buggy)
        self.name = "fixture:biquad" + (":buggy" if self.buggy else "")

    # -- parameter API (mirrors PedalboardHost for a uniform demo surface) ----
    @property
    def parameters(self) -> dict[str, float | bool]:
        """Current parameter values, keyed by name."""
        return {
            "cutoff_hz": self.cutoff_hz,
            "q": self.q,
            "gain_db": self.gain_db,
            "buggy": self.buggy,
        }

    def set_param(self, name: str, value) -> None:
        """Set a parameter by name. Raises KeyError for unknown names."""
        if name not in self.parameters:
            raise KeyError(
                f"Unknown parameter {name!r}; available: {sorted(self.parameters)}"
            )
        if name == "buggy":
            self.buggy = bool(value)
            self.name = "fixture:biquad" + (":buggy" if self.buggy else "")
        else:
            setattr(self, name, float(value))

    # -- PluginHost Protocol ---------------------------------------------------
    def render(self, input_signal: np.ndarray, samplerate: int) -> np.ndarray:
        x = np.asarray(input_signal, dtype=np.float64).reshape(-1)
        b, a = _rbj_lowpass_coeffs(self.cutoff_hz, self.q, samplerate)
        y = lfilter(b, a, x)
        y *= 10.0 ** (self.gain_db / 20.0)
        if self.buggy:
            # Deliberate defect: half-wave rectification blended into the
            # output. Even harmonics extend past Nyquist -> aliasing.
            y = 0.6 * y + 0.4 * np.maximum(y, 0.0)
        return y.astype(np.float32)


class PureGainFixture:
    """Multiplies the input by a constant linear gain. Trivial known-good host."""

    def __init__(self, gain: float = 1.0) -> None:
        self.gain = float(gain)
        self.name = f"fixture:gain({self.gain:g})"

    @property
    def parameters(self) -> dict[str, float]:
        return {"gain": self.gain}

    def set_param(self, name: str, value) -> None:
        if name != "gain":
            raise KeyError(f"Unknown parameter {name!r}; available: ['gain']")
        self.gain = float(value)
        self.name = f"fixture:gain({self.gain:g})"

    def render(self, input_signal: np.ndarray, samplerate: int) -> np.ndarray:
        x = np.asarray(input_signal, dtype=np.float32).reshape(-1)
        return (x * np.float32(self.gain)).astype(np.float32)
