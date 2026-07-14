"""Tests for pluginproof.report and pluginproof.diagnose (Agent R lane).

Works purely from contract dataclasses constructed as fixtures — no imports of
measurements/host/baseline.
"""
from __future__ import annotations

import base64
import re

import numpy as np
import pytest

from pluginproof.contract import DiffResult, Metric, RunResult, Spectrum, Status, Verdict
from pluginproof.diagnose import FALLBACK_MARKER, diagnose
from pluginproof.report import render_report

PLUGIN_NAME = "FixtureComp.vst3"
SR = 48000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _synth_spectrum(peak_hz: float, peak_db: float, noise_floor_db: float = -96.0) -> Spectrum:
    """Plausible magnitude spectrum: noise floor + a gaussian-ish peak."""
    freqs = np.logspace(np.log10(20.0), np.log10(SR / 2), 512)
    mags = np.full_like(freqs, noise_floor_db)
    mags += np.random.default_rng(42).normal(0.0, 1.5, freqs.shape)
    mags += (peak_db - noise_floor_db) * np.exp(
        -((np.log10(freqs) - np.log10(peak_hz)) ** 2) / 0.005
    )
    return Spectrum(freqs=freqs, mags_db=mags.astype(np.float64))


@pytest.fixture
def current_run() -> RunResult:
    return RunResult(
        plugin=PLUGIN_NAME,
        samplerate=SR,
        metrics=[
            Metric(name="thd_n", value=-52.0, unit="dB"),
            Metric(name="aliasing_score", value=-38.0, unit="dB"),
            Metric(name="freq_response_dev", value=0.9, unit="dB"),
            Metric(name="nan_denormal", value=0.0, unit="count"),
        ],
        spectra={
            "sine_sweep": _synth_spectrum(1000.0, -6.0),
            "hf_tone": _synth_spectrum(15000.0, -3.0),
        },
    )


@pytest.fixture
def baseline_run() -> RunResult:
    return RunResult(
        plugin=PLUGIN_NAME,
        samplerate=SR,
        metrics=[
            Metric(name="thd_n", value=-60.0, unit="dB"),
            Metric(name="aliasing_score", value=-70.0, unit="dB"),
            Metric(name="freq_response_dev", value=0.1, unit="dB"),
            Metric(name="nan_denormal", value=0.0, unit="count"),
        ],
        spectra={
            "sine_sweep": _synth_spectrum(1000.0, -6.5),
            "hf_tone": _synth_spectrum(15000.0, -30.0),
        },
    )


@pytest.fixture
def verdict() -> Verdict:
    return Verdict(
        overall=Status.FAIL,
        diffs=[
            DiffResult("thd_n", -60.0, -52.0, 8.0, 6.0, Status.WARN, "dB"),
            DiffResult("aliasing_score", -70.0, -38.0, 32.0, 6.0, Status.FAIL, "dB"),
            DiffResult("freq_response_dev", 0.1, 0.9, 0.8, 0.5, Status.WARN, "dB"),
            DiffResult("nan_denormal", 0.0, 0.0, 0.0, 0.0, Status.PASS, "count"),
        ],
        diagnosis="Your change introduced aliasing above 15 kHz - check your oversampling stage.",
    )


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------

class TestRenderReport:
    def test_report_written_and_self_contained(self, current_run, baseline_run, verdict, tmp_path):
        out = tmp_path / "report.html"
        result = render_report(current_run, verdict, out, baseline_run=baseline_run)

        assert result == out
        assert out.exists()
        html_text = out.read_text(encoding="utf-8")

        # Header: plugin name and overall badge text
        assert PLUGIN_NAME in html_text
        assert "FAIL" in html_text

        # Diagnosis panel text
        assert "check your oversampling stage" in html_text

        # Metric names appear on cards
        for name in ("thd_n", "aliasing_score", "freq_response_dev", "nan_denormal"):
            assert name in html_text

        # At least one embedded base64 PNG that actually decodes to a PNG
        imgs = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', html_text)
        assert len(imgs) >= 1
        assert base64.b64decode(imgs[0])[:8] == b"\x89PNG\r\n\x1a\n"

        # One plot per spectrum in the current run
        assert len(imgs) == len(current_run.spectra)

        # No leftover placeholders
        assert "{{" not in html_text

    def test_report_without_baseline_or_diagnosis(self, current_run, tmp_path):
        verdict = Verdict(
            overall=Status.PASS,
            diffs=[DiffResult("thd_n", -60.0, -59.5, 0.5, 6.0, Status.PASS, "dB")],
            diagnosis=None,
        )
        out = tmp_path / "pass_report.html"
        render_report(current_run, verdict, out)
        html_text = out.read_text(encoding="utf-8")
        assert "PASS" in html_text
        assert "No diagnosis available" in html_text
        assert "data:image/png;base64," in html_text

    def test_custom_shell_template(self, current_run, verdict, tmp_path):
        shell = tmp_path / "shell.html"
        shell.write_text(
            "<html><body><h1>{{plugin_name}}</h1><p>{{overall_status}}</p>"
            "{{metric_cards}}{{spectra}}<div>{{diagnosis}}</div>{{timestamp}}</body></html>",
            encoding="utf-8",
        )
        out = tmp_path / "shelled.html"
        render_report(current_run, verdict, out, shell=shell)
        html_text = out.read_text(encoding="utf-8")
        assert f"<h1>{PLUGIN_NAME}</h1>" in html_text
        assert "<p>FAIL</p>" in html_text
        assert "{{" not in html_text


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------

class _FakeResponses:
    def __init__(self, text: str):
        self._text = text
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs

        class _Resp:
            output_text = self._text

        return _Resp()


class _FakeClient:
    """Mimics the OpenAI client's Responses API surface."""

    def __init__(self, text: str):
        self.responses = _FakeResponses(text)


class _RaisingClient:
    class responses:  # noqa: N801 - mimic attribute shape
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("network down")


class TestDiagnose:
    def test_mock_client_passthrough_and_prompt_contents(self, verdict):
        client = _FakeClient("Aliasing regression detected; inspect the oversampler.")
        context = {"plugin": PLUGIN_NAME, "samplerate": SR}

        result = diagnose(verdict, context, client=client)

        assert result == "Aliasing regression detected; inspect the oversampler."

        kwargs = client.responses.last_kwargs
        assert kwargs is not None
        assert kwargs["model"] == "gpt-5.6"
        prompt = kwargs["input"]
        for name in ("thd_n", "aliasing_score", "freq_response_dev", "nan_denormal"):
            assert name in prompt
        assert PLUGIN_NAME in prompt
        assert str(SR) in prompt

    def test_offline_fallback_on_client_error(self, verdict):
        result = diagnose(verdict, {"plugin": PLUGIN_NAME, "samplerate": SR},
                          client=_RaisingClient())
        assert result.startswith(FALLBACK_MARKER)
        assert "aliasing" in result.lower()
        assert PLUGIN_NAME in result

    def test_offline_fallback_without_api_key(self, verdict, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = diagnose(verdict, {"plugin": PLUGIN_NAME, "samplerate": SR})
        assert result.startswith(FALLBACK_MARKER)

    def test_fallback_pass_verdict(self):
        verdict = Verdict(
            overall=Status.PASS,
            diffs=[DiffResult("thd_n", -60.0, -59.9, 0.1, 6.0, Status.PASS, "dB")],
        )
        result = diagnose(verdict, {"plugin": PLUGIN_NAME}, client=_RaisingClient())
        assert result.startswith(FALLBACK_MARKER)
        assert "no regression" in result.lower()

    def test_never_crashes_on_empty_response(self, verdict):
        result = diagnose(verdict, {"plugin": PLUGIN_NAME}, client=_FakeClient("   "))
        assert result.startswith(FALLBACK_MARKER)
