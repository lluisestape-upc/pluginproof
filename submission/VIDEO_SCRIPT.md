# PluginProof — demo video script (<3 min, natural voice)

> Spoken first-person. Short sentences, easy to say out loud. Total ~2:35.
> Rules require the audio to cover how you used Codex AND GPT-5.6 — covered in the last section.

## 0:00–0:20 — Intro (title card, then your face or the app)

"Hi, I'm Lluís Estapé, and I built PluginProof — an app that catches audio bugs in
music plugins before your users hear them.

I make audio plugins myself. And the truth is, we all test them the same way: by ear.
The problem? Ears don't diff. You refactor something, it compiles, it sounds fine...
and you just shipped aliasing to everyone. There's no pytest for sound. So I built one."

## 0:20–0:50 — Golden baseline (screen: the app)

*Drag the healthy MegaCrusher.vst3 onto the screen. Measurement runs. Press SET GOLDEN.*

"This is PluginProof. I drop in my plugin — this is MegaCrusher, a distortion I actually
ship — and it measures it like a lab instrument. Frequency response, distortion,
aliasing, stability. Through the real VST3 binary, not a simulation.

Then I hit Set Golden. That's the reference — this is how my plugin is supposed to
sound. Frozen."

## 0:50–1:15 — The bug (screen: the code diff)

*Show the 3-line diff in the editor.*

"Now let me break it — the realistic way. Here's a refactor I might do on a Friday:
make the bit crusher branchless. Looks clean, right? Code review would approve this.

Except... simplifying that expression flipped the parameter mapping. The plugin now
crushes everything to four levels — even with the knob at zero. It compiles perfectly.
Let's pretend I never noticed."

*(Off camera: copy the buggy .vst3 over the same file.)*

## 1:15–1:55 — The catch (screen: the app — money shot)

*Press CHECK. Live re-measure. FAIL screen: red LED, three failed metrics, spectrum
overlay, AI diagnosis panel.*

"I rebuild, and I just hit Check. PluginProof measures the plugin again and compares it
to the golden... and there it is. Frequency response off by forty-three dB. Distortion
up nineteen. Aliasing up nineteen. Caught.

And this panel is my favorite part. The numbers go to an AI that answers like a DSP
engineer — what probably broke, and where to look. It ships with GPT-5.6 as the default
engine. But it's bring-your-own-key: you can plug in Anthropic, or — like I'm doing
right now — run it completely local and free with Ollama. And the report always tells
you honestly which engine you're reading."

## 1:55–2:15 — CI gate (screen: GitHub PR with the red X)

"Same check, headless, with exit codes. So it runs in CI: this pull request is blocked
because the sound regressed — nobody had to listen to anything. And because the whole
pipeline is deterministic, an unchanged plugin diffs to exactly zero. No flaky tests."

## 2:15–2:35 — How I built it + outro (outro card)

"I built the core of PluginProof in a Codex session with GPT-5.6 — the desktop app, the
plugin host, the AI layer, the packaging, the CI. Codex even debugged a drag-and-drop
quirk by reading the library's source code. And GPT-5.6 stays in the product, as the
default diagnosis engine.

PluginProof. Catch audio bugs before your users do. Repo and Windows app in the
description. Thanks for watching."

---

### Shot checklist
- [ ] App: drop healthy plugin → SET GOLDEN
- [ ] Editor: the 3-line "branchless" diff (branch `demo-bug`)
- [ ] Swap buggy .vst3 off camera · ⚙ set provider to Ollama BEFORE recording
- [ ] App: CHECK → FAIL report with AI diagnosis
- [ ] GitHub: failed Action on the PR
- [ ] Title + outro cards from assets/video_assets.html
- [ ] YouTube: public · repo link in description · under 3:00
