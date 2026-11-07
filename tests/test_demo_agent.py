"""Tests for src/demo_agent.py helpers that must work WITHOUT a live model."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import demo_agent  # noqa: E402


def test_restore_bug_baseline_creates_missing_workspace(tmp_path, monkeypatch):
    """Fresh clones have no demo_workspace at all — restore must create it, not traceback."""
    pristine = tmp_path / "pristine"
    (pristine / "tests").mkdir(parents=True)
    (pristine / "stats_utils.py").write_text("def median(xs):\n    pass\n", encoding="utf-8")
    (pristine / "tests" / "test_stats.py").write_text(
        "def test_noop():\n    pass\n", encoding="utf-8"
    )
    ws = tmp_path / "ws"  # absent — fresh-clone condition
    monkeypatch.setattr(demo_agent, "DEMO_WORKSPACE", ws)
    monkeypatch.setattr(demo_agent, "PRISTINE", pristine)

    demo_agent.restore_bug_baseline()

    assert (ws / "stats_utils.py").read_text(encoding="utf-8").startswith("def median")
    assert (ws / "tests" / "test_stats.py").exists()
