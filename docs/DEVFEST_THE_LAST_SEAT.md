# DevFest 2026 · Guangzhou — "The Last Seat" live demo

A fully **local** AI-agent stage demo: a fictional 40-seat DevFest AI workshop,
39 seats taken, and a real off-by-one defect in the check-in code that wrongly
rejects the 40th registration. A **real local agent** — Google Antigravity SDK
harness driven by `gemma4:26b` on Ollama over AMD ROCm (Radeon 8060S / gfx1151)
— reads the code, runs the failing pytest suite, fixes **only** the source
file, and re-runs the tests. An **independent verification subprocess** plus
SHA256/diff/tamper-evidence gates must all pass before the stage flips to the
Google-green **YOU'RE IN!** celebration and the clearly-fake **Builder Pass**.

Nothing on stage is faked: no canned animations, no pre-injected fix, no
synthetic tool calls, no invented GPU numbers, no fabricated test results.
Replay and design-preview modes exist — visibly and unambiguously labeled.

- Issue: [#2 — DevFest 2026 live demo: The Last Seat](https://github.com/AIwork4me/Antigravity-Gemma-ROCm/issues/2)
- Stage UI: `http://127.0.0.1:8421` (loopback only, 1920×1080 projection layout)
- 中文文档: [docs/DEVFEST_THE_LAST_SEAT.zh-CN.md](DEVFEST_THE_LAST_SEAT.zh-CN.md)
- GO/NO-GO ledger: [docs/DEVFEST_GO_NO_GO_LEDGER.md](DEVFEST_GO_NO_GO_LEDGER.md)

## The 5-second story

```
LEFT (the app)                    RIGHT (the truth)
┌────────────────────────┐       ┌──────────────────────────────┐
│ 39 / 40 seats          │       │ 👁 view_file checkin.py       │
│ seat 40: ✗ REJECTED    │  ←──  │ ⚙️ pytest → 1 failed (RED)    │
│ "workshop at full      │       │ ✏️ edit_file checkin.py       │
│  capacity (39/40)"     │       │ ⚙️ pytest → 9 passed (GREEN)  │
│                        │       │ 🛡 ALLOW python -m pytest …    │
│ [Agent fixes it…]      │       │ ✓ independent verify: PASS    │
│ 40 / 40 — YOU'RE IN! 🎉│       │ diff: `>= capacity-1` → `>= capacity` │
└────────────────────────┘       └──────────────────────────────┘
```

## Architecture (all on one Windows machine)

```
Browser (1920×1080 projection)
   │  http://127.0.0.1:8421  (loopback bind; Host/Origin/Sec-Fetch-Site gate
   │                          on every state-changing endpoint)
   ▼
src/devfest_webui.py  — stage dashboard, SSE event stream, recorder/replay,
   │                   design-preview mode, probe endpoint, evidence viewer
   ├─► src/devfest_probe.py   — admission probe: runs the REAL checkin.py from
   │        devfest_workspace in an isolated `python -I` subprocess; the UI's
   │        before/after business state is ONLY ever derived from this probe
   ├─► src/devfest_agent.py   — the real agent task (same audited pipeline as
   │        src/demo_agent.py): mission → SDK Agent → tool-call stream →
   │        policy audit → SHA256 before/after → independent pytest → diff
   │        → tamper-evident acceptance (frozen tests byte-identical, source
   │        allowlist = checkin.py, disk re-verification, int exit codes)
   ▼
Ollama (http://127.0.0.1:11434) → gemma4:26b → llama-server runner on
AMD ROCm (gfx1151). "ROCm VERIFIED" on stage requires BOTH live model
residency with full digest (GET /api/ps) AND llama-server.exe holding AMD
HIP DLLs (tasklist /m) — never a possibly-stale server.log line.
```

**Security boundary (unchanged from the audited baseline):** the agent's file
tools are allow-listed to `devfest_workspace/` (junction-safe, reparse-aware
path normalization); `run_command` is a structured fail-closed allowlist —
only `python -m pytest …` / `pytest …` with an in-workspace `Cwd`. The
Windows policy handler is a POLICY boundary, **not an OS sandbox**. The
workspace reset reuses the junction-safe pattern from `demo_agent.py`. No
`allow_all()`, no arbitrary shell, no WAN binding, no cloud fallback.

**Honesty rules baked into the code:**

| Rule | Enforcement |
| --- | --- |
| No fake agent runs | run button only starts the real pipeline; every UI state change comes from real events or the real probe |
| No pre-injected fix | pristine scenario ships with the defect; tests freeze it (a green pristine fails CI) |
| No test tampering counts | `files_changed` ∩ `tests/` → acceptance FAIL; frozen tests must be byte-identical |
| Only whitelisted source may change | `MUTABLE_ALLOWLIST = ("checkin.py",)`; anything else → FAIL |
| No forged exit codes | string `"0"` exit codes raise; disk SHA256 re-verified after the map diff |
| No fake GPU claims | `ROCm VERIFIED` only with live `/api/ps` residency + runner DLL evidence |
| Replay ≠ live | amber REPLAY banner + chip on every replay; replay never implies fresh hardware/validation |
| Preview ≠ acceptance | DESIGN PREVIEW emits `ok: null`, never writes evidence/workspace |

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
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -Replay recording.json   # REPLAY (labeled)
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -DryRun                  # DESIGN PREVIEW (no Ollama needed)
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -SkipWarmup -Port 8422   # custom port, no warmup

# Direct, no helper script
.venv\Scripts\python -I src\devfest_webui.py --port 8421
```

On stage you get: preflight gate → model warm-up (keep-alive 5 m) → browser
opens `http://127.0.0.1:8421`. The left panel probes the real check-in code
on load (the 40th seat is rejected in red), the right panel waits. Press
**▶ 运行** (Run) and watch the real trace: read → pytest RED → edit → pytest
GREEN → independent verify → diff → GPU evidence → **YOU'RE IN!**.

## The 2–3 minute stage script

1. **(0:00)** "This is a real 40-seat workshop app. 39 developers are in.
   Watch the last registration." — press **报名第 40 席** (Register). Red
   rejection. The bug is live, on this machine, right now.
2. **(0:20)** "The tests document the truth and they're failing. Let a fully
   local Gemma 4 agent fix it — no cloud, no hints." — press **▶ 运行**.
3. **(0:30–2:00)** Narrate the trace as it streams: the agent lists files,
   reads `checkin.py` and the frozen tests, runs pytest (RED), diagnoses the
   boundary, edits exactly one line, re-runs (GREEN). Point at the policy
   ALLOW/DENY lines: "it can only run pytest, only inside this folder".
4. **(2:00)** Independent verification turns green, the diff shows
   `>= capacity - 1` → `>= capacity`, GPU chip shows `ROCm VERIFIED`.
   The left panel flips: **40 / 40 — YOU'RE IN!**, four-color confetti,
   Builder Pass (clearly fake).
5. **(2:20)** Press **报名第 40 席** again: the same probe now admits the
   developer into seat 40 — from the real code, not a hardcoded flip.
6. **(2:30)** "Everything you saw is reproducible" — open **🗂 验证证据**
   (evidence viewer): tool trace, pytest before/after, SHA256, GPU
   attribution, timings.

**90-second emergency fallback:** pre-record one real run
(`-Record recording.json` before the talk) and start with
`-Replay recording.json`. The UI loudly labels it REPLAY; narrate it as
"here's this morning's real run on this same machine".

## Verification & evidence

- Physical runs land in `artifacts/devfest-agent-execution/run-*/` (local,
  gitignored) and a **curated, redacted copy** in
  `evidence/devfest-last-seat/run-*/` (committed): mission, config, full
  tool-call stream, policy audit, pytest before/after, diff.patch, SHA256
  maps, agent reply, `gpu_evidence.json` (digest + residency + runner DLL
  PIDs + backend), `env.json`, and the enriched `summary.json`.
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
| First run slow (~60–75 s before tokens) | model cold-load; the start script warms it — or run with `-SkipWarmup` off |
| GPU chip shows `not resident (cold)` | normal before the first inference; re-check after a run |
| GPU chip shows `resident (backend unverified)` | model loaded but no ROCm DLL evidence from the runner — check `tasklist /m amdhip64*`; a non-ROCm runner tier is being used |
| `缺陷基线 已修复/漂移` chip | a previous run left the workspace fixed — press 演示重置 (Reset) |
| Reset refuses: workspace is a junction | deliberate guard; remove the junction manually and reset again |
| `tests_tampered` on a run | the agent edited the frozen tests — by design that run FAILS acceptance; reset and re-run |
| Browser tab shows stale events | events stream via SSE; reload the page (state is re-derived from the real code) |
| Pytest not found inside agent runs | the venv must own the interpreter running `devfest_webui.py`; run via `scripts\start-devfest-demo.ps1` |

## Scope & limits

- The Builder Pass is a **fictional commemorative badge**, not an admission
  ticket; attendee names are fictional; the optional registrant name is
  bounded (24 chars), control-character-free, HTML-escaped via textContent,
  never persisted, and never sent to the model.
- The stage UI uses original CSS/SVG geometry in the DevFest 2026 palette;
  no organizer template files or logos are redistributed. GDG Shanghai
  branding from the organizer template is intentionally NOT used — the
  header says `DevFest 2026 · Guangzhou`.
- Dry-run/design preview never counts as acceptance; replay is labeled on
  every surface; if physical hardware is unavailable the validation ledger
  records BLOCKED rather than inventing results.
