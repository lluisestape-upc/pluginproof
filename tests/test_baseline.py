"""Tests for pluginproof.baseline: JSON round-trip and the diff engine."""
from __future__ import annotations

import math

import numpy as np
import pytest

from pluginproof.baseline import diff, load_baseline, save_baseline
from pluginproof.contract import (
    DEFAULT_THRESHOLDS,
    Metric,
    RunResult,
    Spectrum,
    Status,
)


def make_run(values: dict[str, float] | None = None, *,
             plugin: str = "fixture:biquad",
             samplerate: int = 48000,
             with_spectra: bool = True) -> RunResult:
    """Build a canned RunResult with the 4 canonical metrics (values overridable)."""
    defaults = {
        "freq_response_dev": 0.1,
        "thd_n": -60.0,
        "aliasing_score": -80.0,
        "nan_denormal": 0.0,
    }
    if values is not None:
        defaults.update(values)
    units = {
        "freq_response_dev": "dB",
        "thd_n": "dB",
        "aliasing_score": "dB",
        "nan_denormal": "count",
    }
    metrics = [Metric(name=n, value=v, unit=units.get(n, "dB"))
               for n, v in defaults.items()]
    spectra = {}
    if with_spectra:
        freqs = np.linspace(20.0, 20000.0, 64)
        spectra = {
            "sine_sweep": Spectrum(freqs=freqs, mags_db=-6.0 - 0.001 * freqs),
            "hf_tone": Spectrum(freqs=freqs, mags_db=np.full_like(freqs, -90.0)),
        }
    return RunResult(plugin=plugin, samplerate=samplerate, metrics=metrics,
                     spectra=spectra, artifacts={"render": "out.wav"})


# ---------------------------------------------------------------------------
# save/load round trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_round_trip_preserves_everything(self, tmp_path):
        run = make_run()
        path = tmp_path / "golden.json"
        save_baseline(run, path)
        loaded = load_baseline(path)

        assert loaded.plugin == run.plugin
        assert loaded.samplerate == run.samplerate
        assert loaded.artifacts == run.artifacts

        assert [m.name for m in loaded.metrics] == [m.name for m in run.metrics]
        for orig, back in zip(run.metrics, loaded.metrics):
            assert back.value == pytest.approx(orig.value)
            assert back.unit == orig.unit

    def test_spectra_restored_as_numpy(self, tmp_path):
        run = make_run()
        path = tmp_path / "golden.json"
        save_baseline(run, path)
        loaded = load_baseline(path)

        assert set(loaded.spectra) == set(run.spectra)
        for name, spec in run.spectra.items():
            back = loaded.spectra[name]
            assert isinstance(back.freqs, np.ndarray)
            assert isinstance(back.mags_db, np.ndarray)
            np.testing.assert_allclose(back.freqs, spec.freqs)
            np.testing.assert_allclose(back.mags_db, spec.mags_db)

    def test_numpy_values_in_metric_detail_are_serialized(self, tmp_path):
        run = make_run()
        run.metrics[0].detail = {
            "per_band": np.array([0.1, 0.2]),
            "peak": np.float64(3.5),
            "bins": np.int32(7),
        }
        path = tmp_path / "golden.json"
        save_baseline(run, path)
        loaded = load_baseline(path)
        detail = loaded.metrics[0].detail
        assert detail["per_band"] == [pytest.approx(0.1), pytest.approx(0.2)]
        assert detail["peak"] == pytest.approx(3.5)
        assert detail["bins"] == 7

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "golden.json"
        save_baseline(make_run(with_spectra=False), path)
        assert path.exists()


# ---------------------------------------------------------------------------
# diff engine
# ---------------------------------------------------------------------------

class TestDiff:
    def test_identical_runs_all_pass(self):
        base = make_run()
        cur = make_run()
        verdict = diff(base, cur, DEFAULT_THRESHOLDS)
        assert verdict.overall is Status.PASS
        assert len(verdict.diffs) == 4
        assert all(d.status is Status.PASS for d in verdict.diffs)

    def test_improvement_negative_delta_passes(self):
        base = make_run({"thd_n": -60.0})
        cur = make_run({"thd_n": -70.0})  # THD dropped: better
        verdict = diff(base, cur)
        d = next(d for d in verdict.diffs if d.metric == "thd_n")
        assert d.delta == pytest.approx(-10.0)
        assert d.status is Status.PASS
        assert verdict.overall is Status.PASS

    def test_delta_above_half_threshold_warns(self):
        # freq_response_dev threshold 0.5 dB -> delta 0.3 is > 0.25 but <= 0.5
        base = make_run({"freq_response_dev": 0.1})
        cur = make_run({"freq_response_dev": 0.4})
        verdict = diff(base, cur)
        d = next(d for d in verdict.diffs if d.metric == "freq_response_dev")
        assert d.delta == pytest.approx(0.3)
        assert d.threshold == pytest.approx(0.5)
        assert d.status is Status.WARN
        assert verdict.overall is Status.WARN

    def test_delta_above_threshold_fails(self):
        base = make_run({"aliasing_score": -80.0})
        cur = make_run({"aliasing_score": -70.0})  # +10 dB rise > 6 dB threshold
        verdict = diff(base, cur)
        d = next(d for d in verdict.diffs if d.metric == "aliasing_score")
        assert d.delta == pytest.approx(10.0)
        assert d.status is Status.FAIL
        assert verdict.overall is Status.FAIL

    def test_delta_exactly_at_threshold_warns_not_fails(self):
        base = make_run({"thd_n": -60.0})
        cur = make_run({"thd_n": -54.0})  # delta == 6.0 == threshold
        verdict = diff(base, cur)
        d = next(d for d in verdict.diffs if d.metric == "thd_n")
        assert d.status is Status.WARN

    def test_nan_denormal_any_nonzero_current_fails(self):
        base = make_run({"nan_denormal": 0.0})
        cur = make_run({"nan_denormal": 1.0})
        verdict = diff(base, cur)
        d = next(d for d in verdict.diffs if d.metric == "nan_denormal")
        assert d.status is Status.FAIL
        assert verdict.overall is Status.FAIL

    def test_nan_denormal_fails_even_if_baseline_was_worse(self):
        # delta is negative (improvement) but any current nonzero must still FAIL
        base = make_run({"nan_denormal": 10.0})
        cur = make_run({"nan_denormal": 2.0})
        verdict = diff(base, cur)
        d = next(d for d in verdict.diffs if d.metric == "nan_denormal")
        assert d.status is Status.FAIL

    def test_nan_denormal_zero_passes(self):
        base = make_run({"nan_denormal": 3.0})
        cur = make_run({"nan_denormal": 0.0})
        verdict = diff(base, cur)
        d = next(d for d in verdict.diffs if d.metric == "nan_denormal")
        assert d.status is Status.PASS

    def test_metric_missing_in_current_warns_with_note(self):
        base = make_run()
        cur = make_run()
        cur.metrics = [m for m in cur.metrics if m.name != "thd_n"]
        verdict = diff(base, cur)
        d = next(d for d in verdict.diffs if d.metric == "thd_n")
        assert d.status is Status.WARN
        assert math.isnan(d.current)
        assert math.isnan(d.delta)
        assert "missing" in getattr(d, "note", "")
        assert verdict.overall is Status.WARN

    def test_metric_missing_in_baseline_warns_with_note(self):
        base = make_run()
        base.metrics = [m for m in base.metrics if m.name != "aliasing_score"]
        cur = make_run()
        verdict = diff(base, cur)
        d = next(d for d in verdict.diffs if d.metric == "aliasing_score")
        assert d.status is Status.WARN
        assert math.isnan(d.baseline)
        assert "missing" in getattr(d, "note", "")

    def test_overall_is_worst_status(self):
        base = make_run()
        cur = make_run({
            "freq_response_dev": 0.4,   # WARN (delta 0.3)
            "aliasing_score": -60.0,    # FAIL (delta 20)
        })
        verdict = diff(base, cur)
        assert verdict.overall is Status.FAIL

    def test_custom_thresholds_respected(self):
        base = make_run({"thd_n": -60.0})
        cur = make_run({"thd_n": -58.0})  # delta 2 dB
        strict = dict(DEFAULT_THRESHOLDS, thd_n=1.0)
        verdict = diff(base, cur, strict)
        d = next(d for d in verdict.diffs if d.metric == "thd_n")
        assert d.status is Status.FAIL

    def test_roundtripped_baseline_diffs_clean_against_itself(self, tmp_path):
        run = make_run()
        path = tmp_path / "golden.json"
        save_baseline(run, path)
        verdict = diff(load_baseline(path), run)
        assert verdict.overall is Status.PASS
