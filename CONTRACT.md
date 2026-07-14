# PluginProof — Data Contract (v1)

All workstreams build against these shapes. Do not change them unilaterally — if you need a
change, note it in your final report instead.

## Language / stack
- Python 3.11+, dependency-light: `numpy`, `scipy`, `pedalboard`, `typer`, `matplotlib` (report only), `openai` (diagnose only).
- Package layout: `pluginproof/` package, `tests/` with pytest.

## Core dataclasses (live in `pluginproof/contract.py` — shared, tiny, no heavy imports)

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

@dataclass
class Metric:
    name: str            # e.g. "thd_n", "aliasing_score", "freq_response_dev", "nan_denormal"
    value: float         # scalar summary of the measurement
    unit: str            # e.g. "dB", "%", "count"
    detail: dict = field(default_factory=dict)  # free-form extras (per-band data, etc.)

@dataclass
class RunResult:
    plugin: str                      # plugin path or fixture name
    samplerate: int
    metrics: list[Metric]
    spectra: dict[str, "Spectrum"] = field(default_factory=dict)  # keyed by signal name
    artifacts: dict[str, str] = field(default_factory=dict)       # name -> file path (wavs, pngs)

@dataclass
class Spectrum:
    freqs: "np.ndarray"   # Hz
    mags_db: "np.ndarray" # dBFS

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
    overall: Status                  # worst of all diffs
    diffs: list[DiffResult]
    diagnosis: Optional[str] = None  # filled by diagnose.py (GPT-5.6 text)
```

## Interfaces between modules

### host.py → measurements
```python
class PluginHost(Protocol):
    name: str
    def render(self, input_signal: np.ndarray, samplerate: int) -> np.ndarray: ...
    # input/output: float32 mono or (channels, samples); document your choice, be consistent.
```
Implementations: `PedalboardHost(path_to_vst3)`, `WavFileHost(in_wav, out_wav)`,
`fixtures.biquad.BiquadFixture(...)` — all satisfy the same Protocol.

### signals.py
Each generator returns `(signal: np.ndarray, samplerate: int)`:
`sine_sweep(sr, dur)`, `multitone(sr, dur)`, `hf_tone(sr, dur, freq)`, `silence(sr, dur)`,
`full_scale_extremes(sr, dur)`.

### measurements.py
```python
def frequency_response(inp, out, sr) -> tuple[Metric, Spectrum]: ...
def thd_n(inp, out, sr) -> Metric: ...
def aliasing_score(inp, out, sr) -> tuple[Metric, Spectrum]: ...
def nan_denormal_check(inp, out, sr) -> Metric: ...
```
The 4 canonical metric names: `freq_response_dev`, `thd_n`, `aliasing_score`, `nan_denormal`.

### baseline.py
- `save_baseline(run: RunResult, path)` → JSON (spectra stored as lists).
- `load_baseline(path) -> RunResult`
- `diff(baseline: RunResult, current: RunResult, thresholds: dict[str, float]) -> Verdict`
- Default thresholds: freq_response_dev 0.5 dB · thd_n 6 dB rise · aliasing_score 6 dB rise ·
  nan_denormal any nonzero = FAIL.

### diagnose.py
`diagnose(verdict: Verdict, context: dict) -> str` — calls OpenAI model `gpt-5.6`; must be
mockable (accept an injected client).

### report.py
`render_report(run: RunResult, verdict: Verdict, out_html: Path)` — fills
`assets/report_shell.html` template ({{placeholders}}); if the shell is missing, fall back to a
plain built-in template. Single self-contained HTML (base64-embedded PNGs).

### cli.py (typer)
- `pluginproof baseline <plugin> [--out golden.json]`
- `pluginproof check <plugin> [--baseline golden.json] [--report report.html]`
  exits 0 on PASS, 1 on WARN, 2 on FAIL.
- `<plugin>` resolution: path ending `.vst3` → PedalboardHost; `fixture:biquad` → fixture;
  `--wav-in/--wav-out` pair → WavFileHost.

## Ownership map (do not edit outside your lane)
- **Agent M:** `pluginproof/signals.py`, `pluginproof/measurements.py`, `tests/test_measurements.py`
- **Agent H:** `pluginproof/host.py`, `pluginproof/fixtures/biquad.py`, `tests/test_host.py`
- **Agent O:** `pluginproof/baseline.py`, `pluginproof/cli.py`, `tests/test_baseline.py`, `tests/test_cli.py`
- **Agent R:** `pluginproof/report.py`, `pluginproof/diagnose.py`, `tests/test_report.py`
- Shared read-only: `pluginproof/contract.py` (already written), this file, PLAN.md.
