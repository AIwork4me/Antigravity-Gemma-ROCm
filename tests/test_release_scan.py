"""Tests for scripts/release_scan.py — publication-surface scanner.

Verifies detection, allowlist behavior, enforcement exit codes, evidence
redaction-awareness, and zip scanning. Uses temp trees only.
"""

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release_scan as rs  # noqa: E402


# ------------------------------------------------------------- detection
def test_scan_text_detects_user_path():
    text = "log line: opened C:\\Users\\alice\\Desktop\\proj\\file.py ok"
    findings = rs.scan_text(text, "src/fake.py")
    assert any(f["kind"] == "abs_win_user_path" for f in findings)


def test_scan_text_detects_unix_home():
    findings = rs.scan_text("built at /home/bob/project", "scripts/x.sh")
    assert any(f["kind"] == "abs_unix_user_path" for f in findings)


def test_scan_text_detects_secret_like():
    findings = rs.scan_text("api_key = 'abcdefghij12345678'", "src/leak.py")
    assert any(f["kind"] == "secret_like" for f in findings)


def test_scan_text_detects_token_prefix():
    findings = rs.scan_text("token: ghp_0123456789abcdefXYZ", "notes.md")
    assert any(f["kind"] == "token_prefix" for f in findings)


def test_scan_text_detects_machine_serial():
    findings = rs.scan_text("device HN7306 shipped", "docs/spec.md")
    assert any(f["kind"] == "machine_serial" for f in findings)


def test_scan_text_detects_fine_grained_pat():
    findings = rs.scan_text("token github_pat_11ABCDEFG0123456789", "notes.md")
    assert any(f["kind"] == "token_prefix" for f in findings)


def test_scan_text_detects_unc_admin_share():
    findings = rs.scan_text(r"copy from \\localhost\c$\Users\dana\Desktop\x", "notes.md")
    assert any(f["kind"] == "unc_user_path" for f in findings)


def test_scan_text_detects_percent_encoded_path():
    findings = rs.scan_text("fetch C:%5CUsers%5Cdana%5CDesktop", "notes.md")
    assert any(f["kind"] == "pct_encoded_path" for f in findings)


def test_scan_text_covers_log_suffix():
    findings = rs.scan_text(r"opened C:\Users\dana\x\a.log" + "\n", "evidence/run.log")
    assert any(f["kind"] == "abs_win_user_path" for f in findings)


def test_clean_text_has_no_findings():
    assert rs.scan_text("print('hello ROCm world')\n", "src/clean.py") == []


# ------------------------------------------------------------- allowlist
def test_machine_model_allowed_in_readme():
    text = "Measured on ASUS ProArt PX13 — Ryzen AI MAX+ 395."
    assert rs.scan_text(text, "README.md") == []


def test_machine_model_flagged_outside_readme():
    text = "Measured on ASUS ProArt PX13."
    assert any(f["kind"] == "machine_model" for f in rs.scan_text(text, "src/evil.py"))


def test_machine_serial_never_allowed():
    text = "device HN7306"
    assert any(f["kind"] == "machine_serial" for f in rs.scan_text(text, "README.md"))


def test_placeholder_username_allowed():
    assert rs.scan_text("cd C:\\Users\\<you>\\repo", "docs/SETUP.md") == []


def test_git_at_github_not_email():
    assert rs.scan_text("git remote add origin git@github.com:o/r.git", "scripts/x.py") == []


# ------------------------------------------------------------- scanning trees
def test_scan_root_redacts_evidence(tmp_path):
    """evidence/** is scanned AS EXPORTED (redacted): a raw user path inside
    evidence must NOT be a finding (it ships redacted), while the same path in
    src/ must be."""
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "run.txt").write_text(
        "Cwd C:\\Users\\someone\\Desktop\\Antigravity\\demo_workspace", encoding="utf-8"
    )
    files, findings = rs.scan_root(tmp_path)
    assert files == 1
    assert findings == []

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "p = r'C:\\Users\\someone\\Desktop\\Antigravity\\x'", encoding="utf-8"
    )
    _, findings = rs.scan_root(tmp_path)
    assert any(f["kind"] == "abs_win_user_path" and f["file"] == "src/a.py" for f in findings)


def test_scan_root_skips_excluded_files(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "SPEAKER_CARD.md").write_text(
        "cd C:\\Users\\someone\\Desktop\\Antigravity", encoding="utf-8"
    )
    files, findings = rs.scan_root(tmp_path)
    assert files == 0 and findings == []  # publish_export.EXCLUDE_FILES honored


def test_scan_zip_members(tmp_path):
    z = tmp_path / "bundle.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("evidence/policy_audit.txt", "path C:\\Users\\alice\\w\\demo")
    files, findings = rs.scan_zip(z)
    assert files == 1
    assert any(f["kind"] == "abs_win_user_path" for f in findings)


# ------------------------------------------------------------- enforcement
def test_enforce_exit_codes(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "clean.py").write_text("ok\n", encoding="utf-8")
    out = tmp_path / "report.json"
    assert rs.main(["--root", str(tmp_path), "--json-out", str(out), "--enforce"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["findings"] == []

    (tmp_path / "src" / "leak.py").write_text(
        "x = r'C:\\Users\\alice\\Desktop\\p'", encoding="utf-8"
    )
    assert rs.main(["--root", str(tmp_path), "--json-out", str(out), "--enforce"]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["findings"]


def test_scan_zip_skipped_members_are_findings(tmp_path, monkeypatch):
    """Members beyond the count/size limits must FAIL enforcement, not be
    silently skipped (independent review F-9)."""
    monkeypatch.setattr(rs, "ZIP_MEMBER_LIMIT", 2)
    z = tmp_path / "many.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(4):
            zf.writestr(f"filler/{i}.txt", "clean")
    _, findings = rs.scan_zip(z)
    assert sum(1 for f in findings if f["kind"] == "zip_member_skipped") == 2

    monkeypatch.setattr(rs, "ZIP_MEMBER_LIMIT", 2000)
    monkeypatch.setattr(rs, "ZIP_MEMBER_BYTES", 16)
    z2 = tmp_path / "huge.zip"
    with zipfile.ZipFile(z2, "w") as zf:
        zf.writestr("big/leak.md", "x" * 64)
    _, findings = rs.scan_zip(z2)
    assert any(f["kind"] == "zip_member_skipped" for f in findings)
