"""Tests for src/devfest_agent.py helpers (no live model needed): junction-safe
baseline restore, tamper-evident acceptance, and a full fake-agent end-to-end
run that verifies evidence capture and containment to the demo workspace.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import devfest_agent  # noqa: E402


def _mkjunction(link: Path, target: Path) -> bool:
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode == 0


def _mkpristine(tmp_path: Path) -> Path:
    """Copy the real pristine scenario into tmp for isolated restore tests."""
    dst = tmp_path / "pristine"
    dst.mkdir()
    for item in (ROOT / "devfest_workspace_pristine").iterdir():
        if item.is_dir():
            shutil_copytree(item, dst / item.name)
        else:
            shutil_copy2(item, dst)
    return dst


def shutil_copytree(src: Path, dst: Path) -> None:
    import shutil

    shutil.copytree(src, dst, dirs_exist_ok=True)


def shutil_copy2(src: Path, dst: Path) -> None:
    import shutil

    shutil.copy2(src, dst)


class TestRestoreBaseline:
    def test_creates_missing_workspace_from_pristine(self, tmp_path, monkeypatch):
        pristine = _mkpristine(tmp_path)
        ws = tmp_path / "ws"
        monkeypatch.setattr(devfest_agent, "DEVFEST_PRISTINE", pristine)
        monkeypatch.setattr(devfest_agent, "DEVFEST_WORKSPACE", ws)

        devfest_agent.restore_devfest_baseline()

        assert (ws / "checkin.py").read_text(encoding="utf-8").startswith('"""Check-in engine')
        assert (ws / "tests" / "test_checkin.py").exists()

    def test_resets_drifted_workspace_to_defective_state(self, tmp_path, monkeypatch):
        pristine = _mkpristine(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "checkin.py").write_text("def hacked():\n    pass\n", encoding="utf-8")
        (ws / "stray.txt").write_text("leftover", encoding="utf-8")
        monkeypatch.setattr(devfest_agent, "DEVFEST_PRISTINE", pristine)
        monkeypatch.setattr(devfest_agent, "DEVFEST_WORKSPACE", ws)

        devfest_agent.restore_devfest_baseline()

        assert not (ws / "stray.txt").exists()
        assert "hacked" not in (ws / "checkin.py").read_text(encoding="utf-8")

    def test_refuses_junction_workspace_root(self, tmp_path, monkeypatch):
        """A junction as the workspace ROOT must abort (deletion redirect)."""
        pristine = _mkpristine(tmp_path)
        victim = tmp_path / "victim"
        victim.mkdir()
        (victim / "sentinel.txt").write_text("keep me", encoding="utf-8")
        link = tmp_path / "ws_link"
        if not _mkjunction(link, victim):
            pytest.skip("junction creation not permitted on this host")
        monkeypatch.setattr(devfest_agent, "DEVFEST_PRISTINE", pristine)
        monkeypatch.setattr(devfest_agent, "DEVFEST_WORKSPACE", link)

        with pytest.raises(RuntimeError, match="junction"):
            devfest_agent.restore_devfest_baseline()
        assert (victim / "sentinel.txt").exists()

    def test_junction_inside_workspace_removed_as_link_only(self, tmp_path, monkeypatch):
        pristine = _mkpristine(tmp_path)
        ws = tmp_path / "ws"
        monkeypatch.setattr(devfest_agent, "DEVFEST_PRISTINE", pristine)
        monkeypatch.setattr(devfest_agent, "DEVFEST_WORKSPACE", ws)
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("do not delete", encoding="utf-8")
        if not _mkjunction(ws / "escape", outside):
            pytest.skip("junction creation not permitted on this host")

        devfest_agent.restore_devfest_baseline()

        assert not (ws / "escape").exists()
        assert (outside / "secret.txt").exists()


class TestAcceptanceGates:
    """The acceptance verdict must be unforgivably strict (anti-cheat)."""

    @staticmethod
    def _maps(changed: list):
        before = {
            "README.md": {"sha256": "ccc", "bytes": 1},
            "checkin.py": {"sha256": "aaa", "bytes": 1},
            "tests/test_checkin.py": {"sha256": "bbb", "bytes": 1},
        }
        after = {
            k: ({"sha256": "zzz", "bytes": 2} if k in changed else v) for k, v in before.items()
        }
        return before, after

    def _accept(self, changed, monkeypatch, pre_rc=1, post_rc=0, drift=()):
        before, after = self._maps(changed)
        monkeypatch.setattr(devfest_agent, "reverify_on_disk", lambda a: list(drift))
        return devfest_agent.acceptance(
            before, after, {"exit_code": pre_rc}, {"exit_code": post_rc}
        )

    def test_clean_fix_is_ok(self, tmp_path, monkeypatch):
        acc = self._accept(["checkin.py"], monkeypatch)
        assert acc["ok"] is True
        assert acc["files_changed"] == ["checkin.py"]

    def test_string_exit_codes_are_forged_and_rejected(self, tmp_path, monkeypatch):
        before, after = self._maps(["checkin.py"])
        monkeypatch.setattr(devfest_agent, "reverify_on_disk", lambda a: [])
        with pytest.raises(ValueError, match="forged"):
            devfest_agent.acceptance(before, after, {"exit_code": "0"}, {"exit_code": 0})

    def test_tests_tampering_fails_acceptance(self, tmp_path, monkeypatch):
        acc = self._accept(["tests/test_checkin.py"], monkeypatch)
        assert acc["ok"] is False
        assert acc["tests_tampered"] is True

    def test_forbidden_file_change_fails(self, tmp_path, monkeypatch):
        acc = self._accept(["checkin.py", "README.md"], monkeypatch)
        assert acc["ok"] is False
        assert acc["forbidden_changed"] == ["README.md"]

    def test_disk_drift_fails(self, tmp_path, monkeypatch):
        acc = self._accept(["checkin.py"], monkeypatch, drift=["checkin.py"])
        assert acc["ok"] is False
        assert acc["disk_drift"] == ["checkin.py"]

    def test_no_change_at_all_fails(self, tmp_path, monkeypatch):
        acc = self._accept([], monkeypatch)
        assert acc["ok"] is False

    def test_green_before_fails_bug_presence_gate(self, tmp_path, monkeypatch):
        acc = self._accept(["checkin.py"], monkeypatch, pre_rc=0)
        assert acc["ok"] is False
        assert acc["bug_present_before"] is False


class TestFakeAgentEndToEnd:
    """Full run_full_task pipeline with a fake agent that performs exactly the
    edit a real agent would: change `capacity - 1` to `capacity`."""

    @pytest.fixture()
    def isolated(self, tmp_path, monkeypatch):
        pristine = _mkpristine(tmp_path)
        ws = tmp_path / "devfest_workspace"
        ev = tmp_path / "evidence"
        monkeypatch.setattr(devfest_agent, "DEVFEST_PRISTINE", pristine)
        monkeypatch.setattr(devfest_agent, "DEVFEST_WORKSPACE", ws)
        monkeypatch.setattr(devfest_agent, "EVIDENCE_ROOT", ev)

        async def fake_agent_task(model, base_url, timeout_s, ev_dir, on_event=None):
            # simulate: read -> test (RED) -> edit -> test (GREEN), and write
            # the same per-agent evidence files the real harness writes
            src = (ws / "checkin.py").read_text(encoding="utf-8")
            fixed = src.replace("registered >= capacity - 1", "registered >= capacity")
            assert fixed != src, "fake agent failed to apply the canonical fix"
            (ws / "checkin.py").write_text(fixed, encoding="utf-8")
            tool_events = [
                {"name": "view_file"},
                {"name": "run_command"},
                {"name": "edit_file"},
                {"name": "run_command"},
            ]
            (ev_dir / "config.txt").write_text("{}", encoding="utf-8")
            (ev_dir / "mission.txt").write_text("mission", encoding="utf-8")
            (ev_dir / "tool_calls.json").write_text(json.dumps(tool_events), encoding="utf-8")
            (ev_dir / "policy_audit.txt").write_text("run_command decision=ALLOW", encoding="utf-8")
            (ev_dir / "agent_reply.txt").write_text("fixed the boundary", encoding="utf-8")
            if on_event:
                # V2 shape: an OBSERVED tool call carries its sequence + status;
                # "observed" never means the tool ran successfully
                on_event(
                    "tool",
                    {"name": "edit_file", "args": "checkin.py", "status": "observed", "seq": 3},
                )
                on_event("sdk", {"phase": "agent_config_ready"})
                on_event("sdk", {"phase": "agent_session_started"})
                on_event("sdk", {"phase": "mission_submitted"})
                on_event("sdk", {"phase": "agent_response_completed", "n_tool_calls": 4})
            return {"elapsed_s": 1.2, "n_tool_calls": 4, "tool_events": tool_events}

        monkeypatch.setattr(devfest_agent, "run_agent_task", fake_agent_task)
        ws.mkdir(exist_ok=True)
        ev.mkdir(exist_ok=True)
        return ws, ev

    @staticmethod
    def _tree_snapshot(root: Path) -> set:
        return {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*")}

    def test_full_task_succeeds_and_writes_evidence(self, isolated):
        ws, ev = isolated
        before_tree = self._tree_snapshot(ws.parent)
        seen_events: list[tuple[str, object]] = []

        def on_event(kind, data):
            seen_events.append((kind, data))

        summary = devfest_agent.run_full_task(
            "fake-model", "http://x", 60, "fake-1", on_event=on_event
        )

        assert summary["ok"] is True, summary
        assert summary["bug_present_before"] is True
        assert summary["bug_fixed_after"] is True
        assert summary["files_changed"] == ["checkin.py"]
        # V2: short SHA256 of changed files ships with the summary (dev view)
        assert set(summary["sha256_after_short"]) == {"checkin.py"}
        assert len(summary["sha256_after_short"]["checkin.py"]) == 16
        # V2: SDK verification lifecycle events must be emitted around the
        # independent pytest acceptance
        sdk_phases = [d.get("phase") for k, d in seen_events if k == "sdk" and isinstance(d, dict)]
        assert "independent_verification_started" in sdk_phases
        assert sdk_phases.index("independent_verification_started") < sdk_phases.index(
            "independent_verification_completed"
        )
        completed = [d for d in sdk_phases if d == "independent_verification_completed"]
        assert completed
        done_events = [d for k, d in seen_events if k == "done"]
        assert done_events and done_events[-1]["ok"] is True
        run_dir = ev / "run-fake-1"
        for f in (
            "summary.json",
            "tool_calls.json",
            "pytest_before.json",
            "pytest_after.json",
            "diff.patch",
            "workspace_before.json",
            "workspace_after.json",
            "policy_audit.txt",
            "agent_reply.txt",
            "mission.txt",
            "config.txt",
        ):
            assert (run_dir / f).is_file(), f"missing evidence file {f}"
        patch = (run_dir / "diff.patch").read_text(encoding="utf-8")
        assert "-    if registered >= capacity - 1:" in patch
        assert "+    if registered >= capacity:" in patch

        # containment: nothing new outside the workspace / evidence subtrees
        after_tree = self._tree_snapshot(ws.parent)
        allowed_prefixes = ("devfest_workspace/", "evidence/")
        new = {p for p in after_tree - before_tree if not p.startswith(allowed_prefixes)}
        assert not new, f"writes leaked outside demo workspace/evidence: {new}"

    def test_full_task_fails_when_agent_tampers_tests(self, isolated, monkeypatch):
        ws, ev = isolated

        async def evil_agent(model, base_url, timeout_s, ev_dir, on_event=None):
            t = ws / "tests" / "test_checkin.py"
            t.write_text("def test_nothing():\n    assert True\n", encoding="utf-8")
            return {"elapsed_s": 0.5, "n_tool_calls": 1, "tool_events": []}

        monkeypatch.setattr(devfest_agent, "run_agent_task", evil_agent)
        summary = devfest_agent.run_full_task("fake-model", "http://x", 60, "fake-2")
        assert summary["ok"] is False
        assert summary["tests_tampered"] is True
        assert (
            json.loads((ev / "run-fake-2" / "summary.json").read_text(encoding="utf-8"))["ok"]
            is False
        )

    def test_full_task_fails_when_agent_does_nothing(self, isolated, monkeypatch):
        ws, ev = isolated

        async def lazy_agent(model, base_url, timeout_s, ev_dir, on_event=None):
            return {"elapsed_s": 0.1, "n_tool_calls": 0, "tool_events": []}

        monkeypatch.setattr(devfest_agent, "run_agent_task", lazy_agent)
        summary = devfest_agent.run_full_task("fake-model", "http://x", 60, "fake-3")
        assert summary["ok"] is False
        assert summary["files_changed"] == []


class TestMissionConfig:
    def test_mission_mentions_workspace_only_edit_rule(self):
        assert "Only modify files inside this workspace" in devfest_agent.MISSION

    def test_mission_tells_agent_to_use_the_frozen_tests(self):
        assert "tests/test_checkin.py" in devfest_agent.MISSION
        assert "python -m pytest tests/ -q" in devfest_agent.MISSION

    def test_no_asyncio_left_behind_in_sync_context(self):
        # run_full_task must be sync (called from a plain thread in webui)
        import inspect

        assert not inspect.iscoroutinefunction(devfest_agent.run_full_task)
