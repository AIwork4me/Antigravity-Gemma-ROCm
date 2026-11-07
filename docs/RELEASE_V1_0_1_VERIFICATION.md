# v1.0.1 Verification (developer-facing)

This page records how the **v1.0.1 security-hardening release** was verified, with
the exact evidence you can re-check yourself. Every claim below traces to a file
in this repository, a command you can run, or a public GitHub URL. The
merge commit, tag `v1.0.1` and the published Release (with the sanitized
evidence bundle) are on this repository's [Releases page](../../releases) —
that page is the authoritative record of what shipped.

## What v1.0.1 is

A security-and-truthfulness hardening release on top of v1.0.0. No new product
features; the agent, dashboard and scenarios are unchanged. What changed:

1. **Physical GPU validation is non-destructive by default.** The validator
   spawns its own private Ollama instance (`127.0.0.1:11577`) and never
   unloads, stops or kills anything it did not create. (v1.0.0's validator
   evicted every model resident on a shared `11434` server before starting —
   which could interrupt other inference workloads on the same machine.)
   Eviction is now an explicit, disruptive opt-in (`--evict-shared-models`),
   and read-only `/api/ps` snapshots taken before/after each run **prove** the
   shared server was untouched — enforced as a gate, not just recorded.
2. **Tighter, better-attributed evidence chain.** A GPU PASS is bound to the
   run that produced it: code SHA, owned-server PID, the `llama-server` runner
   PID proven to descend from that server (PPID chain), the exact DLL names
   loaded by that runner (e.g. `amdhip64_7.dll` — parsed from the module
   column, never the wildcard pattern searched), the model digest
   cross-checked between `/api/ps` and `ollama list`, and a `seal.json`
   hashing every evidence file in the run directory.
3. **Hardened GPU workflow.** The opt-in `workflow_dispatch` gate runs only on
   this repository's `main` (tag wildcards removed), inputs travel via
   environment variables through a fail-closed validation step, and an
   environment probe can never satisfy the full-validation release gate (a
   green `gpu-validate` job means exactly *sealed full GPU PASS*).
4. **Publication-surface fixes.** `.xml` artifacts are now scanned/redacted
   like other text (the v1.0.0-era candidate bundle shipped a `dxdiag.xml`
   with a machine name and serial-shaped identifiers — caught and fixed before
   release, never published); unrecognized archive member types now fail the
   publication scan closed; report/stdout paths are redacted.
5. **Documentation truth pass.** The upcoming GDG DevFest talk is no longer
   described in the past tense; test counts are layered by era instead of
   mixed; SECURITY.md now states precisely: loopback is not authentication,
   every check is TOCTOU-raceable, evidence sidecars are integrity checks —
   not signatures.

## Verified on real hardware (local physical gate)

Run `gpuval-20261107-220207`, produced by `scripts/gpu_validate.py --with-agent`
on the dev/reference machine with the release candidate code
(`ad153f3`, clean tree):

| Chain link | Result |
|---|---|
| Scope / verdict | `full` / **PASS** (exit 0) |
| Code binding | dev SHA `ad153f3`, tree `7a3c83c2`, `dirty=false` |
| Machine | Ryzen AI MAX+ 395, Radeon 8060S (`gfx1151`), Windows 11 |
| Ollama | 0.40.14 |
| Model | `gemma4:26b`, full digest `001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c`, 100% GPU-resident (`size == size_vram`) |
| Identity cross-check | `/api/ps` digest extends the `ollama list` id `001e5dafc3c7` — PASS |
| Owned server | PID 42180 on `127.0.0.1:11577`, log captured by this run |
| Runner attribution | `llama-server.exe` PID 128036 descends from the owned server (PPID chain) |
| Backend (live DLLs) | `amdhip64_7.dll`, `libhipblas.dll`, `rocblas.dll` in that runner — **ROCm** |
| Discovery line (own log) | `library=ROCm compute=gfx1151`, GPU "AMD Radeon(TM) 8060S Graphics" |
| Non-interference | shared `11434` residency before == after (`[]`/`[]`), gate PASS |
| Agent stage | subprocess exit code `0` (genuine int), 5 tool calls, 61.4 s, `pytest` red → green, one non-test file changed (`stats_utils.py`), after-hashes re-verified on disk |
| Seal | `seal.json` — SHA256 of all 13 evidence files, independently re-verified |

The same script also backs the release workflow: after merge, a
`workflow_dispatch` run of `GPU physical validation` on `main` (through the
protected `gpu-validation` environment) re-executes this gate on the merged
code. That run's URL is recorded in the v1.0.1 Release Notes.

## Test and scan totals (release candidate)

| Check | Command | Result |
|---|---|---|
| Unit + security red-team | `python -m pytest -q` | **323 passed, 1 skipped** (live deselected) |
| Live agent tests (real model) | `python -m pytest -m live -q` | **3 passed** |
| Lint / format | `ruff check .` / `ruff format --check .` | clean |
| Publication scan | `python scripts/release_scan.py --enforce` | **0 findings** (114 files) |
| Dependency audit | `pip-audit --skip-editable` | **no known vulnerabilities** |
| CI (GitHub-hosted) | windows-latest × Python 3.12 / 3.13 | both legs green on the release head (see the PR's checks) |

Re-run all of it yourself:

```powershell
git clone https://github.com/AIwork4me/Antigravity-Gemma-ROCm.git
cd Antigravity-Gemma-ROCm
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q        # 323 passed, 1 skipped
.\.venv\Scripts\python.exe scripts\release_scan.py --enforce
```

## Independent review

Two adversarial review rounds were run against the release-candidate diff:

- **Round 1**: 0 Critical / 0 High / **3 Medium / 5 Low / 3 Nit** — all fixed
  with regression tests (non-destructive default, redacted reports, `.xml`
  scanning, seal-required classification, PID-window narrowing, enforced
  non-interference gate, anchored DLL matching, exact runner image).
- **Round 2**: verified every Round-1 fix closed; found **3 Low / 4 Nit**
  residual items — fixed (stdout path, opaque-member fail-closed, stale
  counts, case-insensitive hostname pattern) or documented as accepted
  residuals (existence-only seal check in the workflow's classifier; content
  verification remains `scripts/verify_bundle.py`'s job).

No unresolved Critical/High issues. The public summary of the T1-era audit is
[docs/SECURITY_AUDIT_T1.md](SECURITY_AUDIT_T1.md).

## Repository governance (as configured for this release)

- Branch `main` is protected by an active ruleset: no force-push, no deletion,
  changes only via pull request, with required status checks `test (3.12)` and
  `test (3.13)` (strict/up-to-date policy). Verified live: a force-push
  attempt is rejected with a rule-violation error.
- The `gpu-validation` GitHub environment requires a reviewer before the
  self-hosted GPU job executes.
- **Solo-maintainer limitation (stated honestly):** this repository has
  exactly one collaborator, so GitHub-enforced *review* is inherently
  self-review — the PR cannot require an approving review count above 0 (the
  author cannot approve their own PR), and the environment reviewer is the
  same account that dispatches (`prevent_self_review=false` is the only
  satisfiable setting). These are process signals, not independent approvals.
  Meaningful independent enforcement would require adding a second
  maintainer/reviewer.

## Evidence bundle

`evidence-bundle-v1.0.1.zip` (attached to the v1.0.1 Release) contains the
sanitized evidence tree with an inner `SHA256SUMS.txt` manifest and an outer
`.sha256` sidecar. Verify after download:

```powershell
certutil -hashfile evidence-bundle-v1.0.1.zip SHA256   # compare with the .sha256 sidecar
python scripts\verify_bundle.py evidence-bundle-v1.0.1.zip
```

`verify_bundle.py` recomputes the outer hash with certutil (independent
implementation), re-verifies every inner manifest hash, checks member counts,
rejects unsafe member names, and re-runs the publication scanner over the
archive.

## Legacy asset disclosure (v1.0.0)

The `evidence-bundle-v1.0.0.zip` attached to the v1.0.0 release contains
machine-generated evidence with absolute paths including the development
machine's username (`C:\Users\<name>\…`). No credentials, tokens or keys were
included; the exposure is the local username only. The historical release and
asset are preserved unmodified for traceability — v1.0.0 predates the
deterministic path-redaction now applied to every shipped text member.

## Trust boundaries (unchanged, restated)

The GPU validation above is strong **process-level** evidence — it ties a PASS
to live inference, an owned server's log, an attributed runner's DLLs and a
sealed evidence directory. It is **not** an unforgeable hardware proof and
**not** an OS sandbox; a sufficiently privileged local adversary could fake
parts of the chain. Sidecars are integrity checks, not signatures. See
[SECURITY.md](../SECURITY.md) for the full boundary model.
