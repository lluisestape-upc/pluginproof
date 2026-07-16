from __future__ import annotations

from pathlib import Path

from pluginproof.contract import Metric, RunResult
import pluginproof.gui as gui


def make_run() -> RunResult:
    return RunResult(
        plugin="MegaCrusher.vst3", samplerate=48000,
        metrics=[Metric("freq_response_dev", 0.0, "dB")],
    )


def test_baseline_path_is_per_plugin_and_stable(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "app_data_dir", lambda: tmp_path)
    first = gui.baseline_path(tmp_path / "one" / "Plugin.vst3")
    second = gui.baseline_path(tmp_path / "two" / "Plugin.vst3")
    assert first.parent == tmp_path / "baselines"
    assert first.name != second.name
    assert first == gui.baseline_path(tmp_path / "one" / "Plugin.vst3")


def test_set_golden_and_check_render_report(monkeypatch, tmp_path):
    plugin = tmp_path / "Plugin.vst3"
    plugin.touch()
    api = gui.PluginProofApi()
    monkeypatch.setattr(gui, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(gui, "PedalboardHost", lambda path: path)
    monkeypatch.setattr(gui, "run_suite", lambda host, sr: make_run())
    monkeypatch.setattr(gui, "diagnose", lambda verdict, context: "AI diagnosis")

    # Exercise the worker body synchronously; start_measurement itself is asynchronous.
    api._measure(plugin)
    saved = api.set_golden()
    checked = api.check()

    assert saved["ok"] is True
    assert Path(saved["baseline"]).exists()
    assert checked == {"ok": True, "status": "pass"}
    assert api.state()["report_url"].startswith("file:")
    assert "AI diagnosis" in api.state()["message"]

    assert api.new_check() == {"ok": True}
    assert api.state()["phase"] == "ready"


def test_check_requires_golden(monkeypatch, tmp_path):
    plugin = tmp_path / "Plugin.vst3"
    plugin.touch()
    api = gui.PluginProofApi()
    monkeypatch.setattr(gui, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(gui, "PedalboardHost", lambda path: path)
    monkeypatch.setattr(gui, "run_suite", lambda host, sr: make_run())
    api._measure(plugin)
    result = api.check()
    assert result["ok"] is False
    assert "Set Golden" in result["error"]
