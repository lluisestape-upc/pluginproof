"""Typer CLI for PluginProof.

Commands:
- pluginproof baseline <plugin> [--out golden.json] [--samplerate 48000]
- pluginproof check <plugin> [--baseline golden.json] [--report report.html]
                             [--samplerate 48000] [--no-diagnose]
  exit codes: 0 = PASS, 1 = WARN, 2 = FAIL

Host / measurement / report / diagnose modules are imported lazily so the CLI
loads (and is testable with mocks) even while sibling workstreams are unfinished.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from pluginproof.baseline import diff as diff_runs
from pluginproof.baseline import load_baseline, run_suite, save_baseline
from pluginproof.contract import DEFAULT_THRESHOLDS, RunResult, Status, Verdict

app = typer.Typer(
    name="pluginproof",
    help="Regression testing for audio plugins - catch audio bugs before your users do.",
    no_args_is_help=True,
)

# Wide fixed width when output is captured/piped (tests, CI) so table cells
# don't wrap mid-word; auto-detect width on a real terminal.
console = Console(width=None if sys.stdout.isatty() else 160)

_STATUS_STYLE = {
    Status.PASS: "bold green",
    Status.WARN: "bold yellow",
    Status.FAIL: "bold red",
}
_EXIT_CODE = {Status.PASS: 0, Status.WARN: 1, Status.FAIL: 2}


def _fail(message: str, code: int = 2) -> "typer.Exit":
    console.print(f"[bold red]error:[/bold red] {message}")
    return typer.Exit(code=code)


def resolve_host(plugin: str,
                 wav_in: Optional[Path] = None,
                 wav_out: Optional[Path] = None):
    """Resolve a plugin spec to a PluginHost.

    - "fixture:biquad"            -> fixtures.biquad.BiquadFixture
    - path ending in .vst3        -> host.PedalboardHost
    - --wav-in/--wav-out provided -> host.WavFileHost
    """
    if plugin.startswith("fixture:"):
        fixture_name = plugin.split(":", 1)[1]
        if fixture_name not in ("biquad", "biquad:buggy"):
            raise _fail(f"unknown fixture '{fixture_name}' (available: biquad, biquad:buggy)")
        try:
            from pluginproof.fixtures.biquad import BiquadFixture
        except ImportError as exc:
            raise _fail(f"fixtures/biquad module not ready yet ({exc})")
        return BiquadFixture(buggy=fixture_name.endswith(":buggy"))

    if wav_in is not None or wav_out is not None:
        if wav_in is None or wav_out is None:
            raise _fail("--wav-in and --wav-out must be given together")
        try:
            from pluginproof.host import WavFileHost
        except ImportError as exc:
            raise _fail(f"host module not ready yet ({exc})")
        return WavFileHost(wav_in, wav_out)

    if plugin.lower().endswith(".vst3"):
        try:
            from pluginproof.host import PedalboardHost
        except ImportError as exc:
            raise _fail(f"host module not ready yet ({exc})")
        return PedalboardHost(plugin)

    raise _fail(
        f"cannot resolve plugin '{plugin}': expected a .vst3 path, 'fixture:biquad', "
        "or a --wav-in/--wav-out pair"
    )


def _fmt(value: float) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:+.3f}" if value < 0 else f"{value:.3f}"


def _print_run_summary(run: RunResult) -> None:
    table = Table(title=f"Baseline for {run.plugin} @ {run.samplerate} Hz")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_column("Unit")
    for m in run.metrics:
        table.add_row(m.name, _fmt(m.value), m.unit)
    console.print(table)


def _print_verdict(verdict: Verdict, plugin: str) -> None:
    table = Table(title=f"Regression check for {plugin}")
    table.add_column("Metric", style="cyan")
    table.add_column("Baseline", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Threshold", justify="right")
    table.add_column("Status")
    table.add_column("Note", style="dim")
    for d in verdict.diffs:
        style = _STATUS_STYLE[d.status]
        table.add_row(
            d.metric,
            _fmt(d.baseline),
            _fmt(d.current),
            _fmt(d.delta),
            _fmt(d.threshold),
            f"[{style}]{d.status.value.upper()}[/{style}]",
            getattr(d, "note", ""),
        )
    console.print(table)
    style = _STATUS_STYLE[verdict.overall]
    console.print(f"Overall: [{style}]{verdict.overall.value.upper()}[/{style}]")
    if verdict.diagnosis:
        console.print()
        console.print("[bold]Diagnosis[/bold]")
        console.print(verdict.diagnosis)


@app.command()
def baseline(
    plugin: str = typer.Argument(..., help=".vst3 path, 'fixture:biquad', or wav pair"),
    out: Path = typer.Option(Path("golden.json"), "--out", help="Where to write the golden baseline JSON."),
    samplerate: int = typer.Option(48000, "--samplerate", help="Samplerate for the test suite."),
    wav_in: Optional[Path] = typer.Option(None, "--wav-in", help="Input WAV for WavFileHost."),
    wav_out: Optional[Path] = typer.Option(None, "--wav-out", help="Output WAV for WavFileHost."),
):
    """Run the measurement suite and save a golden baseline snapshot."""
    host = resolve_host(plugin, wav_in, wav_out)
    try:
        run = run_suite(host, samplerate)
    except RuntimeError as exc:
        raise _fail(str(exc))
    save_baseline(run, out)
    _print_run_summary(run)
    console.print(f"Baseline saved to [bold]{out}[/bold]")


@app.command()
def check(
    plugin: str = typer.Argument(..., help=".vst3 path, 'fixture:biquad', or wav pair"),
    baseline_path: Path = typer.Option(Path("golden.json"), "--baseline", help="Golden baseline JSON to diff against."),
    report: Optional[Path] = typer.Option(None, "--report", help="Write an HTML report here."),
    samplerate: int = typer.Option(48000, "--samplerate", help="Samplerate for the test suite."),
    diagnose: bool = typer.Option(True, "--diagnose/--no-diagnose", help="Run the GPT diagnosis on non-PASS verdicts."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Diagnosis provider override: openai, anthropic, ollama, or off."),
    wav_in: Optional[Path] = typer.Option(None, "--wav-in", help="Input WAV for WavFileHost."),
    wav_out: Optional[Path] = typer.Option(None, "--wav-out", help="Output WAV for WavFileHost."),
):
    """Run the suite, diff against the golden baseline, and report pass/warn/fail."""
    if not Path(baseline_path).exists():
        raise _fail(
            f"baseline file '{baseline_path}' not found - run "
            f"'pluginproof baseline {plugin}' first"
        )

    host = resolve_host(plugin, wav_in, wav_out)
    try:
        current = run_suite(host, samplerate)
    except RuntimeError as exc:
        raise _fail(str(exc))

    base = load_baseline(baseline_path)
    verdict = diff_runs(base, current, DEFAULT_THRESHOLDS)

    if diagnose and verdict.overall is not Status.PASS:
        try:
            from pluginproof.diagnose import diagnose as run_diagnose
            verdict.diagnosis = run_diagnose(
                verdict,
                {"plugin": plugin, "samplerate": samplerate},
                provider=provider,
            )
        except ImportError:
            console.print("[dim]diagnose module not ready yet - skipping diagnosis[/dim]")
        except Exception as exc:  # diagnosis is best-effort, never break the check
            console.print(f"[dim]diagnosis unavailable ({exc})[/dim]")

    _print_verdict(verdict, plugin)

    if report is not None:
        try:
            from pluginproof.report import render_report
            render_report(current, verdict, Path(report))
            console.print(f"Report written to [bold]{report}[/bold]")
        except ImportError:
            console.print("[dim]report module not ready yet - skipping HTML report[/dim]")
        except Exception as exc:
            console.print(f"[yellow]report generation failed:[/yellow] {exc}")

    raise typer.Exit(code=_EXIT_CODE[verdict.overall])


if __name__ == "__main__":
    app()
