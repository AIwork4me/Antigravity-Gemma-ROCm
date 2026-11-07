"""Tests for scripts/build_evidence_bundle.py — flagship curation + bundle builder."""

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_evidence_bundle import build_bundle, curate_flagship, pick_flagship  # noqa: E402

CHAIN = (
    "mission.txt",
    "tool_calls.json",
    "policy_audit.txt",
    "agent_reply.txt",
    "config.txt",
    "pytest_before.json",
    "pytest_after.json",
    "workspace_before.json",
    "workspace_after.json",
    "summary.json",
)


def _make_run(tmp_path: Path, fixed: bool = True) -> Path:
    run = tmp_path / "run-demoX"
    run.mkdir(parents=True)
    for name in CHAIN:
        (run / name).write_text(f"content of {name}\n", encoding="utf-8")
    (run / "git-diff.patch").write_text("+ fixed\n", encoding="utf-8")
    (run / "summary.json").write_text(
        '{"run_id": "demoX", "bug_fixed_after": %s}' % ("true" if fixed else "false"),
        encoding="utf-8",
    )
    return run


# ------------------------------------------------------------------ curation
def test_curate_flagship_copies_chain(tmp_path):
    run = _make_run(tmp_path)
    dest = tmp_path / "evidence" / "flagship-run"
    out = curate_flagship(run, dest)
    assert out == dest
    names = {p.name for p in dest.iterdir()}
    assert set(CHAIN) <= names
    assert "git-diff.patch" in names
    assert (dest / "summary.json").read_text(encoding="utf-8").startswith('{"run_id"')


def test_curate_flagship_missing_summary(tmp_path):
    run = _make_run(tmp_path)
    (run / "summary.json").unlink()
    with pytest.raises(FileNotFoundError):
        curate_flagship(run, tmp_path / "out")


# -------------------------------------------------------------------- bundle
def test_pick_flagship_requires_full_chain(tmp_path):
    """A newer summary-only web run must NOT outrank an older full-chain run."""
    runs = tmp_path / "agent-execution"
    web = runs / "run-web-999999"
    web.mkdir(parents=True)
    (web / "summary.json").write_text('{"bug_fixed_after": true}', encoding="utf-8")
    full = _make_run(runs)  # run-demoX with the complete chain
    assert pick_flagship(tmp_path) == full


def test_pick_flagship_skips_unfixed_runs(tmp_path):
    _make_run(tmp_path / "agent-execution", fixed=False)
    with pytest.raises(SystemExit):
        pick_flagship(tmp_path / "agent-execution")


def test_build_bundle_zip_contents(tmp_path):
    src = tmp_path / "artifacts"
    (src / "sub").mkdir(parents=True)
    files = {"a.txt": b"alpha", "sub/b.log": b"beta", "c.log.tmp": b"skip-me"}
    for rel, data in files.items():
        (src / rel).write_bytes(data)
    dest = tmp_path / "dist"
    out = build_bundle(src, dest)
    assert out.exists() and out.suffix == ".zip"
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert "a.txt" in names and "sub/b.log" in names
        assert "c.log.tmp" not in names  # gitignore-aligned exclusion
        assert "SHA256SUMS.txt" in names
        sums = {}
        for line in z.read("SHA256SUMS.txt").decode().splitlines():
            digest, rel = line.split("  ", 1)
            sums[rel] = digest
        assert sums["a.txt"] == hashlib.sha256(b"alpha").hexdigest()


def test_build_bundle_missing_src(tmp_path):
    with pytest.raises(SystemExit) as ei:
        build_bundle(tmp_path / "nope", tmp_path / "dist")
    assert "not found" in str(ei.value)
