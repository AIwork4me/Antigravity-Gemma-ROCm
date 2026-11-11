# Google DevFest 2026 — "THE LAST SEAT" live demo (V2)

A fully **local** AI-agent stage demo, built for the talk
《从 Gemma 4 到本地个人智能体：Google Antigravity × AMD ROCm 实战》.

The story the audience sees in under 3 minutes:

1. A fictional DevFest workshop has **40 seats; 39 are taken**.
2. The last developer tries to register — the system **wrongly rejects** them.
3. We hand the problem to a local agent powered by the **Google Antigravity SDK**.
4. **Gemma 4 26B**, inferring on the local AMD GPU, decides which tools to call.
5. The Antigravity SDK **really executes** file reads, tests, and the code edit.
6. An **independent verifier** re-runs the frozen tests and audits every change.
7. The last seat opens — the board flips to **Google green, 40/40**.
8. **YOU'RE IN! / The Last Seat Is Saved!** with Google four-color confetti.

The three-layer message:

- **Gemma 4 = The Brain** (local model, local decisions)
- **Antigravity SDK = The Agent Harness** (real tool execution + policy)
- **AMD ROCm = The Local Compute** (Radeon 8060S / gfx1151, no cloud)

Nothing on stage is faked: no canned animations, no pre-injected fix, no
synthetic tool calls, no invented GPU numbers, no fabricated test results.
Replay and design-preview modes exist — visibly and unambiguously labeled.

- Issue: [#2 — DevFest 2026 live demo: The Last Seat](https://github.com/AIwork4me/Antigravity-Gemma-ROCm/issues/2)
- Stage UI: `http://127.0.0.1:8421` (loopback only, 1920×1080 projection layout)
- 中文文档: [docs/DEVFEST_THE_LAST_SEAT.zh-CN.md](DEVFEST_THE_LAST_SEAT.zh-CN.md)
- GO/NO-GO ledger: [docs/DEVFEST_GO_NO_GO_LEDGER.md](DEVFEST_GO_NO_GO_LEDGER.md)

## The 5-second story (V2 layout)

```
LEFT — the story (STAGE VIEW)          RIGHT — Google Antigravity SDK
┌──────────────────────────────┐       ┌────────────────────────────────────┐
│ Google DevFest 2026          │       │ Gemma4→SDK→Tools→App pipeline      │
│ THE LAST SEAT                │       │ AMD ROCm · Local GPU Inference     │
│ [PROBLEM→…→CELEBRATION acts] │       │ ┌────────────────────────────────┐ │
│ 39 / 40 · seat 40 ✗ REJECTED │       │ │ Antigravity SDK → view_file    │ │
│ "workshop at full capacity"  │       │ │ Antigravity SDK → run_command  │ │
│ [Agent fixes it…]            │       │ │ Antigravity SDK → edit_file    │ │
│ 40/40 — YOU'RE IN! 🎉        │       │ │ Antigravity SDK → run_command  │ │
│ Builder Pass (clearly fake)  │       │ └────────────────────────────────┘ │
└──────────────────────────────┘       │ ✨ Behind the Magic (real code)    │
                                       └────────────────────────────────────┘
```

Switch to **DEVELOPER VIEW** any time for the receipts: SDK lifecycle events,
real `pytest` results, the whitelisted diff, SHA256, the policy audit trail,
Ollama model/GPU evidence (honest per-source labels), and timings.

## Branding & visual design

- The header is **Google DevFest 2026** with the classic Google letter
  colors (G blue `#4285F4`, o red `#EA4335`, o yellow `#FBBC04`, g blue,
  l green `#34A853`, e red); `DevFest 2026` in high-contrast off-white.
- Demo title: **THE LAST SEAT**; tagline: *Can a Local AI Agent Save the
  Final Seat?*; scenario line: *39 Registered · 1 Seat Left ·
  Registration Denied.*
- Palette and geometry follow the DevFest dark template family
  (`#1E1E1E` background, `#F0F0F0` off-white, Google four colors, simple
  geometric decorations) with original CSS/SVG only — **no organizer
  template files, fonts, or logos are redistributed**, and no city branding
  is claimed (the old `· Guangzhou` suffix was removed in V2).
- Fonts are system stacks (Google Sans when present); no font files ship
  in the repo.

## Architecture (all on one Windows machine)

```
Browser (1920×1080 projection)
   │  http://127.0.0.1:8421  (loopback bind; Host/Origin/Sec-Fetch-Site gate
   │                          on every state-changing endpoint)
   ▼
src/devfest_webui.py  — V2 stage dashboard: STAGE/DEVELOPER views, six-act
   │                   story state machine, SSE stream, recorder/replay,
   │                   design-preview mode, probe endpoint, evidence viewer
   ├─► src/devfest_probe.py   — admission probe: runs the REAL checkin.py from
   │        devfest_workspace in an isolated `python -I` subprocess; the UI's
   │        business state is ONLY ever derived from this probe
   ├─► src/devfest_agent.py   — the real agent task: mission → SDK Agent →
   │        tool-call stream → policy audit → SHA256 before/after →
   │        independent pytest → diff → tamper-evident acceptance; emits the
   │        SDK lifecycle events (agent_config_ready / agent_session_started /
   │        mission_submitted / tool observed / agent_response_completed /
   │        independent_verification_started|completed)
   ▼
Ollama (http://127.0.0.1:11434) → gemma4:26b → llama-server runner on
AMD ROCm (gfx1151). "ROCm VERIFIED" on stage requires BOTH live model
residency with full digest (GET /api/ps) AND llama-server.exe holding AMD
HIP DLLs (tasklist /m) — never a possibly-stale server.log line.
```

**Model / Harness / Compute:**

- **Model decides** — Gemma 4 26B picks the tools; nobody scripts the steps.
- **Harness executes** — the Antigravity SDK runs the tools under a
  fail-closed policy; the UI shows *Model decides → Harness executes →
  Tools return evidence → Agent continues*.
- **Compute stays local** — AMD ROCm on Radeon 8060S; `AMD ROCm · Local
  GPU Inference` is printed next to the pipeline.

## The six acts and the real events behind them

| Story stage | Audience sees | Backed by (real event only) |
| --- | --- | --- |
| THE PROBLEM | The last seat is wrongly rejected! | real business probe (`seat` event, `admitted:false`) |
| THE MISSION | Antigravity accepts the mission | `sdk: agent_session_started` + `mission_submitted` |
| THE INVESTIGATION | Agent investigates the registration code | observed non-edit SDK tool calls (`view_file`, `run_command`, …) |
| THE FIX | Agent is fixing the admission logic | observed `edit_file`/`create_file`/`replace_file_content` call |
| THE CHECK | Confirming the fix actually works | `sdk: independent_verification_started` → independent pytest |
| THE CELEBRATION | The Last Seat Is Saved! | acceptance `ok:true` **AND** final business probe PASS (`seat` `verified:true`) |

The stage machine is monotonic (acts never regress), terminal on failure,
and cannot be advanced by timers: `setTimeout` drives nothing. A tool call
being **observed** never means it succeeded — only the acceptance chain
(frozen tests byte-identical, allowlist diff, disk SHA256 re-verify, int
exit codes) plus the real business probe can unlock the celebration, and
both must happen **in the same run**. Failure/cancel/timeout/reset clears
any success state.

### Success conditions (all must hold)

1. Agent completed the task (`done`).
2. Independent pytest subprocess: all green after the fix.
3. Frozen tests byte-identical before/after.
4. No files changed outside the whitelist (`checkin.py` only).
5. SHA256 map re-verified against the on-disk files.
6. Real business probe (isolated `python -I`) admits the 40th developer.
7. The events belong to the current LIVE run (run-id scoped).

## Stage View / Developer View

- **STAGE VIEW (default)** — for the audience: six-act strip, big story
  narration, seat board, register button, and the right-side
  **Google Antigravity SDK — Live Agent Execution** panel (pipeline,
  dynamic tool cards, Behind-the-Magic snippet). Fits 1920×1080 with no
  page scroll; honors `prefers-reduced-motion`; keyboard operable.
- **DEVELOPER VIEW** — a persistent header (SDK title, pipeline, live
  agent-status line, mission button) plus a **tabbed technical
  workspace**: `SDK Timeline` (default) · `Code Diff` · `Model Output` ·
  `Evidence` (pytest before/after, run summary + SHA256, GPU evidence).
  Exactly one tab is visible at a time; each tab panel is its own bounded
  scroller, so long logs/diffs/tokens never squeeze or overlap each other.
- Switching views or tabs is display-only: it never interrupts the agent
  run, re-opens the SSE stream, or loses history (a single EventSource
  keeps flowing; scroll position is preserved, and follow-the-tail
  resumes only after you scroll back to the bottom). Tabs are keyboard
  operable (←/→/Home/End) with proper `aria-selected`/`tabindex`.
- Success in Developer View shows the full YOU'RE IN! card and Builder
  Pass; on small viewports the panel scrolls instead of clipping (the
  badge is always reachable), verified down to 1280×720.

### Developer View layout fix (V3) — root causes & design

Two bugs were reported at 1704×864 (overlap in the right panel; Builder
Pass clipped) and diagnosed in a real browser before fixing:

- **Right-panel overlap**: the SDK feed's `flex:1` sat inside a
  non-flex `.devsec` parent, so it grew unbounded (measured 5715px under
  a stress replay) while sibling sections were flex-shrunk below their
  content, overflowing over each other; the panel's `overflow:hidden`
  then clipped the rest. Fix: the workspace is now
  `display:grid; grid-template-rows:auto minmax(0,1fr)`; each tab panel
  is `height:100%; min-height:0; overflow:auto` — no element can escape
  its box, and hidden tabs have zero layout footprint.
- **Builder Pass clipped**: `#win` (which uses `overflow:hidden` for the
  confetti) was itself flex-shrunk inside the left panel, cropping the
  badge internally where panel scrolling could not reach it. Fix:
  `#win{flex:none}` keeps its intrinsic height; `.panel{overflow:auto}`
  turns any small-viewport overflow into a reachable scroll.
  Seat-grid columns became `minmax(0,38px)` so they shrink cleanly at
  1366×768 instead of pushing the panel 37px wide.
- **Replay pages**: loading a replay/preview page no longer runs the
  startup business probe (it used to overwrite the replayed
  celebration), and the register button stays disabled outside LIVE.

**Automated visual regression** (`tests/test_devfest_devview_layout.py`,
Playwright over a local Chromium-family channel): five viewports
(1920×1080, 1704×864, 1366×768, 1280×720, 1536×864 = 1080p at 125% zoom)
× four tabs with `getBoundingClientRect` assertions (panels bounded, no
horizontal overflow, footer clear, badge visible/scrollable, hidden tabs
inert), tab state preservation + single-EventSource check, keyboard
navigation, scroll-follow semantics, replay-identity reload survival,
stage-view regression, agent error state, and DESIGN PREVIEW. Fixtures
are deterministic REPLAY recordings (100 tool events, 100 policy lines,
long args, a 300-line diff, 200 mixed zh/en tokens) — test-only, never
LIVE data. CI without a browser skips this module; the reference Windows
machine runs it for real (8/8).

## Antigravity SDK visibility (why V2 exists)

The right panel is titled **Google Antigravity SDK — Live Agent Execution**
and always shows `Gemma 4 26B → Antigravity SDK → Real Tools → Application`.
Every real tool call becomes a stage-readable card:

```
Antigravity SDK → view_file     正在阅读报名代码   observed · #2
Antigravity SDK → run_command   正在运行 Python 测试  observed · #3
Antigravity SDK → edit_file     正在修改报名逻辑   observed · #4
```

Cards are generated from the real `response.tool_calls` stream — their
number and order can differ run to run; nothing is a fixed pipeline.
**Behind the Magic** expands to the repo's actual core calls:

```python
from google.antigravity import Agent, LocalOpenAIAgentConfig
from google.antigravity.hooks import policy

config = LocalOpenAIAgentConfig(
    model="gemma4:26b",                    # local Ollama (OpenAI-compatible)
    base_url="http://127.0.0.1:11434/v1",
    workspaces=[str(DEVFEST_WORKSPACE)],   # the only writable workspace
    policies=[policy.allow(t, when=in_workspace_path) for t in FILE_TOOLS]
    + [policy.ask_user("run_command", handler=pytest_only)],
).lightweight()

async with Agent(config) as agent:
    response = await agent.chat(MISSION)   # Gemma 4 decides which tools
    async for call in response.tool_calls: ...
```

(The stage shows a faithful condensation; the executable truth is
`src/config.py` + `src/devfest_agent.py`.)

## Install (Windows PowerShell, from a fresh clone)

```powershell
git clone https://github.com/AIwork4me/Antigravity-Gemma-ROCm.git
cd Antigravity-Gemma-ROCm
git fetch origin feat/devfest-2026-last-seat-demo   # while the PR is open
git checkout feat/devfest-2026-last-seat-demo

# 1) Python venv (3.12 or 3.13). Skip if .venv already exists.
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt

# 2) Ollama with the model (≈18 GB, one-time; skip if already present)
ollama --version            # must print a version; install from ollama.com if missing
ollama pull gemma4:26b      # do NOT reinstall Ollama if it's already running

# 3) Automated stage preflight (PASS/WARN/FAIL/BLOCKED per item; exit 0 = GO)
powershell -ExecutionPolicy Bypass -File scripts\preflight-devfest.ps1
```

Hardware expectations: reference machine is a Ryzen AI MAX+ 395 with Radeon
8060S iGPU (ROCm gfx1151). Any AMD Radeon with a ROCm-capable Ollama runner
works; NVIDIA GPUs use the CUDA tier; CPU-only runs are slow but functional
(see [COMPATIBILITY_MATRIX.md](COMPATIBILITY_MATRIX.md)).

## Run

```powershell
# LIVE — the real on-stage run (warms the model first, then opens the browser)
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1

# Variants
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -Record recording.json   # LIVE + record for replay/GIF
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -Replay recording.json   # REPLAY (labeled, stays replay)
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -DryRun                  # DESIGN PREVIEW (no Ollama needed)
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -SkipWarmup -Port 8422   # custom port, no warmup

# Direct, no helper script
.venv\Scripts\python -I src\devfest_webui.py --port 8421
```

On stage you get: preflight gate → model warm-up (keep-alive 5 m) → browser
opens `http://127.0.0.1:8421` in STAGE VIEW. The page probes the real
check-in code on load (THE PROBLEM is live), type a fictional developer
name, press **报名第 40 席** (rejected in red), then press
**▶ Give Mission to Antigravity** and watch the six acts advance from real
events: read → pytest RED → edit → pytest GREEN → independent verify →
**YOU'RE IN!**.

## The 3-minute stage script

1. **(0:00)** "This is a real 40-seat workshop app. 39 developers are in.
   Watch the last registration." — type a name, press **报名第 40 席**.
   Red rejection. The bug is live, on this machine, right now.
2. **(0:20)** "Let a fully local agent fix it — Gemma 4 decides, the
   Antigravity SDK executes, AMD ROCm computes. No cloud, no hints." —
   press **▶ Give Mission to Antigravity**.
3. **(0:30–2:00)** Narrate the acts as they advance: the agent reads the
   code and the frozen tests, runs pytest (RED), edits one line, re-runs
   (GREEN). Point at the pipeline and tool cards: "the model decides,
   the harness executes, tools return evidence". Point at a policy
   ALLOW/DENY line in Developer View: "it can only run pytest, only
   inside this folder".
4. **(2:00)** THE CHECK turns green, the diff shows
   `>= capacity - 1` → `>= capacity`, GPU chip shows `ROCm VERIFIED`.
   The left panel flips: **40 / 40 — YOU'RE IN!**, four-color confetti,
   Builder Pass (clearly fake).
5. **(2:20)** Press **报名第 40 席** again: the same probe now admits the
   developer into seat 40 — from the real code, not a hardcoded flip.
6. **(2:30)** "Everything you saw is reproducible" — open **🗂 验证证据**
   (evidence viewer) or switch to **DEVELOPER VIEW**: tool trace, pytest
   before/after, SHA256, GPU attribution, timings.

**90-second emergency fallback:** pre-record one real run
(`-Record recording.json` before the talk) and start with
`-Replay recording.json`. The UI loudly labels it REPLAY from banner to
badge watermark; narrate it as "here's this morning's real run on this
same machine".

## Replay semantics (V2)

- A replay **keeps its RECORDED RUN / REPLAY identity forever** — after
  `replay_done` the banner changes to "replay finished" but the page never
  re-badges itself as LIVE; only starting a **new real run** brings LIVE
  back.
- Historical success screens keep a diagonal **REPLAY watermark**.
- During replay, the run/reset endpoints are refused (409) — a recording
  can never trigger a real agent run or a workspace write.
- GPU chips render replayed GPU events with a `[历史]` prefix.
- Refreshing or reconnecting preserves the correct identity (the mode is
  server-side state, re-read via `/api/status`).

## GPU evidence & memory honesty (V2 finding)

Per-run GPU attribution requires live evidence (see architecture above).
During V2 validation on the reference machine we measured **three
different "sizes"** that must never be conflated:

| Source | Value (reference machine) | Meaning |
| --- | --- | --- |
| `/api/tags` model entry | 18.73 GB | the on-disk model artifact |
| `llama-server.exe` working set (`tasklist`) | ≈29–31 GB | the real runner footprint (weights + KV + compute) |
| `/api/ps` `size`/`size_vram` | 2.92 GB, constant | Ollama-internal accounting — NOT the model footprint |

Consequently the stage never presents `/api/ps` size as "model VRAM usage";
DEVELOPER VIEW lists all three with their sources, and `gpu_evidence.json`
records them per run. `100% GPU` residency (`size == size_vram`) is still
meaningful — it is what `ollama ps` prints as `100% GPU` — but it is
Ollama's own claim about placement, reported as such.

## Stage screenshots & recording (real runs, 2026-11-11)

All captured from real LIVE/REPLAY runs on the reference machine at
1920×1080 (STAGE VIEW unless noted):

| File | Moment |
| --- | --- |
| `docs/assets/v2-01-problem.png` | THE PROBLEM — registration rejected (real probe) |
| `docs/assets/v2-03-investigating.png` | THE INVESTIGATION — real `view_file`/`run_command` cards streaming |
| `docs/assets/v2-02-fixing.png` | THE FIX — `edit_file` observed (LIVE) |
| `docs/assets/v2-04-success.png` | THE CELEBRATION — 40/40, YOU'RE IN!, Builder Pass, `ROCm VERIFIED` |
| `docs/assets/v2-05-developer.png` | DEVELOPER VIEW — pytest/SHA256/GPU evidence |
| `docs/assets/v2-06-replay.png` | REPLAY — finished replay keeps its RECORDED RUN identity + watermark |
| `docs/assets/devfest-last-seat-v2.gif` | story-arc GIF rendered from the real event recording |
| `docs/assets/last-seat-recording.json` | the real recorded event stream (replay fallback) |
| `docs/assets/developer-before-fix.png` | (V3) right-panel overlap before the fix — stress replay, 1920×1080 |
| `docs/assets/developer-before-fix-1704-badge-clip.png` | (V3) Builder Pass clipped at 1704×864 before the fix |
| `docs/assets/developer-sdk-timeline-fixed.png` | (V3) SDK Timeline tab after the fix — real run |
| `docs/assets/developer-code-diff-fixed.png` | (V3) Code Diff tab — real run |
| `docs/assets/developer-model-output-fixed.png` | (V3) Model Output tab — real run |
| `docs/assets/developer-evidence-fixed.png` | (V3) Evidence tab (pytest/SHA256/GPU) — real run |
| `docs/assets/developer-builder-pass-fixed.png` | (V3) full Builder Pass at 1704×864 after the fix |
| `docs/assets/stage-view-regression.png` | (V3) stage view unchanged after the layout fix |

## Verification & evidence

- Physical runs land in `artifacts/devfest-agent-execution/run-*/` (local,
  gitignored) and a **curated, redacted copy** in
  `evidence/devfest-last-seat/run-*/` (committed): mission, config, full
  tool-call stream, policy audit, pytest before/after, diff.patch, SHA256
  maps, agent reply, `gpu_evidence.json` (digest + residency + runner DLL
  PIDs + backend + memory sources), `env.json`, and the enriched
  `summary.json`.
- `evidence/devfest-last-seat/validation_ledger.json` aggregates all runs
  with per-run acceptance gates — the machine-readable GO/NO-GO source.
- Re-verify any run yourself:
  `python -I src\devfest_probe.py --workspace devfest_workspace --scenario last`
  (what the real code says today), and
  `cd devfest_workspace && ..\.venv\Scripts\python -m pytest tests\ -q`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `port 8421 already in use` | `Get-NetTCPConnection -LocalPort 8421` then stop the owner, or `-Port 8422` |
| Preflight `Ollama unreachable` | start `ollama serve` (headless) or launch the Ollama app; do NOT reinstall |
| `model missing` | `ollama pull gemma4:26b` (≈18 GB; if pull fails over IPv6 use IPv4 explicitly) |
| First run slow (~45–75 s before tokens) | model cold-load; the start script warms it — or run with `-SkipWarmup` off |
| GPU chip shows `not resident (cold)` | normal before the first inference; re-check after a run |
| GPU chip shows `resident (backend unverified)` | model loaded but no ROCm DLL evidence from the runner — check `tasklist /m amdhip64*`; a non-ROCm runner tier is being used |
| Chip shows `场景 BLOCKED · 异常` | workspace drifted into an unexpected state — press ↺ 重置 |
| Reset refuses: workspace is a junction | deliberate guard; remove the junction manually and reset again |
| `tests_tampered` on a run | the agent edited the frozen tests — by design that run FAILS acceptance; reset and re-run |
| Browser tab shows stale events | events stream via SSE; reload the page (state is re-derived from the real code) |
| Pytest not found inside agent runs | the venv must own the interpreter running `devfest_webui.py`; run via `scripts\start-devfest-demo.ps1` |

## Scope & limits

- The Builder Pass is a **fictional commemorative badge**, not an admission
  ticket; attendee names are fictional; the optional registrant name is
  bounded (24 chars), control-character-free, rendered via textContent,
  never persisted, and never sent to the model.
- The stage UI uses original CSS/SVG geometry in the Google/DevFest palette;
  no organizer template files, logos, or fonts are redistributed. The
  header says **Google DevFest 2026** (text styling in Google's brand
  colors; not an official logo asset).
- Dry-run/design preview never counts as acceptance; replay is labeled on
  every surface and never re-badges as LIVE; if physical hardware is
  unavailable the validation ledger records BLOCKED rather than inventing
  results.

## What changed in V2 (vs V1)

| Area | V1 | V2 |
| --- | --- | --- |
| Branding | "The Last Seat · DevFest 2026 · Guangzhou" | **Google DevFest 2026** (colored letters) + THE LAST SEAT + tagline |
| Layout | one combined view | STAGE VIEW (story) + DEVELOPER VIEW (receipts), 1080p no-scroll |
| Narrative | raw event feed | six-act story state machine driven only by real events |
| SDK visibility | small footer mention | dedicated **Antigravity SDK — Live Agent Execution** panel: pipeline, tool cards, lifecycle events, Behind the Magic |
| Celebration | any admitted probe could trigger it (bug) | requires acceptance PASS **and** business probe PASS **and** same-run verified seat |
| Replay end | re-badged the page as LIVE (bug) | keeps RECORDED RUN identity; watermark persists; refresh-safe |
| Workspace chip | "已修复/漂移" red | READY / FIXING / FIXED / BLOCKED |
| GPU memory | `size==size_vram` shown as-is | three sources labeled; stage shows backend+digest only |
