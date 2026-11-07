# Security & Publication Audit — T1 / T1.1 (public summary)

Last updated: 2026-11-07. This is the public technical summary of the two
internal audit passes. Internal working documents (launch plans, session
records) stay private by design; everything technical that affects users is
here or in the referenced files.

## What was audited

The agent policy layer (`src/config.py`), the web dashboard (`src/webui.py`),
workspace restoration (`src/demo_agent.py`), the publication pipeline
(`scripts/release_scan.py`, `scripts/publish_export.py`,
`scripts/build_evidence_bundle.py`), the physical GPU gate
(`scripts/gpu_validate.py`, `.github/workflows/gpu-validation.yml`), and the
published evidence surface.

## T1 findings (all fixed, each with adversarial tests)

| # | Finding | Fix |
|---|---|---|
| 1 | `run_command` authorization was prefix/substring matching — `python -c import demo_workspace` and `pytest_evil …` passed | structured fail-closed policy: schema/tokenizer/executable/args/Cwd validation |
| 2 | `POST /api/run` had no CSRF/origin controls | loopback Host + exact-netloc Origin + Sec-Fetch-Site gate (403) |
| 3 | SSE generator blocked the event loop (`Condition.wait`) | non-blocking snapshot loop |
| 4 | replay files unbounded; `files_changed` rendered into `innerHTML` | size/shape caps; textContent rendering |
| 5 | publication scanner covered 5 dirs, never `evidence/`, could not fail | full publication surface + `--enforce` + auditable allowlist |
| 6 | shipped evidence carried dev-username paths | deterministic `<repo>`/`<home>` redaction (evidence only) |
| 7 | workspace restore followed junctions | junction-aware removal + root-refusal |
| 8 | independent review: 1 MED + 8 LOW (origin port-insensitivity, NaN passes, quoted-argv aliasing, recursion DoS, zip skip fail-open, pattern gaps, …) | all fixed with tests (`tests/test_webui_http.py`, `tests/test_security_boundaries.py`, `tests/test_release_scan.py`) |

## T1.1 findings (all fixed)

| # | Finding | Fix |
|---|---|---|
| 1 | **GPU validation accepted a stale backend log**: the T1 run's `library=ROCm` line was 3 days older than the serving process — a false-positive PASS class | `gpu_validate.py` v2: attribution-first chain — owned `ollama serve` on a private port with its captured log (fresh discovery line), full 64-hex digest + 100% VRAM from `/api/ps`, live backend identity from runner-process DLLs (`llama-server` + `rocblas`/`libhipblas`; `tasklist /m` patterns must be exact-name or trailing-star — mid-string `*` is invalid syntax there and used to silently kill half the probes), agent exit code required, red→green re-derived from the run's own JSON with int-typed exit codes, `files_changed` re-checked against SHA256 maps **and against the files on disk**, and runs that go green by touching/deleting the workspace's own `tests/` are FAIL; 47 tests (positive + negative) force FAIL/BLOCKED for every invalid condition |
| 2 | GPU workflow interpolated `workflow_dispatch` inputs into shell strings (self-hosted runner command injection) | inputs travel via env vars only, validated by a fail-closed step; `permissions: contents: read`, `persist-credentials: false`, protected `environment:`, concurrency=1, timeout, repository+ref guard; 7 static guard tests (`tests/test_gpu_workflow.py`) |
| 3 | `--no-agent` could report a full PASS | explicit `ENV_PROBE_PASS` / `ENV_PROBE_BLOCKED` labels, never "PASS" |
| 4 | executable shadowing: Windows resolves the CWD before PATH, so a planted `python.bat` in the writable workspace can hijack an allowed command (empirically pinned with a harmless sentinel) | documented as a hard limitation — syntax validation is explicitly NOT execution isolation (`SECURITY.md`, `tests/test_exec_resolution.py`) |
| 5 | requested-but-missing export/archive was silently skipped by the scanner | fail-closed exit 2 |
| 6 | evidence bundle shipped raw paths; no independent hash verification | bundle text members redacted before hashing; `<zip>.sha256` sidecar (accidental-drift check — it is **not signed**, so it is not tamper-proofing); `scripts/verify_bundle.py` recomputes the outer hash with certutil + inner manifest + counts + member-name safety + runs the publication scanner over the archive |

**T1.1 second independent review (0 HIGH / 3 MED / 4 LOW / 5 NIT, all
confirmed):** dead `tasklist /m` wildcard patterns (F-1), OwnedServer
failure-path process/log leak (F-2), gut-the-tests false PASS (F-3),
verdict fail-open on missing keys (F-4), trailing-newline tag divergence
(F-5), traversal member names blessed by the verifier (F-6), unsigned
sidecar overstated as "immutable" (F-7) + NITs — **all fixed in-code with
tests** (see `tests/test_gpu_validate.py`, `tests/test_verify_bundle.py`).
Remaining accepted: the workflow `if:` guard and protected environment
bound repo *writers*, not malicious forks only — the environment must be
configured with required reviewers by the owner to become a real approval
gate; agent-writable golden tests remain the roadmap answer to fully
unforgeable red→green acceptance.

## Known residual limitations (accepted, documented)

1. No OS sandbox for SDK subprocesses; a pytest allowlist is not a sandbox.
2. Executable shadowing in the writable workspace Cwd (above) — mitigations:
   trusted workspace only, `NoDefaultCurrentDirectoryInExePath`, or
   `enable_shell=False` for foreign repos.
3. Policy-time vs harness-execution TOCTOU window (closed-source harness).
4. Loopback dashboard is origin-gated, not authenticated (single-user tool).
5. SSE ring buffer is lossy for suspended clients; REPLAY mode is sticky.
6. The already-published v1.0.0 release assets contain `C:\Users\<name>`
   paths (username disclosure, no secrets found) — **owner decision**: leave
   as-is or re-cut from the redacted pipeline (v1.0.1 bundle prepared and
   independently verified on the dev side; not published without approval).

## Verify it yourself

- `pytest -q` — full suite incl. all adversarial/negative suites
- `python scripts/release_scan.py --enforce --extra-root <export> --zip <bundle>`
- `python scripts/verify_bundle.py <bundle.zip>`
- `python scripts/gpu_validate.py --with-agent` on real ROCm hardware
