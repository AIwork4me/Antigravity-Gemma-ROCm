"""Offline verification (software-level isolation).

Physical network isolation requires admin changes (firewall/interface) which the
project must not perform. Instead we run the FULL agent task with process-level
proxy isolation:

  HTTP_PROXY/HTTPS_PROXY = http://127.0.0.1:9   (dead port -> any external
  HTTP(S) request from the server or SDK fails immediately)
  NO_PROXY covers localhost so local traffic is unaffected.

Ollama server is restarted (headless, default backend) with these env vars,
then the SDK agent runs the complete bug-fix task. Success = task green while
every possible outbound HTTP path is broken.

Usage: python scripts/offline_agent_test.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "stability"

OLLAMA = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # 1. restart server with poisoned proxies (user-scope, reversible)
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Stop-Process -Name llama-server -Force -ErrorAction SilentlyContinue; "
            "Stop-Process -Name ollama -Force -ErrorAction SilentlyContinue",
        ],
        timeout=60,
    )
    time.sleep(3)
    env = dict(os.environ)
    env["HTTP_PROXY"] = "http://127.0.0.1:9"
    env["HTTPS_PROXY"] = "http://127.0.0.1:9"
    env["http_proxy"] = "http://127.0.0.1:9"
    env["https_proxy"] = "http://127.0.0.1:9"
    env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    env["no_proxy"] = "localhost,127.0.0.1,::1"
    log_path = OUT / "offline-serve.log"
    # NOTE: never use stdout=PIPE here without draining — the server logs enough
    # to fill the OS pipe buffer and deadlock (observed as a 900s hang in run 1).
    logf = open(log_path, "ab")
    _srv = subprocess.Popen([OLLAMA, "serve"], env=env, stdout=logf, stderr=subprocess.STDOUT)
    up = False
    for _ in range(30):
        time.sleep(2)
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2).read()
            up = True
            break
        except Exception:
            pass
    if not up:
        print("OFFLINE_TEST: server failed to start")
        return 1
    print("[offline] server up with poisoned proxies (dead port 9, localhost exempt)")

    # 2. run the full agent task under the same env
    t0 = time.time()
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "src" / "demo_agent.py"),
            "--run-id",
            "offline",
            "--timeout",
            "900",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=1200,
    )
    wall = round(time.time() - t0, 1)
    print(proc.stdout[-500:])

    # 3. restore normal headless server
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Stop-Process -Name llama-server -Force -ErrorAction SilentlyContinue; "
            "Stop-Process -Name ollama -Force -ErrorAction SilentlyContinue",
        ],
        timeout=60,
    )
    time.sleep(2)
    subprocess.Popen([OLLAMA, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(2)
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2).read()
            break
        except Exception:
            pass

    summary_path = ROOT / "artifacts" / "agent-execution" / "run-offline" / "summary.json"
    ok = proc.returncode == 0 and summary_path.exists()
    if summary_path.exists():
        s = json.loads(summary_path.read_text(encoding="utf-8"))
        ok = ok and s.get("bug_fixed_after") and s.get("bug_present_before")
    result = {
        "mode": "software_proxy_isolation",
        "agent_exit": proc.returncode,
        "wall_s": wall,
        "task_green": bool(ok),
    }
    (OUT / "offline_agent_test.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"OFFLINE_TEST: {'PASS' if ok else 'FAIL'} -> {result}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
