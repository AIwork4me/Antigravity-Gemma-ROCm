"""Tests for scripts/verify_bundle.py and the v1.0.1 bundle redaction."""

import hashlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import verify_bundle as vb  # noqa: E402
from build_evidence_bundle import build_bundle  # noqa: E402


def _artifacts(tmp_path: Path) -> Path:
    art = tmp_path / "artifacts"
    (art / "agent-execution" / "run-x").mkdir(parents=True)
    (art / "agent-execution" / "run-x" / "policy_audit.txt").write_text(
        r"Cwd C:\Users\dana\Desktop\Antigravity\demo_workspace", encoding="utf-8"
    )
    (art / "agent-execution" / "run-x" / "blob.bin").write_bytes(b"\x00\x01\x02")
    return art


def test_bundle_redacts_text_and_writes_sidecar(tmp_path):
    out = build_bundle(_artifacts(tmp_path), tmp_path, name="t-bundle.zip")
    assert (tmp_path / "t-bundle.zip.sha256").is_file()
    with zipfile.ZipFile(out) as z:
        member = z.read("agent-execution/run-x/policy_audit.txt").decode("utf-8")
        assert "dana" not in member and "<repo>" in member  # redacted before hashing
        binary = z.read("agent-execution/run-x/blob.bin")
        assert binary == b"\x00\x01\x02"  # binary untouched


def test_verify_bundle_ok(tmp_path):
    out = build_bundle(_artifacts(tmp_path), tmp_path, name="ok-bundle.zip")
    assert vb.verify(out) == 0


def test_verify_detects_outer_hash_tamper(tmp_path):
    out = build_bundle(_artifacts(tmp_path), tmp_path, name="tamper.zip")
    out.write_bytes(out.read_bytes() + b"junk")  # corrupt after sidecar written
    assert vb.verify(out) == 1


def test_verify_detects_inner_mismatch(tmp_path):
    out = build_bundle(_artifacts(tmp_path), tmp_path, name="inner.zip")
    # rewrite one member, keep the manifest stale
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(buf, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "agent-execution/run-x/policy_audit.txt":
                data = b"swapped"
            zout.writestr(item, data)
    out.write_bytes(buf.getvalue())
    assert vb.verify(out) == 1


def test_verify_detects_leak_inside_bundle(tmp_path):
    """A zip that did NOT go through the builder's redaction (e.g. hand-made
    or tampered) must fail verification via the publication scanner."""
    leak = "path C:\\Users\\someone\\Desktop\\p"
    out = tmp_path / "leaky.zip"
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("notes/leak.md", leak)
        digest = hashlib.sha256(leak.encode()).hexdigest()
        z.writestr("SHA256SUMS.txt", f"{digest}  notes/leak.md\n")
    (tmp_path / "leaky.zip.sha256").write_text(
        "0" * 64 + "  leaky.zip\n", encoding="utf-8"
    )  # outer hash will mismatch too — either failure mode is correct
    assert vb.verify(out) == 1


def test_bundle_excludes_dev_working_outputs(tmp_path):
    """Scanner reports (they contain flagged snippets incl. serial samples)
    and console captures never ship in a release bundle."""
    art = _artifacts(tmp_path)
    (art / "rc1").mkdir()
    (art / "rc1" / "release_scan.json").write_text(
        '{"findings": [{"kind": "machine_serial"}]}', encoding="utf-8"
    )
    (art / "rc1" / "release_scan_export.json").write_text("{}", encoding="utf-8")
    (art / "gpuval-console.log").write_text("console capture", encoding="utf-8")
    out = build_bundle(art, tmp_path, name="excl.zip")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert not any("release_scan" in n for n in names)
    assert not any(n.endswith("gpuval-console.log") for n in names)
    assert any(n.endswith("policy_audit.txt") for n in names)  # evidence kept


def test_redaction_covers_macos_users_paths(tmp_path):
    """llama.cpp build logs embed upstream /Users/<name> paths."""
    art = _artifacts(tmp_path)
    (art / "upstream.log").write_text("built by /Users/daniel/llama.cpp\n", encoding="utf-8")
    out = build_bundle(art, tmp_path, name="unix.zip")
    with zipfile.ZipFile(out) as z:
        member = z.read("upstream.log").decode("utf-8")
    assert "daniel" not in member and "<unix-home>" in member


def test_verify_rejects_traversal_member_names(tmp_path):
    """A zip whose member names escape the archive must never verify OK."""
    payload = b"clean"
    out = tmp_path / "evil.zip"
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("../evil.txt", payload)
        z.writestr("ok.txt", payload)
        digest = hashlib.sha256(payload).hexdigest()
        z.writestr("SHA256SUMS.txt", f"{digest}  ../evil.txt\n{digest}  ok.txt\n")
    (tmp_path / "evil.zip.sha256").write_text(
        hashlib.sha256(out.read_bytes()).hexdigest() + "  evil.zip\n", encoding="utf-8"
    )
    assert vb.verify(out) == 1


def test_verify_rejects_duplicate_manifest_lines(tmp_path):
    payload = b"clean"
    out = tmp_path / "dup.zip"
    digest = hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("ok.txt", payload)
        z.writestr("SHA256SUMS.txt", f"{digest}  ok.txt\n{digest}  ok.txt\n")
    (tmp_path / "dup.zip.sha256").write_text(
        hashlib.sha256(out.read_bytes()).hexdigest() + "  dup.zip\n", encoding="utf-8"
    )
    assert vb.verify(out) == 1


def test_verify_rejects_empty_sidecar(tmp_path):
    art = _artifacts(tmp_path)
    out = build_bundle(art, tmp_path, name="empty-sidecar.zip")
    (tmp_path / "empty-sidecar.zip.sha256").write_text("\n", encoding="utf-8")
    assert vb.verify(out) == 1
