"""Render the Model/Harness/Compute architecture diagram (Pillow, dev-only).

A clean, repo-styled PNG for the README "How it works" section:

    Gemma 4 26B  ->  Antigravity SDK  ->  Real Tools  ->  Application
    (model decides) (harness executes)  (view/edit/run)  (40-seat app)
                     fail-closed policy
    ------------------------------------------------------------
    Ollama (local OpenAI-compatible) + AMD ROCm -> local GPU compute

Usage:
    python scripts/render_agent_flow.py [--out docs/assets/devfest-agent-flow.png]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

W, H = 1600, 900
BG = (30, 30, 30)
CARD = (38, 38, 38)
CARD2 = (45, 45, 45)
LINE = (60, 64, 67)
FG = (240, 240, 240)
DIM = (154, 160, 166)
BLUE = (66, 133, 244)
RED = (234, 67, 53)
YELLOW = (251, 188, 4)
GREEN = (52, 168, 83)


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    for cand in (
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
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


def render_agent_flow(out: Path) -> Path:
    from PIL import Image, ImageDraw

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Google four-color top bar
    q = W // 4
    for i, c in enumerate((BLUE, RED, YELLOW, GREEN)):
        d.rectangle([i * q, 0, (i + 1) * q - 1, 10], fill=c)

    f_kicker = _font(26, bold=True)
    f_title = _font(44, bold=True)
    f_box = _font(31, bold=True)
    f_small = _font(19)
    f_tiny = _font(17)

    d.text(
        (70, 64), "MODEL  ·  HARNESS  ·  COMPUTE  —  ALL ON YOUR MACHINE", fill=DIM, font=f_kicker
    )
    d.text((70, 104), "How the local agent works", fill=FG, font=f_title)

    # ---- top row: the agent pipeline ----
    nodes = [
        ("Gemma 4 26B", "the model decides\nwhich tools to call", BLUE),
        ("Antigravity SDK", "the harness executes\nunder a fail-closed policy", RED),
        ("Real Tools", "view_file · edit_file ·\nrun_command", YELLOW),
        ("Application", "the 40-seat workshop\nregistration app", GREEN),
    ]
    x0, y0, nw, nh, gap = 70, 250, 330, 190, 47
    centers = []
    for i, (name, sub, color) in enumerate(nodes):
        x = x0 + i * (nw + gap)
        d.rounded_rectangle([x, y0, x + nw, y0 + nh], radius=18, fill=CARD, outline=color, width=3)
        d.text((x + 24, y0 + 26), name, fill=color, font=f_box)
        dy = y0 + 84
        for ln in sub.split("\n"):
            d.text((x + 24, dy), ln, fill=DIM, font=f_small)
            dy += 30
        centers.append(x + nw // 2)
        if i:
            ax = x0 + i * (nw + gap) - gap
            d.line([(ax + 6, y0 + nh // 2), (ax + gap - 6, y0 + nh // 2)], fill=FG, width=4)
            d.polygon(
                [
                    (ax + gap - 4, y0 + nh // 2 - 11),
                    (ax + gap + 8, y0 + nh // 2),
                    (ax + gap - 4, y0 + nh // 2 + 11),
                ],
                fill=FG,
            )

    # labels under the pipeline (the loop)
    d.text(
        (70, y0 + nh + 28),
        "Model decides  →  Harness executes  →  Tools return evidence  →  Agent continues",
        fill=FG,
        font=f_small,
    )
    d.text(
        (70, y0 + nh + 66),
        "An independent verifier re-runs the frozen tests and audits every change (SHA256 + whitelisted diff).",
        fill=DIM,
        font=f_tiny,
    )

    # ---- bottom band: local compute ----
    by = 620
    d.line([(70, by - 26), (W - 70, by - 26)], fill=LINE, width=2)
    d.text((70, by - 10), "LOCAL COMPUTE — NO CLOUD, NO API KEYS", fill=DIM, font=f_kicker)

    # left: Ollama box; right: ROCm box; feeding the model node above
    bw, bh = 480, 150
    d.rounded_rectangle(
        [70, by + 34, 70 + bw, by + 34 + bh], radius=16, fill=CARD2, outline=LINE, width=2
    )
    d.text((94, by + 56), "Ollama", fill=FG, font=f_box)
    d.text((94, by + 108), "local OpenAI-compatible server · gemma4:26b", fill=DIM, font=f_tiny)
    d.rounded_rectangle(
        [W - 70 - bw, by + 34, W - 70, by + 34 + bh], radius=16, fill=CARD2, outline=LINE, width=2
    )
    d.text((W - 70 - bw + 24, by + 56), "AMD ROCm", fill=FG, font=f_box)
    d.text(
        (W - 70 - bw + 24, by + 108),
        "Radeon 8060S (gfx1151) · llama-server HIP runner",
        fill=DIM,
        font=f_tiny,
    )
    # arrows: ollama -> (up to) gemma node; rocm -> ollama
    arrow_up_x = centers[0]
    d.line([(arrow_up_x, by + 30), (arrow_up_x, y0 + nh + 108)], fill=BLUE, width=4)
    d.polygon(
        [
            (arrow_up_x - 11, y0 + nh + 120),
            (arrow_up_x, y0 + nh + 106),
            (arrow_up_x + 11, y0 + nh + 120),
        ],
        fill=BLUE,
    )
    mid_y = by + 34 + bh // 2
    d.line([(70 + bw + 12, mid_y), (W - 70 - bw - 12, mid_y)], fill=GREEN, width=4)
    d.polygon(
        [
            (W - 70 - bw - 2, mid_y - 11),
            (W - 70 - bw + 12, mid_y),
            (W - 70 - bw - 2, mid_y + 11),
        ],
        fill=GREEN,
    )
    d.text(
        (W // 2 - 190, mid_y - 34),
        "tokens generated on-device",
        fill=GREEN,
        font=f_tiny,
    )

    d.text(
        (70, H - 52),
        "Windows 11 · Ryzen AI MAX+ 395 · measured, not promised",
        fill=DIM,
        font=f_tiny,
    )
    d.text(
        (W - 70 - 460, H - 52), "github.com/AIwork4me/Antigravity-Gemma-ROCm", fill=FG, font=f_tiny
    )

    img.save(out, format="PNG", optimize=True)
    print(f"[agent-flow] {W}x{H} -> {out} ({out.stat().st_size / 2**10:.0f} KiB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="render the Model/Harness/Compute diagram")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "assets" / "devfest-agent-flow.png")
    args = ap.parse_args()
    render_agent_flow(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
