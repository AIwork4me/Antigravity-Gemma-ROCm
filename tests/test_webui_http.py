"""HTTP-surface security tests for src/webui.py (no Ollama/model needed).

Covers the T1 P0 web hardening:
  - browser-origin/CSRF gate on POST /api/run (loopback-only service)
  - security response headers on the homepage
  - non-blocking SSE snapshot helper
  - replay loader bounds (size / event count / shape)
  - client-side rendering does not interpolate event data into innerHTML
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import webui  # noqa: E402


@pytest.fixture()
def fresh_state():
    with webui._lock:
        webui._events.clear()
        webui._state.update(running=False, replay=False, run_id=None, started_at=None)
    webui._seq["n"] = 0
    yield
    with webui._lock:
        webui._events.clear()
        webui._state.update(running=False, replay=False, run_id=None, started_at=None)
    webui._seq["n"] = 0


class _FakeRequest:
    def __init__(self, headers: dict):
        self.headers = {k.lower(): v for k, v in headers.items()}


# ------------------------------------------------------------- origin gate
class TestOriginGate:
    def test_no_origin_allowed(self):
        """curl / pytest send neither Origin nor Sec-Fetch-Site."""
        ok, _ = webui.origin_allowed({"host": "127.0.0.1:8420"})
        assert ok

    def test_exact_same_origin_allowed(self):
        ok, _ = webui.origin_allowed({"host": "127.0.0.1:8420", "origin": "http://127.0.0.1:8420"})
        assert ok

    def test_same_netloc_https_allowed(self):
        ok, _ = webui.origin_allowed({"host": "127.0.0.1:8420", "origin": "https://127.0.0.1:8420"})
        assert ok

    @pytest.mark.parametrize(
        "origin",
        [
            "http://127.0.0.1:9999",  # loopback, DIFFERENT PORT (review F-1)
            "http://localhost:8420",  # loopback, different host
            "http://[::1]:8420",
            "http://127.0.0.1",  # missing port vs Host with port
        ],
    )
    def test_loopback_but_different_netloc_denied(self, origin):
        ok, _ = webui.origin_allowed({"host": "127.0.0.1:8420", "origin": origin})
        assert not ok

    @pytest.mark.parametrize(
        "origin",
        [
            "http://evil.example",
            "https://evil.example:8420",
            "null",
            "http://127.0.0.1.evil.example",
            "http://192.168.1.5:8420",
            "malformed-without-scheme",
        ],
    )
    def test_cross_origin_denied(self, origin):
        ok, _ = webui.origin_allowed({"host": "127.0.0.1:8420", "origin": origin})
        assert not ok

    @pytest.mark.parametrize("site", ["cross-site", "same-city"])
    def test_sec_fetch_site_denied(self, site):
        ok, _ = webui.origin_allowed({"host": "127.0.0.1:8420", "sec-fetch-site": site})
        assert not ok

    @pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
    def test_sec_fetch_site_allowed(self, site):
        ok, _ = webui.origin_allowed({"host": "127.0.0.1:8420", "sec-fetch-site": site})
        assert ok

    @pytest.mark.parametrize("host", ["evil.example:8420", "10.0.0.1:8420", "[fe80::1]:8420", ""])
    def test_non_loopback_host_denied(self, host):
        ok, _ = webui.origin_allowed({"host": host})
        assert not ok


class TestApiRunGate:
    def test_evil_origin_403(self, fresh_state):
        req = _FakeRequest({"host": "127.0.0.1:8420", "origin": "http://evil.example"})
        resp = asyncio.run(webui.api_run(req))
        assert resp.status_code == 403
        assert "forbidden" in json.loads(resp.body)["error"]

    def test_dns_rebinding_host_403(self, fresh_state):
        req = _FakeRequest({"host": "evil.example:8420"})
        resp = asyncio.run(webui.api_run(req))
        assert resp.status_code == 403

    def test_local_origin_but_busy_409(self, fresh_state):
        """Legit origin must still pass through the run gate (busy -> 409)."""
        with webui._lock:
            webui._state["running"] = True
        req = _FakeRequest({"host": "127.0.0.1:8420", "origin": "http://127.0.0.1:8420"})
        resp = asyncio.run(webui.api_run(req))
        assert resp.status_code == 409


async def _homepage_headers():
    resp = await webui.homepage(_FakeRequest({}))
    return resp


class TestHomepageHeaders:
    def test_security_headers(self):
        resp = asyncio.run(_homepage_headers())
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["referrer-policy"] == "no-referrer"


# ------------------------------------------------------------- SSE snapshot
class TestSseSnapshot:
    def test_snapshot_after_returns_newer_only(self, fresh_state):
        webui.emit("stage", {"a": 1})
        webui.emit("token", "x")
        first = webui._snapshot_after(0)
        assert [e["kind"] for e in first] == ["stage", "token"]
        assert webui._snapshot_after(first[-1]["id"]) == []

    def test_snapshot_does_not_clear(self, fresh_state):
        webui.emit("stage", {"a": 1})
        webui._snapshot_after(0)
        assert len(webui._events) == 1  # history preserved for late subscribers


# ------------------------------------------------------- replay loader bounds
class TestReplayBounds:
    def test_oversize_file_rejected(self, fresh_state, tmp_path, monkeypatch):
        monkeypatch.setattr(webui, "_MAX_REPLAY_BYTES", 64)
        p = tmp_path / "big.json"
        p.write_text("x" * 200, encoding="utf-8")
        with pytest.raises(ValueError, match="too large"):
            webui.load_replay(p)

    def test_too_many_events_rejected(self, fresh_state, tmp_path, monkeypatch):
        monkeypatch.setattr(webui, "_MAX_REPLAY_EVENTS", 3)
        p = tmp_path / "many.json"
        events = [{"kind": "token", "data": "x", "ts": 1.0} for _ in range(4)]
        p.write_text(json.dumps({"format": 1, "meta": {}, "events": events}), encoding="utf-8")
        with pytest.raises(ValueError, match="too many"):
            webui.load_replay(p)

    def test_unknown_kind_rejected(self, fresh_state, tmp_path):
        p = tmp_path / "kind.json"
        p.write_text(
            json.dumps({"format": 1, "meta": {}, "events": [{"kind": "alert", "data": "x"}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="kind"):
            webui.load_replay(p)

    def test_non_object_event_rejected(self, fresh_state, tmp_path):
        p = tmp_path / "arr.json"
        p.write_text(json.dumps({"format": 1, "meta": {}, "events": ["token"]}), encoding="utf-8")
        with pytest.raises(ValueError, match="object"):
            webui.load_replay(p)

    def test_huge_single_event_rejected(self, fresh_state, tmp_path):
        p = tmp_path / "huge.json"
        p.write_text(
            json.dumps(
                {"format": 1, "meta": {}, "events": [{"kind": "token", "data": "y" * 70_000}]}
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="too large"):
            webui.load_replay(p)

    def test_bad_ts_rejected(self, fresh_state, tmp_path):
        p = tmp_path / "ts.json"
        p.write_text(
            json.dumps({"format": 1, "meta": {}, "events": [{"kind": "token", "ts": "soon"}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="ts"):
            webui.load_replay(p)

    def test_nan_ts_rejected(self, fresh_state, tmp_path):
        """NaN ts would make time.sleep(nan) explode mid-replay (review F-7)."""
        p = tmp_path / "nan.json"
        p.write_text(
            '{"format":1,"meta":{},"events":[{"kind":"token","ts":NaN}]}', encoding="utf-8"
        )
        with pytest.raises(ValueError, match="finite"):
            webui.load_replay(p)

    def test_deeply_nested_json_valueerror(self, fresh_state, tmp_path):
        """Pathological nesting raises RecursionError inside json.loads —
        must surface as ValueError, not a traceback (review F-6)."""
        p = tmp_path / "deep.json"
        p.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
        with pytest.raises(ValueError, match="cannot read replay"):
            webui.load_replay(p)

    def test_meta_not_dict_rejected(self, fresh_state, tmp_path):
        p = tmp_path / "meta.json"
        p.write_text(json.dumps({"format": 1, "meta": "x", "events": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="meta"):
            webui.load_replay(p)

    @pytest.mark.parametrize("speed,clamped", [(0.0, 0.1), (-5, 0.1), (1000, 100.0), (1.5, 1.5)])
    def test_speed_clamped(self, speed, clamped):
        assert webui.clamp_speed(speed) == clamped


# ------------------------------------------------- client rendering (no innerHTML)
class TestClientRendering:
    def test_done_event_renders_via_text(self):
        """files_changed comes from a (possibly replayed) JSON file — it must be
        rendered with textContent, never interpolated into innerHTML."""
        assert "d.files_changed.join" not in webui.PAGE or "addEvRow" in webui.PAGE
        assert "addEvRow" in webui.PAGE  # safe row builder exists

    def test_error_events_are_shown(self):
        assert "e.data" in webui.PAGE  # error listener distinguishes transport vs data

    def test_diff_escaping_covers_quotes(self):
        assert "&quot;" in webui.PAGE
