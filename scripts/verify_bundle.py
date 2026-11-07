"""Independently verify a proposed evidence bundle before publication.

Checks (fail-closed, exit 1 on any mismatch):
  1. outer sha256 of the .zip matches the `<zip>.sha256` sidecar — recomputed
     with certutil (an implementation independent of the Python builder)
  2. every SHA256SUMS.txt manifest line matches the actual member bytes
     (Python zipfile + hashlib, recomputed from scratch)
  3. member count == manifest line count == namelist count (no orphan/missing)
  4. no text member carries an unredacted user path (release_scan.scan_zip —
     the same gate CI runs, so no artifact can bypass the publication scanner)

Usage: python scripts/verify_bundle.py dist/evidence-bundle-v1.0.1.zip
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_scan import scan_zip  # noqa: E402


def certutil_sha256(path: Path) -> str | None:
    """sha256 via certutil — independent of Python hashlib."""
    r = subprocess.run(
        ["certutil", "-hashfile", str(path), "SHA256"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # localized header text must not kill the reader thread
        timeout=120,
    )
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if len(lines) >= 2 and len(lines[1]) == 64:
        return lines[1].lower()
    return None


def verify(bundle: Path) -> int:
    bundle = Path(bundle)
    sidecar = bundle.with_name(bundle.name + ".sha256")
    problems: list[str] = []

    # 1. outer hash, independent implementation
    if not bundle.is_file():
        print(f"FAIL: bundle missing: {bundle}")
        return 1
    outer = certutil_sha256(bundle)
    if outer is None:
        problems.append("certutil could not hash the bundle")
    elif sidecar.is_file():
        sidecar_lines = [
            ln for ln in sidecar.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        if not sidecar_lines:
            problems.append("sidecar is empty")
        else:
            expected = sidecar_lines[0].split()[0].lower()
            if expected != outer:
                problems.append(f"outer sha256 mismatch: sidecar={expected} certutil={outer}")
    else:
        problems.append(f"sidecar missing: {sidecar}")

    # 2+3. inner manifest, member-name safety, and counts
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
        for n in names:  # traversal/absolute member names are extraction hazards
            parts = n.replace("\\", "/").split("/")
            if n.startswith(("/", "\\")) or ":" in n or ".." in parts:
                problems.append(f"unsafe member name: {n!r}")
        if "SHA256SUMS.txt" not in names:
            problems.append("no SHA256SUMS.txt inside bundle")
        else:
            manifest = {}
            for line in z.read("SHA256SUMS.txt").decode("utf-8").splitlines():
                if line.strip():
                    digest, _, rel = line.partition("  ")
                    rel = rel.strip()
                    if rel in manifest:
                        problems.append(f"duplicate manifest entry: {rel}")
                    manifest[rel] = digest.strip()
            members = [n for n in names if n != "SHA256SUMS.txt"]
            if len(manifest) != len(members):
                problems.append(f"manifest has {len(manifest)} entries vs {len(members)} members")
            for n in members:
                data = z.read(n)
                got = hashlib.sha256(data).hexdigest()
                if manifest.get(n) != got:
                    problems.append(
                        f"inner hash mismatch: {n} manifest={manifest.get(n)} actual={got}"
                    )
            print(f"[verify] {len(members)} members, inner hashes recomputed")

    # 4. publication scanner over the same archive — no artifact bypasses it
    files_scanned, findings = scan_zip(bundle)
    print(f"[verify] scanner: {files_scanned} text members scanned, {len(findings)} findings")
    for f in findings:
        problems.append(f"scanner finding: {f['kind']} {f['file']}")

    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        return 1
    print(f"OK: {bundle.name} verified (outer={outer or '?'})")
    return 0


if __name__ == "__main__":
    sys.exit(
        verify(Path(sys.argv[1]))
        if len(sys.argv) > 1
        else SystemExit("usage: verify_bundle.py <zip>")
    )
