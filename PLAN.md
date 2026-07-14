# PluginProof — Build Plan (OpenAI Build Week)

> Audio-plugin regression harness. Give it a plugin, it auto-generates a "golden-signal"
> test suite and tells you — in plain language — when a code change broke the sound.
>
> **Track:** Developer Tools · **Deadline:** Tue Jul 21, 5:00 PM PT · **Prize target:** 1st Dev Tools ($15k)
> **Working name:** PluginProof (alts: SignalGuard, Earwitness — swappable)

---

## 0. Critical constraints (read first)

| Constraint | Deadline | Consequence if missed |
|---|---|---|
| **Request free Codex credits** (Resources tab) | **Fri Jul 17, 12:00 PM PT** | No free credits to build with |
| **Core built in a Codex session** → capture `/feedback` Session ID | ongoing | Ineligible / weak on judging criterion #1 |
| Submission (repo + <3min video + description) | **Tue Jul 21, 5:00 PM PT** | No entry |

**⚠️ Eligibility rule that shapes everything:** the submission requires the Codex Session ID
"where the majority of core functionality" was built, and criterion #1 is literally *"how
thoroughly does the project use Codex."* Therefore:

- **Core code is built in Codex (OpenAI)** — measurement engine, host adapter, diff, GPT-5.6 diagnosis.
- **Fable autonomous agents (Claude Code)** do the *support layer*: specs, scaffolding, test
  fixtures, docs, Devpost copy, and code-review of Codex's output. They make the Codex session
  fast and well-scoped — they do NOT replace it for the core.
- **Antigravity (Gemini)** does the visual/branding/report front-end.

---

## 1. Architecture (de-risked)

**Key decision: use Spotify's `pedalboard` (Python) to load the VST3.** This eliminates the
C++ VST3-SDK headless-host problem we were worried about — `pedalboard` loads VST3/AU directly
in Python on Windows. That's the single biggest risk gone.

```
pluginproof/
  signals.py     # test-signal generators: sine sweep, multitone, HF tone, silence, extremes
  host.py        # load plugin via pedalboard; set params; render input->output. WAV-in/out fallback
  measurements.py# frequency response, THD+N, aliasing score, gain/DC, NaN/denormal check
  baseline.py    # golden snapshot store (JSON + reference spectra), diff engine, thresholds, verdict
  diagnose.py    # GPT-5.6: turn metric diffs into human-readable diagnosis
  report.py      # self-contained HTML report: spectrum overlays, pass/fail badges, diagnosis
  cli.py         # `pluginproof baseline <plugin>` / `pluginproof check <plugin>`
  fixtures/
    biquad.py    # tiny built-in Python DSP "plugin" so the whole pipeline is testable w/o any VST3
```

**Data contract (define Day 1, everyone builds against it):**
```python
Metric  = {name, value, unit}
RunResult = {plugin, samplerate, metrics: list[Metric], spectra: {...}, artifacts: {...}}
DiffResult = {metric, baseline, current, delta, threshold, status: pass|warn|fail}
```

**GPT-5.6 shows up in the running product** at `diagnose.py` — it reads the numeric diffs and
writes *"your oversampling change introduced audible aliasing above 15 kHz."* That's exactly the
"how was GPT-5.6 used" the judges ask for.

---

## 2. Workstreams → autonomous Fable agents

Agents run in parallel against the Day-1 data contract. **Each writes the spec + scaffold + tests;
the meaty core implementation is then done/finished in the Codex session** (see §0).

| Agent | Owns | Deliverable | Verifiable without a real plugin? |
|---|---|---|---|
| **M — Measurement** | `signals.py`, `measurements.py` | 4 measurements + signal generators + unit tests against known references (a pure gain, a known biquad) | ✅ yes — tests use `fixtures/biquad.py` |
| **H — Host adapter** | `host.py`, `fixtures/biquad.py` | pedalboard VST3 loader + WAV fallback + built-in test plugin | ✅ built-in fixture |
| **O — Orchestration** | `baseline.py`, `cli.py` | golden store, diff engine, thresholds, verdict, CLI | ✅ with mocked RunResult |
| **R — Report + diagnosis** | `report.py`, `diagnose.py` | HTML report (spectrum overlays, badges) + GPT-5.6 diagnosis module | ✅ with fixture diff data |
| **C — CI (stretch)** | `.github/workflows/` | GitHub Action: run `check` on PR, comment the diff | ✅ |

**Coordination:** the 4 core measurements to ship (do these well, skip the rest):
**frequency response · THD+N · aliasing · NaN/denormal check.** Depth > breadth.

---

## 3. Antigravity — artistic / visual side (prompts in §6)

Antigravity owns the *look*: brand identity, the report front-end shell, and the demo/Devpost
assets. Aesthetic direction: **"lab instrument meets modern devtool"** — dark UI, oscilloscope /
spectrum-analyzer visual language, one accent (phosphor green or electric cyan), pass=calm /
fail=alarm. The report shell Antigravity builds is the HTML/CSS that `report.py` fills with data.

Assets: (1) logo + wordmark + palette, (2) report dashboard front-end, (3) demo-video title cards
+ YouTube thumbnail, (4) Devpost hero banner.

---

## 4. Timeline (Jul 14 → Jul 21)

| Day | Date | Focus |
|---|---|---|
| 1 | Mon Jul 14 | Lock architecture, write data contract, scaffold repo. Dispatch Fable agents (specs+scaffold+tests). Antigravity starts brand + report shell. |
| 2 | Tue Jul 15 | Core build **in Codex**: measurements + host on top of Fable scaffolds. First end-to-end on `biquad` fixture. |
| 3 | Wed Jul 16 | Baseline/diff + report wired. GPT-5.6 diagnosis in. Green E2E on fixture. |
| — | **Wed Jul 16** | **← request Codex credits well before Fri 12PM PT cutoff** |
| 4 | Thu Jul 17 | Run on a REAL VST3 (your ESP plugin). Fix real-world breakage. Antigravity assets integrated. |
| 5 | Fri Jul 18 | The "inject-a-bug" demo scenario (break oversampling → tool catches it). Polish report visuals. |
| 6 | Sat Jul 19 | Record <3min demo video. README with Codex-usage highlights. Devpost copy. Grab `/feedback` Session ID. |
| 7 | Sun–Mon Jul 20–21 | Buffer + **submit early**. Deadline Mon Jul 21 5PM PT. |

---

## 5. Fable agent dispatch prompts (copy-paste)

> Model: `claude-fable-5`. Run in background, parallel. Each is scoped to not collide.
> Prereq: repo scaffolded + `CONTRACT.md` (the data contract) committed first.

**Agent M — Measurement engine**
```
You are building the measurement engine for PluginProof, a Python audio-plugin regression
harness. Read CONTRACT.md for the RunResult/Metric shapes. Implement signals.py (sine-sweep,
multitone, high-frequency tone, silence, full-scale extremes generators) and measurements.py
with 4 functions: frequency_response, thd_n, aliasing_score, nan_denormal_check — each takes
(input_signal, output_signal, samplerate) and returns Metric(s). Write pytest unit tests that
validate against known references using fixtures/biquad.py (a pure gain must read 0 THD; a known
lowpass must show expected rolloff). Do not touch host.py/report.py. Keep it dependency-light
(numpy/scipy only).
```

**Agent H — Host adapter**
```
Build host.py for PluginProof using Spotify's `pedalboard` to load a VST3/AU plugin, expose its
parameters, and render a given input buffer to an output buffer at a chosen samplerate. Add a
WAV-in/WAV-out fallback adapter for any plugin. Also write fixtures/biquad.py: a tiny pure-Python
biquad "plugin" with the same interface so the whole pipeline runs with zero external plugins.
Conform to CONTRACT.md. Include a smoke test that renders a sine through the biquad fixture.
```

**Agent O — Orchestration + CLI**
```
Build baseline.py and cli.py for PluginProof. baseline.py: save/load golden metrics as JSON,
a diff engine that compares current vs baseline per metric with configurable thresholds, and a
pass/warn/fail verdict (DiffResult per CONTRACT.md). cli.py (use typer): `pluginproof baseline
<plugin>` writes a golden snapshot; `pluginproof check <plugin>` runs and diffs, exits non-zero
on fail. Work against mocked RunResult objects — do not depend on real measurement/host internals,
only their contract. Include tests.
```

**Agent R — Report + GPT-5.6 diagnosis**
```
Build report.py and diagnose.py for PluginProof. report.py generates a single self-contained
HTML file: before/after spectrum overlays (matplotlib PNG embedded as base64), pass/warn/fail
badges per metric, and a diagnosis section. diagnose.py calls the OpenAI API with model
`gpt-5.6` to turn a list of DiffResult into a short human diagnosis ("your change introduced
aliasing above 15 kHz"). Use the visual shell Antigravity produces (report_shell.html) as the
template. Work from fixture DiffResult data. Keep the OpenAI call isolated and mockable.
```

**Agent DOCS (late, Day 6) — README + Devpost copy**
```
Write README.md for PluginProof (setup, sample data, how to run, and a "How Codex & GPT-5.6
accelerated this build" section referencing the real Codex session) plus a Devpost project
description hitting the 4 judging criteria (technical implementation, design, potential impact
for indie plugin devs, quality of idea). Draft a <3min demo-video script for the inject-a-bug
scenario.
```

---

## 6. Antigravity prompts — artistic apartado (copy-paste)

**Prompt 1 — Brand identity**
```
Create a brand identity for "PluginProof", a developer tool that catches audio bugs in music
software plugins. Deliver: (a) a logo mark combining a waveform/spectrum motif with a
checkmark/shield idea, (b) a wordmark, (c) a color palette for a dark UI — one primary accent
(phosphor green OR electric cyan), plus pass/warn/fail semantic colors, (d) a light + dark
variant. Aesthetic: "lab instrument meets modern devtool" — precise, technical, confident, not
cartoonish. Provide SVG for the mark and hex values. Show it on a dark background.
```

**Prompt 2 — Report dashboard front-end (this is real front-end, not just an image)**
```
Build report_shell.html: a single self-contained, responsive HTML/CSS template for an audio
plugin test report. Sections: header with logo + plugin name + overall PASS/FAIL verdict badge;
a grid of metric cards (Frequency Response, THD+N, Aliasing, Stability) each with a status color,
a baseline-vs-current number, and a slot for an embedded spectrum image; a "Diagnosis" panel for
natural-language text. Dark theme, oscilloscope/spectrum-analyzer visual language, one accent
color, generous monospace for numbers. Use clear {{placeholders}} where Python will inject data.
Inline all CSS. Must look like a real instrument readout, calm on pass, alarming on fail.
```

**Prompt 3 — Demo video assets**
```
Create video assets for a <3-minute demo of PluginProof: (a) an animated title card / intro
(logo reveal over a moving spectrum), (b) lower-third name/label graphics, (c) an outro card with
"Built with Codex + GPT-5.6", (d) a YouTube thumbnail (1280x720) that reads "Catch audio bugs
before your users do" with the plugin/waveform motif. Match the dark, phosphor-accent brand.
```

**Prompt 4 — Devpost hero banner**
```
Design a Devpost hero banner (wide, ~1200x630) for PluginProof: the wordmark, tagline "Regression
testing for audio plugins", and a stylized before/after spectrum showing a caught bug (a red
aliasing spike). Dark, technical, brand-consistent. Deliver PNG + the source.
```

---

## 7. Definition of done (so it's "runnable, not PoC")

- [ ] `pluginproof baseline <plugin>` and `pluginproof check <plugin>` both work end-to-end
- [ ] Runs on a REAL VST3 (the ESP plugin), not just the fixture
- [ ] The inject-a-bug demo: break oversampling → tool flags aliasing regression with GPT-5.6 text
- [ ] HTML report renders with real spectra + verdict + diagnosis
- [ ] README documents Codex usage; `/feedback` Session ID captured
- [ ] <3min video uploaded (public YouTube); Devpost submitted before Jul 21 5PM PT
