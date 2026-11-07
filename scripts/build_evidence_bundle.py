"""Curate the flagship evidence chain and build the full evidence bundle.

Two deliverables:
  1. `curate_flagship(run_dir, dest)` — copies the 11-file evidence chain of a
     successful run (mission -> tool calls -> policy audit -> reply -> pytest
     red/green -> workspace hashes -> diff -> summary) into `evidence/flagship-run/`
     so the public repo ships ONE complete, walkable proof without the bulk.
  2. `build_bundle(src, dest)` — zips the whole `artifacts/` tree (skipping
     `*.log.tmp`, matching .gitignore) with an inner `SHA256SUMS.txt` manifest,
     for attachment to the GitHub Release. Prints the zip's own SHA256.

Usage:
    python scripts/build_evidence_bundle.py [--artifacts artifacts]
        [--flagship <run-dir>] [--recording <run.json>] [--out dist]
    Defaults: newest artifacts/agent-execution/run-* with bug_fixed_after=true.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHAIN_FILES = (
    "mission.txt",
    "tool_calls.json",
    "policy_audit.txt",
    "agent_reply.txt",
    "config.txt",
    "pytest_before.json",
    "pytest_after.json",
    "workspace_before.json",
    "workspace_after.json",
    "git-diff.patch",  # optional in older runs
    "summary.json",
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def curate_flagship(run_dir: Path, dest: Path) -> Path:
    """Copy the flagship evidence chain from a run dir into dest (e.g. evidence/)."""
    run_dir = Path(run_dir)
    summary = run_dir / "summary.json"
    if not summary.exists():
        raise FileNotFoundError(f"no summary.json in {run_dir}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in CHAIN_FILES:
        src = run_dir / name
        if src.exists():
            (dest / name).write_bytes(src.read_bytes())
            copied += 1
    print(f"[curate] {copied} files: {run_dir} -> {dest}")
    return dest


def pick_flagship(artifacts: Path) -> Path:
    """Newest run-* dir with the FULL evidence chain whose summary says the bug was fixed.

    Web dashboard runs write only summary.json; a flagship must carry the
    complete textual chain (mission, tool calls, policy audit, pytest red/green).
    """
    runs_root = Path(artifacts) / "agent-execution"
    candidates = []
    for run in sorted(runs_root.glob("run-*"), reverse=True):
        s = run / "summary.json"
        if not s.exists() or not (run / "mission.txt").exists():
            continue
        try:
            if json.loads(s.read_text(encoding="utf-8")).get("bug_fixed_after"):
                candidates.append(run)
        except (OSError, json.JSONDecodeError):
            continue
    if not candidates:
        raise SystemExit(f"no successful full-chain run found under {runs_root}")
    return candidates[0]


def build_bundle(src: Path, dest: Path, name: str = "evidence-bundle-v1.0.0.zip") -> Path:
    """Zip the artifacts tree with an inner SHA256SUMS.txt; return the zip path."""
    src = Path(src)
    if not src.is_dir():
        raise SystemExit(f"artifacts dir not found: {src}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / name

    files = sorted(p for p in src.rglob("*") if p.is_file() and not p.name.endswith(".log.tmp"))
    sums = "\n".join(f"{sha256_of(p)}  {p.relative_to(src).as_posix()}" for p in files)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(src).as_posix())
        z.writestr("SHA256SUMS.txt", sums + "\n")
    print(f"[bundle] {len(files)} files -> {out} ({out.stat().st_size / 2**20:.1f} MiB)")
    print(f"[bundle] bundle sha256: {sha256_of(out)}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifacts", type=Path, default=ROOT / "artifacts")
    ap.add_argument(
        "--flagship",
        type=Path,
        default=None,
        help="run dir to curate (default: newest successful run-*)",
    )
    ap.add_argument(
        "--recording",
        type=Path,
        default=None,
        help="web UI recording (run.json) to copy alongside the chain",
    )
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--curate-only", action="store_true", help="skip the bulk bundle")
    args = ap.parse_args()

    run = args.flagship or pick_flagship(args.artifacts)
    print(f"[curate] flagship run: {run}")
    curate_flagship(run, ROOT / "evidence" / "flagship-run")
    if args.recording and Path(args.recording).exists():
        target = ROOT / "evidence" / "flagship-run" / "webui-recording.json"
        target.write_bytes(Path(args.recording).read_bytes())
        print(f"[curate] recording copied -> {target}")

    if not args.curate_only:
        build_bundle(args.artifacts, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
