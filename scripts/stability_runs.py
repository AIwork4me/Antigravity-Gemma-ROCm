"""Phase 8 — stability runs: N independent bug-fix tasks with baseline restore.

For each run: restore the buggy baseline, run the agent, verify with
independent pytest, classify outcome, record timings. Writes
artifacts/stability/stability.json (+ per-run evidence dirs from demo_agent).

Usage:
    python scripts/stability_runs.py --runs 10 --model gemma4:26b
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "stability"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--model", default="gemma4:26b")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    results = []
    for i in range(1, args.runs + 1):
        run_id = f"stability-{i:02d}"
        print(
            f"=== run {i}/{args.runs} ({run_id}) start {time.strftime('%H:%M:%S')} ===", flush=True
        )
        t0 = time.time()
        proc = subprocess.run(
            [
                py,
                str(ROOT / "src" / "demo_agent.py"),
                "--run-id",
                run_id,
                "--model",
                args.model,
                "--timeout",
                str(args.timeout),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=args.timeout + 600,
        )
        wall = round(time.time() - t0, 1)
        outcome = "SUCCESS" if proc.returncode == 0 else "FAIL"
        tail = (proc.stdout or "")[-600:]
        print(f"    -> {outcome} in {wall}s rc={proc.returncode}", flush=True)
        if outcome == "FAIL":
            print(tail, flush=True)
        results.append(
            {
                "run": i,
                "run_id": run_id,
                "outcome": outcome,
                "wall_s": wall,
                "returncode": proc.returncode,
                "summary_file": str(
                    ROOT / "artifacts" / "agent-execution" / f"run-{run_id}" / "summary.json"
                ),
            }
        )
        (OUT / "stability.json").write_text(
            json.dumps(
                {
                    "model": args.model,
                    "runs": results,
                    "n_success": sum(1 for r in results if r["outcome"] == "SUCCESS"),
                    "n_total": len(results),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    n_ok = sum(1 for r in results if r["outcome"] == "SUCCESS")
    print(f"STABILITY: {n_ok}/{len(results)} success (target >= {max(1, len(results) - 1)})")
    return 0 if n_ok >= max(1, len(results) - 1) else 1


if __name__ == "__main__":
    sys.exit(main())
