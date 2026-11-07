"""Local web dashboard for the DevFest demo (no new dependencies).

Serves a single page at http://127.0.0.1:8420 with:
  - live status chips (server / model residency / backend / workspace baseline)
  - a big RUN button that executes the real agent bug-fix task in-process
  - live SSE stream: stages, tool calls, policy decisions, model tokens,
    pytest red->green, final diff
Uses the SAME locked-down config and verification helpers as the CLI demo,
so the GUI shows exactly what the audited pipeline does.

Run:  python -I src/webui.py [--port 8420]
      python -I src/webui.py --record run.json    # capture real runs
      python -I src/webui.py --replay run.json [--replay-speed 1.5]
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
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

from config import DEFAULT_MODEL, DEMO_WORKSPACE, PROJECT_ROOT, build_config  # noqa: E402
from demo_agent import (  # noqa: E402
    MISSION,
    PRISTINE,
    restore_bug_baseline,
    run_pytest_independently,
    workspace_state,
)

OLLAMA = Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"

# ---------------------------------------------------------------- run state
_lock = threading.Lock()
_events: deque = deque(maxlen=4000)
_cond = threading.Condition()
_state = {"running": False, "replay": False, "run_id": None, "started_at": None}
_seq = {"n": 0}

# recorder (--record): captures every emitted event so a real run can be
# replayed (--replay), rendered to a GIF, or used as an on-stage fallback.
_recorder: list[dict] | None = None
_recorder_path: Path | None = None


def set_recorder(path: Path | None) -> None:
    """Arm/disarm event recording; the LAST completed run wins (file is rewritten)."""
    global _recorder, _recorder_path
    if path is None:
        _recorder = None
        _recorder_path = None
    else:
        _recorder = []
        _recorder_path = Path(path)


def dump_recording() -> Path | None:
    """Write the recorded event stream to disk (no-op when not recording)."""
    if _recorder is None or _recorder_path is None:
        return None
    payload = {
        "format": 1,
        "meta": {
            "run_id": _state.get("run_id"),
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": DEFAULT_MODEL,
        },
        "events": list(_recorder),
    }
    _recorder_path.parent.mkdir(parents=True, exist_ok=True)
    _recorder_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return _recorder_path


def load_replay(path: Path) -> dict:
    """Load and validate a recording; ValueError with a one-line message on bad input."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read replay file {path}: {e}") from e
    if (
        not isinstance(rec, dict)
        or rec.get("format") != 1
        or not isinstance(rec.get("events"), list)
    ):
        raise ValueError(f"unsupported replay file {path}: expected format 1 with an events list")
    return rec


def run_allowed() -> tuple[bool, str]:
    """Synchronous gate for /api/run: live runs and replays never interleave."""
    with _lock:
        if _state["replay"]:
            return False, "replay in progress"
        if _state["running"]:
            return False, "a run is already in progress"
    return True, ""


def _append_replay_event(ev: dict) -> None:
    """Feed a recorded event into the live SSE stream (fresh id, original kind/data/t)."""
    _seq["n"] += 1
    with _cond:
        _events.append(
            {
                "id": _seq["n"],
                "kind": ev.get("kind", "event"),
                "data": ev.get("data"),
                "t": ev.get("t", "--:--:--"),
            }
        )
        _cond.notify_all()


def start_replay(recording: dict, speed: float = 1.0) -> bool:
    """Replay a recorded run through the same SSE pipeline, at original pacing.

    The UI receives a leading `replay` event (banner) and a trailing
    `replay_done` marker. Per-event wait is capped at 2 s so a long real run
    replays briskly even at speed 1.0. While replaying, /api/run returns 409.
    """
    events = list(recording.get("events", []))
    meta = recording.get("meta") or {}
    with _lock:
        if _state["running"] or _state["replay"]:
            return False
        _state.update(running=True, replay=True, run_id=meta.get("run_id"))

    emit("replay", {"run_id": meta.get("run_id"), "recorded_at": meta.get("recorded_at")})

    def feeder() -> None:
        try:
            prev_ts = None
            for ev in events:
                ts = ev.get("ts")
                if isinstance(ts, int | float) and prev_ts is not None:
                    time.sleep(min(max(ts - prev_ts, 0.0) / max(speed, 0.01), 2.0))
                prev_ts = ts
                _append_replay_event(ev)
            emit("replay_done", {"run_id": meta.get("run_id")})
        finally:
            with _lock:
                _state["running"] = False

    threading.Thread(target=feeder, daemon=True).start()
    return True


def emit(kind: str, data) -> None:
    ts = time.time()
    _seq["n"] += 1
    ev = {"id": _seq["n"], "kind": kind, "data": data, "t": time.strftime("%H:%M:%S")}
    with _cond:
        _events.append(ev)
        _cond.notify_all()
    if _recorder is not None:
        _recorder.append({**ev, "data": copy.deepcopy(data), "ts": ts})


# ---------------------------------------------------------------- the task
def run_demo_task(run_id: str) -> None:
    try:
        emit("stage", {"name": "restore", "text": "重置 demo_workspace 到 bug 基线"})
        restore_bug_baseline()
        before = workspace_state(DEMO_WORKSPACE)

        emit("stage", {"name": "pytest_before", "text": "独立 pytest（期望：红）"})
        pre = run_pytest_independently(DEMO_WORKSPACE)
        emit(
            "pytest",
            {
                "phase": "before",
                "exit": pre["exit_code"],
                "tail": pre["stdout"].strip().splitlines()[-1] if pre["stdout"] else "",
            },
        )

        audit: list[str] = []
        ev_dir = PROJECT_ROOT / "artifacts" / "agent-execution" / f"run-{run_id}"
        ev_dir.mkdir(parents=True, exist_ok=True)

        emit("stage", {"name": "agent", "text": "Agent 开始任务（本地 Gemma 4）"})

        async def agent_loop() -> None:
            from google.antigravity import Agent

            cfg = build_config(
                audit_log=audit,
                system_instructions=(
                    "You are a careful Python engineer. Prefer minimal, correct fixes. "
                    "Use only the provided tools; modify files with edit_file; "
                    "run tests with run_command 'python -m pytest tests/ -q'."
                ),
            )
            seen_audit = 0
            t0 = time.time()
            async with Agent(cfg) as agent:
                resp = await agent.chat(MISSION)

                async def tokens():
                    nonlocal seen_audit
                    buf: list[str] = []
                    last = time.time()
                    async for t in resp:
                        buf.append(t)
                        if len(buf) >= 24 or time.time() - last > 0.5:
                            emit("token", "".join(buf))
                            buf, last = [], time.time()
                            while seen_audit < len(audit):
                                emit("policy", audit[seen_audit])
                                seen_audit += 1
                    if buf:
                        emit("token", "".join(buf))

                async def tools():
                    async for c in resp.tool_calls:
                        emit(
                            "tool",
                            {
                                "name": getattr(c, "name", "?"),
                                "args": str(getattr(c, "args", None))[:220],
                            },
                        )

                await asyncio.gather(tokens(), tools())
                while seen_audit < len(audit):
                    emit("policy", audit[seen_audit])
                    seen_audit += 1
            emit("meta", {"elapsed_s": round(time.time() - t0, 1)})

        asyncio.run(asyncio.wait_for(agent_loop(), timeout=900))

        after = workspace_state(DEMO_WORKSPACE)
        changed = sorted(
            k
            for k in set(before) | set(after)
            if before.get(k, {}).get("sha256") != after.get(k, {}).get("sha256")
        )
        emit("stage", {"name": "pytest_after", "text": "独立 pytest 验证（期望：全绿）"})
        post = run_pytest_independently(DEMO_WORKSPACE)
        emit(
            "pytest",
            {
                "phase": "after",
                "exit": post["exit_code"],
                "tail": post["stdout"].strip().splitlines()[-1] if post["stdout"] else "",
            },
        )

        diff = subprocess.run(
            ["git", "diff", "--no-index", "--", str(PRISTINE), str(DEMO_WORKSPACE)],
            capture_output=True,
            text=True,
        ).stdout
        emit("diff", {"files": changed, "patch": (diff or "")[:8000]})

        ok = pre["exit_code"] != 0 and post["exit_code"] == 0
        summary = {
            "run_id": run_id,
            "ok": ok,
            "files_changed": changed,
            "bug_present_before": pre["exit_code"] != 0,
            "bug_fixed_after": post["exit_code"] == 0,
        }
        (ev_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        emit("done", summary)
    except Exception as e:
        emit("error", {"message": f"{type(e).__name__}: {e}"})
    finally:
        dump_recording()  # write the full stream, then reset for the next run
        if _recorder is not None:
            _recorder.clear()


def start_run() -> bool:
    with _lock:
        if _state["running"] or _state["replay"]:
            return False
        _state.update(
            running=True,
            run_id=f"web-{time.strftime('%H%M%S')}",
            started_at=time.strftime("%H:%M:%S"),
        )
        rid = _state["run_id"]

    def wrapper():
        try:
            run_demo_task(rid)
        finally:
            with _lock:
                _state["running"] = False

    threading.Thread(target=wrapper, daemon=True).start()
    return True


# ---------------------------------------------------------------- endpoints
async def homepage(request: Request) -> Response:
    return Response(PAGE, media_type="text/html; charset=utf-8")


async def api_status(request: Request) -> JSONResponse:
    status = {
        "server": False,
        "model": DEFAULT_MODEL,  # active model (AGENT_MODEL-aware) so the chip never lies
        "model_listed": False,
        "model_resident": False,
        "backend": "unknown",
        "workspace_at_baseline": False,
        "replay": _state.get("replay", False),
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
    for log in [ROOT / "artifacts/rc1-serve.log", Path.home() / "AppData/Local/Ollama/server.log"]:
        if log.exists():
            txt = log.read_text(encoding="utf-8", errors="replace")
            last = txt.rfind("inference compute")
            if last >= 0:
                line = txt[last : last + 400]
                if "library=ROCm" in line:
                    m = re.search(r"compute=(\S+)", line)
                    status["backend"] = f"ROCm ({m.group(1)})" if m else "ROCm"
                elif "library=Vulkan" in line:
                    status["backend"] = "Vulkan"
                elif "library=CUDA" in line:
                    status["backend"] = "CUDA"
                break
    try:
        import hashlib

        a = hashlib.sha256((PRISTINE / "stats_utils.py").read_bytes()).hexdigest()
        b = hashlib.sha256((DEMO_WORKSPACE / "stats_utils.py").read_bytes()).hexdigest()
        status["workspace_at_baseline"] = a == b
    except Exception:
        pass
    status["running"] = _state["running"]
    return JSONResponse(status)


async def api_run(request: Request):
    ok, msg = run_allowed()
    if not ok:
        return JSONResponse({"error": msg}, status_code=409)
    if not start_run():
        return JSONResponse({"error": "a run is already in progress"}, status_code=409)
    return JSONResponse({"started": True, "run_id": _state["run_id"]})


async def api_stream(request: Request):
    last_id = 0

    async def gen():
        nonlocal last_id
        while True:
            if await request.is_disconnected():
                return
            with _cond:
                _cond.wait(timeout=0.5)
                batch = [e for e in _events if e["id"] > last_id]
            for e in batch:
                last_id = e["id"]
                yield {"event": e["kind"], "data": json.dumps(e["data"], ensure_ascii=False)}
            if not batch:
                yield {"event": "ping", "data": ""}

    return EventSourceResponse(gen())


PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>本地 AI Agent — Gemma 4 × Antigravity SDK × ROCm</title>
<style>
:root{--bg:#0b1020;--card:#121a30;--line:#233350;--txt:#dbe6ff;--dim:#7d8db5;--acc:#4f8cff;--ok:#2ecc71;--bad:#ff5d73}
*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--txt)}
header{padding:18px 28px;background:linear-gradient(90deg,#101a36,#0b1020);border-bottom:1px solid var(--line)}
h1{margin:0;font-size:20px}h1 small{color:var(--dim);font-weight:400;margin-left:10px;font-size:13px}
.chips{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:5px 14px;font-size:13px}
.chip b{color:var(--acc)} .chip.ok b{color:var(--ok)} .chip.bad b{color:var(--bad)}
main{display:grid;grid-template-columns:1.1fr .9fr;gap:16px;padding:20px 28px;max-width:1400px;margin:auto}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.panel h2{margin:0 0 10px;font-size:15px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
#runbtn{width:100%;padding:16px;font-size:18px;border:0;border-radius:12px;background:linear-gradient(90deg,#2f6bff,#7a4dff);color:#fff;cursor:pointer;font-weight:600}
#runbtn:disabled{opacity:.45;cursor:not-allowed}
.feed{display:flex;flex-direction:column;gap:8px;max-height:340px;overflow-y:auto}
.ev{border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:13px;animation:pop .18s}
@keyframes pop{from{transform:translateY(4px);opacity:0}}
.ev .t{color:var(--dim);margin-right:8px;font-family:Consolas,monospace}
.ev.tool{border-left:3px solid var(--acc)} .ev.policy{border-left:3px solid #ffb454;color:#ffd9a8}
.ev.stage{border-left:3px solid #9aa7ff}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:700;font-size:13px}
.badge.red{background:#3d1723;color:var(--bad)} .badge.green{background:#10331f;color:var(--ok)}
#tokens{font-family:Consolas,monospace;font-size:12.5px;color:#aebdda;white-space:pre-wrap;max-height:220px;overflow-y:auto;background:#0a0f1f;border:1px solid var(--line);border-radius:8px;padding:10px}
#diff{font-family:Consolas,monospace;font-size:12.5px;white-space:pre-wrap;background:#0a0f1f;border:1px solid var(--line);border-radius:8px;padding:10px;max-height:260px;overflow:auto}
#diff .a{color:var(--bad)} #diff .b{color:var(--ok)}
footer{color:var(--dim);font-size:12px;text-align:center;padding:14px}
</style></head><body>
<header><h1>🖥️ 本地 AI Agent 演示台 <small>Gemma 4 26B · Antigravity SDK · Ollama · AMD ROCm (gfx1151) — 全本地，无云端</small></h1>
<div id="replaybanner" style="display:none;background:#3a2a10;border:1px solid #ffb454;color:#ffd9a8;border-radius:8px;padding:8px 14px;margin-top:10px;font-size:14px">🔁 <b>REPLAY</b> — 录制回放（非实时运行）· run <span id="replayid"></span></div>
<div class="chips" id="chips"><div class="chip">加载中…</div></div></header>
<main>
 <div class="panel"><h2>演示控制</h2>
  <button id="runbtn" onclick="doRun()">▶ 运行 Demo：让本地 Agent 修复真实 Bug</button>
  <p style="color:var(--dim);font-size:13px" id="hint">任务：Agent 将自己读代码 → 跑 pytest（红）→ 定位 bug → 修改文件 → 复测（绿）。全程策略沙箱约束，最终由独立测试进程验收。</p>
  <div class="panel" style="padding:12px;background:#0a0f1f"><h2 style="font-size:12px">🧪 pytest 验收</h2>
   <div id="py_before"><span class="badge red">等待运行</span> 修复前</div>
   <div style="height:8px"></div>
   <div id="py_after"><span class="badge red">等待运行</span> 修复后</div></div>
  <h2 style="margin-top:16px">📄 Agent 产生的 diff</h2><div id="diff">（运行后显示）</div>
 </div>
 <div class="panel"><h2>🛠️ 实时执行流</h2><div class="feed" id="feed"><div class="ev stage"><span class="t">--:--:--</span>等待启动…</div></div>
  <h2 style="margin-top:16px">💬 模型输出（流式）</h2><div id="tokens">（运行后显示）</div></div>
</main>
<footer>验证独立性：成功判据 = Agent 进程之外的 pytest 退出码，而非模型自评 · 证据落盘 artifacts/agent-execution/</footer>
<script>
const ICON={view_file:'👁',create_file:'📝',edit_file:'✏️',run_command:'⚙️',list_directory:'📁',find_file:'🔍'};
let es=null,running=false;
async function refresh(){try{const s=await (await fetch('/api/status')).json();
 const c=(ok,label,val)=>`<div class="chip ${ok?'ok':'bad'}">${label}：<b>${val}</b></div>`;
 document.getElementById('chips').innerHTML=
  c(s.server,'Ollama',s.server||'离线')+
  c(s.model_listed,'模型',s.model_listed?(s.model||'gemma4:26b'):'缺失')+
  c(s.model_resident,'GPU 驻留',s.model_resident?'是':'否(冷加载~70s)')+
  c(s.backend!=='unknown','后端',s.backend)+
  c(s.workspace_at_baseline,'Bug 基线',s.workspace_at_baseline?'就绪':'漂移(将自动重置)');
 if(!running){document.getElementById('runbtn').disabled=!(s.server&&s.model_listed)||s.replay;}}catch(e){}}
function addEv(html,cls){const f=document.getElementById('feed');
 const d=document.createElement('div');d.className='ev '+(cls||'');d.innerHTML=html;
 f.appendChild(d);f.scrollTop=f.scrollHeight;}
function addEvEl(el,cls){const f=document.getElementById('feed');
 const d=document.createElement('div');d.className='ev '+(cls||'');d.appendChild(el);
 f.appendChild(d);f.scrollTop=f.scrollHeight;}
function tsSpan(){const s=document.createElement('span');s.className='t';s.textContent=new Date().toLocaleTimeString();return s;}
function connect(){es=new EventSource('/api/stream');
 es.addEventListener('replay',e=>{const d=JSON.parse(e.data);
  document.getElementById('replaybanner').style.display='block';
  document.getElementById('replayid').textContent=`${d.run_id||'?'} @ ${d.recorded_at||''}`;});
 es.addEventListener('stage',e=>{const s=JSON.parse(e.data);
  const row=document.createElement('span');row.appendChild(tsSpan());
  row.appendChild(document.createTextNode('🔹 '+s.text));addEvEl(row,'stage');});
 es.addEventListener('tool',e=>{const d=JSON.parse(e.data);
  const row=document.createElement('span');row.appendChild(tsSpan());
  row.appendChild(document.createTextNode((ICON[d.name]||'🔧')+' '));
  const b=document.createElement('b');b.textContent=d.name;row.appendChild(b);
  const a=document.createElement('span');a.style.color='var(--dim)';a.textContent=' '+d.args;row.appendChild(a);
  addEvEl(row,'tool');});
 es.addEventListener('policy',e=>{const s=JSON.parse(e.data);
  const row=document.createElement('span');row.appendChild(tsSpan());
  row.appendChild(document.createTextNode('🛡️ 策略：'));
  const b=document.createElement('b');b.style.color=s.includes('ALLOW')?'var(--ok)':'var(--bad)';
  b.textContent=s.includes('ALLOW')?'ALLOW':'DENY';row.appendChild(b);
  row.appendChild(document.createTextNode(' '+(s.split('args=')[1]||'')));
  addEvEl(row,'policy');});
 const tk=document.getElementById('tokens');
 ['token'].forEach(k=>es.addEventListener(k,e=>{const s=document.createElement('span');s.textContent=JSON.parse(e.data);tk.appendChild(s);tk.scrollTop=tk.scrollHeight;}));
 es.addEventListener('pytest',e=>{const d=JSON.parse(e.data);
  const el=document.getElementById(d.phase==='before'?'py_before':'py_after');
  el.innerHTML=d.exit===0?'<span class="badge green">✓ PASS</span>':'<span class="badge red">✗ FAIL</span>'+(d.phase==='before'?' 修复前（bug 存在，符合预期）':' 修复后');});
 es.addEventListener('diff',e=>{const d=JSON.parse(e.data);
  document.getElementById('diff').innerHTML=d.patch.split('\\n').map(l=>
   l.startsWith('+')?`<span class="b">${l.replace(/</g,'&lt;')}</span>`:
   l.startsWith('-')?`<span class="a">${l.replace(/</g,'&lt;')}</span>`:
   l.replace(/</g,'&lt;')).join('\\n');});
 es.addEventListener('done',e=>{const d=JSON.parse(e.data);running=false;
  if(document.getElementById('replaybanner').style.display!=='block')
   document.getElementById('runbtn').disabled=false;
  addEv(`<span class="t">${new Date().toLocaleTimeString()}</span>${d.ok?'✅ <b>演示成功</b> — bug 已修复，独立验证全绿':'⚠️ <b>未完成</b>'} 变更文件：${d.files_changed.join(', ')||'(无)'}`,'stage');});
 es.addEventListener('error',e=>{});}
async function doRun(){if(running)return;running=true;
 document.getElementById('feed').innerHTML='';document.getElementById('tokens').textContent='';
 document.getElementById('diff').textContent='（运行中…）';
 document.getElementById('py_before').innerHTML='<span class="badge red">…</span>';
 document.getElementById('py_after').innerHTML='<span class="badge red">…</span>';
 document.getElementById('runbtn').disabled=true;
 const r=await (await fetch('/api/run',{method:'POST'})).json();
 if(r.error){alert(r.error);running=false;document.getElementById('runbtn').disabled=false;}}
refresh();connect();setInterval(refresh,5000);
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument(
        "--record",
        type=Path,
        default=None,
        help="write each real run's event stream to this JSON file",
    )
    ap.add_argument(
        "--replay",
        type=Path,
        default=None,
        help="replay a recorded run (visibly labeled REPLAY) instead of live execution",
    )
    ap.add_argument(
        "--replay-speed", type=float, default=1.0, help="replay pacing multiplier (>1 = faster)"
    )
    args = ap.parse_args()

    if args.record and args.replay:
        print(
            "ERROR: --record and --replay are mutually exclusive "
            "(replay banner events would pollute the recording)"
        )
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

    app = Starlette(
        routes=[
            Route("/", homepage),
            Route("/api/status", api_status),
            Route("/api/run", api_run, methods=["POST"]),
            Route("/api/stream", api_stream),
        ]
    )
    import uvicorn

    print(f"Dashboard: http://127.0.0.1:{args.port}  (Ctrl+C 停止)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
