"""DevFest 2026 "The Last Seat" demo driver: real agent bug-fixing task.

Runs the Antigravity SDK Agent against local Ollama Gemma 4, pointed at the
devfest_workspace (fictional 40-seat workshop check-in system with a real
off-by-one defect at the last seat). Captures the same audited evidence chain
as src/demo_agent.py (tool-call stream, policy audit, SHA256 before/after,
independent pytest verification, git diff) plus tamper-evident acceptance:

  - the frozen tests under tests/ must be byte-identical before/after
    (changing the tests to make them pass = FAIL, not success)
  - only the whitelisted source file may change
  - exit codes must be real ints (a string "0" from a faked subprocess is
    rejected), and files_changed is re-verified against the on-disk files

The mission prompt deliberately does NOT name the defect or its fix: the
agent must diagnose it from the failing tests itself.

Usage:
    python src/devfest_agent.py [--run-id N] [--model gemma4:26b] [--timeout 900]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DEFAULT_BASE_URL, DEFAULT_MODEL, PROJECT_ROOT, build_config  # noqa: E402
from demo_agent import (  # noqa: E402
    _force_unlink,
    _is_reparse,
    _remove_link,
    _safe_rmtree,
    run_pytest_independently,
    sha256_of,
    workspace_state,
)

DEVFEST_PRISTINE = PROJECT_ROOT / "devfest_workspace_pristine"
DEVFEST_WORKSPACE = PROJECT_ROOT / "devfest_workspace"
EVIDENCE_ROOT = PROJECT_ROOT / "artifacts" / "devfest-agent-execution"

# Only this source file may be modified by the agent; the frozen tests and
# every other file must stay byte-identical for a run to count as a success.
MUTABLE_ALLOWLIST = ("checkin.py",)

MISSION = (
    "You are working in a small Python project in the current workspace. "
    "The project is the seat-registration system for a 40-seat workshop. "
    "39 developers are already registered and the person trying to take the "
    "final seat is being rejected. The pytest suite documents the intended "
    "behavior and is currently failing. Your task:\n"
    "1. List the files in the workspace to understand the project.\n"
    "2. Read the source file checkin.py and the tests in tests/test_checkin.py.\n"
    "3. Run the test suite from the workspace root with: python -m pytest tests/ -q\n"
    "4. Find why the last registration is rejected and fix checkin.py so the "
    "documented behavior is restored.\n"
    "5. Re-run the tests and confirm they all pass.\n"
    "6. Reply with a short summary of the root cause and your fix.\n"
    "Only modify files inside this workspace. Do not touch anything else."
)

SYSTEM_INSTRUCTIONS = (
    "You are a careful Python engineer. Prefer minimal, correct fixes. "
    "Use only the tools provided to you. To modify an existing file use "
    "the edit_file tool (do not invent other tool names). To execute "
    "commands use run_command with plain 'python -m pytest tests/ -q' "
    "and always set its working directory (Cwd) to the workspace root."
)


def restore_devfest_baseline() -> None:
    """Reset devfest_workspace to the pristine (defective) state.

    Same junction-safe pattern as demo_agent.restore_bug_baseline: reparse
    points are removed as links, never followed; the workspace root itself
    must not be a junction.
    """
    if _is_reparse(DEVFEST_WORKSPACE):
        raise RuntimeError(
            f"refusing to restore: {DEVFEST_WORKSPACE} is a junction/symlink "
            "(a malicious workspace could redirect deletion outside)"
        )
    import shutil

    DEVFEST_WORKSPACE.mkdir(parents=True, exist_ok=True)
    for item in DEVFEST_WORKSPACE.iterdir():
        if _is_reparse(item):
            _remove_link(item)
        elif item.is_dir():
            _safe_rmtree(item)
        else:
            _force_unlink(item)
    for item in DEVFEST_PRISTINE.iterdir():
        if item.is_dir():
            shutil.copytree(item, DEVFEST_WORKSPACE / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, DEVFEST_WORKSPACE)


def verify_files_unchanged(before: dict, after: dict, allow: tuple) -> dict:
    """Tamper-evidence gate over the before/after SHA256 maps.

    Returns {"tests_tampered": bool, "forbidden_changed": list, "files_changed": list}.
    A file is 'changed' when its hash differs or it appears/disappears; the
    verdict is then cross-checked against the live on-disk file by the caller.
    """
    changed = sorted(
        k
        for k in set(before) | set(after)
        if before.get(k, {}).get("sha256") != after.get(k, {}).get("sha256")
    )
    tests_tampered = any(k.startswith("tests/") for k in changed)
    forbidden = [k for k in changed if k not in allow]
    return {
        "tests_tampered": tests_tampered,
        "forbidden_changed": forbidden,
        "files_changed": changed,
    }


def reverify_on_disk(after: dict) -> list:
    """Re-hash the recorded files now; any drift (map vs disk) is reported."""
    drifted = []
    for rel, meta in after.items():
        p = DEVFEST_WORKSPACE / rel
        try:
            if sha256_of(p) != meta["sha256"]:
                drifted.append(rel)
        except OSError:
            drifted.append(rel)
    return drifted


def acceptance(before: dict, after: dict, pre: dict, post: dict) -> dict:
    """Full independent acceptance for one run (all gates must hold)."""
    if not isinstance(pre.get("exit_code"), int) or not isinstance(post.get("exit_code"), int):
        raise ValueError("pytest exit codes must be ints (string codes are forged)")
    files = verify_files_unchanged(before, after, MUTABLE_ALLOWLIST)
    drift = reverify_on_disk(after)
    ok = bool(  # bool() so the `and` chain never leaks a list as the verdict
        pre["exit_code"] != 0  # bug present before
        and post["exit_code"] == 0  # fixed after, independently
        and not files["tests_tampered"]
        and not files["forbidden_changed"]
        and not drift
        and files["files_changed"]  # the agent actually fixed the source
    )
    return {
        "ok": ok,
        "bug_present_before": pre["exit_code"] != 0,
        "bug_fixed_after": post["exit_code"] == 0,
        "tests_tampered": files["tests_tampered"],
        "forbidden_changed": files["forbidden_changed"],
        "disk_drift": drift,
        **files,
    }


def display_shortened(raw: str, limit: int = 220) -> str:
    """Stage-display copy of tool args / diffs: absolute workspace/repo paths
    are shortened. Tool-arg strings are str(dict) reprs, so backslash paths
    appear doubled — cover native, forward-slash and repr-escaped forms (the
    raw evidence keeps full paths; curation redacts them)."""
    variants = []
    for base in (DEVFEST_WORKSPACE, DEVFEST_PRISTINE, PROJECT_ROOT):
        native = str(base)
        variants += [native, native.replace("\\", "/"), native.replace("\\", "\\\\")]
    out = raw
    for pfx in sorted(variants, key=len, reverse=True):
        out = out.replace(pfx, "…")
    return out[:limit]


async def run_agent_task(
    model: str, base_url: str, timeout_s: int, ev_dir: Path, on_event=None
) -> dict:
    """Run one real agent task; on_event(kind, data) streams live UI events."""
    from google.antigravity import Agent

    audit_log: list[str] = []
    tool_events: list[dict] = []
    tokens: list[str] = []

    def fire(kind: str, data) -> None:
        if on_event is not None:
            on_event(kind, data)

    config = build_config(
        model=model,
        base_url=base_url,
        workspaces=[DEVFEST_WORKSPACE],
        system_instructions=SYSTEM_INSTRUCTIONS,
        audit_log=audit_log,
    )
    (ev_dir / "config.txt").write_text(
        json.dumps(
            {
                "model": model,
                "base_url": base_url,
                "workspaces": ["<repo>/devfest_workspace"],
                "policies": [
                    "file tools: allow+when path resolves inside devfest_workspace",
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
            seen_audit = 0
            async for token in response:
                tokens.append(token)
                fire("token", token)
                while seen_audit < len(audit_log):  # policy decisions stream live
                    fire("policy", display_shortened(audit_log[seen_audit]))
                    seen_audit += 1
            while seen_audit < len(audit_log):
                fire("policy", display_shortened(audit_log[seen_audit]))
                seen_audit += 1

        async def collect_tool_calls():
            async for call in response.tool_calls:
                ev = {
                    "name": getattr(call, "name", None),
                    "args": str(getattr(call, "args", None))[:2000],
                }
                tool_events.append(ev)
                fire("tool", {"name": ev["name"], "args": display_shortened(ev["args"])})

        await asyncio.wait_for(
            asyncio.gather(collect_tokens(), collect_tool_calls()), timeout=timeout_s
        )
    elapsed = time.time() - t0

    (ev_dir / "mission.txt").write_text(MISSION, encoding="utf-8")
    (ev_dir / "tool_calls.json").write_text(json.dumps(tool_events, indent=2), encoding="utf-8")
    (ev_dir / "policy_audit.txt").write_text(
        "\n".join(audit_log) or "(no policy decisions)", encoding="utf-8"
    )
    (ev_dir / "agent_reply.txt").write_text("".join(tokens), encoding="utf-8")

    return {
        "elapsed_s": round(elapsed, 1),
        "n_tool_calls": len(tool_events),
        "tool_events": tool_events,
    }


def run_full_task(
    model: str,
    base_url: str,
    timeout_s: int,
    run_id: str,
    on_event=None,
    keep_workspace: bool = False,
) -> dict:
    """One complete, independently verified run (shared by CLI and web UI).

    Always resets the workspace to the defective baseline first (unless
    keep_workspace), so every run starts from the same reproducible state.
    Writes the audited evidence chain under artifacts/devfest-agent-execution/.
    """
    ev_dir = EVIDENCE_ROOT / f"run-{run_id}"
    ev_dir.mkdir(parents=True, exist_ok=True)

    if not keep_workspace:
        if on_event:
            on_event("stage", {"name": "restore", "text": "重置工作区到缺陷基线"})
        restore_devfest_baseline()
    before = workspace_state(DEVFEST_WORKSPACE)
    (ev_dir / "workspace_before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")

    if on_event:
        on_event("stage", {"name": "pytest_before", "text": "独立 pytest（期望：红）"})
    pre = run_pytest_independently(DEVFEST_WORKSPACE)
    (ev_dir / "pytest_before.json").write_text(json.dumps(pre, indent=2), encoding="utf-8")
    if on_event:
        on_event(
            "pytest",
            {
                "phase": "before",
                "exit": pre["exit_code"],
                "tail": pre["stdout"].strip().splitlines()[-1] if pre["stdout"] else "",
            },
        )

    if on_event:
        on_event("stage", {"name": "agent", "text": "Agent 开始任务（本地 Gemma 4）"})
    result = asyncio.run(run_agent_task(model, base_url, timeout_s, ev_dir, on_event))

    after = workspace_state(DEVFEST_WORKSPACE)
    (ev_dir / "workspace_after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")

    if on_event:
        on_event("stage", {"name": "pytest_after", "text": "独立 pytest 验证（期望：全绿）"})
    post = run_pytest_independently(DEVFEST_WORKSPACE)
    (ev_dir / "pytest_after.json").write_text(json.dumps(post, indent=2), encoding="utf-8")
    if on_event:
        on_event(
            "pytest",
            {
                "phase": "after",
                "exit": post["exit_code"],
                "tail": post["stdout"].strip().splitlines()[-1] if post["stdout"] else "",
            },
        )

    # Diff only the whitelisted source files: a whole-tree --no-index diff
    # would drown the fix in __pycache__/.pytest_cache binary noise. The full
    # change set (hash-verified, cache dirs excluded) is in files_changed.
    diff_parts = []
    for rel in MUTABLE_ALLOWLIST:
        p_pristine = DEVFEST_PRISTINE / rel
        p_now = DEVFEST_WORKSPACE / rel
        if p_pristine.is_file() and p_now.is_file():
            diff_parts.append(
                subprocess.run(
                    ["git", "diff", "--no-index", "--", str(p_pristine), str(p_now)],
                    capture_output=True,
                    text=True,
                ).stdout
            )
    diff = "\n".join(diff_parts)
    (ev_dir / "diff.patch").write_text(diff or "", encoding="utf-8")

    acc = acceptance(before, after, pre, post)
    summary = {
        "run_id": run_id,
        "model": model,
        "elapsed_s": result["elapsed_s"],
        "n_tool_calls": result["n_tool_calls"],
        "tool_names": [t.get("name") for t in result["tool_events"]],
        **acc,
    }
    (ev_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if on_event:
        disp_patch = display_shortened(diff or "", 8000)
        on_event("diff", {"files": acc["files_changed"], "patch": disp_patch})
        on_event("done", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None, help="label for this run (default: timestamp)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--no-restore", action="store_true", help="do not reset the defect baseline first"
    )
    args = parser.parse_args()

    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")

    def cli_event(kind: str, data) -> None:
        if kind == "pytest":
            print(f"[{run_id}] pytest {data['phase']}: exit={data['exit']}")
        elif kind == "tool":
            print(f"[{run_id}] tool: {data['name']}")

    summary = run_full_task(
        args.model,
        args.base_url,
        args.timeout,
        run_id,
        on_event=cli_event,
        keep_workspace=args.no_restore,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
