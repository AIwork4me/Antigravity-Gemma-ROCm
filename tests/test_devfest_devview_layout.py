"""Real-browser layout regression tests for the Developer View (V3 fix).

Executed with Playwright against the REAL page served by src/devfest_webui.py
in deterministic modes only — REPLAY fixtures (stress event streams) and
DESIGN PREVIEW. No model, no GPU, no workspace writes; fixtures are test-only
and the page keeps its honest REPLAY/PREVIEW identity throughout.

Browser: uses the local Chromium-family channel (msedge/chrome) so nothing
needs downloading on a normal Windows machine; CI without any browser skips
this module (the rest of the suite still runs there). On the reference
machine these tests run for real — see docs/DEVFEST_THE_LAST_SEAT.md.

Covers (mission P4):
  - viewports 1920x1080, 1704x864 (original bug report), 1366x768, 1280x720,
    1536x864 (= 1920x1080 at 125% zoom)
  - geometry via getBoundingClientRect: no overlap, panels bounded, footer
    clear, badge reachable, no horizontal overflow, hidden tabs inert
  - tab switching preserves history + exactly one EventSource
  - keyboard tab navigation with aria-selected
  - follow-the-tail scroll respects manual scrolling
  - replay identity survives a page reload (no startup-probe stomp)
  - stage-view regression at 1080p
  - agent error state and DESIGN PREVIEW states
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

VIEWPORTS = [(1920, 1080), (1704, 864), (1366, 768), (1280, 720), (1536, 864)]


# --------------------------------------------------------------- fixtures
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_server(port: int, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"webui on :{port} did not come up")


class _Server:
    def __init__(self, args: list[str]):
        self.port = _free_port()
        self.proc = subprocess.Popen(
            [PY, "-I", str(ROOT / "src" / "devfest_webui.py"), "--port", str(self.port), *args],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_server(self.port)
        except Exception:
            self.stop()
            raise

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def stress_replay_server(tmp_path_factory):
    """Stress REPLAY: 100 tools, 100 policy lines, long args, 300-line diff,
    200 tokens (zh+en) and the full verified success chain."""
    p = tmp_path_factory.mktemp("dv") / "stress.json"
    p.write_text(json.dumps(_stress_recording()), encoding="utf-8")
    srv = _Server(["--replay", str(p), "--replay-speed", "100"])
    yield srv
    srv.stop()


@pytest.fixture(scope="module")
def error_replay_server(tmp_path_factory):
    p = tmp_path_factory.mktemp("dv") / "error.json"
    p.write_text(json.dumps(_error_recording()), encoding="utf-8")
    srv = _Server(["--replay", str(p), "--replay-speed", "100"])
    yield srv
    srv.stop()


@pytest.fixture(scope="module")
def preview_server():
    srv = _Server(["--dry-run"])
    yield srv
    srv.stop()


_CHANNEL: str | None = None


def _launch(p):
    global _CHANNEL
    if _CHANNEL is None:
        for ch in ("msedge", "chrome", None):
            try:
                b = (
                    p.chromium.launch(headless=True, channel=ch)
                    if ch
                    else p.chromium.launch(headless=True)
                )
                b.close()
                _CHANNEL = ch or "bundled"
                break
            except Exception:
                continue
        if _CHANNEL is None:
            pytest.skip("no local Chromium-family browser channel available")
    return p.chromium.launch(headless=True, channel=None if _CHANNEL == "bundled" else _CHANNEL)


# ------------------------------------------------------- event fixtures
def _stress_recording() -> dict:
    long_arg = (
        "{'AbsolutePath': '<repo>/devfest_workspace/" + "tests/" * 20 + "test_checkin.py', "
        "'Extra': '" + ("很长的中文参数混排 English mixed content " * 6) + "'}"
    )
    long_line = "+" + ("x" * 180) + "  # long line 中文 混排 no-wrap check"
    events: list[dict] = []
    t = 1000.0

    def ev(kind, data):
        nonlocal t
        events.append({"kind": kind, "data": data, "ts": round(t, 3)})
        t += 0.01

    ev("stage", {"name": "restore", "text": "重置工作区到缺陷基线"})
    ev("pytest", {"phase": "before", "exit": 1, "tail": "1 failed, 8 passed"})
    ev("sdk", {"phase": "agent_config_ready"})
    ev("sdk", {"phase": "agent_session_started"})
    ev("sdk", {"phase": "mission_submitted"})
    names = ["view_file", "list_directory", "run_command", "find_file", "search_directory"]
    for i in range(100):
        nm = "edit_file" if i == 50 else names[i % len(names)]
        args = long_arg if i % 10 == 3 else "{'AbsolutePath': 'checkin.py#" + str(i) + "'}"
        ev("tool", {"name": nm, "args": args, "status": "observed", "seq": i + 1})
        if i % 3 == 0:
            dec = "ALLOW" if i % 6 == 0 else "DENY"
            ev("policy", f"run_command decision={dec} args={{'CommandLine': 'python -m pytest'}}")
    for i in range(200):
        ev("token", f"流式 token 第 {i} 段 — streaming chunk {i} mixed 中英文. ")
    ev("sdk", {"phase": "agent_response_completed", "n_tool_calls": 100})
    ev("sdk", {"phase": "independent_verification_started"})
    patch = ["--- a/checkin.py", "+++ b/checkin.py", "@@ -30,7 +30,7 @@"]
    patch += [long_line if i % 7 == 0 else f"+    context line {i} 上下文行" for i in range(300)]
    patch += ["-    if registered >= capacity - 1:", "+    if registered >= capacity:"]
    ev("diff", {"files": ["checkin.py"], "patch": "\n".join(patch)})
    ev("pytest", {"phase": "after", "exit": 0, "tail": "9 passed"})
    ev(
        "gpu",
        {
            "phase": "post",
            "backend": "ROCm VERIFIED",
            "digest": "0" * 64,
            "resident": True,
            "model_file_size": 18_730_000_000,
            "runner_working_set": 31_000_000_000,
            "size": 2_919_298_496,
            "runner_size_note": "fixture note",
        },
    )
    ev("sdk", {"phase": "independent_verification_completed", "ok": True})
    ev("seat", {"phase": "final", "probe": {"admitted": True, "seat": 40}, "verified": True})
    ev(
        "done",
        {
            "ok": True,
            "elapsed_s": 123.4,
            "n_tool_calls": 100,
            "files_changed": ["checkin.py"],
            "sha256_after_short": {"checkin.py": "0" * 16},
        },
    )
    return {"format": 1, "meta": {"run_id": "stress-fixture"}, "events": events}


def _error_recording() -> dict:
    events = [
        {"kind": "stage", "data": {"name": "restore", "text": "重置工作区"}, "ts": 1.0},
        {
            "kind": "story",
            "data": {
                "stage": "problem",
                "act": "THE PROBLEM",
                "title": "最后一席被错误拒绝",
                "line": "…",
            },
            "ts": 1.05,
        },
        {
            "kind": "story",
            "data": {
                "stage": "investigation",
                "act": "THE INVESTIGATION",
                "title": "Agent 正在调查报名程序",
                "line": "…",
            },
            "ts": 1.08,
        },
        {"kind": "tool", "data": {"name": "view_file", "status": "observed", "seq": 1}, "ts": 1.1},
        {"kind": "error", "data": {"message": "TimeoutError: simulated failure 失败"}, "ts": 1.2},
        {
            "kind": "story",
            "data": {
                "stage": "failed",
                "act": "THE OUTCOME",
                "title": "任务未通过独立验收",
                "line": "…",
            },
            "ts": 1.25,
        },
        {"kind": "done", "data": {"ok": False, "files_changed": []}, "ts": 1.3},
    ]
    return {"format": 1, "meta": {"run_id": "error-fixture"}, "events": events}


ES_COUNT_INIT = (
    "window.__esCount = 0;"
    "const __ES = window.EventSource;"
    "window.EventSource = function(...a){ window.__esCount++; return new __ES(...a); };"
    "window.EventSource.prototype = __ES.prototype;"
)


# ------------------------------------------------------ page geometry DSL
GEOM = """
() => {
  const rp = document.querySelectorAll('.panel')[1];
  const lp = document.querySelectorAll('.panel')[0];
  const rpr = rp.getBoundingClientRect(), lpr = lp.getBoundingClientRect();
  const active = document.querySelector('.tabpanel.active');
  const ar = active ? active.getBoundingClientRect() : null;
  const foot = document.querySelector('footer').getBoundingClientRect();
  const main = document.querySelector('main').getBoundingClientRect();
  const head = document.querySelector('.sdkhead').getBoundingClientRect();
  const tabbar = document.querySelector('.tabbar').getBoundingClientRect();
  const badge = document.querySelector('#win .badge-card');
  const winEl = document.getElementById('win');
  let badgeOk = false, badgeScrolled = false;
  if (winEl.style.display === 'block' && badge) {
    const br = badge.getBoundingClientRect();
    if (br.bottom <= lpr.bottom + 1) badgeOk = true;
    else if (lp.scrollHeight > lp.clientHeight) {
      lp.scrollTop = lp.scrollHeight;
      badgeScrolled = badge.getBoundingClientRect().bottom <= lp.getBoundingClientRect().bottom + 1;
      badgeOk = badgeScrolled;
    }
  }
  return {
    view: document.body.dataset.view,
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    bodyOverflowY: document.documentElement.scrollHeight - window.innerHeight,
    mainBottom: Math.round(main.bottom), footerTop: Math.round(foot.top),
    footerClear: main.bottom <= foot.top + 1,
    activePanelBounded: ar ? (ar.bottom <= rpr.bottom + 1 && ar.top >= tabbar.bottom - 1) : false,
    activePanelId: active ? active.id : null,
    headerAboveTabs: head.bottom <= tabbar.top + 1,
    rightPanelOverflowX: rp.scrollWidth - rp.clientWidth,
    leftPanelOverflowX: lp.scrollWidth - lp.clientWidth,
    winShown: winEl.style.display,
    badgeFullyVisibleOrScrollable: badgeOk,
    badgeNeededScroll: badgeScrolled,
    tabs: Array.from(document.querySelectorAll('.tabbtn')).map(b => ({
      id: b.id, sel: b.getAttribute('aria-selected')}))
  };
}
"""


def _goto_dev(page, url):
    page.goto(url)
    page.wait_for_function("document.getElementById('win').style.display==='block'", timeout=15000)
    page.get_by_role("button", name="DEVELOPER VIEW").click()
    page.wait_for_timeout(300)


# ------------------------------------------------------------------ tests
def test_devview_geometry_matrix(stress_replay_server):
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        _goto_dev(page, stress_replay_server.url)
        for w, h in VIEWPORTS:
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(200)
            for tab in ("tab-sdk", "tab-diff", "tab-out", "tab-ev"):
                page.click(f"#{tab}")
                page.wait_for_timeout(80)
                g = page.evaluate(GEOM)
                label = f"{w}x{h}:{tab}"
                assert g["docOverflowX"] <= 1, f"{label} horizontal overflow"
                assert g["rightPanelOverflowX"] <= 1, f"{label} right panel h-overflow"
                assert g["leftPanelOverflowX"] <= 1, f"{label} left panel h-overflow"
                assert g["activePanelBounded"], f"{label} tab panel escapes right panel"
                assert g["headerAboveTabs"], f"{label} header overlaps tabs"
                assert g["footerClear"], f"{label} footer covered by main"
                assert g["winShown"] == "block", f"{label} success card vanished"
                assert g["badgeFullyVisibleOrScrollable"], (
                    f"{label} Builder Pass clipped and unreachable"
                )
        # feed content actually reachable at 1080 in the biggest tab
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.click("#tab-sdk")
        page.wait_for_timeout(150)
        reach = page.evaluate(
            """() => { const p = document.getElementById('panel-sdk');
              p.scrollTop = p.scrollHeight;
              const last = p.lastElementChild.getBoundingClientRect();
              const pr = p.getBoundingClientRect();
              return {scrollable: p.scrollHeight > p.clientHeight,
                      lastVisible: last.bottom <= pr.bottom + 2}; }"""
        )
        assert reach["scrollable"], "stress feed should be scrollable, not unbounded"
        assert reach["lastVisible"], "last feed item unreachable after scrolling"
        browser.close()


def test_tabs_preserve_state_single_connection(stress_replay_server):
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.add_init_script(ES_COUNT_INIT)  # count EventSource constructions
        _goto_dev(page, stress_replay_server.url)
        counts = page.evaluate(
            """() => ({feed: document.getElementById('devfeed').children.length,
                        tokens: document.getElementById('tokens').children.length,
                        streams: window.__esCount,
                        open: (typeof es!=='undefined' && es && es.readyState === 1)})"""
        )
        assert counts["feed"] >= 100, "stress feed history missing"
        assert counts["tokens"] >= 100, "token history missing"
        assert counts["streams"] == 1, "more than one EventSource connection"
        assert counts["open"] is True, "the single EventSource must still be open"
        for tab in ("tab-diff", "tab-out", "tab-ev", "tab-sdk"):
            page.click(f"#{tab}")
            page.wait_for_timeout(80)
        after = page.evaluate(
            """() => ({feed: document.getElementById('devfeed').children.length,
                        tokens: document.getElementById('tokens').children.length,
                        streams: window.__esCount,
                        open: (typeof es!=='undefined' && es && es.readyState === 1)})"""
        )
        assert after == counts, "tab switching lost history or reconnected"
        # hidden tab is inert (no layout footprint)
        page.click("#tab-diff")
        page.wait_for_timeout(80)
        inert = page.evaluate(
            """() => ({hidden: getComputedStyle(document.getElementById('panel-sdk')).display,
                        hiddenW: document.getElementById('panel-sdk').offsetWidth})"""
        )
        assert inert["hidden"] == "none" and inert["hiddenW"] == 0
        browser.close()


def test_tab_keyboard_navigation(stress_replay_server):
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        _goto_dev(page, stress_replay_server.url)
        page.focus("#tab-sdk")
        page.keyboard.press("ArrowRight")
        assert page.evaluate("document.activeElement.id") == "tab-diff"
        assert (
            page.eval_on_selector("#tab-diff", "el => el.getAttribute('aria-selected')") == "true"
        )
        page.keyboard.press("End")
        assert page.evaluate("document.activeElement.id") == "tab-ev"
        page.keyboard.press("Home")
        assert page.evaluate("document.activeElement.id") == "tab-sdk"
        page.keyboard.press("ArrowLeft")
        assert page.evaluate("document.activeElement.id") == "tab-ev", "ArrowLeft must wrap"
        browser.close()


def test_scroll_follow_respects_user_scroll(stress_replay_server):
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        _goto_dev(page, stress_replay_server.url)
        page.click("#tab-sdk")
        # user scrolls UP to read history -> new events must not force-scroll
        page.evaluate(
            """() => { const p = document.getElementById('panel-sdk');
                 p.scrollTop = 0; p.dispatchEvent(new Event('scroll')); }"""
        )
        page.evaluate("addEv(document.createTextNode('new-event-while-scrolled-up'))")
        assert page.evaluate("document.getElementById('panel-sdk').scrollTop") < 40
        # back at the bottom -> follow-the-tail resumes
        page.evaluate(
            """() => { const p = document.getElementById('panel-sdk');
                 p.scrollTop = p.scrollHeight; p.dispatchEvent(new Event('scroll')); }"""
        )
        page.evaluate("addEv(document.createTextNode('new-event-while-following'))")
        at_bottom = page.evaluate(
            """() => { const p = document.getElementById('panel-sdk');
                 return p.scrollHeight - p.scrollTop - p.clientHeight; }"""
        )
        assert at_bottom < 40, "did not resume following after scrolling back down"
        browser.close()


def test_replay_identity_survives_reload(stress_replay_server):
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        _goto_dev(page, stress_replay_server.url)
        page.reload()
        page.wait_for_function("document.getElementById('win').style.display==='block'")
        page.wait_for_timeout(1200)  # let a full refresh cycle run
        state = page.evaluate(
            """() => ({win: document.getElementById('win').style.display,
                        rpwmark: getComputedStyle(document.getElementById('rpwmark')).display,
                        regDisabled: document.getElementById('regbtn').disabled,
                        banner: document.getElementById('replaybanner').textContent.slice(0, 40),
                        bannerShown: getComputedStyle(document.getElementById('replaybanner')).display})"""
        )
        assert state["win"] == "block", "startup probe stomped the replayed celebration"
        assert state["rpwmark"] == "flex", "REPLAY watermark missing"
        assert state["regDisabled"] is True, "register must be disabled on a replay page"
        assert state["bannerShown"] == "block"
        assert "REPLAY" in state["banner"] or "回放" in state["banner"]
        browser.close()


def test_stage_view_regression_1080(stress_replay_server):
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        _goto_dev(page, stress_replay_server.url)
        page.get_by_role("button", name="STAGE VIEW").click()
        page.wait_for_timeout(300)
        g = page.evaluate(
            """() => {
              const stage = {view: document.body.dataset.view};
              stage.docOverflowX = document.documentElement.scrollWidth - innerWidth;
              const acts = document.querySelectorAll('.act').length;
              const cards = document.getElementById('toolcards').children.length;
              const tc = document.getElementById('toolcards').getBoundingClientRect();
              const rp = document.querySelectorAll('.panel')[1].getBoundingClientRect();
              const story = document.getElementById('storybox').getBoundingClientRect();
              stage.acts = acts; stage.cards = cards;
              stage.cardsBounded = tc.bottom <= rp.bottom + 1;
              stage.storyVisible = story.height > 20;
              stage.seat = document.getElementById('seattaken').textContent;
              stage.winShown = document.getElementById('win').style.display;
              return stage; }"""
        )
        assert g["view"] == "stage"
        assert g["docOverflowX"] <= 1
        assert g["acts"] == 6 and g["cards"] >= 5
        assert g["cardsBounded"], "tool cards escape the right panel in stage view"
        assert g["storyVisible"]
        assert g["seat"] == "40" and g["winShown"] == "block"
        browser.close()


def test_error_state_layout(error_replay_server):
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1704, "height": 864})
        page.goto(error_replay_server.url)
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="DEVELOPER VIEW").click()
        page.wait_for_timeout(300)
        g = page.evaluate(GEOM)
        assert g["activePanelBounded"] and g["docOverflowX"] <= 1
        assert g["winShown"] == "none", "failed run must never show the success card"
        status = page.evaluate("document.getElementById('sdkstatus').textContent")
        assert "⚠️" in status
        story = page.evaluate("document.getElementById('story_act').textContent")
        assert (
            story == "THE OUTCOME"
            or "OUTCOME" in story
            or "未通过" in (page.evaluate("document.getElementById('story_title').textContent"))
        )
        browser.close()


def test_design_preview_states(preview_server):
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(preview_server.url)
        page.wait_for_timeout(1500)
        # mid-run state in stage view
        page.get_by_role("button", name="▶ Give Mission to Antigravity").click()
        page.wait_for_timeout(6500)  # first preview tool card lands ~4.3 s in
        mid = page.evaluate(
            """() => ({cards: document.getElementById('toolcards').children.length,
                        docOverflowX: document.documentElement.scrollWidth - innerWidth,
                        runDisabled: document.getElementById('runbtn').disabled})"""
        )
        assert mid["cards"] >= 1, "preview produced no tool cards"
        assert mid["docOverflowX"] <= 1
        assert mid["runDisabled"] is True
        # finish the synthetic timeline (~15 s) and check the dev-view layout
        page.wait_for_function(
            "document.getElementById('win').style.display==='block'", timeout=25000
        )
        page.get_by_role("button", name="DEVELOPER VIEW").click()
        page.wait_for_timeout(300)
        g = page.evaluate(GEOM)
        assert g["activePanelBounded"] and g["footerClear"] and g["docOverflowX"] <= 1
        assert g["winShown"] == "block"
        pvw = page.evaluate("getComputedStyle(document.getElementById('pvwmark')).display")
        assert pvw == "flex", "PREVIEW watermark missing on the success card"
        browser.close()
