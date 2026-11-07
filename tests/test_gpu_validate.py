"""Negative + positive tests for scripts/gpu_validate.py (no GPU needed).

Mission T1.1 P0-2: every deliberately-invalid condition must produce FAIL or
BLOCKED — never PASS. All fixtures are synthetic; nothing here talks to
Ollama or executes injected commands.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gpu_validate as gv  # noqa: E402

GOOD_DIGEST = "0" * 63 + "1"  # 64 hex


# ------------------------------------------------------------- model tag gate
class TestModelTag:
    @pytest.mark.parametrize(
        "tag",
        [
            "gemma4:26b",
            "qwen3:8b",
            "llama3.1:8b-instruct-q4",
            "org/model:tag",
        ],
    )
    def test_valid_tags(self, tag):
        ok, _ = gv.validate_model_tag(tag)
        assert ok

    @pytest.mark.parametrize(
        "tag",
        [
            "",
            "evil; calc",
            "evil && whoami",
            "x | y",
            "a`b",
            "$(whoami)",
            "line1\nline2",
            "gemma4:26b\n",  # trailing newline: '$' would accept, \Z does not (F-5)
            " spaced ",
            "-leading-dash",
            "with space",
            "x" * 200,
        ],
    )
    def test_invalid_tags(self, tag):
        ok, _ = gv.validate_model_tag(tag)
        assert not ok


# ---------------------------------------------------------------- ps entries
def _ps(tag="gemma4:26b", digest=GOOD_DIGEST, size=100, vram=100):
    return {"models": [{"name": tag, "digest": digest, "size": size, "size_vram": vram}]}


class TestPsEntry:
    def test_good(self):
        r = gv.check_ps_entry(_ps(), "gemma4:26b")
        assert r["status"] == "PASS" and r["digest"] == GOOD_DIGEST

    def test_wrong_tag_resident(self):
        r = gv.check_ps_entry(_ps(tag="other:1b"), "gemma4:26b")
        assert r["status"] == "FAIL"

    def test_nothing_resident(self):
        r = gv.check_ps_entry({"models": []}, "gemma4:26b")
        assert r["status"] == "FAIL"

    def test_short_digest_rejected(self):
        r = gv.check_ps_entry(_ps(digest="001e5dafc3c7"), "gemma4:26b")
        assert r["status"] == "FAIL" and "sha256" in r["reason"]

    def test_partial_vram_rejected(self):
        r = gv.check_ps_entry(_ps(size=100, vram=40), "gemma4:26b")
        assert r["status"] == "FAIL" and "GPU-resident" in r["reason"]

    def test_malformed_payload_blocked(self):
        r = gv.check_ps_entry(None, "gemma4:26b")
        assert r["status"] == "BLOCKED"


# ------------------------------------------------------------ backend classify
class TestClassifyBackend:
    def test_rocm_from_runner_dlls(self):
        rows = [
            {"image": "llama-server.exe", "pid": "1", "modules": ["rocblas.dll", "libhipblas.dll"]},
            {"image": "ollama.exe", "pid": "2", "modules": ["vulkan-1.dll"]},  # loader, ignored
        ]
        r = gv.classify_backend(rows)
        assert r["status"] == "PASS" and r["backend"] == "ROCm"

    def test_vulkan_runner_is_vulkan(self):
        rows = [{"image": "llama-server.exe", "pid": "1", "modules": ["vulkan-1.dll"]}]
        r = gv.classify_backend(rows)
        assert r["backend"] == "Vulkan"

    def test_cuda_runner(self):
        rows = [{"image": "llama-server.exe", "pid": "1", "modules": ["cudart64_12.dll"]}]
        r = gv.classify_backend(rows)
        assert r["backend"] == "CUDA"

    def test_main_ollama_vulkan_loader_is_not_evidence(self):
        rows = [{"image": "ollama.exe", "pid": "9", "modules": ["vulkan-1.dll"]}]
        r = gv.classify_backend(rows)
        assert r["status"] == "BLOCKED"

    def test_no_runner_blocked(self):
        assert gv.classify_backend([])["status"] == "BLOCKED"

    def test_runner_without_backend_dlls_blocked(self):
        rows = [{"image": "llama-server.exe", "pid": "1", "modules": ["kernel32.dll"]}]
        assert gv.classify_backend(rows)["status"] == "BLOCKED"


# ------------------------------------------------------------ discovery lines
GOOD_LINE = 'time=2026-11-07T20:00:00+08:00 level=INFO source=types.go:32 msg="inference compute" id=0 library=ROCm compute=gfx1151 name=ROCm0 description="AMD Radeon(TM) 8060S Graphics"'


class TestDiscoveryLine:
    def test_good_rocm(self):
        r = gv.parse_inference_compute(GOOD_LINE)
        assert r["status"] == "PASS"
        assert r["backend"] == "ROCm" and r["compute"] == "gfx1151"
        assert "8060S" in r["gpu_name"]

    def test_vulkan_line_identified(self):
        line = GOOD_LINE.replace("library=ROCm", "library=Vulkan")
        assert gv.parse_inference_compute(line)["backend"] == "Vulkan"

    def test_missing_line_blocked(self):
        assert gv.parse_inference_compute("unrelated log")["status"] == "BLOCKED"

    def test_missing_compute_arch_blocked(self):
        line = GOOD_LINE.replace("compute=gfx1151 ", "")
        r = gv.parse_inference_compute(line)
        assert r["status"] == "BLOCKED" and "compute" in r["reason"]

    def test_unknown_backend_blocked(self):
        line = GOOD_LINE.replace("library=ROCm", "library=Exotic")
        assert gv.parse_inference_compute(line)["status"] == "BLOCKED"


# ------------------------------------------------------------- agent chain
def _hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_chain(tmp_path: Path, *, red=True, green=True, mutate=None, files=None):
    """Build a synthetic agent run dir; `mutate`/`files` corrupt it on demand."""
    ws = tmp_path / "ws"
    ws.mkdir()
    bug, fix = (
        b"def median(xs):\n    return xs[0]  # BUG\n",
        b"def median(xs):\n    return xs[len(xs) // 2]\n",
    )
    (ws / "stats_utils.py").write_bytes(fix if green else bug)
    ev = tmp_path / "run-x"
    ev.mkdir()
    before = {"stats_utils.py": {"sha256": _hash(bug), "bytes": len(bug)}}
    after = {"stats_utils.py": {"sha256": _hash(fix), "bytes": len(fix)}}
    data = {
        "summary.json": {
            "run_id": "x",
            "bug_present_before": red,
            "bug_fixed_after": green,
            "files_changed": ["stats_utils.py"] if green else [],
            "elapsed_s": 12.0,
        },
        "pytest_before.json": {"exit_code": 1 if red else 0},
        "pytest_after.json": {"exit_code": 0 if green else 1},
        "tool_calls.json": [{"name": "view_file", "args": "{}"}],
        "policy_audit.txt": "run_command decision=ALLOW",
        "agent_reply.txt": "fixed",
        "mission.txt": "fix the bug",
        "workspace_before.json": before,
        "workspace_after.json": after,
        "git-diff.patch": "+ fix\n",
    }
    for name, obj in data.items():
        (ev / name).write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    if mutate:
        mutate(ev)
    return ev, ws


class TestAgentChain:
    def test_good_chain_passes_and_rechecks_disk(self, tmp_path):
        ev, ws = _make_chain(tmp_path)
        r = gv.validate_agent_chain(ev, ws)
        assert r["status"] == "PASS" and r["files_changed"] == ["stats_utils.py"]

    def test_no_red_before_fails(self, tmp_path):
        ev, ws = _make_chain(tmp_path, red=False)
        r = gv.validate_agent_chain(ev, ws)
        assert r["status"] == "FAIL" and any("RED" in x for x in r["reasons"])

    def test_not_green_after_fails(self, tmp_path):
        ev, ws = _make_chain(tmp_path, green=False)
        assert gv.validate_agent_chain(ev, ws)["status"] == "FAIL"

    def test_files_changed_mismatch_fails(self, tmp_path):
        def swap(ev):
            s = json.loads((ev / "summary.json").read_text(encoding="utf-8"))
            s["files_changed"] = ["other.py"]
            (ev / "summary.json").write_text(json.dumps(s), encoding="utf-8")

        ev, ws = _make_chain(tmp_path, mutate=swap)
        r = gv.validate_agent_chain(ev, ws)
        assert r["status"] == "FAIL" and any("SHA256-map diff" in x for x in r["reasons"])

    def test_disk_hash_drift_fails(self, tmp_path):
        """A copied historical PASS whose after-state no longer matches disk."""
        ev, ws = _make_chain(tmp_path)
        (ws / "stats_utils.py").write_bytes(b"tampered")
        r = gv.validate_agent_chain(ev, ws)
        assert r["status"] == "FAIL" and any("on disk" in x for x in r["reasons"])

    def test_missing_file_blocked(self, tmp_path):
        ev, ws = _make_chain(tmp_path)
        (ev / "pytest_after.json").unlink()
        assert gv.validate_agent_chain(ev, ws)["status"] == "BLOCKED"

    def test_malformed_json_blocked(self, tmp_path):
        ev, ws = _make_chain(tmp_path)
        (ev / "summary.json").write_text("{not json", encoding="utf-8")
        assert gv.validate_agent_chain(ev, ws)["status"] == "BLOCKED"

    def test_no_tool_calls_fails(self, tmp_path):
        ev, ws = _make_chain(tmp_path)
        (ev / "tool_calls.json").write_text("[]", encoding="utf-8")
        assert gv.validate_agent_chain(ev, ws)["status"] == "FAIL"

    def test_unsafe_changed_key_fails(self, tmp_path):
        def evil(ev):
            for name, sha in (
                ("workspace_before.json", _hash(b"before")),
                ("workspace_after.json", _hash(b"after")),
            ):
                d = json.loads((ev / name).read_text(encoding="utf-8"))
                d["C:/evil/x.py"] = {"sha256": sha, "bytes": 6}
                (ev / name).write_text(json.dumps(d), encoding="utf-8")
            s = json.loads((ev / "summary.json").read_text(encoding="utf-8"))
            s["files_changed"] = ["C:/evil/x.py", "stats_utils.py"]
            (ev / "summary.json").write_text(json.dumps(s), encoding="utf-8")

        ev, ws = _make_chain(tmp_path, mutate=evil)
        r = gv.validate_agent_chain(ev, ws)
        assert r["status"] == "FAIL" and any("safe relative" in x for x in r["reasons"])

    def test_summary_flags_lies_fail(self, tmp_path):
        def lie(ev):
            s = json.loads((ev / "summary.json").read_text(encoding="utf-8"))
            s["bug_fixed_after"] = False
            (ev / "summary.json").write_text(json.dumps(s), encoding="utf-8")

        ev, ws = _make_chain(tmp_path, mutate=lie)
        assert gv.validate_agent_chain(ev, ws)["status"] == "FAIL"

    def test_gutting_the_tests_is_not_a_fix(self, tmp_path):
        """Green by weakening the workspace's own tests -> FAIL (review F-3)."""
        ws = tmp_path / "ws"
        good, gutted = b"assert median([1, 2, 3]) == 2\n", b"assert True\n"
        ev = tmp_path / "run-gut"
        ev.mkdir()
        (ws / "tests").mkdir(parents=True)
        (ws / "tests" / "test_stats.py").write_bytes(gutted)  # on-disk == after
        (ws / "stats_utils.py").write_bytes(b"def median(xs):\n    return xs[0]  # BUG\n")
        before = {
            "stats_utils.py": {"sha256": _hash(b"BUG\n"), "bytes": 4},
            "tests/test_stats.py": {"sha256": _hash(good), "bytes": len(good)},
        }
        after = {
            "stats_utils.py": {"sha256": _hash(b"BUG\n"), "bytes": 4},  # bug NOT fixed
            "tests/test_stats.py": {"sha256": _hash(gutted), "bytes": len(gutted)},
        }
        payload = {
            "summary.json": {
                "run_id": "gut",
                "bug_present_before": True,
                "bug_fixed_after": True,  # self-attested lie
                "files_changed": ["tests/test_stats.py"],
                "elapsed_s": 5.0,
            },
            "pytest_before.json": {"exit_code": 1},
            "pytest_after.json": {"exit_code": 0},  # green because tests gutted
            "tool_calls.json": [{"name": "edit_file"}],
            "policy_audit.txt": "x",
            "agent_reply.txt": "x",
            "mission.txt": "x",
            "workspace_before.json": before,
            "workspace_after.json": after,
            "git-diff.patch": "-assert median\n+assert True\n",
        }
        for name, obj in payload.items():
            (ev / name).write_text(
                obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8"
            )
        r = gv.validate_agent_chain(ev, ws)
        assert r["status"] == "FAIL"
        assert any("protected path" in x or "nothing outside" in x for x in r["reasons"])

    def test_deleting_the_tests_is_not_a_fix(self, tmp_path):
        def delete_tests(ev):
            d = json.loads((ev / "workspace_after.json").read_text(encoding="utf-8"))
            d.pop("tests/test_stats.py", None)
            (ev / "workspace_after.json").write_text(json.dumps(d), encoding="utf-8")
            s = json.loads((ev / "summary.json").read_text(encoding="utf-8"))
            s["files_changed"] = ["stats_utils.py", "tests/test_stats.py"]
            (ev / "summary.json").write_text(json.dumps(s), encoding="utf-8")

        ev, ws = _make_chain(tmp_path, mutate=delete_tests)
        r = gv.validate_agent_chain(ev, ws)
        assert r["status"] == "FAIL"

    @pytest.mark.parametrize("which", ["before", "after"])
    def test_string_exit_codes_rejected(self, tmp_path, which):
        """Forged evidence: exit codes must be ints, not '0' strings (F-8)."""

        def forge(ev):
            name = f"pytest_{which}.json"
            d = json.loads((ev / name).read_text(encoding="utf-8"))
            d["exit_code"] = str(d.get("exit_code"))
            (ev / name).write_text(json.dumps(d), encoding="utf-8")

        ev, ws = _make_chain(tmp_path, mutate=forge)
        assert gv.validate_agent_chain(ev, ws)["status"] == "FAIL"


# ------------------------------------------------------------------ verdicts
class TestVerdict:
    BASE = {
        "model_in_store": {"status": "PASS"},
        "live_probe": {"status": "PASS"},
        "residency": {"status": "PASS"},
        "backend_live": {"status": "PASS", "backend": "ROCm"},
        "discovery_line": {"status": "PASS", "backend": "ROCm"},
    }

    def test_full_pass(self):
        assert gv._verdict(self.BASE | {"agent": {"status": "PASS"}}, no_agent=False) == "PASS"

    def test_any_fail_is_fail(self):
        rep = self.BASE | {"agent": {"status": "FAIL"}, "residency": {"status": "PASS"}}
        assert gv._verdict(rep, no_agent=False) == "FAIL"

    def test_blocked_link_blocks(self):
        rep = self.BASE | {"agent": {"status": "PASS"}, "discovery_line": {"status": "BLOCKED"}}
        assert gv._verdict(rep, no_agent=False) == "BLOCKED"

    def test_wrong_backend_discovery(self):
        rep = self.BASE | {
            "agent": {"status": "PASS"},
            "discovery_line": {"status": "FAIL", "reason": "shows Vulkan"},
        }
        assert gv._verdict(rep, no_agent=False) == "FAIL"

    def test_missing_gate_key_blocks(self):
        """A missing/typo'd must-key is NOT success (review F-4)."""
        rep = dict(self.BASE)
        rep.pop("residency")
        assert gv._verdict(rep | {"agent": {"status": "PASS"}}, no_agent=False) == "BLOCKED"

    def test_no_agent_never_full_pass(self):
        """Env-probe scope gets an explicit ENV_PROBE_* label, never PASS."""
        rep = dict(self.BASE)
        assert gv._verdict(rep, no_agent=True) == "ENV_PROBE_PASS"
        assert gv._verdict(rep, no_agent=True) != "PASS"
        blocked = dict(self.BASE) | {"discovery_line": {"status": "BLOCKED"}}
        assert gv._verdict(blocked, no_agent=True) == "ENV_PROBE_BLOCKED"
        failed = dict(self.BASE) | {"residency": {"status": "FAIL"}}
        assert gv._verdict(failed, no_agent=True) == "FAIL"


class TestDllPatternSyntax:
    def test_patterns_are_legal_tasklist_syntax(self):
        """tasklist /m forbids characters after '*' — mid-string wildcards
        silently error and were invisible until review T1.1 F-1."""
        for pat in gv.TASKLIST_DLL_PATTERNS:
            assert "*" not in pat[:-1] or pat.endswith("*"), f"illegal pattern: {pat}"
            assert re.fullmatch(r"[A-Za-z0-9_.*-]+", pat), pat

    def test_probe_errors_surface(self):
        """A dead probe must be visible in the rows, never silently absent."""
        rows = gv.tasklist_modules(["definitely*bad*pattern"])
        assert any(r["image"] == "__probe_errors__" for r in rows)
