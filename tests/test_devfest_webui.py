"""Security and behavior tests for src/devfest_webui.py V2 (no Ollama needed).

Stage-UI gate coverage:
  - response headers, same-origin + DNS-rebinding gate on ALL POST endpoints
  - replay vs live vs design-preview separation (modes never mislabeled; a
    finished replay KEEPS its RECORDED RUN identity — V2 fix)
  - XSS: name field bounded/escaped, no innerHTML with untrusted event data
  - race/busy gates (409) for run/reset/register
  - workspace reset safety (refuses while busy; junction-safe restore reused)
  - admission engine behavior derived from the REAL scenario code via probe
  - GPU honesty: 'ROCm VERIFIED' only with live DLL+residency evidence; the
    /api/ps size number is labeled as accounting, never model VRAM usage
  - six-act story state machine: monotonic, event-driven, never celebrates
    without acceptance+probe (V2 premature-celebration fix)
  - branding: Google DevFest 2026 with Google letter colors, no city suffix
"""

import asyncio
import json
import sys
import threading
import time as _time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import devfest_webui as dw  # noqa: E402


@pytest.fixture()
def fresh_state():
    with dw._lock:
        dw._events.clear()
        dw._state.update(
            running=False,
            replay=False,
            preview=False,
            mode="live",
            run_id=None,
            started_at=None,
            acceptance_ok=None,
            last_probe_admitted=None,
        )
    dw._seq["n"] = 0
    dw._recorder = None
    dw._recorder_path = None
    dw._story = dw.StoryTracker()
    yield
    with dw._lock:
        dw._events.clear()
        dw._state.update(
            running=False,
            replay=False,
            preview=False,
            mode="live",
            run_id=None,
            started_at=None,
            acceptance_ok=None,
            last_probe_admitted=None,
        )
    dw._seq["n"] = 0


class _FakeRequest:
    def __init__(self, headers: dict, body: dict | None = None):
        self.headers = {k.lower(): v for k, v in headers.items()}
        self._body = body or {}

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


GOOD = {"host": "127.0.0.1:8421", "origin": "http://127.0.0.1:8421"}


# ------------------------------------------------ origin / DNS rebinding gate
class TestOriginGate:
    @pytest.mark.parametrize("endpoint", ["api_run", "api_reset", "api_register"])
    @pytest.mark.parametrize(
        "headers",
        [
            {"host": "evil.example:8421", "origin": "http://evil.example"},  # rebinding
            {"host": "127.0.0.1:8421", "origin": "http://evil.example"},  # CSRF
            {"host": "127.0.0.1:8421", "origin": "http://127.0.0.1:9999"},  # diff port
            {"host": "10.0.0.1:8421"},  # non-loopback Host only
        ],
    )
    def test_forbidden_on_all_posts(self, endpoint, headers, fresh_state, monkeypatch):
        monkeypatch.setattr(dw, "probe_register", lambda n, s="last": {"admitted": False})
        resp = asyncio.run(getattr(dw, endpoint)(_FakeRequest(headers)))
        assert resp.status_code == 403

    @pytest.mark.parametrize("endpoint", ["api_run", "api_reset", "api_register"])
    def test_same_origin_allowed(self, endpoint, fresh_state, monkeypatch):
        called = {}

        def fake_run():
            called["run"] = True
            return True, ""

        def fake_restore():
            called["restore"] = True

        monkeypatch.setattr(dw, "start_run", fake_run)
        monkeypatch.setattr(dw, "restore_devfest_baseline", fake_restore)

        def recording_probe(n, s="last"):
            called["probe_reg"] = True
            return {"admitted": True, "seat": 40}

        monkeypatch.setattr(dw, "probe_register", recording_probe)
        resp = asyncio.run(getattr(dw, endpoint)(_FakeRequest(GOOD, body={"name": "x"})))
        assert resp.status_code == 200
        assert called


class TestHeaders:
    def test_security_headers(self):
        resp = asyncio.run(dw.homepage(_FakeRequest({})))
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["referrer-policy"] == "no-referrer"

    def test_page_declares_utf8_and_title(self):
        resp = asyncio.run(dw.homepage(_FakeRequest({})))
        assert "charset=utf-8" in resp.media_type
        assert b"Google DevFest 2026" in resp.body


# ------------------------------------------------------------ busy/race gates
class TestBusyGates:
    def test_run_409_when_running(self, fresh_state):
        with dw._lock:
            dw._state.update(running=True)
        resp = asyncio.run(dw.api_run(_FakeRequest(GOOD)))
        assert resp.status_code == 409

    def test_run_409_when_replaying(self, fresh_state):
        with dw._lock:
            dw._state.update(replay=True)
        resp = asyncio.run(dw.api_run(_FakeRequest(GOOD)))
        assert resp.status_code == 409
        assert "replay" in json.loads(resp.body)["error"]

    def test_reset_409_when_running(self, fresh_state):
        with dw._lock:
            dw._state.update(running=True)
        resp = asyncio.run(dw.api_reset(_FakeRequest(GOOD)))
        assert resp.status_code == 409

    def test_reset_refuses_during_preview(self, fresh_state):
        with dw._lock:
            dw._state.update(preview=True)
        resp = asyncio.run(dw.api_reset(_FakeRequest(GOOD)))
        assert resp.status_code == 409

    def test_live_run_completion_releases_busy_gate(self, fresh_state, monkeypatch):
        """Regression: the live-run wrapper MUST clear _state['running'] when it
        finishes, or the dashboard stays busy forever after one run."""

        def fake_full_task(model, base_url, timeout_s, run_id, on_event=None, keep_workspace=False):
            on_event("tool", {"name": "view_file", "status": "observed"})
            on_event("tool", {"name": "edit_file", "status": "observed"})
            on_event("sdk", {"phase": "independent_verification_started"})
            on_event("done", {"ok": True})
            return {"ok": True, "files_changed": ["checkin.py"], "elapsed_s": 1}

        dumped = threading.Event()
        monkeypatch.setattr(dw, "run_full_task", fake_full_task)
        monkeypatch.setattr(
            dw,
            "gpu_evidence",
            lambda *a, **k: {"backend": "x", "resident": False, "gpu_name": None},
        )
        monkeypatch.setattr(
            dw, "probe_register", lambda n, s="last": {"admitted": True, "seat": 40}
        )
        monkeypatch.setattr(dw, "dump_recording", lambda: dumped.set())
        ok, why = dw.start_run()
        assert ok, why
        assert dumped.wait(10), "live-run wrapper never finished"
        # dump_recording runs AFTER running=False inside the same finally block,
        # so by the time it fired the busy gate must already be released
        with dw._lock:
            assert dw._state["running"] is False
        # V2: a successful run records acceptance and emits a VERIFIED final
        # seat event (the only seat event allowed to celebrate)
        with dw._lock:
            assert dw._state["acceptance_ok"] is True
            seats = [e["data"] for e in dw._events if e["kind"] == "seat"]
        assert seats and seats[-1]["verified"] is True
        assert dw._story.stage == "celebration"
        assert any(e["kind"] == "story" and e["data"]["stage"] == "celebration" for e in dw._events)

    def test_failed_run_never_verifies_seat(self, fresh_state, monkeypatch):
        def fake_full_task(model, base_url, timeout_s, run_id, on_event=None, keep_workspace=False):
            on_event("done", {"ok": False, "tests_tampered": False})
            return {"ok": False, "files_changed": []}

        done = threading.Event()
        monkeypatch.setattr(dw, "run_full_task", fake_full_task)
        monkeypatch.setattr(
            dw,
            "gpu_evidence",
            lambda *a, **k: {"backend": "x", "resident": False, "gpu_name": None},
        )

        def probe_boom(n, s="last"):
            raise AssertionError("failed run must not run the business probe")

        monkeypatch.setattr(dw, "probe_register", probe_boom)
        monkeypatch.setattr(dw, "dump_recording", lambda: done.set())
        ok, _ = dw.start_run()
        assert ok
        assert done.wait(10)
        with dw._lock:
            assert dw._state["acceptance_ok"] is False
        assert dw._story.stage == "failed"

    def test_register_still_allowed_while_running(self, fresh_state, monkeypatch):
        """Register is a read-only probe: it must stay available during a run."""
        with dw._lock:
            dw._state.update(running=True)
        monkeypatch.setattr(dw, "probe_register", lambda n, s="last": {"admitted": False})
        resp = asyncio.run(dw.api_register(_FakeRequest(GOOD, body={"name": "y"})))
        assert resp.status_code == 200


# ----------------------------------------------------- mode separation & honesty
class TestModeSeparation:
    def test_replay_loader_accepts_devfest_kinds(self, tmp_path):
        p = tmp_path / "rec.json"
        p.write_text(
            json.dumps(
                {
                    "format": 1,
                    "meta": {"run_id": "r1"},
                    "events": [
                        {"kind": "seat", "data": {"probe": {"admitted": True}}, "ts": 1.0},
                        {"kind": "gpu", "data": {"backend": "ROCm VERIFIED"}, "ts": 1.5},
                        {"kind": "sdk", "data": {"phase": "agent_session_started"}, "ts": 1.6},
                        {"kind": "story", "data": {"stage": "mission"}, "ts": 1.7},
                    ],
                }
            ),
            encoding="utf-8",
        )
        rec = dw.load_replay(p)
        assert len(rec["events"]) == 4

    def test_replay_loader_rejects_unknown_kind(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(
            json.dumps({"format": 1, "meta": {}, "events": [{"kind": "alert", "data": "x"}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="kind"):
            dw.load_replay(p)

    def test_replay_speed_clamped(self):
        assert dw.clamp_speed(0.0) == 0.1
        assert dw.clamp_speed(1e6) == 100.0

    def test_start_replay_blocked_while_running(self, fresh_state):
        with dw._lock:
            dw._state.update(running=True)
        assert dw.start_replay({"events": [], "meta": {}}) is False

    def _wait_not_running(self, timeout=8):
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            with dw._lock:
                if not dw._state["running"]:
                    return True
            _time.sleep(0.05)
        return False

    def test_replay_keeps_identity_after_done(self, fresh_state):
        """V2 P0-A: after replay finishes the mode STAYS 'replay' — the page
        never re-badges a finished recording as LIVE."""
        events = [
            {"kind": "stage", "data": {"x": 1}, "ts": 0.0},
            {"kind": "replay_done", "data": {}, "ts": 0.1},
        ]
        assert dw.start_replay({"events": events, "meta": {"run_id": "r9"}}, 100) is True
        assert self._wait_not_running()
        with dw._lock:
            assert dw._state["replay"] is False
            assert dw._state["running"] is False
            assert dw._state["mode"] == "replay"  # identity persists (V2 fix)

    def test_new_live_run_restores_live_identity(self, fresh_state, monkeypatch):
        with dw._lock:
            dw._state.update(mode="replay")  # page sitting on a finished replay
        monkeypatch.setattr(
            dw,
            "run_full_task",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("thread internals tested elsewhere")
            ),
        )

        # patch run_task_thread to avoid spawning the real pipeline
        monkeypatch.setattr(dw, "run_task_thread", lambda rid: None)
        ok, why = dw.start_run()
        assert ok, why
        with dw._lock:
            assert dw._state["mode"] == "live"  # only a NEW real run re-badges LIVE

    def test_replay_never_runs_real_pipeline(self, fresh_state, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("replay must never start a real agent run")

        monkeypatch.setattr(dw, "run_full_task", boom)
        monkeypatch.setattr(dw, "restore_devfest_baseline", boom)
        events = [{"kind": "stage", "data": {"x": 1}, "ts": 0.0}]
        assert dw.start_replay({"events": events, "meta": {}}, 100) is True
        assert self._wait_not_running()


class TestPreviewHonesty:
    def test_preview_never_touches_workspace_or_evidence(self, fresh_state, monkeypatch):
        """DESIGN PREVIEW must not write anywhere: restore/evidence are booby-trapped."""

        def boom(*a, **k):  # noqa: ANN001
            raise AssertionError("preview must not reset/write the workspace")

        monkeypatch.setattr(dw, "restore_devfest_baseline", boom)
        monkeypatch.setattr(dw, "run_full_task", boom)
        ok, why = dw.start_preview()
        assert ok
        # run the synthetic timeline to completion fast
        deadline = _time.time() + 30
        while _time.time() < deadline:
            with dw._lock:
                done = any(e["kind"] == "done" for e in dw._events)
            if done:
                break
            _time.sleep(0.2)
        assert done, "preview timeline did not complete"
        with dw._lock:
            dones = [e["data"] for e in dw._events if e["kind"] == "done"]
        assert dones and dones[-1]["ok"] is None  # never ok:true
        assert dones[-1]["preview"] is True
        # the synthetic celebration in preview is labeled as preview
        stories = [e["data"] for e in dw._events if e["kind"] == "story"]
        assert any(s.get("stage") == "celebration" and s.get("preview") for s in stories)

    def test_preview_mode_flag_routes_run_to_preview(self, fresh_state, monkeypatch):
        monkeypatch.setattr(dw, "_PREVIEW_ONLY", True)
        started = {}

        def fake_preview():
            started["yes"] = True
            return True, ""

        monkeypatch.setattr(dw, "start_preview", fake_preview)
        resp = asyncio.run(dw.api_run(_FakeRequest(GOOD)))
        assert resp.status_code == 200
        assert started.get("yes") is True


# ------------------------------------------------------- admission via probe
class TestAdmissionEngine:
    def test_register_runs_real_probe_and_emits_seat(self, fresh_state, monkeypatch):
        seen = {}

        def fake_probe(name, scenario="last"):
            seen["name"], seen["scenario"] = name, scenario
            return {
                "admitted": False,
                "error_type": "RegistrationError",
                "error_text": "workshop is at full capacity (39/40 taken)",
            }

        monkeypatch.setattr(dw, "probe_register", fake_probe)
        resp = asyncio.run(dw.api_register(_FakeRequest(GOOD, body={"name": "Ada L."})))
        body = json.loads(resp.body)
        assert body["probe"]["admitted"] is False
        assert body["verified"] is None  # never verified on a rejection
        assert seen["name"] == "Ada L."
        with dw._lock:
            seats = [e for e in dw._events if e["kind"] == "seat"]
        assert seats and seats[-1]["data"]["probe"]["admitted"] is False

    def test_register_bounded_name_never_reaches_model(self, fresh_state, monkeypatch):
        """The name is bounded/sanitized and only ever used for the probe/UI —
        the mission is static text, so nothing user-controlled reaches the model."""
        captured = {}

        def fake_probe(name, scenario="last"):
            captured["len"] = len(name)
            return {"admitted": False}

        monkeypatch.setattr(dw, "probe_register", fake_probe)
        long_name = "Z" * 500 + "\x00\x1b[31m"
        resp = asyncio.run(dw.api_register(_FakeRequest(GOOD, body={"name": long_name})))
        assert json.loads(resp.body)["name"] == "Z" * 24
        assert captured["len"] == 24

    def test_sanitize_name_rules(self):
        assert dw.sanitize_name(None) == "Guest Developer"
        assert dw.sanitize_name(123) == "Guest Developer"
        assert dw.sanitize_name("   ") == "Guest Developer"
        assert len(dw.sanitize_name("Z" * 99)) == 24
        assert dw.sanitize_name("\x00\x01bad\x7fname") == "badname"

    def test_real_probe_against_fixed_workspace(self, tmp_path):
        """The probe derives state from ACTUAL code: buggy -> rejected,
        fixed -> admitted. No hardcoded admission in the UI path."""
        import shutil

        from devfest_probe import probe as direct_probe

        ws = tmp_path / "ws"
        shutil.copytree(ROOT / "devfest_workspace_pristine", ws)
        assert direct_probe(ws, "last", "Dev 40")["admitted"] is False
        src = (ws / "checkin.py").read_text(encoding="utf-8")
        (ws / "checkin.py").write_text(
            src.replace("registered >= capacity - 1", "registered >= capacity"), encoding="utf-8"
        )
        fixed = direct_probe(ws, "last", "Dev 40")
        assert fixed["admitted"] is True
        assert fixed["seat"] == 40


# ------------------------------------------- V2: premature celebration gates
class TestPrematureCelebrationGates:
    """P0-B: /api/register must NEVER be able to trigger the celebration while
    an agent run is active, and a passing probe alone never counts as success."""

    def test_register_during_run_is_never_verified(self, fresh_state, monkeypatch):
        with dw._lock:
            dw._state.update(running=True, mode="live")
        monkeypatch.setattr(
            dw, "probe_register", lambda n, s="last": {"admitted": True, "seat": 40}
        )
        resp = asyncio.run(dw.api_register(_FakeRequest(GOOD, body={"name": "Mid Run"})))
        body = json.loads(resp.body)
        assert body["probe"]["admitted"] is True
        assert body["verified"] is False  # mid-run probe: candidate, not success
        assert body["run_active"] is True
        with dw._lock:
            seats = [e["data"] for e in dw._events if e["kind"] == "seat"]
        assert seats and seats[-1]["verified"] is False

    def test_register_after_acceptance_is_verified(self, fresh_state, monkeypatch):
        """After a PASSING run (acceptance_ok=True), pressing register again on
        the fixed workspace is the one allowed path to verified=True."""
        with dw._lock:
            dw._state.update(acceptance_ok=True)
        monkeypatch.setattr(
            dw, "probe_register", lambda n, s="last": {"admitted": True, "seat": 40}
        )
        resp = asyncio.run(dw.api_register(_FakeRequest(GOOD, body={"name": "After"})))
        assert json.loads(resp.body)["verified"] is True

    def test_admitted_probe_without_acceptance_is_not_verified(self, fresh_state, monkeypatch):
        """Workspace fixed (e.g. by an aborted run) but acceptance never passed:
        the probe reports admission WITHOUT verified — no celebration."""
        with dw._lock:
            dw._state.update(acceptance_ok=None)
        monkeypatch.setattr(
            dw, "probe_register", lambda n, s="last": {"admitted": True, "seat": 40}
        )
        resp = asyncio.run(dw.api_register(_FakeRequest(GOOD, body={"name": "Drift"})))
        body = json.loads(resp.body)
        assert body["probe"]["admitted"] is True
        assert body["verified"] is None

    def test_failed_acceptance_blocks_verification(self, fresh_state, monkeypatch):
        with dw._lock:
            dw._state.update(acceptance_ok=False)
        monkeypatch.setattr(
            dw, "probe_register", lambda n, s="last": {"admitted": True, "seat": 40}
        )
        resp = asyncio.run(dw.api_register(_FakeRequest(GOOD, body={"name": "After Fail"})))
        assert json.loads(resp.body)["verified"] is None

    def test_reset_clears_acceptance_state(self, fresh_state, monkeypatch):
        with dw._lock:
            dw._state.update(acceptance_ok=True)
        monkeypatch.setattr(dw, "restore_devfest_baseline", lambda: None)
        monkeypatch.setattr(dw, "probe_register", lambda n, s="last": {"admitted": False})
        asyncio.run(dw.api_reset(_FakeRequest(GOOD)))
        with dw._lock:
            assert dw._state["acceptance_ok"] is None


# ----------------------------------------------- V2: six-act story machine
class TestStoryTracker:
    def _seat(self, admitted, verified=None):
        return ("seat", {"probe": {"admitted": admitted}, "verified": verified})

    def test_full_happy_path(self):
        t = dw.StoryTracker()
        assert t.stage == "idle"
        assert t.observe(*self._seat(False)) == "problem"
        assert t.observe("sdk", {"phase": "agent_config_ready"}) is None
        assert t.observe("sdk", {"phase": "agent_session_started"}) == "mission"
        assert t.observe("tool", {"name": "view_file"}) == "investigation"
        assert t.observe("tool", {"name": "run_command"}) is None  # same stage
        assert t.observe("tool", {"name": "edit_file"}) == "fix"
        assert t.observe("sdk", {"phase": "agent_response_completed"}) is None
        assert t.observe("sdk", {"phase": "independent_verification_started"}) == "check"
        assert t.observe(*self._seat(True, verified=True)) == "celebration"
        assert t.stage == "celebration"

    def test_admitted_probe_alone_never_celebrates(self):
        t = dw.StoryTracker()
        t.observe(*self._seat(False))
        t.observe("sdk", {"phase": "agent_session_started"})
        t.observe("tool", {"name": "edit_file"})
        t.observe("sdk", {"phase": "independent_verification_started"})
        # an admitted seat WITHOUT verified=True must not reach celebration
        assert t.observe(*self._seat(True)) is None
        assert t.stage == "check"

    def test_verified_seat_out_of_order_is_ignored(self):
        """A late/duplicated/forged verified seat before THE CHECK can never
        jump the story ahead."""
        t = dw.StoryTracker()
        t.observe(*self._seat(False))
        assert t.observe(*self._seat(True, verified=True)) is None  # not at check yet
        assert t.stage == "problem"

    def test_no_stage_regression_after_edit(self):
        t = dw.StoryTracker()
        t.observe(*self._seat(False))
        t.observe("sdk", {"phase": "mission_submitted"})
        t.observe("tool", {"name": "edit_file"})
        assert t.observe("tool", {"name": "view_file"}) is None  # fix -> read: no going back
        assert t.stage == "fix"

    def test_failed_is_terminal(self):
        t = dw.StoryTracker()
        t.observe(*self._seat(False))
        t.observe("tool", {"name": "view_file"})
        assert t.observe("done", {"ok": False}) == "failed"
        assert t.observe("tool", {"name": "edit_file"}) is None
        assert t.observe(*self._seat(True, verified=True)) is None
        assert t.stage == "failed"

    def test_error_event_fails_the_story(self):
        t = dw.StoryTracker()
        t.observe(*self._seat(False))
        t.observe("sdk", {"phase": "agent_session_started"})
        assert t.observe("error", {"message": "x"}) == "failed"

    def test_begin_run_reopens_after_celebration(self):
        t = dw.StoryTracker()
        t.observe(*self._seat(False))
        t.observe("sdk", {"phase": "agent_session_started"})
        t.observe("sdk", {"phase": "independent_verification_started"})
        t.observe(*self._seat(True, verified=True))
        t.begin_run()
        assert t.stage == "problem"

    def test_reset_reopens_problem(self):
        t = dw.StoryTracker()
        t.observe("done", {"ok": False})
        t.reset()
        assert t.stage == "problem"

    def test_tool_request_is_not_success_semantics(self):
        """edit_file OBSERVED moves to THE FIX (attempted), but only the
        verified seat can reach THE CELEBRATION — request != verified."""
        t = dw.StoryTracker()
        t.observe(*self._seat(False))
        t.observe("sdk", {"phase": "agent_session_started"})
        t.observe("tool", {"name": "edit_file", "status": "observed"})
        assert t.stage == "fix"
        assert t.observe("done", {"ok": True}) is None  # done ok=True alone: no celebration
        assert t.stage in ("fix", "check")  # never jumped to celebration


# ----------------------------------------------- V2: workspace state machine
class TestWorkspaceState:
    def test_fixing_while_live_run(self, fresh_state, monkeypatch):
        with dw._lock:
            dw._state.update(running=True, mode="live")
        monkeypatch.setattr(dw, "_workspace_at_baseline", lambda: True)
        assert dw._workspace_state() == "FIXING"

    def test_ready_at_baseline(self, fresh_state, monkeypatch):
        monkeypatch.setattr(dw, "_workspace_at_baseline", lambda: True)
        assert dw._workspace_state() == "READY"

    def test_fixed_when_probe_admits_off_baseline(self, fresh_state, monkeypatch):
        monkeypatch.setattr(dw, "_workspace_at_baseline", lambda: False)
        with dw._lock:
            dw._state.update(last_probe_admitted=True)
        assert dw._workspace_state() == "FIXED"

    def test_blocked_on_unexpected_drift(self, fresh_state, monkeypatch):
        monkeypatch.setattr(dw, "_workspace_at_baseline", lambda: False)
        with dw._lock:
            dw._state.update(last_probe_admitted=None)
        assert dw._workspace_state() == "BLOCKED"

    def test_status_includes_story_and_workspace_state(self, fresh_state, monkeypatch):
        monkeypatch.setattr(dw, "_workspace_at_baseline", lambda: True)
        monkeypatch.setattr(dw, "gpu_name", lambda: None)
        resp = asyncio.run(dw.api_status(_FakeRequest({})))
        body = json.loads(resp.body)
        assert body["workspace_state"] in ("READY", "FIXING", "FIXED", "BLOCKED")
        assert "story" in body and "mode" in body


# ------------------------------------------------------------- GPU honesty
class TestGpuHonesty:
    def test_rocm_verified_requires_dll_evidence(self, fresh_state, monkeypatch):
        monkeypatch.setattr(
            dw,
            "_ps_models",
            lambda: [{"name": "gemma4:26b", "digest": "x" * 64, "size": 10, "size_vram": 10}],
        )
        monkeypatch.setattr(
            dw,
            "_tasklist_has_module",
            lambda pat: (
                ["llama-server.exe  1234  amdhip64_7.dll"] if pat.startswith("amdhip64") else []
            ),
        )
        ev = dw.gpu_evidence()
        assert ev["backend"] == "ROCm VERIFIED"
        assert ev["resident"] and ev["gpu_100pct"]
        assert ev["rocm_dll_processes"] == ["1234"]

    def test_resident_without_dll_is_unverified(self, fresh_state, monkeypatch):
        monkeypatch.setattr(
            dw,
            "_ps_models",
            lambda: [{"name": "gemma4:26b", "digest": "x" * 64, "size": 10, "size_vram": 10}],
        )
        monkeypatch.setattr(dw, "_tasklist_has_module", lambda pat: [])
        ev = dw.gpu_evidence()
        assert ev["backend"] == "resident (backend unverified)"

    def test_not_resident_is_cold(self, fresh_state, monkeypatch):
        monkeypatch.setattr(dw, "_ps_models", lambda: [])
        assert dw.gpu_evidence()["backend"] == "not resident (cold)"

    def test_partial_vram_residency_reported_honestly(self, fresh_state, monkeypatch):
        monkeypatch.setattr(
            dw,
            "_ps_models",
            lambda: [{"name": "gemma4:26b", "digest": "x" * 64, "size": 10, "size_vram": 4}],
        )
        monkeypatch.setattr(dw, "_tasklist_has_module", lambda pat: [])
        ev = dw.gpu_evidence()
        assert ev["gpu_100pct"] is False
        assert ev["backend"] == "resident (backend unverified)"

    def test_memory_numbers_labeled_not_vram_claim(self, fresh_state, monkeypatch):
        """V2 P1-C: /api/ps size never presented as model VRAM usage — it is
        reported alongside the model file size and the runner working set with
        an explicit note that its semantics differ."""
        monkeypatch.setattr(
            dw,
            "_ps_models",
            lambda: [
                {
                    "name": "gemma4:26b",
                    "digest": "x" * 64,
                    "size": 2_919_298_496,
                    "size_vram": 2_919_298_496,
                }
            ],
        )
        monkeypatch.setattr(dw, "_model_file_size", lambda m: 18_730_000_000)
        monkeypatch.setattr(dw, "_llama_runner_working_set", lambda: 30_502_416 * 1024)
        monkeypatch.setattr(
            dw,
            "_tasklist_has_module",
            lambda pat: (
                ["llama-server.exe  1234  amdhip64_7.dll"] if pat.startswith("amdhip64") else []
            ),
        )
        ev = dw.gpu_evidence()
        assert ev["model_file_size"] == 18_730_000_000
        assert ev["runner_working_set"] == 30_502_416 * 1024
        assert "never show it as" in ev["runner_size_note"]

    def test_stage_page_marks_api_ps_size_as_accounting(self):
        assert "≠模型占用" in dw.PAGE
        assert "工作集" in dw.PAGE


# ------------------------------------------------------------- XSS / client
class TestClientRendering:
    def test_dynamic_values_use_textcontent(self):
        page = dw.PAGE
        # Builder Pass holder name goes through txt() -> el(id).textContent
        assert "txt('passname'" in page
        assert ".innerHTML" in page  # only the escaped diff builder may use it

    def test_diff_innerhtml_is_escaped(self):
        page = dw.PAGE
        assert "function esc(" in page
        assert "&quot;" in page  # quotes escaped too

    def test_name_input_is_bounded(self):
        assert 'maxlength="24"' in dw.PAGE

    def test_no_gdg_shanghai_or_city_branding(self):
        low = dw.PAGE.lower()
        assert "shanghai" not in low
        assert "gdg shanghai" not in low
        assert "guangzhou" not in low  # V2: the old city suffix is gone

    def test_google_brand_letters_use_google_colors(self):
        page = dw.PAGE
        assert "Google DevFest 2026".replace("Google", "") in page or "grest" in page
        # the six Google letters carry the classic brand palette classes
        for cls, color in (
            ("gb", "#4285F4"),
            ("gr", "#EA4335"),
            ("gy", "#FBBC04"),
            ("gg", "#34A853"),
        ):
            assert cls in page
            assert color in page

    def test_demo_title_and_tagline_present(self):
        page = dw.PAGE
        assert "THE LAST SEAT" in page
        assert "Can a Local AI Agent Save the Final Seat?" in page
        assert "39 Registered" in page and "1 Seat Left" in page

    def test_brand_is_one_line(self):
        """'Google DevFest 2026' and 'THE LAST SEAT' share one brand line
        (boss review): both sit inside the same .brandline flex row."""
        page = dw.PAGE
        assert 'class="brandline"' in page
        start = page.index('class="brandline"')
        end = page.index("</div>", page.index("demotitle", start))
        row = page[start:end]
        assert "gbrand" in row and "THE LAST SEAT" in row
        assert "tagline" not in row  # tagline stays on its own line below

    def test_google_palette_used(self):
        for color in ("#4285F4", "#34A853", "#EA4335", "#FBBC04"):
            assert color in dw.PAGE

    def test_live_replay_preview_all_have_distinct_ui(self):
        page = dw.PAGE
        assert "LIVE" in page and "REPLAY" in page and "DESIGN PREVIEW" in page
        assert "replaybanner" in page and "previewbanner" in page and "livechip" in page
        assert "RECORDED RUN" in page  # replay chip names its identity

    def test_celebration_states_clearly_fake_badge(self):
        assert "not an admission ticket" in dw.PAGE
        assert "虚构纪念徽章" in dw.PAGE

    def test_two_views_toggleable(self):
        page = dw.PAGE
        assert "STAGE VIEW" in page and "DEVELOPER VIEW" in page
        assert "setView" in page

    def test_six_acts_defined(self):
        for act in ("PROBLEM", "MISSION", "INVESTIGATION", "FIX", "CHECK", "CELEBRATION"):
            assert act in dw.PAGE

    def test_sdk_panel_is_first_class(self):
        page = dw.PAGE
        assert "Google Antigravity SDK — Live Agent Execution" in page
        assert "Gemma 4 26B" in page and "Antigravity SDK" in page
        assert "Real Tools" in page
        assert "AMD ROCm" in page
        assert "Behind the Magic" in page
        # the harness loop is spelled out
        assert "Model decides." in page and "Harness executes." in page

    def test_reduced_motion_and_keyboard_supported(self):
        page = dw.PAGE
        assert "prefers-reduced-motion" in page
        assert "focus-visible" in page

    def test_celebration_has_replay_watermark(self):
        assert "rpwmark" in dw.PAGE
        assert "REPLAY · 历史回放" in dw.PAGE


# --------------------------------------------------------- reset & evidence
class TestResetAndEvidence:
    def test_reset_restores_and_reprobes(self, fresh_state, monkeypatch):
        calls = []

        def fake_restore():
            calls.append("restore")

        def fake_probe(name, scenario="last"):
            calls.append("probe")
            return {"admitted": False, "error_type": "RegistrationError"}

        monkeypatch.setattr(dw, "restore_devfest_baseline", fake_restore)
        monkeypatch.setattr(dw, "probe_register", fake_probe)
        resp = asyncio.run(dw.api_reset(_FakeRequest(GOOD)))
        assert json.loads(resp.body)["reset"] is True
        assert calls == ["restore", "probe"]
        # V2: reset reopens THE PROBLEM on stage via a story event
        assert any(e["kind"] == "story" and e["data"]["stage"] == "problem" for e in dw._events)

    def test_evidence_lists_only_curated_dir(self, fresh_state, tmp_path, monkeypatch):
        monkeypatch.setattr(dw, "CURATED_EVIDENCE", tmp_path)
        (tmp_path / "run-1").mkdir()
        (tmp_path / "run-1" / "summary.json").write_text(
            json.dumps({"ok": True, "run_id": "1"}), encoding="utf-8"
        )
        (tmp_path / "run-1" / "big.bin").write_bytes(b"\0" * 300_000)  # oversized -> skipped
        resp = asyncio.run(dw.api_evidence(_FakeRequest({})))
        body = json.loads(resp.body)
        assert body["runs"][0]["run"] == "run-1"
        assert body["runs"][0]["summary"]["ok"] is True
        assert "big.bin" not in body["runs"][0]["files"]

    def test_evidence_missing_dir_is_empty_not_error(self, fresh_state, tmp_path, monkeypatch):
        monkeypatch.setattr(dw, "CURATED_EVIDENCE", tmp_path / "nope")
        resp = asyncio.run(dw.api_evidence(_FakeRequest({})))
        assert json.loads(resp.body)["runs"] == []


# ------------------------------------------------------------ SSE plumbing
class TestSse:
    def test_snapshot_after(self, fresh_state):
        dw.emit("stage", {"a": 1})
        dw.emit("gpu", {"backend": "x"})
        batch = dw._snapshot_after(0)
        assert [e["kind"] for e in batch] == ["stage", "gpu"]
        assert dw._snapshot_after(batch[-1]["id"]) == []

    def test_run_scoped_events_stamped_with_run_id(self, fresh_state):
        with dw._lock:
            dw._state.update(running=True, mode="live", run_id="stage-XYZ")
        dw.emit("tool", {"name": "view_file"})
        dw.emit("stage", {"name": "x"})
        with dw._lock:
            tool = [e for e in dw._events if e["kind"] == "tool"][-1]
            stage = [e for e in dw._events if e["kind"] == "stage"][-1]
        assert tool["data"]["run_id"] == "stage-XYZ"
        assert "run_id" not in stage["data"]  # non-run-scoped kinds untouched

    def test_recorder_captures_ts(self, fresh_state, tmp_path):
        dw.set_recorder(tmp_path / "rec.json")
        dw.emit("seat", {"probe": {"admitted": True}})
        p = dw.dump_recording()
        assert p is not None and p.exists()
        rec = json.loads(p.read_text(encoding="utf-8"))
        assert rec["format"] == 1
        assert rec["meta"]["demo"] == "devfest-last-seat"
        assert rec["events"][0]["kind"] == "seat"
        assert isinstance(rec["events"][0]["ts"], float)
