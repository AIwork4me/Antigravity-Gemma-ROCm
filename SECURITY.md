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

## Scope

- ✅ In scope: the policy predicates, workspace resolution, web dashboard
  (binds 127.0.0.1 only), demo workspaces, scripts in this repo.
- ❌ Out of scope: the `google-antigravity` SDK itself (report to Google),
  Ollama, model weights, and anything requiring physical access to an
  unlocked machine.
