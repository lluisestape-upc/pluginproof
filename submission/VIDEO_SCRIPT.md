# PluginProof — demo video script (<3 min)

> Screen recording + voiceover. Segments timed to total ~2:40, leaving buffer under 3:00.
> Required by rules: audio must cover **how you used Codex AND GPT-5.6**.

## 0:00–0:15 — Hook (title card from assets/video_assets.html)

**VO:** "Audio plugin developers test releases *by ear*. One refactor can quietly add
aliasing or distortion that no unit test will ever catch — because there's no pytest for
sound. I built one. This is PluginProof."

## 0:15–0:45 — The app + golden baseline (screen: PluginProof.exe)

*Open PluginProof.exe. Drag MegaCrusher.vst3 (healthy) onto the CRT screen. Measurement
runs. Press SET GOLDEN.*

**VO:** "PluginProof measures a plugin like a lab instrument: frequency response, THD,
aliasing, and numerical stability — through the real VST3 binary. One click snapshots a
golden baseline of how my plugin is *supposed* to sound. This is MegaCrusher, a
distortion plugin I ship."

## 0:45–1:10 — The bug (screen: VS Code / Visual Studio diff)

*Show the 3-line diff: bit crusher "branchless" refactor.*

**VO:** "Now the villain. This refactor makes the bit crusher branchless — it compiles,
the diff looks like a cleanup, and code review would wave it through. But simplifying
the expression inverted the depth mapping. The plugin now quantizes everything to four
levels at default settings. Let's pretend I never noticed."

*(Off camera: copy MegaCrusher_buggy.vst3 over the installed plugin.)*

## 1:10–1:50 — The catch (screen: app, the money shot)

*Press CHECK. Re-measure runs live. FAIL screen: red LED, triple FAIL cards, spectrum
overlay showing the divergence, AI diagnosis panel.*

**VO:** "I rebuild, and hit Check. PluginProof re-measures the plugin from disk and
diffs it against the golden: frequency response off by 43 dB, distortion up 19,
aliasing up 19. And this panel is the part I love — the metric diffs go to an AI that
answers like a DSP engineer. It ships with GPT-5.6 as the default engine; it also takes
an Anthropic key, or — like right now — runs fully local and free on Ollama. The report
always says which engine you're reading."

## 1:50–2:15 — CI gate (screen: GitHub PR with failed check)

*Show the GitHub Actions run failing on a PR / the workflow file.*

**VO:** "The same check runs headless with exit codes, so it's a CI gate: this pull
request is blocked because the audio regressed — no human listening required. Golden
baselines are deterministic, so an unchanged plugin diffs to exactly zero. Zero flaky CI."

## 2:15–2:40 — How it was built + outro (outro card)

**VO:** "The core of PluginProof was built in a Codex session with GPT-5.6: the desktop
app, the VST3 host hardening, the multi-provider AI layer, the packaging, and the CI
gate — Codex even root-caused a pywebview drag-and-drop quirk from the library source.
And GPT-5.6 lives on inside the product as the default diagnosis engine. PluginProof:
catch audio bugs before your users do. Repo and Windows exe in the description."

---

### Shot checklist
- [ ] PluginProof.exe launch → drop → SET GOLDEN (healthy plugin installed)
- [ ] Diff of the bug in the editor (branch `demo-bug` of MegaCrusher)
- [ ] Swap in buggy .vst3 (off camera), CHECK → FAIL report (Ollama provider set in ⚙)
- [ ] GitHub Actions failing run screenshot/tab
- [ ] Title card + outro card from assets/video_assets.html
- [ ] YouTube: public, title "PluginProof — regression testing for audio plugins (OpenAI Build Week)", repo link in description
