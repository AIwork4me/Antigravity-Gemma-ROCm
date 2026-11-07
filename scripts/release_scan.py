"""Release readiness scan: flag private info across the COMPLETE publication surface.

Scanned surface (mirrors scripts/publish_export.py plus everything that has
ever shipped): src, tests, scripts, docs, scenarios, evidence, .github,
demo_workspace_pristine and the root publish files; optionally an exported
tree (--extra-root build/public) and release archives (--zip dist/*.zip).

Flags: absolute user paths (Windows + Unix), this machine's username in
paths, machine-serial-like strings, secret-looking assignments, SSH keys,
API-token prefixes, emails. Deliberate disclosures (e.g. the README hardware
section naming the reference laptop model) are suppressed by an explicit,
auditable allowlist — product names like "ROCm" are NOT findings.

Modes:
    python scripts/release_scan.py                 # report -> artifacts/rc1/release_scan.json
    python scripts/release_scan.py --enforce       # additionally exit 1 on findings (CI gate)
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Share the export's exclusion table + redaction rules so "what we scan" and
# "what we ship" can never drift apart.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_export import _excluded, redact_text  # noqa: E402

# Must stay aligned with publish_export.PUBLIC_DIRS / PUBLIC_FILES (the export
# is the authoritative public tree; this list is the superset we hold to).
PUBLISH_DIRS = (
    "src",
    "tests",
    "scripts",
    "docs",
    "scenarios",
    "evidence",
    ".github",
    "demo_workspace_pristine",
)
PUBLISH_FILES = (
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
TEXT_NAMES = {".gitignore", ".gitattributes"}
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
ZIP_MEMBER_LIMIT = 2000
ZIP_MEMBER_BYTES = 2 * 1024 * 1024

PATTERNS: dict[str, re.Pattern[str]] = {
    # C:\Users\<name>\... or C:/Users/<name>/... (any drive letter)
    "abs_win_user_path": re.compile(r"[A-Za-z]:[/\\]+Users[/\\]+[A-Za-z0-9_.-]+"),
    # /home/<name>/ or /Users/<name>/ (Unix-style)
    "abs_unix_user_path": re.compile(r"(?<![\w.])/(?:home|Users)/[A-Za-z0-9_.-]+/"),
    # reference-machine model names — deliberate in the hardware docs (see ALLOW_RULES)
    "machine_model": re.compile(r"ProArt|PX13", re.IGNORECASE),
    # machine serial-like identifiers — NEVER allowed anywhere
    "machine_serial": re.compile(r"HN\d{4,}", re.IGNORECASE),
    # secret-looking assignments (key/token/password = <long literal>)
    "secret_like": re.compile(
        r"(api[_-]?key|token|password|passwd|secret)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}",
        re.IGNORECASE,
    ),
    # known API-token prefixes (GitHub gho_/ghp_/fine-grained PATs, OpenAI sk-, AWS)
    "token_prefix": re.compile(
        r"\b(gho_|ghp_|ghu_|ghs_|github_pat_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})"
    ),
    # admin-share UNC paths that reveal users: \\host\c$\Users\<name>
    "unc_user_path": re.compile(
        r"\\\\[A-Za-z0-9_.-]+\\[A-Za-z]\$\\+Users\\+[A-Za-z0-9_.-]+", re.IGNORECASE
    ),
    # percent-encoded drive paths (C:%5CUsers%5Cname) — invisible to the plain patterns
    "pct_encoded_path": re.compile(r"%5[Cc][Uu]sers%5[Cc][A-Za-z0-9_.-]+"),
    "ssh_key": re.compile(r"ssh-(rsa|ed25519)\s+[A-Za-z0-9+/]{50,}"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}

# Explicit, auditable suppressions. A finding is suppressed only when the
# kind matches AND the path matches AND (if given) the snippet regex matches.
ALLOW_RULES: list[dict] = [
    # The README/docs hardware sections deliberately name the reference laptop.
    {"kind": "machine_model", "path_glob": "README*.md"},
    {"kind": "machine_model", "path_glob": "docs/GEMMA4_GFX1151_REGRESSION.md"},
    {"kind": "machine_model", "path_glob": "docs/COMPATIBILITY_MATRIX.md"},
    {"kind": "machine_model", "path_glob": "docs/GPU_BACKEND_VALIDATION.md"},
    {"kind": "machine_model", "path_glob": "docs/WINDOWS_INSTALL.md"},
    {"kind": "machine_model", "path_glob": "docs/ARCHITECTURE.md"},
    {"kind": "machine_model", "path_glob": "docs/ACCEPTANCE_MATRIX.md"},
    {"kind": "machine_model", "path_glob": "docs/RELEASE_READINESS.md"},
    {"kind": "machine_model", "path_glob": "docs/REPRODUCIBILITY.md"},
    # The scanner flags its own pattern definitions; that is by construction.
    {"kind": "machine_model", "path": "scripts/release_scan.py"},
    {"kind": "token_prefix", "path": "scripts/release_scan.py"},
    {"kind": "machine_serial", "path": "scripts/release_scan.py"},
    {"kind": "pct_encoded_path", "path": "scripts/release_scan.py"},
    # Red-team test fixtures deliberately contain fake paths/tokens/serials
    # (alice/bob/someone, ghp_…, HN7306) to exercise detection. They are test
    # vectors, never real data.
    {"kind": "abs_win_user_path", "path_glob": "tests/test_release_scan.py"},
    {"kind": "abs_unix_user_path", "path_glob": "tests/test_release_scan.py"},
    {"kind": "machine_model", "path_glob": "tests/test_release_scan.py"},
    {"kind": "machine_serial", "path_glob": "tests/test_release_scan.py"},
    {"kind": "secret_like", "path_glob": "tests/test_release_scan.py"},
    {"kind": "token_prefix", "path_glob": "tests/test_release_scan.py"},
    {"kind": "unc_user_path", "path_glob": "tests/test_release_scan.py"},
    {"kind": "pct_encoded_path", "path_glob": "tests/test_release_scan.py"},
    {"kind": "abs_win_user_path", "path_glob": "tests/test_publish_export.py"},
    {"kind": "abs_win_user_path", "path_glob": "tests/test_verify_bundle.py"},
    {"kind": "abs_unix_user_path", "path_glob": "tests/test_verify_bundle.py"},
    # git@github.com:owner/repo is an SSH remote, not an email address.
    {"kind": "email", "snippet_regex": r"git@github\.com"},
    # Placeholder usernames used in documentation examples.
    {"kind": "abs_win_user_path", "snippet_regex": r"Users[/\\]+<"},
    {"kind": "abs_win_user_path", "snippet_regex": r"Users[/\\]+x(?:[/\\]|\b)"},
    {"kind": "abs_unix_user_path", "snippet_regex": r"/(?:home|Users)/<"},
]


def _path_match(rule: dict, rel: str) -> bool:
    if "path_glob" in rule:
        return fnmatch.fnmatch(rel, rule["path_glob"])
    if "path" in rule:
        return rule["path"] == rel
    return True  # snippet-only rule: applies on any path


def _snippet_match(rule: dict, snippet: str) -> bool:
    if "snippet_regex" not in rule:
        return True
    return re.search(rule["snippet_regex"], snippet) is not None


def _suppressed(kind: str, rel: str, snippet: str) -> bool:
    return any(
        rule["kind"] == kind and _path_match(rule, rel) and _snippet_match(rule, snippet)
        for rule in ALLOW_RULES
    )


def scan_text(text: str, rel: str) -> list[dict]:
    """Return (unsuppressed) findings for one file's text."""
    findings: list[dict] = []
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            snippet = text[max(0, m.start() - 30) : m.end() + 30].replace("\n", " ")
            if _suppressed(name, rel, snippet):
                continue
            findings.append({"file": rel, "line": line_no, "kind": name, "snippet": snippet})
    return findings


def _iter_tree_files(base: Path, rel_prefix: str = "") -> Iterable[tuple[str, Path]]:
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if SKIP_PARTS & set(p.parts):
            continue
        if p.suffix in TEXT_SUFFIXES or p.name in TEXT_NAMES:
            yield rel_prefix + p.relative_to(base).as_posix(), p


def _read_for_scan(p: Path, rel: str) -> str:
    """File text as it would ship: evidence/** is redacted exactly like the
    export does, so enforcement here == enforcement on the publication surface
    while the dev-side originals keep their verbatim paths."""
    text = p.read_text(encoding="utf-8", errors="replace")
    if rel.startswith("evidence/"):
        text, _ = redact_text(text)
    return text


def scan_root(root: Path, label: str | None = None) -> tuple[int, list[dict]]:
    """Scan one tree root: PUBLISH_DIRS + PUBLISH_FILES under it."""
    root = Path(root)
    label = label or str(root)
    files_scanned = 0
    findings: list[dict] = []
    for d in PUBLISH_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for rel, p in _iter_tree_files(base, d + "/"):
            if _excluded(Path(rel)):
                continue  # never ships (same table the export uses)
            files_scanned += 1
            findings.extend(scan_text(_read_for_scan(p, rel), rel))
    for f in PUBLISH_FILES:
        p = root / f
        if p.is_file():
            files_scanned += 1
            findings.extend(scan_text(_read_for_scan(p, f), f))
    for f in findings:
        f["root"] = label
    return files_scanned, findings


def scan_zip(zip_path: Path) -> tuple[int, list[dict]]:
    """Scan text members of a release archive (e.g. dist/evidence-bundle-*.zip).

    Skipped members (over the count limit or oversized) become explicit
    `zip_member_skipped` findings so --enforce fails instead of silently
    fail-opening (independent review F-9).
    """
    files_scanned = 0
    findings: list[dict] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for n in names[ZIP_MEMBER_LIMIT:]:
            findings.append(
                {
                    "file": n,
                    "line": 0,
                    "kind": "zip_member_skipped",
                    "snippet": f"{zip_path.name}: member beyond ZIP_MEMBER_LIMIT not scanned",
                    "root": zip_path.name,
                }
            )
        for n in names[:ZIP_MEMBER_LIMIT]:
            info = zf.getinfo(n)
            suffix = Path(n).suffix
            if suffix not in TEXT_SUFFIXES:
                continue
            if info.file_size > ZIP_MEMBER_BYTES:
                findings.append(
                    {
                        "file": n,
                        "line": 0,
                        "kind": "zip_member_skipped",
                        "snippet": f"{zip_path.name}: member {info.file_size}B > "
                        f"{ZIP_MEMBER_BYTES}B not scanned",
                        "root": zip_path.name,
                    }
                )
                continue
            files_scanned += 1
            text = zf.read(n).decode("utf-8", errors="replace")
            for f in scan_text(text, n):
                f["root"] = zip_path.name
                findings.append(f)
    return files_scanned, findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--enforce", action="store_true", help="exit 1 when findings exist")
    ap.add_argument(
        "--root", type=Path, default=ROOT, help="repo root to scan (default: this repo)"
    )
    ap.add_argument(
        "--extra-root",
        type=Path,
        action="append",
        default=[],
        help="additional exported tree to scan (e.g. build/public); repeatable",
    )
    ap.add_argument(
        "--zip",
        type=Path,
        action="append",
        default=[],
        help="release archive to scan (e.g. dist/evidence-bundle-v1.0.0.zip); repeatable",
    )
    ap.add_argument("--json-out", type=Path, default=None, help="where to write the JSON report")
    args = ap.parse_args(argv)

    total_files = 0
    all_findings: list[dict] = []
    files, findings = scan_root(args.root)
    total_files += files
    all_findings += findings
    # a requested-but-missing export/archive must FAIL CLOSED: silently
    # skipping it would let an unbuilt/renamed tree bypass the scan (T1.1)
    for extra in args.extra_root:
        if not Path(extra).is_dir():
            print(f"ENFORCE: requested export root missing: {extra}")
            return 2
        files, findings = scan_root(Path(extra))
        total_files += files
        all_findings += findings
    for z in args.zip:
        if not Path(z).is_file():
            print(f"ENFORCE: requested archive missing: {z}")
            return 2
        files, findings = scan_zip(Path(z))
        total_files += files
        all_findings += findings

    report = {
        "scanned_roots": [str(args.root)]
        + [str(e) for e in args.extra_root]
        + [str(z) for z in args.zip],
        "files_scanned": total_files,
        "findings": all_findings,
    }
    out = args.json_out or (ROOT / "artifacts" / "rc1" / "release_scan.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    by_kind: dict[str, list[str]] = {}
    for f in all_findings:
        by_kind.setdefault(f["kind"], []).append(f["file"])
    print(
        f"scanned {total_files} files across {len(report['scanned_roots'])} root(s); {len(all_findings)} findings"
    )
    for k, v in sorted(by_kind.items()):
        print(f"  {k}: {len(v)} hits in {len(set(v))} files")
    print(f"report: {out}")
    if args.enforce and all_findings:
        print("ENFORCE: failing (findings above must be fixed or explicitly allowed)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
