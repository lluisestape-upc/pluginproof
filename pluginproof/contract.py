"""Shared data contract for PluginProof. Keep this module tiny and dependency-light.

All workstreams import from here. See CONTRACT.md for the full spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

import numpy as np


@dataclass
class Metric:
    name: str
    value: float
    unit: str
    detail: dict = field(default_factory=dict)


@dataclass
class Spectrum:
    freqs: np.ndarray   # Hz
    mags_db: np.ndarray # dBFS


@dataclass
class RunResult:
    plugin: str
    samplerate: int
    metrics: list[Metric]
    spectra: dict[str, Spectrum] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)


class Status(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class DiffResult:
    metric: str
    baseline: float
    current: float
    delta: float
    threshold: float
    status: Status
    unit: str = ""


@dataclass
class Verdict:
    overall: Status
    diffs: list[DiffResult]
    diagnosis: Optional[str] = None


@runtime_checkable
class PluginHost(Protocol):
    """Anything that can turn an input signal into an output signal."""

    name: str

    def render(self, input_signal: np.ndarray, samplerate: int) -> np.ndarray:
        """Process float32 mono (samples,) input; return same-shape output."""
        ...


# Canonical metric names used across the suite
METRIC_FREQ_RESPONSE = "freq_response_dev"
METRIC_THD_N = "thd_n"
METRIC_ALIASING = "aliasing_score"
METRIC_NAN_DENORMAL = "nan_denormal"

DEFAULT_THRESHOLDS: dict[str, float] = {
    METRIC_FREQ_RESPONSE: 0.5,  # dB deviation vs baseline
    METRIC_THD_N: 6.0,          # dB rise vs baseline
    METRIC_ALIASING: 6.0,       # dB rise vs baseline
    METRIC_NAN_DENORMAL: 0.0,   # any nonzero count = FAIL
}
