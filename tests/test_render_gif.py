"""Tests for asset generators — hero GIF + social preview (Pillow, dev-only)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

pytest.importorskip("PIL")

from render_demo_gif import render_gif  # noqa: E402
from render_social_preview import render_social_preview  # noqa: E402


def _recording() -> dict:
    return {
        "format": 1,
        "meta": {"run_id": "t1", "recorded_at": "2026-11-07 12:00:00", "model": "gemma4:26b"},
        "events": [
            {
                "id": 1,
                "kind": "stage",
                "data": {"text": "重置基线 restore"},
                "t": "12:00:00",
                "ts": 100.0,
            },
            {
                "id": 2,
                "kind": "tool",
                "data": {"name": "edit_file", "args": "stats_utils.py"},
                "t": "12:00:01",
                "ts": 101.0,
            },
            {
                "id": 3,
                "kind": "pytest",
                "data": {"phase": "after", "exit": 0},
                "t": "12:00:02",
                "ts": 102.0,
            },
        ],
    }


def test_render_gif_from_fixture(tmp_path):
    out = render_gif(_recording(), tmp_path / "demo.gif", width=480)
    assert out.exists() and out.stat().st_size > 0
    from PIL import Image

    with Image.open(out) as im:
        assert im.n_frames >= 3


def test_render_gif_cjk_events(tmp_path):
    """Recorded events contain Chinese text — rendering must not crash (glyph fallback ok)."""
    out = render_gif(_recording(), tmp_path / "cjk.gif", width=480)
    assert out.exists()


def test_render_gif_rejects_bad_format(tmp_path):
    with pytest.raises(ValueError):
        render_gif({"format": 2, "events": []}, tmp_path / "bad.gif")


def test_social_preview_dimensions(tmp_path):
    out = render_social_preview(
        tmp_path / "preview.png",
        title="Antigravity-Gemma-ROCm",
        tagline="A fully local AI coding agent on Windows",
        chips=["ROCm", "Gemma", "Ollama", "Ryzen AI MAX", "Windows"],
    )
    from PIL import Image

    with Image.open(out) as im:
        assert im.size == (1280, 640)
