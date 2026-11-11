# GO / NO-GO ledger — Google DevFest 2026 "The Last Seat" (V2)

Machine-readable source of truth: `evidence/devfest-last-seat/validation_ledger.json`
(now carries both the V1 and V2 physical runs — six real runs total).
Rerun the automated gate any time with `powershell -ExecutionPolicy Bypass -File
scripts\preflight-devfest.ps1` (exit 0 = GO) and refresh the physical evidence with
`python scripts/devfest_validate.py --runs 3`.

## Physical validation — 2026-11-11, reference machine (V2 upgrade, PASS)

Same machine as the V1 delivery; V2 adds the storytelling UI, the Antigravity
SDK live panel, and the honesty fixes (no policy/security changes).

| Item | Value |
| --- | --- |
| OS | Windows 11 (build 10.0.26200) |
| GPU adapter | AMD Radeon(TM) 8060S Graphics (Ryzen AI MAX class, ROCm gfx1151) |
| Runtime | Ollama 0.40.14 · gemma4:26b · Antigravity SDK 0.1.23 · Python 3.13.13 |
| Model digest | `001e5dafc3c77684c23…` (identical across all runs, V1 and V2) |
| ROCm attribution | llama-server.exe PID holding `amdhip64*` DLLs — verified per run |

### V2 independent full runs (fresh baseline reset before each)

| Run | Agent verdict | Tool calls | Agent time | Wall time | Files changed | Tests | GPU backend |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v2-1 | **PASS** | 5 (view, view, pytest, edit, pytest) | 81.4 s | 82.5 s | `checkin.py` only | frozen, byte-identical | ROCm VERIFIED |
| v2-2 | **PASS** | 8 (incl. three edit iterations) | 115.0 s | 116.1 s | `checkin.py` only | frozen, byte-identical | ROCm VERIFIED |
| v2-3 | **PASS** | 5 | 78.7 s | 79.8 s | `checkin.py` only | frozen, byte-identical | ROCm VERIFIED |

**Ledger verdict: 3/3 required V2 runs PASS → GO** (hardware present and
exercised; no BLOCKED entries).

### V1 physical runs (retained history — PR #3 initial delivery, same day)

devfest-1 PASS 102.5 s · devfest-2 PASS 107.0 s · devfest-3 PASS 113.9 s —
plus two on-stage UI runs (`stage-173437` 110.8 s, `stage-174249` 90.0 s).
All ROCm VERIFIED, same digest.

## Stage rehearsals — V2 UI (2026-11-11, real browser at 1920×1080)

Full audience flow (reset → name → register rejected → Give Mission →
six acts → celebration → post-acceptance register → developer view), timed
from the mission click to the verified celebration:

| Rehearsal | Registrant | Time to YOU'RE IN! | Story arc | GPU |
| --- | --- | --- | --- | --- |
| 1 (+recording) | Ada Chen | **97.8 s** | all six acts, tool cards 5 | ROCm VERIFIED |
| 2 | Grace M. | **106.3 s** | all six acts, tool cards 5 | ROCm VERIFIED |
| 3 (+recording) | Linus T. | **119.6 s** | all six acts, tool cards 5 | ROCm VERIFIED |

Replay fallback verified live (2× speed, honestly labeled): REPLAY banner
with run id, `[历史]` GPU prefix, REPLAY watermark over the celebration, and
the page **keeps its RECORDED RUN identity after `replay_done`** — only a new
real run re-badges LIVE. Design preview verified separately (no workspace
writes, `ok:null`).

## GPU memory honesty finding (V2)

Three sources, never conflated (see the docs' GPU section):

| Source | Value | Meaning |
| --- | --- | --- |
| `/api/tags` | 18.73 GB (decimal) | on-disk model artifact |
| `llama-server.exe` working set | 37–41 GB sampled post-run | real runner footprint |
| `/api/ps` size/size_vram | 2.92 GB constant | Ollama-internal accounting — NOT model VRAM usage |

The stage never presents `/api/ps` size as model memory; DEVELOPER VIEW lists
all three with source labels and a caveat.

## Automated stage gate (preflight-devfest.ps1)

| Check | Meaning for the stage |
| --- | --- |
| OS / GPU adapter | machine tier + expected gfx1151 reference |
| Ollama server reachable | live stack present (BLOCKED if not) |
| model `gemma4:26b` available | BLOCKED if missing (`ollama pull gemma4:26b`) |
| model resident in VRAM | WARN if cold — warm up (~45–75 s) before the talk |
| venv + SDK + `src/devfest_*.py` importable | FAIL catches broken installs |
| pristine scenario exactly 1 RED test | FAIL if the defect baseline leaked a fix |
| port 8421 free | FAIL if another dashboard is already running |

## Known risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Cold model load eats 45–75 s on stage | start script warms with keep-alive 5 m; preflight WARNs |
| Model degrades mid-talk / Ollama dies | 90 s prerecorded REPLAY fallback (`-Replay`), loudly labeled, identity-persistent |
| Agent run exceeds 900 s | harness timeout → honest error event; story flips to THE OUTCOME (failed); rerun or use replay |
| Agent takes an extra edit iteration (v2-2: 8 calls, 115 s) | by design — cards are dynamic; budget up to ~2.5 min of narration |
| Workspace left fixed from a rehearsal | chip shows FIXED; ↺ 重置 restores the defect baseline |
| Agent edits tests to force green | acceptance gate FAILs the run (tests_tampered) — never shown as success |
| Premature celebration via mid-run register | V2: probe during a run is `verified:false` ("修复候选已出现"); celebration needs acceptance AND probe PASS |
| Non-ROCm tier machine (CUDA/CPU) | backend chip reports honestly; COMPATIBILITY_MATRIX tiers apply |
| Port collision | `-Port` flag + preflight check |

## Honesty contract

- LIVE = real local agent on this machine, this moment.
- REPLAY = recorded real run, amber banner everywhere; after it finishes the
  page KEEPS its RECORDED RUN identity; never implies fresh hardware or fresh
  test validation.
- DESIGN PREVIEW = synthetic labeled timeline (`ok: null`); never acceptance,
  never writes evidence or workspace.
- Tool calls are only ever reported as **observed**; only the independent
  acceptance chain (frozen tests, allowlist diff, SHA256 re-verify, int exit
  codes) plus the real business probe can mark a run successful — both gates
  in the same run.
- `ROCm VERIFIED` = live `/api/ps` residency + full digest AND the
  llama-server runner holding AMD HIP DLLs. Nothing less is ever claimed.
- Physical hardware unavailable ⇒ ledger records BLOCKED, never fabricated.
