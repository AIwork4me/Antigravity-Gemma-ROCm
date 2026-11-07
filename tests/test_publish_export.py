"""Tests for scripts/publish_export.py — one-way export into a fresh-history repo."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from publish_export import collect_files, init_public_repo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_collect_files_includes_core():
    files = collect_files(ROOT)
    keys = set(files)
    assert "README.md" in keys
    assert "README.zh-CN.md" in keys
    assert "src/webui.py" in keys
    assert ".github/workflows/ci.yml" in keys
    assert "requirements.txt" in keys
    assert "LICENSE" in keys


def test_collect_files_includes_evidence():
    files = collect_files(ROOT)
    keys = set(files)
    assert "evidence/flagship-run/summary.json" in keys


def test_collect_files_excludes_internal():
    files = collect_files(ROOT)
    keys = set(files)
    for prefix in (
        "artifacts/",
        "docs/superpowers/",
        "demo_workspace/",
        ".venv/",
        "dist/",
        "build/",
        ".superpowers/",
    ):
        assert not any(k.startswith(prefix) for k in keys), f"leaked: {prefix}"
    # scenario runtime outputs are excluded, pristine fixtures are kept
    assert not any(k.startswith("scenarios/") and "/run/" in k for k in keys)
    assert any(k.startswith("scenarios/") and k.endswith("pristine/textutils.py") for k in keys)


def test_collect_files_excludes_speaker_card():
    """The speaker crib sheet carries machine-local paths; it never ships."""
    files = collect_files(ROOT)
    assert "docs/SPEAKER_CARD.md" not in set(files)
    # while general docs still ship
    assert "docs/TROUBLESHOOTING.md" in set(files)


def test_init_public_repo_five_commits(tmp_path):
    files = {
        "README.md": ROOT / "README.md",
        "LICENSE": ROOT / "LICENSE",
        "src/config.py": ROOT / "src" / "config.py",
        "docs/TROUBLESHOOTING.md": ROOT / "docs" / "TROUBLESHOOTING.md",
        ".github/workflows/ci.yml": ROOT / ".github" / "workflows" / "ci.yml",
        "evidence/flagship-run/README.md": ROOT / "evidence" / "flagship-run" / "README.md",
    }
    dest = tmp_path / "public"
    init_public_repo(dest, files)
    log = subprocess.run(
        ["git", "-C", str(dest), "log", "--oneline"], capture_output=True, text=True, check=True
    ).stdout
    assert len(log.strip().splitlines()) == 5
    status = subprocess.run(
        ["git", "-C", str(dest), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status == "", f"dirty tree: {status}"
    assert (dest / "README.md").exists()
