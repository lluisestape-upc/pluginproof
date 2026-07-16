# Devpost submission copy — PluginProof

**Category:** Developer Tools
**Tagline (short):** Regression testing for audio plugins — catch audio bugs before your users do.

## Inspiration

I develop audio plugins (guitar effects, a distortion called MegaCrusher). Every release
is tested the same way the whole industry does it: *by ear*. But ears don't diff. A
refactor that compiles cleanly can invert a parameter mapping, bypass an oversampler, or
introduce denormals — and you find out from a 1-star review. Web developers have Jest
snapshots and visual regression tools; audio developers have nothing. PluginProof is the
missing `pytest` for sound.

## What it does

PluginProof measures a VST3 plugin like a lab instrument — frequency response, THD+N,
aliasing, NaN/denormal stability — through the real plugin binary, and snapshots a
**golden baseline**. After every build, one click (or one CI job) re-measures and diffs
against the golden with per-metric thresholds: PASS / WARN / FAIL, spectrum overlays,
and exit codes 0/1/2.

Then the part numbers can't do: the metric diffs go to an AI that answers like a DSP
engineer — *"severe aliasing above 15 kHz, check your oversampling stage."* It ships
with **GPT-5.6 as the default engine**, and it's bring-your-own-key: Anthropic works,
and **Ollama runs it 100 % local and free** (perfect for CI). A rule-based offline
fallback means the tool never breaks, and every report honestly labels which engine
produced the diagnosis.

It's a real product, not a PoC: a windowed desktop app (drag a .vst3 onto a phosphor CRT
screen — the whole UI is a bench-instrument faceplate), a single-file Windows exe on the
GitHub release, a typer CLI, and a GitHub Actions gate that blocks PRs that regress
audio in under two minutes.

## How we built it

The core was built in a Codex session with GPT-5.6 (Session ID submitted): the pywebview
desktop app — including root-causing from library source why dropped-file paths only
exist in pywebview's Python-side DOM events — the re-measure-on-check flow, the
multi-provider BYOK diagnosis layer (one OpenAI-compatible code path for three
providers), real-world VST3 hardening (stereo adaptation, single-thread affinity for
pedalboard renders), PyInstaller packaging, and the CI workflow. The measurement engine
is numpy/scipy: Welch H1 transfer functions, least-squares sine fitting for THD (with
frequency refinement — FFT interpolation alone de-phased the fit), and an aliasing score
that counts only non-harmonic image energy. Spotify's pedalboard hosts the VST3, which
avoided writing a C++ headless host. Claude Code agents scaffolded specs and tests in
parallel lanes against a shared data contract, and the brand started from a Gemini pass.

## Challenges we ran into

Measuring "does it sound the same" without false positives: the whole pipeline is
deterministic (seeded phases, fixed stimuli) so an unchanged plugin diffs to exactly
zero. THD via naive FFT interpolation read −31 dB on a *clean* signal — a 0.02 Hz
frequency error de-phases the sine fit — fixed with a bounded refinement step. And
pywebview drag-and-drop silently never exposes file paths to in-page JavaScript.

## Accomplishments we're proud of

A real bug demo: a one-line "branchless" refactor of MegaCrusher's bit crusher that
compiles clean and quantizes audio to 4 levels — caught with a triple FAIL (+43 dB freq,
+19 dB THD, +19.6 dB aliasing) and an AI explanation. Both builds and the golden are in
the repo so judges can reproduce the catch in 60 seconds without compiling anything.

## What we learned

Domain moats matter: the hard part wasn't the app, it was knowing *what to measure* and
what makes a measurement trustworthy. Also: deterministic > statistical for CI gates.

## What's next

More formats (AU, CLAP), parameter-sweep baselines (measure at N parameter snapshots,
not just defaults), latency/CPU regression tracking, and a hosted dashboard for teams.

---

**Try it:** `PluginProof.exe` on the [release page](https://github.com/lluisestape-upc/pluginproof/releases) ·
repro the caught bug: `pluginproof check assets/MegaCrusher_buggy.vst3 --baseline demo_golden.json`
