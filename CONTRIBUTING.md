# Contributing to Antigravity-Gemma-ROCm

Thanks for helping build the evidence-first local agent. This repo has two
kinds of truth: **code** (tests decide) and **claims** (measurements decide).
Both welcome contributions.

## Dev setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\scripts\preflight.ps1        # readiness gate (auto-restores demo_workspace)
.\.venv\Scripts\python.exe -m pytest -q          # unit + security red-team
.\.venv\Scripts\python.exe -m pytest -m live -q  # needs Ollama + model loaded
```

Lint before you push — CI runs exactly this:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

## The fastest first PR: a hardware-table row

Run the demo on your machine, then either:

1. open a [hardware report](.github/ISSUE_TEMPLATE/hardware_report.md) issue with
   the filled table (a maintainer will add the row), **or**
2. PR the row yourself: edit the *Hardware compatibility* table in **both**
   `README.md` and `README.zh-CN.md`, linking your issue as evidence.

Rules for rows: only claim ✅ **measured** for runs you actually did on that
hardware/backend; everything else stays 🟡 with a pointer to the report.

## Contributing a scenario task

Scenarios live in [`scenarios/`](scenarios/) with the shape:

```
scenarios/X_name/
  pristine/          # the starting workspace the agent receives
  _mutants/          # (optional) seeded mutants for test-writing tasks
```

Requirements for a new scenario:

- `pristine/` must be self-contained (no imports outside it) and carry its own
  `tests/` where the task type needs them;
- the task must be **independently verifiable** — success is decided by a
  process outside the agent (pytest exit code / diff), never by model self-report;
- add the scenario to `scripts/multitask_matrix.py` and one validation run under
  `artifacts/` (before open-sourcing) or a hardware-report issue as evidence.

## Code conventions

- Python ≥ 3.11, type hints in new code, `ruff` (line 100) decides style —
  don't hand-format against it.
- Security invariants live in `src/config.py` and are pinned by
  `tests/test_security_boundaries.py`; if your change touches tool policy,
  extend the red-team suite **first** (failing test), then change the policy.
- Measured numbers in READMEs must trace to `evidence/`, `docs/`, or a linked
  issue — never gut-feel numbers.

## Commit style

Conventional-ish one-liners: `feat:`, `fix:`, `docs:`, `ci:`, `evidence:`.
Keep commits small enough to review individually.

## Reporting security issues

Use GitHub **Security Advisories** (Report a vulnerability), not public issues.
Context on the boundary model: [SECURITY.md](SECURITY.md) and
[docs/SECURITY_BOUNDARY_REVIEW.md](docs/SECURITY_BOUNDARY_REVIEW.md).
