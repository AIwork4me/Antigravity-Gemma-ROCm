# GO / NO-GO ledger — DevFest 2026 "The Last Seat"

Machine-readable source of truth: `evidence/devfest-last-seat/validation_ledger.json`.
Rerun the automated gate any time with `powershell -ExecutionPolicy Bypass -File
scripts\preflight-devfest.ps1` (exit 0 = GO) and refresh the physical evidence with
`python scripts\devfest_validate.py --runs 3`.

## Physical validation — 2026-11-11, reference machine (PASS)

| Item | Value |
| --- | --- |
| OS | Windows 11 (build 10.0.26200) |
| GPU adapter | AMD Radeon(TM) 8060S Graphics (Ryzen AI MAX class, ROCm gfx1151) |
| Runtime | Ollama 0.40.14 · gemma4:26b · Antigravity SDK 0.1.23 · Python 3.13.13 |
| Model digest | `001e5dafc3c77684c23…` (identical across all runs) |
| GPU residency | 100% VRAM (size_vram == size == 2,919,298,496 B) on every run |
| ROCm attribution | llama-server.exe PID holding `amdhip64*` DLLs — verified per run |

### Independent full runs (fresh baseline reset before each)

| Run | Agent verdict | Tool calls | Agent time | Wall time | Files changed | Tests | GPU backend |
| --- | --- | --- | --- | --- | --- | --- | --- |
| devfest-1 | **PASS** | 5 (view, view, pytest, edit, pytest) | 102.5 s | 103.5 s | `checkin.py` only | frozen, byte-identical | ROCm VERIFIED |
| devfest-2 | **PASS** | 5 | 107.0 s | 108.0 s | `checkin.py` only | frozen, byte-identical | ROCm VERIFIED |
| devfest-3 | **PASS** | 6 | 113.9 s | 115.0 s | `checkin.py` only | frozen, byte-identical | ROCm VERIFIED |

Additional on-stage runs through the web UI (same gates): `stage-173437` PASS
110.8 s, `stage-174249` PASS 90.0 s. Every run applied the same minimal fix
(`registered >= capacity - 1` → `registered >= capacity`), independently
re-verified by an out-of-process pytest (1 failed → 9 passed).

**Ledger verdict: 3/3 required runs PASS → GO** (hardware present and exercised;
no BLOCKED entries).

## Automated stage gate (preflight-devfest.ps1)

| Check | Meaning for the stage |
| --- | --- |
| OS / GPU adapter | machine tier + expected gfx1151 reference |
| Ollama server reachable | live stack present (BLOCKED if not) |
| model `gemma4:26b` available | BLOCKED if missing (`ollama pull gemma4:26b`) |
| model resident in VRAM | WARN if cold — warm up (~60–75 s) before the talk |
| venv + SDK + `src/devfest_*.py` importable | FAIL catches broken installs |
| pristine scenario exactly 1 RED test | FAIL if the defect baseline leaked a fix |
| port 8421 free | FAIL if another dashboard is already running |

## Known risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Cold model load eats 60–75 s on stage | start script warms with keep-alive 5 m; preflight WARNs |
| Model degrades mid-talk / Ollama dies | 90 s prerecorded REPLAY fallback (`-Replay`), loudly labeled |
| Agent run exceeds 900 s | harness timeout → honest error event; rerun or use replay |
| Workspace left fixed from a rehearsal | status chip 缺陷基线 shows drift; 演示重置 button restores |
| Agent edits tests to force green | acceptance gate FAILs the run (tests_tampered) — never shown as success |
| Non-ROCm tier machine (CUDA/CPU) | backend chip reports honestly; COMATIBILITY_MATRIX tiers apply |
| Port collision | `-Port` flag + preflight check |

## Honesty contract

- LIVE = real local agent on this machine, this moment.
- REPLAY = recorded real run, amber banner everywhere; never implies fresh
  hardware or fresh test validation.
- DESIGN PREVIEW = synthetic labeled timeline (`ok: null`); never acceptance,
  never writes evidence or workspace.
- `ROCm VERIFIED` = live `/api/ps` residency + full digest AND the
  llama-server runner holding AMD HIP DLLs. Nothing less is ever claimed.
- Physical hardware unavailable ⇒ ledger records BLOCKED, never fabricated.
