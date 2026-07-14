"""Golden-baseline store and diff engine for PluginProof.

- save_baseline / load_baseline: JSON round-trip of a RunResult (numpy spectra <-> lists).
- diff: per-metric comparison against configurable thresholds -> Verdict.
- run_suite: thin orchestrator that runs the 4 canonical measurements against a host.

Only depends on the shared contract + numpy; signals/measurements are imported lazily
inside run_suite so this module loads even while those modules are being built.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from pluginproof.contract import (
    DEFAULT_THRESHOLDS,
    METRIC_ALIASING,
    METRIC_FREQ_RESPONSE,
    METRIC_NAN_DENORMAL,
    METRIC_THD_N,
    DiffResult,
    Metric,
    RunResult,
    Spectrum,
    Status,
    Verdict,
)

BASELINE_FORMAT_VERSION = 1

# Fallback threshold (dB) for metrics that appear in a run but have no configured threshold.
_FALLBACK_THRESHOLD_DB = 6.0

_STATUS_RANK = {Status.PASS: 0, Status.WARN: 1, Status.FAIL: 2}


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

def _to_jsonable(obj):
    """Best-effort conversion of numpy containers/scalars into plain JSON types."""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.integer, np.bool_)):
        return obj.item()
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def save_baseline(run: RunResult, path) -> None:
    """Serialize a RunResult to JSON at `path` (numpy arrays stored as lists)."""
    payload = {
        "version": BASELINE_FORMAT_VERSION,
        "plugin": run.plugin,
        "samplerate": int(run.samplerate),
        "metrics": [
            {
                "name": m.name,
                "value": float(m.value),
                "unit": m.unit,
                "detail": _to_jsonable(m.detail),
            }
            for m in run.metrics
        ],
        "spectra": {
            name: {
                "freqs": np.asarray(spec.freqs, dtype=float).tolist(),
                "mags_db": np.asarray(spec.mags_db, dtype=float).tolist(),
            }
            for name, spec in run.spectra.items()
        },
        "artifacts": dict(run.artifacts),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_baseline(path) -> RunResult:
    """Load a RunResult previously written by save_baseline (lists -> np.ndarray)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    metrics = [
        Metric(
            name=m["name"],
            value=float(m["value"]),
            unit=m.get("unit", ""),
            detail=m.get("detail", {}) or {},
        )
        for m in data.get("metrics", [])
    ]
    spectra = {
        name: Spectrum(
            freqs=np.asarray(spec["freqs"], dtype=np.float64),
            mags_db=np.asarray(spec["mags_db"], dtype=np.float64),
        )
        for name, spec in data.get("spectra", {}).items()
    }
    return RunResult(
        plugin=data["plugin"],
        samplerate=int(data["samplerate"]),
        metrics=metrics,
        spectra=spectra,
        artifacts=data.get("artifacts", {}) or {},
    )


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------

def _worst(statuses) -> Status:
    worst = Status.PASS
    for s in statuses:
        if _STATUS_RANK[s] > _STATUS_RANK[worst]:
            worst = s
    return worst


def _missing_diff(name: str, unit: str, threshold: float,
                  baseline_value: float, current_value: float, note: str) -> DiffResult:
    d = DiffResult(
        metric=name,
        baseline=baseline_value,
        current=current_value,
        delta=math.nan,
        threshold=threshold,
        status=Status.WARN,
        unit=unit,
    )
    # DiffResult has no note field in the contract; attach as an extra attribute so
    # downstream consumers (CLI table, report) can surface it via getattr(d, "note", "").
    d.note = note
    return d


def diff(baseline: RunResult, current: RunResult,
         thresholds: dict[str, float] = DEFAULT_THRESHOLDS) -> Verdict:
    """Compare current run against baseline, metric by metric.

    Semantics:
    - nan_denormal: any current value > 0 is FAIL, regardless of baseline.
    - dB metrics: delta = current - baseline; FAIL if delta > threshold,
      WARN if delta > threshold / 2, else PASS.
    - Metric present on only one side: WARN, with a `note` attribute on the DiffResult.
    - Overall verdict = worst individual status.
    """
    base_by_name = {m.name: m for m in baseline.metrics}
    cur_by_name = {m.name: m for m in current.metrics}

    # Baseline order first, then any current-only metrics in their own order.
    names = [m.name for m in baseline.metrics]
    names += [m.name for m in current.metrics if m.name not in base_by_name]

    diffs: list[DiffResult] = []
    for name in names:
        b = base_by_name.get(name)
        c = cur_by_name.get(name)
        threshold = thresholds.get(name, _FALLBACK_THRESHOLD_DB)

        if b is None or c is None:
            present = b if b is not None else c
            note = ("metric missing in current run" if c is None
                    else "metric missing in baseline")
            diffs.append(_missing_diff(
                name,
                unit=present.unit,
                threshold=threshold,
                baseline_value=b.value if b is not None else math.nan,
                current_value=c.value if c is not None else math.nan,
                note=note,
            ))
            continue

        delta = c.value - b.value
        if name == METRIC_NAN_DENORMAL:
            status = Status.FAIL if c.value > 0 else Status.PASS
        elif delta > threshold:
            status = Status.FAIL
        elif delta > threshold / 2:
            status = Status.WARN
        else:
            status = Status.PASS

        diffs.append(DiffResult(
            metric=name,
            baseline=b.value,
            current=c.value,
            delta=delta,
            threshold=threshold,
            status=status,
            unit=c.unit or b.unit,
        ))

    return Verdict(overall=_worst(d.status for d in diffs), diffs=diffs)


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------

def run_suite(host, samplerate: int = 48000) -> RunResult:
    """Run the 4 canonical measurements against `host` and collect a RunResult.

    Lazily imports pluginproof.signals / pluginproof.measurements so this module
    stays importable while those are under construction.
    """
    try:
        from pluginproof import measurements, signals
    except ImportError as exc:  # pragma: no cover - depends on sibling workstreams
        raise RuntimeError(
            "pluginproof.signals / pluginproof.measurements are not ready yet "
            f"(import failed: {exc})"
        ) from exc

    metrics: list[Metric] = []
    spectra: dict[str, Spectrum] = {}

    # 1. Frequency response deviation (sine sweep)
    sweep, sr = signals.sine_sweep(samplerate, 1.0)
    out = host.render(sweep, sr)
    metric, spectrum = measurements.frequency_response(sweep, out, sr)
    metrics.append(metric)
    spectra["sine_sweep"] = spectrum

    # 2. THD+N (single 1 kHz sine — measurements.thd_n does a least-squares sine
    # fit at the stimulus fundamental, so it needs a single-tone input; a multitone
    # would leave the other tones in the residual and read as huge "distortion")
    tone, sr = signals.hf_tone(samplerate, 1.0, 1000.0)
    out = host.render(tone, sr)
    metrics.append(measurements.thd_n(tone, out, sr))

    # 3. Aliasing score (high-frequency tone near Nyquist; freq=None keeps the
    # measurement-tuned default of 0.41*Nyquist so folded images land off-harmonic)
    hf, sr = signals.hf_tone(samplerate, 1.0)
    out = host.render(hf, sr)
    metric, spectrum = measurements.aliasing_score(hf, out, sr)
    metrics.append(metric)
    spectra["hf_tone"] = spectrum

    # 4. NaN / denormal stability check (full-scale extremes)
    extremes, sr = signals.full_scale_extremes(samplerate, 1.0)
    out = host.render(extremes, sr)
    metrics.append(measurements.nan_denormal_check(extremes, out, sr))

    return RunResult(
        plugin=getattr(host, "name", str(host)),
        samplerate=samplerate,
        metrics=metrics,
        spectra=spectra,
    )
