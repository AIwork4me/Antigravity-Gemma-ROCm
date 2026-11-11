"""Render a DevFest-themed animated GIF from a "The Last Seat" recording.

The recording is the SAME file the stage dashboard writes with --record, so
the GIF is generated from a REAL run's event stream — no screen capture, no
fabricated frames. Palette follows the DevFest 2026 dark theme + Google four
colors; the top status line tracks the seat narrative (39/40 rejected ->
40/40 YOU'RE IN!) exactly as the events say.

Usage:
    python scripts/render_devfest_gif.py --recording <recording.json>
        [--out docs/assets/devfest-last-seat.gif] [--width 960] [--max-frames 70]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BG = (30, 30, 30)  # #1E1E1E, DevFest 2026 dark
FG = (240, 240, 240)
BLUE = (66, 133, 244)
GREEN = (52, 168, 83)
RED = (234, 67, 53)
YELLOW = (251, 188, 4)
DIM = (154, 160, 166)

KIND_ICON = {
    "stage": "»»",
    "tool": "TOOL",
    "policy": "POL",
    "token": "say",
    "pytest": "TEST",
    "diff": "DIFF",
    "done": "DONE",
    "error": "ERR!",
    "meta": "meta",
    "replay": "RPLY",
    "seat": "SEAT",
    "gpu": "GPU",
}


def _text_for(ev: dict) -> str:
    kind = ev.get("kind", "event")
    data = ev.get("data")
    if isinstance(data, dict):
        if kind == "seat":
            probe = data.get("probe", {})
            if probe.get("admitted"):
                body = f"admitted -> seat {probe.get('seat', '?')} (real code verdict)"
            elif probe.get("probe_error"):
                body = f"probe error: {str(probe['probe_error'])[:80]}"
            else:
                body = f"REJECTED: {probe.get('error_text', '?')[:70]}"
        elif kind == "gpu":
            body = f"backend: {data.get('backend', '?')}" + (
                f"  digest {str(data.get('digest', ''))[:12]}" if data.get("digest") else ""
            )
        elif "text" in data:
            body = str(data["text"])
        elif kind == "tool":
            body = f"{data.get('name', '?')} {str(data.get('args', ''))[:80]}"
        elif kind == "pytest":
            verdict = "PASS" if data.get("exit") == 0 else "FAIL"
            body = f"{data.get('phase', '?')}: {verdict}"
        elif kind == "done":
            if data.get("preview"):
                body = "DESIGN PREVIEW ONLY (not a live run)"
            else:
                body = (
                    "LAST SEAT FIXED — independently verified"
                    if data.get("ok")
                    else "acceptance FAILED"
                ) + (f"  files: {', '.join(data.get('files_changed', [])[:3])}")
        else:
            body = json.dumps(data, ensure_ascii=False)[:100]
    else:
        body = str(data)[:100]
    return f"[{KIND_ICON.get(kind, kind):4}] {body}"


def render_gif(recording: dict, out: Path, width: int = 960, max_frames: int = 70) -> Path:
    if not isinstance(recording, dict) or recording.get("format") != 1:
        raise ValueError("unsupported recording: expected format 1")
    events = list(recording.get("events", []))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    from PIL import Image, ImageDraw, ImageFont

    font = None
    title_font = None
    for cand in (
        r"C:\Windows\Fonts\msyh.ttc",  # Microsoft YaHei (ASCII + CJK)
        r"C:\Windows\Fonts\seguisym.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ):
        try:
            if Path(cand).exists():
                font = ImageFont.truetype(cand, 15)
                title_font = ImageFont.truetype(cand, 17)
                break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=15)
            title_font = ImageFont.load_default(size=17)
        except TypeError:
            font = title_font = ImageFont.load_default()

    if len(events) > max_frames:
        step = len(events) / max_frames
        events = [events[int(i * step)] for i in range(max_frames - 1)] + [events[-1]]

    line_h = 25
    pad = 16
    model = recording.get("meta", {}).get("model", "model")
    header = f"  Google DevFest 2026 — THE LAST SEAT  ·  {model} · local, no cloud"
    lines_per_frame = 13

    frames: list[Image.Image] = []
    for start in range(0, len(events), max(1, len(events) // max_frames) if len(events) > 8 else 1):
        chunk = events[max(0, start - lines_per_frame + 1) : start + 1]
        lines = [f"{ev.get('t', '--:--:--')} {_text_for(ev)}" for ev in chunk][-lines_per_frame:]
        # seat state is derived from events exactly as they happened
        seats_won = any(
            ev.get("kind") == "seat"
            and isinstance(ev.get("data"), dict)
            and ev["data"].get("probe", {}).get("admitted")
            or (
                ev.get("kind") == "done"
                and isinstance(ev.get("data"), dict)
                and ev["data"].get("ok")
            )
            for ev in events[: start + 1]
        )
        img = Image.new("RGB", (width, line_h * (len(lines) + 3) + pad * 2), BG)
        d = ImageDraw.Draw(img)
        # Google four-color bar
        q = width // 4
        for i, c in enumerate((BLUE, RED, YELLOW, GREEN)):
            d.rectangle([i * q, 0, (i + 1) * q - 1, 5], fill=c)
        d.rectangle([0, 6, width, 44], fill=(38, 38, 38))
        d.text((pad, 12), header, fill=FG, font=title_font)
        if seats_won:
            seat_line = "  SEATS: 40/40 — YOU'RE IN! (independently verified)"
            d.text((pad, 30), seat_line, fill=GREEN, font=font)
        else:
            d.text(
                (pad, 30),
                "  SEATS: 39/40 — last registration REJECTED (live bug)",
                fill=RED,
                font=font,
            )
        y = 54
        for line in lines:
            color = FG
            low = line
            if "[POL" in low:
                color = (255, 224, 130)
            elif "[TEST" in low:
                color = GREEN if "PASS" in low or "after" in low else RED
            elif "[SEAT" in low or "[DONE" in low or "[GPU" in low:
                color = (
                    GREEN if ("admitted" in low or "VERIFIED" in low or "verified" in low) else FG
                )
            elif "[ERR" in low:
                color = RED
            d.text((pad, y), line[:118], fill=color, font=font)
            y += line_h
        frames.append(img)

    if not frames:
        frames = [Image.new("RGB", (width, 120), BG)]

    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=850,
        loop=0,
        optimize=True,
    )
    print(f"[gif] {len(frames)} frames -> {out} ({out.stat().st_size / 2**20:.2f} MiB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="render a Last Seat recording to a DevFest GIF")
    ap.add_argument("--recording", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "assets" / "devfest-last-seat.gif")
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--max-frames", type=int, default=70)
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
