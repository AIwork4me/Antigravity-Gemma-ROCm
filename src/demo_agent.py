"""Demo driver: real agent tool-execution demo (bug-fixing task).

Runs the Antigravity SDK Agent against local Ollama Gemma 4, pointed at the
demo_workspace with a planted, independently-reproducible bug. Captures:

  - the full tool-call stream (name + args)   [real harness tool execution]
  - run_command policy decisions (audit log)
  - SHA256 of workspace files before/after
  - an independent pytest verification subprocess (outside the agent)
  - git diff of the workspace change

Usage:
    python src/demo_agent.py [--run-id N] [--model gemma4:26b] [--timeout 900]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DEFAULT_BASE_URL, DEFAULT_MODEL, DEMO_WORKSPACE, PROJECT_ROOT, build_config

PRISTINE = PROJECT_ROOT / "demo_workspace_pristine"
EVIDENCE_ROOT = PROJECT_ROOT / "artifacts" / "agent-execution"

MISSION = (
    "You are working in a small Python project in the current workspace. "
    "The pytest suite is currently failing. Your task:\n"
    "1. List the files in the workspace to understand the project.\n"
    "2. Read the source file stats_utils.py and the tests in tests/test_stats.py.\n"
    "3. Run the test suite from the workspace root with: python -m pytest tests/ -q\n"
    "4. Find the bug that makes the failing tests fail, fix it by editing stats_utils.py.\n"
    "5. Re-run the tests and confirm they all pass.\n"
    "6. Reply with a short summary of the root cause and your fix.\n"
    "Only modify files inside this workspace. Do not touch anything else."
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def workspace_state(ws: Path) -> dict:
    return {
        str(p.relative_to(ws)).replace("\\", "/"): {
            "sha256": sha256_of(p),
            "bytes": p.stat().st_size,
        }
        for p in sorted(ws.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }


def _is_reparse(p: Path) -> bool:
    """Junction or symlink (Windows junctions are NOT is_symlink())."""
    try:
        return p.is_symlink() or os.path.isjunction(p)
    except OSError:
        return True  # cannot tell -> treat as reparse point, never follow


def _remove_link(link: Path) -> None:
    """Remove a reparse point itself, never its target."""
    try:
        link.unlink()
    except OSError:
        link.rmdir()  # directory junctions remove with rmdir


def _force_unlink(p: Path) -> None:
    """unlink that clears Windows read-only bits first (partial-state fix)."""
    try:
        p.unlink()
    except PermissionError:
        os.chmod(p, stat.S_IWRITE)
        p.unlink()


def _safe_rmtree(path: Path) -> None:
    """rmtree that never follows junctions/symlinks anywhere in the tree."""
    if _is_reparse(path):
        _remove_link(path)
        return
    for child in path.iterdir():
        if _is_reparse(child):
            _remove_link(child)
        elif child.is_dir():
            _safe_rmtree(child)
        else:
            _force_unlink(child)
    try:
        path.rmdir()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.rmdir()


def restore_bug_baseline() -> None:
    """Reset demo_workspace to the pristine (buggy) state for a fresh run.

    Fresh clones have no demo_workspace at all — create it (preflight does the
    same, but this function must survive being called directly). Deletion is
    junction-safe: a planted link inside the workspace is removed as a link,
    its target is never touched, and the workspace root itself must not be a
    reparse point.
    """
    if _is_reparse(DEMO_WORKSPACE):
        raise RuntimeError(
            f"refusing to restore: {DEMO_WORKSPACE} is a junction/symlink "
            "(a malicious workspace could redirect deletion outside)"
        )
    DEMO_WORKSPACE.mkdir(parents=True, exist_ok=True)
    for item in DEMO_WORKSPACE.iterdir():
        if _is_reparse(item):
            _remove_link(item)
        elif item.is_dir():
            _safe_rmtree(item)
        else:
            _force_unlink(item)
    for item in PRISTINE.iterdir():
        if item.is_dir():
            shutil.copytree(item, DEMO_WORKSPACE / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, DEMO_WORKSPACE)


def run_pytest_independently(ws: Path) -> dict:
    """Verification OUTSIDE the agent process (independent test run)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
        cwd=ws,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-2000:],
    }


async def run_agent_task(model: str, base_url: str, timeout_s: int, ev_dir: Path) -> dict:
    from google.antigravity import Agent

    audit_log: list[str] = []
    tool_events: list[dict] = []
    tokens: list[str] = []

    config = build_config(
        model=model,
        base_url=base_url,
        workspaces=[DEMO_WORKSPACE],
        system_instructions=(
            "You are a careful Python engineer. Prefer minimal, correct fixes. "
            "Use only the tools provided to you. To modify an existing file use "
            "the edit_file tool (do not invent other tool names). To execute "
            "commands use run_command with plain 'python -m pytest tests/ -q' "
            "and always set its working directory (Cwd) to the workspace root."
        ),
        audit_log=audit_log,
    )
    (ev_dir / "config.txt").write_text(
        json.dumps(
            {
                "model": model,
                "base_url": base_url,
                "workspaces": [str(DEMO_WORKSPACE)],
                "policies": [
                    "file tools: allow+when path resolves inside demo_workspace",
                    "run_command: ask_user handler, structured fail-closed allowlist "
                    "(python -m pytest / pytest only, in-workspace Cwd required)",
                ],
                "preset": "lightweight",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    t0 = time.time()
    async with Agent(config) as agent:
        response = await agent.chat(MISSION)

        async def collect_tokens():
            async for token in response:
                tokens.append(token)

        async def collect_tool_calls():
            async for call in response.tool_calls:
                tool_events.append(
                    {
                        "name": getattr(call, "name", None),
                        "args": str(getattr(call, "args", None))[:2000],
                    }
                )

        await asyncio.wait_for(
            asyncio.gather(collect_tokens(), collect_tool_calls()), timeout=timeout_s
        )
    elapsed = time.time() - t0

    (ev_dir / "mission.txt").write_text(MISSION, encoding="utf-8")
    (ev_dir / "tool_calls.json").write_text(json.dumps(tool_events, indent=2), encoding="utf-8")
    (ev_dir / "policy_audit.txt").write_text(
        "\n".join(audit_log) or "(no run_command calls)", encoding="utf-8"
    )
    (ev_dir / "agent_reply.txt").write_text("".join(tokens), encoding="utf-8")

    return {
        "elapsed_s": round(elapsed, 1),
        "n_tool_calls": len(tool_events),
        "tool_events": tool_events,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None, help="label for this run (default: timestamp)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--no-restore", action="store_true", help="do not reset the bug baseline first"
    )
    args = parser.parse_args()

    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    ev_dir = EVIDENCE_ROOT / f"run-{run_id}"
    ev_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_restore:
        restore_bug_baseline()

    before = workspace_state(DEMO_WORKSPACE)
    (ev_dir / "workspace_before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")

    # Pre-task independent verification: bug must be present
    pre = run_pytest_independently(DEMO_WORKSPACE)
    (ev_dir / "pytest_before.json").write_text(json.dumps(pre, indent=2), encoding="utf-8")
    print(f"[run-{run_id}] pytest before: exit={pre['exit_code']} (expect nonzero = bug present)")

    result = asyncio.run(run_agent_task(args.model, args.base_url, args.timeout, ev_dir))
    print(
        f"[run-{run_id}] agent finished in {result['elapsed_s']}s with {result['n_tool_calls']} tool calls"
    )

    after = workspace_state(DEMO_WORKSPACE)
    (ev_dir / "workspace_after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")

    post = run_pytest_independently(DEMO_WORKSPACE)
    (ev_dir / "pytest_after.json").write_text(json.dumps(post, indent=2), encoding="utf-8")
    print(f"[run-{run_id}] pytest after: exit={post['exit_code']} (0 = all green)")

    files_changed = sorted(
        k
        for k in set(before) | set(after)
        if before.get(k, {}).get("sha256") != after.get(k, {}).get("sha256")
    )
    summary = {
        "run_id": run_id,
        "model": args.model,
        "bug_present_before": pre["exit_code"] != 0,
        "bug_fixed_after": post["exit_code"] == 0,
        "files_changed": files_changed,
        "elapsed_s": result["elapsed_s"],
        "n_tool_calls": result["n_tool_calls"],
        "tool_names": [t.get("name") for t in result["tool_events"]],
    }
    (ev_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if (summary["bug_fixed_after"] and summary["bug_present_before"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
