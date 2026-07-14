"""AI diagnosis layer for PluginProof.

``diagnose(verdict, context, client=None)`` turns a :class:`~pluginproof.contract.Verdict`
into a short plain-language explanation for an audio developer, using the OpenAI API
(model ``gpt-5.6``). The OpenAI client is injectable for testing; when no client is
given, one is constructed lazily from the ``OPENAI_API_KEY`` environment variable.

If the ``openai`` package is missing, no API key is set, or the API call fails for any
reason, a clearly-marked rule-based fallback diagnosis is generated locally from the
diff results, so the tool keeps working offline. This function never raises.
"""
from __future__ import annotations

import os

from pluginproof.contract import DiffResult, Status, Verdict

MODEL = "gpt-5.6"

FALLBACK_MARKER = "[offline rule-based diagnosis - AI unavailable]"

_SYSTEM_PROMPT = (
    "You are an audio DSP expert helping a plugin developer understand a regression "
    "test result. Given metric diffs between a known-good baseline build and the "
    "current build, explain in 2-4 plain sentences what most likely changed in the "
    "audio path and where to look (e.g. oversampling, filter coefficients, gain "
    "staging, denormal handling). Be concrete and brief; no markdown, no preamble."
)


def diagnose(verdict: Verdict, context: dict, client=None) -> str:
    """Return a short plain-language diagnosis of *verdict*. Never raises.

    Parameters
    ----------
    verdict:  the Verdict produced by ``baseline.diff``.
    context:  free-form run context, e.g. ``{"plugin": "MyComp.vst3", "samplerate": 48000}``.
    client:   optional pre-built OpenAI-compatible client (injected in tests).
              When ``None``, an ``openai.OpenAI()`` client is constructed lazily,
              reading ``OPENAI_API_KEY`` from the environment.
    """
    prompt = build_prompt(verdict, context)

    if client is None:
        client = _default_client()
        if client is None:
            return _fallback_diagnosis(verdict, context)

    try:
        text = _call_model(client, prompt)
    except Exception:
        return _fallback_diagnosis(verdict, context)

    if not text or not text.strip():
        return _fallback_diagnosis(verdict, context)
    return text.strip()


def build_prompt(verdict: Verdict, context: dict) -> str:
    """Build the compact user prompt sent to the model (also unit-testable)."""
    lines = [
        f"Plugin: {context.get('plugin', 'unknown')}",
        f"Samplerate: {context.get('samplerate', 'unknown')} Hz",
        f"Overall verdict: {verdict.overall.value.upper()}",
        "",
        "Metric diffs (baseline -> current):",
    ]
    for d in verdict.diffs:
        lines.append(
            f"- {d.metric}: baseline {d.baseline:g}{_u(d)} -> current {d.current:g}{_u(d)}, "
            f"delta {d.delta:+g}{_u(d)} (threshold {d.threshold:g}{_u(d)}) => {d.status.value.upper()}"
        )
    lines.append("")
    lines.append("Diagnose the most likely DSP-level cause and where to look in the code.")
    return "\n".join(lines)


def _u(d: DiffResult) -> str:
    return f" {d.unit}" if d.unit else ""


def _default_client():
    """Lazily construct an openai.OpenAI() client, or None if unavailable."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        import openai  # lazy: package must be optional
    except ImportError:
        return None
    try:
        return openai.OpenAI()
    except Exception:
        return None


def _call_model(client, prompt: str) -> str:
    """Call the Responses API, falling back to Chat Completions for older clients."""
    responses = getattr(client, "responses", None)
    if responses is not None and hasattr(responses, "create"):
        resp = responses.create(
            model=MODEL,
            instructions=_SYSTEM_PROMPT,
            input=prompt,
        )
        text = getattr(resp, "output_text", None)
        if text:
            return text
        # Fall through to chat if the response shape was unexpected.
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# Offline rule-based fallback
# ---------------------------------------------------------------------------

_METRIC_HINTS = {
    "aliasing_score": (
        "aliasing energy rose {delta:+g}{unit} - non-harmonic images are folding back "
        "into the audible band; check your oversampling/decimation stage and any "
        "waveshaping or modulation added since the baseline"
    ),
    "thd_n": (
        "THD+N rose {delta:+g}{unit} - the output is more distorted/noisy than the "
        "baseline; check gain staging, saturation curves, or an unintended clipping stage"
    ),
    "freq_response_dev": (
        "frequency response deviates by {delta:+g}{unit} from the baseline - the tonal "
        "balance changed; check filter coefficients, cutoff/Q parameters, or "
        "samplerate-dependent coefficient computation"
    ),
    "nan_denormal": (
        "{current:g} NaN/denormal samples detected (baseline {baseline:g}) - check "
        "feedback paths, uninitialised state, and add denormal flushing (FTZ/DAZ) in "
        "recursive filters"
    ),
}


def _fallback_diagnosis(verdict: Verdict, context: dict) -> str:
    plugin = context.get("plugin", "the plugin")
    bad = [d for d in verdict.diffs if d.status is not Status.PASS]

    if verdict.overall is Status.PASS or not bad:
        body = (
            f"All metrics for {plugin} are within thresholds versus the baseline; "
            "no regression detected."
        )
        return f"{FALLBACK_MARKER} {body}"

    parts = []
    for d in sorted(bad, key=lambda d: d.status is not Status.FAIL):  # FAILs first
        template = _METRIC_HINTS.get(d.metric)
        if template is None:
            template = (
                "{metric} moved {delta:+g}{unit} beyond its {threshold:g}{unit} threshold"
            )
        parts.append(
            template.format(
                metric=d.metric,
                delta=d.delta,
                baseline=d.baseline,
                current=d.current,
                threshold=d.threshold,
                unit=_u(d),
            )
        )
    body = f"{verdict.overall.value.upper()} for {plugin}: " + "; ".join(parts) + "."
    return f"{FALLBACK_MARKER} {body}"
