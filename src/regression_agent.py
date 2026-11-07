"""Phase 6 — agent-level long-context regression scenarios (D/E/F).

D. Multi-turn agent conversation (context accumulates across turns)
E. Long tool return (agent must consume a large tool output and still answer)
F. Recovery after a tool/agent failure (agent continues after an error)

Runs against the live SDK + local Ollama. Writes artifacts/regression/agent_scenarios.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import build_config  # noqa: E402

MODEL = os.environ.get("GEMMA_MODEL", "gemma4:26b")
BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OUT = ROOT / "artifacts" / "regression"


def long_tool_output(n: int) -> str:
    """Simulated big tool result (~n tokens of structured lines)."""
    lines = [
        f"module_{i:05d}: status=ok latency_ms={i % 97 + 3} version=1.{i % 9}"
        for i in range(n // 8)
    ]
    return "\n".join(lines)


async def scenario_d_multi_turn() -> dict:
    from google.antigravity import Agent

    cfg = build_config(model=MODEL, base_url=BASE, workspaces=[ROOT / "demo_workspace"])
    t0 = time.time()
    answers = []
    async with Agent(cfg) as agent:
        for _i, q in enumerate(
            [
                "Remember the number 4271. Just reply OK.",
                "Now remember the word PINEAPPLE. Reply OK.",
                "What number did I ask you to remember first? Reply with only the number.",
                "What word came after the number? Reply with only the word.",
            ]
        ):
            response = await agent.chat(q)
            text = []
            async for tok in response:
                text.append(tok)
            answers.append("".join(text).strip())
    ok = ("4271" in answers[2]) and ("PINEAPPLE" in answers[3].upper())
    return {
        "scenario": "D_multi_turn",
        "ok": ok,
        "answers": answers,
        "elapsed_s": round(time.time() - t0, 1),
    }


async def scenario_e_long_tool_return() -> dict:
    from google.antigravity import Agent

    def get_log_dump() -> str:
        """Custom tool returning a large output (~1500 tokens)."""
        return long_tool_output(1500)

    cfg = build_config(
        model=MODEL,
        base_url=BASE,
        workspaces=[ROOT / "demo_workspace"],
        allow_tools=["get_log_dump"],
    )
    cfg.tools = [get_log_dump]
    t0 = time.time()
    async with Agent(cfg) as agent:
        response = await agent.chat(
            "Call the tool get_log_dump, then tell me ONLY the status of module_00042 "
            "and the total number of lines, in the form 'module_00042 status=<s> lines=<n>'."
        )
        text = []
        async for tok in response:
            text.append(tok)
        reply = "".join(text)
    ok = (
        ("module_00042" in reply or "00042" in reply)
        and ("ok" in reply.lower())
        and ("187" in reply or "188" in reply)
    )
    return {
        "scenario": "E_long_tool_return",
        "ok": ok,
        "reply": reply[:400],
        "elapsed_s": round(time.time() - t0, 1),
    }


async def scenario_f_recovery() -> dict:
    from google.antigravity import Agent

    calls = {"n": 0}

    def flaky_tool() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated transient tool failure")
        return "recovered: value=777"

    cfg = build_config(
        model=MODEL, base_url=BASE, workspaces=[ROOT / "demo_workspace"], allow_tools=["flaky_tool"]
    )
    cfg.tools = [flaky_tool]
    t0 = time.time()
    reply = ""
    err = None
    try:
        async with Agent(cfg) as agent:
            response = await agent.chat(
                "Call flaky_tool. If it errors, call it again, then report the value it returns."
            )
            text = []
            async for tok in response:
                text.append(tok)
            reply = "".join(text)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    ok = ("777" in reply) and calls["n"] >= 2
    return {
        "scenario": "F_recovery",
        "ok": ok,
        "tool_calls": calls["n"],
        "reply": reply[:400],
        "error": err,
        "elapsed_s": round(time.time() - t0, 1),
    }


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for sc in (scenario_d_multi_turn, scenario_e_long_tool_return, scenario_f_recovery):
        print(f"[agent-scenario] {sc.__name__} ...", flush=True)
        try:
            r = await asyncio.wait_for(sc(), timeout=900)
        except Exception as e:
            r = {"scenario": sc.__name__, "ok": False, "error": f"{type(e).__name__}: {e}"}
        results.append(r)
        print(f"    -> ok={r.get('ok')} err={r.get('error')}", flush=True)
    (OUT / "agent_scenarios.json").write_text(
        json.dumps({"model": MODEL, "results": results}, indent=2), encoding="utf-8"
    )
    n_ok = sum(1 for r in results if r.get("ok"))
    print(f"AGENT_SCENARIOS: {n_ok}/{len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
