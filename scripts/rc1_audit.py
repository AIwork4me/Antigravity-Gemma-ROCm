"""RC1 Phase 0 — independent audit: recompute claims from raw artifacts/source.

Nothing here trusts a previous summary: every number is recomputed from the
per-run raw evidence (summary.json / tool_calls.json / pytest_*.json / logs)
and cross-checked against the aggregate stability.json.

Output: artifacts/rc1/audit.json (printed tables go to the MD report).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "artifacts"
OUT = ART / "rc1"
CST = timezone(timedelta(hours=8))

report: dict = {"generated_at": datetime.now(CST).isoformat(), "sections": {}}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- 1. backend identity (fresh, this round) ----------
fresh_log = ROOT / "artifacts/rc1-serve.log"
fresh = fresh_log.read_text(encoding="utf-8", errors="replace") if fresh_log.exists() else ""
lib_lines = re.findall(r'"inference compute"[^\n]*', fresh)
backend_fresh = None
for line in lib_lines:
    m = re.search(r"library=(\S+) compute=(\S+)", line)
    if m:
        backend_fresh = {"library": m.group(1), "compute": m.group(2), "line": line[:220]}
report["sections"]["backend_fresh"] = backend_fresh

# historical corroboration from timestamped logs
hist = []
for lg in [
    "gpu-backend/server-startup.log",
    "gpu-backend/default-serve.log",
    "gpu-backend/default-serve2.log",
    "gpu-backend/vulkan-serve.log",
]:
    p = ROOT / "artifacts" / lg
    if p.exists():
        txt = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
            r'time=([0-9T:+.-]+).*?"inference compute".*?library=(\S+) compute=(\S+)', txt
        ):
            hist.append({"log": lg, "at": m.group(1), "library": m.group(2), "compute": m.group(3)})
report["sections"]["backend_history"] = hist

# ---------- 2. gfx1151 identity (re-queried live) ----------
try:
    hip = subprocess.run(
        [os.path.expandvars(r"%USERPROFILE%\miniconda3\Scripts\hipInfo.exe")],
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    gcn = re.search(r"gcnArchName:\s+(\S+)", hip)
    name = re.search(r"Name:\s+(.+)", hip)
    report["sections"]["gpu_identity"] = {
        "source": "hipInfo.exe (live re-run)",
        "gcnArchName": gcn.group(1) if gcn else None,
        "device": name.group(1).strip() if name else None,
    }
except Exception as e:
    report["sections"]["gpu_identity"] = {"error": str(e)}

# ---------- 3. model digest ----------
manifest = Path.home() / ".ollama/models/manifests/registry.ollama.ai/library/gemma4/26b"
digest_calc = None
if manifest.exists():
    digest_calc = "sha256:" + sha256_file(manifest)
ollama_list = subprocess.run(
    [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"), "list"],
    capture_output=True,
    text=True,
    timeout=60,
).stdout
m = re.search(r"gemma4:26b\s+(\w+)", ollama_list)
digest_listed = m.group(1) if m else None
blobs_dir = Path.home() / ".ollama/models/blobs"
blob_ok = {}
mf = json.loads(manifest.read_text(encoding="utf-8"))
entries = [("config", mf["config"]["digest"], mf["config"]["size"])] + [
    (f"layer{i}", layer["digest"], layer["size"]) for i, layer in enumerate(mf["layers"])
]
for tag, dg, size in entries:
    p = blobs_dir / dg.replace(":", "-")
    blob_ok[tag] = {
        "digest": dg[:22],
        "exists": p.exists(),
        "size_match": p.exists() and p.stat().st_size == size,
    }
report["sections"]["model"] = {
    "manifest_digest_recomputed": digest_calc,
    "ollama_list_id": digest_listed,
    "digest_match": bool(
        digest_calc and digest_listed and digest_calc.startswith("sha256:" + digest_listed)
    ),
    "blobs": blob_ok,
}

# ---------- 4. dependency versions ----------
freeze = subprocess.run(
    [str(ROOT / ".venv/Scripts/python.exe"), "-m", "pip", "freeze"],
    capture_output=True,
    text=True,
    timeout=120,
).stdout.splitlines()
lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
report["sections"]["deps"] = {
    "freeze_eq_lock": sorted(freeze) == sorted(lock),
    "only_in_freeze": sorted(set(freeze) - set(lock)),
    "only_in_lock": sorted(set(lock) - set(freeze)),
    "google-antigravity": next(
        (line for line in freeze if line.startswith("google-antigravity")), None
    ),
}

# ---------- 5-9. per-run recomputation ----------
runs = sorted((ART / "agent-execution").glob("run-stability-*"))
per_run = []
for rd in runs:
    sj = rd / "summary.json"
    if not sj.exists():
        per_run.append({"run": rd.name, "error": "summary.json missing"})
        continue
    s = json.loads(sj.read_text(encoding="utf-8"))
    tc = (
        json.loads((rd / "tool_calls.json").read_text(encoding="utf-8"))
        if (rd / "tool_calls.json").exists()
        else []
    )
    before = json.loads((rd / "pytest_before.json").read_text(encoding="utf-8"))
    after = json.loads((rd / "pytest_after.json").read_text(encoding="utf-8"))
    ws_b = json.loads((rd / "workspace_before.json").read_text(encoding="utf-8"))
    ws_a = json.loads((rd / "workspace_after.json").read_text(encoding="utf-8"))
    # recompute files-changed from the two SHA maps (don't trust summary)
    changed = sorted(
        k
        for k in set(ws_b) | set(ws_a)
        if ws_b.get(k, {}).get("sha256") != ws_a.get(k, {}).get("sha256")
    )
    # verify pristine baseline hash equals the recorded 'before' hash of stats_utils.py
    pristine = ROOT / "demo_workspace_pristine" / "stats_utils.py"
    pristine_sha = sha256_file(pristine)
    before_sha = ws_b.get("stats_utils.py", {}).get("sha256")
    # policy audit must contain an ALLOWed pytest
    pa = (
        (rd / "policy_audit.txt").read_text(encoding="utf-8", errors="replace")
        if (rd / "policy_audit.txt").exists()
        else ""
    )
    pytest_allowed = "decision=ALLOW args={'CommandLine': 'python -m pytest" in pa or (
        "decision=ALLOW" in pa and "pytest" in pa
    )
    per_run.append(
        {
            "run": rd.name,
            "recomputed_success": before["exit_code"] != 0 and after["exit_code"] == 0,
            "summary_says": s.get("bug_fixed_after") and s.get("bug_present_before"),
            "elapsed_s": s.get("elapsed_s"),
            "n_tool_calls_recounted": len(tc),
            "n_tool_calls_summary": s.get("n_tool_calls"),
            "tool_names": [t.get("name") for t in tc],
            "has_edit_file": any(
                t.get("name") == "edit_file" or t.get("name") == "create_file" for t in tc
            ),
            "pytest_red_green": (before["exit_code"], after["exit_code"]),
            "red_text_present": "failed" in before.get("stdout", ""),
            "green_text_present": "passed" in after.get("stdout", "")
            and "failed" not in after.get("stdout", ""),
            "files_changed_recomputed": changed,
            "pristine_matches_before": pristine_sha == before_sha,
            "pytest_allowed_by_policy": pytest_allowed,
            "mtime_of_summary": datetime.fromtimestamp(sj.stat().st_mtime, CST).isoformat(),
        }
    )
report["sections"]["runs_recomputed"] = per_run

ok = [r for r in per_run if r.get("recomputed_success")]
times = [r["elapsed_s"] for r in ok if r.get("elapsed_s")]
report["sections"]["aggregate"] = {
    "n_runs": len(per_run),
    "success_recomputed": len(ok),
    "avg_s": round(sum(times) / len(times), 1) if times else None,
    "min_s": min(times) if times else None,
    "max_s": max(times) if times else None,
    "cross_check_stability_json": None,
}
stab = ART / "stability" / "stability.json"
if stab.exists():
    st = json.loads(stab.read_text(encoding="utf-8"))
    report["sections"]["aggregate"]["cross_check_stability_json"] = {
        "claimed": st.get("n_success"),
        "of": st.get("n_total"),
        "matches_recomputed": st.get("n_success") == len(ok),
    }

# provenance: summary mtimes must be ordered & within the stability console window
mt = [r["mtime_of_summary"] for r in per_run if r.get("mtime_of_summary")]
report["sections"]["provenance"] = {
    "summaries_monotonic": mt == sorted(mt),
    "first": mt[0] if mt else None,
    "last": mt[-1] if mt else None,
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(report["sections"]["aggregate"], indent=2))
print("AUDIT_JSON_WRITTEN")
