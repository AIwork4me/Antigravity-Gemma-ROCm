# Security Policy

## Reporting a vulnerability

Please use GitHub's private **"Report a vulnerability"** (Security Advisories)
on this repository. Do not open public issues for security problems.

## What the boundaries are — and are not

This project runs an LLM agent that executes real tools on your machine. The
demo ships **three policy boundaries** around it:

1. **File boundary** — file tools (view/create/edit/list) only operate on paths
   that resolve inside the configured workspace; resolution is reparse-point
   aware (junctions/symlinks are resolved before comparison). See
   `src/config.py` and `tests/test_security_boundaries.py`.
2. **Shell boundary** — `run_command` is gated by a structured, fail-closed
   allowlist: the command line is tokenized and validated as
   *(executable, arguments, working directory)*. Only `python -m pytest` /
   `pytest` invocations with an enumerated flag set, in-workspace test paths
   and an **in-workspace Cwd** pass. Unknown argument schemas, ambiguous
   command keys, arbitrary scripts, `-c` code evaluation and any shell
   metacharacter are denied. Decisions are audit-logged.
3. **Web dashboard boundary** — the dashboard binds `127.0.0.1` only;
   `POST /api/run` additionally enforces loopback Host/Origin/Sec-Fetch-Site
   headers (CSRF drive-by protection), the SSE stream is served by a
   non-blocking snapshot loop, and replay files are size/shape-validated
   before rendering.

**Known limitation (documented honestly):** the SDK's shell subprocesses run
with the caller's privileges — there is **no OS-level sandbox** on Windows in
this stack, and an allowlist of `pytest` is not a sandbox either: pytest
executes whatever test code is in the workspace. Keep the demo confined to
the disposable `demo_workspace`; for arbitrary imported repositories,
default-disable execution (`build_config(enable_shell=False)`) or require
explicit human approval plus a low-privilege account. Details and red-team
results: [docs/SECURITY_BOUNDARY_REVIEW.md](docs/SECURITY_BOUNDARY_REVIEW.md).

**Executable shadowing (why syntax validation ≠ execution isolation):** the
policy validates the command *string*, but the SDK harness resolves the
executable with the OS. On default-config Windows, `cmd.exe` and
`CreateProcess` search the **current directory before PATH** — and the
workspace Cwd is writable by the agent, so a planted `python.bat` /
`pytest.exe` inside the workspace can hijack an "allowed" command
(`tests/test_exec_resolution.py` pins this with a harmless sentinel). The
policy layer cannot rewrite commands to absolute interpreter paths, so we do
not claim command-syntax validation provides execution isolation; treat the
workspace as trusted input, or set
`NoDefaultCurrentDirectoryInExePath` / run from a non-writable Cwd for
defense in depth.

## More limits stated precisely

- **Loopback is not authentication.** The dashboard binds `127.0.0.1` and
  checks Host/Origin headers — that stops *cross-origin browsers*, not other
  local processes. Any code running as the same (or higher-privileged) user
  on the machine can reach a loopback port; there is no per-request identity.
- **TOCTOU:** every file/path check is a check-then-use pair. A concurrent
  local process can swap a verified path (junction, rename) between check and
  use. The boundary layer narrows this window and the red-team tests pin the
  resolution rules, but a same-machine adversary with timing control is
  outside the threat model.
- **Evidence sidecars are integrity checks, not signatures.** The SHA256
  sidecars/manifests shipped with releases let you verify that the bytes you
  downloaded are the bytes we hashed. They are **not** digital signatures:
  they bind no identity and provide no non-repudiation.
- **The physical GPU validation is strong process-level evidence, not an
  unforgeable hardware proof.** `scripts/gpu_validate.py` ties a PASS to a
  live inference probe, an owned server's log, PPID-attributed runner DLLs
  and a sealed evidence directory — but a sufficiently privileged local
  adversary could still fake parts of the chain. It is also not an OS
  sandbox. Trust boundary: [docs/GPU_BACKEND_VALIDATION.md](docs/GPU_BACKEND_VALIDATION.md).

## Scope

- ✅ In scope: the policy predicates, workspace resolution, web dashboard
  (binds 127.0.0.1 only), demo workspaces, scripts in this repo.
- ❌ Out of scope: the `google-antigravity` SDK itself (report to Google),
  Ollama, model weights, and anything requiring physical access to an
  unlocked machine.
