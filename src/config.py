"""Configuration for the Antigravity-Gemma-ROCm local agent.

Builds a locked-down LocalOpenAIAgentConfig:
- model served by local Ollama (OpenAI-compatible endpoint)
- file tools (view/create/edit) allowed ONLY inside the demo workspace,
  enforced by path predicates on the `AbsolutePath` tool argument
- run_command gated by a structured, fail-closed handler: the command line is
  tokenized and validated as (executable, args, Cwd) — NOT by prefix/substring
  matching. Only `python -m pytest ...` / `pytest ...` invocations with an
  in-workspace Cwd pass; everything else, including unknown argument schemas,
  ambiguous command keys, arbitrary scripts and `-c` code evaluation, is denied.

Tool arg schemas observed live on google-antigravity 0.1.23 (Windows):
  view_file/create_file/edit_file -> {'AbsolutePath': 'C:/...'}
  run_command                     -> {'CommandLine': str, 'Cwd': str, 'WaitMsBeforeAsync': int}
Note: policy.workspace_only() in 0.1.23 unconditionally DENIES all file tools
(it ignores its workspaces arg), so we implement restriction via allow+when.

This is a POLICY boundary, not an OS sandbox: the SDK's subprocesses run with
the caller's privileges (see SECURITY.md / docs/SECURITY_BOUNDARY_REVIEW.md).
"""

from __future__ import annotations

import math
import os
import re
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

# --- structured shell policy (replaces prefix/substring matching) ----------
#
# The old model ("starts with pytest/python" + substring deny-list) was
# bypassable: `python -c import demo_workspace` passed the substring check,
# and `pytest_evil` matched the `pytest` prefix. The policy below validates
# the COMPLETE structure of the tool call instead of pattern-matching text.

# run_command argument keys recognized on SDK 0.1.23 (+ defensive aliases).
_CMD_KEYS = ("CommandLine", "CommandLineJson", "command", "Command", "cmd")
_CWD_KEYS = ("Cwd", "cwd", "WorkingDirectory", "working_directory", "working_dir")
_KNOWN_RUN_KEYS = (
    frozenset(_CMD_KEYS)
    | frozenset(_CWD_KEYS)
    | {
        "WaitMsBeforeAsync",
        "wait_ms_before_async",
    }
)

# Executables the agent may invoke, after alias resolution. Anything else —
# including absolute paths and prefixed lookalikes (`pytest_evil`) — is denied.
_EXE_ALIASES = {
    "python": "python",
    "python3": "python",
    "python.exe": "python",
    "py": "py",
    "py.exe": "py",
    "pytest": "pytest",
    "pytest.exe": "pytest",
    "py.test": "pytest",
}

# pytest flags with no operand. `--tb=` is validated separately by value.
_PYTEST_FLAGS = frozenset({"-q", "-qq", "-v", "-vv", "-x", "-s", "--no-header", "--no-summary"})
_TB_VALUES = frozenset({"short", "long", "line", "no", "auto"})

# A pytest positional operand: a relative test path / node id. No `..`, no
# drive letters, no UNC, no absolute paths (absolute paths are checked against
# the workspace separately).
_POSITIONAL_RE = re.compile(r"^[A-Za-z0-9_\-./\\]+(?:::[A-Za-z0-9_\-]+)*$")

# Any shell metacharacter anywhere in the raw command rejects it outright —
# the enumerated commands never need quoting, chaining or substitution.
_SHELL_METACHARS = (";", "&", "|", ">", "<", "`", "(", ")", "$", "\n", "\r", "^", "%")

# Characters allowed anywhere in a token after dequoting. Rejects quote
# tricks, unicode homoglyphs, and anything the enumerated commands don't use.
_TOKEN_CHARSET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.:/\\=+@")


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


def _pick_key(args: dict, keys: tuple[str, ...]) -> tuple[str, object] | None:
    """Return the single recognized key present in args, or None.

    More than one alias present (e.g. both 'CommandLine' and 'cmd') is
    ambiguous — callers must deny, because the harness might honor either.
    """
    present = [k for k in keys if k in args]
    if len(present) != 1:
        return None
    return present[0], args[present[0]]


def _tokenize(cmd: str) -> list[str] | None:
    """Quote-aware tokenizer for the enumerated commands; None = malformed.

    Accepts "..." / '...' grouping (which the enumerated commands never need,
    so any quoted token is later rejected by the charset check — quoting is
    parsed only so it cannot smuggle a second structure past the splitter).
    """
    tokens: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    for ch in cmd:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in ('"', "'"):
            quote = ch
        elif ch.isspace():
            if cur:
                tokens.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if quote:  # unterminated quote -> malformed
        return None
    if cur:
        tokens.append("".join(cur))
    return tokens or None


def _valid_positional(tok: str, workspace_norms: list[str]) -> bool:
    """A pytest positional operand: relative in-workspace path or node id,
    or an absolute path that resolves inside a workspace."""
    if _looks_like_abs_path(tok) or tok.startswith("//") or tok.startswith("\\\\"):
        n = _norm(tok)
        return any(n == w or n.startswith(w + "/") for w in workspace_norms)
    if not _POSITIONAL_RE.match(tok):
        return False
    if ".." in tok.replace("\\", "/").split("/"):
        return False
    return True  # relative path without traversal; Cwd keeps it in-workspace


def _pytest_args_ok(argv: list[str], workspace_norms: list[str]) -> bool:
    """Validate the argument tail of a pytest invocation, fail-closed."""
    for tok in argv:
        if not tok or any(ch not in _TOKEN_CHARSET for ch in tok):
            return False
        if tok in _PYTEST_FLAGS:
            continue
        if tok.startswith("--tb="):
            if tok[5:] not in _TB_VALUES:
                return False
            continue
        if tok.startswith("--"):  # unknown long flag
            return False
        if tok.startswith("-"):  # unknown short flag / option operand
            return False
        if not _valid_positional(tok, workspace_norms):
            return False
    return True


def command_is_safe(tc_or_args, workspace_norms: list[str] | None = None) -> bool:
    """Structured, fail-closed predicate for run_command tool calls.

    Validates the COMPLETE tool-call structure — schema, executable, argument
    tree and working directory — instead of pattern-matching text:

    1. args must be a dict whose keys are all recognized run_command keys
       (unknown schema -> deny); exactly one command key must be present.
    2. The command must contain no shell metacharacters, tokenize cleanly,
       and consist only of characters the enumerated commands use.
    3. The executable must be a bare, exactly-known name (python / py / pytest
       / py.test) — no paths, no prefixed lookalikes.
    4. `python` may only run `-m pytest` (no `-c`, no scripts, no other -m);
       `py` may only launch `-3 -m pytest`.
    5. pytest arguments are limited to an enumerated flag set plus in-workspace
       test paths / node ids.
    6. A Cwd is REQUIRED and must resolve inside a workspace.

    This is a strictly validated allow rule, NOT allow_all(). It is still a
    policy boundary, not an OS sandbox — see the module docstring.
    """
    if isinstance(tc_or_args, ToolCall) or hasattr(tc_or_args, "args"):
        args = tc_or_args.args
    else:
        args = tc_or_args
    if not isinstance(args, dict):
        return False
    if any(k not in _KNOWN_RUN_KEYS for k in args):
        return False  # unknown schema -> deny
    picked = _pick_key(args, _CMD_KEYS)
    if picked is None:
        return False  # missing or ambiguous command key
    cmd = picked[1]
    if not isinstance(cmd, str):
        return False
    cmd = cmd.strip()
    # quotes too: the enumerated commands never need them, and dequoted
    # argv[0] aliasing (py"th"on) is noise we reject outright (review F-3)
    if not cmd or any(ch in cmd for ch in _SHELL_METACHARS) or any(c in cmd for c in "\"'"):
        return False

    # working directory: REQUIRED, exactly one alias, canonical in-workspace
    picked_cwd = _pick_key(args, _CWD_KEYS)
    if picked_cwd is None:
        return False
    cwd = picked_cwd[1]
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    if workspace_norms is None:
        return False  # cannot validate -> conservative deny
    cwd_n = _norm(cwd)
    if not any(cwd_n == w or cwd_n.startswith(w + "/") for w in workspace_norms):
        return False

    wait = args.get("WaitMsBeforeAsync", args.get("wait_ms_before_async"))
    if wait is not None and (
        isinstance(wait, bool)
        or not isinstance(wait, int | float)
        or not math.isfinite(wait)  # NaN passes plain range checks (review F-2)
        or wait < 0
        or wait > 600_000
    ):
        return False

    argv = _tokenize(cmd)
    if argv is None:
        return False
    exe = _EXE_ALIASES.get(argv[0].lower())
    if exe is None:
        return False  # unknown/absolute/prefixed executable
    if any(ch not in _TOKEN_CHARSET for tok in argv[1:] for ch in tok):
        return False

    if exe == "py":
        if len(argv) < 2 or argv[1] != "-3":
            return False
        tail = argv[2:]
        exe = "python"
    else:
        tail = argv[1:]
    if exe == "python":
        if len(tail) < 2 or tail[0] != "-m" or tail[1].lower() != "pytest":
            return False  # only `-m pytest`; no -c, no scripts, no other module
        return _pytest_args_ok(tail[2:], workspace_norms)
    return _pytest_args_ok(tail, workspace_norms)  # pytest / py.test


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
