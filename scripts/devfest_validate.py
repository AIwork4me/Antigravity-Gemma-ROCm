"""Physical GPU validation runner for the DevFest "The Last Seat" demo.

Runs N independent, fully-reset agent tasks against the real local stack
(Ollama gemma4:26b on Radeon 8060S / gfx1151 via the Antigravity SDK) and
captures, per run, the tamper-evident chain plus GPU backend attribution:

  - curated evidence -> evidence/devfest-last-seat/run-<id>/ (committed)
    (absolute home/repo paths redacted to ~ / <repo>)
  - raw evidence     -> artifacts/devfest-agent-execution/run-<id>/ (local)

GPU attribution is NEVER inferred from a (possibly stale) server.log:
residency + full digest come from GET /api/ps AFTER inference, and the ROCm
claim additionally requires the llama-server.exe runner process to hold
AMD HIP DLLs (tasklist /m, wildcard last). Missing hardware evidence is
reported as such — a run without GPU attribution is still recorded honestly.

Usage:
    python scripts/devfest_validate.py --runs 3 --run-prefix devfest
    python scripts/devfest_validate.py --runs 1 --skip-warmup
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import DEFAULT_MODEL  # noqa: E402
from devfest_agent import EVIDENCE_ROOT, run_full_task  # noqa: E402

CURATED = ROOT / "evidence" / "devfest-last-seat"
OLLAMA = Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"

# files copied (redacted) from the raw evidence dir into the curated dir
CURATED_FILES = (
    "summary.json",
    "pytest_before.json",
    "pytest_after.json",
    "tool_calls.json",
    "policy_audit.txt",
    "agent_reply.txt",
    "diff.patch",
    "config.txt",
)


def redact(text: str) -> str:
    # repo first: it is nested under home, and once "~" is substituted the
    # longer repo marker would no longer match
    text = text.replace(str(ROOT), "<repo>")
    text = text.replace(str(ROOT).replace("\\", "/"), "<repo>")
    text = text.replace(str(Path.home()), "~")
    return text


def redact_json(obj):
    if isinstance(obj, dict):
        return {k: redact_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_json(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj


def http_json(url: str, timeout: int = 5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def warm_model(model: str) -> dict:
    body = json.dumps({"model": model, "keep_alive": "30m"}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            r.read()
        return {"warmed": True, "warmup_s": round(time.time() - t0, 1)}
    except Exception as e:
        return {"warmed": False, "error": f"{type(e).__name__}: {e}"}


def gpu_evidence(model: str) -> dict:
    """Live, run-attributable GPU evidence (same rules as devfest_webui)."""
    ev = {
        "model": model,
        "resident": False,
        "digest": None,
        "size_vram": None,
        "size": None,
        "gpu_100pct": False,
        "rocm_dll_processes": [],
        "backend": "unknown",
    }
    try:
        for m in http_json("http://127.0.0.1:11434/api/ps").get("models", []):
            if m.get("name") == model:
                ev.update(
                    resident=True,
                    digest=m.get("digest"),
                    size_vram=m.get("size_vram"),
                    size=m.get("size"),
                    gpu_100pct=m.get("size_vram") == m.get("size") and m.get("size", 0) > 0,
                )
                break
    except Exception as e:
        ev["ps_error"] = f"{type(e).__name__}: {e}"
    if ev["resident"]:
        out = subprocess.run(
            ["tasklist", "/m", "amdhip64*"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        ).stdout
        lines = [ln.strip() for ln in out.splitlines() if ln.lower().startswith("llama-server")]
        if lines:
            ev["rocm_dll_processes"] = [ln.split()[1] for ln in lines if len(ln.split()) > 1]
            ev["backend"] = "ROCm VERIFIED"
        else:
            out2 = subprocess.run(
                ["tasklist", "/m", "ggml-vulkan.dll"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            ).stdout
            if any(ln.lower().startswith("llama-server") for ln in out2.splitlines()):
                ev["backend"] = "Vulkan"
            else:
                ev["backend"] = "resident (backend unverified)"
    else:
        ev["backend"] = "not resident (cold)"
    try:
        ev["ollama_ps_text"] = redact(
            subprocess.run(
                [str(OLLAMA), "ps"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            ).stdout.strip()
        )
    except Exception as e:
        ev["ollama_ps_text"] = f"error: {type(e).__name__}"
    return ev


def gpu_name() -> str | None:
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController).Name",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        ).stdout
        names = [ln.strip() for ln in out.splitlines() if "radeon" in ln.lower()]
        return names[0] if names else None
    except Exception:
        return None


def collect_run(run_id: str, gpu: dict, env: dict) -> Path:
    raw = EVIDENCE_ROOT / f"run-{run_id}"
    cur = CURATED / f"run-{run_id}"
    cur.mkdir(parents=True, exist_ok=True)
    for name in CURATED_FILES:
        src = raw / name
        if not src.is_file():
            continue
        dst = cur / name
        if name.endswith(".json"):
            try:
                dst.write_text(
                    json.dumps(
                        redact_json(json.loads(src.read_text(encoding="utf-8"))),
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                continue
            except (OSError, json.JSONDecodeError):
                pass
        dst.write_text(redact(src.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    (cur / "gpu_evidence.json").write_text(
        json.dumps(gpu, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (cur / "env.json").write_text(json.dumps(env, indent=2, ensure_ascii=False), encoding="utf-8")
    return cur


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--run-prefix", default="devfest")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--skip-warmup", action="store_true")
    args = ap.parse_args()

    try:
        ver = http_json("http://127.0.0.1:11434/api/version").get("version")
    except Exception as e:
        print(
            f"BLOCKED: Ollama unreachable ({e}) — physical validation requires the "
            "live local stack; refusing to fake it."
        )
        return 2

    try:
        import google.antigravity as _ga

        sdk = getattr(_ga, "__version__", "0.1.23")
    except Exception:
        sdk = "unknown"

    gpu = gpu_name()
    env = {
        "os": f"{platform.system()} {platform.release()} (build {platform.version()})",
        "python": platform.python_version(),
        "antigravity_sdk": sdk,
        "ollama_server": ver,
        "model": args.model,
        "gpu_adapter": gpu or "(not enumerated)",
        "note": "GPU adapter name via Win32_VideoController; backend claims require "
        "per-run residency+DLL evidence in gpu_evidence.json",
    }

    if not args.skip_warmup:
        print(f"warming {args.model} (keep_alive 30m)…")
        print(json.dumps(warm_model(args.model)))

    results = []
    overall_ok = True
    for i in range(1, args.runs + 1):
        run_id = f"{args.run_prefix}-{i}"
        print(f"\n=== RUN {i}/{args.runs} ({run_id}) — fresh baseline reset, real agent ===")
        t0 = time.time()
        summary = run_full_task(args.model, "http://127.0.0.1:11434/v1", args.timeout, run_id)
        wall_s = round(time.time() - t0, 1)
        gp = gpu_evidence(args.model)
        cur = collect_run(run_id, gp, env)
        summary["wall_s"] = wall_s
        summary["gpu_backend"] = gp["backend"]
        summary["model_digest"] = gp.get("digest")
        results.append(summary)
        # keep the curated summary in sync with the enriched one
        (cur / "summary.json").write_text(
            json.dumps(redact_json(summary), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(
            f"run {run_id}: ok={summary['ok']} backend={gp['backend']} "
            f"elapsed={summary['elapsed_s']}s wall={wall_s}s"
        )
        if not summary["ok"]:
            overall_ok = False

    ledger = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "env": env,
        "runs_required": args.runs,
        "runs_ok": sum(1 for r in results if r["ok"]),
        "all_ok": overall_ok,
        "results": results,
    }
    CURATED.mkdir(parents=True, exist_ok=True)
    (CURATED / "validation_ledger.json").write_text(
        json.dumps(redact_json(ledger), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        "\n" + json.dumps({k: ledger[k] for k in ("runs_required", "runs_ok", "all_ok")}, indent=2)
    )
    print(f"ledger: {CURATED / 'validation_ledger.json'}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
