"""Run the Phase 2 matrix: 3 scenarios x 3 runs, aggregate stats."""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVID = ROOT / "artifacts" / "multitask"

rows = []
for sc in ("A_boundary", "B_writetests", "C_excfix"):
    for k in range(1, 4):
        t0 = time.time()
        p = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "src" / "scenario_agent.py"),
                "--scenario",
                sc,
                "--run",
                str(k),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=1200,
        )
        wall = round(time.time() - t0, 1)
        acc_path = EVID / f"{sc}-run{k}" / "acceptance.json"
        acc = json.loads(acc_path.read_text(encoding="utf-8")) if acc_path.exists() else {}
        row = {
            "scenario": sc,
            "run": k,
            "accept": acc.get("accept"),
            "why": acc.get("why"),
            "agent_elapsed_s": acc.get("elapsed_s"),
            "driver_wall_s": wall,
            "n_tool_calls": acc.get("n_tool_calls"),
        }
        rows.append(row)
        print(
            f"{sc} run{k}: {'ACCEPT' if row['accept'] else 'REJECT'} "
            f"agent={row['agent_elapsed_s']}s wall={wall}s tools={row['n_tool_calls']} {row['why']}",
            flush=True,
        )
        (EVID / "matrix.json").write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
        )

agg = {}
for sc in ("A_boundary", "B_writetests", "C_excfix"):
    rs = [r for r in rows if r["scenario"] == sc]
    ok = [r for r in rs if r["accept"]]
    agg[sc] = {
        "success": f"{len(ok)}/{len(rs)}",
        "avg_agent_s": round(sum(r["agent_elapsed_s"] or 0 for r in ok) / len(ok), 1)
        if ok
        else None,
        "min_s": min((r["agent_elapsed_s"] for r in ok if r["agent_elapsed_s"]), default=None),
        "max_s": max((r["agent_elapsed_s"] for r in ok if r["agent_elapsed_s"]), default=None),
        "avg_tools": round(sum(r["n_tool_calls"] or 0 for r in ok) / len(ok), 1) if ok else None,
        "fail_reasons": [r["why"] for r in rs if not r["accept"]],
    }
(EVID / "aggregate.json").write_text(
    json.dumps(agg, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(json.dumps(agg, indent=2, ensure_ascii=False))
