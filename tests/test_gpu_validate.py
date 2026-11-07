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
        "model_identity": {"status": "PASS"},
        "backend_live": {"status": "PASS", "backend": "ROCm"},
        "discovery_line": {"status": "PASS", "backend": "ROCm"},
        "shared_interference": {"status": "PASS"},
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

    def test_identity_mismatch_fails(self):
        """/api/ps digest that contradicts `ollama list` fails the run."""
        rep = self.BASE | {
            "agent": {"status": "PASS"},
            "model_identity": {"status": "FAIL", "reason": "identity mismatch"},
        }
        assert gv._verdict(rep, no_agent=False) == "FAIL"

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


# ------------------------------------------------- module names (real, not patterns)
class TestModuleNames:
    """T1.2 B-5: the evidence must carry the ACTUAL DLL names from the CSV
    module cell — a trailing-star pattern hit is not proof of any DLL."""

    @staticmethod
    def _fake_tasklist(csv: str):
        return lambda cmd, timeout=90: {"ok": True, "stdout": csv, "stderr": "", "rc": 0}

    def test_real_dll_names_recorded(self, monkeypatch):
        csv = (
            '"映像名称","PID","模块"\r\n"llama-server.exe","200","amdhip64_7.dll, rocblas.dll"\r\n'
        )
        monkeypatch.setattr(gv, "_run", self._fake_tasklist(csv))
        rows = gv.tasklist_modules(["amdhip64*"])
        assert rows == [
            {
                "image": "llama-server.exe",
                "pid": "200",
                "modules": ["amdhip64_7.dll", "rocblas.dll"],
            }
        ]

    def test_pattern_never_recorded_as_module(self, monkeypatch):
        csv = '"映像名称","PID","模块"\r\n"llama-server.exe","200","amdhip64_7.dll"\r\n'
        monkeypatch.setattr(gv, "_run", self._fake_tasklist(csv))
        rows = gv.tasklist_modules(["amdhip64*"])
        assert rows[0]["modules"] == ["amdhip64_7.dll"]
        assert "amdhip64*" not in rows[0]["modules"]

    def test_no_module_cell_fails_closed(self, monkeypatch):
        """A 2-cell row must not fabricate a module name from the pattern."""
        csv = '"映像名称","PID"\r\n"llama-server.exe","200"\r\n'
        monkeypatch.setattr(gv, "_run", self._fake_tasklist(csv))
        rows = gv.tasklist_modules(["amdhip64*"])
        assert rows == [{"image": "llama-server.exe", "pid": "200", "modules": []}]

    def test_na_and_non_dll_dropped(self, monkeypatch):
        csv = '"映像名称","PID","模块"\r\n"llama-server.exe","200","N/A"\r\n'
        monkeypatch.setattr(gv, "_run", self._fake_tasklist(csv))
        rows = gv.tasklist_modules(["amdhip64*"])
        assert rows == [{"image": "llama-server.exe", "pid": "200", "modules": []}]

    def test_localized_banner_yields_no_rows(self, monkeypatch):
        monkeypatch.setattr(
            gv, "_run", self._fake_tasklist("信息: 没有运行的任务匹配指定标准。\r\n")
        )
        assert gv.tasklist_modules(["amdhip64*"]) == []


# ---------------------------------------------------------- model identity
class TestModelIdentity:
    ROW = "gemma4:26b    17ab34cd1234   17 GB   5 days ago"

    def test_digest_extends_list_id(self):
        r = gv.crosscheck_model_identity(self.ROW, "17ab34cd1234" + "f" * 52)
        assert r["status"] == "PASS" and r["id12"] == "17ab34cd1234"

    def test_digest_mismatch_fails(self):
        r = gv.crosscheck_model_identity(self.ROW, "99" * 32)
        assert r["status"] == "FAIL" and "mismatch" in r["reason"]

    def test_row_without_id_blocked(self):
        r = gv.crosscheck_model_identity("no id here", "0" * 64)
        assert r["status"] == "BLOCKED"


# ------------------------------------------------------ runner attribution
class TestRunnerAttribution:
    """T1.2 B-5/3.4: DLL evidence only counts from runners that descend
    from OUR owned server PID; a foreign runner proves nothing."""

    PROCS = [
        {"pid": 100, "ppid": 1, "name": "ollama.exe"},  # our owned server
        {"pid": 200, "ppid": 100, "name": "llama-server.exe"},  # OUR runner
        {"pid": 300, "ppid": 2, "name": "llama-server.exe"},  # foreign runner
    ]
    ROWS = [
        {"image": "llama-server.exe", "pid": "200", "modules": ["amdhip64_7.dll", "rocblas.dll"]},
        {"image": "llama-server.exe", "pid": "300", "modules": ["vulkan-1.dll"]},
    ]

    def test_only_descendant_runner_counts(self, monkeypatch):
        monkeypatch.setattr(gv, "process_snapshot", lambda: self.PROCS)
        r = gv.attribute_runner(100, self.ROWS)
        assert r["status"] == "PASS" and r["backend"] == "ROCm"
        assert r["owned_runner_pids"] == ["200"]

    def test_foreign_runner_only_is_blocked(self, monkeypatch):
        monkeypatch.setattr(gv, "process_snapshot", lambda: self.PROCS)
        rows = [self.ROWS[1]]  # only the foreign vulkan runner observed
        r = gv.attribute_runner(100, rows)
        assert r["status"] == "BLOCKED"

    def test_no_descendant_runner_blocked(self, monkeypatch):
        procs = [self.PROCS[0], self.PROCS[2]]
        monkeypatch.setattr(gv, "process_snapshot", lambda: procs)
        r = gv.attribute_runner(100, self.ROWS)
        assert r["status"] == "BLOCKED" and "descends" in r["reason"]

    def test_cim_unavailable_blocks(self, monkeypatch):
        """No process data -> ownership unprovable -> BLOCKED (fail closed)."""
        monkeypatch.setattr(gv, "process_snapshot", lambda: [])
        r = gv.attribute_runner(100, self.ROWS)
        assert r["status"] == "BLOCKED"

    def test_transitive_descendants(self):
        procs = [
            {"pid": 100, "ppid": 1, "name": "ollama.exe"},
            {"pid": 150, "ppid": 100, "name": "shim.exe"},
            {"pid": 200, "ppid": 150, "name": "llama-server.exe"},
            {"pid": 999, "ppid": 3, "name": "unrelated.exe"},
        ]
        assert gv.descendants_of(100, procs) == {150, 200}

    def test_shared_mode_annotated_unattributed(self, monkeypatch):
        monkeypatch.setattr(gv, "process_snapshot", lambda: self.PROCS)
        r = gv.attribute_runner(None, self.ROWS)
        assert r["status"] == "PASS"  # classify both, but flagged
        assert "not provable" in r["attribution"]


# ------------------------------------------------------ shared-server non-interference
class FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid
        self._alive = True
        self.returncode = None
        self.signals = []

    def poll(self):
        return None if self._alive else (self.returncode if self.returncode is not None else 0)

    def wait(self, timeout=None):
        self._alive = False
        return self.returncode if self.returncode is not None else 0

    def send_signal(self, sig):
        self.signals.append(sig)


class TestSharedNonInterference:
    """T1.2 Phase 1: the default run must never touch the shared server."""

    def _owned(self, tmp_path):
        return gv.OwnedServer(Path("C:/fake/ollama.exe"), tmp_path / "owned-server.log")

    def test_default_start_never_evicts_shared_models(self, monkeypatch, tmp_path):
        """start() with no opt-in must make ZERO requests to the shared
        11434 base (the old code evicted every resident model here)."""
        calls = []

        def fake_http(url, payload=None, timeout=240):
            calls.append(url)
            if url.endswith("/api/version"):
                return {"version": "0.40.14"}
            return {"models": []}

        monkeypatch.setattr(gv, "_http_json", fake_http)
        monkeypatch.setattr(gv, "_port_in_use", lambda port, host="127.0.0.1": False)
        proc = FakeProc()
        monkeypatch.setattr(gv.subprocess, "Popen", lambda *a, **k: proc)
        owned = self._owned(tmp_path)
        owned.start()
        try:
            shared = [u for u in calls if "11434" in u]
            assert shared == [], f"default start() touched the shared server: {shared}"
        finally:
            monkeypatch.setattr(gv, "process_snapshot", lambda: [])
            owned.stop()

    def test_evict_is_explicit_opt_in(self, monkeypatch, tmp_path):
        calls = []

        def fake_http(url, payload=None, timeout=240):
            calls.append((url, payload))
            if url.endswith("/api/ps"):
                return {"models": [{"name": "qwen3:8b"}, {"name": "llama3:8b"}]}
            return {}

        monkeypatch.setattr(gv, "_http_json", fake_http)
        owned = self._owned(tmp_path)
        r = owned.evict_shared_models()
        assert r["status"] == "PASS" and sorted(r["evicted"]) == ["llama3:8b", "qwen3:8b"]
        evictions = [(u, p) for u, p in calls if u.endswith("/api/generate")]
        assert all(p.get("keep_alive") == 0 for _, p in evictions)
        assert len(evictions) == 2  # per model — "*" is not a valid tag

    def test_evict_survives_unreachable_shared(self, monkeypatch, tmp_path):
        def boom(url, payload=None, timeout=240):
            raise OSError("refused")

        monkeypatch.setattr(gv, "_http_json", boom)
        r = self._owned(tmp_path).evict_shared_models()
        assert r["status"] == "BLOCKED"

    def test_port_conflict_blocks_without_killing(self, monkeypatch, tmp_path):
        """An occupied OWN_PORT means someone else owns it: BLOCKED, and we
        never spawn, never kill, never probe their server."""
        spawns, kills = [], []

        monkeypatch.setattr(gv, "_port_in_use", lambda port, host="127.0.0.1": True)
        monkeypatch.setattr(
            gv.subprocess,
            "Popen",
            lambda *a, **k: spawns.append(a) or FakeProc(),
        )

        def fake_run(cmd, timeout=90):
            if cmd[:1] == ["taskkill"]:
                kills.append(cmd)
            return {"ok": True, "stdout": "", "stderr": "", "rc": 0}

        monkeypatch.setattr(gv, "_run", fake_run)
        owned = self._owned(tmp_path)
        with pytest.raises(gv.PortOwnedError):
            owned.start()
        assert spawns == [] and kills == []

    def test_start_failure_still_redacts_and_reaps(self, monkeypatch, tmp_path):
        """Fast-death path: serve exits immediately -> RuntimeError path in
        main() calls stop(); log gets closed and redacted, runners reaped."""
        owned = self._owned(tmp_path)
        monkeypatch.setattr(gv, "_port_in_use", lambda port, host="127.0.0.1": False)
        proc = FakeProc()
        proc._alive = False  # poll() != None -> "exited rc" branch
        proc.returncode = 1
        monkeypatch.setattr(gv.subprocess, "Popen", lambda *a, **k: proc)
        with pytest.raises(RuntimeError, match="exited"):
            owned.start()
        reaped = []

        def fake_run(cmd, timeout=90):
            if cmd[:1] == ["taskkill"]:
                reaped.append(cmd)
            return {"ok": True, "stdout": "", "stderr": "", "rc": 0}

        monkeypatch.setattr(gv, "_run", fake_run)
        monkeypatch.setattr(
            gv,
            "process_snapshot",
            lambda: [
                {"pid": 555, "ppid": proc.pid, "name": "llama-server.exe"},  # orphan of OURS
                {"pid": 666, "ppid": 999, "name": "llama-server.exe"},  # someone else's
            ],
        )
        owned.stop()
        # only the orphan UNDER OUR PID was targeted, by PID — never by name
        assert reaped == [["taskkill", "/F", "/PID", "555"]]

    def test_teardown_targets_only_own_pid_tree(self, monkeypatch, tmp_path):
        kills = []
        monkeypatch.setattr(gv, "_port_in_use", lambda port, host="127.0.0.1": False)
        proc = FakeProc(pid=777)

        def refuse_break(sig):  # force the taskkill fallback path
            raise OSError("not attached to this console")

        proc.send_signal = refuse_break
        monkeypatch.setattr(gv.subprocess, "Popen", lambda *a, **k: proc)
        monkeypatch.setattr(gv, "_http_json", lambda url, payload=None, timeout=240: {})
        monkeypatch.setattr(gv, "process_snapshot", lambda: [])

        def fake_run(cmd, timeout=90):
            if cmd[:1] == ["taskkill"]:
                kills.append(cmd)
            return {"ok": True, "stdout": "", "stderr": "", "rc": 0}

        monkeypatch.setattr(gv, "_run", fake_run)
        owned = self._owned(tmp_path)
        owned.start()
        owned.stop()
        # exactly one tree-kill of our own PID; nothing image-scoped
        assert kills == [["taskkill", "/F", "/T", "/PID", "777"]]

    def test_stop_is_idempotent(self, monkeypatch, tmp_path):
        kills = []

        def fake_run(cmd, timeout=90):
            if cmd[:1] == ["taskkill"]:
                kills.append(cmd)
            return {"ok": True, "stdout": "", "stderr": "", "rc": 0}

        monkeypatch.setattr(gv, "_run", fake_run)
        monkeypatch.setattr(gv, "process_snapshot", lambda: [])
        monkeypatch.setattr(gv, "_port_in_use", lambda port, host="127.0.0.1": False)
        proc = FakeProc()
        monkeypatch.setattr(gv.subprocess, "Popen", lambda *a, **k: proc)
        monkeypatch.setattr(gv, "_http_json", lambda url, payload=None, timeout=240: {})
        owned = self._owned(tmp_path)
        owned.start()
        owned.stop()
        owned.stop()
        owned.stop()
        assert kills == []  # graceful CTRL_BREAK path — no kill needed at all

    def test_log_redaction_happens_on_every_stop_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gv, "process_snapshot", lambda: [])
        monkeypatch.setattr(
            gv, "_run", lambda cmd, timeout=90: {"ok": True, "stdout": "", "stderr": "", "rc": 0}
        )
        owned = self._owned(tmp_path)
        owned.log_path.write_text(
            r"blob at C:\Users\x\Desktop\Antigravity\.ollama\models\x", encoding="utf-8"
        )
        owned.stop()
        assert "C:\\Users" not in owned.log_path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ run seal
class TestSeal:
    def test_seal_hashes_every_evidence_file(self, tmp_path):
        (tmp_path / "env.json").write_bytes(b'{"overall": "PASS"}')
        sub = tmp_path / "agent"
        sub.mkdir()
        (sub / "summary.json").write_bytes(b"{}")
        seal = gv._seal_run(tmp_path)
        files = seal["files"]
        assert set(files) == {"env.json", "agent/summary.json"}  # seal.json excluded
        assert files["env.json"] == hashlib.sha256(b'{"overall": "PASS"}').hexdigest()
        assert json.loads((tmp_path / "seal.json").read_text(encoding="utf-8"))["files"] == files


# --------------------------------------------- round-1 review fixes (T1.2)
class TestRound1Fixes:
    """Regression tests for the adversarial review R1 findings."""

    # --- F-10: anchored DLL family matching -------------------------------
    @pytest.mark.parametrize(
        ("module", "is_rocm"),
        [
            ("amdhip64_7.dll", True),
            ("amdhip64.dll", True),
            ("libhipblas.dll", True),
            ("rocblas.dll", True),
            ("rocblasLt.dll", False),  # letter-suffixed variant: fail-closed
            ("myrocblas.dll", False),  # substring hit — must NOT count
            ("nvcuda-fake.dll", False),  # hyphenated impersonation
            ("fakeamdhip64.dll", False),
            ("vulkan-1.dll", False),
            ("kernel32.dll", False),
        ],
    )
    def test_dll_family_anchored(self, module, is_rocm):
        assert gv._dll_family(module, gv.ROCM_DLLS) is is_rocm

    def test_cuda_family_anchored(self):
        assert gv._dll_family("cudart64_12.dll", gv.CUDA_DLLS)
        assert not gv._dll_family("nvcuda-fake.dll", gv.CUDA_DLLS)
        assert not gv._dll_family("mycudart.dll", gv.CUDA_DLLS)

    # --- F-11: exact runner image ------------------------------------------
    def test_lookalike_runner_image_is_not_a_runner(self):
        rows = [{"image": "llama-server-evil.exe", "pid": "1", "modules": ["rocblas.dll"]}]
        r = gv.classify_backend(rows)
        assert r["status"] == "BLOCKED"

    def test_lookalike_runner_not_reaped(self, monkeypatch, tmp_path):
        """The orphan sweep must never target llama-server-evil.exe."""
        monkeypatch.setattr(gv, "process_snapshot", lambda: [])
        monkeypatch.setattr(
            gv, "_run", lambda cmd, timeout=90: {"ok": True, "stdout": "", "stderr": "", "rc": 0}
        )
        owned = gv.OwnedServer(Path("C:/fake/ollama.exe"), tmp_path / "log")
        proc = FakeProc(pid=31337)
        monkeypatch.setattr(gv.subprocess, "Popen", lambda *a, **k: proc)
        monkeypatch.setattr(gv, "_http_json", lambda url, payload=None, timeout=240: {})
        monkeypatch.setattr(gv, "_port_in_use", lambda port, host="127.0.0.1": False)
        owned.start()
        monkeypatch.setattr(
            gv,
            "process_snapshot",
            lambda: [
                {"pid": 900, "ppid": 31337, "name": "llama-server-evil.exe"},
                {"pid": 901, "ppid": 31337, "name": "llama-server.exe"},
            ],
        )
        kills = []

        def fake_run(cmd, timeout=90):
            if cmd[:1] == ["taskkill"]:
                kills.append(cmd)
            return {"ok": True, "stdout": "", "stderr": "", "rc": 0}

        monkeypatch.setattr(gv, "_run", fake_run)

        def refuse_break(sig):
            raise OSError("no console")

        proc.send_signal = refuse_break
        owned.stop()
        # only the genuine runner under our PID is reaped (by exact PID)
        assert kills == [
            ["taskkill", "/F", "/T", "/PID", "31337"],
            ["taskkill", "/F", "/PID", "901"],
        ]

    # --- F-7: enforced non-interference ------------------------------------
    @staticmethod
    def _args(shared=False, evict=False):
        import argparse

        return argparse.Namespace(shared_server=shared, evict_shared_models=evict)

    def test_interference_unchanged_passes(self):
        rep = {
            "shared_ps_before": {"status": "PASS", "resident": ["a:1b"]},
            "shared_ps_after": {"status": "PASS", "resident": ["a:1b"]},
        }
        assert gv._shared_interference_gate(rep, self._args())["status"] == "PASS"

    def test_interference_changed_without_optin_fails(self):
        rep = {
            "shared_ps_before": {"status": "PASS", "resident": ["a:1b"]},
            "shared_ps_after": {"status": "PASS", "resident": []},
        }
        r = gv._shared_interference_gate(rep, self._args())
        assert r["status"] == "FAIL" and "fail-closed" in r["reason"]

    def test_interference_changed_with_optin_passes(self):
        rep = {
            "shared_ps_before": {"status": "PASS", "resident": ["a:1b"]},
            "shared_ps_after": {"status": "PASS", "resident": []},
        }
        assert gv._shared_interference_gate(rep, self._args(evict=True))["status"] == "PASS"

    def test_no_shared_server_is_trivially_clean(self):
        rep = {
            "shared_ps_before": {"status": "BLOCKED", "reason": "unreachable"},
            "shared_ps_after": {"status": "BLOCKED", "reason": "unreachable"},
        }
        assert gv._shared_interference_gate(rep, self._args())["status"] == "PASS"

    def test_shared_server_vanished_mid_run_blocks(self):
        rep = {
            "shared_ps_before": {"status": "PASS", "resident": ["a:1b"]},
            "shared_ps_after": {"status": "BLOCKED", "reason": "unreachable"},
        }
        assert gv._shared_interference_gate(rep, self._args())["status"] == "BLOCKED"

    def test_interference_gate_is_a_must_key(self):
        """A run that never recorded the gate cannot be a PASS."""
        base = dict(TestVerdict.BASE | {"agent": {"status": "PASS"}})
        assert gv._verdict(base, no_agent=False) == "PASS"
        broken = dict(base)
        broken.pop("shared_interference")
        assert gv._verdict(broken, no_agent=False) == "BLOCKED"

    # --- F-9: contradictory flags rejected ---------------------------------
    def test_shared_server_plus_evict_rejected(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["gpu_validate.py", "--shared-server", "--evict-shared-models"]
        )
        with pytest.raises(SystemExit) as e:
            gv.main()
        assert e.value.code == 2

    # --- F-2: REPORT.md ships redacted --------------------------------------
    def test_report_md_is_redacted(self, tmp_path):
        rep = dict(TestVerdict.BASE | {"agent": {"status": "PASS"}})
        rep["overall"] = "PASS"
        rep["run_id"] = "gpuval-test"
        rep["scope"] = "full"
        rep["owned_server"] = {
            "status": "BLOCKED",
            "reason": "owned server failed to start: see C:\\Users\\x\\Desktop\\Antigravity\\a.log",
        }
        gv._write(tmp_path, rep)
        text = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
        assert "C:\\Users" not in text and "<repo>" in text
