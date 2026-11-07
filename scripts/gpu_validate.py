"""Opt-in PHYSICAL GPU validation — produces honest hardware evidence.

Never fabricates a GPU result: every proof is read from the live machine
(Ollama binary, model store, server log, a real agent run). Anything that
cannot be proven is reported BLOCKED, not skipped-green.

Collects (artifacts/gpu-validation/<run-id>/):
  env.json          ollama version, model digest, backend proof, timestamps
  agent/            full red->green demo chain (only with --with-agent)
  REPORT.md         human-readable PASS/FAIL/BLOCKED summary (path-redacted)

Usage:
    python scripts/gpu_validate.py --model gemma4:26b --with-agent
    python scripts/gpu_validate.py --no-agent        # environment probe only

Exit code 0 only when every requested proof holds.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from publish_export import redact_text  # noqa: E402

OLLAMA_CANDIDATES = (
    Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
    Path("C:/Program Files/Ollama/ollama.exe"),
    Path("ollama.exe"),
)
SERVER_LOGS = (
    Path.home() / "AppData/Local/Ollama/server.log",
    ROOT / "artifacts/rc1-serve.log",
)
AGENT_CHAIN = (
    "summary.json",
    "tool_calls.json",
    "policy_audit.txt",
    "pytest_before.json",
    "pytest_after.json",
    "workspace_before.json",
    "workspace_after.json",
    "git-diff.patch",
    "agent_reply.txt",
    "mission.txt",
)


def _run(cmd: list[str], timeout: int = 60) -> dict:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": p.returncode == 0, "stdout": p.stdout, "stderr": p.stderr}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def find_ollama() -> Path | None:
    for cand in OLLAMA_CANDIDATES:
        if cand.exists():
            return cand
    which = _run(["where", "ollama"])
    if which["ok"]:
        first = which["stdout"].strip().splitlines()[0]
        if first:
            return Path(first)
    return None


def ollama_version(exe: Path) -> str:
    r = _run([str(exe), "--version"])
    return r["stdout"].strip() or r["stderr"].strip() or "unknown"


def model_info(exe: Path, model: str) -> dict:
    """Presence + digest straight from the local model store."""
    info: dict = {"listed": False, "digest": None, "size": None}
    r = _run([str(exe), "list"])
    for line in r["stdout"].splitlines():
        parts = line.split()
        if parts and (parts[0] == model or parts[0].startswith(model.split(":")[0] + ":")):
            info["listed"] = True
            info["digest"] = parts[1] if len(parts) > 1 else None
            info["size"] = parts[2] if len(parts) > 2 else None
            break
    if info["listed"]:
        show = _run([str(exe), "show", model])
        m = re.search(r"digest\s+[0-9a-f:]+", show["stdout"], re.IGNORECASE) or re.search(
            r"sha256[0-9a-f:]+", show["stdout"]
        )
        if m:
            info["digest"] = m.group(0).split(None, 1)[-1]
    return info


def backend_proof() -> dict:
    """Backend evidence from the Ollama server log (not GPU utilization)."""
    for log in SERVER_LOGS:
        if not log.exists():
            continue
        txt = log.read_text(encoding="utf-8", errors="replace")
        last = txt.rfind("inference compute")
        if last >= 0:
            line = txt[last : last + 400]
            backend, compute = "unknown", None
            if "library=ROCm" in line:
                backend = "ROCm"
            elif "library=Vulkan" in line:
                backend = "Vulkan"
            elif "library=CUDA" in line:
                backend = "CUDA"
            elif "library=CPU" in line:
                backend = "CPU"
            m = re.search(r"compute=(\S+)", line)
            if m:
                compute = m.group(1)
            return {
                "status": "PASS",
                "source": log.name,
                "backend": backend,
                "compute": compute,
                "line": line.strip()[:200],
            }
    return {"status": "BLOCKED", "reason": "no server log with an 'inference compute' line found"}


def run_agent(run_id: str, model: str, exe: Path) -> dict:
    """One real demo run through src/demo_agent.py; returns its summary."""
    print(f"[gpu-validate] starting agent run {run_id} (this can take 2-10 min)...")
    t0 = time.time()
    r = _run(
        [
            sys.executable,
            "-I",
            str(ROOT / "src" / "demo_agent.py"),
            "--run-id",
            run_id,
            "--model",
            model,
        ],
        timeout=1200,
    )
    elapsed = round(time.time() - t0, 1)
    ev = ROOT / "artifacts" / "agent-execution" / f"run-{run_id}"
    summary_path = ev / "summary.json"
    if not summary_path.exists():
        return {
            "status": "FAIL",
            "elapsed_s": elapsed,
            "reason": r["stderr"][-500:] or "no summary",
        }
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary |= {
        "status": "PASS" if summary.get("bug_fixed_after") else "FAIL",
        "elapsed_s": elapsed,
    }
    return summary


def changed_file_hashes(ev: Path) -> dict:
    """sha256 of every workspace file that changed during the run."""
    out: dict = {}
    try:
        before = json.loads((ev / "workspace_before.json").read_text(encoding="utf-8"))
        after = json.loads((ev / "workspace_after.json").read_text(encoding="utf-8"))
        for k in sorted(set(before) | set(after)):
            b, a = before.get(k), after.get(k)
            if (b or {}).get("sha256") != (a or {}).get("sha256"):
                out[k] = {"before": (b or {}).get("sha256"), "after": (a or {}).get("sha256")}
    except (OSError, json.JSONDecodeError):
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="opt-in physical GPU validation")
    ap.add_argument("--model", default="gemma4:26b")
    g = ap.add_mutually_exclusive_group()
    g.add_argument(
        "--with-agent", action="store_true", help="run the live red->green demo (default)"
    )
    g.add_argument("--no-agent", action="store_true", help="environment probe only")
    args = ap.parse_args()
    with_agent = not args.no_agent

    run_id = f"gpuval-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = ROOT / "artifacts" / "gpu-validation" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "run_id": run_id,
        "model": args.model,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    exe = find_ollama()
    if exe is None:
        report["ollama"] = {"status": "BLOCKED", "reason": "ollama.exe not found"}
    else:
        report["ollama"] = {"status": "PASS", "version": ollama_version(exe)}

    report["model_info"] = (
        model_info(exe, args.model)
        if exe
        else {"listed": False, "digest": None, "status": "BLOCKED"}
    )
    report["model_info"]["status"] = "PASS" if report["model_info"].get("listed") else "BLOCKED"
    report["backend"] = backend_proof()

    agent_dir = None
    if with_agent:
        if not report["model_info"].get("listed"):
            report["agent"] = {"status": "BLOCKED", "reason": f"model {args.model} not present"}
        else:
            summary = run_agent(run_id, args.model, exe)
            report["agent"] = summary
            ev = ROOT / "artifacts" / "agent-execution" / f"run-{run_id}"
            if ev.is_dir():
                agent_dir = out_dir / "agent"
                agent_dir.mkdir()
                for name in AGENT_CHAIN:
                    src = ev / name
                    if src.exists():
                        text, _ = redact_text(src.read_text(encoding="utf-8", errors="replace"))
                        (agent_dir / name).write_text(text, encoding="utf-8")
                report["agent"]["changed_file_hashes"] = changed_file_hashes(ev)

    # verdict: requested proofs must all be PASS
    verdicts = [
        report["ollama"]["status"],
        report["model_info"]["status"],
        report["backend"]["status"],
    ]
    if with_agent:
        verdicts.append(report["agent"].get("status", "BLOCKED"))
    overall = (
        "PASS"
        if all(v == "PASS" for v in verdicts)
        else ("FAIL" if "FAIL" in verdicts else "BLOCKED")
    )
    report["overall"] = overall
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    env_text, _ = redact_text(json.dumps(report, indent=2, ensure_ascii=False))
    (out_dir / "env.json").write_text(env_text, encoding="utf-8")

    lines = [
        f"# Physical GPU validation — {run_id}",
        "",
        f"- overall: **{overall}**",
        f"- ollama: {report['ollama'].get('version', report['ollama'].get('reason'))}",
        f"- model: `{args.model}` digest `{report['model_info'].get('digest')}`",
        f"- backend: {report['backend'].get('backend')} compute={report['backend'].get('compute')} "
        f"(from {report['backend'].get('source', report['backend'].get('reason'))})",
    ]
    if with_agent:
        a = report.get("agent", {})
        lines += [
            f"- agent demo: {a.get('status')} — red before="
            f"{a.get('bug_present_before')}, green after={a.get('bug_fixed_after')}, "
            f"{a.get('n_tool_calls')} tool calls, {a.get('elapsed_s')}s",
            f"- changed files: {', '.join(a.get('files_changed', [])) or '(none)'}",
        ]
    lines.append(f"\nFull JSON: `env.json`{'; agent chain in `agent/`' if agent_dir else ''}")
    (out_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({k: report[k] for k in ("run_id", "overall")}, indent=2))
    print(f"evidence: {out_dir}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
