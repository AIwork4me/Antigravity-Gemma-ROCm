"""Opt-in PHYSICAL GPU validation v2 — attribution-first evidence chain.

A GPU PASS requires EVERY link to be fresh and attributable to THIS run:

  1. model tag syntactically valid, present in the local model store, and
     its /api/ps digest extends the 12-hex id shown by `ollama list`
     (two independent sources for the model identity)
  2. a live inference probe on OUR server instance (default): tiny
     generate -> /api/ps shows the exact tag with a full 64-hex digest
     and 100% VRAM residency
  3. backend identity from the process table: a llama-server runner that
     DESCENDS from our owned server (PPID chain) must have ROCm-family
     DLLs loaded — the exact DLL names come from tasklist's module cell,
     never from the wildcard pattern we searched for — and not
     Vulkan/CUDA. DLLs of a runner that belongs to someone else's server
     prove nothing about this run: BLOCKED.
  4. GPU identity + compute architecture from OUR server's captured log
     ("inference compute" line: library/compute/name) — never from a stale
     shared server.log (T1 accepted such a line and it was 3 days old)
  5. agent stage (--with-agent): subprocess exit 0 AND chain validation —
     pytest red BEFORE / green AFTER from the run's own JSON, summary flags,
     files_changed equal to the SHA256 diff of the workspace maps, and each
     recorded after-hash re-verified against the file on disk right now
  6. run binding: code SHA, owned-server PID, owned runner PIDs, shared
     /api/ps snapshots before/after, and a seal.json hashing every evidence
     byte in the run directory

Non-interference contract (T1.2): the default run NEVER unloads models
from the shared 11434 server, NEVER stops a server it did not spawn, and
NEVER kills a process outside the tree of the PID it spawned. Evicting
resident shared models is an explicit, loud opt-in
(--evict-shared-models). Insufficient VRAM ends BLOCKED/FAIL with the
reason recorded — it never silently intervenes in shared workloads.

Anything unprovable is BLOCKED, not skipped-green. --no-agent reports an
explicit ENV_PROBE result, never a full validation PASS.

This is strong process-level evidence, NOT an unforgeable hardware proof
and NOT an OS sandbox: a sufficiently privileged local adversary could
fake parts of the chain. The trust boundary is documented in
docs/GPU_BACKEND_VALIDATION.md.

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
import socket
import subprocess
import sys
import time
import urllib.error
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
RUNNER_EXE = "llama-server.exe"  # exact match — "llama-server-evil.exe" is not ours (R1 F-11)

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


def crosscheck_model_identity(list_row: str, digest: str) -> dict:
    """`ollama list` shows a 12-hex prefix of the manifest digest; the
    /api/ps digest observed live must extend it. Two independent sources
    for the model identity — a copied /api/ps payload for tag A cannot
    masquerade as tag B (T1.2 B-7)."""
    m = re.search(r"\b([0-9a-f]{12})\b", list_row or "")
    if not m:
        return {"status": "BLOCKED", "reason": "no 12-hex id found in 'ollama list' row"}
    id12 = m.group(1)
    if not str(digest).startswith(id12):
        return {
            "status": "FAIL",
            "reason": f"/api/ps digest {str(digest)[:12]}… does not extend "
            f"'ollama list' id {id12} — model identity mismatch",
        }
    return {"status": "PASS", "id12": id12}


def _dll_family(module: str, families: tuple[str, ...]) -> bool:
    """Anchored family match (review R1 F-10): strip the extension and any
    `lib` prefix; the stem must be the family EXACTLY, or the family followed
    by a version-ish separator (`_` or a digit) as in `amdhip64_7.dll` /
    `cudart64_12.dll`. Impersonations like `myrocblas.dll` or `nvcuda-fake.dll`
    must NOT count as backend evidence."""
    stem = module.lower().rsplit(".", 1)[0]
    if stem.startswith("lib") and len(stem) > 3:
        stem = stem[3:]
    for f in families:
        if stem == f:
            return True
        if (
            stem.startswith(f)
            and len(stem) > len(f)
            and (stem[len(f)] == "_" or stem[len(f)].isdigit())
        ):
            return True
    return False


def classify_backend(tasklist_rows: list[dict]) -> dict:
    """Classify the backend from which runner processes load which DLLs.

    tasklist_rows: [{'image': 'llama-server.exe', 'pid': '123', 'modules': [...]}]
    Only rows of the exact runner image count (R1 F-11); a module counts
    only when its stem is prefixed by a known family token (R1 F-10).
    """
    runners = [r for r in tasklist_rows if r.get("image", "").lower() == RUNNER_EXE]
    if not runners:
        return {
            "status": "BLOCKED",
            "reason": f"no {RUNNER_EXE} process found (model not being served)",
        }
    rocm, cuda, vulkan = [], [], []
    for r in runners:
        for mod in r.get("modules", []):
            low = mod.lower()
            if _dll_family(low, ROCM_DLLS):
                rocm.append(f"{r['image']}(pid {r['pid']}): {mod}")
            elif _dll_family(low, CUDA_DLLS):
                cuda.append(f"{r['image']}(pid {r['pid']}): {mod}")
            elif low.startswith("vulkan"):
                vulkan.append(f"{r['image']}(pid {r['pid']}): {mod}")
    if rocm:
        return {"status": "PASS", "backend": "ROCm", "evidence": rocm[:4]}
    if cuda:
        return {"status": "PASS", "backend": "CUDA", "evidence": cuda[:4]}
    if vulkan:
        return {"status": "PASS", "backend": "Vulkan", "evidence": vulkan[:4]}
    return {
        "status": "BLOCKED",
        "reason": f"{RUNNER_EXE} running but no backend-identifying DLLs found",
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

    The third CSV cell carries the ACTUAL module name(s) that matched the
    pattern — e.g. `amdhip64_7.dll`, never the `amdhip64*` wildcard we
    searched for. Recording the pattern as if it were a module would
    overstate what was observed: a trailing-star hit is NOT proof that any
    specific DLL is loaded (T1.2 B-5). If the module cell is missing or
    unreadable ("N/A", access denied), we record nothing — never fabricate
    a name — so classification fails closed.

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
            if not (len(cells) >= 2 and cells[0] and cells[0] != '"'):
                continue
            mods = rows.setdefault((cells[0], cells[1]), set())
            if len(cells) < 3:
                continue  # no module cell -> fail closed, no fabricated names
            for mod in cells[2].split(","):
                mod = mod.strip().strip('"')
                if mod.lower().endswith(".dll"):  # drops "N/A" / banners
                    mods.add(mod)
    return [
        {"image": img, "pid": pid, "modules": sorted(mods)}
        for (img, pid), mods in sorted(rows.items())
    ]


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Is something already serving on this loopback port?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def snapshot_shared_ps(base: str = DEFAULT_BASE) -> dict:
    """READ-ONLY snapshot of the shared server's resident models.

    Non-destructive by design: recorded before/after a validation run to
    PROVE the default run never evicted anyone else's models (T1.2 1.7).
    An unreachable shared server is BLOCKED-as-context, never an error.
    """
    try:
        ps = _http_json(f"{base}/api/ps", timeout=10)
        names = sorted(str(m.get("name", "?")) for m in ps.get("models", []))
        return {"status": "PASS", "resident": names}
    except Exception as e:
        return {"status": "BLOCKED", "reason": f"shared server unreachable: {type(e).__name__}"}


def process_snapshot() -> list[dict]:
    """[{pid, ppid, name}] for every process, from one CIM query.

    This is the attribution backbone: the PPID chain ties a llama-server
    runner (and its DLL evidence) to the owned `ollama serve` PID we
    spawned. If the query yields nothing, ownership cannot be proven and
    callers must BLOCK, not guess.
    """
    r = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | ForEach-Object "
            "{ '{0},{1},{2}' -f $_.ProcessId, $_.ParentProcessId, $_.Name }",
        ],
        timeout=60,
    )
    out: list[dict] = []
    for line in r["stdout"].splitlines():
        line = line.strip()
        parts = line.split(",", 2)
        if len(parts) != 3:
            continue
        pid_s, ppid_s, name = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if pid_s.isdigit() and ppid_s.isdigit() and name:
            out.append({"pid": int(pid_s), "ppid": int(ppid_s), "name": name})
    return out


def descendants_of(pid: int, procs: list[dict]) -> set[int]:
    """Transitive set of descendant PIDs (excludes the root). Handles any
    spawn depth, so an extra shim layer between serve and runner still
    attributes correctly."""
    children: dict[int, list[int]] = {}
    for p in procs:
        children.setdefault(p["ppid"], []).append(p["pid"])
    seen: set[int] = set()
    frontier = [pid]
    while frontier:
        cur = frontier.pop()
        for child in children.get(cur, []):
            if child not in seen:
                seen.add(child)
                frontier.append(child)
    return seen


def attribute_runner(
    owned_pid: int | None, tasklist_rows: list[dict], procs: list[dict] | None = None
) -> dict:
    """Classify the backend ONLY from runners we can attribute.

    With an owned server, DLL evidence from a llama-server that does not
    descend from our PID proves someone ELSE'S backend — BLOCKED. In
    --shared-server mode there is no owned PID, so the result is annotated
    as unattributed (and the shared mode already BLOCKs the discovery line,
    keeping a full PASS unreachable there).

    `procs` lets the caller pass a process snapshot taken BEFORE the DLL
    probe, so attribution and module rows describe (nearly) the same
    instant — joining two far-apart snapshots re-opens a PID-reuse window
    (review R1 F-5). When omitted, a fresh snapshot is taken.
    """
    if procs is None:
        procs = process_snapshot()
    runners = [p for p in procs if p["name"].lower() == RUNNER_EXE]
    if owned_pid is None:
        cls = classify_backend(tasklist_rows)
        cls["attribution"] = "shared-server mode: runner ownership not provable"
        return cls
    ours = [p for p in runners if p["pid"] in descendants_of(owned_pid, procs)]
    if not ours:
        return {
            "status": "BLOCKED",
            "reason": (
                f"no {RUNNER_EXE} process descends from the owned server "
                f"pid {owned_pid} — DLL evidence cannot be attributed to this run"
            ),
        }
    ours_pids = {str(p["pid"]) for p in ours}
    rows = [r for r in tasklist_rows if str(r.get("pid")) in ours_pids]
    cls = classify_backend(rows)
    cls["owned_runner_pids"] = sorted(ours_pids)
    return cls


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


class PortOwnedError(RuntimeError):
    """OWN_PORT is already serving — attribution would be ambiguous, and we
    never kill or share a server we did not spawn (T1.2 1.2/1.6)."""


class OwnedServer:
    """A private `ollama serve` on OWN_PORT with its stdout captured.

    Non-interference contract (T1.2 Phase 1 — owned-server mode, the default):
      - start() NEVER touches the shared 11434 server. Evicting its resident
        models (keep_alive=0) interrupts other workloads and is a separate,
        explicit opt-in: evict_shared_models(), wired to
        --evict-shared-models, off by default. (--shared-server mode is
        different BY DESIGN: the run's own generate requests go to the
        shared server, which may LRU-evict models resident there.)
      - the port must be FREE before we spawn: an occupied OWN_PORT means
        another server owns it — we raise PortOwnedError (BLOCKED) instead
        of probing someone else's instance, and we never kill it.
      - teardown signals/kills ONLY the tree of the PID we spawned, plus
        runners orphaned directly under our PID (taskkill /T cannot walk a
        dead parent). Nothing is ever killed by image name.
    """

    def __init__(self, exe: Path, log_path: Path, port: int = OWN_PORT):
        self.exe, self.log_path, self.port = exe, log_path, port
        self.base = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.log_file = None

    def evict_shared_models(self) -> dict:
        """EXPLICIT opt-in only (--evict-shared-models): send keep_alive=0
        for every model resident on the shared 11434 server to free VRAM.
        This EVICTS models other workloads are using — the default run
        never calls this ('*' is not a valid tag; evict per model name).
        """
        snap = snapshot_shared_ps()
        if snap.get("status") != "PASS":
            return snap
        evicted, errors = [], []
        for name in snap.get("resident", []):
            try:
                _http_json(
                    f"{DEFAULT_BASE}/api/generate",
                    {"model": name, "keep_alive": 0},
                    timeout=180,
                )
                evicted.append(name)
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}")
        out: dict = {"status": "PASS", "evicted": evicted}
        if errors:
            out["partial_errors"] = errors
        out["resident_after"] = snapshot_shared_ps().get("resident", [])
        return out

    def start(self) -> OwnedServer:
        if _port_in_use(self.port):
            raise PortOwnedError(
                f"port {self.port} is already in use by another process — "
                "refusing to share or kill an unowned server; free the port "
                "or wait for its owner to exit, then rerun"
            )
        self.log_file = open(self.log_path, "wb")
        env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{self.port}"}
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
                _http_json(f"{self.base}/api/version", timeout=3)
                # the responder must still be the child we spawned — a
                # racing impostor on the port would poison attribution
                if self.proc.poll() is not None:
                    raise RuntimeError("owned ollama serve died right after becoming ready")
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

    def _collect_orphan_runner_pids(self) -> list[int]:
        """PID list of llama-server children under OUR pid, collected while
        we still hold the parent handle (so the PPID cannot have been
        recycled — review R1 F-6). taskkill /T cannot walk a dead parent,
        so these are reaped explicitly after the parent exits. Strictly
        PPID+exact-image scoped: runners of the shared server or any other
        parent are never collected, hence never killed.
        """
        if self.proc is None:
            return []
        return [
            p["pid"]
            for p in process_snapshot()
            if p["ppid"] == self.proc.pid and p["name"].lower() == RUNNER_EXE
        ]

    def stop(self) -> None:
        """Idempotent teardown that ALWAYS redacts the log.

        Kills the whole process tree (`taskkill /T`) — TerminateProcess on
        ollama.exe alone can orphan llama-server children holding VRAM —
        and never lets a signal error skip the redaction pass (review
        T1.1 F-2). Only the PID we spawned is ever a kill target: we hold
        its handle, so Windows cannot have reused the PID while it lives.
        Orphaned-runner PIDs are collected BEFORE the parent dies (handle
        still valid) and killed by those exact PIDs afterwards (R1 F-6).
        When the parent already died before stop() (fast-fail path), the
        orphans can only be collected post-mortem: strictly PPID+exact-image
        scoped, with the sub-second reuse window documented as residual
        (R1 F-6) — the alternative (no reap) leaks VRAM on every fast fail.
        """
        orphan_pids: list[int] = []
        try:
            parent_was_alive = self.proc is not None and self.proc.poll() is None
            if parent_was_alive:
                orphan_pids = self._collect_orphan_runner_pids()
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
            if self.proc is not None and not parent_was_alive and not orphan_pids:
                # parent died before stop(): collect the orphans it left behind
                orphan_pids = self._collect_orphan_runner_pids()
            for pid in orphan_pids:
                _run(["taskkill", "/F", "/PID", str(pid)], timeout=30)
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
def code_identity() -> dict:
    """Bind the run to the exact code tree that produced it (T1.2 B-6)."""

    def git(*a: str) -> str:
        r = _run(["git", "-C", str(ROOT), *a], timeout=30)
        return r["stdout"].strip() if r["ok"] else ""

    sha, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    out: dict = {"dev_sha": sha or "unknown", "dirty": bool(git("status", "--porcelain"))}
    if tree:
        out["dev_tree"] = tree
    return out


def _seal_run(out_dir: Path) -> dict:
    """SHA256 of every evidence file in the run dir, written last.

    Runs after env.json (and after the owned log was redacted), so the seal
    hashes exactly the bytes that ship. seal.json never hashes itself.
    """
    seal = {
        "sealed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": {
            p.relative_to(out_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out_dir.rglob("*"))
            if p.is_file() and p.name != "seal.json"
        },
    }
    (out_dir / "seal.json").write_text(json.dumps(seal, indent=2), encoding="utf-8")
    return seal


def _finish(out_dir: Path, rep: dict, no_agent: bool) -> int:
    """Single exit path: stamp verdict, write report, seal, print, exit code."""
    rep["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rep["overall"] = _verdict(rep, no_agent)
    _write(out_dir, rep)
    _seal_run(out_dir)
    print(
        json.dumps(
            {"run_id": rep.get("run_id"), "scope": rep.get("scope"), "overall": rep["overall"]},
            indent=2,
        )
    )
    # stdout lands in the public Actions log — repo-relative, never the
    # absolute path (review R2 N-1)
    print(f"evidence: {out_dir.relative_to(ROOT)}")
    return {"PASS": 0, "ENV_PROBE_PASS": 0, "FAIL": 1}.get(rep["overall"], 2)


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
        "--evict-shared-models",
        action="store_true",
        help="DISRUPTIVE, opt-in: keep_alive=0 every model resident on the "
        "shared 11434 server before starting the owned server (frees VRAM "
        "by interrupting other workloads). Default: never touch the shared "
        "server; insufficient VRAM ends BLOCKED/FAIL instead.",
    )
    ap.add_argument(
        "--shared-server",
        action="store_true",
        help="use the existing 11434 server instead of an owned instance "
        "(attribution of runner DLLs and the discovery line will be BLOCKED)",
    )
    args = ap.parse_args()
    if args.shared_server and args.evict_shared_models:
        ap.error(
            "--evict-shared-models only applies to the owned-server run; "
            "--shared-server mode works on the shared instance by definition "
            "(review R1 F-9: refusing to record a contradictory opt-in)"
        )

    run_id = f"gpuval-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = ROOT / "artifacts" / "gpu-validation" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    scope = "environment-probe" if args.no_agent else "full"
    rep: dict = {
        "run_id": run_id,
        "scope": scope,
        "model": args.model,
        "require_backend": args.require_backend,
        "evict_shared_models_opt_in": args.evict_shared_models,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "code": code_identity(),
    }

    ok_tag, why = validate_model_tag(args.model)
    if not ok_tag:
        return _finish(
            out_dir, rep | {"model_tag": {"status": "BLOCKED", "reason": why}}, args.no_agent
        )

    exe = find_ollama()
    if exe is None:
        return _finish(
            out_dir,
            rep | {"ollama": {"status": "BLOCKED", "reason": "ollama.exe not found"}},
            args.no_agent,
        )

    rep["ollama_version"] = ollama_version(exe)
    rep["model_in_store"] = model_in_store(exe, args.model)
    rep["gpus"] = gpu_names()

    base = DEFAULT_BASE
    owned: OwnedServer | None = None
    owned_pid: int | None = None
    try:
        server_blocked = False
        if not args.shared_server:
            owned = OwnedServer(exe, out_dir / "owned-server.log")
            # read-only non-interference proof: shared residency before/after
            rep["shared_ps_before"] = snapshot_shared_ps()
            if args.evict_shared_models:
                print("[gpu-validate] OPT-IN evicting shared-server models (disruptive)")
                rep["evict_shared_models"] = owned.evict_shared_models()
            try:
                owned.start()
            except PortOwnedError as e:
                rep["owned_server"] = {"status": "BLOCKED", "reason": str(e)}
                server_blocked = True
            except Exception as e:
                # never leak the serve process / an unredacted log on the
                # readiness-failure path (review T1.1 F-2)
                owned.stop()
                rep["owned_server"] = {
                    "status": "BLOCKED",
                    "reason": f"owned server failed to start: {type(e).__name__}: {e}"[:240],
                }
                server_blocked = True
            else:
                base = owned.base
                owned_pid = owned.proc.pid
                rep["owned_server"] = {
                    "status": "PASS",
                    "base_url": base,
                    "log": "owned-server.log",
                    "pid": owned_pid,
                }

        if not server_blocked:
            # live probe: tiny generate on the server under test, then residency
            t0 = time.time()
            try:
                _http_json(
                    f"{base}/api/generate",
                    {
                        "model": args.model,
                        "prompt": "ok",
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                )
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read(200).decode("utf-8", "replace")
                except Exception:
                    pass
                rep["live_probe"] = {
                    "status": "FAIL",
                    "reason": f"generate failed HTTP {e.code}: {detail}"[:240],
                }
                if not args.shared_server:
                    rep["live_probe"]["hint"] = (
                        "if VRAM is exhausted because the shared 11434 server keeps "
                        "models resident, rerun with the explicit opt-in "
                        "--evict-shared-models (disruptive) or free VRAM manually"
                    )
            except Exception as e:
                rep["live_probe"] = {
                    "status": "BLOCKED",
                    "reason": f"{type(e).__name__}: {e}"[:240],
                }
            else:
                rep["live_probe"] = {"status": "PASS", "latency_s": round(time.time() - t0, 1)}

            try:
                rep["residency"] = check_ps_entry(_http_json(f"{base}/api/ps"), args.model)
            except Exception as e:
                rep["residency"] = {"status": "BLOCKED", "reason": f"{type(e).__name__}: {e}"[:240]}

            # model identity from two independent sources (B-7); when a
            # prerequisite did not pass, the chain is honestly incomplete
            if (
                rep.get("residency", {}).get("status") == "PASS"
                and rep.get("model_in_store", {}).get("status") == "PASS"
            ):
                rep["model_identity"] = crosscheck_model_identity(
                    rep["model_in_store"].get("row", ""), rep["residency"].get("digest", "")
                )
            else:
                rep["model_identity"] = {
                    "status": "BLOCKED",
                    "reason": "prerequisite gate (model_in_store/residency) did not pass",
                }

            # attribution view captured BEFORE the (multi-second) DLL probe
            procs_before_dll_probe = process_snapshot()

            mods = tasklist_modules(list(TASKLIST_DLL_PATTERNS))
            (out_dir / "tasklist_modules.json").write_text(
                json.dumps(mods, indent=2), encoding="utf-8"
            )
            probe_errs = next((r["modules"] for r in mods if r["image"] == "__probe_errors__"), [])
            if probe_errs:
                rep["dll_probe_errors"] = probe_errs  # visible diagnostics, never silent
            # snapshot taken right before the DLL probe narrows the PID-reuse
            # window between the two views (review R1 F-5)
            rep["backend_live"] = attribute_runner(owned_pid, mods, procs=procs_before_dll_probe)

            if owned is not None:
                rep["discovery_line"] = parse_inference_compute(owned.wait_discovery_line())
            else:
                rep["discovery_line"] = {
                    "status": "BLOCKED",
                    "reason": "shared-server mode: no attributable owned log "
                    "(arch cannot be proven fresh)",
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
        # non-interference proof: shared residency unchanged by this run
        rep["shared_ps_after"] = snapshot_shared_ps()
        rep["shared_interference"] = _shared_interference_gate(rep, args)

    return _finish(out_dir, rep, args.no_agent)


def _shared_interference_gate(rep: dict, args: argparse.Namespace) -> dict:
    """ENFORCED non-interference (review R1 F-7): in the default owned-server
    mode, the shared 11434 residency must be unchanged unless the operator
    explicitly opted into eviction. A run that cannot prove it FAILs/BLOCKs —
    recording snapshots without comparing them was theater.
    """
    if args.shared_server:
        return {
            "status": "PASS",
            "note": "shared-server mode: not applicable (the run uses the shared "
            "server by design; a full PASS is unreachable there anyway)",
        }
    before, after = rep.get("shared_ps_before", {}), rep.get("shared_ps_after", {})
    if before.get("status") != "PASS":
        return {"status": "PASS", "note": "no shared server observed — nothing to disturb"}
    if after.get("status") != "PASS":
        return {
            "status": "BLOCKED",
            "reason": "shared server became unreachable during the run — "
            "non-interference not provable",
        }
    if before.get("resident") != after.get("resident"):
        if args.evict_shared_models:
            return {
                "status": "PASS",
                "note": "residency changed by the explicit opt-in eviction "
                "(see evict_shared_models)",
            }
        return {
            "status": "FAIL",
            "reason": (
                f"shared residency changed during the run "
                f"{before.get('resident')} -> {after.get('resident')}; this run makes "
                "no mutating calls to the shared server, so another local actor "
                "likely changed it — fail-closed either way"
            ),
        }
    return {"status": "PASS", "resident": after.get("resident", [])}


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
    must = [
        "model_in_store",
        "live_probe",
        "residency",
        "model_identity",
        "backend_live",
        "discovery_line",
        "shared_interference",
    ]
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
        f"- code: {rep.get('code', {}).get('dev_sha')} (dirty={rep.get('code', {}).get('dirty')})",
        f"- ollama: {rep.get('ollama_version')}",
        f"- model in store: {s('model_in_store')} — {rep.get('model_in_store', {}).get('row') or rep.get('model_in_store', {}).get('reason')}",
        f"- GPUs: {'; '.join(rep.get('gpus', [])) or 'n/a'}",
        f"- owned server: {s('owned_server')} pid={rep.get('owned_server', {}).get('pid')} — {rep.get('owned_server', {}).get('reason') or rep.get('owned_server', {}).get('base_url', 'shared mode')}",
        f"- shared ps (before/after, read-only): {rep.get('shared_ps_before', {}).get('resident')} / {rep.get('shared_ps_after', {}).get('resident')}",
        f"- shared interference gate: {s('shared_interference')} — {rep.get('shared_interference', {}).get('note') or rep.get('shared_interference', {}).get('reason') or ''}",
        f"- live probe: {s('live_probe')} ({rep.get('live_probe', {}).get('latency_s')}s) {rep.get('live_probe', {}).get('reason', '')}",
        f"- residency: {s('residency')} digest={rep.get('residency', {}).get('digest')} vram={rep.get('residency', {}).get('size_vram')}/{rep.get('residency', {}).get('size')}",
        f"- model identity (list id vs ps digest): {s('model_identity')} — {rep.get('model_identity', {}).get('id12') or rep.get('model_identity', {}).get('reason', '')}",
        f"- backend (live DLLs, attributed runners): {s('backend_live')} {rep.get('backend_live', {}).get('backend', '')} — {rep.get('backend_live', {}).get('evidence') or rep.get('backend_live', {}).get('reason')}",
        f"- discovery line (owned log): {s('discovery_line')} backend={rep.get('discovery_line', {}).get('backend')} compute={rep.get('discovery_line', {}).get('compute')} gpu={rep.get('discovery_line', {}).get('gpu_name')}",
        f"- agent: {rep.get('agent', {}).get('status', 'n/a')} {rep.get('agent', {}).get('reasons') or rep.get('agent', {}).get('reason') or ''}",
        "",
        "Full JSON: `env.json`; owned server log: `owned-server.log`; agent chain in `agent/` when run; `seal.json` hashes every file above.",
    ]
    # exception reasons/stderr tails embed absolute local paths — the report
    # is uploaded as a workflow artifact, so it ships redacted too (R1 F-2)
    report_text, _ = redact_text("\n".join(lines))
    (out_dir / "REPORT.md").write_text(report_text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
