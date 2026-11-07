"""Release readiness scan: flag private info in the PUBLISHABLE set.

Publishable set = code + docs + scenarios (NOT artifacts/, NOT .venv, NOT logs).
Flags: absolute local paths, usernames, machine-serial-like strings, secrets.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLISH_DIRS = ["src", "tests", "scripts", "scenarios", "docs"]
PUBLISH_FILES = [
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "pytest.ini",
    "requirements.lock.txt",
    ".gitignore",
    "pull_parallel.py",
]

PATTERNS = {
    "abs_user_path": re.compile(r"[A-Za-z]:[/\\]+Users[/\\]+[A-Za-z0-9_.-]+"),
    "username_rocm": re.compile(r"\brocm\b", re.IGNORECASE),
    "machine_model": re.compile(r"ProArt|PX13|HN7306", re.IGNORECASE),
    "secret_like": re.compile(
        r"(api[_-]?key|token|password|passwd|secret)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}",
        re.IGNORECASE,
    ),
    "ssh_key": re.compile(r"ssh-(rsa|ed25519)\s+[A-Za-z0-9+/]{50,}"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}

findings: list[dict] = []
files_scanned = 0
for d in PUBLISH_DIRS:
    for p in (ROOT / d).rglob("*"):
        if (
            p.is_file()
            and p.suffix in {".py", ".md", ".ps1", ".sh", ".txt", ".ini", ".patch"}
            and "__pycache__" not in p.parts
            and ".pytest_cache" not in p.parts
        ):
            files_scanned += 1
            text = p.read_text(encoding="utf-8", errors="replace")
            for name, rx in PATTERNS.items():
                for m in rx.finditer(text):
                    line_no = text.count("\n", 0, m.start()) + 1
                    snippet = text[max(0, m.start() - 30) : m.end() + 30].replace("\n", " ")
                    findings.append(
                        {
                            "file": str(p.relative_to(ROOT)),
                            "line": line_no,
                            "kind": name,
                            "snippet": snippet,
                        }
                    )
for f in PUBLISH_FILES:
    p = ROOT / f
    if p.exists():
        files_scanned += 1
        text = p.read_text(encoding="utf-8", errors="replace")
        for name, rx in PATTERNS.items():
            for m in rx.finditer(text):
                findings.append(
                    {
                        "file": f,
                        "line": text.count("\n", 0, m.start()) + 1,
                        "kind": name,
                        "snippet": text[max(0, m.start() - 30) : m.end() + 30].replace("\n", " "),
                    }
                )

out = ROOT / "artifacts" / "rc1" / "release_scan.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(
    __import__("json").dumps(
        {"files_scanned": files_scanned, "findings": findings}, indent=2, ensure_ascii=False
    ),
    encoding="utf-8",
)
by_kind = {}
for f in findings:
    by_kind.setdefault(f["kind"], []).append(f["file"])
print(f"scanned {files_scanned} files; {len(findings)} findings")
for k, v in by_kind.items():
    print(f"  {k}: {len(v)} hits in {len(set(v))} files")
