"""Render the 1280x640 GitHub social-preview card (Pillow, dev-only).

V2 design — "Your PC. Your Model. Your Agent.": DevFest-dark background,
Google four-color bar and accents, headline with per-word Google colors,
tech chips (Gemma / Antigravity SDK / Ollama / ROCm / Windows), and the
one-line story. No logos or fonts are redistributed; text + CSS-style
geometry only.

Usage:
    python scripts/render_social_preview.py [--out docs/assets/social-preview.png]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

W, H = 1280, 640
BG = (18, 18, 18)
BG2 = (16, 24, 44)
FG = (240, 240, 240)
DIM = (154, 160, 166)
BLUE = (66, 133, 244)
RED = (234, 67, 53)
YELLOW = (251, 188, 4)
GREEN = (52, 168, 83)
CHIP_BG = (38, 38, 38)


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


def render_social_preview(
    out: Path,
    title: str = "Antigravity-Gemma-ROCm",
    tagline: str = "Real Local AI Coding Agent on AMD ROCm",
    chips: list[str] | None = None,
) -> Path:
    chips = chips or ["Gemma 4 26B", "Antigravity SDK", "Ollama", "AMD ROCm gfx1151", "Windows 11"]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # subtle vertical blend toward a deep blue at the bottom
    for y in range(H // 2, H):
        t = (y - H // 2) / (H // 2)
        fill = tuple(int(a + (b - a) * t) for a, b in zip(BG, BG2, strict=False))
        d.line([(0, y), (W, y)], fill=fill)

    # Google four-color top bar
    q = W // 4
    for i, c in enumerate((BLUE, RED, YELLOW, GREEN)):
        d.rectangle([i * q, 0, (i + 1) * q - 1, 12], fill=c)

    f_repo = _font(26, bold=True)
    f_big = _font(88, bold=True)
    f_tag = _font(36, bold=True)
    f_chip = _font(24, bold=True)
    f_small = _font(22)
    f_url = _font(24, bold=True)

    # repo name (top-left)
    d.text((66, 52), title, fill=DIM, font=f_repo)

    # headline (two lines, key words in Google colors). Self-checked below
    # so an overlong line can never silently clip at the card edge again.
    lines = (
        [(("Your ", FG), ("PC.", BLUE), ("  Your ", FG), ("Model.", RED))],
        [(("Your ", FG), ("Agent.", GREEN))],
    )
    y0 = 128
    for li, line in enumerate(lines):
        x, y = 66, y0 + li * 104
        for text, color in line[0]:
            d.text((x, y), text, fill=color, font=f_big)
            x += d.textlength(text, font=f_big)

    # tagline
    d.text((66, 356), tagline, fill=(138, 180, 248), font=f_tag)

    # one-line story
    d.text(
        (66, 424),
        "39/40 seats. One broken registration. A local agent reads the code,",
        fill=DIM,
        font=f_small,
    )
    d.text(
        (66, 458),
        "fixes the bug, runs the tests — and saves the last seat.",
        fill=DIM,
        font=f_small,
    )

    # chips row
    cx, cy = 66, 516
    for chip in chips:
        w = d.textlength(chip, font=f_chip) + 40
        d.rounded_rectangle(
            [cx, cy, cx + w, cy + 50], radius=25, fill=CHIP_BG, outline=(70, 74, 77), width=1
        )
        d.text((cx + 20, cy + 11), chip, fill=FG, font=f_chip)
        cx += w + 16
    assert cx - 16 <= W - 40, f"chips overflow the card width ({cx:.0f} > {W - 40})"

    # measured-on strip + URL (must not collide: checked, not assumed)
    measured = "measured on Ryzen AI MAX+ 395 · Radeon 8060S"
    url = "github.com/AIwork4me/Antigravity-Gemma-ROCm"
    d.text((66, 588), measured, fill=GREEN, font=f_small)
    d.text((W - 66 - d.textlength(url, font=f_url), 588), url, fill=FG, font=f_url)
    assert 66 + d.textlength(measured, font=f_small) < W - 66 - d.textlength(url, font=f_url), (
        "bottom-strip texts collide"
    )

    # headline fit self-check (both lines fully inside the card)
    for line in lines:
        width = sum(d.textlength(t, font=f_big) for t, _ in line[0])
        assert 66 + width <= W - 40, (
            f"headline line overflows the card ({66 + width:.0f}px > {W - 40}px)"
        )

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
