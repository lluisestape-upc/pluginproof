"""Native desktop shell for PluginProof's existing measurement engine.

Run with ``python -m pluginproof.gui``.  The browser-facing API deliberately
contains no DSP logic: it only coordinates the shared host, baseline, and
report modules and exposes state for the small pywebview page.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any

from pluginproof.baseline import diff, load_baseline, run_suite, save_baseline
from pluginproof.diagnose import FALLBACK_MARKER, diagnose
from pluginproof.host import PedalboardHost
from pluginproof.report import render_report

APP_NAME = "PluginProof"
SAMPLE_RATE = 48_000
_GUI_PAGE = Path(__file__).resolve().parent.parent / "assets" / "gui.html"


def app_data_dir() -> Path:
    """Return the per-user PluginProof data directory, creating it if needed."""
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def baseline_path(plugin_path: str | Path) -> Path:
    """Create a stable, collision-resistant baseline name for a plugin path."""
    plugin = Path(plugin_path).expanduser().resolve()
    digest = hashlib.sha256(str(plugin).encode("utf-8")).hexdigest()[:12]
    return app_data_dir() / "baselines" / f"{plugin.name}-{digest}.json"


class PluginProofApi:
    """The small API exposed to the pywebview page as ``window.pywebview.api``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "phase": "idle", "message": "Drop a .vst3 file to begin.",
            "plugin": None, "report_url": None,
        }
        self._run = None
        self._plugin_path: Path | None = None

    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def pick_plugin(self) -> dict[str, Any]:
        """Show the native file chooser.  Called only from the UI thread."""
        try:
            import webview
            selected = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("VST3 plugins (*.vst3)", "All files (*.*)"),
            )
        except Exception as exc:
            return {"ok": False, "error": f"Could not open file picker: {exc}"}
        if not selected:
            return {"ok": False, "cancelled": True}
        return self.start_measurement(selected[0])

    def start_measurement(self, plugin_path: str) -> dict[str, Any]:
        path = Path(plugin_path).expanduser()
        if path.suffix.lower() != ".vst3":
            return {"ok": False, "error": "Choose a .vst3 plugin file."}
        if not path.exists():
            return {"ok": False, "error": f"Plugin not found: {path}"}
        with self._lock:
            if self._state["phase"] == "measuring":
                return {"ok": False, "error": "A measurement is already running."}
            self._state = {"phase": "measuring", "message": "Loading plugin…",
                           "plugin": str(path), "report_url": None}
            self._run = None
            self._plugin_path = path
        threading.Thread(target=self._measure, args=(path,), daemon=True).start()
        return {"ok": True}

    def _measure(self, path: Path) -> None:
        try:
            with self._lock:
                self._plugin_path = path
                self._state["plugin"] = str(path)
                self._state["message"] = "Running frequency, distortion, aliasing, and stability tests…"
            run = run_suite(PedalboardHost(path), SAMPLE_RATE)
        except Exception as exc:
            with self._lock:
                self._state.update(phase="error", message=f"Measurement failed: {exc}")
            return
        with self._lock:
            self._run = run
            self._state.update(phase="ready", message="Measurement complete. Set a golden baseline or check it.")

    def set_golden(self) -> dict[str, Any]:
        run, path, error = self._ready_run()
        if error:
            return {"ok": False, "error": error}
        destination = baseline_path(path)
        try:
            save_baseline(run, destination)
        except Exception as exc:
            return {"ok": False, "error": f"Could not save golden baseline: {exc}"}
        with self._lock:
            self._state["message"] = f"Golden baseline saved: {destination.name}"
        return {"ok": True, "baseline": str(destination)}

    def check(self) -> dict[str, Any]:
        run, path, error = self._ready_run()
        if error:
            return {"ok": False, "error": error}
        golden = baseline_path(path)
        if not golden.exists():
            return {"ok": False, "error": "No golden baseline yet. Choose Set Golden first."}
        try:
            baseline = load_baseline(golden)
            verdict = diff(baseline, run)
            try:
                verdict.diagnosis = diagnose(
                    verdict,
                    context={"plugin": str(path), "samplerate": SAMPLE_RATE},
                )
            except Exception as exc:  # diagnosis is optional; reporting is not
                verdict.diagnosis = f"Diagnosis unavailable: {exc}"
            diagnosis_source = (
                "offline fallback diagnosis"
                if verdict.diagnosis.startswith(FALLBACK_MARKER)
                else "AI diagnosis"
            )
            report = app_data_dir() / "reports" / f"{golden.stem}-report.html"
            render_report(run, verdict, report, baseline_run=baseline)
        except Exception as exc:
            return {"ok": False, "error": f"Could not create report: {exc}"}
        with self._lock:
            self._state.update(phase="report", message=(f"Check complete: {verdict.overall.value.upper()} "
                                                         f"({diagnosis_source})."),
                               report_url=report.resolve().as_uri())
        return {"ok": True, "status": verdict.overall.value}

    def new_check(self) -> dict[str, Any]:
        """Return from the report to the launcher without discarding this run."""
        with self._lock:
            if self._state["phase"] == "measuring":
                return {"ok": False, "error": "Measurement is still running."}
            ready = self._run is not None and self._plugin_path is not None
            self._state.update(
                phase="ready" if ready else "idle",
                message=("Ready to check again, or drop another .vst3 plugin."
                         if ready else "Drop a .vst3 file to begin."),
                report_url=None,
            )
        return {"ok": True}

    def _ready_run(self):
        with self._lock:
            if self._run is None or self._plugin_path is None:
                return None, None, "Drop a plugin and wait for measurement to finish first."
            return self._run, self._plugin_path, None


def main() -> None:
    try:
        import webview
    except ImportError as exc:
        raise SystemExit("PluginProof GUI requires pywebview. Install with: pip install pywebview") from exc
    if not _GUI_PAGE.exists():
        raise SystemExit(f"GUI page not found: {_GUI_PAGE}")
    window = webview.create_window(APP_NAME, _GUI_PAGE.as_uri(), js_api=PluginProofApi(),
                                   width=1180, height=820, min_size=(760, 580))
    webview.start()


if __name__ == "__main__":
    main()
