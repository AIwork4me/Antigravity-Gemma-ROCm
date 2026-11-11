"""Scenario integrity tests for the DevFest "The Last Seat" workspace.

Guards the frozen defect baseline: the pristine workspace MUST contain a
real, reproducible off-by-one at the last seat (exactly one failing test),
and the runtime mission must NOT leak the defect or its fix to the agent.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import devfest_agent  # noqa: E402

PRISTINE = ROOT / "devfest_workspace_pristine"


def _load_engine(workspace: Path):
    spec = importlib.util.spec_from_file_location("checkin_under_test", workspace / "checkin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def engine():
    return _load_engine(PRISTINE)


class TestPristineDefectBaseline:
    def test_pristine_pytest_is_red_exactly_on_last_seat(self):
        """The frozen suite in pristine must fail on EXACTLY the last-seat
        test — a green pristine (leaked fix) or extra failures break the demo."""
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"],
            cwd=PRISTINE,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        assert isinstance(proc.returncode, int)
        assert proc.returncode == 1, f"expected exactly 1 failing test, got rc={proc.returncode}"
        assert "test_last_seat_is_admitted" in proc.stdout
        assert proc.stdout.strip().splitlines()[-1].startswith("1 failed")

    def test_engine_rejects_the_last_seat_at_39(self, engine):
        """The defect is REAL and reachable: 39/40 taken -> seat 40 refused."""
        with pytest.raises(engine.RegistrationError):
            engine.register("Last Dev", 39)

    def test_engine_admits_normal_registrations(self, engine):
        assert engine.register("Ada L.", 10) == {"name": "Ada L.", "seat": 11}

    def test_engine_rejects_genuinely_full_room(self, engine):
        with pytest.raises(engine.RegistrationError):
            engine.register("Overflow", 40)

    def test_defect_is_an_off_by_one_not_a_capacity_change(self, engine):
        """The engine still knows 1 seat remains at 39 — only register() is
        wrong, so the fix must be a boundary change, not a feature rebuild."""
        assert engine.seats_remaining(39) == 1
        assert engine.CAPACITY == 40


class TestMissionSecrecy:
    LEAK_TERMS = (
        "off-by-one",
        "off by one",
        "off_by_one",
        "capacity - 1",
        "capacity-1",
        ">= capacity",
        "boundary condition in register",
        "change the comparison",
        "the bug is in",
        "fix the condition",
    )

    def test_mission_does_not_name_defect_or_fix(self):
        low = devfest_agent.MISSION.lower()
        for term in self.LEAK_TERMS:
            assert term not in low, f"mission leaks the defect/fix: {term!r}"

    def test_system_instructions_do_not_name_defect_or_fix(self):
        low = devfest_agent.SYSTEM_INSTRUCTIONS.lower()
        for term in self.LEAK_TERMS:
            assert term not in low, f"system prompt leaks the defect/fix: {term!r}"

    def test_pristine_source_has_no_planted_bug_marker(self):
        """Unlike the median demo, the defect must look like ordinary code —
        no 'BUG (planted)' comment that hands the agent the answer."""
        src = (PRISTINE / "checkin.py").read_text(encoding="utf-8")
        for marker in ("BUG", "planted", "FIXME", "TODO", "XXX"):
            assert marker not in src, f"defect marked in source: {marker}"


class TestFrozenTestsTamperEvident:
    def test_mutable_allowlist_only_covers_source(self):
        assert devfest_agent.MUTABLE_ALLOWLIST == ("checkin.py",)

    def test_tests_dir_is_not_in_allowlist(self):
        assert not any(
            f.startswith("tests/") or f.startswith("tests\\")
            for f in devfest_agent.MUTABLE_ALLOWLIST
        )

    def test_scenario_files_exist_and_small(self):
        for rel in ("checkin.py", "tests/test_checkin.py", "README.md"):
            p = PRISTINE / rel
            assert p.is_file(), f"missing scenario file {rel}"
            assert p.stat().st_size < 100_000

    def test_readme_names_39_fictional_attendees(self):
        text = (PRISTINE / "README.md").read_text(encoding="utf-8")
        assert "39 fictional developers" in text
        assert "fictional" in text.lower()
