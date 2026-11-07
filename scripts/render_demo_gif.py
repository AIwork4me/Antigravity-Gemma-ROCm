"""Render a terminal-style animated GIF from a web UI recording (run.json).

The recording is the SAME file the web dashboard writes with --record, so the
GIF is generated from a real run's event stream — no screen capture needed.

Usage:
    python scripts/render_demo_gif.py --recording <run.json> [--out docs/assets/demo.gif]
        [--width 960] [--max-frames 60]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BG = (11, 16, 31)  # #0b1020, matches the dashboard
FG = (219, 230, 255)
ACCENT = (79, 140, 255)
GREEN = (46, 204, 113)
RED = (255, 93, 115)
DIM = (125, 141, 181)

KIND_ICON = {
    "stage": ">>",
    "tool": "TOOL",
    "policy": "POLICY",
    "token": "say",
    "pytest": "PYTEST",
    "diff": "DIFF",
    "done": "DONE",
    "error": "ERROR",
    "meta": "meta",
    "replay": "REPLAY",
}


def _text_for(ev: dict) -> str:
    """One printable line for an event (short, terminal-flavored)."""
    kind = ev.get("kind", "event")
    data = ev.get("data")
    if isinstance(data, dict):
        if "text" in data:
            body = str(data["text"])
        elif kind == "tool":
            body = f"{data.get('name', '?')} {str(data.get('args', ''))[:90]}"
        elif kind == "pytest":
            verdict = "PASS" if data.get("exit") == 0 else "FAIL"
            body = f"{data.get('phase', '?')}: {verdict}"
        elif kind == "done":
            body = ("bug fixed, tests GREEN" if data.get("ok") else "incomplete") + (
                f"  files: {', '.join(data.get('files_changed', [])[:3])}"
            )
        else:
            body = json.dumps(data, ensure_ascii=False)[:110]
    else:
        body = str(data)[:110]
    return f"[{KIND_ICON.get(kind, kind):6}] {body}"


def _frame_lines(events: list[dict]) -> list[str]:
    return [f"{ev.get('t', '--:--:--')} {_text_for(ev)}" for ev in events]


def render_gif(recording: dict, out: Path, width: int = 960, max_frames: int = 60) -> Path:
    """Render the recording as an animated terminal GIF; returns its path."""
    if not isinstance(recording, dict) or recording.get("format") != 1:
        raise ValueError("unsupported recording: expected format 1")
    events = list(recording.get("events", []))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    from PIL import Image, ImageDraw, ImageFont

    # CJK-capable font stack: the default bitmap font lacks CJK glyphs (tofu).
    font = None
    for cand in (
        r"C:\Windows\Fonts\msyh.ttc",  # Microsoft YaHei (ASCII + CJK)
        r"C:\Windows\Fonts\seguisym.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ):
        try:
            if Path(cand).exists():
                font = ImageFont.truetype(cand, 16)
                break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=16)
        except TypeError:
            font = ImageFont.load_default()

    # sample the event list down to max_frames "keyframes", always keeping the last
    if len(events) > max_frames:
        step = len(events) / max_frames
        events = [events[int(i * step)] for i in range(max_frames - 1)] + [events[-1]]

    line_h = 26
    pad = 18
    header = (
        f"  local agent — {recording.get('meta', {}).get('model', 'model')}  ·  fully on-device"
    )
    lines_per_frame = 14

    frames: list[Image.Image] = []
    for start in range(0, len(events), max(1, len(events) // max_frames) if len(events) > 8 else 1):
        chunk = events[max(0, start - lines_per_frame + 1) : start + 1]
        lines = _frame_lines(chunk)[-lines_per_frame:]
        img = Image.new("RGB", (width, line_h * (len(lines) + 2) + pad * 2), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, width, 38], fill=(16, 26, 54))
        d.text((pad, 9), header, fill=ACCENT, font=font)
        y = 48
        for line in lines:
            color = FG
            if "POLICY" in line:
                color = (255, 217, 168)
            elif "PYTEST" in line:
                color = GREEN if "PASS" in line else RED
            elif "DONE" in line or "REPLAY" in line:
                color = GREEN
            d.text((pad, y), line[:120], fill=color, font=font)
            y += line_h
        frames.append(img)

    if not frames:  # empty recording -> single idle frame
        frames = [Image.new("RGB", (width, 120), BG)]

    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=900,
        loop=0,
        optimize=True,
    )
    print(f"[gif] {len(frames)} frames -> {out} ({out.stat().st_size / 2**20:.2f} MiB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="render a recording to a terminal-style GIF")
    ap.add_argument("--recording", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "assets" / "demo.gif")
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--max-frames", type=int, default=60)
    args = ap.parse_args()
    try:
        recording = json.loads(args.recording.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot read recording: {e}")
        return 2
    render_gif(recording, args.out, width=args.width, max_frames=args.max_frames)
    return 0


if __name__ == "__main__":
    sys.exit(main())
