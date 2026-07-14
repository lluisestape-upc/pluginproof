"""Unit tests for pluginproof.signals and pluginproof.measurements.

Reference "plugins" are implemented inline as tiny numpy functions with
analytically known behaviour — no dependency on host.py, fixtures, or any
other agent's lane:

* pure gain (x2)      -> flat response, ~0 dB deviation, tiny THD, no aliasing
* hard clipper        -> high THD
* half-wave rectifier -> folded harmonics = high aliasing score
* zero-order-hold 2x  -> naive downsample/upsample = high aliasing score
* NaN/Inf/denormal injection -> exact counts
"""
from __future__ import annotations

import numpy as np
import pytest

from pluginproof import measurements as m
from pluginproof import signals as s
from pluginproof.contract import Metric, Spectrum

SR = 48_000


# --------------------------------------------------------------------------- inline reference "plugins"

def pure_gain(x: np.ndarray, g: float = 2.0) -> np.ndarray:
    """Ideal linear plugin: flat response, no distortion, no aliasing."""
    return (x * g).astype(np.float32)


def hard_clip(x: np.ndarray, threshold: float = 0.3) -> np.ndarray:
    """Heavy symmetric clipper. Driving a 0.5-amplitude sine into a 0.3
    ceiling squares it up: THD+N must come out high (a true square wave
    has THD+N ~= -6.3 dB)."""
    return np.clip(x, -threshold, threshold).astype(np.float32)


def half_wave_rectify(x: np.ndarray) -> np.ndarray:
    """Non-oversampled nonlinearity: for a tone at 0.41*Nyquist its even
    harmonics (4*f0, 6*f0, ...) land above Nyquist and fold back to
    non-harmonic frequencies -> measurable aliasing."""
    return np.maximum(x, 0.0).astype(np.float32)


def zoh_resample(x: np.ndarray) -> np.ndarray:
    """Naive downsample-by-2 then upsample via sample-and-hold (no
    anti-alias / anti-image filtering). Classic aliasing generator."""
    return np.repeat(x[::2], 2)[: len(x)].astype(np.float32)


def single_tone(freq: float, sr: int = SR, dur: float = 1.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(sr * dur)) / sr
    return (amp * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


# --------------------------------------------------------------------------- signal generators

@pytest.mark.parametrize("gen", [s.sine_sweep, s.multitone, s.hf_tone, s.silence, s.full_scale_extremes])
def test_generators_contract_shape(gen):
    """Every generator returns (float32 mono 1-D array, samplerate)."""
    sig, sr = gen(SR, 0.5)
    assert sr == SR
    assert isinstance(sig, np.ndarray)
    assert sig.dtype == np.float32
    assert sig.ndim == 1
    assert len(sig) == int(SR * 0.5)
    assert np.all(np.isfinite(sig))


def test_generator_amplitudes():
    sweep, _ = s.sine_sweep(SR, 1.0)
    assert 0.4 < np.max(np.abs(sweep)) <= 0.5001  # -6 dBFS analysis level

    quiet, _ = s.silence(SR, 0.25)
    assert np.all(quiet == 0.0)

    extremes, _ = s.full_scale_extremes(SR, 1.0)
    assert np.max(extremes) == pytest.approx(1.0)   # actually hits the rails
    assert np.min(extremes) == pytest.approx(-1.0)
    assert np.max(np.abs(extremes)) <= 1.0          # but never exceeds them


def test_hf_tone_frequency_and_bounds():
    """Default HF tone sits at 0.41*Nyquist; out-of-range freq raises."""
    tone, _ = s.hf_tone(SR, 1.0)
    detected = m._dominant_freq(tone.astype(np.float64), SR)
    assert detected == pytest.approx(0.41 * SR / 2.0, rel=1e-3)
    with pytest.raises(ValueError):
        s.hf_tone(SR, 1.0, freq=SR)  # above Nyquist


# --------------------------------------------------------------------------- frequency response

def test_frequency_response_pure_gain_is_flat():
    """A x2 gain must read as flat (deviation ~0 dB) with a +6.02 dB
    reference gain — the deviation metric must not punish level changes."""
    inp, sr = s.sine_sweep(SR, 2.0)
    out = pure_gain(inp, 2.0)
    metric, spectrum = m.frequency_response(inp, out, sr)

    assert isinstance(metric, Metric) and isinstance(spectrum, Spectrum)
    assert metric.name == "freq_response_dev"
    assert metric.unit == "dB"
    assert metric.value < 0.1  # essentially flat
    assert metric.detail["ref_gain_db"] == pytest.approx(20 * np.log10(2.0), abs=0.1)
    assert len(spectrum.freqs) == len(spectrum.mags_db) > 0


def test_frequency_response_detects_lowpass():
    """A one-pole lowpass at ~1 kHz is far from flat over a 30 Hz–19 kHz
    analysis band: deviation must exceed several dB."""
    inp, sr = s.sine_sweep(SR, 2.0)
    fc = 1000.0
    alpha = 1.0 - np.exp(-2.0 * np.pi * fc / sr)
    out = np.empty_like(inp)
    acc = 0.0
    for i, v in enumerate(inp):  # y[n] = y[n-1] + a*(x[n]-y[n-1])
        acc += alpha * (v - acc)
        out[i] = acc
    metric, _ = m.frequency_response(inp, out.astype(np.float32), sr)
    assert metric.value > 3.0


# --------------------------------------------------------------------------- THD+N

def test_thd_n_pure_gain_is_tiny():
    """Transparent gain: residual is only float32 rounding, way below -80 dB."""
    inp = single_tone(997.0)
    metric = m.thd_n(inp, pure_gain(inp), SR)
    assert metric.name == "thd_n"
    assert metric.unit == "dB"
    assert metric.value < -80.0
    assert metric.detail["fundamental_hz"] == pytest.approx(997.0, rel=1e-3)


def test_thd_n_hard_clipper_is_high():
    """A 0.5-amp sine clipped at 0.3 is nearly square: THD+N ~ -6 dB.
    Anything above -15 dB clearly flags gross distortion."""
    inp = single_tone(997.0)
    metric = m.thd_n(inp, hard_clip(inp, 0.3), SR)
    assert metric.value > -15.0


def test_thd_n_orders_clean_vs_dirty():
    """Sanity: THD+N must rank a clean gain far below a clipper."""
    inp = single_tone(997.0)
    clean = m.thd_n(inp, pure_gain(inp), SR).value
    dirty = m.thd_n(inp, hard_clip(inp), SR).value
    assert dirty - clean > 50.0  # separated by decades


# --------------------------------------------------------------------------- aliasing

def test_aliasing_pure_gain_is_negligible():
    inp, sr = s.hf_tone(SR, 1.0)
    metric, spectrum = m.aliasing_score(inp, pure_gain(inp), sr)
    assert isinstance(metric, Metric) and isinstance(spectrum, Spectrum)
    assert metric.name == "aliasing_score"
    assert metric.unit == "dB"
    assert metric.value < -60.0  # at/near the float32 noise floor


def test_aliasing_half_wave_rectifier_is_high():
    """|x| expansion of a 0.41*Nyquist tone puts its 4th harmonic at
    1.64*Nyquist, folding to 0.36*Nyquist (non-harmonic) at about -15 dB
    relative to the fundamental — the score must rise dramatically."""
    inp, sr = s.hf_tone(SR, 1.0)
    metric, _ = m.aliasing_score(inp, half_wave_rectify(inp), sr)
    assert metric.value > -40.0


def test_aliasing_zoh_resampler_is_high():
    """Sample-and-hold decimation images the tone across the spectrum."""
    inp, sr = s.hf_tone(SR, 1.0)
    metric, _ = m.aliasing_score(inp, zoh_resample(inp), sr)
    assert metric.value > -40.0


def test_aliasing_orders_clean_vs_dirty():
    inp, sr = s.hf_tone(SR, 1.0)
    clean = m.aliasing_score(inp, pure_gain(inp), sr)[0].value
    dirty = m.aliasing_score(inp, half_wave_rectify(inp), sr)[0].value
    assert dirty - clean > 40.0


def test_aliasing_excludes_inband_harmonics():
    """Distortion whose harmonics all stay below Nyquist is THD, not
    aliasing: soft-clipping a 1 kHz tone (harmonics 2k, 3k, ... all well
    in-band) must NOT trip the aliasing score."""
    inp = single_tone(997.0)
    out = np.tanh(3.0 * inp.astype(np.float64)).astype(np.float32)  # only odd in-band harmonics matter
    metric, _ = m.aliasing_score(inp, out, SR)
    assert metric.value < -45.0


# --------------------------------------------------------------------------- NaN / denormal

def test_nan_denormal_clean_output_is_zero():
    inp, sr = s.multitone(SR, 0.5)
    metric = m.nan_denormal_check(inp, pure_gain(inp), sr)
    assert metric.name == "nan_denormal"
    assert metric.unit == "count"
    assert metric.value == 0.0


def test_nan_denormal_counts_exactly():
    """3 NaN + 2 Inf + 4 denormals injected -> value must be exactly 9,
    with the per-kind breakdown in detail."""
    inp, sr = s.multitone(SR, 0.5)
    out = pure_gain(inp).copy()
    out[10:13] = np.nan
    out[100] = np.inf
    out[101] = -np.inf
    out[200:204] = np.float32(1e-40)  # subnormal for float32 (tiny ~ 1.18e-38)
    metric = m.nan_denormal_check(inp, out, sr)
    assert metric.value == 9.0
    assert metric.detail == {
        "nan": 3, "inf": 2, "denormal": 4, "total_samples": out.size,
    }


def test_nan_denormal_ignores_true_zeros():
    """Digital silence contains no denormals — exact zeros are legal."""
    inp, sr = s.silence(SR, 0.25)
    metric = m.nan_denormal_check(inp, inp.copy(), sr)
    assert metric.value == 0.0
