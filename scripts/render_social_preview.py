"""Render the 1280x640 GitHub social-preview card (Pillow, dev-only).

Usage:
    python scripts/render_social_preview.py [--out docs/assets/social-preview.png]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

W, H = 1280, 640
TOP = (11, 16, 31)  # #0b1020
BOTTOM = (16, 26, 54)  # #101a36
FG = (219, 230, 255)
ACCENT = (79, 140, 255)
CHIP_BG = (18, 26, 48)
CHIP_FG = (159, 167, 255)
DIM = (125, 141, 181)
GREEN = (46, 204, 113)


def render_social_preview(
    out: Path,
    title: str = "Antigravity-Gemma-ROCm",
    tagline: str = "A fully local AI coding agent on Windows",
    chips: list[str] | None = None,
) -> Path:
    chips = chips or ["AMD ROCm", "Ryzen AI MAX 395/495", "Gemma", "Ollama", "Windows"]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (W, H), TOP)
    d = ImageDraw.Draw(img)
    # vertical gradient
    for y in range(H):
        t = y / H
        d.line(
            [(0, y), (W, y)],
            fill=tuple(int(a + (b - a) * t) for a, b in zip(TOP, BOTTOM, strict=False)),
        )

    try:
        f_title = ImageFont.load_default(size=64)
        f_tag = ImageFont.load_default(size=30)
        f_chip = ImageFont.load_default(size=22)
        f_small = ImageFont.load_default(size=20)
    except TypeError:
        f_title = f_tag = f_chip = f_small = ImageFont.load_default()

    # accent rail
    d.rectangle([70, 130, 78, 330], fill=ACCENT)

    d.text((104, 130), title, fill=FG, font=f_title)
    d.text((106, 224), tagline, fill=ACCENT, font=f_tag)
    d.text(
        (106, 276),
        "zero cloud  ·  zero API spend  ·  verified red -> green",
        fill=DIM,
        font=f_small,
    )

    # chips row
    x = 106
    y = 350
    for chip in chips:
        w = d.textlength(chip, font=f_chip) + 36
        d.rounded_rectangle(
            [x, y, x + w, y + 46], radius=23, fill=CHIP_BG, outline=(35, 51, 80), width=1
        )
        d.text((x + 18, y + 11), chip, fill=CHIP_FG, font=f_chip)
        x += w + 14

    # status strip
    d.text(
        (104, 470),
        "measured on Ryzen AI MAX+ 395 · Radeon 8060S (gfx1151) · ROCm on Windows",
        fill=GREEN,
        font=f_small,
    )
    d.text((104, 540), "github.com/AIwork4me/Antigravity-Gemma-ROCm", fill=FG, font=f_chip)

    img.save(out, format="PNG", optimize=True)
    print(f"[preview] {img.size[0]}x{img.size[1]} -> {out} ({out.stat().st_size / 2**10:.0f} KiB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="render the GitHub social preview card")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "assets" / "social-preview.png")
    args = ap.parse_args()
    render_social_preview(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
