"""Tests for pluginproof.cli using CliRunner with mocked resolve_host / run_suite."""
from __future__ import annotations

import json

import numpy as np
import pytest
from typer.testing import CliRunner

import pluginproof.cli as cli
from pluginproof.baseline import save_baseline
from pluginproof.contract import Metric, RunResult, Spectrum

runner = CliRunner()


class FakeHost:
    name = "fake-host"

    def render(self, input_signal, samplerate):  # pragma: no cover - never called
        return input_signal


def make_run(values: dict[str, float] | None = None) -> RunResult:
    defaults = {
        "freq_response_dev": 0.1,
        "thd_n": -60.0,
        "aliasing_score": -80.0,
        "nan_denormal": 0.0,
    }
    if values is not None:
        defaults.update(values)
    units = {"nan_denormal": "count"}
    metrics = [Metric(name=n, value=v, unit=units.get(n, "dB"))
               for n, v in defaults.items()]
    freqs = np.linspace(20.0, 20000.0, 32)
    spectra = {"sine_sweep": Spectrum(freqs=freqs, mags_db=np.zeros_like(freqs))}
    return RunResult(plugin="fake-host", samplerate=48000,
                     metrics=metrics, spectra=spectra)


@pytest.fixture
def mocked_pipeline(monkeypatch):
    """Patch resolve_host + run_suite; returns a dict to control the canned run."""
    state = {"run": make_run(), "hosts": [], "suite_calls": []}

    def fake_resolve_host(plugin, wav_in=None, wav_out=None):
        state["hosts"].append(plugin)
        return FakeHost()

    def fake_run_suite(host, samplerate=48000):
        state["suite_calls"].append((host, samplerate))
        return state["run"]

    monkeypatch.setattr(cli, "resolve_host", fake_resolve_host)
    monkeypatch.setattr(cli, "run_suite", fake_run_suite)
    return state


# ---------------------------------------------------------------------------
# baseline command
# ---------------------------------------------------------------------------

class TestBaselineCommand:
    def test_writes_golden_and_prints_summary(self, mocked_pipeline, tmp_path):
        out = tmp_path / "golden.json"
        result = runner.invoke(cli.app, ["baseline", "fixture:biquad", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists()

        data = json.loads(out.read_text(encoding="utf-8"))
        names = [m["name"] for m in data["metrics"]]
        assert names == ["freq_response_dev", "thd_n", "aliasing_score", "nan_denormal"]

        assert "freq_response_dev" in result.output
        assert "Baseline saved" in result.output

    def test_passes_samplerate_through(self, mocked_pipeline, tmp_path):
        out = tmp_path / "golden.json"
        result = runner.invoke(
            cli.app,
            ["baseline", "fixture:biquad", "--out", str(out), "--samplerate", "96000"],
        )
        assert result.exit_code == 0, result.output
        assert mocked_pipeline["suite_calls"][0][1] == 96000

    def test_module_not_ready_message(self, monkeypatch, tmp_path):
        def fake_resolve_host(plugin, wav_in=None, wav_out=None):
            return FakeHost()

        def broken_run_suite(host, samplerate=48000):
            raise RuntimeError("pluginproof.signals / pluginproof.measurements are not ready yet")

        monkeypatch.setattr(cli, "resolve_host", fake_resolve_host)
        monkeypatch.setattr(cli, "run_suite", broken_run_suite)
        result = runner.invoke(
            cli.app, ["baseline", "fixture:biquad", "--out", str(tmp_path / "g.json")]
        )
        assert result.exit_code == 2
        assert "not ready" in result.output


# ---------------------------------------------------------------------------
# check command
# ---------------------------------------------------------------------------

def write_golden(tmp_path, values=None):
    path = tmp_path / "golden.json"
    save_baseline(make_run(values), path)
    return path


class TestCheckCommand:
    def test_pass_exits_zero(self, mocked_pipeline, tmp_path):
        golden = write_golden(tmp_path)
        mocked_pipeline["run"] = make_run()  # identical to baseline
        result = runner.invoke(
            cli.app,
            ["check", "fixture:biquad", "--baseline", str(golden), "--no-diagnose"],
        )
        assert result.exit_code == 0, result.output
        assert "PASS" in result.output
        assert "Overall" in result.output

    def test_warn_exits_one(self, mocked_pipeline, tmp_path):
        golden = write_golden(tmp_path)
        # freq_response_dev 0.1 -> 0.4: delta 0.3 > 0.25 (thr/2) but <= 0.5 -> WARN
        mocked_pipeline["run"] = make_run({"freq_response_dev": 0.4})
        result = runner.invoke(
            cli.app,
            ["check", "fixture:biquad", "--baseline", str(golden), "--no-diagnose"],
        )
        assert result.exit_code == 1, result.output
        assert "WARN" in result.output

    def test_fail_exits_two(self, mocked_pipeline, tmp_path):
        golden = write_golden(tmp_path)
        # aliasing_score -80 -> -60: +20 dB rise -> FAIL
        mocked_pipeline["run"] = make_run({"aliasing_score": -60.0})
        result = runner.invoke(
            cli.app,
            ["check", "fixture:biquad", "--baseline", str(golden), "--no-diagnose"],
        )
        assert result.exit_code == 2, result.output
        assert "FAIL" in result.output

    def test_nan_denormal_fails(self, mocked_pipeline, tmp_path):
        golden = write_golden(tmp_path)
        mocked_pipeline["run"] = make_run({"nan_denormal": 5.0})
        result = runner.invoke(
            cli.app,
            ["check", "fixture:biquad", "--baseline", str(golden), "--no-diagnose"],
        )
        assert result.exit_code == 2, result.output

    def test_table_lists_all_metrics(self, mocked_pipeline, tmp_path):
        golden = write_golden(tmp_path)
        result = runner.invoke(
            cli.app,
            ["check", "fixture:biquad", "--baseline", str(golden), "--no-diagnose"],
        )
        for name in ["freq_response_dev", "thd_n", "aliasing_score", "nan_denormal"]:
            assert name in result.output

    def test_missing_baseline_file_errors(self, mocked_pipeline, tmp_path):
        result = runner.invoke(
            cli.app,
            ["check", "fixture:biquad",
             "--baseline", str(tmp_path / "nope.json"), "--no-diagnose"],
        )
        assert result.exit_code == 2
        assert "not found" in result.output

    def test_diagnose_failure_does_not_break_check(self, mocked_pipeline, tmp_path, monkeypatch):
        """With --diagnose on and the diagnose module unavailable, check still completes."""
        golden = write_golden(tmp_path)
        mocked_pipeline["run"] = make_run({"aliasing_score": -60.0})  # FAIL path
        result = runner.invoke(
            cli.app,
            ["check", "fixture:biquad", "--baseline", str(golden)],
        )
        # exit code still reflects the verdict, not a diagnose crash
        assert result.exit_code == 2, result.output
        assert "FAIL" in result.output


# ---------------------------------------------------------------------------
# host resolution (unmocked resolve_host, unknown spec)
# ---------------------------------------------------------------------------

class TestResolveHost:
    def test_unknown_plugin_spec_errors(self, monkeypatch, tmp_path):
        golden = write_golden(tmp_path)
        result = runner.invoke(
            cli.app,
            ["check", "not-a-plugin", "--baseline", str(golden), "--no-diagnose"],
        )
        assert result.exit_code == 2
        assert "cannot resolve plugin" in result.output

    def test_wav_pair_requires_both(self, tmp_path):
        # real (unmocked) resolve_host; fails before run_suite is ever reached
        result = runner.invoke(
            cli.app,
            ["baseline", "someplugin", "--out", str(tmp_path / "g.json"),
             "--wav-in", str(tmp_path / "in.wav")],
        )
        assert result.exit_code == 2
        assert "--wav-in and --wav-out" in result.output
