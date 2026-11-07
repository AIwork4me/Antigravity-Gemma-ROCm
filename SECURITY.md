# Security Policy

## Reporting a vulnerability

Please use GitHub's private **"Report a vulnerability"** (Security Advisories)
on this repository. Do not open public issues for security problems.

## What the boundaries are — and are not

This project runs an LLM agent that executes real tools on your machine. The
demo ships **two policy boundaries** around it:

1. **File boundary** — file tools (view/create/edit/list) only operate on paths
   that resolve inside the configured workspace; resolution is reparse-point
   aware (junctions/symlinks are resolved before comparison). See
   `src/config.py` and `tests/test_security_boundaries.py`.
2. **Shell boundary** — `run_command` is gated by a strict whitelist
   (pytest/python on workspace modules); any shell metacharacter rejects the
   command outright. Decisions are audit-logged.

**Known limitation (documented honestly):** the SDK's shell subprocesses run
with the caller's privileges — there is **no OS-level sandbox** on Windows in
this stack. If you need stronger isolation, run the driver under a
low-privilege account, wire an ask-a-human handler (`policy.ask_user`) for
`run_command`, or both. Details and red-team results:
[docs/SECURITY_BOUNDARY_REVIEW.md](docs/SECURITY_BOUNDARY_REVIEW.md).

## Scope

- ✅ In scope: the policy predicates, workspace resolution, web dashboard
  (binds 127.0.0.1 only), demo workspaces, scripts in this repo.
- ❌ Out of scope: the `google-antigravity` SDK itself (report to Google),
  Ollama, model weights, and anything requiring physical access to an
  unlocked machine.
