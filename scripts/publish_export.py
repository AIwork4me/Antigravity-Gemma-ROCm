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
}

# Individual files that never ship (machine-local content, personal material)
EXCLUDE_FILES = {
    "docs/SPEAKER_CARD.md",  # talk crib sheet with local paths — personal, not public
}

COMMIT_IDENTITY = ("Antigravity-Gemma-ROCm", "release@aiwork4me")


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

    for rel, src in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

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
