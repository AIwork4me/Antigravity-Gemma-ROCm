"""Parallel range downloader for Ollama registry blobs over IPv4.

Splits each blob into N byte-range chunks, downloads them concurrently with
curl -4 (working around this machine's broken IPv6 route to Cloudflare R2),
concatenates, and verifies sha256 against the manifest digest before
installing into the Ollama content store.

Usage:
    python scripts/pull_parallel.py library/gemma4 26b [--chunks 24]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REG = "https://registry.ollama.ai"
HOME = Path.home()
MODELS = Path(os.environ.get("OLLAMA_MODELS", HOME / ".ollama" / "models"))
BLOBS = MODELS / "blobs"
WORK = Path(__file__).resolve().parent.parent / "artifacts" / "ollama" / "work"
LOGF = Path(__file__).resolve().parent.parent / "artifacts" / "ollama" / "pull-parallel.log"
ACCEPT = "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def curl4(args: list[str], timeout: int = 0) -> subprocess.CompletedProcess:
    cmd = [
        "curl",
        "-4",
        "-sS",
        "--retry",
        "3",
        "--retry-delay",
        "3",
        "--connect-timeout",
        "20",
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or None)


def resolve_manifest(repo: str, tag: str) -> dict:
    m = WORK / f"manifest-{tag}.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    out = curl4(
        [
            "-H",
            f"Accept: {ACCEPT}",
            "-o",
            str(m),
            "-w",
            "%{http_code}",
            f"{REG}/v2/{repo}/manifests/{tag}",
        ]
    )
    if out.stdout.strip() != "200":
        raise RuntimeError(f"manifest fetch failed: {out.stdout} {out.stderr}")
    manifest = json.loads(m.read_text(encoding="utf-8"))
    if "manifests" in manifest:  # manifest list -> linux/amd64
        for e in manifest["manifests"]:
            p = e.get("platform", {})
            if p.get("os") == "linux" and p.get("architecture") == "amd64":
                d = e["digest"]
                out = curl4(
                    [
                        "-H",
                        f"Accept: {ACCEPT}",
                        "-o",
                        str(m),
                        "-w",
                        "%{http_code}",
                        f"{REG}/v2/{repo}/manifests/{d}",
                    ]
                )
                if out.stdout.strip() != "200":
                    raise RuntimeError(f"arch manifest fetch failed: {out.stdout} {out.stderr}")
                manifest = json.loads(m.read_text(encoding="utf-8"))
                break
    return manifest


def get_signed_url(repo: str, digest: str) -> str:
    """Follow the registry redirect to obtain the final signed blob URL.

    A GET on the registry blob URL returns 307 to a signed R2 URL; HEAD may
    instead answer 200 directly, so we probe with GET and read redirect_url.
    """
    url = f"{REG}/v2/{repo}/blobs/{digest}"
    out = curl4(["-o", "/dev/null", "-w", "%{redirect_url}", url])
    redirect = out.stdout.strip()
    if redirect:
        return redirect
    # no redirect: registry serves the blob directly
    return url


def download_chunk(url: str, path: Path, start: int, end: int) -> tuple[int, str]:
    want = end - start + 1
    for _attempt in range(6):
        r = subprocess.run(
            [
                "curl",
                "-4",
                "-sS",
                "--retry",
                "2",
                "--connect-timeout",
                "20",
                "--speed-time",
                "60",
                "--speed-limit",
                "20480",
                "-r",
                f"{start}-{end}",
                "-o",
                str(path),
                url,
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and path.exists() and path.stat().st_size == want:
            return want, ""
        time.sleep(3)
    return (
        path.stat().st_size if path.exists() else 0
    ), f"chunk {path.name} incomplete after retries: {r.stderr[:150]}"


def fetch_blob_parallel(repo: str, digest: str, size: int, chunks: int) -> bool:
    dest = BLOBS / f"{digest.replace(':', '-')}"
    if dest.exists() and dest.stat().st_size == size and sha256_file(dest) == digest.split(":")[1]:
        log(f"SKIP {digest[:20]} (already complete)")
        return True
    url = get_signed_url(repo, digest)
    cdir = WORK / digest.replace(":", "-")
    cdir.mkdir(parents=True, exist_ok=True)

    chunk_size = size // chunks
    jobs = []
    for i in range(chunks):
        start = i * chunk_size
        end = (size - 1) if i == chunks - 1 else (start + chunk_size - 1)
        cpath = cdir / f"part{i:03d}"
        want = end - start + 1
        if cpath.exists() and cpath.stat().st_size == want:
            continue  # already done (resume)
        jobs.append((url, cpath, start, end))

    t0 = time.time()
    log(f"FETCH {digest[:20]} size={size / 2**30:.2f}GiB chunks={chunks} parallel={len(jobs)}")
    if jobs:
        with cf.ThreadPoolExecutor(max_workers=min(12, len(jobs))) as ex:
            futs = {ex.submit(download_chunk, u, p, s, e): p for u, p, s, e in jobs}
            for fut in cf.as_completed(futs):
                n, err = fut.result()
                if err:
                    log(f"  ERROR {err}")
    # verify all chunks
    ok = True
    for i in range(chunks):
        start = i * chunk_size
        end = (size - 1) if i == chunks - 1 else (start + chunk_size - 1)
        cpath = cdir / f"part{i:03d}"
        if not cpath.exists() or cpath.stat().st_size != end - start + 1:
            log(f"  MISSING chunk {i}")
            ok = False
    if not ok:
        return False
    # concatenate
    with open(dest, "wb") as out:
        for i in range(chunks):
            cpath = cdir / f"part{i:03d}"
            with open(cpath, "rb") as f:
                while True:
                    buf = f.read(1 << 22)
                    if not buf:
                        break
                    out.write(buf)
    dt = time.time() - t0
    rate = size / dt / 2**20 if dt else 0
    good = sha256_file(dest) == digest.split(":")[1]
    log(f"{'VERIFIED' if good else 'VERIFY_FAILED'} {digest[:20]} in {dt:.0f}s ({rate:.1f} MiB/s)")
    if good:
        for i in range(chunks):
            (cdir / f"part{i:03d}").unlink(missing_ok=True)
        cdir.rmdir()
    else:
        dest.unlink(missing_ok=True)
    return good


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("tag")
    ap.add_argument("--chunks", type=int, default=24)
    args = ap.parse_args()

    BLOBS.mkdir(parents=True, exist_ok=True)
    manifest = resolve_manifest(args.repo, args.tag)
    entries = [(manifest["config"]["digest"], manifest["config"]["size"])]
    entries += [(layer["digest"], layer["size"]) for layer in manifest["layers"]]
    total = sum(s for _, s in entries)
    log(f"plan: {len(entries)} blobs, total {total / 2**30:.2f} GiB")

    for digest, size in entries:
        n = args.chunks if size > 64 << 20 else 1
        if not fetch_blob_parallel(args.repo, digest, size, n):
            log(f"FAILED blob {digest}")
            return 1

    mdest = MODELS / "manifests" / "registry.ollama.ai" / args.repo / args.tag
    mdest.parent.mkdir(parents=True, exist_ok=True)
    (WORK / f"manifest-{args.tag}.json").replace(mdest)
    log(f"MANIFEST_INSTALLED {args.repo}:{args.tag}")
    log("PULL_PARALLEL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
