"""Tests for src/webui.py record/replay — talk backup + media source.

No Ollama/model required: these tests exercise the recorder, the replay
loader/feeder, and the run-gating logic only.
"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import webui  # noqa: E402


@pytest.fixture()
def fresh_state():
    """Reset webui module-level run state around each test."""
    with webui._lock:
        webui._events.clear()
        webui._state.update(running=False, replay=False, run_id=None, started_at=None)
    webui._seq["n"] = 0
    yield
    with webui._lock:
        webui._events.clear()
        webui._state.update(running=False, replay=False, run_id=None, started_at=None)
    webui._seq["n"] = 0


# ------------------------------------------------------------------ recorder
def test_record_roundtrip(fresh_state, tmp_path):
    path = tmp_path / "run.json"
    webui.set_recorder(path)
    try:
        webui.emit("stage", {"name": "restore", "text": "重置基线"})
        webui.emit("token", "hello")
        webui.emit("done", {"ok": True})
        out = webui.dump_recording()
    finally:
        webui.set_recorder(None)
    assert out == path
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["format"] == 1
    assert rec["meta"]["model"]
    assert [e["kind"] for e in rec["events"]] == ["stage", "token", "done"]
    assert all(isinstance(e["ts"], float) for e in rec["events"])
    assert rec["events"][2]["data"] == {"ok": True}


def test_dump_recording_without_recorder(fresh_state):
    assert webui.dump_recording() is None


# -------------------------------------------------------------- replay load
def test_load_replay_rejects_bad_json(fresh_state, tmp_path):
    truncated = tmp_path / "truncated.json"
    truncated.write_text('{"format": 1, "events": [', encoding="utf-8")
    with pytest.raises(ValueError):
        webui.load_replay(truncated)

    wrong_format = tmp_path / "wrong.json"
    wrong_format.write_text(json.dumps({"format": 2, "events": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        webui.load_replay(wrong_format)


def test_load_replay_accepts_valid(fresh_state, tmp_path):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps({"format": 1, "meta": {"run_id": "x"}, "events": []}), encoding="utf-8")
    assert webui.load_replay(p)["meta"]["run_id"] == "x"


# ---------------------------------------------------------------- run gating
def test_run_allowed_during_replay(fresh_state):
    ok, msg = webui.run_allowed()
    assert ok and msg == ""
    with webui._lock:
        webui._state["replay"] = True
    ok, msg = webui.run_allowed()
    assert not ok and "replay" in msg


# ------------------------------------------------------------- status model
def test_api_status_reports_model(fresh_state):
    """/api/status must carry the ACTIVE model so the chip never lies under AGENT_MODEL."""
    import asyncio

    resp = asyncio.run(webui.api_status(None))
    status = json.loads(resp.body.decode("utf-8"))
    assert status["model"] == webui.DEFAULT_MODEL


# ------------------------------------------------------------- replay feeder
def test_start_replay_feeds_events_in_order(fresh_state):
    recording = {
        "format": 1,
        "meta": {"run_id": "flagship", "recorded_at": "2026-11-07 12:00:00"},
        "events": [
            {"id": 1, "kind": "stage", "data": {"text": "one"}, "t": "12:00:00", "ts": 100.0},
            {"id": 2, "kind": "token", "data": "two", "t": "12:00:01", "ts": 101.0},
        ],
    }
    started = webui.start_replay(recording, speed=100.0)
    assert started is True
    deadline = time.time() + 10
    while webui._state["running"] and time.time() < deadline:
        time.sleep(0.05)
    assert not webui._state["running"]
    assert webui._state["replay"] is True  # stays flagged after replay ends
    kinds = [e["kind"] for e in webui._events]
    assert kinds[0] == "replay"
    assert "stage" in kinds and "token" in kinds
    assert kinds[-1] == "replay_done"
    ids = [e["id"] for e in webui._events]
    assert ids == sorted(ids) and len(ids) == len(set(ids))


def test_start_replay_rejected_while_running(fresh_state):
    with webui._lock:
        webui._state["running"] = True
    assert webui.start_replay({"format": 1, "meta": {}, "events": []}, 1.0) is False
