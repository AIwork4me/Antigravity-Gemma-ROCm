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
  - the recording must be a COMPLETE accepted run: pytest before FAILED
    (non-zero exit), pytest after green (exit 0), an edit tool observed in
    between, ``done ok:true`` with no test tampering / forbidden-file
    changes / disk drift, and a final verified probe admitting seat 40 —
    all under one run id, in the right order. Anything less is rejected:
    no success imagery can be produced;
  - the page must visibly identify itself as REPLAY (banner names the
    run id); the capture aborts otherwise, and every GIF frame carries a
    ``RECORDED REAL RUN <id> · EDITED HIGHLIGHTS`` badge;
  - no live GPU metrics are read or overlaid — replayed GPU events render
    with the page's own ``[历史]`` prefix, as-is;
  - the "edit" in EDITED HIGHLIGHTS is scene selection, per-scene pacing
    and real-frame zoom crops only — event order is never changed and no
    state, tool call, or verdict is ever fabricated (sidecar records the
    exact editing: source span, replay speed, scene durations, cuts).

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

    Returns a summary (run_id, model, tool sequence, pytest tails, elapsed,
    event span). Raises ValueError on any of these honesty violations:

      - bad container (not format 1 / no events / no meta run_id);
      - unknown event kinds;
      - out-of-order events (story stage regression, pytest after-before
        inversion, acceptance before verification, verified seat before done);
      - pytest "before" does not FAIL (exit must be non-zero — the bug must
        be demonstrated before the run) or pytest "after" is not green;
      - a done event that is not a full acceptance: ``ok:true``, not a
        design preview, ``tests_tampered`` falsy, ``forbidden_changed`` and
        ``disk_drift`` empty, bug present before and fixed after;
      - the final business probe is not a verified admission of seat 40
        (``verified:true`` + ``admitted:true`` + ``seat:40``);
      - run-id mismatch: any event stamped with a run_id (the done event
        must be) that differs from the recording meta.

    A recording failing any of these can never yield success imagery.
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
    run_id = str(meta["run_id"])
    events = rec["events"]
    if not events:
        raise ValueError("recording has no events")

    stages_seen: list[str] = []
    tool_names: list[str] = []
    pytest_events: list[tuple[int, dict]] = []
    done_idx: int | None = None
    done_ev: dict | None = None
    verified_seat_idx: int | None = None
    verified_seat_ev: dict | None = None
    for i, ev in enumerate(events):
        if not isinstance(ev, dict) or ev.get("kind") not in _KNOWN_KINDS:
            raise ValueError(
                f"unknown replay event kind: {ev.get('kind') if isinstance(ev, dict) else type(ev)!r}"
            )
        kind, data = ev["kind"], ev.get("data")
        if not isinstance(data, dict):
            continue
        ev_rid = data.get("run_id")
        if isinstance(ev_rid, str) and ev_rid != run_id:
            raise ValueError(f"event #{i} carries run_id {ev_rid!r} != recording run {run_id!r}")
        if kind == "story" and data.get("stage") in _STORY_ORDER:
            stages_seen.append(data["stage"])
        elif kind == "pytest":
            pytest_events.append((i, data))
        elif kind == "tool" and data.get("name"):
            tool_names.append(str(data["name"]))
        elif kind == "done":
            if done_idx is not None:
                raise ValueError("recording has more than one done event")
            done_idx, done_ev = i, data
        elif kind == "seat" and data.get("verified") is True:
            if verified_seat_idx is not None:
                raise ValueError("recording has more than one verified seat event")
            verified_seat_idx, verified_seat_ev = i, data

    # --- event order ------------------------------------------------------
    order_idx = [_STORY_ORDER.index(s) for s in stages_seen]
    if any(b <= a for a, b in zip(order_idx, order_idx[1:], strict=False)):
        raise ValueError("story stages regress or repeat out of order in the recording")
    if "problem" not in stages_seen or "celebration" not in stages_seen:
        raise ValueError("recording does not span problem -> celebration")
    before_idx = next((i for i, d in pytest_events if d.get("phase") == "before"), None)
    after_idx = next((i for i, d in pytest_events if d.get("phase") == "after"), None)
    if before_idx is None or after_idx is None:
        raise ValueError("recording lacks the before/after pytest verification pair")
    if after_idx < before_idx:
        raise ValueError("pytest 'after' appears before 'before' in the recording")
    first_edit = next(
        (
            i
            for i, n in enumerate(events)
            if n.get("kind") == "tool"
            and isinstance(n.get("data"), dict)
            and n["data"].get("name") in ("edit_file", "create_file", "replace_file_content")
        ),
        None,
    )
    if first_edit is None or first_edit > after_idx:
        raise ValueError("no edit tool observed before the green pytest — nothing was fixed")
    if done_idx is None:
        raise ValueError("recording has no done event — refusing success imagery")
    if done_idx < after_idx:
        raise ValueError("acceptance (done) precedes the green pytest — out of order")
    if verified_seat_idx is None:
        raise ValueError("recording lacks the final verified seat — refusing success imagery")
    if verified_seat_idx < done_idx:
        raise ValueError("verified seat precedes acceptance (done) — out of order")

    # --- pytest verdicts ---------------------------------------------------
    before_ev = next(d for _, d in pytest_events if d.get("phase") == "before")
    after_ev = next(d for _, d in pytest_events if d.get("phase") == "after")
    if before_ev.get("exit") == 0 or not isinstance(before_ev.get("exit"), int):
        raise ValueError(
            "pytest 'before' did not fail (exit must be non-zero) — bug not demonstrated"
        )
    if after_ev.get("exit") != 0:
        raise ValueError("pytest 'after' is not green (exit != 0) — refusing success imagery")

    # --- full acceptance chain --------------------------------------------
    if not isinstance(done_ev, dict) or done_ev.get("ok") is not True or done_ev.get("preview"):
        raise ValueError(
            "recording has no accepted completion (done ok:true) — refusing success imagery"
        )
    for field, why in (
        ("tests_tampered", "frozen tests were tampered with"),
        ("forbidden_changed", "files changed outside the whitelist"),
        ("disk_drift", "on-disk state drifted from the audited map"),
    ):
        val = done_ev.get(field)
        if val is None:
            raise ValueError(f"done event lacks the {field!r} acceptance field")
        if val:
            raise ValueError(f"acceptance violated: {why} ({field}={val!r})")
    if done_ev.get("bug_present_before") is not True:
        raise ValueError("done event does not attest the bug was present before the run")
    if done_ev.get("bug_fixed_after") is not True:
        raise ValueError("done event does not attest the bug was fixed after the run")
    if done_ev.get("run_id") != run_id:
        raise ValueError(f"done event run_id {done_ev.get('run_id')!r} != recording run {run_id!r}")

    # --- final business probe: verified admission of seat 40 ---------------
    probe = verified_seat_ev.get("probe")
    if not isinstance(probe, dict):
        raise ValueError("verified seat event carries no probe verdict")
    if probe.get("admitted") is not True:
        raise ValueError("final probe did not admit (admitted != true) — refusing success imagery")
    if probe.get("seat") != 40:
        raise ValueError(f"final probe admitted seat {probe.get('seat')!r}, not seat 40")

    pytest_tails = {str(d.get("phase")): str(d.get("tail", "")) for _, d in pytest_events}
    ts = [e.get("ts") for e in events if isinstance(e.get("ts"), (int, float))]
    return {
        "run_id": run_id,
        "model": str(meta.get("model", "model")),
        "recorded_at": str(meta.get("recorded_at", "")),
        "n_events": len(events),
        "tool_sequence": tool_names,
        "pytest_tails": pytest_tails,
        "elapsed_s": done_ev.get("elapsed_s"),
        "n_tool_calls": done_ev.get("n_tool_calls"),
        "event_span_s": round(max(ts) - min(ts), 1) if len(ts) >= 2 else None,
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
    """(left, right) overlay chips. The left chip is the honesty badge: the
    footage is always a RECORDED REAL RUN, and the loop is always an edited
    highlights cut (scene selection + optional zoom on real frames) — never
    a live claim, never an unlabeled replay. `speed` is kept in the
    signature for API compatibility; the pace itself lives in the sidecar."""
    return (f"RECORDED REAL RUN {run_id} · EDITED HIGHLIGHTS", SCENE_LABELS.get(scene, scene))


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


# ---------------------------------------------------------------- zoom crops
# Readability cuts: scenes whose payload lives in the right SDK panel get a
# zoom made ONLY from the real captured frame — a crop of genuine browser
# pixels, upscaled. Nothing is re-rendered or synthesized; the honesty bar
# still labels every frame RECORDED REAL RUN · EDITED HIGHLIGHTS.
# Values: (after_fraction, x0, y0, x1, y1) with coordinates as fractions of
# the master frame. `after_fraction` = position within the scene where the
# cut switches from the full-stage view to the zoom.
ZOOM_SCENES: dict[str, tuple[float, float, float, float, float]] = {
    # the real Antigravity SDK panel: pipeline + live tool cards
    "investigate": (0.45, 0.468, 0.170, 0.981, 0.735),
    # the Evidence tab: pytest red->green boxes + acceptance summary
    "check": (0.30, 0.468, 0.300, 0.981, 0.800),
}


def _crop_169(im, fx0: float, fy0: float, fx1: float, fy1: float):
    """Crop a fractional box from a real frame, widened/centered to exact
    16:9 so the zoom fills the GIF canvas without distortion."""
    w, h = im.size
    x0, y0 = int(fx0 * w), int(fy0 * h)
    x1, y1 = int(fx1 * w), int(fy1 * h)
    cw = max(16, x1 - x0)
    ch = max(9, y1 - y0)
    if cw / ch > 16 / 9:  # too wide -> trim width, keep center
        cw = round(ch * 16 / 9)
        cx = (x0 + x1) // 2
        x0, x1 = cx - cw // 2, cx + cw // 2
    else:  # too tall -> trim height, keep center
        ch = round(cw * 9 / 16)
        cy = (y0 + y1) // 2
        y0, y1 = cy - ch // 2, cy + ch // 2
    return im.crop((max(0, x0), max(0, y0), min(w, x1), min(h, y1)))


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
    """Quantize, badge, and encode the curated frames into the looping GIF.

    Scenes listed in ZOOM_SCENES start on the full-stage view, then cut to a
    zoom crop taken from the SAME real captured frames (no re-rendering).
    """
    from PIL import Image

    seq: list[tuple[int, int, str]] = [
        (pos, idx, sc) for sc in SCENES for pos, idx in enumerate(chosen[sc])
    ]
    if not seq:
        raise ValueError("no frames selected — capture produced no usable scenes")
    master_w = None
    pil_frames: list[Image.Image] = []
    zoom_used: dict[str, int] = {}
    for pos, idx, sc in seq:
        p = frames_dir / f"{idx:04d}.png"
        im = Image.open(p).convert("RGB")
        master_w = im.size[0]
        if sc in ZOOM_SCENES and pos >= round(ZOOM_SCENES[sc][0] * len(chosen[sc])):
            _, fx0, fy0, fx1, fy1 = ZOOM_SCENES[sc]
            im = _crop_169(im, fx0, fy0, fx1, fy1)
            zoom_used[sc] = zoom_used.get(sc, 0) + 1
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
        "zoom_frames": zoom_used,
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
        # summary stays the one freshly validated from the recording above —
        # the cached one may predate new validation fields (e.g. event_span_s)
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
            "run_elapsed_s": summary.get("elapsed_s"),
            "event_span_s": summary.get("event_span_s"),
        },
        "capture": {
            **meta,
            "viewport": [args.viewport_w, args.viewport_h],
            "identity": "page banner asserted REPLAY with run id before any frame was kept",
        },
        "gif": {
            **{k: v for k, v in result.items() if k != "poster"},
            "gif": _rel(result["gif"]),
            "duration_s": result["duration_s"],
        },
        "poster": _rel(result.get("poster")),
        "editing": {
            "mode": "scene-cut highlights from one continuous browser replay of the real recording",
            "replay_speed": float(meta.get("speed", args.speed)),
            "source_run_elapsed_s": summary.get("elapsed_s"),
            "source_event_span_s": summary.get("event_span_s"),
            "final_gif_duration_s": result["duration_s"],
            "scene_durations_s": durations,
            "per_scene_frames": {sc: len(idx) for sc, idx in chosen.items()},
            "zoom_cuts": {
                sc: f"frames {ZOOM_SCENES[sc][0]:.0%} into the scene onward are real-frame crops of the SDK panel region"
                for sc in result.get("zoom_frames", {})
            },
            "guarantees": (
                "event order never changed; frames come from the continuous replay capture "
                "only; crops are genuine browser pixels upscaled; no state, tool call, or "
                "verdict fabricated"
            ),
        },
        "honesty": {
            "footage": "real browser rendering of the real stage UI replaying the real recorded run",
            "replay_label": "in-page REPLAY banner + per-frame RECORDED REAL RUN · EDITED HIGHLIGHTS badge",
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
