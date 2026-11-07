"""Configuration for the Antigravity-Gemma-ROCm local agent.

Builds a locked-down LocalOpenAIAgentConfig:
- model served by local Ollama (OpenAI-compatible endpoint)
- file tools (view/create/edit) allowed ONLY inside the demo workspace,
  enforced by path predicates on the `AbsolutePath` tool argument
- run_command gated by a strict whitelist handler (pytest / python only,
  inside the demo workspace) instead of allow_all()

Tool arg schemas observed live on google-antigravity 0.1.23 (Windows):
  view_file/create_file/edit_file -> {'AbsolutePath': 'C:/...'}
  run_command                     -> {'CommandLine': str, 'Cwd': str, 'WaitMsBeforeAsync': int}
Note: policy.workspace_only() in 0.1.23 unconditionally DENIES all file tools
(it ignores its workspaces arg), so we implement restriction via allow+when.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from google.antigravity import LocalOpenAIAgentConfig
from google.antigravity.hooks import policy
from google.antigravity.types import ToolCall

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Model selection: AGENT_MODEL preferred, legacy GEMMA_MODEL kept as fallback.
DEFAULT_MODEL = os.environ.get("AGENT_MODEL") or os.environ.get("GEMMA_MODEL") or "gemma4:26b"
DEFAULT_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
DEMO_WORKSPACE = PROJECT_ROOT / "demo_workspace"

FILE_TOOLS = (
    "view_file",
    "create_file",
    "edit_file",
    # discovery tools (mission step 1: list workspace files)
    "list_directory",
    "find_file",
    "search_directory",
    # models trained on Gemini-CLI-style harnesses often emit this name
    "replace_file_content",
)

# Commands the agent may run without a human: pytest and python, strictly
# scoped to the demo workspace / its modules. Everything else is denied+logged.
_SAFE_CMD_PREFIXES = ("pytest", "py.test", "python", "py -3")
_BLOCKED_SUBSTRINGS = (
    "..",
    "rm ",
    "del ",
    "remove-item",
    "format",
    "shutdown",
    "reg ",
    "powershell",
    "curl",
    "wget",
    "invoke-",
    "iwr",
    "start-process",
    "cmd /c",
    "bash",
    "git",
)


def _norm(p: str) -> str:
    """Canonicalize a path for workspace comparison.

    - strips \\\\?\\ and \\\\?\\UNC\\ extended-length prefixes
    - collapses '..' segments lexically
    - RESOLVES junctions/symlinks (reparse points) along the existing portion
      of the path, so a link inside the workspace that points outside is
      normalized to its real target before comparison (defense in depth:
      lexical-only normalization was defeated by junction escape, found in
      RC1 security review)
    - lowercases (Windows paths are case-insensitive)
    Relative inputs resolve against CWD and will (correctly) not match.
    """
    s = str(p)
    if s.startswith("\\\\?\\UNC\\"):
        s = "\\\\" + s[8:]
    elif s.startswith("\\\\?\\"):
        s = s[4:]
    try:
        resolved = Path(s).resolve(strict=False)
        s = str(resolved)
    except (OSError, ValueError):
        s = os.path.normpath(s)
    return s.replace("\\", "/").rstrip("/").lower()


def _workspace_norms(workspaces: Sequence[Path | str]) -> list[str]:
    return [_norm(str(Path(w).resolve())) for w in workspaces]


def _looks_like_abs_path(v: str) -> bool:
    return bool(v) and (
        v[1:3] == ":\\" or v[1:3] == ":/" or v.startswith("//") or v.startswith("\\\\")
    )


def _path_in_workspaces(args, workspace_norms: list[str]) -> bool:
    """Predicate for file tools: True iff every path-like argument stays inside a workspace.

    Handles the varying arg schemas across tool names/versions:
    view_file {'AbsolutePath'|'file_path'}, edit {'Path'|'Target'}, etc.
    """
    if not isinstance(args, dict):
        return False
    candidates: list[str] = []
    for k, v in args.items():
        if not isinstance(v, str):
            continue
        kl = k.lower()
        if "path" in kl or kl in ("target", "file", "filename", "directory", "dir"):
            candidates.append(v)
        elif _looks_like_abs_path(v):
            candidates.append(v)
    if not candidates:
        return False  # no path argument to validate -> conservative deny
    for raw in candidates:
        n = _norm(raw)
        if not any(n == w or n.startswith(w + "/") for w in workspace_norms):
            return False
    return True


def _get_command(args) -> str:
    if isinstance(args, dict):
        for k in ("CommandLine", "CommandLineJson", "command", "cmd", "Command"):
            if k in args:
                return str(args[k])
        return ""
    return str(args)


# Shell metacharacters: any occurrence rejects the command outright. The
# whitelisted commands (pytest / python on workspace modules) never need
# them; allowing them would enable command chaining/substitution injection.
_SHELL_METACHARS = (";", "&", "|", ">", "<", "`", "(", ")", "$", "\n", "\r", "^", "%")


def command_is_safe(tc_or_args, workspace_norms: list[str] | None = None) -> bool:
    """Whitelist predicate for run_command tool calls (shell process boundary).

    Allows only pytest/python invocations that reference the demo workspace or
    its modules, with NO shell metacharacters, and (when provided in args) a
    Cwd located inside the workspace. This is a strictly validated allow rule,
    NOT allow_all().
    """
    if isinstance(tc_or_args, ToolCall) or hasattr(tc_or_args, "args"):
        args = tc_or_args.args
    else:
        args = tc_or_args
    cmd = _get_command(args).strip()
    cmd_l = cmd.lower()
    if not cmd_l:
        return False
    if any(ch in cmd for ch in _SHELL_METACHARS):
        return False
    if not cmd_l.startswith(_SAFE_CMD_PREFIXES):
        return False
    if not any(h in cmd_l for h in ("demo_workspace", "stats", "pytest")):
        return False
    if any(b in cmd_l for b in _BLOCKED_SUBSTRINGS):
        return False
    # working directory boundary: if the tool call pins a Cwd, it must live
    # inside a workspace (guards against redirecting relative file access)
    if isinstance(args, dict) and args.get("Cwd"):
        cwd_n = _norm(str(args["Cwd"]))
        if workspace_norms is None:
            return False  # cannot validate -> conservative deny
        if not any(cwd_n == w or cwd_n.startswith(w + "/") for w in workspace_norms):
            return False
    return True


def make_run_command_handler(
    audit_log: list[str] | None = None, workspace_norms: list[str] | None = None
):
    """Returns an AskUserHandler that approves only whitelisted commands."""

    def handler(tc: ToolCall) -> bool:
        ok = command_is_safe(tc, workspace_norms)
        if audit_log is not None:
            try:
                audit_log.append(
                    f"run_command decision={'ALLOW' if ok else 'DENY'} args={dict(tc.args)!r}"
                )
            except Exception:
                audit_log.append(f"run_command decision={'ALLOW' if ok else 'DENY'} <unprintable>")
        return ok

    return handler


def build_config(
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    workspaces: Sequence[Path | str] = (DEMO_WORKSPACE,),
    enable_shell: bool = True,
    system_instructions: str | None = None,
    audit_log: list[str] | None = None,
    allow_tools: Sequence[str] = (),
) -> LocalOpenAIAgentConfig:
    """Build the locked-down local agent config.

    allow_tools: names of in-process custom python tools (cfg.tools) to permit.
    The policy engine default-denies any tool without an explicit allow rule,
    so registered custom functions must be named here to be callable.
    """
    ws_norms = _workspace_norms(workspaces)

    def file_pred(args) -> bool:
        ok = _path_in_workspaces(args, ws_norms)
        if audit_log is not None:
            raw = args.get("AbsolutePath", "<none>") if isinstance(args, dict) else args
            audit_log.append(f"file_tool decision={'ALLOW' if ok else 'DENY'} path={raw!r}")
        return ok

    policies: list = [
        policy.allow(t, when=file_pred, name="workspace_path_guard") for t in FILE_TOOLS
    ]
    policies += [policy.allow(t, name="custom_tool_allow") for t in allow_tools]
    if enable_shell:
        policies.append(
            policy.ask_user(
                "run_command",
                handler=make_run_command_handler(audit_log, ws_norms),
                name="strict_run_command",
            )
        )
    else:
        policies.append(policy.deny("run_command", name="no_shell"))

    return LocalOpenAIAgentConfig(
        model=model,
        base_url=base_url,
        workspaces=[str(Path(w).resolve()) for w in workspaces],
        policies=policies,
        system_instructions=system_instructions,
    ).lightweight()
