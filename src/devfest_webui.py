"""DevFest 2026 "The Last Seat" stage dashboard V2 (port 8421, no new deps).

Serves the 1920x1080 stage page at http://127.0.0.1:8421 with two views:

  STAGE VIEW (default) — the story for the audience:
    LEFT  the real (fictional-event) registration app: 39/40 seats taken,
          the 40th registration runs against the ACTUAL scenario code in an
          isolated probe process, and only after the agent's independently
          verified fix + a passing business probe flips to the Google-green
          "YOU'RE IN!" celebration with a clearly-fake Builder Pass.
    RIGHT Google Antigravity SDK — Live Agent Execution: the
          Gemma 4 26B -> Antigravity SDK -> Real Tools -> Application
          pipeline and dynamically rendered tool cards driven ONLY by real
          SDK tool_calls observations (never a fixed five-step script).
  DEVELOPER VIEW — the receipts: SDK lifecycle events, pytest results,
          policy audit, whitelisted diff, SHA256, GPU attribution.

Six-act story (STAGE VIEW act strip): THE PROBLEM -> THE MISSION ->
THE INVESTIGATION -> THE FIX -> THE CHECK -> THE CELEBRATION. The stage is
a monotonic state machine advanced ONLY by real observed events (probe
verdict, SDK lifecycle, tool observation, acceptance verdict); a tool call
being OBSERVED never means it succeeded, and the celebration act requires
acceptance.ok == True AND the final business probe PASS in the same run.

Modes (visually distinct, never ambiguous):
  - LIVE (default): real local Gemma 4 agent run on this machine
  - --replay rec.json: recorded REPLAY banner; after replay finishes the
    page KEEPS its RECORDED RUN / REPLAY identity until a new real LIVE run
    starts (replay never re-badges itself as LIVE, and historical success
    keeps its REPLAY watermark)
  - --dry-run: DESIGN PREVIEW — synthetic, plainly-labeled timeline that
    works without Ollama/GPU; never writes evidence and never counts as
    acceptance (no ok:true is ever emitted in preview mode)

GPU honesty: "ROCm VERIFIED" requires BOTH model residency (full digest via
/api/ps) AND the llama-server runner holding ROCm HIP DLLs. The /api/ps
`size` field is shown (developer view only) with its own label — on this
Ollama build it does NOT equal the model file size nor the runner's real
working set, so it is never presented as "model VRAM usage".

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
    EDIT_TOOL_NAMES,
    restore_devfest_baseline,
    run_full_task,
)
from webui import origin_allowed  # single audited implementation, keep in sync # noqa: E402

OLLAMA = Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"
PORT_DEFAULT = 8421
# curated, committed evidence of physical validation runs (read-only listing)
CURATED_EVIDENCE = ROOT / "evidence" / "devfest-last-seat"

# ------------------------------------------------------------- six-act story
STORY_STAGES = ("problem", "mission", "investigation", "fix", "check", "celebration")
STORY_TEXTS = {
    "problem": {
        "act": "THE PROBLEM",
        "title": "最后一席被错误拒绝",
        "line": "39 位开发者已报名，第 40 位被报名系统错误拒绝——缺陷就在这台机器的真实代码里。",
    },
    "mission": {
        "act": "THE MISSION",
        "title": "Google Antigravity 接受任务",
        "line": "SDK Agent 会话已创建，任务已提交给本地 Gemma 4 26B——无云端、无提示答案。",
    },
    "investigation": {
        "act": "THE INVESTIGATION",
        "title": "Agent 正在调查报名程序",
        "line": "真实 SDK 工具正在读取代码与测试，定位最后一席被拒的根因。",
    },
    "fix": {
        "act": "THE FIX",
        "title": "Agent 正在修复报名逻辑",
        "line": "已观察到 edit_file 调用——补丁是否正确，要等独立验证说话。",
    },
    "check": {
        "act": "THE CHECK",
        "title": "正在确认修复是否有效",
        "line": "独立 pytest 验收中：冻结测试未被篡改、白名单外零变更、磁盘哈希复核。",
    },
    "celebration": {
        "act": "THE CELEBRATION",
        "title": "The Last Seat Is Saved!",
        "line": "独立验收全绿 + 真实报名探针 PASS——最后一席获救！",
    },
    "failed": {
        "act": "THE OUTCOME",
        "title": "任务未通过独立验收",
        "line": "本次运行未达到验收条件，不显示任何成功画面。可重置后重试。",
    },
}


class StoryTracker:
    """Six-act story state, advanced ONLY by real observed events.

    Monotonic within a run: stages only move forward along STORY_STAGES;
    ``failed`` is terminal until a reset/new run. Nothing here invents
    progress — every transition requires an actual event (probe verdict,
    SDK lifecycle event, tool observation, acceptance verdict). Observing a
    tool call means the SDK reported it, never that it succeeded.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stage = "idle"
        self._order = {s: i for i, s in enumerate(STORY_STAGES)}

    @property
    def stage(self) -> str:
        with self._lock:
            return self._stage

    def begin_run(self) -> None:
        """A new LIVE run re-opens the conflict from the defective baseline."""
        with self._lock:
            if self._stage not in ("problem",):  # idle / celebration / failed -> reopen
                self._stage = "problem"

    def reset(self) -> None:
        with self._lock:
            self._stage = "problem"

    def _advance(self, target: str) -> str | None:
        if target in self._order and self._order[target] > self._order.get(self._stage, -1):
            self._stage = target
            return target
        return None

    def observe(self, kind: str, data) -> str | None:
        """Feed one real event; return the new stage if the story advanced."""
        with self._lock:
            if self._stage in ("celebration", "failed"):
                return None  # terminal: only reset()/begin_run() reopen the story
            nxt: str | None = None
            if kind == "seat" and isinstance(data, dict):
                admitted = (
                    (data.get("probe") or {}).get("admitted")
                    if isinstance(data.get("probe"), dict)
                    else None
                )
                if data.get("verified") is True:
                    # celebration requires the acceptance chain to have reached
                    # THE CHECK first — an out-of-order verified seat can never
                    # skip the story ahead (replayed/duplicated/late events)
                    nxt = "celebration" if self._stage == "check" else None
                elif admitted is False:
                    nxt = "problem"
            elif kind == "sdk" and isinstance(data, dict):
                ph = data.get("phase")
                if ph in ("agent_session_started", "mission_submitted"):
                    nxt = "mission"
                elif ph == "independent_verification_started":
                    nxt = "check"
            elif kind == "tool" and isinstance(data, dict):
                # an edit tool observation = "the fix is being attempted";
                # read/list/search/run tools = investigating. Never a success claim.
                nxt = "fix" if data.get("name") in EDIT_TOOL_NAMES else "investigation"
            elif kind == "done" and isinstance(data, dict):
                if data.get("ok") is False:
                    nxt = "failed"
            elif kind == "error":
                nxt = "failed"
            if nxt is None or nxt == self._stage:
                return None
            if nxt == "failed":
                self._stage = "failed"  # terminal verdict, not an ordered act
                return "failed"
            return self._advance(nxt)


_story = StoryTracker()

# ---------------------------------------------------------------- run state
_lock = threading.Lock()
_events: deque = deque(maxlen=4000)
_state = {
    "running": False,
    "replay": False,
    "preview": False,
    "mode": "live",  # live | replay | preview (identity persists after replay ends)
    "run_id": None,
    "started_at": None,
    "acceptance_ok": None,  # last LIVE run's acceptance verdict (None/True/False)
    "last_probe_admitted": None,  # last business probe verdict (None/True/False)
}
_seq = {"n": 0}

# events that belong to one specific LIVE run (stamped with its run_id)
_RUN_SCOPED_KINDS = frozenset({"tool", "seat", "done", "sdk", "story"})

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
        "sdk",
        "story",
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
        rid = None
        if (
            kind in _RUN_SCOPED_KINDS
            and isinstance(data, dict)
            and _state.get("mode") == "live"
            and _state.get("running")
        ):
            rid = _state.get("run_id")
            data = {**data, "run_id": rid} if rid is not None else data
        ev = {"id": _seq["n"], "kind": kind, "data": data, "t": time.strftime("%H:%M:%S")}
        _events.append(ev)
        if _recorder is not None:
            _recorder.append({**ev, "data": copy.deepcopy(data), "ts": ts})


def _emit_story_stage(stage: str) -> None:
    data = {"stage": stage, **STORY_TEXTS[stage]}
    if _state.get("preview"):
        data = {**data, "preview": True}
    emit("story", data)


def _emit_tracked(kind: str, data) -> None:
    """Emit a real event and let the story state machine observe it first."""
    stage = _story.observe(kind, data)
    emit(kind, data)
    if stage:
        _emit_story_stage(stage)


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
                # mode STAYS "replay": a finished replay keeps its RECORDED RUN
                # identity until a new real LIVE run starts. It never re-badges
                # itself as LIVE (V2 fix: replay_done must not masquerade as live).

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


def _model_file_size(model: str) -> int | None:
    """Model blob size from /api/tags (the on-disk artifact)."""
    import urllib.request

    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=4) as r:
        for m in json.loads(r.read().decode()).get("models", []):
            if m.get("name") == model:
                size = m.get("size")
                return size if isinstance(size, int) else None
    return None


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


def _llama_runner_working_set() -> int | None:
    """Real working set (bytes) of the llama-server runner process, from
    tasklist CSV output (locale-safe: values are ASCII, header skipped)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        ).stdout
    except Exception:
        return None
    for ln in out.splitlines():
        parts = [p.strip().strip('"') for p in ln.split('","')]
        if len(parts) >= 5 and parts[0].lower().startswith("llama-server"):
            mem = parts[4].replace(",", "").replace("\xa0", "").strip()
            if mem.endswith(" K"):
                mem = mem[:-2]
            try:
                return int(mem) * 1024  # tasklist reports KB
            except ValueError:
                continue
    return None


def gpu_evidence(model: str = DEFAULT_MODEL) -> dict:
    """Honest, run-attributable GPU evidence. 'ROCm VERIFIED' requires BOTH
    model residency (full digest via /api/ps) AND the llama-server runner
    holding ROCm HIP DLLs. Anything less is reported as-is — never inferred
    from a (possibly stale) server.log.

    Memory fields are reported separately and honestly (V2): on this Ollama
    build /api/ps `size`/`size_vram` does NOT equal the model file size nor
    the runner's real working set, so `runner_size_note` forbids presenting
    it as model VRAM usage; the actual footprint is `runner_working_set`."""
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
        "model_file_size": None,
        "runner_working_set": None,
        "runner_size_note": (
            "api/ps size is Ollama's own accounting — it does not equal the "
            "model file size or the runner working set; never show it as "
            "model VRAM usage"
        ),
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
        try:
            ev["model_file_size"] = _model_file_size(model)
        except Exception:
            pass
        ev["runner_working_set"] = _llama_runner_working_set()
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
        verdict = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as e:
        verdict = {"probe_error": f"{type(e).__name__}: {e}", "admitted": None}
    admitted = verdict.get("admitted")
    with _lock:
        _state["last_probe_admitted"] = admitted if isinstance(admitted, bool) else None
    return verdict


def _emit_seat(phase: str, verdict: dict, name: str | None = None, **extra) -> None:
    """Emit a business-probe seat event. `verified` may ONLY be True when the
    acceptance chain already passed AND this probe admits — the two gates that
    unlock the celebration."""
    probe = dict(verdict)
    if name:
        probe["name"] = name
    data = {"phase": phase, "probe": probe, "source": "probe", **extra}
    _emit_tracked("seat", data)


# ------------------------------------------------------------ live task run
def run_task_thread(run_id: str) -> None:
    def on_event(kind: str, data) -> None:
        _emit_tracked(kind, data)

    def wrapper():
        try:
            gpu = gpu_evidence()
            emit(
                "gpu", {"phase": "pre", **{k: gpu[k] for k in ("backend", "resident", "gpu_name")}}
            )
            summary = run_full_task(DEFAULT_MODEL, _base_url(), 900, run_id, on_event=on_event)
            with _lock:
                _state["acceptance_ok"] = bool(summary.get("ok"))
            gpu = gpu_evidence()
            emit("gpu", {"phase": "post", **gpu})
            if summary.get("ok"):
                emit("sdk", {"phase": "business_probe_started"})
                verdict = probe_register("Stage Check", "last")
                verified = verdict.get("admitted") is True
                _emit_seat("final", verdict, name="Stage Check", verified=verified, source="probe")
                emit(
                    "sdk",
                    {
                        "phase": "business_outcome_verified",
                        "admitted": verdict.get("admitted") is True,
                        "verified": verified,
                    },
                )
        except Exception as e:
            _emit_tracked("error", {"message": f"{type(e).__name__}: {e}"})
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
            mode="live",  # a NEW real run is the only path back to LIVE identity
            run_id=f"stage-{time.strftime('%H%M%S')}",
            started_at=time.strftime("%H:%M:%S"),
            acceptance_ok=None,
        )
        rid = _state["run_id"]
    prev = _story.stage
    _story.begin_run()
    if _story.stage != prev:
        _emit_story_stage(_story.stage)  # reopen the conflict after a prior win/failure
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
        def pv(kind, data, delay=0.8):
            time.sleep(delay)
            # policy events are plain strings (audit lines); the rest are dicts
            payload = data if not isinstance(data, dict) else {"preview": True, **data}
            _emit_tracked(kind, payload)

        try:
            pv("stage", {"name": "restore", "text": "[预览] 模拟时间线 — 不触碰真实工作区"}, 0.3)
            pv("pytest", {"phase": "before", "exit": 1, "tail": "[预览] 1 failed, 8 passed (模拟)"})
            pv("stage", {"name": "agent", "text": "[预览] 模拟 Agent 步骤"})
            pv("sdk", {"phase": "agent_config_ready", "model": DEFAULT_MODEL})
            pv("sdk", {"phase": "agent_session_started"})
            pv("sdk", {"phase": "mission_submitted"})
            for name, args in [
                ("list_directory", "{'Path': 'devfest_workspace'}"),
                ("view_file", "{'AbsolutePath': '.../checkin.py'}"),
                ("run_command", "{'CommandLine': 'python -m pytest tests/ -q'}"),
                ("edit_file", "{'Path': '.../checkin.py'}"),
                ("run_command", "{'CommandLine': 'python -m pytest tests/ -q'}"),
            ]:
                pv("tool", {"name": name, "args": args, "status": "observed"})
                if name == "run_command":
                    pv("policy", "run_command decision=ALLOW args=[预览] 模拟策略决策", 0.4)
            for chunk in ["[DESIGN PREVIEW] ", "此处为设计预览文本，", "非真实模型输出。"]:
                pv("token", chunk, 0.25)
            pv("sdk", {"phase": "agent_response_completed"})
            pv("pytest", {"phase": "after", "exit": 0, "tail": "[预览] 9 passed (模拟)"})
            pv(
                "diff",
                {"files": ["checkin.py"], "patch": "[预览] 模拟 diff — LIVE 模式显示真实 diff"},
            )
            pv("sdk", {"phase": "independent_verification_started"})
            pv("sdk", {"phase": "independent_verification_completed", "ok": None})
            pv("gpu", {"phase": "post", "backend": "[预览] 未验证", "resident": False})
            time.sleep(0.4)
            emit(
                "seat",
                {
                    "preview": True,
                    "phase": "final",
                    "probe": {"admitted": True, "seat": 40},
                    "source": "preview-mock",
                    "note": "模拟成功画面（非真实验证）",
                },
            )
            emit("story", {"stage": "celebration", **STORY_TEXTS["celebration"], "preview": True})
            emit(
                "done",
                {"preview": True, "ok": None, "note": "design preview only — not a live agent run"},
            )
        except Exception as e:
            emit("error", {"message": f"preview aborted: {type(e).__name__}: {e}"})
        finally:
            with _lock:
                _state["running"] = False
                _state["preview"] = False
                # mode stays "preview" — the page keeps its DESIGN PREVIEW identity

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


def _workspace_state() -> str:
    """Scenario state for the status chip: READY / FIXING / FIXED / BLOCKED.

    READY = defective baseline in place (waiting for the mission); FIXING =
    a real run is working on it; FIXED = the workspace differs from the
    baseline AND the real business probe admits (normal fixed outcome);
    BLOCKED = neither (drifted into something unexpected)."""
    with _lock:
        if _state["running"] and _state["mode"] == "live":
            return "FIXING"
        at_base = None
    at_base = _workspace_at_baseline()
    if at_base:
        return "READY"
    with _lock:
        admitted = _state.get("last_probe_admitted")
    if admitted is True:
        return "FIXED"
    return "BLOCKED"


async def api_status(request: Request) -> JSONResponse:
    status = {
        "server": False,
        "model": DEFAULT_MODEL,
        "model_listed": False,
        "model_resident": False,
        "workspace_has_engine": (DEVFEST_WORKSPACE / "checkin.py").is_file(),
        "workspace_at_baseline": _workspace_at_baseline(),
        "workspace_state": _workspace_state(),
        "story": _story.stage,
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
        _state["acceptance_ok"] = None
    restore_devfest_baseline()
    _story.reset()
    emit("stage", {"name": "reset", "text": "演示已重置：工作区恢复到缺陷基线"})
    verdict = probe_register("Reset Check", "last")
    _emit_seat("reset", verdict, name="Reset Check")
    _emit_story_stage(_story.stage)  # reopen THE PROBLEM on stage
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
    with _lock:
        run_active = bool(_state["running"] and _state["mode"] == "live")
        acceptance_ok = _state.get("acceptance_ok")
    # Celebration gate (V2 fix for premature celebration): a probe can only be
    # `verified` when NO run is active AND the last LIVE run's acceptance passed
    # AND the real code admits. Mid-run probes never celebrate — at most they
    # reveal that a fix CANDIDATE exists, pending independent acceptance.
    if run_active:
        verified = False
    else:
        verified = True if (acceptance_ok is True and verdict.get("admitted") is True) else None
    _emit_seat("user", verdict, name=name, verified=verified, run_active=run_active)
    return JSONResponse(
        {
            "name": name,
            "probe": verdict,
            "phase": "user",
            "verified": verified,
            "run_active": run_active,
        }
    )


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
<title>Google DevFest 2026 · The Last Seat — 本地 Agent 现场演示</title>
<style>
:root{--bg:#1E1E1E;--card:#262626;--card2:#2D2D2D;--line:#3C4043;--txt:#F0F0F0;--dim:#9AA0A6;
--blue:#4285F4;--green:#34A853;--red:#EA4335;--yellow:#FBBC04}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{font-family:"Google Sans","Segoe UI",Roboto,system-ui,sans-serif;background:var(--bg);
color:var(--txt);display:flex;flex-direction:column;height:100vh;overflow:hidden}
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
header{position:relative;z-index:1;padding:14px 30px 10px;border-bottom:1px solid var(--line);
background:linear-gradient(180deg,#232323,#1E1E1E)}
.hbar{position:absolute;left:0;right:0;top:0;height:6px;
background:linear-gradient(90deg,var(--blue) 0 25%,var(--red) 25% 50%,var(--yellow) 50% 75%,var(--green) 75% 100%)}
.hrow{display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.brandblock{min-width:0}
.gbrand{font-size:30px;font-weight:800;letter-spacing:.5px;line-height:1.15}
.gbrand .gb{color:var(--blue)}.gbrand .gr{color:var(--red)}.gbrand .gy{color:var(--yellow)}
.gbrand .gg{color:var(--green)}.gbrand .grest{color:var(--txt)}
.demotitle{font-size:26px;font-weight:900;letter-spacing:8px;color:var(--txt);margin-top:2px}
.tagline{color:var(--dim);font-size:14px;font-weight:400;margin-top:3px}
.tagline b{color:#E8EAED;font-weight:700}
.hright{margin-left:auto;display:flex;flex-direction:column;gap:8px;align-items:flex-end}
.viewtoggle{display:flex;border:1px solid var(--line);border-radius:999px;overflow:hidden}
.viewtoggle button{background:var(--card);border:0;color:var(--dim);padding:7px 18px;font-size:13.5px;
font-weight:800;cursor:pointer;letter-spacing:.5px}
.viewtoggle button.on{background:var(--blue);color:#fff}
.viewtoggle button:focus-visible{outline:3px solid var(--yellow)}
.modechip{display:flex;gap:10px;align-items:center}
.live{display:inline-flex;align-items:center;gap:8px;background:#3B0E0A;border:1px solid var(--red);
color:#FF8A80;border-radius:999px;padding:5px 14px;font-weight:800;font-size:14px}
.live .dot{width:9px;height:9px;border-radius:50%;background:var(--red);animation:blink 1s infinite}
@keyframes blink{50%{opacity:.25}}
.rchip{display:none;align-items:center;gap:8px;background:#3A2A10;border:1px solid var(--yellow);
color:#FFE082;border-radius:999px;padding:5px 14px;font-weight:800;font-size:14px}
.pchip{display:none;align-items:center;gap:8px;background:#0D243E;border:2px dashed var(--blue);
color:#8AB4F8;border-radius:999px;padding:5px 14px;font-weight:800;font-size:14px}
.banner{display:none;margin-top:10px;border-radius:10px;padding:8px 14px;font-size:14px;font-weight:700}
.banner.replay{display:block;background:#3A2A10;border:1px solid var(--yellow);color:#FFE082}
.banner.preview{display:block;background:#0D243E;border:2px dashed var(--blue);color:#8AB4F8}
.chips{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 13px;
font-size:13px;color:var(--dim)}
.chip b{color:var(--txt);font-weight:700}
.chip.ok b{color:var(--green)}.chip.bad b{color:#FF8A80}.chip.warn b{color:#FFE082}
main{position:relative;z-index:1;flex:1;min-height:0;display:grid;grid-template-columns:46fr 54fr;gap:18px;
padding:16px 30px;max-width:1920px;margin:0 auto;width:100%}
.panel{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px 20px;
position:relative;overflow:hidden;display:flex;flex-direction:column;min-height:0}
.panel h2{margin:0 0 10px;font-size:14px;color:var(--dim);font-weight:700;letter-spacing:2px;text-transform:uppercase}
/* six-act strip */
.actstrip{display:flex;gap:4px;margin-bottom:10px}
.act{flex:1;text-align:center;padding:6px 2px 5px;border-radius:9px;border:1px solid var(--line);
color:var(--dim);font-size:10.5px;font-weight:800;letter-spacing:.4px;background:var(--card2)}
.act .k{display:block;font-size:8.5px;font-weight:700;opacity:.75;letter-spacing:.5px}
.act.done{color:#81C995;border-color:#2E5C3E}
.act.cur{color:#fff;background:#17304F;border-color:var(--blue)}
.act.cur.a0{background:#3B0E0A;border-color:var(--red);color:#FF8A80}
.act.cur.a4{background:#0D243E;border-color:var(--blue);color:#8AB4F8}
.act.cur.a5{background:#10331F;border-color:var(--green);color:#B9F6CA}
.act.fail{color:#FF8A80;border-color:var(--red);background:#3B0E0A}
.storybox{border:1px solid var(--line);border-radius:14px;padding:12px 16px;background:var(--card2);
margin-bottom:10px;min-height:86px}
.storybox .act-name{font-size:11.5px;letter-spacing:3px;color:var(--dim);font-weight:800}
.storybox .act-title{font-size:23px;font-weight:900;margin:2px 0 3px;color:var(--txt)}
.storybox .act-line{font-size:13.5px;color:var(--dim);line-height:1.45}
.storybox.cur-problem{border-color:var(--red)}.storybox.cur-problem .act-title{color:#FF8A80}
.storybox.cur-celebration{border-color:var(--green)}.storybox.cur-celebration .act-title{color:var(--green)}
.storybox.cur-failed{border-color:var(--red)}.storybox.cur-failed .act-title{color:#FF8A80}
/* seats */
.seatrow{display:flex;align-items:center;gap:16px}
.seatnum{display:flex;align-items:baseline;gap:8px;flex:none}
.seatnum .n{font-size:56px;font-weight:800;line-height:1}
.seatnum .d{font-size:20px;color:var(--dim);font-weight:700}
.seatnum.ok .n{color:var(--green)}
.seatnum.bad .n{color:#FF8A80}
.seatgrid{display:grid;grid-template-columns:repeat(10,38px);gap:5px;justify-content:center;flex:1}
.seat{aspect-ratio:1;border-radius:8px;display:flex;align-items:center;justify-content:center;
font-size:12px;font-weight:700;color:#E8EAED;background:var(--card2);border:1px solid var(--line)}
.seat.taken{background:#2E3A54;border-color:#3E5170}
.seat.last{background:transparent;border:2px dashed #5F6368;color:var(--dim);position:relative}
.seat.last.rejected{border-color:var(--red);color:#FF8A80;animation:pulseRed 1.4s infinite}
.seat.last.admitted{border-color:var(--green);color:#B9F6CA;background:#1E3620;animation:none}
@keyframes pulseRed{50%{box-shadow:0 0 0 6px rgba(234,67,53,.15)}}
.regrow{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center}
#nameinput{flex:1;min-width:180px;background:#1E1E1E;border:1px solid var(--line);color:var(--txt);
border-radius:999px;padding:10px 18px;font-size:15px}
#nameinput:focus-visible{outline:3px solid var(--blue)}
.bigbtn{border:0;border-radius:999px;padding:10px 26px;font-size:16px;font-weight:800;cursor:pointer;
background:linear-gradient(90deg,var(--blue),#7BAAF7);color:#fff}
.bigbtn:hover{filter:brightness(1.1)}
.bigbtn:disabled{opacity:.4;cursor:not-allowed}
.bigbtn.reset,.smbtn{background:transparent;border:1.5px solid var(--dim);color:var(--dim);font-weight:600}
.smbtn{border-radius:999px;padding:8px 16px;font-size:13.5px;cursor:pointer}
.smbtn:hover,.bigbtn.reset:hover{border-color:var(--blue);color:var(--txt)}
.verdict{margin-top:10px;border-radius:12px;padding:12px 16px;font-size:15px;line-height:1.5;min-height:64px}
.verdict.rej{background:#3B0E0A;border:1px solid var(--red);color:#FF8A80}
.verdict.ok{background:#10331F;border:1px solid var(--green);color:#B9F6CA}
.verdict.cand{background:#3A2A10;border:1px solid var(--yellow);color:#FFE082}
.verdict .why{display:block;color:var(--dim);font-size:13px;margin-top:4px}
.verdict .why code{font-family:Consolas,monospace;color:inherit}
/* celebration */
#win{display:none;margin-top:12px;text-align:center;border:2px solid var(--green);border-radius:16px;
padding:14px;background:linear-gradient(180deg,#12291B,#0F2416);position:relative;overflow:hidden}
#win h3{margin:0;font-size:40px;font-weight:900;color:var(--green);letter-spacing:1px}
#win .sm{color:#B9F6CA;font-size:14px;margin-top:3px}
.badge-card{margin:12px auto 0;max-width:400px;border-radius:14px;padding:12px 16px;text-align:left;
background:#1E1E1E;border:1px solid var(--line)}
.badge-card .ttl{font-weight:800;font-size:16px}
.badge-card .row{display:flex;justify-content:space-between;font-size:13px;color:#E8EAED;margin-top:5px}
.badge-card .row b{color:var(--txt)}
.badge-card .fake{display:inline-block;margin-top:8px;background:#17140C;border:1px solid var(--yellow);
color:#FFE082;font-size:12px;border-radius:999px;padding:2px 11px;font-weight:700}
.fourbar{height:6px;border-radius:3px;margin-top:9px;
background:linear-gradient(90deg,var(--blue) 0 25%,var(--red) 25% 50%,var(--yellow) 50% 75%,var(--green) 75% 100%)}
.confetti{position:absolute;top:-12px;width:9px;height:14px;opacity:.95;animation:fall linear forwards}
@keyframes fall{to{transform:translateY(560px) rotate(720deg);opacity:.1}}
.pvwmark{position:absolute;inset:0;display:none;align-items:center;justify-content:center;z-index:5;
background:rgba(13,36,62,.55);font-size:20px;font-weight:900;color:#8AB4F8;letter-spacing:2px;transform:rotate(-14deg)}
.rpwmark{position:absolute;inset:0;display:none;align-items:center;justify-content:center;z-index:5;
background:rgba(58,42,16,.55);font-size:20px;font-weight:900;color:#FFE082;letter-spacing:2px;transform:rotate(-14deg)}
/* RIGHT: Antigravity SDK panel */
.sdkhead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.sdkhead h2{margin:0;color:var(--txt);font-size:16px;letter-spacing:.5px;text-transform:none}
.sdkhead .sub{color:var(--dim);font-size:12.5px}
.pipeline{display:flex;align-items:center;gap:6px;margin:10px 0 4px;flex-wrap:nowrap}
.pnode{flex:1;text-align:center;border:1px solid var(--line);border-radius:12px;padding:8px 4px;
background:var(--card2);color:var(--dim);font-size:13px;font-weight:800;transition:all .25s}
.pnode small{display:block;font-size:10px;font-weight:600;opacity:.8;margin-top:1px}
.pnode.on{border-color:var(--blue);color:#8AB4F8;background:#0D243E}
.pnode.on.g{border-color:var(--green);color:#B9F6CA;background:#10331F}
.parrow{color:#5F6368;font-size:16px;flex:none}
.harness{color:var(--dim);font-size:12px;margin:4px 0 8px}
.harness b{color:#E8EAED}
.btnrow{display:flex;gap:8px;margin:4px 0 10px;flex-wrap:wrap}
#toolcards{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:8px;padding-right:4px}
.tcard{border:1px solid var(--line);border-left:4px solid var(--blue);border-radius:12px;
background:var(--card2);padding:9px 14px;animation:pop .18s}
@keyframes pop{from{transform:translateY(4px);opacity:0}}
.tcard .zh{font-size:17px;font-weight:800;color:var(--txt)}
.tcard .meta{display:flex;gap:10px;align-items:center;margin-top:3px;font-size:12px;color:var(--dim)}
.tcard .meta code{font-family:Consolas,monospace;background:#141414;border:1px solid var(--line);
border-radius:6px;padding:1px 7px;color:#8AB4F8}
.tcard .st{background:#0D243E;border:1px solid var(--blue);color:#8AB4F8;border-radius:999px;
padding:1px 9px;font-weight:800;font-size:10.5px}
.tcard.edit{border-left-color:var(--yellow)}
.tcard .pvtag,.pvtag{margin-left:8px;background:#0D243E;border:1px solid var(--blue);color:#8AB4F8;border-radius:6px;
font-size:10.5px;padding:1px 7px;font-weight:800}
/* developer view */
.devsec{border-top:1px solid var(--line);padding-top:8px;margin-top:8px;min-height:0}
.devsec h3{margin:0 0 6px;font-size:12px;color:var(--dim);font-weight:800;letter-spacing:1.5px;
text-transform:uppercase}
body[data-view="stage"] .devonly{display:none!important}
body[data-view="dev"] .stageonly{display:none!important}
body[data-view="dev"] #toolcards{flex:none;max-height:200px}
.feed{display:flex;flex-direction:column;gap:6px;overflow-y:auto;padding-right:4px}
#devfeed{flex:1;min-height:120px}
.ev{border:1px solid var(--line);border-left:3px solid var(--blue);border-radius:9px;
padding:6px 10px;font-size:12.5px;background:var(--card2);animation:pop .18s}
.ev .t{color:var(--dim);margin-right:8px;font-family:Consolas,monospace;font-size:11px}
.ev.tool{border-left-color:var(--blue)}
.ev.policy{border-left-color:var(--yellow);color:#FFE082}
.ev.stage{border-left-color:#A78BFA}
.ev.sdk{border-left-color:var(--green);color:#B9F6CA}
.ev.pv{outline:2px dashed var(--blue)}
.pyrow{display:flex;gap:10px;margin:4px 0;flex-wrap:wrap}
.pybox{flex:1;min-width:180px;background:#1E1E1E;border:1px solid var(--line);border-radius:12px;
padding:8px 12px;font-size:13px}
.pybox .lab{color:var(--dim);font-size:11.5px;display:block;margin-bottom:3px}
.rbadge{display:inline-block;padding:2px 11px;border-radius:999px;font-weight:800;font-size:12px}
.rbadge.red{background:#3B0E0A;color:#FF8A80}.rbadge.green{background:#10331F;color:#B9F6CA}
.rbadge.gray{background:#2D2D2D;color:var(--dim)}
#diff{font-family:Consolas,monospace;font-size:12px;white-space:pre-wrap;background:#141414;
border:1px solid var(--line);border-radius:10px;padding:10px;max-height:180px;overflow:auto;display:none}
#diff .a{color:#FF8A80}#diff .b{color:#B9F6CA}#diff .h{color:var(--dim)}
#tokens{font-family:Consolas,monospace;font-size:12px;color:#C9D1D9;white-space:pre-wrap;
background:#141414;border:1px solid var(--line);border-radius:10px;padding:10px;height:110px;overflow-y:auto}
#gpudetail{font-size:12.5px;color:var(--dim);line-height:1.6}
#gpudetail b{color:#E8EAED}
#devsum{font-size:12.5px;color:var(--dim);line-height:1.6;min-height:20px}
#devsum b{color:#E8EAED}
#devsum code{font-family:Consolas,monospace;color:#8AB4F8}
footer{position:relative;z-index:1;border-top:1px solid var(--line);padding:8px 30px;display:flex;
gap:20px;flex-wrap:wrap;color:var(--dim);font-size:12.5px;background:#1B1B1B}
footer b{color:#E8EAED}
.gpuchip{font-weight:800}
.gpuchip.verified b{color:var(--green)}
/* behind the magic */
details.magic{border:1px solid var(--line);border-radius:12px;background:var(--card2);margin-top:8px}
details.magic summary{cursor:pointer;padding:8px 14px;font-size:13px;font-weight:800;color:#8AB4F8;
list-style:none}
details.magic summary:focus-visible{outline:3px solid var(--yellow)}
details.magic pre{margin:0 10px 10px;background:#141414;border:1px solid var(--line);border-radius:10px;
padding:10px;font-size:11.5px;overflow-x:auto;color:#C9D1D9;line-height:1.5}
details.magic .note{color:var(--dim);font-size:11.5px;margin:0 12px 8px}
/* evidence modal */
#evmodal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:50;align-items:center;justify-content:center}
#evmodal .box{background:var(--card);border:1px solid var(--line);border-radius:16px;max-width:760px;
width:92%;max-height:80vh;overflow-y:auto;padding:22px}
#evmodal h3{margin:0 0 6px;font-size:19px}
#evmodal pre{background:#141414;border:1px solid var(--line);border-radius:10px;padding:10px;
font-size:12px;overflow-x:auto;white-space:pre-wrap}
@media (max-width:1100px){main{grid-template-columns:1fr}.seatnum .n{font-size:46px}
body{overflow:auto}}
@media (prefers-reduced-motion: reduce){
*{animation:none!important;transition:none!important}
.live .dot{animation:none}}
</style></head><body data-view="stage">
<div class="geo" aria-hidden="true"><i class="c1"></i><i class="c2"></i><i class="c3"></i>
<b class="plus" style="left:12%;top:110px"></b><b class="plus" style="right:30%;bottom:80px"></b></div>
<header><div class="hbar"></div>
 <div class="hrow">
  <div class="brandblock">
   <div class="gbrand"><span class="gb">G</span><span class="gr">o</span><span class="gy">o</span><span class="gb">g</span><span class="gg">l</span><span class="gr">e</span><span class="grest">&nbsp;DevFest 2026</span></div>
   <div class="demotitle">THE LAST SEAT</div>
   <div class="tagline"><b>Can a Local AI Agent Save the Final Seat?</b> — 39 Registered · 1 Seat Left · Registration Denied</div>
  </div>
  <div class="hright">
   <div class="viewtoggle" role="tablist" aria-label="视图切换">
    <button id="vstage" class="on" onclick="setView('stage')">STAGE VIEW</button>
    <button id="vdev" onclick="setView('dev')">DEVELOPER VIEW</button>
   </div>
   <div class="modechip">
    <span class="live" id="livechip"><span class="dot"></span>LIVE</span>
    <span class="rchip" id="replaychip">⏵ RECORDED RUN / REPLAY</span>
    <span class="pchip" id="previewchip">◈ DESIGN PREVIEW</span>
   </div>
  </div>
 </div>
 <div class="banner replay" id="replaybanner">🔁 <b>REPLAY — 录制回放</b>：以下为已录制的历史运行，不代表当前硬件或实时测试验证。</div>
 <div class="banner preview" id="previewbanner">◈ <b>DESIGN PREVIEW — 设计预览，非真实 Agent 运行</b>：事件为合成时间线，不触碰工作区、不产生证据、不计入验收。</div>
 <div class="chips" id="chips"><div class="chip">加载中…</div></div>
</header>
<main>
 <section class="panel" id="leftpanel">
  <h2>Workshop 报名 · 实时应用（真实代码）</h2>
  <div class="actstrip stageonly" id="actstrip" aria-hidden="true"></div>
  <div class="storybox stageonly" id="storybox" aria-live="polite">
   <div class="act-name" id="story_act">THE LAST SEAT</div>
   <div class="act-title" id="story_title">最后一席，等待出发</div>
   <div class="act-line" id="story_line">输入开发者姓名，点击「报名第 40 席」——UI 状态由真实场景代码（隔离进程执行 checkin.py）派生。</div>
  </div>
  <div class="seatrow">
   <div class="seatnum bad" id="seatnum"><span class="n" id="seattaken">39</span><span class="d">/ 40 seats</span></div>
   <div class="seatgrid" id="seatgrid" aria-label="座位图"></div>
  </div>
  <div class="regrow">
   <input id="nameinput" maxlength="24" placeholder="第 40 位开发者姓名（可选，仅本地显示）" autocomplete="off">
   <button class="bigbtn" id="regbtn" onclick="doRegister()">报名第 40 席</button>
   <button class="bigbtn reset" id="resetbtn" onclick="doReset()">↺ 重置</button>
  </div>
  <div class="verdict rej" id="verdict">等待报名…<span class="why">探针即将运行真实场景代码。</span></div>
  <div id="win">
   <h3>YOU'RE IN!</h3>
   <div class="sm" id="winline">40 / 40 — The Last Seat Is Saved.（独立验收 + 真实报名探针双 PASS）</div>
   <div class="badge-card">
    <div class="ttl">🦾 Builder Pass</div>
    <div class="row"><span>持有人</span><b id="passname">Guest Developer</b></div>
    <div class="row"><span>席位</span><b>No. 40 / 40</b></div>
    <div class="row"><span>签发</span><b>Google DevFest 2026 · The Last Seat</b></div>
    <div class="fourbar"></div>
    <span class="fake">虚构纪念徽章 · 非入场凭证 · Fictional commemorative badge — not an admission ticket</span>
   </div>
   <div class="pvwmark" id="pvwmark">PREVIEW · 模拟画面 · 非真实结果</div>
   <div class="rpwmark" id="rpwmark">REPLAY · 历史回放 · 非本次运行</div>
  </div>
  <div class="devonly" style="flex:1;min-height:0;display:flex;flex-direction:column">
   <div class="devsec"><h3>独立 pytest（子进程，冻结测试）</h3>
    <div class="pyrow">
     <div class="pybox"><span class="lab">修复前（预期红）</span><span id="py_before"><span class="rbadge gray">未运行</span></span> <span id="pyb_tail" style="color:var(--dim);font-size:11.5px"></span></div>
     <div class="pybox"><span class="lab">修复后（预期全绿）</span><span id="py_after"><span class="rbadge gray">未运行</span></span> <span id="pya_tail" style="color:var(--dim);font-size:11.5px"></span></div>
    </div>
   </div>
   <div class="devsec"><h3>运行摘要</h3><div id="devsum">（运行后显示：验收、文件、SHA256、耗时）</div></div>
   <div class="devsec" style="flex:1;min-height:0"><h3>GPU 证据（诚实口径）</h3><div id="gpudetail">（运行后显示）</div></div>
  </div>
 </section>
 <section class="panel">
  <div class="sdkhead"><h2>Google Antigravity SDK — Live Agent Execution</h2>
   <span class="sub">Gemma 4 26B → Antigravity SDK → Real Tools → Application</span></div>
  <div class="pipeline" id="pipeline">
   <div class="pnode" id="pn_model">Gemma 4 26B<small>本地推理 · 决策</small></div><span class="parrow">→</span>
   <div class="pnode" id="pn_sdk">Antigravity SDK<small>Agent Harness · 权限</small></div><span class="parrow">→</span>
   <div class="pnode" id="pn_tools">Real Tools<small>view / edit / run_command</small></div><span class="parrow">→</span>
   <div class="pnode" id="pn_app">Application<small>40 席位报名系统</small></div>
  </div>
  <div class="harness">AMD ROCm · Local GPU Inference &nbsp;—&nbsp; <b>Model decides.</b> <b>Harness executes.</b> <b>Tools return evidence.</b> <b>Agent continues.</b></div>
  <div class="btnrow">
   <button class="bigbtn" id="runbtn" onclick="doRun()">▶ Give Mission to Antigravity</button>
   <button class="smbtn" onclick="showEvidence()">🗂 验证证据</button>
  </div>
  <div id="toolcards" class="stageonly" aria-live="polite"></div>
  <div class="devonly" style="flex:1;min-height:0;display:flex;flex-direction:column">
   <div class="devsec"><h3>SDK 生命周期 + 工具事件（真实流）</h3>
    <div class="feed" id="devfeed" aria-live="polite"><div class="ev stage"><span class="t">--:--:--</span>等待启动…</div></div>
   </div>
   <div class="devsec"><h3>Agent Diff（仅白名单源文件）</h3><div id="diff"></div></div>
   <div class="devsec"><h3>模型输出（流式）</h3><div id="tokens">（运行后显示）</div></div>
  </div>
  <details class="magic stageonly">
   <summary>✨ Behind the Magic — 真实 SDK 核心调用（与仓库一致）</summary>
   <pre># 节选自 src/devfest_agent.py + src/config.py（完整失败即拒策略链见 src/config.py）
from google.antigravity import Agent, LocalOpenAIAgentConfig
from google.antigravity.hooks import policy

config = LocalOpenAIAgentConfig(
    model="gemma4:26b",                    # 本地 Ollama（OpenAI 兼容端点）
    base_url="http://127.0.0.1:11434/v1",
    workspaces=[str(DEVFEST_WORKSPACE)],   # 唯一可写工作区
    policies=[                             # 失败即拒的策略链
        policy.allow(t, when=in_workspace_path) for t in FILE_TOOLS
    ] + [policy.ask_user("run_command", handler=pytest_only)],
    system_instructions=SYSTEM_INSTRUCTIONS,
).lightweight()

async with Agent(config) as agent:         # 真实 SDK 会话
    response = await agent.chat(MISSION)   # Gemma 4 决定调用哪些工具
    async for token in response: ...       # 流式模型输出
    async for call in response.tool_calls: # 真实工具调用观察
        ...</pre>
   <div class="note">你也可以：pip install google-antigravity，把本地模型变成自己的 Agent。上为仓库真实调用的节选（未运行展示的完整源码见 src/config.py · src/devfest_agent.py）。</div>
  </details>
 </section>
</main>
<footer>
 <span>模型 <b id="f_model">gemma4:26b</b></span>
 <span class="gpuchip" id="f_gpu">GPU 后端 <b>未验证</b></span>
 <span>验证 <b>独立 pytest + SHA256 + 白名单 diff + 业务探针</b></span>
 <span>地址 <b id="f_port">http://127.0.0.1:8421</b></span>
 <span id="f_mode">模式 <b>LIVE</b></span>
 <span>策略边界 <b>非 OS 沙箱</b></span>
</footer>
<div id="evmodal"><div class="box">
 <h3>🗂 物理 GPU 验证证据（只读）</h3>
 <div id="evbody" style="font-size:13.5px;color:var(--dim)">加载中…</div>
 <div class="btnrow"><button class="smbtn" onclick="document.getElementById('evmodal').style.display='none'">关闭</button></div>
</div></div>
<script>
const ROSTER=39; const GCOLORS=['#4285F4','#EA4335','#FBBC04','#34A853'];
const ICON={view_file:'👁',create_file:'📝',edit_file:'✏️',run_command:'⚙️',list_directory:'📁',find_file:'🔍',search_directory:'🔎',replace_file_content:'✏️'};
const TOOL_STORY={list_directory:['查看项目结构','Agent 正在浏览工作区文件'],find_file:['查找文件','Agent 正在定位关键文件'],
search_directory:['搜索代码','Agent 正在检索代码内容'],view_file:['阅读报名代码','Agent 正在阅读源码与冻结测试'],
run_command:['运行 Python 测试','Agent 正在执行 pytest 验证'],edit_file:['修改报名逻辑','Agent 正在对源码应用修复'],
create_file:['创建新文件','Agent 正在写入新文件'],replace_file_content:['修改报名逻辑','Agent 正在对源码应用修复']};
const ACTS=[['problem','PROBLEM','问题'],['mission','MISSION','任务'],['investigation','INVESTIGATION','调查'],
['fix','FIX','修复'],['check','CHECK','验证'],['celebration','CELEBRATION','庆祝']];
const ACT_IDX={problem:0,mission:1,investigation:2,fix:3,check:4,celebration:5,failed:4};
let es=null,running=false,mode='live',won=false,curRun=null;
function el(id){return document.getElementById(id);}
function txt(id,s){el(id).textContent=s;}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function setView(v){document.body.dataset.view=v;
 el('vstage').className=(v==='stage')?'on':'';el('vdev').className=(v==='dev')?'on':'';
 try{localStorage.setItem('lastseat_view',v);}catch(_){}}
(function(){try{const v=localStorage.getItem('lastseat_view');if(v==='dev')setView('dev');}catch(_){}})();
function buildSeats(){const g=el('seatgrid');g.textContent='';
 for(let i=1;i<=40;i++){const d=document.createElement('div');d.className='seat'+(i<40?' taken':' last');d.textContent=i<40?i:'';d.id='seat'+i;g.appendChild(d);}}
function buildActs(){const s=el('actstrip');s.textContent='';
 ACTS.forEach((a,i)=>{const d=document.createElement('div');d.className='act';d.id='act_'+a[0];
  const k=document.createElement('span');k.className='k';k.textContent='ACT '+(i+1);
  d.appendChild(k);d.appendChild(document.createTextNode(a[1]));
  d.title=a[2];s.appendChild(d);});}
function setStory(stage,preview){
 const i=ACT_IDX[stage];
 ACTS.forEach(a=>{const d=el('act_'+a[0]);if(!d)return;d.className='act';});
 if(stage==='failed'){const d=el('act_check');if(d)d.className='act fail';
  const d2=el('act_celebration');if(d2)d2.className='act fail';}
 else if(i!==undefined){for(let k=0;k<6;k++){const d=el('act_'+ACTS[k][0]);if(!d)continue;
  if(k<i)d.className='act done';else if(k===i)d.className='act cur a'+k;}}
 const sb=el('storybox');sb.className='storybox stageonly cur-'+stage;
 if(stage==='problem'){txt('story_act','THE PROBLEM');txt('story_title','最后一席被错误拒绝');
  txt('story_line','39 位开发者已报名，第 40 位被报名系统错误拒绝——缺陷就在这台机器的真实代码里。');}
 else if(stage==='mission'){txt('story_act','THE MISSION');txt('story_title','Google Antigravity 接受任务');
  txt('story_line','SDK Agent 会话已创建，任务已提交给本地 Gemma 4 26B——无云端、无提示答案。');}
 else if(stage==='investigation'){txt('story_act','THE INVESTIGATION');txt('story_title','Agent 正在调查报名程序');
  txt('story_line','真实 SDK 工具正在读取代码与测试，定位最后一席被拒的根因。');}
 else if(stage==='fix'){txt('story_act','THE FIX');txt('story_title','Agent 正在修复报名逻辑');
  txt('story_line','已观察到 edit_file 调用——补丁是否正确，要等独立验证说话。');}
 else if(stage==='check'){txt('story_act','THE CHECK');txt('story_title','正在确认修复是否有效');
  txt('story_line','独立 pytest 验收中：冻结测试未被篡改、白名单外零变更、磁盘哈希复核。');}
 else if(stage==='celebration'){txt('story_act','THE CELEBRATION');txt('story_title','The Last Seat Is Saved!');
  txt('story_line','独立验收全绿 + 真实报名探针 PASS——最后一席获救！');}
 else if(stage==='failed'){txt('story_act','THE OUTCOME');txt('story_title','任务未通过独立验收');
  txt('story_line','本次运行未达到验收条件，不显示任何成功画面。可重置后重试。');}
 setPipeline(stage);}
function setPipeline(stage){
 const on=(id,g)=>{const d=el(id);if(d)d.className='pnode'+(g?' on g':' on');};
 const off=(id)=>{const d=el(id);if(d)d.className='pnode';};
 ['pn_model','pn_sdk','pn_tools','pn_app'].forEach(off);
 if(stage==='mission'){on('pn_model');on('pn_sdk');}
 else if(stage==='investigation'){on('pn_model');on('pn_sdk');on('pn_tools');}
 else if(stage==='fix'){on('pn_model');on('pn_sdk');on('pn_tools');}
 else if(stage==='check'){on('pn_model');on('pn_sdk');}
 else if(stage==='celebration'){on('pn_model',1);on('pn_sdk',1);on('pn_tools',1);on('pn_app',1);}}
function setMode(m){mode=m;
 el('livechip').style.display=(m==='live')?'inline-flex':'none';
 el('replaychip').style.display=(m==='replay')?'inline-flex':'none';
 el('previewchip').style.display=(m==='preview')?'inline-flex':'none';
 el('replaybanner').style.display=(m==='replay')?'block':'none';
 el('previewbanner').style.display=(m==='preview')?'block':'none';
 if(won){el('rpwmark').style.display=(m==='replay')?'flex':'none';
  el('pvwmark').style.display=(m==='preview')?'flex':'none';}
 txt('f_mode','模式 '+(m==='live'?'LIVE':m==='replay'?'RECORDED RUN / REPLAY':'DESIGN PREVIEW'));}
function addToolCard(d){
 const box=el('toolcards');const name=d.name||'?';
 const c=document.createElement('div');c.className='tcard'+((name==='edit_file'||name==='create_file'||name==='replace_file_content')?' edit':'');
 const zh=document.createElement('div');zh.className='zh';
 zh.textContent=((TOOL_STORY[name]||['调用工具','Agent 正在调用 SDK 工具'])[0]);
 c.appendChild(zh);
 const meta=document.createElement('div');meta.className='meta';
 const who=document.createElement('span');who.textContent='Antigravity SDK →';
 meta.appendChild(who);
 const code=document.createElement('code');code.textContent=name;meta.appendChild(code);
 const st=document.createElement('span');st.className='st';
 st.textContent=(d.status||'observed')+(d.seq?' · #'+d.seq:'');meta.appendChild(st);
 if(d.args){const a=document.createElement('span');a.textContent=' '+d.args;meta.appendChild(a);}
 if(d.preview){const p=document.createElement('span');p.className='pvtag';p.textContent='预览';meta.appendChild(p);}
 c.appendChild(meta);box.appendChild(c);
 while(box.children.length>40)box.removeChild(box.firstChild);
 box.scrollTop=box.scrollHeight;}
function addEv(node,cls,pv){const f=el('devfeed');if(!f)return;
 const d=document.createElement('div');d.className='ev '+(cls||'')+(pv?' pv':'');
 d.appendChild(node);if(pv){const t=document.createElement('span');t.className='pvtag';t.textContent='预览';d.appendChild(t);}
 f.appendChild(d);while(f.children.length>150)f.removeChild(f.firstChild);f.scrollTop=f.scrollHeight;}
function row(parts,pv){const r=document.createElement('span');parts.forEach(p=>{if(typeof p==='string'){r.appendChild(document.createTextNode(p));}
 else{const b=document.createElement('b');if(p.cls)b.className=p.cls;b.textContent=p.b;r.appendChild(b);}});addEv(r,undefined,pv);return r;}
function tsSpan(){const s=document.createElement('span');s.className='t';s.textContent=new Date().toLocaleTimeString();return s;}
async function refresh(){try{const s=await (await fetch('/api/status')).json();
 const chips=el('chips');chips.textContent='';
 const c=(cls,label,val)=>{const d=document.createElement('div');d.className='chip '+(cls||'');
  d.appendChild(document.createTextNode(label+'：'));const b=document.createElement('b');b.textContent=val;d.appendChild(b);chips.appendChild(d);};
 c(s.server?'ok':'bad','Ollama',s.server||'离线');
 c(s.model_listed?'ok':'bad','模型',s.model_listed?s.model:'缺失');
 c(s.model_resident?'ok':'','GPU 驻留',s.model_resident?'是':'否');
 const ws=s.workspace_state||'READY';
 const wsTxt={READY:['ok','READY · 等待任务'],FIXING:['warn','FIXING · Agent 工作中'],
  FIXED:['ok','FIXED · 已完成'],BLOCKED:['bad','BLOCKED · 异常']}[ws]||['',''+ws];
 c(wsTxt[0],'场景',wsTxt[1]);
 if(s.gpu_name){const d=document.createElement('div');d.className='chip';d.appendChild(document.createTextNode('GPU：'));
  const b=document.createElement('b');b.textContent=s.gpu_name;d.appendChild(b);chips.appendChild(d);}
 txt('f_model',s.model);txt('f_port','http://127.0.0.1:'+s.port);
 if(s.story&&s.story!=='idle'&&!won&&!running){setStory(s.story);}
 if(!running&&!won){el('runbtn').disabled=(s.mode==='preview')?false:!(s.server&&s.model_listed);
  if(!running)setMode(s.mode);}
}catch(e){}}
function setPy(phase,exit,tail,pv){const bid=phase==='before'?'py_before':'py_after';
 const box=el(bid);box.textContent='';
 const b=document.createElement('span');b.className='rbadge '+(exit===null||exit===undefined?'gray':exit===0?'green':'red');
 b.textContent=pv?'—':(exit===0?'✓ PASS':'✗ FAIL');box.appendChild(b);
 if(pv){const t=document.createElement('span');t.className='pvtag';t.textContent='预览';box.appendChild(t);}
 txt(phase==='before'?'pyb_tail':'pya_tail',tail||'');}
function confetti(){const win=el('win');for(let i=0;i<44;i++){const c=document.createElement('span');c.className='confetti';
 c.style.left=Math.random()*100+'%';c.style.background=GCOLORS[i%4];
 c.style.animationDuration=(2.4+Math.random()*2.2)+'s';c.style.animationDelay=(Math.random()*0.9)+'s';
 win.appendChild(c);setTimeout(()=>c.remove(),5200);}}
function celebrate(name,identity){
 won=true;
 el('seatnum').className='seatnum ok';txt('seattaken','40');
 const s40=el('seat40');if(s40){s40.className='seat last admitted';s40.textContent='40';}
 const v=el('verdict');v.className='verdict ok';
 v.textContent='';const strong=document.createElement('b');strong.textContent='报名成功 — 席位 40/40';
 v.appendChild(strong);const why=document.createElement('span');why.className='why';
 why.textContent=(identity==='preview')?'预览模式：模拟成功画面（未执行真实修复）':
  (identity==='replay')?'REPLAY 历史回放：当时真实探针在隔离进程中确认 register(name, 39) → seat 40':
  '真实场景代码在隔离进程中确认：register(name, 39) → seat 40';
 v.appendChild(why);
 txt('passname',name||'Guest Developer');
 el('win').style.display='block';
 el('rpwmark').style.display=(identity==='replay')?'flex':'none';
 el('pvwmark').style.display=(identity==='preview')?'flex':'none';
 if(identity==='live'&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches){confetti();}
 el('runbtn').disabled=false;
 el('regbtn').disabled=false;}
function clearWin(){won=false;el('win').style.display='none';
 el('pvwmark').style.display='none';el('rpwmark').style.display='none';
 el('seatnum').className='seatnum bad';txt('seattaken','39');}
async function applyProbe(d,serverName){
 const p=d.probe||{};const v=el('verdict');
 const name=(el('nameinput').value||'').trim()||serverName||p.name||'Guest Developer';
 if(d.source==='preview-mock'){celebrate(name,'preview');setStory('celebration');return;}
 if(p.probe_error){v.className='verdict rej';v.textContent='探针错误：'+p.probe_error;return;}
 if(d.verified===true){celebrate(name,mode==='replay'?'replay':'live');return;}
 if(p.admitted){ /* fix candidate present — NEVER celebrate on this alone */
  clearWin();
  const s40=el('seat40');if(s40){s40.className='seat last';s40.textContent='40';}
  v.className='verdict cand';v.textContent='';
  const b=document.createElement('b');
  b.textContent=d.run_active?'修复候选已出现 — 等待独立验收':'当前代码可报名 — 但本页未见证独立验收';
  v.appendChild(b);
  const why=document.createElement('span');why.className='why';
  why.textContent=d.run_active?'Agent 已修改文件（探针通过），但独立验收未完成前不算成功。':'此状态不构成成功：成功仅由「验收 PASS + 探针 PASS」共同触发。';
  v.appendChild(why);return;}
 clearWin();
 const s40=el('seat40');if(s40){s40.className='seat last rejected';s40.textContent='40';}
 v.className='verdict rej';v.textContent='';
 const b=document.createElement('b');b.textContent='✗ 报名被拒 — '+((p.error_text||'workshop is at full capacity (39/40 taken)'));
 v.appendChild(b);
 const why=document.createElement('span');why.className='why';
 const code=document.createElement('code');
 code.textContent='checkin.register('+JSON.stringify(p.name||name||'Guest Developer')+', 39) → '+p.error_type+'';
 why.appendChild(document.createTextNode('由真实场景代码在隔离进程执行：'));why.appendChild(code);v.appendChild(why);}
async function doRegister(){if(running&&mode==='live'){}
 const name=el('nameinput').value||'Guest Developer';
 el('regbtn').disabled=true;
 const r=await (await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})).json();
 el('regbtn').disabled=false;
 if(r.error){alert(r.error);return;}
 applyProbe(r,r.name);}
async function doRun(){if(running)return;running=true;clearWin();
 el('toolcards').textContent='';el('tokens').textContent='（运行中…）';el('diff').style.display='none';
 el('devfeed').textContent='';txt('devsum','（运行中…）');
 setPy('before',null,'');setPy('after',null,'');
 el('runbtn').disabled=true;
 setPipeline('mission');
 const r=await (await fetch('/api/run',{method:'POST'})).json();
 if(r.error){alert(r.error);running=false;el('runbtn').disabled=false;return;}
 curRun=r.run_id;setMode(r.mode||'live');}
async function doReset(){if(running)return;
 const r=await (await fetch('/api/reset',{method:'POST'})).json();
 if(r.error){alert(r.error);return;}
 clearWin();setPy('before',null,'');setPy('after',null,'');el('diff').style.display='none';
 el('toolcards').textContent='';el('devfeed').textContent='';txt('devsum','（已重置）');
 ['pn_model','pn_sdk','pn_tools','pn_app'].forEach(id=>{const d=el(id);if(d)d.className='pnode';});
 applyProbe(r,'Guest Developer');}
async function showEvidence(){el('evmodal').style.display='flex';
 const r=await (await fetch('/api/evidence')).json();const b=el('evbody');b.textContent='';
 if(!r.runs||!r.runs.length){b.textContent='暂无已发布的物理验证证据。LIVE 运行证据落盘于 artifacts/devfest-agent-execution/，精选证据将收录到 '+r.evidence_dir+'。';return;}
 r.runs.forEach(run=>{const h=document.createElement('b');h.textContent='▶ '+run.run;b.appendChild(h);
  if(run.summary){const pre=document.createElement('pre');pre.textContent=JSON.stringify(run.summary,null,1);b.appendChild(pre);}
  const list=document.createElement('div');list.textContent='文件：'+run.files.join(' · ');b.appendChild(list);
  const sp=document.createElement('div');sp.style.height='14px';b.appendChild(sp);});}
function fmtGB(b){return (b?(b/1e9).toFixed(2):'?')+' GB';}
function gpuDetail(d){
 const g=el('gpudetail');g.textContent='';
 const line=(k,val)=>{const dv=document.createElement('div');dv.appendChild(document.createTextNode(k+'：'));
  const b=document.createElement('b');b.textContent=val;dv.appendChild(b);g.appendChild(dv);};
 line('后端',d.backend||'未知');
 if(d.digest)line('模型 digest',String(d.digest).slice(0,19)+'…');
 if(d.model_file_size)line('模型文件大小（/api/tags）',fmtGB(d.model_file_size));
 if(d.runner_working_set)line('runner 进程工作集（tasklist 实测）',fmtGB(d.runner_working_set));
 if(d.size)line('runner-reported size（/api/ps 记账口径，≠模型占用）',fmtGB(d.size));
 const note=document.createElement('div');note.style.color='var(--dim)';note.style.marginTop='4px';
 note.textContent='注：/api/ps 的 size 字段为 Ollama 自身记账，实测不等于模型文件大小或进程工作集，故不作为"模型显存占用"展示。';
 g.appendChild(note);}
function connect(){es=new EventSource('/api/stream');
 es.addEventListener('replay',e=>{const d=JSON.parse(e.data);setMode('replay');won=false;
  txt('replaybanner','🔁 REPLAY — 录制回放：run '+(d.run_id||'?')+' @ '+(d.recorded_at||'')+'（不代表当前硬件或实时验证）');});
 es.addEventListener('replay_done',()=>{ /* stay REPLAY — a finished replay never re-badges as LIVE */
  txt('replaybanner','🔁 REPLAY — 回放结束：以上为历史运行结果（保持 RECORDED RUN 身份，直到新的真实 LIVE 运行开始）。');
  el('runbtn').disabled=true;refresh();});
 es.addEventListener('story',e=>{const d=JSON.parse(e.data);setStory(d.stage,d.preview);});
 es.addEventListener('sdk',e=>{const d=JSON.parse(e.data);
  const LABEL={agent_config_ready:'Agent 配置就绪（策略链锁定）',agent_session_started:'Agent 会话已创建（真实 SDK）',
  mission_submitted:'任务已提交（agent.chat）',agent_response_completed:'Agent 响应完成',
  independent_verification_started:'独立验证开始',independent_verification_completed:'独立验证完成',
  business_probe_started:'业务探针启动',business_outcome_verified:'业务结果验证'};
  const r=document.createElement('span');r.appendChild(tsSpan());
  r.appendChild(document.createTextNode('◈ '));
  const b=document.createElement('b');b.textContent=LABEL[d.phase]||d.phase;r.appendChild(b);
  if(d.ok!==undefined){const s=document.createElement('span');s.textContent=' → '+(d.ok===true?'PASS':d.ok===false?'FAIL':'无结论（预览）');r.appendChild(s);}
  addEv(r,'sdk',d.preview);});
 es.addEventListener('stage',e=>{const s=JSON.parse(e.data);const r=document.createElement('span');
  r.appendChild(tsSpan());r.appendChild(document.createTextNode('🔹 '+s.text));addEv(r,'stage',s.preview);});
 es.addEventListener('tool',e=>{const d=JSON.parse(e.data);
  addToolCard(d);
  const r=document.createElement('span');r.appendChild(tsSpan());
  r.appendChild(document.createTextNode((ICON[d.name]||'🔧')+' '));
  const b=document.createElement('b');b.textContent=d.name;r.appendChild(b);
  const a=document.createElement('span');a.style.color='var(--dim)';a.textContent=' '+(d.args||'');r.appendChild(a);
  addEv(r,'tool',d.preview);});
 es.addEventListener('policy',e=>{const s=JSON.parse(e.data);const r=document.createElement('span');
  r.appendChild(tsSpan());r.appendChild(document.createTextNode('🛡️ 策略：'));
  const b=document.createElement('b');b.style.color=s.includes('ALLOW')?'var(--green)':'#FF8A80';
  b.textContent=s.includes('ALLOW')?'ALLOW':'DENY';r.appendChild(b);
  r.appendChild(document.createTextNode(' '+(s.split('args=')[1]||'')));addEv(r,'policy',String(s).includes('预览'));});
 const tk=el('tokens');
 es.addEventListener('token',e=>{const s=document.createElement('span');s.textContent=JSON.parse(e.data);
  tk.appendChild(s);tk.scrollTop=tk.scrollHeight;});
 es.addEventListener('pytest',e=>{const d=JSON.parse(e.data);setPy(d.phase,d.exit,d.tail,d.preview);});
 es.addEventListener('gpu',e=>{const d=JSON.parse(e.data);const chip=el('f_gpu');
  chip.className='gpuchip'+(d.backend==='ROCm VERIFIED'?' verified':'');
  chip.textContent='GPU 后端 ';const b=document.createElement('b');
  const pre=(mode==='replay')?'[历史] ':'';
  b.textContent=d.preview?'[预览] 未验证':(pre+(d.backend+(d.digest?' · '+String(d.digest).slice(7,19):'')));
  chip.appendChild(b);
  if(d.phase==='post')gpuDetail(d);});
 es.addEventListener('diff',e=>{const d=JSON.parse(e.data);if(!d.patch)return;
  const box=el('diff');box.style.display='block';
  box.innerHTML=d.patch.split('\\n').map(l=>
   l.startsWith('+')?`<span class="b">${esc(l)}</span>`:
   l.startsWith('-')?`<span class="a">${esc(l)}</span>`:
   `<span class="h">${esc(l)}</span>`).join('\\n');});
 es.addEventListener('seat',e=>{const d=JSON.parse(e.data);applyProbe(d,d.probe&&d.probe.name);});
 es.addEventListener('done',e=>{const d=JSON.parse(e.data);running=false;
  const parts=[];
  const sum=el('devsum');sum.textContent='';
  if(d.preview){parts.push({b:'◈ 设计预览结束',cls:''},d.note||'');txt('devsum','设计预览 — 不产生验收摘要（真实运行显示：验收、文件、SHA256、耗时）');}
  else if(d.ok){parts.push({b:'✅ 任务成功',cls:''},' — 独立验证全绿，变更文件：'+((d.files_changed||[]).join(', ')||'(无)'),
   ' · 耗时 '+(d.elapsed_s||'?')+'s · 工具调用 '+(d.n_tool_calls||0)+' 次');
   el('runbtn').disabled=false;}
  else{parts.push({b:'⚠️ 未通过独立验收',cls:''},' '+(d.tests_tampered?'测试被改动 → 拒绝':(d.forbidden_changed&&d.forbidden_changed.length)?'白名单外文件被改：'+d.forbidden_changed.join(','):'未完成修复'));
   clearWin();el('runbtn').disabled=false;}
  row(parts,d.preview);
  if(!d.preview&&sum){
   const b=document.createElement('b');b.textContent=d.ok?'验收 PASS':'验收未通过';sum.appendChild(b);
   sum.appendChild(document.createTextNode(' · 文件 '+(d.files_changed||[]).join(',')+' · 耗时 '+(d.elapsed_s||'?')+'s'));
   if(d.sha256_after_short){const pre=document.createElement('div');pre.style.marginTop='3px';
    pre.appendChild(document.createTextNode('SHA256(after)：'));
    Object.keys(d.sha256_after_short).forEach(k=>{const c=document.createElement('code');
     c.textContent=k+'='+d.sha256_after_short[k]+' ';pre.appendChild(c);});
    sum.appendChild(pre);}}});
 es.addEventListener('error',e=>{if(!e.data)return;
  try{const d=JSON.parse(e.data);const r=document.createElement('span');r.appendChild(tsSpan());
  r.appendChild(document.createTextNode('⚠️ '+(d.message||'run error')));addEv(r,'stage');running=false;clearWin();el('runbtn').disabled=false;}catch(_){}});}
buildSeats();buildActs();setMode('live');refresh();connect();setInterval(refresh,5000);
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
