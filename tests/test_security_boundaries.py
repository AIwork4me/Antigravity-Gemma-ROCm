"""RC1 Phase 1 — Windows tool security boundary test suite.

Red-teams the two independent security boundaries in src/config.py:
  (1) FILE PATH authorization  — _path_in_workspaces / file_pred
  (2) SHELL PROCESS permission — command_is_safe / run_command handler

Uses an isolated temp sandbox with sentinel files only. Never touches real
user data. Run:  python -m pytest tests/test_security_boundaries.py -v
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import _path_in_workspaces, _workspace_norms, command_is_safe  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path: Path):
    """Isolated workspace + outside world with sentinel files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws / "project.py").write_text("print('inside')", encoding="utf-8")
    (outside / "secret_sentinel.txt").write_text("SENTINEL", encoding="utf-8")
    return ws, outside


def fp(args, ws):
    return _path_in_workspaces(args, _workspace_norms([ws]))


# ---------- boundary 1: file path authorization ----------


class TestRelativeAndTraversal:
    def test_relative_path_denied(self, sandbox):
        ws, _ = sandbox
        assert fp({"AbsolutePath": "project.py"}, ws) is False
        assert fp({"AbsolutePath": "tests/x.py"}, ws) is False

    def test_dotdot_backslash_denied(self, sandbox):
        ws, _ = sandbox
        assert fp({"AbsolutePath": str(ws) + "\\..\\secret.txt"}, ws) is False

    def test_dotdot_slash_denied(self, sandbox):
        ws, _ = sandbox
        assert fp({"AbsolutePath": str(ws) + "/../outside/secret.txt"}, ws) is False

    def test_dotdot_deep_denied(self, sandbox):
        ws, _ = sandbox
        deep = str(ws) + "/a/b/../../../../Windows/system32/config.sys"
        assert fp({"AbsolutePath": deep}, ws) is False

    def test_dotdot_case_denied(self, sandbox):
        ws, _ = sandbox
        assert fp({"AbsolutePath": str(ws) + "/../secret.txt"}, ws) is False


class TestCaseInsensitivity:
    def test_inside_uppercase_allowed(self, sandbox):
        ws, _ = sandbox
        upper = str(ws).upper() + "\\PROJECT.PY"
        assert fp({"AbsolutePath": upper}, ws) is True

    def test_outside_lookalike_denied(self, sandbox):
        ws, _ = sandbox
        # a *different* directory that merely contains the workspace name
        fake = str(ws) + "-lookalike\\secret.txt"
        assert fp({"AbsolutePath": fake}, ws) is False

    def test_prefix_trick_denied(self, sandbox):
        ws, _ = sandbox
        # workspace = ...\\demo  vs  ...\\demo_workspace — prefix without separator
        parent = ws.parent
        sibling = parent / (ws.name + "_evil")
        assert fp({"AbsolutePath": str(sibling) + "\\x"}, ws) is False


def _mkjunction(link: Path, target: Path) -> bool:
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode == 0


def _mksymlink(link: Path, target: Path, is_dir: bool) -> bool:
    flag = "/D" if is_dir else ""
    r = subprocess.run(
        ["cmd", "/c", "mklink", flag, str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode == 0


class TestReparsePoints:
    def test_junction_escape(self, sandbox):
        """A junction INSIDE the workspace pointing OUTSIDE must be denied
        after resolve()-based normalization."""
        ws, outside = sandbox
        link = ws / "linkdir"
        assert _mkjunction(link, outside), "junction creation failed"
        target = str(link / "secret_sentinel.txt")
        # lexical check would allow; guard must resolve the junction
        assert fp({"AbsolutePath": target}, ws) is False, "JUNCTION ESCAPE ALLOWED"

    def test_symlink_file_escape(self, sandbox):
        ws, outside = sandbox
        link = ws / "linkfile.txt"
        made = _mksymlink(link, outside / "secret_sentinel.txt", is_dir=False)
        if not made:
            pytest.skip("symlink creation requires dev-mode/admin on this machine")
        assert fp({"AbsolutePath": str(link)}, ws) is False, "SYMLINK ESCAPE ALLOWED"

    def test_junction_to_inside_still_allowed(self, sandbox):
        """Junction inside ws pointing deeper INSIDE ws stays allowed."""
        ws, _ = sandbox
        inner = ws / "nested"
        inner.mkdir()
        link = ws / "oklink"
        assert _mkjunction(link, inner)
        assert fp({"AbsolutePath": str(link / "x.py")}, ws) is True


class TestUncAndExtendedLength:
    def test_unc_denied(self, sandbox):
        ws, _ = sandbox
        assert fp({"AbsolutePath": r"\\server\share\secret.txt"}, ws) is False
        assert fp({"AbsolutePath": r"\\?\UNC\server\share\secret.txt"}, ws) is False

    def test_extended_length_outside_denied(self, sandbox):
        ws, _ = sandbox
        assert fp({"AbsolutePath": r"\\?\C:\Windows\System32\config.sys"}, ws) is False

    def test_extended_length_inside_allowed(self, sandbox):
        ws, _ = sandbox
        long_inside = "\\\\?\\" + str(ws / "project.py")
        assert fp({"AbsolutePath": long_inside}, ws) is True


class TestNonexistentTargets:
    def test_new_file_inside_allowed(self, sandbox):
        ws, _ = sandbox
        assert fp({"AbsolutePath": str(ws / "newdir" / "new.py")}, ws) is True

    def test_nonexistent_parent_traversal_denied(self, sandbox):
        ws, _ = sandbox
        assert fp({"AbsolutePath": str(ws / "noexist" / ".." / ".." / "x")}, ws) is False

    def test_no_path_args_denied(self, sandbox):
        ws, _ = sandbox
        assert fp({"Contents": "data"}, ws) is False  # nothing to validate -> deny


# ---------- boundary 2: shell process permission ----------


def safe(cmd, cwd=None, ws=None):
    args = {"CommandLine": cmd}
    if cwd is not None:
        args["Cwd"] = cwd
    norms = _workspace_norms([ws]) if ws else None
    return command_is_safe(args, norms)


class TestShellWhitelist:
    @pytest.mark.parametrize(
        "cmd",
        [
            "python -m pytest tests/ -q",
            "pytest -q",
            "pytest -q tests/test_stats.py",
            "python -m pytest -q",
            "pytest tests/test_stats.py::test_mean -q --tb=short",
        ],
    )
    def test_allowed(self, cmd, sandbox):
        ws, _ = sandbox
        assert safe(cmd, cwd=str(ws), ws=ws) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls",
            "dir",
            "whoami",
            "echo hello",
            "python -c 'print(1)'",  # arbitrary code
            "python -c import demo_workspace",  # code eval, no metachars
            "python -m http.server 8000",  # network server
            "python -m pip install requests",  # package install
            "powershell -c Get-ChildItem",
            "curl http://x",
            "format C:",
            "shutdown /r",
            "python stats_utils.py",  # arbitrary script (was allowed pre-T1)
            "pytest_evil tests/",  # prefix lookalike
            "python",  # bare interpreter
            "python -3 -m pytest tests/",  # unknown python flag
            "python -m pytest -m slow tests/",  # unknown pytest flag
        ],
    )
    def test_denied(self, cmd, sandbox):
        ws, _ = sandbox
        assert safe(cmd, cwd=str(ws), ws=ws) is False


class TestStructuredSchema:
    """Fail-closed behavior on the run_command ARGUMENT SCHEMA itself."""

    def _ok_args(self, ws):
        return {"CommandLine": "pytest -q", "Cwd": str(ws), "WaitMsBeforeAsync": 1000}

    def test_unknown_key_denied(self, sandbox):
        ws, _ = sandbox
        args = self._ok_args(ws) | {"Script": "evil.py"}
        assert command_is_safe(args, _workspace_norms([ws])) is False

    def test_ambiguous_command_keys_denied(self, sandbox):
        ws, _ = sandbox
        args = self._ok_args(ws) | {"command": "pytest -q"}
        assert command_is_safe(args, _workspace_norms([ws])) is False

    def test_missing_command_key_denied(self, sandbox):
        ws, _ = sandbox
        assert command_is_safe({"Cwd": str(ws)}, _workspace_norms([ws])) is False

    def test_cwd_alias_accepted(self, sandbox):
        ws, _ = sandbox
        args = {"CommandLine": "pytest -q", "WorkingDirectory": str(ws)}
        assert command_is_safe(args, _workspace_norms([ws])) is True

    def test_cwd_required(self, sandbox):
        ws, _ = sandbox
        assert command_is_safe({"CommandLine": "pytest -q"}, _workspace_norms([ws])) is False

    @pytest.mark.parametrize("wait", ["1000", -1, 10**9, True])
    def test_bad_wait_ms_denied(self, sandbox, wait):
        ws, _ = sandbox
        args = self._ok_args(ws) | {"WaitMsBeforeAsync": wait}
        assert command_is_safe(args, _workspace_norms([ws])) is False

    @pytest.mark.parametrize("wait", [float("nan"), float("inf"), float("-inf")])
    def test_nonfinite_wait_ms_denied(self, sandbox, wait):
        """NaN slips past plain range comparisons (review F-2)."""
        ws, _ = sandbox
        args = self._ok_args(ws) | {"WaitMsBeforeAsync": wait}
        assert command_is_safe(args, _workspace_norms([ws])) is False

    def test_non_dict_args_denied(self, sandbox):
        ws, _ = sandbox
        assert command_is_safe("pytest -q", _workspace_norms([ws])) is False


class TestTokenizationTricks:
    def test_unterminated_quote_denied(self, sandbox):
        ws, _ = sandbox
        assert safe('python -m pytest tests/ -q"', cwd=str(ws), ws=ws) is False

    @pytest.mark.parametrize(
        "cmd",
        [
            'python -m "pytest" tests/ -q',
            "py' th'on -m pytest tests/",
            'py"th"on -m pytest tests/ -q',  # dequoted argv[0] aliasing (review F-3)
            'py"te"st -q',
        ],
    )
    def test_any_quoting_denied(self, sandbox, cmd):
        """The enumerated commands never need quotes — any quote rejects."""
        ws, _ = sandbox
        assert safe(cmd, cwd=str(ws), ws=ws) is False

    def test_unicode_homoglyph_denied(self, sandbox):
        ws, _ = sandbox
        assert safe("python -m pytest tests\u2215 -q", cwd=str(ws), ws=ws) is False

    def test_absolute_test_path_outside_denied(self, sandbox):
        ws, outside = sandbox
        assert safe(f"python -m pytest {outside}", cwd=str(ws), ws=ws) is False

    def test_absolute_test_path_inside_allowed(self, sandbox):
        ws, _ = sandbox
        assert safe(f"python -m pytest {ws}\\tests", cwd=str(ws), ws=ws) is True

    def test_py_alias_requires_3(self, sandbox):
        ws, _ = sandbox
        assert safe("py -3 -m pytest tests/ -q", cwd=str(ws), ws=ws) is True
        assert safe("py -3.12 -m pytest tests/", cwd=str(ws), ws=ws) is False
        assert safe("py -m pytest tests/", cwd=str(ws), ws=ws) is False

    def test_exe_absolute_path_denied(self, sandbox, tmp_path):
        ws, _ = sandbox
        fake = (tmp_path / "python.exe").as_posix()
        assert safe(f"{fake} -m pytest tests/ -q", cwd=str(ws), ws=ws) is False


class TestShellMetacharInjection:
    @pytest.mark.parametrize(
        "cmd",
        [
            "python -m pytest tests/ -q; whoami",
            "python -m pytest tests/ -q && calc",
            "python -m pytest & curl http://evil/x",
            "python -m pytest tests/ -q | findstr secret",
            "python -m pytest tests/ -q > C:/stolen.txt",
            "python -m pytest tests/ -q < input.txt",
            "python stats_utils.py `whoami`",
            "python -m pytest tests/ -q $(whoami)",
            "python -m pytest tests/ -q\ncalc",
        ],
    )
    def test_injection_denied(self, cmd):
        assert safe(cmd) is False, f"INJECTION ALLOWED: {cmd}"


class TestCwdBoundary:
    def test_cwd_outside_denied(self, sandbox):
        ws, outside = sandbox
        assert safe("python -m pytest tests/ -q", cwd=str(outside), ws=ws) is False

    def test_cwd_inside_allowed(self, sandbox):
        ws, _ = sandbox
        assert safe("python -m pytest tests/ -q", cwd=str(ws), ws=ws) is True

    def test_cwd_lookalike_denied(self, sandbox):
        ws, _ = sandbox
        assert safe("python -m pytest tests/ -q", cwd=str(ws) + "-evil", ws=ws) is False

    def test_cwd_without_norms_conservative_deny(self, sandbox):
        ws, _ = sandbox
        # no workspace context provided -> cannot validate Cwd -> deny
        assert safe("python -m pytest tests/ -q", cwd=str(ws)) is False
