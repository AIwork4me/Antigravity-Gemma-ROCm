"""P1-1: executable-resolution shadowing — documented threat-model fact.

The command POLICY validates the command STRING ('python -m pytest ...'), but
the SDK harness resolves the executable with the OS. On default-config
Windows, both cmd.exe and CreateProcess search the CURRENT DIRECTORY before
PATH — and the workspace Cwd is writable by the agent. A planted
`python.bat` / `pytest.exe` inside the workspace can therefore hijack an
"allowed" command. The policy layer cannot prevent this (it can only
allow/deny, never rewrite the command to an absolute interpreter path).

These tests pin that fact with a harmless sentinel so the limitation stays
visible instead of being re-discovered the hard way. They never run the real
harness and never touch anything outside tmp_path.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import command_is_safe  # noqa: E402


def _no_cwd_search_enabled() -> bool:
    """NoDefaultCurrentDirectoryInExePath (env or registry) disables CWD search."""
    import winreg

    if "NoDefaultCurrentDirectoryInExePath" in __import__("os").environ:
        return True
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Command Processor",
        ) as k:
            winreg.QueryValueEx(k, "NoDefaultCurrentDirectoryInExePath")
            return True
    except OSError:
        return False


def test_cmd_exemark_resolves_cwd_first_with_planted_bat(tmp_path):
    """cmd.exe executes a planted python.bat from the CWD before PATH's
    python.exe on default-config Windows (harmless sentinel proof)."""
    if _no_cwd_search_enabled():
        pytest.skip("NoDefaultCurrentDirectoryInExePath set — CWD search disabled on this host")
    (tmp_path / "python.bat").write_text("@echo SHADOW_SENTINEL_RAN\r\n", encoding="utf-8")
    r = subprocess.run(
        ["cmd", "/c", "python --version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = r.stdout + r.stderr
    assert "SHADOW_SENTINEL_RAN" in combined, (
        f"host no longer CWD-first: update SECURITY.md shadowing note. got: {combined[:200]!r}"
    )


def test_policy_cannot_rewrite_to_absolute_interpreter():
    """The policy is boolean allow/deny: there is no code path that could
    pin the command to an absolute interpreter path, so syntax validation
    must never be described as execution isolation."""
    import inspect

    from config import make_run_command_handler

    src = inspect.getsource(make_run_command_handler)
    assert "return ok" in src  # handler returns a bool only — no rewriting


def test_allowed_command_shape_still_string_validated():
    """What the policy DOES guarantee: the command string is one of the
    enumerated pytest invocations with an in-workspace Cwd."""
    from config import _workspace_norms

    ws = str(ROOT / "demo_workspace")
    args = {
        "CommandLine": "python -m pytest tests/ -q",
        "Cwd": ws,
        "WaitMsBeforeAsync": 1000,
    }
    assert command_is_safe(args, _workspace_norms([ws])) is True
