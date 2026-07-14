"""Measurement engine for PluginProof.

Each function takes the *input* signal that was fed to the plugin, the
*output* the plugin produced, and the samplerate, and distils them into
the canonical :class:`~pluginproof.contract.Metric` shapes defined in
CONTRACT.md:

* ``frequency_response`` -> ("freq_response_dev", dB)   + Spectrum
* ``thd_n``              -> ("thd_n", dB)
* ``aliasing_score``     -> ("aliasing_score", dB)      + Spectrum
* ``nan_denormal_check`` -> ("nan_denormal", count)

Conventions
-----------
* Signals are float32 mono ``(samples,)`` per the contract. A
  ``(channels, samples)`` array is tolerated and averaged to mono,
  except in :func:`nan_denormal_check`, which inspects raw samples.
* Spectral measurements sanitise NaN/Inf to zero first so a single bad
  sample cannot poison an entire metric — counting bad samples is
  exactly :func:`nan_denormal_check`'s job.
* All dB metrics are "lower is better" ratios, so a regression shows up
  as the value *rising* — matching the rise-based thresholds in
  ``contract.DEFAULT_THRESHOLDS``.
"""
from __future__ import annotations

import numpy as np
from scipy import signal as sps
from scipy.optimize import minimize_scalar

from pluginproof.contract import (
    METRIC_ALIASING,
    METRIC_FREQ_RESPONSE,
    METRIC_NAN_DENORMAL,
    METRIC_THD_N,
    Metric,
    Spectrum,
)

__all__ = [
    "frequency_response",
    "thd_n",
    "aliasing_score",
    "nan_denormal_check",
]

_EPS = 1e-30  # floor to keep log10 finite on digital silence


# --------------------------------------------------------------------------- helpers

def _mono_f64(x: np.ndarray) -> np.ndarray:
    """Collapse to mono, cast to float64, and zero out NaN/Inf samples."""
    x = np.asarray(x)
    if x.ndim == 2:  # (channels, samples) -> mono
        x = x.mean(axis=0)
    x = x.astype(np.float64, copy=False)
    return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)


def _central_segment(x: np.ndarray, frac: float = 0.8) -> np.ndarray:
    """Central ``frac`` of a signal — skips edge fades and plugin warm-up."""
    n = len(x)
    trim = int(n * (1.0 - frac) / 2.0)
    return x[trim:n - trim] if trim > 0 else x


def _dominant_freq(x: np.ndarray, sr: int) -> float:
    """Frequency of the strongest spectral peak (Hann-windowed FFT),
    refined with parabolic interpolation for sub-bin accuracy."""
    seg = _central_segment(x)
    win = np.hanning(len(seg))
    mag = np.abs(np.fft.rfft(seg * win))
    mag[0] = 0.0  # ignore DC
    k = int(np.argmax(mag))
    # Parabolic peak interpolation on log-magnitude.
    if 0 < k < len(mag) - 1:
        a, b, c = np.log(mag[k - 1] + _EPS), np.log(mag[k] + _EPS), np.log(mag[k + 1] + _EPS)
        denom = a - 2.0 * b + c
        if abs(denom) > _EPS:
            k = k + 0.5 * (a - c) / denom
    return float(k) * sr / len(seg)


def _refine_freq(x: np.ndarray, sr: int, f0: float) -> float:
    """Refine a coarse frequency estimate by minimising the least-squares
    sine-fit residual on ``x``.

    Parabolic FFT interpolation is only good to ~1/50th of a bin; over a
    long analysis window even that tiny error de-phases the fit enough to
    dominate the residual of a *clean* signal (~-30 dB instead of the
    float32 floor). A bounded 1-D search over +/-2 bins fixes that.
    """
    bin_w = sr / len(x)

    def resid_power(f: float) -> float:
        _, residual = _fit_tone(x, sr, f)
        return float(np.mean(np.square(residual)))

    res = minimize_scalar(
        resid_power,
        bounds=(max(f0 - 2.0 * bin_w, bin_w), f0 + 2.0 * bin_w),
        method="bounded",
        options={"xatol": bin_w * 1e-7},
    )
    return float(res.x)


def _fit_tone(x: np.ndarray, sr: int, f0: float) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares fit of ``a*sin + b*cos + dc`` at ``f0``.

    Returns ``(fitted_tone, residual)``. The time-domain fit avoids all
    FFT leakage issues, so the residual is *everything* that is not the
    fundamental: harmonics + noise. Exactly what THD+N wants.
    """
    t = np.arange(len(x)) / sr
    basis = np.column_stack([
        np.sin(2.0 * np.pi * f0 * t),
        np.cos(2.0 * np.pi * f0 * t),
        np.ones_like(t),
    ])
    coef, *_ = np.linalg.lstsq(basis, x, rcond=None)
    fundamental = basis[:, :2] @ coef[:2]  # DC excluded from the "signal" part
    residual = x - basis @ coef
    return fundamental, residual


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)) + _EPS))


# --------------------------------------------------------------------------- 1. frequency response

def frequency_response(inp: np.ndarray, out: np.ndarray, sr: int) -> tuple[Metric, Spectrum]:
    """Magnitude response from a broadband (sweep) stimulus.

    Uses the Welch H1 estimator ``H(f) = Pxy(f) / Pxx(f)`` (cross-spectrum
    over input auto-spectrum), which averages across segments and is
    robust to a little noise. Analysis is restricted to bins where the
    stimulus actually has energy.

    Returns
    -------
    Metric
        ``freq_response_dev`` — max deviation (dB) of the magnitude
        response from flat, where "flat" is the median gain across the
        analysis band. A pure gain therefore scores ~0 dB regardless of
        the gain amount; the gain itself is reported in ``detail``.
    Spectrum
        Gain in dB per frequency bin over the analysis band.
    """
    x = _mono_f64(inp)
    y = _mono_f64(out)
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]

    nperseg = min(4096, 1 << max(int(np.log2(max(n // 4, 16))), 4))
    freqs, pxx = sps.welch(x, fs=sr, nperseg=nperseg)
    _, pxy = sps.csd(x, y, fs=sr, nperseg=nperseg)

    # Keep bins inside the sweep band and with real stimulus energy
    # (>-100 dB relative to the strongest bin) — elsewhere H is noise/0-div.
    band = (freqs >= 30.0) & (freqs <= 0.4 * sr)
    energetic = pxx > np.max(pxx) * 1e-10
    keep = band & energetic
    if not np.any(keep):  # degenerate stimulus (e.g. silence)
        metric = Metric(METRIC_FREQ_RESPONSE, float("inf"), "dB",
                        detail={"error": "input has no analysable energy"})
        return metric, Spectrum(freqs=np.array([]), mags_db=np.array([]))

    h_mag = np.abs(pxy[keep]) / pxx[keep]
    mags_db = 20.0 * np.log10(h_mag + _EPS)

    ref_db = float(np.median(mags_db))       # "flat" reference gain
    dev_db = float(np.max(np.abs(mags_db - ref_db)))

    metric = Metric(
        name=METRIC_FREQ_RESPONSE,
        value=dev_db,
        unit="dB",
        detail={
            "ref_gain_db": ref_db,
            "band_hz": [float(freqs[keep][0]), float(freqs[keep][-1])],
            "n_bins": int(np.sum(keep)),
        },
    )
    return metric, Spectrum(freqs=freqs[keep], mags_db=mags_db)


# --------------------------------------------------------------------------- 2. THD+N

def thd_n(inp: np.ndarray, out: np.ndarray, sr: int) -> Metric:
    """THD+N in dB from a single-tone stimulus.

    The fundamental frequency is detected from the *input* (so the plugin
    cannot fool the detector with a huge harmonic), then a least-squares
    sine at that frequency is subtracted from the output. Everything left
    over — harmonics, intermodulation, noise — is the "D+N" part:

        THD+N = 20*log10( rms(residual) / rms(fundamental) )

    Lower (more negative) is cleaner. A transparent plugin lands near the
    float32 noise floor (~-130 dB); a hard clipper lands around -10 dB.
    """
    x = _mono_f64(inp)
    y = _mono_f64(out)
    # Coarse estimate from the FFT peak, then refine on the clean *input*
    # tone: sub-millihertz accuracy so detector error never masquerades
    # as distortion in the fit residual.
    f0 = _refine_freq(_central_segment(x), sr, _dominant_freq(x, sr))

    seg = _central_segment(y)  # skip fades / warm-up transients
    fundamental, residual = _fit_tone(seg, sr, f0)

    fund_rms = _rms(fundamental)
    resid_rms = _rms(residual)
    value = 20.0 * np.log10(resid_rms / max(fund_rms, _EPS))

    return Metric(
        name=METRIC_THD_N,
        value=float(value),
        unit="dB",
        detail={
            "fundamental_hz": f0,
            "fundamental_rms": fund_rms,
            "residual_rms": resid_rms,
        },
    )


# --------------------------------------------------------------------------- 3. aliasing

def aliasing_score(inp: np.ndarray, out: np.ndarray, sr: int) -> tuple[Metric, Spectrum]:
    """Aliasing energy from a high-frequency tone stimulus, in dB.

    A nonlinearity driven by a tone at ``f0`` generates harmonics at
    ``k*f0``. Harmonics above Nyquist *fold back* to non-harmonic image
    frequencies — that folded energy is aliasing. We therefore measure
    the output power at every frequency that is **not** ``f0`` or a
    below-Nyquist harmonic of it, relative to the fundamental power:

        score = 10*log10( P(non-harmonic bins) / P(fundamental) )

    A clean linear plugin scores near the noise floor (~-120 dB); naive
    (non-oversampled) waveshaping or a broken resampler scores far higher.
    Harmonic distortion *below* Nyquist is deliberately excluded — that is
    THD's job, not aliasing's.
    """
    x = _mono_f64(inp)
    y = _mono_f64(out)
    f0 = _dominant_freq(x, sr)
    nyq = sr / 2.0

    seg = _central_segment(y)
    win = np.hanning(len(seg))
    spec = np.abs(np.fft.rfft(seg * win))
    freqs = np.fft.rfftfreq(len(seg), d=1.0 / sr)
    power = np.square(spec)

    # Tolerance band around each expected line: wide enough to swallow
    # Hann leakage skirts, scaled with f0 for detector inaccuracy.
    bin_w = sr / len(seg)
    tol = max(8.0 * bin_w, 0.01 * f0)

    harmonics = [k * f0 for k in range(1, int(nyq / f0) + 1) if k * f0 < nyq * 0.999]
    expected = np.zeros_like(freqs, dtype=bool)
    for h in harmonics:
        expected |= np.abs(freqs - h) <= tol
    expected |= freqs < 20.0  # ignore DC / subsonic (DC offset is not aliasing)

    fund_mask = np.abs(freqs - f0) <= tol
    fund_power = float(np.sum(power[fund_mask]))
    alias_power = float(np.sum(power[~expected]))

    value = 10.0 * np.log10((alias_power + _EPS) / max(fund_power, _EPS))

    mags_db = 20.0 * np.log10(spec / (np.max(spec) + _EPS) + _EPS)  # normalised dBFS-ish
    metric = Metric(
        name=METRIC_ALIASING,
        value=float(value),
        unit="dB",
        detail={
            "fundamental_hz": f0,
            "harmonics_hz": [float(h) for h in harmonics],
            "alias_power": alias_power,
            "fundamental_power": fund_power,
        },
    )
    return metric, Spectrum(freqs=freqs, mags_db=mags_db)


# --------------------------------------------------------------------------- 4. NaN / denormal

def nan_denormal_check(inp: np.ndarray, out: np.ndarray, sr: int) -> Metric:
    """Count NaN, Inf, and denormal (subnormal) samples in the output.

    Denormals matter because many plugins go quiet-but-CPU-hungry when a
    feedback path decays into the subnormal range; NaN/Inf mean the DSP
    blew up outright. Any nonzero count is a FAIL per the default
    thresholds. The raw output array is inspected sample-by-sample —
    no mono folding, so nothing can cancel out.
    """
    y = np.asarray(out)
    tiny = np.finfo(y.dtype if np.issubdtype(y.dtype, np.floating) else np.float32).tiny

    n_nan = int(np.count_nonzero(np.isnan(y)))
    n_inf = int(np.count_nonzero(np.isinf(y)))
    finite = y[np.isfinite(y)]
    n_denormal = int(np.count_nonzero((finite != 0.0) & (np.abs(finite) < tiny)))

    return Metric(
        name=METRIC_NAN_DENORMAL,
        value=float(n_nan + n_inf + n_denormal),
        unit="count",
        detail={"nan": n_nan, "inf": n_inf, "denormal": n_denormal,
                "total_samples": int(y.size)},
    )
