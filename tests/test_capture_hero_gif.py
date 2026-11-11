"""Tests for the hero-GIF capture pipeline (scripts/capture_devfest_hero.py).

The honesty gate is the point of this module:
  - recordings that are not complete accepted runs must never yield success
    imagery (validate_recording raises);
  - out-of-order events must be rejected, not silently re-ordered;
  - every produced frame carries the RECORDED REAL RUN / EDITED HIGHLIGHTS badge —
    an unlabeled replay can never be packaged as live;
  - generated metadata never leaks absolute user paths;
  - assembled GIFs have sane dimensions and file sizes.

The end-to-end browser capture runs only where a Chromium-family channel is
available (reference machine); CI skips that part.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from capture_devfest_hero import (  # noqa: E402
    DEFAULT_SCENE_DURATIONS,
    SCENES,
    _rel,
    assemble_gif,
    badge_texts,
    classify_frame,
    select_scenes,
    validate_recording,
)

ROOT = Path(__file__).resolve().parent.parent


def _event(kind: str, data: dict, ts: float = 0.0) -> dict:
    return {"id": 1, "kind": kind, "data": data, "t": "12:00:00", "ts": ts}


def _accepted_recording() -> dict:
    """The minimal shape of a complete, accepted real run (mirrors the real
    docs/assets/last-seat-recording.json structure)."""
    return {
        "format": 1,
        "meta": {"run_id": "t-hero", "recorded_at": "2026-11-11 20:00:00", "model": "gemma4:26b"},
        "events": [
            _event("seat", {"phase": "user", "probe": {"admitted": False}}),
            _event("story", {"stage": "problem"}),
            _event("pytest", {"phase": "before", "exit": 1, "tail": "1 failed, 8 passed"}),
            _event("story", {"stage": "mission"}),
            _event("tool", {"name": "view_file"}),
            _event("tool", {"name": "edit_file"}),
            _event("story", {"stage": "investigation"}),
            _event("story", {"stage": "fix"}),
            _event("story", {"stage": "check"}),
            _event("pytest", {"phase": "after", "exit": 0, "tail": "9 passed"}),
            _event(
                "done",
                {
                    "run_id": "t-hero",
                    "ok": True,
                    "elapsed_s": 90.0,
                    "n_tool_calls": 2,
                    "bug_present_before": True,
                    "bug_fixed_after": True,
                    "tests_tampered": False,
                    "forbidden_changed": [],
                    "disk_drift": [],
                },
            ),
            _event("story", {"stage": "celebration"}),
            _event(
                "seat",
                {
                    "phase": "final",
                    "verified": True,
                    "run_id": "t-hero",
                    "probe": {"admitted": True, "seat": 40},
                },
            ),
        ],
    }


# ---------------------------------------------------------------- validation
def test_validate_accepts_complete_run_and_summarizes():
    s = validate_recording(_accepted_recording())
    assert s["run_id"] == "t-hero"
    assert s["tool_sequence"] == ["view_file", "edit_file"]
    assert s["pytest_tails"]["after"] == "9 passed"
    assert s["n_events"] == 13


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.update(format=2), id="bad-format"),
        pytest.param(lambda r: r["events"][3].update(kind="nonsense"), id="unknown-kind"),
        pytest.param(lambda r: r["events"].clear(), id="no-events"),
        pytest.param(lambda r: r["meta"].pop("run_id"), id="no-run-id"),
    ],
)
def test_validate_rejects_bad_containers(mutate):
    rec = _accepted_recording()
    mutate(rec)
    with pytest.raises(ValueError):
        validate_recording(rec)


def test_validate_rejects_failed_acceptance():
    """A run that did not pass acceptance must never produce success imagery."""
    rec = _accepted_recording()
    rec["events"][10] = _event("done", {"ok": False})  # acceptance FAILED
    with pytest.raises(ValueError, match="accepted completion"):
        validate_recording(rec)


def test_validate_rejects_missing_verified_seat():
    rec = _accepted_recording()
    rec["events"] = [
        e for e in rec["events"] if not (e["kind"] == "seat" and e["data"].get("verified"))
    ]
    with pytest.raises(ValueError, match="verified seat"):
        validate_recording(rec)


def test_validate_rejects_design_preview_done():
    """A design-preview 'done' (ok under preview:true) is not acceptance."""
    rec = _accepted_recording()
    rec["events"][10] = _event("done", {"ok": True, "preview": True})
    with pytest.raises(ValueError, match="accepted completion"):
        validate_recording(rec)


def test_validate_rejects_story_regression():
    rec = _accepted_recording()
    rec["events"].insert(6, _event("story", {"stage": "mission"}))  # goes backwards
    with pytest.raises(ValueError, match="regress"):
        validate_recording(rec)


def test_validate_rejects_pytest_order_inversion():
    rec = _accepted_recording()
    rec["events"][2], rec["events"][9] = rec["events"][9], rec["events"][2]
    with pytest.raises(ValueError, match="before"):
        validate_recording(rec)


def test_validate_rejects_missing_verification_pair():
    rec = _accepted_recording()
    rec["events"] = [e for e in rec["events"] if e["kind"] != "pytest"]
    with pytest.raises(ValueError, match="before/after"):
        validate_recording(rec)


def test_validate_rejects_green_before_pytest():
    """The bug must be demonstrated: a green 'before' run is invalid."""
    rec = _accepted_recording()
    rec["events"][2] = _event("pytest", {"phase": "before", "exit": 0, "tail": "9 passed"})
    with pytest.raises(ValueError, match="before.*fail"):
        validate_recording(rec)


def test_validate_rejects_red_after_pytest():
    rec = _accepted_recording()
    rec["events"][9] = _event("pytest", {"phase": "after", "exit": 1, "tail": "1 failed"})
    with pytest.raises(ValueError, match="after.*green"):
        validate_recording(rec)


def test_validate_rejects_tampered_tests():
    rec = _accepted_recording()
    rec["events"][10]["data"]["tests_tampered"] = True
    with pytest.raises(ValueError, match="tampered"):
        validate_recording(rec)


def test_validate_rejects_forbidden_file_changes():
    rec = _accepted_recording()
    rec["events"][10]["data"]["forbidden_changed"] = ["outside.py"]
    with pytest.raises(ValueError, match="whitelist"):
        validate_recording(rec)


def test_validate_rejects_disk_drift():
    rec = _accepted_recording()
    rec["events"][10]["data"]["disk_drift"] = ["checkin.py"]
    with pytest.raises(ValueError, match="drift"):
        validate_recording(rec)


def test_validate_rejects_missing_acceptance_field():
    rec = _accepted_recording()
    rec["events"][10]["data"].pop("tests_tampered")
    with pytest.raises(ValueError, match="acceptance field"):
        validate_recording(rec)


def test_validate_rejects_probe_not_admitted():
    rec = _accepted_recording()
    rec["events"][12]["data"]["probe"]["admitted"] = False
    with pytest.raises(ValueError, match="admit"):
        validate_recording(rec)


def test_validate_rejects_wrong_seat():
    rec = _accepted_recording()
    rec["events"][12]["data"]["probe"]["seat"] = 39
    with pytest.raises(ValueError, match="seat 39"):
        validate_recording(rec)


def test_validate_rejects_run_id_mismatch():
    rec = _accepted_recording()
    rec["events"][10]["data"]["run_id"] = "someone-elses-run"
    with pytest.raises(ValueError, match="run_id"):
        validate_recording(rec)


def test_validate_rejects_verified_seat_before_done():
    rec = _accepted_recording()
    events = rec["events"]
    i_done = next(i for i, e in enumerate(events) if e["kind"] == "done")
    i_seat = next(
        i for i, e in enumerate(events) if e["kind"] == "seat" and e["data"].get("verified")
    )
    events[i_done], events[i_seat] = events[i_seat], events[i_done]
    with pytest.raises(ValueError, match="out of order"):
        validate_recording(rec)


def test_validate_rejects_done_before_green_pytest():
    rec = _accepted_recording()
    events = rec["events"]
    i_after = next(
        i for i, e in enumerate(events) if e["kind"] == "pytest" and e["data"]["phase"] == "after"
    )
    i_done = next(i for i, e in enumerate(events) if e["kind"] == "done")
    events[i_after], events[i_done] = events[i_done], events[i_after]
    with pytest.raises(ValueError, match="order"):
        validate_recording(rec)


def test_validate_rejects_no_edit_tool():
    """Green tests with no observed edit = nothing was fixed."""
    rec = _accepted_recording()
    rec["events"] = [
        e for e in rec["events"] if not (e["kind"] == "tool" and e["data"]["name"] == "edit_file")
    ]
    with pytest.raises(ValueError, match="edit tool"):
        validate_recording(rec)


def test_validate_rejects_multiple_done_events():
    rec = _accepted_recording()
    rec["events"].insert(11, _event("done", {"ok": True}))
    with pytest.raises(ValueError, match="more than one done"):
        validate_recording(rec)


# ------------------------------------------------------------------- honesty
def test_badge_always_identifies_recorded_replay():
    for scene in SCENES:
        left, right = badge_texts("t-hero", 1.5, scene)
        assert "RECORDED REAL RUN t-hero" in left
        assert "EDITED HIGHLIGHTS" in left
        assert right  # every frame names its scene


def test_badge_never_claims_live_at_any_speed():
    for speed in (0.5, 1.0, 1.5, 4.0):
        left, _ = badge_texts("t-hero", speed, "problem")
        assert "RECORDED REAL RUN" in left and "EDITED HIGHLIGHTS" in left
        assert "LIVE" not in left


def test_badge_text_has_no_paths_or_secrets():
    left, right = badge_texts("t-hero", 2.0, "check")
    assert "\\" not in left and "/" not in left
    assert "\\" not in right


def test_rel_never_leaks_absolute_user_paths(tmp_path):
    assert _rel(ROOT / "docs" / "assets" / "x.png") == "docs/assets/x.png"
    assert _rel(tmp_path / "outside.png") == "outside.png"  # outside repo -> basename only
    assert _rel(None) is None


# ------------------------------------------------------------- frame mapping
def test_classify_frame_maps_the_arc():
    assert classify_frame({"story": "THE PROBLEM", "tools": 0}) == "problem"
    assert classify_frame({"story": "THE MISSION", "tools": 0}) == "mission"
    assert classify_frame({"story": "THE INVESTIGATION", "tools": 2, "tokens": 0}) == "investigate"
    assert (
        classify_frame({"story": "THE FIX", "tools": 5, "tokens": 200, "tab": "tab-out"}) == "model"
    )
    assert classify_frame({"story": "THE CHECK", "tools": 5, "tokens": 500}) == "check"
    assert classify_frame({"story": "THE CELEBRATION", "tools": 5, "win": True}) == "celebrate"


def test_select_scenes_samples_and_pads():
    states = [{"frame": i, "scene": "problem"} for i in range(50)]
    chosen = select_scenes(states, DEFAULT_SCENE_DURATIONS, gif_fps=8.0)
    assert len(chosen["problem"]) == round(DEFAULT_SCENE_DURATIONS["problem"] * 8)
    assert chosen["problem"][0] == 0 and chosen["problem"][-1] == 49
    # a scene with fewer captured frames than the target pads by repeating
    few = select_scenes(
        [{"frame": 0, "scene": "mission"}, {"frame": 1, "scene": "mission"}],
        DEFAULT_SCENE_DURATIONS,
        8.0,
    )
    assert len(few["mission"]) == round(DEFAULT_SCENE_DURATIONS["mission"] * 8)
    assert few["mission"][-1] == 1
    # scenes with no footage yield an empty list (assembly then fails loudly
    # if the WHOLE capture is empty — never fabricates frames)
    assert chosen["celebrate"] == []


def test_select_scenes_empty_capture_yields_nothing_and_assembly_refuses():
    empty = select_scenes([], DEFAULT_SCENE_DURATIONS, 8.0)
    assert all(v == [] for v in empty.values())
    with pytest.raises(ValueError):
        assemble_gif(Path("./nope"), empty, Path("./out.gif"), "t", 1.5)


# ------------------------------------------------------------------ assembly
def _tiny_frames(frames_dir: Path, n: int, w: int = 320, h: int = 180) -> None:
    from PIL import Image, ImageDraw

    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        im = Image.new("RGB", (w, h), (30, 30, 30))
        d = ImageDraw.Draw(im)
        d.rectangle([10, 10, 10 + i * 5, 40], fill=(66, 133, 244))
        im.save(frames_dir / f"{i:04d}.png")


def test_assemble_gif_dimensions_and_duration(tmp_path):
    _tiny_frames(tmp_path / "frames", 12)
    chosen = {
        "problem": [0, 1, 2],
        "mission": [3],
        "investigate": [4, 5],
        "model": [6],
        "check": [7, 8],
        "celebrate": [9, 10, 11],
    }
    res = assemble_gif(
        tmp_path / "frames",
        chosen,
        tmp_path / "out.gif",
        run_id="t-hero",
        speed=1.5,
        gif_width=240,
        gif_fps=8.0,
        poster_out=tmp_path / "poster.png",
    )
    assert res["gif_width"] == 240 and res["gif_height"] == 135  # 16:9 preserved
    assert res["frames"] == 12 and res["duration_s"] == 1.5  # 12 frames / 8 fps
    assert res["gif_bytes"] > 0
    poster = tmp_path / "poster.png"
    assert poster.exists()

    from PIL import Image

    with Image.open(tmp_path / "out.gif") as im:
        assert im.size == (240, 135)
    with Image.open(poster) as im:
        assert im.size == (320, 180)  # poster keeps master resolution


def test_assemble_gif_refuses_empty_selection(tmp_path):
    _tiny_frames(tmp_path / "frames", 3)
    empty = {sc: [] for sc in SCENES}
    with pytest.raises(ValueError):
        assemble_gif(tmp_path / "frames", empty, tmp_path / "out.gif", "t-hero", 1.5)


def test_real_recording_passes_validation():
    """The shipped recording stays a complete accepted run (guards the asset)."""
    rec = json.loads(
        (ROOT / "docs" / "assets" / "last-seat-recording.json").read_text(encoding="utf-8")
    )
    s = validate_recording(rec)
    assert s["run_id"] == "stage-201902"
    assert "edit_file" in s["tool_sequence"]
    assert s["pytest_tails"]["before"].startswith("1 failed")
    assert s["pytest_tails"]["after"].startswith("9 passed")


def test_hero_meta_sidecar_is_clean():
    """Committed metadata: repo-relative paths, honest labels, no secrets."""
    meta_p = ROOT / "docs" / "assets" / "devfest-last-seat-hero.meta.json"
    if not meta_p.exists():  # asset regeneration is reference-machine work
        pytest.skip("hero sidecar not generated on this machine")
    sidecar = json.loads(meta_p.read_text(encoding="utf-8"))
    assert sidecar["source"]["run_id"] == "stage-201902"
    assert len(sidecar["source"]["sha256"]) == 64
    assert sidecar["honesty"]["replay_label"]
    for blob in (json.dumps(sidecar["source"]), json.dumps(sidecar["capture"])):
        assert "Users" not in blob and ":\\\\" not in blob


# ------------------------------------------------- end-to-end (browser only)
def test_end_to_end_capture_from_real_ui(tmp_path):
    """Reference-machine E2E: the REAL stage page, the REAL shipped recording,
    a fast replay, tiny GIF. Asserts the honesty banner assertion ran."""
    e2e = pytest.importorskip("capture_devfest_hero", reason="scripts not importable")
    try:
        import playwright.sync_api as _pw  # noqa: F401
    except ImportError:
        pytest.skip("playwright not installed")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            for ch in ("msedge", "chrome", None):
                try:
                    p.chromium.launch(headless=True, channel=ch).close()
                    break
                except Exception:
                    continue
            else:
                pytest.skip("no local Chromium-family browser channel available")
    except Exception:
        pytest.skip("browser probe failed")

    recording = ROOT / "docs" / "assets" / "last-seat-recording.json"
    port = 8439
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            pytest.skip(f"port {port} busy")

    got = e2e.capture_frames(
        recording,
        tmp_path / "work",
        speed=30.0,  # the point is the pipeline, not the pacing
        port=port,
        fps=6.0,
        hold_s=0.4,
        viewport=(1280, 720),
    )
    assert got["identity_ok"] is True
    assert got["run_id"] == "stage-201902"
    states = json.loads((tmp_path / "work" / "states.json").read_text(encoding="utf-8"))["states"]
    assert states, "capture produced no frames"
    chosen = select_scenes(
        states,
        {
            **DEFAULT_SCENE_DURATIONS,
            "problem": 0.5,
            "investigate": 0.8,
            "model": 0.5,
            "check": 0.5,
            "celebrate": 0.8,
            "mission": 0.4,
        },
        6.0,
    )
    res = assemble_gif(
        tmp_path / "work" / "frames",
        chosen,
        tmp_path / "e2e.gif",
        run_id="stage-201902",
        speed=30.0,
        gif_width=320,
        gif_fps=6.0,
    )
    assert res["gif_bytes"] > 0 and res["frames"] >= 1
