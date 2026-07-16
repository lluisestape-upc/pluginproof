"""Bring-your-own-provider diagnosis with a reliable rule-based fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path

from pluginproof.contract import DiffResult, Status, Verdict

DEFAULTS = {
    "openai": "gpt-5.6",
    "anthropic": "claude-opus-4-8",
    "ollama": "llama3.2",
}
PROVIDERS = frozenset((*DEFAULTS, "off"))
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1/"
OLLAMA_BASE_URL = "http://localhost:11434/v1"
FALLBACK_MARKER = "[rule-based]"

_SYSTEM_PROMPT = (
    "You are an audio DSP expert helping a plugin developer understand a regression "
    "test result. Given metric diffs between a known-good baseline build and the "
    "current build, explain in 2-4 plain sentences what most likely changed in the "
    "audio path and where to look. Be concrete and brief; no markdown, no preamble."
)


def config_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "PluginProof" / "config.json"


def load_settings(path: Path | None = None) -> dict[str, str]:
    """Load persisted provider settings; malformed/missing config safely defaults."""
    try:
        raw = json.loads((path or config_path()).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        raw = {}
    provider = str(raw.get("provider", "openai")).lower()
    if provider not in PROVIDERS:
        provider = "off"
    return {
        "provider": provider,
        "api_key": str(raw.get("api_key", "")),
        "model": str(raw.get("model") or DEFAULTS.get(provider, "")),
    }


def save_settings(settings: dict, path: Path | None = None) -> dict[str, str]:
    """Validate and persist the portable JSON BYOK configuration."""
    provider = str(settings.get("provider", "off")).lower()
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r}.")
    api_key = str(settings.get("api_key", "")).strip()
    if provider in {"openai", "anthropic"} and not api_key:
        raise ValueError(f"An API key is required for {provider}.")
    normalized = {
        "provider": provider,
        "api_key": api_key,
        "model": str(settings.get("model") or DEFAULTS.get(provider, "")).strip(),
    }
    destination = path or config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return normalized


def diagnose(verdict: Verdict, context: dict, client=None, provider: str | None = None) -> str:
    """Return an engine-labelled diagnosis; never raise on unavailable AI services."""
    settings = load_settings()
    if provider is not None:
        settings["provider"] = provider.lower()
        settings["model"] = DEFAULTS.get(settings["provider"], settings["model"])
    elif client is not None:
        # Preserve the simple injectable-client test seam regardless of local config.
        settings["provider"] = "openai"
        settings["model"] = DEFAULTS["openai"]
    selected = settings["provider"]
    if selected not in PROVIDERS or selected == "off":
        return _fallback_diagnosis(verdict, context)

    model = settings["model"] or DEFAULTS[selected]
    label = _engine_label(selected, model)
    if client is None:
        client = _make_client(selected, settings.get("api_key", ""))
    if client is None:
        return _fallback_diagnosis(verdict, context)
    try:
        text = _call_model(client, build_prompt(verdict, context), model)
    except Exception:
        return _fallback_diagnosis(verdict, context)
    if not text or not text.strip():
        return _fallback_diagnosis(verdict, context)
    verdict.diagnosis_engine = label
    return f"[{label}] {text.strip()}"


def _make_client(provider: str, configured_key: str):
    try:
        import openai
    except ImportError:
        return None
    if provider == "openai":
        key = configured_key or os.environ.get("OPENAI_API_KEY")
        kwargs = {"api_key": key} if key else {}
    elif provider == "anthropic":
        key = configured_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return None
        kwargs = {"api_key": key, "base_url": ANTHROPIC_BASE_URL}
    else:  # ollama
        kwargs = {"api_key": "ollama", "base_url": OLLAMA_BASE_URL}
    try:
        return openai.OpenAI(**kwargs)
    except Exception:
        return None


def _engine_label(provider: str, model: str) -> str:
    if provider == "openai":
        return "GPT-5.6" if model == "gpt-5.6" else model
    if provider == "anthropic":
        return "Claude" if model == "claude-opus-4-8" else model
    return f"{model} (local)"


def diagnosis_engine(verdict: Verdict) -> str:
    """The displayable engine label, including a sensible default for old reports."""
    return getattr(verdict, "diagnosis_engine", "rule-based")


def build_prompt(verdict: Verdict, context: dict) -> str:
    lines = [f"Plugin: {context.get('plugin', 'unknown')}", f"Samplerate: {context.get('samplerate', 'unknown')} Hz", f"Overall verdict: {verdict.overall.value.upper()}", "", "Metric diffs (baseline -> current):"]
    for d in verdict.diffs:
        lines.append(f"- {d.metric}: baseline {d.baseline:g}{_u(d)} -> current {d.current:g}{_u(d)}, delta {d.delta:+g}{_u(d)} (threshold {d.threshold:g}{_u(d)}) => {d.status.value.upper()}")
    lines.extend(("", "Diagnose the most likely DSP-level cause and where to look in the code."))
    return "\n".join(lines)


def _call_model(client, prompt: str, model: str) -> str:
    responses = getattr(client, "responses", None)
    if responses is not None and hasattr(responses, "create"):
        response = responses.create(model=model, instructions=_SYSTEM_PROMPT, input=prompt)
        if getattr(response, "output_text", None):
            return response.output_text
    response = client.chat.completions.create(model=model, messages=[{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return response.choices[0].message.content


def _u(d: DiffResult) -> str:
    return f" {d.unit}" if d.unit else ""


_METRIC_HINTS = {
    "aliasing_score": "aliasing energy rose {delta:+g}{unit}; check oversampling/decimation and waveshaping",
    "thd_n": "THD+N rose {delta:+g}{unit}; check gain staging, saturation, or clipping",
    "freq_response_dev": "frequency response changed by {delta:+g}{unit}; check filter coefficients and samplerate handling",
    "nan_denormal": "{current:g} NaN/denormal samples detected; check feedback state and denormal flushing",
}


def _fallback_diagnosis(verdict: Verdict, context: dict) -> str:
    verdict.diagnosis_engine = "rule-based"
    plugin = context.get("plugin", "the plugin")
    bad = [d for d in verdict.diffs if d.status is not Status.PASS]
    if verdict.overall is Status.PASS or not bad:
        return f"{FALLBACK_MARKER} All metrics for {plugin} are within thresholds versus the baseline; no regression detected."
    parts = []
    for d in sorted(bad, key=lambda item: item.status is not Status.FAIL):
        template = _METRIC_HINTS.get(d.metric, "{metric} moved {delta:+g}{unit} beyond its {threshold:g}{unit} threshold")
        parts.append(template.format(metric=d.metric, delta=d.delta, baseline=d.baseline, current=d.current, threshold=d.threshold, unit=_u(d)))
    return f"{FALLBACK_MARKER} {verdict.overall.value.upper()} for {plugin}: " + "; ".join(parts) + "."
