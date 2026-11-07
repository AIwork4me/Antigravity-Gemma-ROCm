"""RC1 Phase 2 — multi-task scenario driver (A / B / C), independent acceptance.

Per run: restore frozen pristine workspace -> independent pytest (before) ->
agent task (natural language, no fix hints) -> independent pytest (after) ->
frozen-integrity SHA check -> scenario-specific acceptance -> evidence dir.

Scenario B additionally mutation-tests the agent-authored test suite against
frozen mutants (the agent's tests must FAIL against wrong implementations —
this defeats "self-graded" or vacuous test suites).

Usage:
    python src/scenario_agent.py --scenario A_boundary --run 1
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import build_config  # noqa: E402

SCEN = ROOT / "scenarios"
EVID = ROOT / "artifacts" / "multitask"

MISSIONS = {
    "A_boundary": (
        "The pytest suite in this workspace is failing. Investigate the code and the tests, "
        "run the tests, find the bug, fix it with a minimal change, re-run the tests until "
        "they all pass, then summarize the root cause and your fix. Only modify files in this workspace."
    ),
    "B_writetests": (
        "Read REQUIREMENTS.md in this workspace. The implementation in grading.py is believed "
        "correct but has no tests. Write a pytest suite in tests/test_grading.py that verifies "
        "the full contract — every grade band, the boundary values, and the error cases. "
        "Run it with python -m pytest tests/ -q and make sure it passes. Do not modify grading.py or REQUIREMENTS.md."
    ),
    "C_excfix": (
        "The service crashed on startup; the traceback has been saved in crash.log in this workspace. "
        "Read the log, reproduce the failure with the tests in tests/, find the root cause in "
        "settings_parser.py, fix it so the whole test suite passes, re-run the tests, and summarize "
        "what caused the crash and how you fixed it. Only modify files in this workspace."
    ),
}
# files the agent is ALLOWED to change (everything else must stay byte-identical)
MUTABLE = {
    "A_boundary": {"textutils.py"},
    "B_writetests": {"tests/test_grading.py"},  # may only ADD this file
    "C_excfix": {"settings_parser.py"},
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def snap(d: Path) -> dict:
    return {
        str(p.relative_to(d)).replace("\\", "/"): sha(p)
        for p in sorted(d.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }


def pytest_run(ws: Path) -> dict:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line"],
        cwd=ws,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {"exit": r.returncode, "tail": (r.stdout or "")[-600:]}


def mutation_score(ws: Path, mutants_dir: Path) -> dict:
    """Swap each frozen mutant into grading.py; agent's tests must fail."""
    real = ws / "grading.py"
    backup = ws / "grading.py.bak"
    shutil.copy2(real, backup)
    results = {}
    try:
        for m in sorted(mutants_dir.glob("grading_m*.py")):
            shutil.copy2(m, real)
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "-q"],
                cwd=ws,
                capture_output=True,
                text=True,
                timeout=300,
            )
            results[m.stem] = {"killed": r.returncode != 0}
    finally:
        shutil.copy2(backup, real)
        backup.unlink()
    return {
        "per_mutant": results,
        "killed": sum(1 for v in results.values() if v["killed"]),
        "total": len(results),
    }


async def agent_task(ws: Path, mission: str, ev: Path, timeout_s: int) -> dict:
    from google.antigravity import Agent

    audit: list[str] = []
    tools: list[dict] = []
    tokens: list[str] = []
    cfg = build_config(
        workspaces=[ws],
        audit_log=audit,
        system_instructions="You are a careful Python engineer. Use only the provided tools; modify files with edit_file/create_file; run tests with run_command 'python -m pytest tests/ -q' and always set its working directory (Cwd) to the workspace root.",
    )
    t0 = time.time()
    async with Agent(cfg) as agent:
        resp = await agent.chat(mission)

        async def tok():
            async for t in resp:
                tokens.append(t)

        async def tcs():
            async for c in resp.tool_calls:
                tools.append(
                    {"name": getattr(c, "name", None), "args": str(getattr(c, "args", None))[:400]}
                )

        await asyncio.wait_for(asyncio.gather(tok(), tcs()), timeout=timeout_s)
    return {
        "elapsed_s": round(time.time() - t0, 1),
        "n_tools": len(tools),
        "tools": tools,
        "audit": audit,
        "reply": "".join(tokens),
        "mission": mission,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=MISSIONS)
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--model", default="gemma4:26b")
    args = ap.parse_args()

    sc = args.scenario
    run_dir = SCEN / sc / "run"
    pristine = SCEN / sc / "pristine"
    ev = EVID / f"{sc}-run{args.run}"
    ev.mkdir(parents=True, exist_ok=True)

    # frozen restore
    if run_dir.exists():
        shutil.rmtree(run_dir)
    shutil.copytree(pristine, run_dir)

    before_snap = snap(run_dir)
    (ev / "before.json").write_text(json.dumps(before_snap, indent=2), encoding="utf-8")

    pre = pytest_run(run_dir)
    (ev / "pytest_before.json").write_text(json.dumps(pre, indent=2), encoding="utf-8")

    try:
        r = asyncio.run(agent_task(run_dir, MISSIONS[sc], ev, args.timeout))
    except Exception as e:
        r = {
            "elapsed_s": None,
            "n_tools": 0,
            "tools": [],
            "audit": [],
            "reply": "",
            "error": f"{type(e).__name__}: {e}",
        }
    (ev / "agent.json").write_text(
        json.dumps({k: v for k, v in r.items() if k != "mission"}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    after_snap = snap(run_dir)
    (ev / "after.json").write_text(json.dumps(after_snap, indent=2), encoding="utf-8")
    post = pytest_run(run_dir)
    (ev / "pytest_after.json").write_text(json.dumps(post, indent=2), encoding="utf-8")

    changed = sorted(
        k for k in set(before_snap) | set(after_snap) if before_snap.get(k) != after_snap.get(k)
    )
    illegal = [c for c in changed if c not in MUTABLE[sc]]

    acceptance: dict = {
        "scenario": sc,
        "run": args.run,
        "elapsed_s": r.get("elapsed_s"),
        "n_tool_calls": r.get("n_tools"),
        "error": r.get("error"),
        "files_changed": changed,
        "illegal_changes": illegal,
    }
    if sc in ("A_boundary", "C_excfix"):
        acceptance["bug_present_before"] = pre["exit"] != 0
        acceptance["all_green_after"] = post["exit"] == 0
        acceptance["used_tools"] = r.get("n_tools", 0) > 0
        acceptance["accept"] = (
            acceptance["bug_present_before"]
            and acceptance["all_green_after"]
            and acceptance["used_tools"]
            and not illegal
        )
        acceptance["why"] = (
            ""
            if acceptance["accept"]
            else (
                f"pre={pre['exit']} post={post['exit']} illegal={illegal} tools={r.get('n_tools')} err={r.get('error')}"
            )
        )
    else:  # B_writetuts
        created = [c for c in changed if c == "tests/test_grading.py"]
        mut = mutation_score(run_dir, SCEN / sc / "_mutants")
        acceptance["suite_created"] = bool(created)
        acceptance["all_green_after"] = post["exit"] == 0
        acceptance["mutation"] = mut
        acceptance["accept"] = (
            created
            and post["exit"] == 0
            and not illegal
            and mut["killed"] >= 2
            and (r.get("n_tools", 0) > 0)
        )
        acceptance["why"] = (
            ""
            if acceptance["accept"]
            else (
                f"created={bool(created)} post={post['exit']} illegal={illegal} mutants_killed={mut['killed']}/{mut['total']} err={r.get('error')}"
            )
        )

    (ev / "acceptance.json").write_text(
        json.dumps(acceptance, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # keep a diff of the change (pristine vs post-agent)
    d = subprocess.run(
        ["git", "diff", "--no-index", "--", str(pristine), str(run_dir)],
        capture_output=True,
        text=True,
    )
    (ev / "diff.patch").write_text(d.stdout or "(no diff)", encoding="utf-8")
    print(json.dumps({k: v for k, v in acceptance.items() if k != "mutation"}, ensure_ascii=False))
    return 0 if acceptance["accept"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
