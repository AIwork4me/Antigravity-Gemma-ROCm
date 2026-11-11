"""Capture the README hero GIF from the REAL "The Last Seat" stage web UI.

This is a browser-screen-capture pipeline (Playwright over the actual
``src/devfest_webui.py`` dashboard), NOT a re-drawn animation:

  1. it starts the real stage server in REPLAY mode with a chosen recording
     (default: ``docs/assets/last-seat-recording.json`` — a real recorded run);
  2. a real Chromium-family browser renders the actual page while the
     recorded event stream drives every pixel (seats, story acts, tool
     cards, pytest badges, celebration);
  3. frames are captured from the browser and the page's own honest
     REPLAY identity (banner + RECORDED RUN chip) is asserted and kept
     visible in every produced frame;
  4. frames are curated into a 12-18 s scene arc and encoded as a looping
     GIF (plus a poster PNG and a metadata sidecar).

Honesty rules baked in (violations raise, they never render):
  - the recording must be a COMPLETE accepted run (pytest red -> green,
    ``done ok:true``, final verified seat). A recording without the final
    acceptance chain is rejected — no success imagery can be produced;
  - the page must visibly identify itself as REPLAY (banner names the
    run id); the capture aborts otherwise, and every GIF frame carries a
    ``RECORDED RUN <id> · <speed>x TIME-COMPRESSED`` badge;
  - no live GPU metrics are read or overlaid — replayed GPU events render
    with the page's own ``[历史]`` prefix, as-is.

The guided "camera" only switches views/tabs that a human could click
(STAGE VIEW <-> DEVELOPER VIEW, tab selection); it never injects state,
events, CSS results, or success visuals.

Usage:
    .venv/Scripts/python.exe scripts/capture_devfest_hero.py \
        [--recording docs/assets/last-seat-recording.json] \
        [--out docs/assets/devfest-last-seat-hero.gif] \
        [--speed 1.5] [--gif-width 960] [--gif-fps 8] [--port 8430]

    --reuse-frames  re-encode from a previous capture's frame cache
                    (workdir), skipping the browser pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDING_DEFAULT = ROOT / "docs" / "assets" / "last-seat-recording.json"
OUT_DEFAULT = ROOT / "docs" / "assets" / "devfest-last-seat-hero.gif"
POSTER_DEFAULT = ROOT / "docs" / "assets" / "devfest-last-seat-poster.png"
META_DEFAULT = ROOT / "docs" / "assets" / "devfest-last-seat-hero.meta.json"
WORKDIR_DEFAULT = ROOT / "artifacts" / "hero-capture"

_KNOWN_KINDS = frozenset(
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
_STORY_ORDER = ["problem", "mission", "investigation", "fix", "check", "celebration"]


# --------------------------------------------------------------------------
# recording validation — the honesty gate
# --------------------------------------------------------------------------
def validate_recording(rec: dict) -> dict:
    """Reject anything that is not a complete, accepted REAL run.

    Returns a summary (run_id, model, tool sequence, pytest tails, elapsed).
    Raises ValueError on: bad container, unknown kinds, out-of-order story
    stages, pytest before/after inversion, or a missing/failed final
    acceptance chain (a failed run must never yield success imagery).
    """
    if (
        not isinstance(rec, dict)
        or rec.get("format") != 1
        or not isinstance(rec.get("events"), list)
    ):
        raise ValueError("unsupported recording: expected format 1 with an events list")
    meta = rec.get("meta")
    if not isinstance(meta, dict) or not meta.get("run_id"):
        raise ValueError("recording meta must carry a run_id")
    events = rec["events"]
    if not events:
        raise ValueError("recording has no events")

    stages_seen: list[str] = []
    pytest_phases: list[str] = []
    tool_names: list[str] = []
    done_ok: bool | None = None
    verified_seat = False
    for ev in events:
        if not isinstance(ev, dict) or ev.get("kind") not in _KNOWN_KINDS:
            raise ValueError(
                f"unknown replay event kind: {ev.get('kind') if isinstance(ev, dict) else type(ev)!r}"
            )
        kind, data = ev["kind"], ev.get("data")
        if kind == "story" and isinstance(data, dict) and data.get("stage") in _STORY_ORDER:
            stages_seen.append(data["stage"])
        elif kind == "pytest" and isinstance(data, dict):
            pytest_phases.append(str(data.get("phase")))
        elif kind == "tool" and isinstance(data, dict) and data.get("name"):
            tool_names.append(str(data["name"]))
        elif kind == "done" and isinstance(data, dict):
            done_ok = data.get("ok") is True and not data.get("preview")
        elif kind == "seat" and isinstance(data, dict) and data.get("verified") is True:
            verified_seat = True

    order_idx = [_STORY_ORDER.index(s) for s in stages_seen]
    if any(b <= a for a, b in zip(order_idx, order_idx[1:], strict=False)):
        raise ValueError("story stages regress or repeat out of order in the recording")
    if "problem" not in stages_seen or "celebration" not in stages_seen:
        raise ValueError("recording does not span problem -> celebration")
    if "before" in pytest_phases and "after" in pytest_phases:
        if pytest_phases.index("after") < pytest_phases.index("before"):
            raise ValueError("pytest 'after' appears before 'before' in the recording")
    elif "before" not in pytest_phases or "after" not in pytest_phases:
        raise ValueError("recording lacks the before/after pytest verification pair")
    if done_ok is not True:
        raise ValueError(
            "recording has no accepted completion (done ok:true) — refusing success imagery"
        )
    if not verified_seat:
        raise ValueError("recording lacks the final verified seat — refusing success imagery")

    done_ev = next(
        e["data"] for e in events if e.get("kind") == "done" and isinstance(e.get("data"), dict)
    )
    pytest_tails = {
        str(e["data"].get("phase")): str(e["data"].get("tail", ""))
        for e in events
        if e.get("kind") == "pytest" and isinstance(e.get("data"), dict)
    }
    return {
        "run_id": str(meta["run_id"]),
        "model": str(meta.get("model", "model")),
        "recorded_at": str(meta.get("recorded_at", "")),
        "n_events": len(events),
        "tool_sequence": tool_names,
        "pytest_tails": pytest_tails,
        "elapsed_s": done_ev.get("elapsed_s"),
        "n_tool_calls": done_ev.get("n_tool_calls"),
    }


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


def _rel(path: Path | str | None) -> str | None:
    """Repo-relative display path (never leak absolute user paths into assets)."""
    if path is None:
        return None
    p = Path(path)
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.name


# --------------------------------------------------------------------------
# scene arc + frame overlay
# --------------------------------------------------------------------------
SCENES = ("problem", "mission", "investigate", "model", "check", "celebrate")
SCENE_LABELS = {
    "problem": "THE PROBLEM · 39/40 — REGISTRATION REJECTED",
    "mission": "THE MISSION — GIVE IT TO THE AGENT",
    "investigate": "REAL SDK TOOL CALLS — READ · EDIT · RUN",
    "model": "GEMMA 4 26B EXPLAINS THE FIX (LOCAL)",
    "check": "INDEPENDENT PYTEST — RED → GREEN",
    "celebrate": "40/40 — YOU'RE IN!",
}
DEFAULT_SCENE_DURATIONS = {
    "problem": 2.6,
    "mission": 1.4,
    "investigate": 3.2,
    "model": 2.6,
    "check": 2.2,
    "celebrate": 2.8,
}


def badge_texts(run_id: str, speed: float, scene: str) -> tuple[str, str]:
    """(left, right) overlay chips. The left chip is the honesty badge — it
    must always identify the footage as a time-compressed recorded replay."""
    if speed and speed > 0 and abs(speed - 1.0) > 1e-9:
        pace = f"{speed:g}x TIME-COMPRESSED"
    else:
        pace = "REAL-TIME PACE"
    return (f"RECORDED RUN {run_id} · {pace}", SCENE_LABELS.get(scene, scene))


def classify_frame(st: dict) -> str | None:
    """Map one captured page-state snapshot to its scene (or None)."""
    story = st.get("story", "")
    tools = int(st.get("tools") or 0)
    tokens = int(st.get("tokens") or 0)
    if st.get("win") or "CELEBRATION" in story:
        return "celebrate"
    if "CHECK" in story:
        return "check"
    if tools >= 1 and (tokens > 40 or st.get("tab") == "tab-out"):
        if st.get("tab") == "tab-out":
            return "model"
        return "model" if tokens > 40 else "investigate"
    if tools >= 1:
        return "investigate"
    if "MISSION" in story:
        return "mission"
    if "PROBLEM" in story:
        return "problem"
    return None


# JavaScript: one round-trip snapshot of the live page state (read-only).
STATE_JS = """() => {
  const g = id => document.getElementById(id);
  const q = s => document.querySelector(s);
  const badge = g('py_after') ? g('py_after').querySelector('.rbadge') : null;
  return {
    story: (g('story_act')||{}).textContent || '',
    view: document.body.dataset.view || '',
    tab: (q('.tabbtn[aria-selected="true"]')||{}).id || '',
    tools: g('toolcards') ? g('toolcards').children.length : 0,
    tokens: g('tokens') ? g('tokens').textContent.length : 0,
    diff: !!g('diff') && g('diff').style.display === 'block',
    pyAfter: badge ? badge.className : '',
    win: !!g('win') && g('win').style.display === 'block',
    taken: (g('seattaken')||{}).textContent || '',
    verdict: (g('verdict')||{}).className || '',
    banner: (g('replaybanner')||{}).textContent || '',
    replayChip: !!g('replaychip') && g('replaychip').style.display !== 'none',
  };
}"""


# --------------------------------------------------------------------------
# browser capture
# --------------------------------------------------------------------------
def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _wait_http(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def capture_frames(
    recording: Path,
    workdir: Path,
    speed: float,
    port: int,
    fps: float = 10.0,
    hold_s: float = 3.0,
    timeout_s: float = 120.0,
    viewport: tuple[int, int] = (1536, 864),
) -> dict:
    """Drive the real stage page through a replay and capture tagged frames.

    Returns a metadata dict (identity assertions included). Frames land in
    ``workdir/frames/NNNN.png`` with ``workdir/states.json`` beside them.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - reference machine only
        raise SystemExit(
            "playwright is required for browser capture: "
            "pip install playwright  (browser channel 'msedge' needs no download)"
        ) from e

    rec = json.loads(Path(recording).read_text(encoding="utf-8"))
    summary = validate_recording(rec)
    run_id = summary["run_id"]

    if not _port_free(port):
        raise SystemExit(f"port {port} is busy — pass --port to pick a free one")
    frames_dir = workdir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    server_log = workdir / "server.log"

    proc = subprocess.Popen(  # noqa: S603 - fixed argv, venv python
        [
            sys.executable,
            "-I",
            str(ROOT / "src" / "devfest_webui.py"),
            "--port",
            str(port),
            "--replay",
            str(Path(recording).resolve()),
            "--replay-speed",
            str(speed),
        ],
        cwd=str(ROOT),
        stdout=server_log.open("w", encoding="utf-8", errors="replace"),
        stderr=subprocess.STDOUT,
    )
    states: list[dict] = []
    meta: dict = {"run_id": run_id, "speed": speed, "port": port, "fps": fps}
    try:
        if not _wait_http(port):
            raise SystemExit(f"stage server did not come up on :{port} (see {server_log})")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded")
            page.wait_for_selector("#seatgrid .seat", timeout=10_000)

            # identity assertions: the page MUST wear its REPLAY identity.
            # (the static banner markup has text while display:none — wait for
            # the chip to actually turn on, i.e. the SSE backlog to flush)
            page.wait_for_function(
                "document.getElementById('replaychip').style.display === 'inline-flex'",
                timeout=10_000,
            )
            page.wait_for_timeout(500)  # let the flushed events paint
            st0 = page.evaluate(STATE_JS)
            # the banner is honest in both shapes: while replaying it names the
            # run id; after replay_done it keeps the RECORDED RUN identity text
            id_ok = (
                st0["replayChip"]
                and "REPLAY" in st0["banner"]
                and (run_id in st0["banner"] or "回放结束" in st0["banner"])
            )
            if not id_ok:
                raise SystemExit(
                    f"page does not identify as REPLAY of {run_id} — refusing to capture"
                )
            meta["identity_banner"] = st0["banner"].strip()
            meta["run_id_in_banner"] = run_id in st0["banner"]
            meta["identity_ok"] = True

            interval = 1.0 / fps
            deadline = time.time() + timeout_s
            done_at: float | None = None
            visited: set[str] = set()
            i = 0
            while time.time() < deadline:
                st = page.evaluate(STATE_JS)
                scene = classify_frame(st)
                # guided camera: view/tab switches only (human-clickable)
                if scene == "celebrate" and st["view"] != "stage" and "to_stage" not in visited:
                    page.evaluate("setView('stage')")
                    visited.add("to_stage")
                elif scene == "check" and "to_ev" not in visited:
                    if st["view"] != "dev":
                        page.evaluate("setView('dev')")
                    page.evaluate("setTab('ev')")
                    visited.add("to_ev")
                elif (
                    scene in ("investigate", "model")
                    and st["tools"] >= 3
                    and int(st["tokens"] or 0) > 40
                    and "to_out" not in visited
                ):
                    if st["view"] != "dev":
                        page.evaluate("setView('dev')")
                    page.evaluate("setTab('out')")
                    visited.add("to_out")
                elif scene == "problem" and st["view"] != "stage":
                    page.evaluate("setView('stage')")
                elif scene == "mission" and st["view"] != "stage":
                    page.evaluate("setView('stage')")

                st["scene"] = scene
                st["frame"] = i
                page.screenshot(path=str(frames_dir / f"{i:04d}.png"), type="png")
                states.append(st)
                i += 1

                if ("回放结束" in st["banner"]) or (st["win"] and "CELEBRATION" in st["story"]):
                    if done_at is None:
                        done_at = time.time()
                    if time.time() - done_at >= hold_s:
                        break
                time.sleep(interval)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()

    (workdir / "states.json").write_text(
        json.dumps({"summary": summary, "states": states, "meta": meta}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {**meta, "summary": summary, "n_frames": len(states)}


# --------------------------------------------------------------------------
# GIF assembly (pure Pillow; testable without a browser)
# --------------------------------------------------------------------------
def select_scenes(
    states: list[dict], durations: dict[str, float], gif_fps: float
) -> dict[str, list[int]]:
    """Evenly sample captured frame indices per scene to hit target durations."""
    buckets: dict[str, list[int]] = {s: [] for s in SCENES}
    for st in states:
        sc = st.get("scene")
        if sc in buckets:
            buckets[sc].append(int(st["frame"]))
    chosen: dict[str, list[int]] = {}
    for sc in SCENES:
        idx = sorted(set(buckets[sc]))
        want = max(1, round(durations[sc] * gif_fps))
        if not idx:
            chosen[sc] = []
            continue
        if len(idx) <= want:
            chosen[sc] = idx + [idx[-1]] * (want - len(idx))
        else:
            step = (len(idx) - 1) / (want - 1) if want > 1 else 0.0
            chosen[sc] = [idx[round(k * step)] for k in range(want)]
    return chosen


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    for cand in (
        rf"C:\Windows\Fonts\segoeu{'' if not bold else 'b'}.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
    ):
        p = Path(cand)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover
        return ImageFont.load_default()


def overlay_badge(img, run_id: str, speed: float, scene: str) -> None:
    """Draw the honesty bar: an opaque full-width strip that replaces the page
    footer in the frame — left: replay identity, right: scene label. Opaque on
    purpose: no page text may bleed through and look like a glitch."""
    from PIL import ImageDraw

    left, right = badge_texts(run_id, speed, scene)
    d = ImageDraw.Draw(img)
    w, h = img.size
    bar_h = max(22, round(h * 0.058))
    fs = max(11, round(w / 74))
    font = _font(fs, bold=True)
    y0 = h - bar_h
    d.rectangle([0, y0, w, h], fill=(20, 20, 20))
    d.line([(0, y0), (w, y0)], fill=(60, 64, 67), width=1)
    ty = y0 + (bar_h - fs) // 2 - 1
    pad = round(w * 0.012)
    d.text((pad, ty), left, fill=(251, 188, 4), font=font)
    tw = d.textlength(right, font=font)
    d.text((w - pad - tw, ty), right, fill=(240, 240, 240), font=font)


def assemble_gif(
    frames_dir: Path,
    chosen: dict[str, list[int]],
    out: Path,
    run_id: str,
    speed: float,
    gif_width: int = 960,
    gif_fps: float = 8.0,
    poster_out: Path | None = None,
) -> dict:
    """Quantize, badge, and encode the curated frames into the looping GIF."""
    from PIL import Image

    seq: list[tuple[int, str]] = [(idx, sc) for sc in SCENES for idx in chosen[sc]]
    if not seq:
        raise ValueError("no frames selected — capture produced no usable scenes")
    master_w = None
    pil_frames: list[Image.Image] = []
    for idx, sc in seq:
        p = frames_dir / f"{idx:04d}.png"
        im = Image.open(p).convert("RGB")
        master_w = im.size[0]
        if im.size[0] != gif_width:
            im = im.resize((gif_width, round(im.size[1] * gif_width / im.size[0])), Image.LANCZOS)
        overlay_badge(im, run_id, speed, sc)
        pil_frames.append(im)

    # one global palette from a montage of the arc (dark UI quantizes well)
    montage = Image.new(
        "RGB", (gif_width, sum(f.size[1] for f in pil_frames[:: max(1, len(pil_frames) // 24)]))
    )
    y = 0
    for f in pil_frames[:: max(1, len(pil_frames) // 24)]:
        montage.paste(f, (0, y))
        y += f.size[1]
    pal = montage.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    qframes = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in pil_frames]

    duration_ms = round(1000 / gif_fps)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    qframes[0].save(
        out,
        save_all=True,
        append_images=qframes[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )

    poster_path = None
    if poster_out is not None:
        p0 = Image.open(frames_dir / f"{chosen['problem'][0]:04d}.png").convert("RGB")
        overlay_badge(p0, run_id, speed, "problem")
        poster_out = Path(poster_out)
        poster_out.parent.mkdir(parents=True, exist_ok=True)
        p0.save(poster_out, format="PNG", optimize=True)
        poster_path = poster_out

    return {
        "gif": str(out),
        "gif_bytes": out.stat().st_size,
        "gif_width": gif_width,
        "gif_height": pil_frames[0].size[1],
        "frames": len(qframes),
        "fps": gif_fps,
        "duration_s": round(len(qframes) * duration_ms / 1000, 2),
        "master_width": master_w,
        "poster": str(poster_path) if poster_path else None,
    }


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="capture the real stage UI into the hero GIF")
    ap.add_argument("--recording", type=Path, default=RECORDING_DEFAULT)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--poster", type=Path, default=POSTER_DEFAULT)
    ap.add_argument("--meta", type=Path, default=META_DEFAULT)
    ap.add_argument("--workdir", type=Path, default=WORKDIR_DEFAULT)
    ap.add_argument("--speed", type=float, default=1.5, help="replay speed (time compression)")
    ap.add_argument("--capture-fps", type=float, default=10.0)
    ap.add_argument("--gif-fps", type=float, default=8.0)
    ap.add_argument("--gif-width", type=int, default=960)
    ap.add_argument("--viewport-w", type=int, default=1536)
    ap.add_argument("--viewport-h", type=int, default=864)
    ap.add_argument("--port", type=int, default=8421)
    ap.add_argument("--hold", type=float, default=3.0, help="seconds to hold the celebration")
    ap.add_argument(
        "--scene-duration",
        action="append",
        default=[],
        metavar="SCENE=SECONDS",
        help="override a scene duration, e.g. problem=2.5 (repeatable)",
    )
    ap.add_argument("--reuse-frames", action="store_true", help="re-encode from workdir cache")
    ap.add_argument("--force", action="store_true", help="allow overwriting existing output files")
    args = ap.parse_args()

    for target in (args.out, args.poster, args.meta):
        if target.exists() and not args.force:
            print(f"ERROR: {target} already exists — pass --force to overwrite")
            return 2

    rec_path = Path(args.recording)
    try:
        recording = json.loads(rec_path.read_text(encoding="utf-8"))
        summary = validate_recording(recording)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: invalid recording {rec_path}: {e}")
        return 2

    durations = dict(DEFAULT_SCENE_DURATIONS)
    for pair in args.scene_duration:
        try:
            sc, secs = pair.rsplit("=", 1)
            if sc not in SCENES:
                raise ValueError(sc)
            durations[sc] = float(secs)
        except ValueError as e:
            print(f"ERROR: bad --scene-duration {pair!r} ({e})")
            return 2

    args.workdir.mkdir(parents=True, exist_ok=True)
    if args.reuse_frames:
        payload = json.loads((args.workdir / "states.json").read_text(encoding="utf-8"))
        states = payload["states"]
        summary = payload["summary"]
        meta = payload["meta"]
    else:
        got = capture_frames(
            rec_path,
            args.workdir,
            speed=args.speed,
            port=args.port,
            fps=args.capture_fps,
            hold_s=args.hold,
            viewport=(args.viewport_w, args.viewport_h),
        )
        states = json.loads((args.workdir / "states.json").read_text(encoding="utf-8"))["states"]
        meta = {
            k: got[k] for k in ("run_id", "speed", "port", "fps", "identity_banner", "identity_ok")
        }

    chosen = select_scenes(states, durations, args.gif_fps)
    result = assemble_gif(
        args.workdir / "frames",
        chosen,
        args.out,
        run_id=summary["run_id"],
        speed=float(meta.get("speed", args.speed)),
        gif_width=args.gif_width,
        gif_fps=args.gif_fps,
        poster_out=args.poster,
    )

    sidecar = {
        "asset": str(Path(args.out).name),
        "source": {
            "recording": _rel(args.recording),
            "sha256": sha256_of(rec_path),
            "run_id": summary["run_id"],
            "model": summary["model"],
            "recorded_at": summary["recorded_at"],
            "n_events": summary["n_events"],
            "tool_sequence": summary["tool_sequence"],
            "pytest_tails": summary["pytest_tails"],
        },
        "capture": {
            **meta,
            "viewport": [args.viewport_w, args.viewport_h],
            "identity": "page banner asserted REPLAY with run id before any frame was kept",
        },
        "gif": {**{k: v for k, v in result.items() if k != "poster"}, "gif": _rel(result["gif"])},
        "poster": _rel(result.get("poster")),
        "scene_durations": durations,
        "scenes": {sc: len(idx) for sc, idx in chosen.items()},
        "honesty": {
            "footage": "real browser rendering of the real stage UI replaying the real recorded run",
            "replay_label": "in-page REPLAY banner + per-frame RECORDED RUN badge",
            "time_compressed": abs(float(meta.get("speed", args.speed)) - 1.0) > 1e-9,
            "live_gpu_metrics": "none read or overlaid",
        },
    }
    Path(args.meta).write_text(json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")

    print(
        f"[hero] {result['frames']} frames @ {result['fps']}fps "
        f"-> {args.out} ({result['gif_bytes'] / 2**20:.2f} MiB, "
        f"{result['gif_width']}x{result['gif_height']}, {result['duration_s']}s loop)"
    )
    print(f"[hero] source run {summary['run_id']} · tools {' -> '.join(summary['tool_sequence'])}")
    print(f"[hero] poster -> {args.poster}")
    print(f"[hero] metadata -> {args.meta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
