"""Security and behavior tests for src/devfest_webui.py (no Ollama needed).

Stage-UI gate coverage from the issue:
  - response headers, same-origin + DNS-rebinding gate on ALL POST endpoints
  - replay vs live vs design-preview separation (modes never mislabeled)
  - XSS: name field bounded/escaped, no innerHTML with untrusted event data
  - race/busy gates (409) for run/reset/register
  - workspace reset safety (refuses while busy; junction-safe restore reused)
  - admission engine behavior derived from the REAL scenario code via probe
  - GPU honesty: 'ROCm VERIFIED' only with live DLL+residency evidence
  - preview mode never emits ok:true and never touches the workspace
"""

import asyncio
import json
import sys
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
            running=False, replay=False, preview=False, mode="live", run_id=None, started_at=None
        )
    dw._seq["n"] = 0
    dw._recorder = None
    dw._recorder_path = None
    yield
    with dw._lock:
        dw._events.clear()
        dw._state.update(
            running=False, replay=False, preview=False, mode="live", run_id=None, started_at=None
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
        assert b"DevFest 2026" in resp.body


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
        import threading

        def fake_full_task(model, base_url, timeout_s, run_id, on_event=None, keep_workspace=False):
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
                    ],
                }
            ),
            encoding="utf-8",
        )
        rec = dw.load_replay(p)
        assert len(rec["events"]) == 2

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

    def test_replay_sets_replay_mode_not_live(self, fresh_state):
        # second event is far in the future: the feeder sleeps (delay capped at
        # 2s) so the state check below runs deterministically mid-replay
        events = [
            {"kind": "stage", "data": {"x": 1}, "ts": 0.0},
            {"kind": "stage", "data": {"x": 2}, "ts": 1000.0},
        ]
        assert dw.start_replay({"events": events, "meta": {}}, 50) is True
        with dw._lock:
            assert dw._state["replay"] is True
            assert dw._state["mode"] == "replay"
        import time as _t

        deadline = _t.time() + 6
        while _t.time() < deadline:
            with dw._lock:
                if not dw._state["running"]:
                    break
            _t.sleep(0.1)


class TestPreviewHonesty:
    def test_preview_never_touches_workspace_or_evidence(self, fresh_state, monkeypatch, tmp_path):
        """DESIGN PREVIEW must not write anywhere: restore/evidence are booby-trapped."""

        def boom(*a, **k):  # noqa: ANN001
            raise AssertionError("preview must not reset/write the workspace")

        monkeypatch.setattr(dw, "restore_devfest_baseline", boom)
        monkeypatch.setattr(dw, "run_full_task", boom)
        ok, why = dw.start_preview()
        assert ok
        # run the synthetic timeline to completion fast
        import time as _t

        deadline = _t.time() + 25
        while _t.time() < deadline:
            with dw._lock:
                done = any(e["kind"] == "done" for e in dw._events)
            if done:
                break
            _t.sleep(0.2)
        assert done, "preview timeline did not complete"
        with dw._lock:
            dones = [e["data"] for e in dw._events if e["kind"] == "done"]
        assert dones and dones[-1]["ok"] is None  # never ok:true
        assert dones[-1]["preview"] is True

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

    def test_no_gdg_shanghai_branding(self):
        low = dw.PAGE.lower()
        assert "shanghai" not in low
        assert "gdg shanghai" not in low
        assert "guangzhou" in low

    def test_google_palette_used(self):
        for color in ("#4285F4", "#34A853", "#EA4335", "#FBBC04"):
            assert color in dw.PAGE

    def test_live_replay_preview_all_have_distinct_ui(self):
        page = dw.PAGE
        assert "LIVE" in page and "REPLAY" in page and "DESIGN PREVIEW" in page
        assert "replaybanner" in page and "previewbanner" in page and "livechip" in page

    def test_celebration_states_clearly_fake_badge(self):
        assert "not an admission ticket" in dw.PAGE
        assert "虚构纪念徽章" in dw.PAGE


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
