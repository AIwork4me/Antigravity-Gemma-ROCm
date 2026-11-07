"""One-way publish export: build the public repo tree with a fresh, curated history.

This script NEVER pushes. It materializes `build/public/` from the dev repo,
then creates exactly 5 thematic commits. The dev repo remains the source of
truth; re-run this script any time to refresh the export. Internal process
docs (docs/superpowers/), raw artifacts, and runtime state never leak.

Usage:
    python scripts/publish_export.py [--dest build/public]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _force_rmtree(path: Path) -> None:
    """rmtree that clears Windows read-only bits (git objects are read-only).

    The onexc kwarg is 3.12+; fall back to the deprecated onerror for 3.11
    (README claims 3.11+ support).
    """

    def _onexc(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_onexc)
    else:  # pragma: no cover - only reachable on Python 3.11
        shutil.rmtree(path, onerror=_onexc)  # type: ignore[arg-type]


# Top-level trees that ship publicly (git-tracked files only are enforced by caller)
PUBLIC_DIRS = (
    "src",
    "tests",
    "scripts",
    "docs",
    "evidence",
    ".github",
    "demo_workspace_pristine",
    "scenarios",
)

# Root files that ship publicly
PUBLIC_FILES = (
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.lock.txt",
    "pytest.ini",
    "ruff.toml",
    ".gitignore",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
)

# Never ship, even if nested inside a PUBLIC_DIR
EXCLUDE_PARTS = {
    "artifacts",
    "dist",
    "build",
    ".venv",
    ".superpowers",
    "__pycache__",
    ".pytest_cache",
    "demo_workspace",  # runtime state, restored from pristine
    "superpowers",  # internal process docs under docs/
    "trending-readiness",  # T1 engineering-session records (owner-facing, dev-only)
}

# Individual files that never ship (machine-local content, personal material)
EXCLUDE_FILES = {
    "docs/SPEAKER_CARD.md",  # talk crib sheet with local paths — personal, not public
    # Launch copy drafts + internal maintainer scorecard: shipping them before
    # launch would undercut the launch itself. BASELINE / ACCEPTANCE_MATRIX /
    # SECURITY_REVIEW / REPRODUCIBILITY_REPORT / EXTERNAL_VALIDATION_PLAN do ship.
    "docs/trending-readiness/LAUNCH_KIT.md",
    "docs/trending-readiness/FINAL_REPORT.md",
}

COMMIT_IDENTITY = ("Antigravity-Gemma-ROCm", "release@aiwork4me")

# Deterministic redaction applied to shipped TEXT files under evidence/ so
# machine-generated artifacts (absolute paths with the dev username) can ship
# without leaking the account. Rules are stable and public — the redacted tree
# stays reproducible. Hand-written files are NEVER rewritten (their test
# fixtures and examples must stay byte-exact; release_scan enforces that they
# are clean at the source). Dev-side artifacts keep verbatim paths.
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".ps1",
    ".sh",
    ".txt",
    ".ini",
    ".patch",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".log",
}

_RE_REPO_ROOT = re.compile(
    r"[A-Za-z]:[/\\]+Users[/\\]+[A-Za-z0-9_.-]+[/\\]+Desktop[/\\]+Antigravity",
    re.IGNORECASE,
)
_RE_HOME = re.compile(r"[A-Za-z]:[/\\]+Users[/\\]+[A-Za-z0-9_.-]+", re.IGNORECASE)
_RE_UNC_HOME = re.compile(
    r"\\\\[A-Za-z0-9_.-]+\\[A-Za-z]\$\\+Users\\+[A-Za-z0-9_.-]+", re.IGNORECASE
)
_RE_UNIX_HOME = re.compile(r"/home/[A-Za-z0-9_.-]+")


def redact_text(text: str) -> tuple[str, int]:
    """Replace absolute repo/home paths with stable placeholders.

    Returns (redacted_text, replacements). Order matters: repo root first so
    `C:\\Users\\x\\Desktop\\Antigravity` becomes `<repo>` and not `<home>\\...`.
    UNC admin shares and Unix homes are covered too (independent review F-10/F-12).
    """
    text, n1 = _RE_REPO_ROOT.subn("<repo>", text)
    text, n2 = _RE_HOME.subn("<home>", text)
    text, n3 = _RE_UNC_HOME.subn("<unc-home>", text)
    text, n4 = _RE_UNIX_HOME.subn("<unix-home>", text)
    return text, n1 + n2 + n3 + n4


def _excluded(rel: Path) -> bool:
    parts = set(rel.parts)
    if parts & EXCLUDE_PARTS:
        return True
    if rel.as_posix() in EXCLUDE_FILES:
        return True
    # scenarios/*/run/ are runtime outputs
    ps = rel.parts
    return "scenarios" in ps and "run" in ps


def collect_files(root: Path) -> dict[str, Path]:
    """Map public relpath -> absolute source path (read-only walk of the dev repo)."""
    root = Path(root)
    tracked = set()
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    tracked.update(out)

    files: dict[str, Path] = {}
    for name in PUBLIC_FILES:
        p = root / name
        if p.is_file():
            files[name] = p
    for d in PUBLIC_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if _excluded(rel):
                continue
            s = rel.as_posix()
            if s in tracked:  # only git-tracked files ship
                files[s] = p
    return files


def init_public_repo(dest: Path, files: dict[str, Path]) -> None:
    """Write the tree at dest and create 5 thematic commits (identity pinned)."""
    dest = Path(dest)
    _force_rmtree(dest)
    dest.mkdir(parents=True)

    redactions = 0
    for rel, src in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # Redaction applies ONLY to machine-generated evidence: hand-written
        # files (tests/docs/src) must be clean at the source — release_scan.py
        # enforces that, and rewriting them here would corrupt test fixtures
        # (found by the clean-checkout validation of the exported tree).
        if rel.startswith("evidence/") and src.suffix in TEXT_SUFFIXES:
            text = src.read_text(encoding="utf-8", errors="replace")
            text, n = redact_text(text)
            redactions += n
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(src, target)
    print(
        f"[export] deterministic redaction applied: {redactions} path replacements (evidence only)"
    )

    def git(*args: str) -> None:
        env = {
            "GIT_AUTHOR_NAME": COMMIT_IDENTITY[0],
            "GIT_AUTHOR_EMAIL": COMMIT_IDENTITY[1],
            "GIT_COMMITTER_NAME": COMMIT_IDENTITY[0],
            "GIT_COMMITTER_EMAIL": COMMIT_IDENTITY[1],
        }
        import os

        full = {**os.environ, **env}
        subprocess.run(
            ["git", "-C", str(dest), *args], check=True, capture_output=True, text=True, env=full
        )

    git("init", "-q", "-b", "main")

    def add(patterns: list[str]) -> None:
        for pat in patterns:
            if list(dest.glob(pat)):
                git("add", pat)

    add(
        [
            "src/*",
            "tests/*",
            "scenarios/*",
            "demo_workspace_pristine/*",
            "requirements*.txt",
            "pytest.ini",
            "ruff.toml",
            ".gitignore",
            "LICENSE",
        ]
    )
    git(
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "feat: locked-down local agent - config, security boundaries, scenarios, tests",
    )

    add(["scripts/*"])
    git(
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "feat: demo drivers, evidence tooling, web dashboard with record/replay",
    )

    add(["README.md", "README.zh-CN.md", "docs/*"])
    git(
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "docs: evidence-first READMEs (EN/zh) and validation reports",
    )

    add([".github/*", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md"])
    git(
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "ci: GitHub Actions (windows, ruff+pytest) + community files",
    )

    add(["evidence/*"])
    git(
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "evidence: flagship run chain (mission->tools->policy->pytest red/green->diff)",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="one-way publish export (never pushes)")
    ap.add_argument("--dest", type=Path, default=ROOT / "build" / "public")
    args = ap.parse_args()

    files = collect_files(ROOT)
    # completeness gate: a silent missing tree would ship a broken repo
    for must in (
        "README.md",
        "src/config.py",
        "src/webui.py",
        "tests/test_config.py",
        ".github/workflows/ci.yml",
        "docs/TROUBLESHOOTING.md",
        "evidence/flagship-run/summary.json",
        "scripts/preflight.ps1",
    ):
        if must not in files:
            raise SystemExit(f"export incomplete — missing {must}")
    print(f"[export] {len(files)} files")
    init_public_repo(args.dest, files)
    print(f"[export] fresh-history repo ready at: {args.dest}")
    print("[export] NEXT (manual, user-confirmed):")
    print("  cd build/public")
    print("  git remote add origin git@github.com:AIwork4me/Antigravity-Gemma-ROCm.git")
    print("  git push -u origin main   # after user approval")
    return 0


if __name__ == "__main__":
    sys.exit(main())
