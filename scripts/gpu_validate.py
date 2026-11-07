"""Opt-in PHYSICAL GPU validation v2 — attribution-first evidence chain.

A GPU PASS requires EVERY link to be fresh and attributable to THIS run:

  1. model tag syntactically valid, present in the local model store
  2. a live inference probe on OUR server instance (--own-server, default):
     tiny generate -> /api/ps shows the exact tag with a full 64-hex digest
     and 100% VRAM residency
  3. backend identity from the process table: the llama-server child serving
     the model must have ROCm-family DLLs loaded (hip/rocblas), not
     Vulkan/CUDA
  4. GPU identity + compute architecture from OUR server's captured log
     ("inference compute" line: library/compute/name) — never from a stale
     shared server.log (T1 accepted such a line and it was 3 days old)
  5. agent stage (--with-agent): subprocess exit 0 AND chain validation —
     pytest red BEFORE / green AFTER from the run's own JSON, summary flags,
     files_changed equal to the SHA256 diff of the workspace maps, and each
     recorded after-hash re-verified against the file on disk right now

Anything unprovable is BLOCKED, not skipped-green. --no-agent reports an
explicit ENV_PROBE result, never a full validation PASS.

Usage:
    python scripts/gpu_validate.py --model gemma4:26b --with-agent   # full gate
    python scripts/gpu_validate.py --no-agent                        # env probe only

Exit codes: 0 PASS (full, or ENV_PROBE_PASS), 1 FAIL, 2 BLOCKED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from publish_export import redact_text  # noqa: E402

DEFAULT_BASE = "http://127.0.0.1:11434"
OWN_PORT = 11577
OWN_BASE = f"http://127.0.0.1:{OWN_PORT}"
# \Z (not $): '$' would accept a trailing newline, diverging from the
# workflow's inline gate (independent review T1.1 F-5)
RE_MODEL_TAG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}\Z")
RE_DIGEST = re.compile(r"^[0-9a-f]{64}$")

# DLL families that identify a backend on the llama-server runner process.
# vulkan-1.dll in the MAIN ollama.exe is just the system loader (observed on
# every install) and is explicitly NOT Vulkan evidence.
ROCM_DLLS = ("libhipblas", "hipblas", "rocblas", "amdhip64", "roczz")
CUDA_DLLS = ("cudart", "nvcuda")
RUNNER_IMAGE = "llama-server"

# tasklist /m pattern syntax: a '*' is only legal as the LAST character —
# "no characters are allowed after '*' in /M" (independent review T1.1 F-1;
# three of the original patterns silently errored). Keep patterns either
# exact names or trailing-star prefixes.
TASKLIST_DLL_PATTERNS = (
    "libhipblas.dll",
    "rocblas.dll",
    "amdhip64*",  # e.g. amdhip64_7.dll
    "hipblas*",
    "roczz*",
    "vulkan-1.dll",
    "cudart*",  # e.g. cudart64_12.dll
)

AGENT_CHAIN_FILES = (
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


# --------------------------------------------------------------- pure helpers
def validate_model_tag(tag: str) -> tuple[bool, str]:
    """Strict model-tag gate: one line, charset-bounded, no shell metachars."""
    if not isinstance(tag, str) or not tag:
        return False, "empty model tag"
    if not RE_MODEL_TAG.match(tag):
        return (
            False,
            f"model tag must match ^[a-zA-Z0-9][a-zA-Z0-9._:/-]{{0,127}}$ (got {tag!r})",
        )
    return True, ""


def parse_inference_compute(own_log_text: str) -> dict:
    """Parse OUR server log for the runner-discovery line."""
    last = own_log_text.rfind("inference compute")
    if last < 0:
        return {"status": "BLOCKED", "reason": "no 'inference compute' line in owned server log"}
    line = own_log_text[last : last + 400]
    out: dict = {"status": "PASS", "line": line.strip()[:240]}
    if "library=ROCm" in line:
        out["backend"] = "ROCm"
    elif "library=Vulkan" in line:
        out["backend"] = "Vulkan"
    elif "library=CUDA" in line:
        out["backend"] = "CUDA"
    elif "library=CPU" in line:
        out["backend"] = "CPU"
    else:
        return {
            "status": "BLOCKED",
            "reason": "backend not identified in discovery line",
            "line": line[:240],
        }
    m = re.search(r"compute=(\S+)", line)
    out["compute"] = m.group(1) if m else None
    # the human GPU name lives in description="…"; name= is the device label
    m = re.search(r'description="([^"]+)"', line) or re.search(r"name=(\S+)", line)
    out["gpu_name"] = m.group(1) if m else None
    if not out["compute"]:
        out["status"] = "BLOCKED"
        out["reason"] = "compute architecture missing from discovery line"
    return out


def check_ps_entry(ps_json: dict, tag: str) -> dict:
    """/api/ps after a live probe must show exactly our tag, GPU-resident."""
    try:
        models = ps_json.get("models", [])
    except AttributeError:
        return {"status": "BLOCKED", "reason": "malformed /api/ps payload"}
    entry = next((m for m in models if m.get("name") == tag), None)
    if entry is None:
        return {
            "status": "FAIL",
            "reason": f"model {tag!r} not resident after the live probe",
            "resident": [m.get("name") for m in models],
        }
    digest = entry.get("digest", "")
    size, vram = entry.get("size", 0), entry.get("size_vram", 0)
    out: dict = {"status": "PASS", "digest": digest, "size": size, "size_vram": vram}
    if not RE_DIGEST.match(str(digest)):
        return out | {"status": "FAIL", "reason": f"digest not a full sha256: {digest!r}"}
    if not size or vram != size:
        return out | {
            "status": "FAIL",
            "reason": f"not fully GPU-resident (size={size}, size_vram={vram})",
        }
    return out


def classify_backend(tasklist_rows: list[dict]) -> dict:
    """Classify the backend from which runner processes load which DLLs.

    tasklist_rows: [{'image': 'llama-server.exe', 'pid': '123', 'modules': [...]}]
    """
    runners = [r for r in tasklist_rows if RUNNER_IMAGE in r.get("image", "").lower()]
    if not runners:
        return {
            "status": "BLOCKED",
            "reason": f"no {RUNNER_IMAGE} process found (model not being served)",
        }
    rocm, cuda, vulkan = [], [], []
    for r in runners:
        for mod in r.get("modules", []):
            low = mod.lower()
            if any(d in low for d in ROCM_DLLS):
                rocm.append(f"{r['image']}(pid {r['pid']}): {mod}")
            elif any(d in low for d in CUDA_DLLS):
                cuda.append(f"{r['image']}(pid {r['pid']}): {mod}")
            elif "vulkan" in low:
                vulkan.append(f"{r['image']}(pid {r['pid']}): {mod}")
    if rocm:
        return {"status": "PASS", "backend": "ROCm", "evidence": rocm[:4]}
    if cuda:
        return {"status": "PASS", "backend": "CUDA", "evidence": cuda[:4]}
    if vulkan:
        return {"status": "PASS", "backend": "Vulkan", "evidence": vulkan[:4]}
    return {
        "status": "BLOCKED",
        "reason": f"{RUNNER_IMAGE} running but no backend-identifying DLLs found",
    }


def _safe_rel_key(key: str) -> bool:
    """Changed-file keys must be plain workspace-relative paths."""
    if not key or ":" in key or key.startswith(("/", "\\")):
        return False
    return ".." not in key.replace("\\", "/").split("/")


def validate_agent_chain(
    ev: Path, workspace: Path | None = None, protected: tuple[str, ...] = ("tests/",)
) -> dict:
    """Full red->green + consistency validation of one agent run directory.

    `protected`: path prefixes the agent must NOT touch. The demo task is
    "fix the source, don't touch the tests" — a run that goes green by
    gutting or deleting the workspace's own tests is a cheat, not a fix
    (independent review T1.1 F-3).
    """
    missing = [f for f in AGENT_CHAIN_FILES if f != "git-diff.patch" and not (ev / f).exists()]
    if missing:
        return {"status": "BLOCKED", "reason": f"missing evidence files: {missing}"}

    def load(name: str) -> dict:
        return json.loads((ev / name).read_text(encoding="utf-8", errors="replace"))

    try:
        summary = load("summary.json")
        before = load("workspace_before.json")
        after = load("workspace_after.json")
        pre = load("pytest_before.json")
        post = load("pytest_after.json")
        tools = load("tool_calls.json")
    except (json.JSONDecodeError, OSError) as e:
        return {"status": "BLOCKED", "reason": f"unparsable evidence: {type(e).__name__}: {e}"}

    checks: list[str] = []

    def require(cond: bool, why: str) -> bool:
        if not cond:
            checks.append(why)
        return cond

    def _exit_int(obj: dict, name: str) -> bool:
        """Exit codes must be genuine ints (a string "0" is forged evidence)."""
        return isinstance(obj.get("exit_code"), int) and not isinstance(obj["exit_code"], bool)

    ok = True
    ok &= require(_exit_int(pre, "pytest_before"), "pytest_before exit_code is not an int")
    ok &= require(_exit_int(post, "pytest_after"), "pytest_after exit_code is not an int")
    if _exit_int(pre, "pytest_before"):
        ok &= require(pre["exit_code"] != 0, "pytest_before is green — no genuine RED state")
    if _exit_int(post, "pytest_after"):
        ok &= require(post["exit_code"] == 0, "pytest_after is not green")
    ok &= require(
        summary.get("bug_present_before") is True, "summary lacks bug_present_before=true"
    )
    ok &= require(summary.get("bug_fixed_after") is True, "summary lacks bug_fixed_after=true")
    ok &= require(isinstance(tools, list) and len(tools) > 0, "no tool calls recorded")

    # SHA256 map diff must equal summary.files_changed
    changed = sorted(
        k
        for k in set(before) | set(after)
        if (before.get(k) or {}).get("sha256") != (after.get(k) or {}).get("sha256")
    )
    ok &= require(
        changed == sorted(summary.get("files_changed", [])),
        f"files_changed {summary.get('files_changed')} != SHA256-map diff {changed}",
    )
    for k in changed:
        ok &= require(_safe_rel_key(k), f"changed key is not a safe relative path: {k!r}")
        ok &= require(
            not k.replace("\\", "/").startswith(protected),
            f"protected path was modified (going green by touching tests is a cheat, "
            f"not a fix): {k!r}",
        )
    ok &= require(
        bool(changed) and any(not k.replace("\\", "/").startswith(protected) for k in changed),
        "no non-test file changed — nothing outside the protected set was fixed",
    )

    # recorded after-hashes must match the files on disk right now (fresh,
    # not a copied historical PASS)
    if workspace is not None and workspace.is_dir():
        for k in changed:
            f = workspace / k
            try:
                actual = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
            except OSError:
                actual = None
            recorded = (after.get(k) or {}).get("sha256")
            ok &= require(
                actual == recorded,
                f"recorded after-hash for {k} does not match the file on disk",
            )

    if not ok:
        return {"status": "FAIL", "reasons": checks, "files_changed": changed}
    return {
        "status": "PASS",
        "files_changed": changed,
        "n_tool_calls": len(tools),
        "elapsed_s": summary.get("elapsed_s"),
    }


# --------------------------------------------------------------- host probes
def _http_json(url: str, payload: dict | None = None, timeout: int = 240) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _run(cmd: list[str], timeout: int = 90) -> dict:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # localized tool output must not kill reader threads
            timeout=timeout,
        )
        return {"ok": p.returncode == 0, "stdout": p.stdout, "stderr": p.stderr, "rc": p.returncode}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "rc": -1}


def find_ollama() -> Path | None:
    for cand in (
        Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
        Path(
            "C:/Program Files/Ollama/ollama.exe",
        ),
    ):
        if cand.exists():
            return cand
    which = _run(["where", "ollama"])
    if which["ok"]:
        first = which["stdout"].strip().splitlines()
        if first:
            return Path(first[0])
    return None


def ollama_version(exe: Path) -> str:
    r = _run([str(exe), "--version"])
    return (r["stdout"] + r["stderr"]).strip() or "unknown"


def model_in_store(exe: Path, model: str) -> dict:
    r = _run([str(exe), "list"])
    for line in r["stdout"].splitlines():
        parts = line.split()
        if parts and parts[0] == model:
            return {"status": "PASS", "row": line.strip()}
    return {"status": "BLOCKED", "reason": f"model {model!r} not in 'ollama list'"}


def tasklist_modules(dll_patterns: list[str]) -> list[dict]:
    """Rows of processes per DLL pattern via `tasklist /m` (CSV).

    A pattern whose invocation errors (bad syntax, tool missing) is recorded
    in a `__probe_errors__` row instead of being silently dropped — a dead
    probe must be visible, never a silent coverage hole (review T1.1 F-1).
    """
    rows: dict[tuple[str, str], set[str]] = {}
    for pat in dll_patterns:
        r = _run(["tasklist", "/m", pat, "/fo", "csv"])
        if r["rc"] != 0 and not r["stdout"].strip():
            rows.setdefault(("__probe_errors__", "0"), set()).add(
                f"{pat}: rc={r['rc']} stderr={r['stderr'][:120].strip()}"
            )
            continue
        for line in r["stdout"].splitlines()[1:]:
            cells = [c.strip('"') for c in line.split('","')]
            # localized "no tasks match" banners fail the 2-cell CSV shape
            if len(cells) >= 2 and cells[0] and cells[0] != '"':
                rows.setdefault((cells[0], cells[1]), set()).add(pat)
    return [
        {"image": img, "pid": pid, "modules": sorted(mods)}
        for (img, pid), mods in sorted(rows.items())
    ]


def gpu_names() -> list[str]:
    r = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-CimInstance Win32_VideoController).Name",
        ]
    )
    return [ln.strip() for ln in r["stdout"].splitlines() if ln.strip()]


class OwnedServer:
    """A private `ollama serve` on OWN_PORT with its stdout captured."""

    def __init__(self, exe: Path, log_path: Path):
        self.exe, self.log_path = exe, log_path
        self.proc: subprocess.Popen | None = None
        self.log_file = None

    def start(self) -> OwnedServer:
        # politely unload resident models from any shared server first
        # (keep_alive=0 evicts without generating; "*" is not a valid tag)
        try:
            for m in _http_json(f"{DEFAULT_BASE}/api/ps", timeout=10).get("models", []):
                try:
                    _http_json(
                        f"{DEFAULT_BASE}/api/generate",
                        {"model": m.get("name"), "keep_alive": 0},
                        timeout=120,
                    )
                except Exception:
                    pass
        except Exception:
            pass
        self.log_file = open(self.log_path, "wb")
        env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{OWN_PORT}"}
        self.proc = subprocess.Popen(
            [str(self.exe), "serve"],
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        deadline = time.time() + 180
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"owned ollama serve exited rc={self.proc.returncode}; see {self.log_path}"
                )
            try:
                _http_json(f"{OWN_BASE}/api/version", timeout=3)
                return self
            except Exception:
                time.sleep(1)
        raise RuntimeError(f"owned ollama serve did not become ready; see {self.log_path}")

    def wait_discovery_line(self, timeout: int = 240) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
            if "inference compute" in text:
                return text
            time.sleep(2)
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def stop(self) -> None:
        """Idempotent teardown that ALWAYS redacts the log.

        Kills the whole process tree (`taskkill /T`) — TerminateProcess on
        ollama.exe alone can orphan llama-server children holding VRAM — and
        never lets a signal error skip the redaction pass (review T1.1 F-2).
        """
        try:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                    self.proc.wait(timeout=15)
                except Exception:
                    pass
            if self.proc and self.proc.poll() is None:
                _run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], timeout=30)
                try:
                    self.proc.wait(timeout=15)
                except Exception:
                    pass
        finally:
            if self.log_file:
                try:
                    self.log_file.close()
                except OSError:
                    pass
                self.log_file = None
            # the log carries absolute local paths (model blobs) — ship it redacted
            try:
                raw = self.log_path.read_text(encoding="utf-8", errors="replace")
                self.log_path.write_text(redact_text(raw)[0], encoding="utf-8")
            except OSError:
                pass


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="opt-in physical GPU validation (attribution-first)")
    ap.add_argument("--model", default="gemma4:26b")
    g = ap.add_mutually_exclusive_group()
    g.add_argument(
        "--with-agent", action="store_true", help="full validation incl. live agent (default)"
    )
    g.add_argument(
        "--no-agent", action="store_true", help="environment probe only (never a full PASS)"
    )
    ap.add_argument(
        "--require-backend",
        default="ROCm",
        choices=["ROCm", "CUDA", "Vulkan"],
        help="backend that must be proven live (default ROCm)",
    )
    ap.add_argument(
        "--shared-server",
        action="store_true",
        help="use the existing 11434 server instead of an owned instance "
        "(compute-arch attribution will likely be BLOCKED for a stale log)",
    )
    args = ap.parse_args()

    run_id = f"gpuval-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = ROOT / "artifacts" / "gpu-validation" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    scope = "environment-probe" if args.no_agent else "full"
    rep: dict = {
        "run_id": run_id,
        "scope": scope,
        "model": args.model,
        "require_backend": args.require_backend,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    ok_tag, why = validate_model_tag(args.model)
    if not ok_tag:
        _write(
            out_dir, rep | {"model_tag": {"status": "BLOCKED", "reason": why}, "overall": "BLOCKED"}
        )
        print(f"BLOCKED: {why}")
        return 2

    exe = find_ollama()
    if exe is None:
        _write(
            out_dir,
            rep
            | {
                "ollama": {"status": "BLOCKED", "reason": "ollama.exe not found"},
                "overall": "BLOCKED",
            },
        )
        return 2

    rep["ollama_version"] = ollama_version(exe)
    rep["model_in_store"] = model_in_store(exe, args.model)
    rep["gpus"] = gpu_names()

    base = DEFAULT_BASE
    owned: OwnedServer | None = None
    try:
        if not args.shared_server:
            owned = OwnedServer(exe, out_dir / "owned-server.log")
            try:
                owned.start()
            except Exception:
                # never leak the serve process / an unredacted log on the
                # readiness-failure path (review T1.1 F-2)
                owned.stop()
                raise
            base = OWN_BASE
            rep["owned_server"] = {"status": "PASS", "base_url": base, "log": "owned-server.log"}

        # live probe: tiny generate on the server under test, then residency
        t0 = time.time()
        _http_json(
            f"{base}/api/generate",
            {"model": args.model, "prompt": "ok", "stream": False, "options": {"num_predict": 1}},
        )
        rep["live_probe"] = {"status": "PASS", "latency_s": round(time.time() - t0, 1)}
        rep["residency"] = check_ps_entry(_http_json(f"{base}/api/ps"), args.model)

        mods = tasklist_modules(list(TASKLIST_DLL_PATTERNS))
        (out_dir / "tasklist_modules.json").write_text(json.dumps(mods, indent=2), encoding="utf-8")
        probe_errs = next((r["modules"] for r in mods if r["image"] == "__probe_errors__"), [])
        if probe_errs:
            rep["dll_probe_errors"] = probe_errs  # visible diagnostics, never silent
        rep["backend_live"] = classify_backend(mods)

        if owned is not None:
            rep["discovery_line"] = parse_inference_compute(owned.wait_discovery_line())
        else:
            rep["discovery_line"] = {
                "status": "BLOCKED",
                "reason": "shared-server mode: no attributable owned log (arch cannot be proven fresh)",
            }

        # the required backend must hold on every source that produced one
        for src in ("backend_live", "discovery_line"):
            got = rep[src].get("backend")
            if rep[src].get("status") == "PASS" and got and got != args.require_backend:
                rep[src] |= {
                    "status": "FAIL",
                    "reason": f"{src} shows {got}, required {args.require_backend}",
                }

        if args.no_agent:
            rep["agent"] = {"status": "SKIPPED", "reason": "--no-agent environment probe"}
        else:
            rep["agent"] = _agent_stage(run_id, args.model, base, out_dir)
    finally:
        if owned is not None:
            owned.stop()

    rep["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rep["overall"] = _verdict(rep, args.no_agent)
    _write(out_dir, rep)
    print(
        json.dumps({"run_id": run_id, "scope": rep["scope"], "overall": rep["overall"]}, indent=2)
    )
    print(f"evidence: {out_dir}")
    return {"PASS": 0, "ENV_PROBE_PASS": 0, "FAIL": 1}.get(rep["overall"], 2)


def _agent_stage(run_id: str, model: str, base: str, out_dir: Path) -> dict:
    print(f"[gpu-validate] agent stage {run_id} on {base} (2-10 min)...")
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
            "--base-url",
            f"{base}/v1",
        ],
        timeout=1500,
    )
    ev = ROOT / "artifacts" / "agent-execution" / f"run-{run_id}"
    stage: dict = {"subprocess_rc": r["rc"], "elapsed_s": round(time.time() - t0, 1)}
    if r["rc"] != 0:
        return stage | {
            "status": "FAIL",
            "reason": f"agent subprocess exit {r['rc']}",
            "stderr_tail": (r["stderr"] or r["stdout"])[-600:],
        }
    if not ev.is_dir():
        return stage | {"status": "BLOCKED", "reason": "agent exited 0 but no evidence dir"}
    chain = validate_agent_chain(ev, ROOT / "demo_workspace")
    agent_dir = out_dir / "agent"
    agent_dir.mkdir(exist_ok=True)
    for name in AGENT_CHAIN_FILES:
        src = ev / name
        if src.exists():
            text, _ = redact_text(src.read_text(encoding="utf-8", errors="replace"))
            (agent_dir / name).write_text(text, encoding="utf-8")
    return stage | chain


def _verdict(rep: dict, no_agent: bool) -> str:
    must = ["model_in_store", "live_probe", "residency", "backend_live", "discovery_line"]
    if not no_agent:
        must.append("agent")
    statuses = [rep.get(k, {}).get("status") for k in must]
    if "FAIL" in statuses:
        return "FAIL"
    # unknown/missing gate keys are NOT success (review T1.1 F-4): only an
    # explicit PASS on every link counts
    if any(s != "PASS" for s in statuses):
        return "ENV_PROBE_BLOCKED" if no_agent else "BLOCKED"
    return "ENV_PROBE_PASS" if no_agent else "PASS"


def _write(out_dir: Path, rep: dict) -> None:
    text, _ = redact_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str))
    (out_dir / "env.json").write_text(text, encoding="utf-8")

    def s(key: str) -> str:
        return rep.get(key, {}).get("status", "n/a")

    lines = [
        f"# Physical GPU validation — {rep.get('run_id')}",
        "",
        f"- scope: **{rep.get('scope')}**",
        f"- overall: **{rep.get('overall')}**",
        f"- required backend: {rep.get('require_backend')}",
        f"- ollama: {rep.get('ollama_version')}",
        f"- model in store: {s('model_in_store')} — {rep.get('model_in_store', {}).get('row') or rep.get('model_in_store', {}).get('reason')}",
        f"- GPUs: {'; '.join(rep.get('gpus', [])) or 'n/a'}",
        f"- live probe: {s('live_probe')} ({rep.get('live_probe', {}).get('latency_s')}s)",
        f"- residency: {s('residency')} digest={rep.get('residency', {}).get('digest')} vram={rep.get('residency', {}).get('size_vram')}/{rep.get('residency', {}).get('size')}",
        f"- backend (live DLLs): {s('backend_live')} {rep.get('backend_live', {}).get('backend', '')} — {rep.get('backend_live', {}).get('evidence') or rep.get('backend_live', {}).get('reason')}",
        f"- discovery line (owned log): {s('discovery_line')} backend={rep.get('discovery_line', {}).get('backend')} compute={rep.get('discovery_line', {}).get('compute')} gpu={rep.get('discovery_line', {}).get('gpu_name')}",
        f"- agent: {rep.get('agent', {}).get('status', 'n/a')} {rep.get('agent', {}).get('reasons') or rep.get('agent', {}).get('reason') or ''}",
        "",
        "Full JSON: `env.json`; owned server log: `owned-server.log`; agent chain in `agent/` when run.",
    ]
    (out_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
