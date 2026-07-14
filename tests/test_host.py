"""Smoke tests for pluginproof.host and the fixtures — no external plugins needed."""
from __future__ import annotations

import numpy as np
import pytest

from pluginproof.contract import PluginHost
from pluginproof.fixtures.biquad import BiquadFixture, PureGainFixture
from pluginproof.host import PedalboardHost, WavFileHost, resolve_host, write_input_wav

SR = 48000


def sine(freq: float, sr: int = SR, dur: float = 0.5, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(sr * dur)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


# ---------------------------------------------------------------------------
# BiquadFixture
# ---------------------------------------------------------------------------

class TestBiquadFixture:
    def test_satisfies_protocol(self):
        assert isinstance(BiquadFixture(), PluginHost)
        assert isinstance(PureGainFixture(), PluginHost)

    def test_render_shape_dtype_finite(self):
        host = BiquadFixture(cutoff_hz=1000.0)
        x = sine(440.0)
        y = host.render(x, SR)
        assert y.shape == x.shape
        assert y.dtype == np.float32
        assert np.all(np.isfinite(y))

    def test_lowpass_attenuates_high_more_than_low(self):
        host = BiquadFixture(cutoff_hz=1000.0)
        low_in, high_in = sine(100.0), sine(8000.0)
        low_ratio = rms(host.render(low_in, SR)) / rms(low_in)
        high_ratio = rms(host.render(high_in, SR)) / rms(high_in)
        # 100 Hz passes nearly untouched; 8 kHz (3 octaves above cutoff,
        # 12 dB/oct) should be crushed by roughly 36 dB.
        assert low_ratio > 0.9
        assert high_ratio < 0.1
        assert high_ratio < low_ratio

    def test_gain_param(self):
        quiet = BiquadFixture(cutoff_hz=20000.0, gain_db=-20.0)
        loud = BiquadFixture(cutoff_hz=20000.0, gain_db=0.0)
        x = sine(440.0)
        ratio = rms(quiet.render(x, SR)) / rms(loud.render(x, SR))
        assert ratio == pytest.approx(10 ** (-20 / 20), rel=0.01)

    def test_set_param_and_parameters(self):
        host = BiquadFixture()
        assert set(host.parameters) == {"cutoff_hz", "q", "gain_db", "buggy"}
        host.set_param("cutoff_hz", 500.0)
        assert host.parameters["cutoff_hz"] == 500.0
        with pytest.raises(KeyError):
            host.set_param("nope", 1.0)

    def test_buggy_mode_adds_distortion(self):
        x = sine(440.0)
        clean = BiquadFixture(cutoff_hz=20000.0).render(x, SR)
        buggy = BiquadFixture(cutoff_hz=20000.0, buggy=True).render(x, SR)
        assert not np.allclose(clean, buggy)
        # Half-wave rectification makes the output asymmetric (positive DC).
        assert float(np.mean(buggy)) > float(np.mean(clean)) + 1e-3
        assert np.all(np.isfinite(buggy))
        assert "buggy" in BiquadFixture(buggy=True).name


class TestPureGainFixture:
    def test_gain_applied(self):
        x = sine(440.0)
        y = PureGainFixture(gain=0.5).render(x, SR)
        assert y.dtype == np.float32
        np.testing.assert_allclose(y, 0.5 * x, atol=1e-7)

    def test_unity_is_identity(self):
        x = sine(1000.0)
        np.testing.assert_array_equal(PureGainFixture(gain=1.0).render(x, SR), x)


# ---------------------------------------------------------------------------
# resolve_host
# ---------------------------------------------------------------------------

class TestResolveHost:
    def test_fixture_biquad(self):
        host = resolve_host("fixture:biquad", cutoff_hz=500.0, buggy=True)
        assert isinstance(host, BiquadFixture)
        assert host.cutoff_hz == 500.0
        assert host.buggy is True

    def test_fixture_gain(self):
        host = resolve_host("fixture:gain", gain=2.0)
        assert isinstance(host, PureGainFixture)
        assert host.gain == 2.0

    def test_unknown_fixture_raises(self):
        with pytest.raises(ValueError, match="Unknown fixture"):
            resolve_host("fixture:reverb9000")

    def test_unknown_spec_raises_clear_message(self):
        with pytest.raises(ValueError, match="Cannot resolve plugin spec"):
            resolve_host("definitely_not_a_plugin")

    def test_empty_spec_raises(self):
        with pytest.raises(ValueError):
            resolve_host("")

    def test_wav_spec(self, tmp_path):
        host = resolve_host("wav", in_wav=tmp_path / "in.wav", out_wav=tmp_path / "out.wav")
        assert isinstance(host, WavFileHost)

    def test_wav_spec_missing_kwargs_raises(self):
        with pytest.raises(ValueError, match="in_wav"):
            resolve_host("wav")

    def test_vst3_maps_to_pedalboard_host(self, tmp_path):
        # Without pedalboard installed this raises ImportError; with it
        # installed, the nonexistent file raises FileNotFoundError. Either
        # way the .vst3 suffix must route to PedalboardHost.
        with pytest.raises((ImportError, FileNotFoundError)):
            resolve_host(str(tmp_path / "missing_plugin.vst3"))


# ---------------------------------------------------------------------------
# WavFileHost
# ---------------------------------------------------------------------------

class TestWavFileHost:
    def test_round_trip_same_samplerate(self, tmp_path):
        from scipy.io import wavfile

        x = sine(440.0, dur=0.25)
        out_wav = tmp_path / "out.wav"
        wavfile.write(str(out_wav), SR, x)

        host = WavFileHost(tmp_path / "in.wav", out_wav)
        y = host.render(x, SR)  # input is ignored except for its length
        assert y.shape == x.shape
        assert y.dtype == np.float32
        np.testing.assert_allclose(y, x, atol=1e-6)

    def test_int16_wav_is_normalized(self, tmp_path):
        from scipy.io import wavfile

        x = sine(440.0, dur=0.1)
        out_wav = tmp_path / "out16.wav"
        wavfile.write(str(out_wav), SR, (x * 32767).astype(np.int16))

        y = WavFileHost(tmp_path / "in.wav", out_wav).render(x, SR)
        assert float(np.max(np.abs(y))) <= 1.0
        np.testing.assert_allclose(y, x, atol=1e-3)

    def test_resamples_to_requested_rate(self, tmp_path):
        from scipy.io import wavfile

        file_sr = 22050
        x_file = sine(440.0, sr=file_sr, dur=0.5)
        out_wav = tmp_path / "out22k.wav"
        wavfile.write(str(out_wav), file_sr, x_file)

        x_ref = sine(440.0, sr=SR, dur=0.5)
        y = WavFileHost(tmp_path / "in.wav", out_wav).render(x_ref, SR)
        assert y.shape == x_ref.shape
        # Compare away from resampler edge transients.
        n = len(y)
        core = slice(n // 8, -n // 8)
        assert rms(y[core]) == pytest.approx(rms(x_ref[core]), rel=0.05)

    def test_length_trim_and_pad(self, tmp_path):
        from scipy.io import wavfile

        x = sine(440.0, dur=0.2)
        out_wav = tmp_path / "out.wav"
        wavfile.write(str(out_wav), SR, x)
        host = WavFileHost(tmp_path / "in.wav", out_wav)

        shorter = np.zeros(len(x) // 2, dtype=np.float32)
        longer = np.zeros(len(x) * 2, dtype=np.float32)
        assert host.render(shorter, SR).shape == shorter.shape
        assert host.render(longer, SR).shape == longer.shape

    def test_missing_out_wav_raises(self, tmp_path):
        host = WavFileHost(tmp_path / "in.wav", tmp_path / "never_rendered.wav")
        with pytest.raises(FileNotFoundError, match="externally"):
            host.render(np.zeros(100, dtype=np.float32), SR)

    def test_write_input_wav_helper(self, tmp_path):
        from scipy.io import wavfile

        x = sine(440.0, dur=0.1)
        path = write_input_wav(x, SR, tmp_path / "sub" / "in.wav")
        assert path.exists()
        sr_read, data = wavfile.read(str(path))
        assert sr_read == SR
        np.testing.assert_allclose(data, x, atol=1e-7)

        host = WavFileHost(tmp_path / "in2.wav", tmp_path / "out.wav")
        host.write_input_wav(x, SR)
        assert (tmp_path / "in2.wav").exists()


# ---------------------------------------------------------------------------
# PedalboardHost (only runs if pedalboard is installed)
# ---------------------------------------------------------------------------

class TestPedalboardHost:
    def test_missing_file_raises(self, tmp_path):
        pytest.importorskip("pedalboard")
        with pytest.raises(FileNotFoundError):
            PedalboardHost(tmp_path / "nope.vst3")

    def test_import_error_message_when_absent(self, monkeypatch, tmp_path):
        """If pedalboard is unimportable, PedalboardHost must fail with a clear message."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pedalboard":
                raise ImportError("no pedalboard here")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="pip install pedalboard"):
            PedalboardHost(tmp_path / "whatever.vst3")
