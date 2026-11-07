"""Tests for scripts/publish_export.py — one-way export into a fresh-history repo."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from publish_export import collect_files, init_public_repo, redact_text  # noqa: E402

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


# ---------------------------------------------------------------- redaction
def test_redact_text_replaces_repo_root_first():
    text = r"opened C:\Users\alice\Desktop\Antigravity\demo_workspace\stats_utils.py"
    out, n = redact_text(text)
    assert out == r"opened <repo>\demo_workspace\stats_utils.py"
    assert n == 1


def test_redact_text_handles_forward_slashes_and_case():
    out, n = redact_text("cwd c:/users/Bob/Desktop/Antigravity/demo_workspace")
    assert out == "cwd <repo>/demo_workspace"
    assert n == 1


def test_redact_text_home_placeholder():
    out, n = redact_text(r"copied C:\Users\alice\Documents\x")
    assert out == r"copied <home>\Documents\x"
    assert n == 1


def test_redact_text_stable_and_idempotent():
    once, _ = redact_text(r"C:\Users\alice\Desktop\Antigravity\a")
    twice, _ = redact_text(once)
    assert once == twice == "<repo>\\a"


def test_init_public_repo_redacts_shipped_text(tmp_path):
    """A shipped evidence file with raw user paths must land in the export
    with placeholders — the public tree never carries the dev username."""
    src_dir = tmp_path / "raw"
    src_dir.mkdir()
    ev = src_dir / "policy_audit.txt"
    ev.write_text(
        r"run_command ALLOW Cwd='C:\Users\dana\Desktop\Antigravity\demo_workspace'",
        encoding="utf-8",
    )
    files = {"evidence/flagship-run/policy_audit.txt": ev}
    dest = tmp_path / "public"
    init_public_repo(dest, files)
    shipped = (dest / "evidence" / "flagship-run" / "policy_audit.txt").read_text(encoding="utf-8")
    assert "dana" not in shipped
    assert "<repo>" in shipped
