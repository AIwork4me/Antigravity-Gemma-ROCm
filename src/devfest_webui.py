"""DevFest 2026 "The Last Seat" stage dashboard (port 8421, no new deps).

Serves the 1920x1080 stage page at http://127.0.0.1:8421:

  LEFT  — the real (fictional-event) registration app: 39/40 seats taken,
          the 40th registration is executed against the ACTUAL scenario code
          in an isolated probe process (never hardcoded state), then — after
          the agent's independently verified fix — flips to the Google-green
          "YOU'RE IN!" celebration and a clearly-fake Builder Pass.
  RIGHT — the live agent execution trace: read -> pytest RED -> edit ->
          pytest GREEN, policy decisions, model tokens, diff, SHA256/timing
          evidence and honest GPU backend attribution (ROCm VERIFIED only
          from live run evidence: model digest via /api/ps + llama-server
          ROCm DLL modules).

Modes (visually distinct, never ambiguous):
  - LIVE (default): real local Gemma 4 agent run on this machine
  - --replay rec.json: recorded REPLAY banner, replay never implies fresh
    hardware or test validation
  - --dry-run: DESIGN PREVIEW — synthetic, plainly-labeled timeline that
    works without Ollama/GPU; never writes evidence and never counts as
    acceptance (no ok:true is ever emitted in preview mode)

Security controls mirror the audited src/webui.py: loopback-only binding,
Host/Origin/Sec-Fetch-Site gate on every state-changing endpoint (imported
from webui so both stay in sync), nosniff/DENY headers, bounded replay
loader, and all dynamic content rendered via textContent (no innerHTML with
untrusted data). The Windows policy handler remains a POLICY boundary, not
an OS sandbox.

Run:  python -I src/devfest_webui.py [--port 8421]
      python -I src/devfest_webui.py --record recording.json
      python -I src/devfest_webui.py --replay recording.json [--replay-speed 1.5]
      python -I src/devfest_webui.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sse_starlette.sse import EventSourceResponse  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

from config import DEFAULT_MODEL  # noqa: E402
from demo_agent import sha256_of  # noqa: E402
from devfest_agent import (  # noqa: E402
    DEVFEST_PRISTINE,
    DEVFEST_WORKSPACE,
    restore_devfest_baseline,
    run_full_task,
)
from webui import origin_allowed  # single audited implementation, keep in sync # noqa: E402

OLLAMA = Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"
PORT_DEFAULT = 8421
# curated, committed evidence of physical validation runs (read-only listing)
CURATED_EVIDENCE = ROOT / "evidence" / "devfest-last-seat"

# ---------------------------------------------------------------- run state
_lock = threading.Lock()
_events: deque = deque(maxlen=4000)
_state = {
    "running": False,
    "replay": False,
    "preview": False,
    "mode": "live",  # live | replay | preview
    "run_id": None,
    "started_at": None,
}
_seq = {"n": 0}

_recorder: list[dict] | None = None
_recorder_path: Path | None = None


def set_recorder(path: Path | None) -> None:
    global _recorder, _recorder_path
    with _lock:
        if path is None:
            _recorder = None
            _recorder_path = None
        else:
            _recorder = []
            _recorder_path = Path(path)


def dump_recording() -> Path | None:
    if _recorder is None or _recorder_path is None:
        return None
    with _lock:
        events = list(_recorder)
        _recorder.clear()
    payload = {
        "format": 1,
        "meta": {
            "run_id": _state.get("run_id"),
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": DEFAULT_MODEL,
            "demo": "devfest-last-seat",
        },
        "events": events,
    }
    _recorder_path.parent.mkdir(parents=True, exist_ok=True)
    _recorder_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return _recorder_path


# Replay loader: same bounds as webui.load_replay plus devfest event kinds.
_MAX_REPLAY_BYTES = 32 * 1024 * 1024
_MAX_REPLAY_EVENTS = 20_000
_MAX_EVENT_CHARS = 65_536
_REPLAY_KINDS = frozenset(
    {
        "stage",
        "tool",
        "policy",
        "token",
        "pytest",
        "diff",
        "done",
        "error",
        "meta",
        "replay",
        "replay_done",
        "ping",
        "seat",
        "gpu",
    }
)


def _check_replay_event(ev) -> None:
    if not isinstance(ev, dict):
        raise ValueError("replay event must be an object")
    if ev.get("kind") not in _REPLAY_KINDS:
        raise ValueError(f"unknown replay event kind: {ev.get('kind')!r}")
    if len(json.dumps(ev, ensure_ascii=False, default=str)) > _MAX_EVENT_CHARS:
        raise ValueError("replay event too large")
    ts = ev.get("ts")
    if ts is not None and (
        isinstance(ts, bool) or not isinstance(ts, int | float) or not math.isfinite(ts)
    ):
        raise ValueError("replay event ts must be a finite number")
    t = ev.get("t")
    if t is not None and (not isinstance(t, str) or len(t) > 32):
        raise ValueError("replay event t must be a short string")


def load_replay(path: Path) -> dict:
    p = Path(path)
    try:
        if p.stat().st_size > _MAX_REPLAY_BYTES:
            raise ValueError(
                f"replay file too large ({p.stat().st_size} bytes > {_MAX_REPLAY_BYTES})"
            )
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, RecursionError) as e:
        raise ValueError(f"cannot read replay file {path}: {type(e).__name__}: {e}") from e
    if (
        not isinstance(rec, dict)
        or rec.get("format") != 1
        or not isinstance(rec.get("events"), list)
    ):
        raise ValueError(f"unsupported replay file {path}: expected format 1 with an events list")
    if len(rec["events"]) > _MAX_REPLAY_EVENTS:
        raise ValueError(
            f"replay file has too many events ({len(rec['events'])} > {_MAX_REPLAY_EVENTS})"
        )
    if not isinstance(rec.get("meta", {}), dict):
        raise ValueError("replay meta must be an object")
    try:
        for ev in rec["events"]:
            _check_replay_event(ev)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e
    return rec


def clamp_speed(speed: float) -> float:
    try:
        return min(max(float(speed), 0.1), 100.0)
    except (TypeError, ValueError):
        return 1.0


def _busy_reason() -> str:
    if _state["replay"]:
        return "replay in progress"
    if _state["preview"]:
        return "design preview in progress"
    if _state["running"]:
        return "a run is already in progress"
    return ""


def emit(kind: str, data) -> None:
    ts = time.time()
    with _lock:
        _seq["n"] += 1
        ev = {"id": _seq["n"], "kind": kind, "data": data, "t": time.strftime("%H:%M:%S")}
        _events.append(ev)
        if _recorder is not None:
            _recorder.append({**ev, "data": copy.deepcopy(data), "ts": ts})


def _append_replay_event(ev: dict) -> None:
    with _lock:
        _seq["n"] += 1
        _events.append(
            {
                "id": _seq["n"],
                "kind": ev.get("kind", "event"),
                "data": ev.get("data"),
                "t": ev.get("t", "--:--:--"),
            }
        )


def start_replay(recording: dict, speed: float = 1.0) -> bool:
    events = list(recording.get("events", []))
    meta = recording.get("meta") or {}
    with _lock:
        if _state["running"] or _state["replay"] or _state["preview"]:
            return False
        _state.update(running=True, replay=True, mode="replay", run_id=meta.get("run_id"))

    emit("replay", {"run_id": meta.get("run_id"), "recorded_at": meta.get("recorded_at")})
    pace = clamp_speed(speed)

    def feeder() -> None:
        try:
            prev_ts = None
            for ev in events:
                ts = ev.get("ts")
                if isinstance(ts, int | float) and not isinstance(ts, bool) and prev_ts is not None:
                    delay = min(max(ts - prev_ts, 0.0) / pace, 2.0)
                    if math.isfinite(delay):
                        time.sleep(delay)
                prev_ts = ts
                _append_replay_event(ev)
            emit("replay_done", {"run_id": meta.get("run_id")})
        except Exception as e:
            emit("error", {"message": f"replay aborted: {type(e).__name__}: {e}"})
        finally:
            with _lock:
                _state["running"] = False
                _state["replay"] = False
                _state["mode"] = "live"

    threading.Thread(target=feeder, daemon=True).start()
    return True


# ------------------------------------------------------- GPU backend evidence
_gpu_name_cache: str | None = None


def gpu_name() -> str | None:
    """One-shot GPU marketing name via CIM (cached; PowerShell script file not
    needed since this is not an inline `$_` pipeline)."""
    global _gpu_name_cache
    if _gpu_name_cache is None:
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
                timeout=15,
            ).stdout
            names = [ln.strip() for ln in out.splitlines() if "radeon" in ln.lower()]
            _gpu_name_cache = names[0] if names else (out.strip().splitlines() or [""])[0] or None
        except Exception:
            _gpu_name_cache = ""
    return _gpu_name_cache or None


def _ps_models() -> list:
    import urllib.request

    with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=4) as r:
        return json.loads(r.read().decode()).get("models", [])


def _tasklist_has_module(module_pattern: str) -> list:
    """PIDs of llama-server.exe processes holding a module matching the pattern
    (wildcard may only be the LAST character of the pattern)."""
    try:
        out = subprocess.run(
            ["tasklist", "/m", module_pattern],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        ).stdout
    except Exception:
        return []
    return [
        ln.strip()
        for ln in out.splitlines()
        if ln.lower().startswith("llama-server")
        and module_pattern.rstrip("*").lower() in ln.lower()
    ]


def gpu_evidence(model: str = DEFAULT_MODEL) -> dict:
    """Honest, run-attributable GPU evidence. 'ROCm VERIFIED' requires BOTH
    model residency (size_vram == size, full digest via /api/ps) AND the
    llama-server runner holding ROCm HIP DLLs. Anything less is reported
    as-is — never inferred from a (possibly stale) server.log."""
    ev = {
        "model": model,
        "resident": False,
        "digest": None,
        "size_vram": None,
        "size": None,
        "gpu_100pct": False,
        "rocm_dll_processes": [],
        "backend": "unknown",
        "gpu_name": gpu_name(),
    }
    try:
        for m in _ps_models():
            if m.get("name") == model:
                ev.update(
                    resident=True,
                    digest=m.get("digest"),
                    size_vram=m.get("size_vram"),
                    size=m.get("size"),
                    gpu_100pct=m.get("size_vram") == m.get("size") and m.get("size", 0) > 0,
                )
                break
    except Exception:
        pass
    if ev["resident"]:
        rocm = _tasklist_has_module("amdhip64*")
        if rocm:
            ev["rocm_dll_processes"] = [ln.split()[1] for ln in rocm if len(ln.split()) > 1]
            ev["backend"] = "ROCm VERIFIED"
        elif _tasklist_has_module("ggml-vulkan.dll"):
            ev["backend"] = "Vulkan"
        else:
            ev["backend"] = "resident (backend unverified)"
    else:
        ev["backend"] = "not resident (cold)"
    return ev


# ------------------------------------------------------------- probe / seats
def sanitize_name(raw) -> str:
    """Bounded, control-free display name; never persisted, never sent to model."""
    if not isinstance(raw, str):
        return "Guest Developer"
    cleaned = "".join(ch for ch in raw.strip() if ord(ch) >= 32 and ch not in "\x7f")
    cleaned = cleaned[:24].strip()
    return cleaned or "Guest Developer"


def probe_register(name: str, scenario: str = "last") -> dict:
    """Execute the REAL scenario code (isolated `python -I` subprocess) and
    return its verdict. This is the ONLY source of admission state for the UI."""
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "src" / "devfest_probe.py"),
                "--workspace",
                str(DEVFEST_WORKSPACE),
                "--scenario",
                scenario,
                "--name",
                name,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"probe_error": f"{type(e).__name__}: {e}", "admitted": None}


# ------------------------------------------------------------ live task run
def run_task_thread(run_id: str) -> None:
    def on_event(kind: str, data) -> None:
        emit(kind, data)

    def wrapper():
        try:
            gpu = gpu_evidence()
            emit(
                "gpu", {"phase": "pre", **{k: gpu[k] for k in ("backend", "resident", "gpu_name")}}
            )
            summary = run_full_task(DEFAULT_MODEL, _base_url(), 900, run_id, on_event=on_event)
            gpu = gpu_evidence()
            emit("gpu", {"phase": "post", **gpu})
            if summary["ok"]:
                verdict = probe_register("Stage Check", "last")
                emit("seat", {"probe": verdict, "source": "probe"})
        except Exception as e:
            emit("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            with _lock:
                _state["running"] = False  # release the busy gate (was missing: stuck-running)
            dump_recording()

    threading.Thread(target=wrapper, daemon=True).start()


def start_run() -> tuple[bool, str]:
    with _lock:
        reason = _busy_reason()
        if reason:
            return False, reason
        _state.update(
            running=True,
            mode="live",
            run_id=f"stage-{time.strftime('%H%M%S')}",
            started_at=time.strftime("%H:%M:%S"),
        )
        rid = _state["run_id"]
    run_task_thread(rid)
    return True, ""


def _base_url() -> str:
    import os

    return os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")


# ---------------------------------------------------------- design preview
def start_preview() -> tuple[bool, str]:
    """DESIGN PREVIEW: a plainly-labeled synthetic timeline. No Ollama, no
    workspace writes, no evidence files, no ok:true — never acceptance."""
    with _lock:
        reason = _busy_reason()
        if reason:
            return False, reason
        _state.update(running=True, preview=True, mode="preview", run_id="preview")

    def feeder() -> None:
        P = {"preview": True}

        def pv(kind, data, delay=0.8):
            time.sleep(delay)
            # policy events are plain strings (audit lines); the rest are dicts
            emit(kind, {**P, **data} if isinstance(data, dict) else data)

        try:
            pv("stage", {"name": "restore", "text": "[预览] 模拟时间线 — 不触碰真实工作区"}, 0.3)
            pv("pytest", {"phase": "before", "exit": 1, "tail": "[预览] 1 failed, 8 passed (模拟)"})
            pv("stage", {"name": "agent", "text": "[预览] 模拟 Agent 步骤"})
            for name, args in [
                ("list_directory", "{'Path': 'devfest_workspace'}"),
                ("view_file", "{'AbsolutePath': '.../checkin.py'}"),
                ("run_command", "{'CommandLine': 'python -m pytest tests/ -q'}"),
                ("edit_file", "{'Path': '.../checkin.py'}"),
                ("run_command", "{'CommandLine': 'python -m pytest tests/ -q'}"),
            ]:
                pv("tool", {"name": name, "args": args})
                if name == "run_command":
                    pv("policy", "run_command decision=ALLOW args=[预览] 模拟策略决策", 0.4)
            for chunk in ["[DESIGN PREVIEW] ", "此处为设计预览文本，", "非真实模型输出。"]:
                pv("token", chunk, 0.25)
            pv("pytest", {"phase": "after", "exit": 0, "tail": "[预览] 9 passed (模拟)"})
            pv(
                "diff",
                {"files": ["checkin.py"], "patch": "[预览] 模拟 diff — LIVE 模式显示真实 diff"},
            )
            pv("gpu", {"phase": "post", "backend": "[预览] 未验证", "resident": False})
            time.sleep(0.4)
            emit(
                "seat",
                {
                    "preview": True,
                    "probe": {"admitted": True, "seat": 40},
                    "source": "preview-mock",
                    "note": "模拟成功画面（非真实验证）",
                },
            )
            emit("done", {**P, "ok": None, "note": "design preview only — not a live agent run"})
        except Exception as e:
            emit("error", {"message": f"preview aborted: {type(e).__name__}: {e}"})
        finally:
            with _lock:
                _state["running"] = False
                _state["preview"] = False
                _state["mode"] = "live"

    threading.Thread(target=feeder, daemon=True).start()
    return True, ""


# ---------------------------------------------------------------- endpoints
async def homepage(request: Request) -> Response:
    return Response(
        PAGE,
        media_type="text/html; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )


def _workspace_at_baseline() -> bool:
    try:
        return sha256_of(DEVFEST_PRISTINE / "checkin.py") == sha256_of(
            DEVFEST_WORKSPACE / "checkin.py"
        )
    except OSError:
        return False


async def api_status(request: Request) -> JSONResponse:
    status = {
        "server": False,
        "model": DEFAULT_MODEL,
        "model_listed": False,
        "model_resident": False,
        "workspace_has_engine": (DEVFEST_WORKSPACE / "checkin.py").is_file(),
        "workspace_at_baseline": _workspace_at_baseline(),
        "mode": _state["mode"],
        "running": _state["running"],
        "port": PORT_DEFAULT,
        "gpu_name": gpu_name(),
    }
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3) as r:
            status["server"] = json.loads(r.read().decode()).get("version")
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            status["model_listed"] = DEFAULT_MODEL in [
                m["name"] for m in json.loads(r.read().decode())["models"]
            ]
    except Exception:
        pass
    try:
        ps = subprocess.run([str(OLLAMA), "ps"], capture_output=True, text=True, timeout=15).stdout
        status["model_resident"] = DEFAULT_MODEL.split(":")[0] in ps and "GPU" in ps
    except Exception:
        pass
    return JSONResponse(status)


async def api_run(request: Request):
    ok, why = origin_allowed(request.headers)
    if not ok:
        return JSONResponse({"error": f"forbidden: {why}"}, status_code=403)
    if _PREVIEW_ONLY:
        started, why = start_preview()
    else:
        started, why = start_run()
    if not started:
        return JSONResponse({"error": why}, status_code=409)
    return JSONResponse({"started": True, "run_id": _state["run_id"], "mode": _state["mode"]})


async def api_reset(request: Request):
    ok, why = origin_allowed(request.headers)
    if not ok:
        return JSONResponse({"error": f"forbidden: {why}"}, status_code=403)
    with _lock:
        reason = _busy_reason()
        if reason:
            return JSONResponse({"error": reason}, status_code=409)
    restore_devfest_baseline()
    emit("stage", {"name": "reset", "text": "演示已重置：工作区恢复到缺陷基线"})
    verdict = probe_register("Reset Check", "last")
    emit("seat", {"probe": verdict, "source": "probe"})
    return JSONResponse({"reset": True, "workspace_at_baseline": True, "probe": verdict})


async def api_register(request: Request):
    ok, why = origin_allowed(request.headers)
    if not ok:
        return JSONResponse({"error": f"forbidden: {why}"}, status_code=403)
    name = "Guest Developer"
    try:
        body = await request.json()
        if isinstance(body, dict):
            name = sanitize_name(body.get("name"))
    except Exception:
        pass
    verdict = probe_register(name, "last")
    emit("seat", {"probe": {**verdict, "name": name}, "source": "probe"})
    return JSONResponse({"name": name, "probe": verdict})


def _snapshot_after(last_id: int) -> list[dict]:
    with _lock:
        return [e for e in _events if e["id"] > last_id]


async def api_stream(request: Request):
    last_id = 0

    async def gen():
        nonlocal last_id
        while True:
            if await request.is_disconnected():
                return
            batch = _snapshot_after(last_id)
            if batch:
                for e in batch:
                    last_id = e["id"]
                    yield {"event": e["kind"], "data": json.dumps(e["data"], ensure_ascii=False)}
                await asyncio.sleep(0.05)
            else:
                yield {"event": "ping", "data": ""}
                await asyncio.sleep(0.5)

    return EventSourceResponse(gen())


async def api_evidence(request: Request) -> JSONResponse:
    """Read-only listing of the curated physical-run evidence directory."""
    runs = []
    if CURATED_EVIDENCE.is_dir():
        for run_dir in sorted(p for p in CURATED_EVIDENCE.iterdir() if p.is_dir()):
            entry: dict = {"run": run_dir.name, "files": []}
            for f in sorted(run_dir.rglob("*")):
                if f.is_file() and f.stat().st_size < 200_000:
                    entry["files"].append(str(f.relative_to(CURATED_EVIDENCE)).replace("\\", "/"))
            summary = run_dir / "summary.json"
            if summary.is_file():
                try:
                    entry["summary"] = json.loads(summary.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    entry["summary"] = None
            runs.append(entry)
    return JSONResponse({"evidence_dir": "evidence/devfest-last-seat", "runs": runs})


# ---------------------------------------------------------------- stage page
PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DevFest 2026 · The Last Seat — 本地 Agent 现场演示</title>
<style>
:root{--bg:#1E1E1E;--card:#262626;--card2:#2D2D2D;--line:#3C4043;--txt:#F0F0F0;--dim:#9AA0A6;
--blue:#4285F4;--green:#34A853;--red:#EA4335;--yellow:#FBBC04}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{font-family:"Segoe UI",Roboto,system-ui,sans-serif;background:var(--bg);color:var(--txt);
display:flex;flex-direction:column;min-height:100vh}
button{font-family:inherit}
/* DevFest geometric decorations: original CSS geometry in Google four colors */
.geo{position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.5}
.geo i{position:absolute;border-radius:50%;display:block}
.geo .c1{width:340px;height:340px;right:-90px;top:-120px;border:2px solid rgba(66,133,244,.35)}
.geo .c2{width:220px;height:220px;right:180px;top:-80px;border:2px solid rgba(251,188,4,.3)}
.geo .c3{width:150px;height:150px;left:-50px;bottom:120px;border:2px solid rgba(52,168,83,.3)}
.geo .plus{position:absolute;width:22px;height:22px}
.geo .plus::before,.geo .plus::after{content:"";position:absolute;background:rgba(234,67,53,.4)}
.geo .plus::before{left:9px;top:0;width:4px;height:22px}
.geo .plus::after{top:9px;left:0;height:4px;width:22px}
header{position:relative;z-index:1;padding:20px 34px 16px;border-bottom:1px solid var(--line);
background:linear-gradient(180deg,#232323,#1E1E1E)}
.hbar{position:absolute;left:0;right:0;top:0;height:6px;
background:linear-gradient(90deg,var(--blue) 0 25%,var(--red) 25% 50%,var(--yellow) 50% 75%,var(--green) 75% 100%)}
.hrow{display:flex;align-items:center;gap:18px;flex-wrap:wrap}
h1{margin:0;font-size:30px;font-weight:800;letter-spacing:.5px}
h1 .g4 span{color:var(--blue)}h1 .g4 b{color:var(--red)}h1 .g4 i{color:var(--yellow);font-style:normal}
h1 .g4 u{color:var(--green);text-decoration:none}
.sub{color:var(--dim);font-size:15px;font-weight:400}
.modechip{margin-left:auto;display:flex;gap:10px;align-items:center}
.live{display:inline-flex;align-items:center;gap:8px;background:#3B0E0A;border:1px solid var(--red);
color:#FF8A80;border-radius:999px;padding:6px 16px;font-weight:800;font-size:15px}
.live .dot{width:10px;height:10px;border-radius:50%;background:var(--red);animation:blink 1s infinite}
@keyframes blink{50%{opacity:.25}}
.rchip{display:none;align-items:center;gap:8px;background:#3A2A10;border:1px solid var(--yellow);
color:#FFE082;border-radius:999px;padding:6px 16px;font-weight:800;font-size:15px}
.pchip{display:none;align-items:center;gap:8px;background:#0D243E;border:2px dashed var(--blue);
color:#8AB4F8;border-radius:999px;padding:6px 16px;font-weight:800;font-size:15px}
.banner{display:none;margin-top:12px;border-radius:10px;padding:10px 16px;font-size:15px;font-weight:700}
.banner.replay{display:block;background:#3A2A10;border:1px solid var(--yellow);color:#FFE082}
.banner.preview{display:block;background:#0D243E;border:2px dashed var(--blue);color:#8AB4F8}
.chips{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap}
.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:6px 16px;font-size:14px;color:var(--dim)}
.chip b{color:var(--txt);font-weight:700}
.chip.ok b{color:var(--green)}.chip.bad b{color:#FF8A80}
main{position:relative;z-index:1;flex:1;display:grid;grid-template-columns:46fr 54fr;gap:20px;
padding:22px 34px;max-width:1920px;margin:0 auto;width:100%}
.panel{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px;position:relative;overflow:hidden}
.panel h2{margin:0 0 14px;font-size:15px;color:var(--dim);font-weight:700;letter-spacing:2px;text-transform:uppercase}
/* LEFT: seats */
.seatnum{display:flex;align-items:baseline;gap:10px}
.seatnum .n{font-size:72px;font-weight:800;line-height:1}
.seatnum .d{font-size:26px;color:var(--dim);font-weight:700}
.seatnum.ok .n{color:var(--green)}
.seatnum.bad .n{color:#FF8A80}
.seatgrid{display:grid;grid-template-columns:repeat(8,56px);gap:7px;margin:16px 0 4px;justify-content:center}
.seat{aspect-ratio:1;border-radius:10px;display:flex;align-items:center;justify-content:center;
font-size:14px;font-weight:700;color:#E8EAED;background:var(--card2);border:1px solid var(--line)}
.seat.taken{background:#2E3A54;border-color:#3E5170}
.seat.last{background:transparent;border:2px dashed #5F6368;color:var(--dim);position:relative}
.seat.last.rejected{border-color:var(--red);color:#FF8A80;animation:pulseRed 1.4s infinite}
.seat.last.admitted{border-color:var(--green);color:#B9F6CA;background:#1E3620;animation:none}
@keyframes pulseRed{50%{box-shadow:0 0 0 6px rgba(234,67,53,.15)}}
.regrow{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap;align-items:center}
#nameinput{flex:1;min-width:200px;background:#1E1E1E;border:1px solid var(--line);color:var(--txt);
border-radius:999px;padding:12px 20px;font-size:16px}
#nameinput:focus-visible{outline:3px solid var(--blue)}
.bigbtn{border:0;border-radius:999px;padding:12px 30px;font-size:17px;font-weight:800;cursor:pointer;
background:linear-gradient(90deg,var(--blue),#7BAAF7);color:#fff}
.bigbtn:hover{filter:brightness(1.1)}
.bigbtn:disabled{opacity:.4;cursor:not-allowed}
.bigbtn.reset{background:transparent;border:1.5px solid var(--dim);color:var(--dim);font-weight:600}
.verdict{margin-top:14px;border-radius:12px;padding:14px 18px;font-size:16px;line-height:1.5}
.verdict.rej{background:#3B0E0A;border:1px solid var(--red);color:#FF8A80}
.verdict.ok{background:#10331F;border:1px solid var(--green);color:#B9F6CA}
.verdict .why{display:block;color:var(--dim);font-size:13.5px;margin-top:4px}
.verdict .why code{font-family:Consolas,monospace;color:inherit}
/* celebration */
#win{display:none;margin-top:16px;text-align:center;border:2px solid var(--green);border-radius:16px;
padding:18px;background:linear-gradient(180deg,#12291B,#0F2416);position:relative;overflow:hidden}
#win h3{margin:0;font-size:44px;font-weight:900;color:var(--green);letter-spacing:1px}
#win .sm{color:#B9F6CA;font-size:15px;margin-top:4px}
.badge-card{margin:14px auto 0;max-width:420px;border-radius:14px;padding:14px 18px;text-align:left;
background:#1E1E1E;border:1px solid var(--line)}
.badge-card .ttl{font-weight:800;font-size:17px}
.badge-card .row{display:flex;justify-content:space-between;font-size:14px;color:#E8EAED;margin-top:6px}
.badge-card .row b{color:var(--txt)}
.badge-card .fake{display:inline-block;margin-top:10px;background:#17140C;border:1px solid var(--yellow);
color:#FFE082;font-size:12.5px;border-radius:999px;padding:3px 12px;font-weight:700}
.fourbar{height:6px;border-radius:3px;margin-top:10px;
background:linear-gradient(90deg,var(--blue) 0 25%,var(--red) 25% 50%,var(--yellow) 50% 75%,var(--green) 75% 100%)}
.confetti{position:absolute;top:-12px;width:9px;height:14px;opacity:.95;animation:fall linear forwards}
@keyframes fall{to{transform:translateY(560px) rotate(720deg);opacity:.1}}
.pvwmark{position:absolute;inset:0;display:none;align-items:center;justify-content:center;z-index:5;
background:rgba(13,36,62,.55);font-size:22px;font-weight:900;color:#8AB4F8;letter-spacing:2px;transform:rotate(-14deg)}
/* RIGHT: agent trace */
.feed{display:flex;flex-direction:column;gap:8px;height:300px;overflow-y:auto;padding-right:4px}
.ev{border:1px solid var(--line);border-left:3px solid var(--blue);border-radius:10px;
padding:8px 12px;font-size:14px;animation:pop .18s;background:var(--card2)}
@keyframes pop{from{transform:translateY(4px);opacity:0}}
.ev .t{color:var(--dim);margin-right:10px;font-family:Consolas,monospace;font-size:12.5px}
.ev.tool{border-left-color:var(--blue)}
.ev.policy{border-left-color:var(--yellow);color:#FFE082}
.ev.stage{border-left-color:#A78BFA}
.ev.pv{outline:2px dashed var(--blue)}
.pvtag{margin-left:8px;background:#0D243E;border:1px solid var(--blue);color:#8AB4F8;border-radius:6px;
font-size:11px;padding:1px 7px;font-weight:800}
.pyrow{display:flex;gap:10px;margin:12px 0;flex-wrap:wrap}
.pybox{flex:1;min-width:200px;background:#1E1E1E;border:1px solid var(--line);border-radius:12px;padding:10px 14px;font-size:14px}
.pybox .lab{color:var(--dim);font-size:12.5px;display:block;margin-bottom:4px}
.rbadge{display:inline-block;padding:2px 12px;border-radius:999px;font-weight:800;font-size:13px}
.rbadge.red{background:#3B0E0A;color:#FF8A80}.rbadge.green{background:#10331F;color:#B9F6CA}
.rbadge.gray{background:#2D2D2D;color:var(--dim)}
#diff{font-family:Consolas,monospace;font-size:13px;white-space:pre-wrap;background:#141414;
border:1px solid var(--line);border-radius:10px;padding:12px;max-height:200px;overflow:auto;display:none}
#diff .a{color:#FF8A80}#diff .b{color:#B9F6CA}#diff .h{color:var(--dim)}
#tokens{font-family:Consolas,monospace;font-size:13px;color:#C9D1D9;white-space:pre-wrap;
background:#141414;border:1px solid var(--line);border-radius:10px;padding:12px;height:130px;overflow-y:auto}
footer{position:relative;z-index:1;border-top:1px solid var(--line);padding:12px 34px;display:flex;
gap:22px;flex-wrap:wrap;color:var(--dim);font-size:13.5px;background:#1B1B1B}
footer b{color:#E8EAED}
.gpuchip{font-weight:800}
.gpuchip.verified b{color:var(--green)}
/* evidence modal */
#evmodal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:50;align-items:center;justify-content:center}
#evmodal .box{background:var(--card);border:1px solid var(--line);border-radius:16px;max-width:760px;
width:92%;max-height:80vh;overflow-y:auto;padding:24px}
#evmodal h3{margin:0 0 6px;font-size:20px}
#evmodal pre{background:#141414;border:1px solid var(--line);border-radius:10px;padding:12px;
font-size:12.5px;overflow-x:auto;white-space:pre-wrap}
.btnrow{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
.smbtn{background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:999px;
padding:8px 18px;font-size:14px;cursor:pointer;font-weight:700}
.smbtn:hover{border-color:var(--blue)}
@media (max-width:1100px){main{grid-template-columns:1fr}.seatnum .n{font-size:60px}}
</style></head><body>
<div class="geo" aria-hidden="true"><i class="c1"></i><i class="c2"></i><i class="c3"></i>
<b class="plus" style="left:12%;top:120px"></b><b class="plus" style="right:30%;bottom:90px"></b></div>
<header><div class="hbar"></div>
 <div class="hrow">
  <h1><span class="g4"><span>The</span> <b>Last</b> <i>Seat</i> <u>·</u></span> DevFest 2026 · Guangzhou</h1>
  <span class="sub">本地 Gemma 4 26B · Antigravity SDK · Ollama · AMD ROCm — 全现场、无云端</span>
  <div class="modechip">
   <span class="live" id="livechip"><span class="dot"></span>LIVE</span>
   <span class="rchip" id="replaychip">⏵ REPLAY 回放</span>
   <span class="pchip" id="previewchip">◈ DESIGN PREVIEW</span>
  </div>
 </div>
 <div class="banner replay" id="replaybanner">🔁 <b>REPLAY — 录制回放</b>：以下为已录制的历史运行，不代表当前硬件或实时测试验证。</div>
 <div class="banner preview" id="previewbanner">◈ <b>DESIGN PREVIEW — 设计预览，非真实 Agent 运行</b>：事件为合成时间线，不触碰工作区、不产生证据、不计入验收。</div>
 <div class="chips" id="chips"><div class="chip">加载中…</div></div>
</header>
<main>
 <section class="panel" id="leftpanel">
  <h2>Workshop 报名 · 实时应用（真实代码）</h2>
  <div class="seatnum bad" id="seatnum"><span class="n" id="seattaken">39</span><span class="d">/ 40 seats</span></div>
  <div class="seatgrid" id="seatgrid" aria-label="座位图"></div>
  <div class="regrow">
   <input id="nameinput" maxlength="24" placeholder="第 40 位开发者姓名（可选，仅本地显示）" autocomplete="off">
   <button class="bigbtn" id="regbtn" onclick="doRegister()">报名第 40 席</button>
  </div>
  <div class="verdict rej" id="verdict">等待报名…<span class="why">UI 状态由真实场景代码（隔离进程执行 checkin.py）派生，非硬编码。</span></div>
  <div id="win">
   <h3>YOU'RE IN!</h3>
   <div class="sm" id="winline">40 / 40 — 最后一席已由本地 Agent 修复并独立验证</div>
   <div class="badge-card">
    <div class="ttl">🦾 Builder Pass</div>
    <div class="row"><span>持有人</span><b id="passname">Guest Developer</b></div>
    <div class="row"><span>席位</span><b>No. 40 / 40</b></div>
    <div class="row"><span>签发</span><b>DevFest 2026 · Guangzhou</b></div>
    <div class="fourbar"></div>
    <span class="fake">虚构纪念徽章 · 非入场凭证 · Fictional commemorative badge — not an admission ticket</span>
   </div>
  </div>
  <div class="pvwmark" id="pvwmark">PREVIEW · 模拟画面 · 非真实结果</div>
 </section>
 <section class="panel">
  <h2>Agent 执行轨迹 · 真实工具调用</h2>
  <div class="btnrow">
   <button class="bigbtn" id="runbtn" onclick="doRun()">▶ 运行：让本地 Agent 抢回最后一席</button>
   <button class="smbtn" onclick="doReset()">↺ 演示重置</button>
   <button class="smbtn" onclick="showEvidence()">🗂 验证证据</button>
  </div>
  <div class="pyrow">
   <div class="pybox"><span class="lab">独立 pytest · 修复前（预期红）</span><span id="py_before"><span class="rbadge gray">未运行</span></span> <span id="pyb_tail" style="color:var(--dim);font-size:12.5px"></span></div>
   <div class="pybox"><span class="lab">独立 pytest · 修复后（预期全绿）</span><span id="py_after"><span class="rbadge gray">未运行</span></span> <span id="pya_tail" style="color:var(--dim);font-size:12.5px"></span></div>
  </div>
  <div class="feed" id="feed" aria-live="polite"><div class="ev stage"><span class="t">--:--:--</span>等待启动…</div></div>
  <h2 style="margin-top:16px">Agent Diff（仅白名单源文件）</h2>
  <div id="diff"></div>
  <h2 style="margin-top:16px">模型输出（流式）</h2>
  <div id="tokens">（运行后显示）</div>
 </section>
</main>
<footer>
 <span>模型 <b id="f_model">gemma4:26b</b></span>
 <span class="gpuchip" id="f_gpu">GPU 后端 <b>未验证</b></span>
 <span>验证 <b>独立 pytest 子进程 + SHA256 + 白名单 diff</b></span>
 <span>地址 <b id="f_port">http://127.0.0.1:8421</b></span>
 <span id="f_mode">模式 <b>LIVE</b></span>
 <span>策略边界 <b>非 OS 沙箱</b></span>
</footer>
<div id="evmodal"><div class="box">
 <h3>🗂 物理 GPU 验证证据（只读）</h3>
 <div id="evbody" style="font-size:14px;color:var(--dim)">加载中…</div>
 <div class="btnrow"><button class="smbtn" onclick="document.getElementById('evmodal').style.display='none'">关闭</button></div>
</div></div>
<script>
const ROSTER=39; const GCOLORS=['#4285F4','#EA4335','#FBBC04','#34A853'];
const ICON={view_file:'👁',create_file:'📝',edit_file:'✏️',run_command:'⚙️',list_directory:'📁',find_file:'🔍',search_directory:'🔎'};
let es=null,running=false,mode='live',won=false;
function el(id){return document.getElementById(id);}
function txt(id,s){el(id).textContent=s;}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function buildSeats(){const g=el('seatgrid');g.textContent='';
 for(let i=1;i<=40;i++){const d=document.createElement('div');d.className='seat'+(i<40?' taken':' last');d.textContent=i<40?i:'';d.id='seat'+i;g.appendChild(d);}}
function setMode(m){mode=m;
 el('livechip').style.display=(m==='live')?'inline-flex':'none';
 el('replaychip').style.display=(m==='replay')?'inline-flex':'none';
 el('previewchip').style.display=(m==='preview')?'inline-flex':'none';
 el('replaybanner').style.display=(m==='replay')?'block':'none';
 el('previewbanner').style.display=(m==='preview')?'block':'none';
 txt('f_mode','模式 '+(m==='live'?'LIVE':m==='replay'?'REPLAY':'DESIGN PREVIEW'));}
function pvTag(){const s=document.createElement('span');s.className='pvtag';s.textContent='预览';return s;}
function addEv(node,cls,pv){const f=el('feed');const d=document.createElement('div');d.className='ev '+(cls||'')+(pv?' pv':'');
 d.appendChild(node);if(pv)d.appendChild(pvTag());f.appendChild(d);while(f.children.length>120)f.removeChild(f.firstChild);f.scrollTop=f.scrollHeight;}
function row(parts,pv){const r=document.createElement('span');parts.forEach(p=>{if(typeof p==='string'){r.appendChild(document.createTextNode(p));}
 else{const b=document.createElement('b');if(p.cls)b.className=p.cls;b.textContent=p.b;r.appendChild(b);}});addEv(r,undefined,pv);return r;}
function tsSpan(){const s=document.createElement('span');s.className='t';s.textContent=new Date().toLocaleTimeString();return s;}
async function refresh(){try{const s=await (await fetch('/api/status')).json();
 const chips=el('chips');chips.textContent='';
 const c=(ok,label,val)=>{const d=document.createElement('div');d.className='chip '+(ok?'ok':'bad');
  d.appendChild(document.createTextNode(label+'：'));const b=document.createElement('b');b.textContent=val;d.appendChild(b);chips.appendChild(d);};
 c(s.server,'Ollama',s.server||'离线');
 c(s.model_listed,'模型',s.model_listed?s.model:'缺失');
 c(s.model_resident,'GPU 驻留',s.model_resident?'是':'否');
 c(s.workspace_at_baseline,'缺陷基线',s.workspace_at_baseline?'就绪':'已修复/漂移');
 if(s.gpu_name){const d=document.createElement('div');d.className='chip';d.appendChild(document.createTextNode('GPU：'));
  const b=document.createElement('b');b.textContent=s.gpu_name;d.appendChild(b);chips.appendChild(d);}
 txt('f_model',s.model);txt('f_port','http://127.0.0.1:'+s.port);
 if(!running&&!won){el('runbtn').disabled=(s.mode==='preview')?false:!(s.server&&s.model_listed);
  setMode(s.mode==='preview'?'preview':mode);}
}catch(e){}}
function setPy(phase,exit,tail,pv){const bid=phase==='before'?'py_before':'py_after';
 const box=el(bid);box.textContent='';
 const b=document.createElement('span');b.className='rbadge '+(exit===null||exit===undefined?'gray':exit===0?'green':'red');
 b.textContent=pv?'预览':(exit===0?'✓ PASS':'✗ FAIL');box.appendChild(b);
 if(pv){const t=document.createElement('span');t.className='pvtag';t.textContent='预览';box.appendChild(t);}
 txt(phase==='before'?'pyb_tail':'pya_tail',tail||'');}
function confetti(){const win=el('win');for(let i=0;i<44;i++){const c=document.createElement('span');c.className='confetti';
 c.style.left=Math.random()*100+'%';c.style.background=GCOLORS[i%4];
 c.style.animationDuration=(2.4+Math.random()*2.2)+'s';c.style.animationDelay=(Math.random()*0.9)+'s';
 win.appendChild(c);setTimeout(()=>c.remove(),5200);}}
function celebrate(name,preview){won=true;
 el('seatnum').className='seatnum ok';txt('seattaken','40');
 const s40=el('seat40');if(s40){s40.className='seat last admitted';s40.textContent='40';}
 const v=el('verdict');v.className='verdict ok';
 v.textContent='';const strong=document.createElement('b');strong.textContent='报名成功 — 席位 40/40';
 v.appendChild(strong);const why=document.createElement('span');why.className='why';
 why.textContent=preview?'预览模式：模拟成功画面（未执行真实修复）':'真实场景代码在隔离进程中确认：register(name, 39) → seat 40';
 v.appendChild(why);
 txt('passname',name||'Guest Developer');
 el('win').style.display='block';el('pvwmark').style.display=preview?'flex':'none';
 if(!preview){confetti();el('runbtn').disabled=false;}
 el('regbtn').disabled=false;}
async function applyProbe(d,serverName,preview){
 const p=d.probe||{};const v=el('verdict');
 const name=(el('nameinput').value||'').trim()||serverName||p.name||'Guest Developer';
 if(d.source==='preview-mock'){celebrate(name,true);return;}
 if(p.probe_error){v.className='verdict rej';v.textContent='探针错误：'+p.probe_error;return;}
 if(p.admitted){celebrate(name,false);return;}
 won=false;el('win').style.display='none';el('pvwmark').style.display='none';
 el('seatnum').className='seatnum bad';txt('seattaken','39');
 const s40=el('seat40');if(s40){s40.className='seat last rejected';s40.textContent='40';}
 v.className='verdict rej';v.textContent='';
 const b=document.createElement('b');b.textContent='✗ 报名被拒 — '+((p.error_text||'workshop is at full capacity (39/40 taken)'));
 v.appendChild(b);
 const why=document.createElement('span');why.className='why';
 const code=document.createElement('code');
 code.textContent='checkin.register('+JSON.stringify(p.name||name||'Guest Developer')+', 39) → '+p.error_type+'';
 why.appendChild(document.createTextNode('由真实场景代码在隔离进程执行：'));why.appendChild(code);v.appendChild(why);}
async function doRegister(){if(running)return;const name=el('nameinput').value||'Guest Developer';
 el('regbtn').disabled=true;
 const r=await (await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})).json();
 el('regbtn').disabled=false;
 if(r.error){alert(r.error);return;}
 applyProbe(r,{name:r.name});}
async function doRun(){if(running)return;running=true;
 el('feed').textContent='';el('tokens').textContent='（运行中…）';el('diff').style.display='none';
 setPy('before',null,'');setPy('after',null,'');
 el('runbtn').disabled=true;
 const r=await (await fetch('/api/run',{method:'POST'})).json();
 if(r.error){alert(r.error);running=false;el('runbtn').disabled=false;}}
async function doReset(){if(running)return;
 const r=await (await fetch('/api/reset',{method:'POST'})).json();
 if(r.error){alert(r.error);return;}
 won=false;setPy('before',null,'');setPy('after',null,'');el('win').style.display='none';
 el('diff').style.display='none';applyProbe(r,'Guest Developer');}
async function showEvidence(){el('evmodal').style.display='flex';
 const r=await (await fetch('/api/evidence')).json();const b=el('evbody');b.textContent='';
 if(!r.runs||!r.runs.length){b.textContent='暂无已发布的物理验证证据。LIVE 运行证据落盘于 artifacts/devfest-agent-execution/，精选证据将收录到 '+r.evidence_dir+'。';return;}
 r.runs.forEach(run=>{const h=document.createElement('b');h.textContent='▶ '+run.run;b.appendChild(h);
  if(run.summary){const pre=document.createElement('pre');pre.textContent=JSON.stringify(run.summary,null,1);b.appendChild(pre);}
  const list=document.createElement('div');list.textContent='文件：'+run.files.join(' · ');b.appendChild(list);
  const sp=document.createElement('div');sp.style.height='14px';b.appendChild(sp);});}
function connect(){es=new EventSource('/api/stream');
 es.addEventListener('replay',e=>{const d=JSON.parse(e.data);setMode('replay');
  txt('replaybanner','🔁 REPLAY — 录制回放：run '+(d.run_id||'?')+' @ '+(d.recorded_at||'')+'（不代表当前硬件或实时验证）');});
 es.addEventListener('replay_done',()=>{setMode('live');el('runbtn').disabled=false;});
 es.addEventListener('stage',e=>{const s=JSON.parse(e.data);const r=document.createElement('span');
  r.appendChild(tsSpan());r.appendChild(document.createTextNode('🔹 '+s.text));addEv(r,'stage',s.preview);});
 es.addEventListener('tool',e=>{const d=JSON.parse(e.data);const r=document.createElement('span');
  r.appendChild(tsSpan());r.appendChild(document.createTextNode((ICON[d.name]||'🔧')+' '));
  const b=document.createElement('b');b.textContent=d.name;r.appendChild(b);
  const a=document.createElement('span');a.style.color='var(--dim)';a.textContent=' '+d.args;r.appendChild(a);
  addEv(r,'tool',d.preview);});
 es.addEventListener('policy',e=>{const s=JSON.parse(e.data);const r=document.createElement('span');
  r.appendChild(tsSpan());r.appendChild(document.createTextNode('🛡️ 策略：'));
  const b=document.createElement('b');b.style.color=s.includes('ALLOW')?'var(--green)':'#FF8A80';
  b.textContent=s.includes('ALLOW')?'ALLOW':'DENY';r.appendChild(b);
  r.appendChild(document.createTextNode(' '+(s.split('args=')[1]||'')));addEv(r,'policy',s.preview!==undefined&&String(s).includes('预览'));});
 const tk=el('tokens');
 es.addEventListener('token',e=>{const s=document.createElement('span');s.textContent=JSON.parse(e.data);
  tk.appendChild(s);tk.scrollTop=tk.scrollHeight;});
 es.addEventListener('pytest',e=>{const d=JSON.parse(e.data);setPy(d.phase,d.exit,d.tail,d.preview);});
 es.addEventListener('gpu',e=>{const d=JSON.parse(e.data);const chip=el('f_gpu');
  chip.className='gpuchip'+(d.backend==='ROCm VERIFIED'?' verified':'');
  chip.textContent='GPU 后端 ';const b=document.createElement('b');
  b.textContent=d.preview?'[预览] 未验证':(d.backend+(d.digest?' · '+String(d.digest).slice(7,19):''));
  chip.appendChild(b);});
 es.addEventListener('diff',e=>{const d=JSON.parse(e.data);if(!d.patch)return;
  const box=el('diff');box.style.display='block';
  box.innerHTML=d.patch.split('\\n').map(l=>
   l.startsWith('+')?`<span class="b">${esc(l)}</span>`:
   l.startsWith('-')?`<span class="a">${esc(l)}</span>`:
   `<span class="h">${esc(l)}</span>`).join('\\n');});
 es.addEventListener('seat',e=>{const d=JSON.parse(e.data);applyProbe(d,d.probe&&d.probe.name,!!d.preview);});
 es.addEventListener('done',e=>{const d=JSON.parse(e.data);running=false;
  const parts=[];
  if(d.preview){parts.push({b:'◈ 设计预览结束',cls:''},d.note||'');}
  else if(d.ok){parts.push({b:'✅ 任务成功',cls:''},' — 独立验证全绿，变更文件：'+((d.files_changed||[]).join(', ')||'(无)'),
   ' · 耗时 '+(d.elapsed_s||'?')+'s');}
  else{parts.push({b:'⚠️ 未通过独立验收',cls:''},' '+(d.tests_tampered?'测试被改动 → 拒绝':(d.forbidden_changed&&d.forbidden_changed.length)?'白名单外文件被改：'+d.forbidden_changed.join(','):'未完成修复'));
   el('runbtn').disabled=false;}
  row(parts,d.preview);});
 es.addEventListener('error',e=>{if(!e.data)return;
  try{const d=JSON.parse(e.data);const r=document.createElement('span');r.appendChild(tsSpan());
  r.appendChild(document.createTextNode('⚠️ '+(d.message||'run error')));addEv(r,'stage');running=false;el('runbtn').disabled=false;}catch(_){}});}
buildSeats();setMode('live');refresh();connect();setInterval(refresh,5000);
(async()=>{try{const s=await (await fetch('/api/status')).json();
 let d;
 if(!s.workspace_has_engine){const rr=await (await fetch('/api/reset',{method:'POST'})).json();d=rr;}
 else{d=await (await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"name":"Guest Developer"}'})).json();}
 if(d&&!d.error)applyProbe(d,d.name||'Guest Developer');}catch(e){}})();
</script></body></html>"""


# ---------------------------------------------------------------- entrypoint
_PREVIEW_ONLY = False


def main() -> int:
    global PORT_DEFAULT, _PREVIEW_ONLY
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--record", type=Path, default=None, help="write real runs' event stream here")
    ap.add_argument(
        "--replay", type=Path, default=None, help="replay a recording (visibly labeled)"
    )
    ap.add_argument("--replay-speed", type=float, default=1.0)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="DESIGN PREVIEW: synthetic labeled timeline; no Ollama/GPU needed; never acceptance",
    )
    args = ap.parse_args()
    PORT_DEFAULT = args.port

    if sum(1 for flag in (args.record, args.replay, args.dry_run) if flag) > 1:
        print("ERROR: --record / --replay / --dry-run are mutually exclusive")
        return 2

    if args.record:
        set_recorder(args.record.resolve())
        print(f"Recording runs to: {args.record.resolve()} (last completed run wins)")
    if args.replay:
        try:
            recording = load_replay(args.replay)
        except ValueError as e:
            print(f"ERROR: {e}")
            return 2
        start_replay(recording, args.replay_speed)
        print(f"REPLAY mode: {args.replay} (speed {args.replay_speed}x) — live runs disabled")
    if args.dry_run:
        _PREVIEW_ONLY = True
        with _lock:
            _state["mode"] = "preview"
        print("DESIGN PREVIEW mode — synthetic labeled timeline; NOT a live agent run")

    app = Starlette(
        routes=[
            Route("/", homepage),
            Route("/api/status", api_status),
            Route("/api/run", api_run, methods=["POST"]),
            Route("/api/reset", api_reset, methods=["POST"]),
            Route("/api/register", api_register, methods=["POST"]),
            Route("/api/evidence", api_evidence),
            Route("/api/stream", api_stream),
        ]
    )
    import uvicorn

    print(f"Stage dashboard: http://127.0.0.1:{args.port}  (Ctrl+C stops)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
