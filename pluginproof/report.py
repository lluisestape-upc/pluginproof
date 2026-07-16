"""Self-contained HTML report renderer for PluginProof.

Public API
----------
``render_report(run, verdict, out_html, shell=None, baseline_run=None) -> Path``

* ``run``          -- the *current* :class:`~pluginproof.contract.RunResult`.
* ``verdict``      -- the :class:`~pluginproof.contract.Verdict` from ``baseline.diff``
                      (``verdict.diagnosis`` fills the Diagnosis panel).
* ``out_html``     -- destination path; one self-contained HTML file is written
                      (spectrum plots are matplotlib PNGs embedded as base64 ``<img>``).
* ``shell``        -- optional explicit path to an HTML shell template. When ``None``,
                      ``assets/report_shell.html`` next to the package is used if it
                      exists; otherwise the built-in dark-theme template below.
* ``baseline_run`` -- optional baseline :class:`RunResult`; spectra sharing a key with
                      ``run.spectra`` are overlaid (baseline dashed) on the same axes.

Template placeholder contract (keep these names EXACTLY -- an externally designed
shell drops in later):

    {{plugin_name}}     -- HTML-escaped plugin name/path
    {{overall_status}}  -- "PASS" | "WARN" | "FAIL" (uppercase badge text)
    {{metric_cards}}    -- HTML fragment: one card per DiffResult
    {{spectra}}         -- HTML fragment: base64 <img> spectrum plots (with captions)
    {{diagnosis}}       -- HTML-escaped diagnosis text (verdict.diagnosis)
    {{timestamp}}       -- local render time, "YYYY-MM-DD HH:MM:SS"

Optional extra (replaced when present, safe to omit from a shell):

    {{overall_status_class}} -- "pass" | "warn" | "fail" (lowercase, for CSS classes)
"""
from __future__ import annotations

import base64
import html
import io
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless; must precede pyplot import
import matplotlib.pyplot as plt

from pluginproof.contract import DiffResult, RunResult, Spectrum, Status, Verdict
from pluginproof.diagnose import diagnosis_engine

_ASSETS_SHELL = Path(__file__).resolve().parent.parent / "assets" / "report_shell.html"

_STATUS_COLOR = {
    Status.PASS: "#2ee6a8",
    Status.WARN: "#ffb454",
    Status.FAIL: "#ff5470",
}

_PLOT_BG = "#0d1117"
_PLOT_FG = "#c9d1d9"
_CURRENT_COLOR = "#38d9f5"
_BASELINE_COLOR = "#8b949e"


def render_report(
    run: RunResult,
    verdict: Verdict,
    out_html: Path,
    shell: Path | None = None,
    baseline_run: RunResult | None = None,
) -> Path:
    """Render one self-contained HTML report to *out_html* and return its path."""
    out_html = Path(out_html)
    template = _load_shell(shell)

    values = {
        "plugin_name": html.escape(run.plugin),
        "overall_status": verdict.overall.value.upper(),
        "overall_status_class": verdict.overall.value,
        "metric_cards": _metric_cards_html(verdict.diffs),
        "spectra": _spectra_html(run, baseline_run),
        "diagnosis": html.escape(
            verdict.diagnosis or "No diagnosis available for this run."
        ).replace("\n", "<br>"),
        "diagnosis_engine": html.escape(diagnosis_engine(verdict)),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    page = template
    for key, value in values.items():
        page = page.replace("{{" + key + "}}", value)

    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(page, encoding="utf-8")
    return out_html


def _load_shell(shell: Path | None) -> str:
    """Explicit shell > assets/report_shell.html > built-in template."""
    if shell is not None:
        return Path(shell).read_text(encoding="utf-8")
    if _ASSETS_SHELL.is_file():
        return _ASSETS_SHELL.read_text(encoding="utf-8")
    return _BUILTIN_TEMPLATE


# ---------------------------------------------------------------------------
# Metric cards
# ---------------------------------------------------------------------------

def _metric_cards_html(diffs: list[DiffResult]) -> str:
    if not diffs:
        return '<p class="no-metrics">No metric diffs recorded.</p>'
    return "\n".join(_metric_card(d) for d in diffs)


def _metric_card(d: DiffResult) -> str:
    color = _STATUS_COLOR[d.status]
    unit = html.escape(f" {d.unit}" if d.unit else "")
    return (
        f'<div class="metric-card status-{d.status.value}" '
        f'style="border-left: 4px solid {color};">\n'
        f'  <div class="metric-name">{html.escape(d.metric)}</div>\n'
        f'  <div class="metric-values">\n'
        f'    <span class="metric-baseline">{d.baseline:g}{unit}</span>\n'
        f'    <span class="metric-arrow">&rarr;</span>\n'
        f'    <span class="metric-current">{d.current:g}{unit}</span>\n'
        f"  </div>\n"
        f'  <div class="metric-delta" style="color: {color};">'
        f"&Delta; {d.delta:+g}{unit} <small>(threshold {d.threshold:g}{unit})</small></div>\n"
        f'  <div class="metric-status" style="color: {color};">'
        f"{d.status.value.upper()}</div>\n"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Spectrum plots -> base64 <img>
# ---------------------------------------------------------------------------

def _spectra_html(run: RunResult, baseline_run: RunResult | None) -> str:
    if not run.spectra:
        return '<p class="no-spectra">No spectra captured for this run.</p>'
    blocks = []
    for name, spectrum in run.spectra.items():
        baseline = None
        if baseline_run is not None:
            baseline = baseline_run.spectra.get(name)
        png_b64 = _plot_spectrum_b64(name, spectrum, baseline)
        caption = html.escape(name)
        if baseline is not None:
            caption += " (baseline vs current)"
        blocks.append(
            f'<figure class="spectrum">\n'
            f'  <img alt="Spectrum: {html.escape(name)}" '
            f'src="data:image/png;base64,{png_b64}">\n'
            f"  <figcaption>{caption}</figcaption>\n"
            f"</figure>"
        )
    return "\n".join(blocks)


def _plot_spectrum_b64(name: str, current: Spectrum, baseline: Spectrum | None) -> str:
    fig, ax = plt.subplots(figsize=(8.5, 3.4), dpi=110)
    fig.patch.set_facecolor(_PLOT_BG)
    ax.set_facecolor(_PLOT_BG)
    try:
        if baseline is not None:
            ax.plot(
                baseline.freqs,
                baseline.mags_db,
                color=_BASELINE_COLOR,
                linewidth=1.0,
                linestyle="--",
                label="baseline",
            )
        ax.plot(
            current.freqs,
            current.mags_db,
            color=_CURRENT_COLOR,
            linewidth=1.2,
            label="current",
        )
        ax.set_xscale("log")
        ax.set_xlabel("Frequency (Hz)", color=_PLOT_FG, fontsize=9)
        ax.set_ylabel("Magnitude (dBFS)", color=_PLOT_FG, fontsize=9)
        ax.set_title(name, color=_PLOT_FG, fontsize=10, family="monospace")
        ax.tick_params(colors=_PLOT_FG, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#30363d")
        ax.grid(True, which="both", color="#21262d", linewidth=0.5)
        legend = ax.legend(
            loc="lower left", fontsize=8, facecolor=_PLOT_BG, edgecolor="#30363d"
        )
        for text in legend.get_texts():
            text.set_color(_PLOT_FG)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=_PLOT_BG)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# Built-in fallback template (oscilloscope / lab-instrument dark theme)
# ---------------------------------------------------------------------------

_BUILTIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PluginProof &mdash; {{plugin_name}}</title>
<style>
  :root {
    --bg: #0d1117; --panel: #161b22; --edge: #30363d;
    --fg: #c9d1d9; --dim: #8b949e; --accent: #38d9f5;
    --pass: #2ee6a8; --warn: #ffb454; --fail: #ff5470;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 2rem; background: var(--bg); color: var(--fg);
    font-family: "Segoe UI", system-ui, sans-serif;
    background-image: radial-gradient(#161b22 1px, transparent 1px);
    background-size: 24px 24px;
  }
  .mono, .metric-values, .metric-delta, .metric-name, .badge, .stamp {
    font-family: "Cascadia Code", "JetBrains Mono", Consolas, monospace;
  }
  header {
    display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
    border: 1px solid var(--edge); border-radius: 8px;
    background: var(--panel); padding: 1rem 1.5rem; margin-bottom: 1.5rem;
  }
  header h1 { font-size: 1.15rem; margin: 0; font-weight: 600; }
  header .sub { color: var(--dim); font-size: 0.8rem; }
  .badge {
    margin-left: auto; padding: 0.35rem 1rem; border-radius: 6px;
    font-weight: 700; letter-spacing: 0.15em; font-size: 0.95rem;
    border: 1px solid currentColor;
  }
  .badge.pass { color: var(--pass); background: rgba(46,230,168,0.08); }
  .badge.warn { color: var(--warn); background: rgba(255,180,84,0.08); }
  .badge.fail { color: var(--fail); background: rgba(255,84,112,0.08); }
  h2 {
    font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.2em;
    color: var(--dim); margin: 2rem 0 0.75rem;
  }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 0.75rem; }
  .metric-card {
    background: var(--panel); border: 1px solid var(--edge); border-radius: 6px;
    padding: 0.9rem 1rem;
  }
  .metric-name { color: var(--accent); font-size: 0.85rem; margin-bottom: 0.4rem; }
  .metric-values { font-size: 1.05rem; }
  .metric-baseline { color: var(--dim); }
  .metric-arrow { color: var(--dim); padding: 0 0.3rem; }
  .metric-delta { font-size: 0.8rem; margin-top: 0.35rem; }
  .metric-delta small { color: var(--dim); }
  .metric-status { font-size: 0.7rem; letter-spacing: 0.2em; margin-top: 0.4rem; font-weight: 700; }
  .spectra { display: flex; flex-direction: column; gap: 1rem; }
  figure.spectrum {
    margin: 0; background: var(--panel); border: 1px solid var(--edge);
    border-radius: 6px; padding: 0.75rem;
  }
  figure.spectrum img { width: 100%; height: auto; display: block; border-radius: 4px; }
  figure.spectrum figcaption { color: var(--dim); font-size: 0.75rem; margin-top: 0.4rem; }
  .diagnosis {
    background: var(--panel); border: 1px solid var(--edge); border-left: 4px solid var(--accent);
    border-radius: 6px; padding: 1rem 1.25rem; line-height: 1.55; font-size: 0.92rem;
  }
  footer { margin-top: 2rem; color: var(--dim); font-size: 0.72rem; }
</style>
</head>
<body>
  <header>
    <div>
      <h1>{{plugin_name}}</h1>
      <div class="sub">PluginProof regression report</div>
    </div>
    <span class="badge {{overall_status_class}}">{{overall_status}}</span>
  </header>

  <h2>Metrics</h2>
  <div class="cards">
{{metric_cards}}
  </div>

  <h2>Spectra</h2>
  <div class="spectra">
{{spectra}}
  </div>

  <h2>AI Diagnosis &mdash; {{diagnosis_engine}}</h2>
  <div class="diagnosis">{{diagnosis}}</div>

  <footer class="stamp">generated {{timestamp}} &middot; pluginproof</footer>
</body>
</html>
"""
