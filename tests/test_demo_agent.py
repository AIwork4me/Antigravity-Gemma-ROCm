"""Tests for src/demo_agent.py helpers that must work WITHOUT a live model."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import demo_agent  # noqa: E402


def _mkjunction(link: Path, target: Path) -> bool:
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode == 0


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


class TestJunctionSafeRestore:
    def _setup(self, tmp_path, monkeypatch):
        pristine = tmp_path / "pristine"
        pristine.mkdir()
        (pristine / "stats_utils.py").write_text("BUG\n", encoding="utf-8")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "stats_utils.py").write_text("OLD\n", encoding="utf-8")
        monkeypatch.setattr(demo_agent, "DEMO_WORKSPACE", ws)
        monkeypatch.setattr(demo_agent, "PRISTINE", pristine)
        return ws, pristine

    def test_junction_inside_workspace_target_survives(self, tmp_path, monkeypatch):
        """A planted junction pointing OUTSIDE must be removed as a link only;
        the target directory (and its sentinel file) must survive."""
        ws, _ = self._setup(tmp_path, monkeypatch)
        victim = tmp_path / "victim"
        victim.mkdir()
        sentinel = victim / "keepme.txt"
        sentinel.write_text("SENTINEL", encoding="utf-8")
        link = ws / "escape"
        assert _mkjunction(link, victim), "junction creation failed"

        demo_agent.restore_bug_baseline()

        assert not link.exists()  # the link is gone
        assert sentinel.exists()  # the target was NOT deleted through the link
        assert (ws / "stats_utils.py").read_text(encoding="utf-8") == "BUG\n"

    def test_nested_junction_target_survives(self, tmp_path, monkeypatch):
        ws, _ = self._setup(tmp_path, monkeypatch)
        victim = tmp_path / "victim2"
        victim.mkdir()
        sentinel = victim / "keepme2.txt"
        sentinel.write_text("SENTINEL2", encoding="utf-8")
        (ws / "sub").mkdir()
        assert _mkjunction(ws / "sub" / "escape", victim)

        demo_agent.restore_bug_baseline()

        assert sentinel.exists()
        assert not (ws / "sub").exists()

    def test_workspace_itself_junction_refused(self, tmp_path, monkeypatch):
        """If the workspace root is a reparse point, restore must refuse."""
        pristine = tmp_path / "pristine"
        pristine.mkdir()
        (pristine / "stats_utils.py").write_text("BUG\n", encoding="utf-8")
        real_ws = tmp_path / "real_ws"
        real_ws.mkdir()
        ws_link = tmp_path / "ws_link"
        assert _mkjunction(ws_link, real_ws)
        monkeypatch.setattr(demo_agent, "DEMO_WORKSPACE", ws_link)
        monkeypatch.setattr(demo_agent, "PRISTINE", pristine)
        with pytest.raises(RuntimeError, match="junction"):
            demo_agent.restore_bug_baseline()
        assert ws_link.exists()  # link untouched, refusal happened first

    def test_readonly_file_restored_anyway(self, tmp_path, monkeypatch):
        """Read-only leftovers must not abort restore half-way (review F-8)."""
        ws, _ = self._setup(tmp_path, monkeypatch)
        locked = ws / "locked.log"
        locked.write_text("OLD\n", encoding="utf-8")
        os.chmod(locked, stat.S_IREAD)
        try:
            demo_agent.restore_bug_baseline()
            assert (ws / "stats_utils.py").read_text(encoding="utf-8") == "BUG\n"
            assert not locked.exists()
        finally:
            if locked.exists():
                os.chmod(locked, stat.S_IWRITE)
