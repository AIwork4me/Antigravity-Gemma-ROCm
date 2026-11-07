"""Live agent tests (marked 'live'; require Ollama + gemma4 loaded).

Covers the Phase 4/5 acceptance behaviors:
  - SDK initializes and reaches the LOCAL model (no cloud fallback)
  - real tool calls happen during a workspace task
  - SDK fails loudly when Ollama is stopped (diagnosable error, not silent cloud)
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

MODEL = os.environ.get("GEMMA_MODEL", "gemma4:26b")
BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")

pytestmark = pytest.mark.live


def _ollama_has_model() -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
            tags = json.loads(r.read().decode())
        return any(
            m["name"] == MODEL or m["name"].startswith(MODEL + ":") or m["name"] == MODEL
            for m in tags.get("models", [])
        )
    except Exception:
        return False


requires_model = pytest.mark.skipif(
    not _ollama_has_model(), reason=f"{MODEL} not present in local Ollama"
)


@requires_model
def test_sdk_connects_local_model_and_streams():
    """Phase 4 Test C+D: full round trip, model reply is non-empty."""
    import asyncio

    from google.antigravity import Agent

    from config import build_config

    async def run():
        cfg = build_config(model=MODEL, base_url=BASE, workspaces=[ROOT / "demo_workspace"])
        async with Agent(cfg) as agent:
            response = await agent.chat("In one short sentence, say you are running locally.")
            tokens = []
            async for t in response:
                tokens.append(t)
            return "".join(tokens)

    reply = asyncio.run(asyncio.wait_for(run(), timeout=600))
    assert len(reply.strip()) > 0, "empty reply from local model"


def test_sdk_fails_loudly_when_server_down():
    """Phase 4 Test E: pointing at a dead port must raise, not fall back to cloud."""
    import asyncio

    from google.antigravity import Agent

    from config import build_config

    async def run():
        cfg = build_config(
            model=MODEL, base_url="http://127.0.0.1:9/v1", workspaces=[ROOT / "demo_workspace"]
        )
        async with Agent(cfg) as agent:
            response = await agent.chat("hello")
            async for _ in response:
                pass

    with pytest.raises(Exception) as ei:
        asyncio.run(asyncio.wait_for(run(), timeout=180))
    # the error must mention connection/target — i.e. a diagnosable transport failure
    msg = str(ei.value).lower()
    assert any(
        k in msg
        for k in (
            "connect",
            "connection",
            "refused",
            "unreachable",
            "error",
            "failed",
            "timeout",
            "503",
            "reset",
        )
    ), f"error not diagnosable as transport failure: {ei.value!r}"


@requires_model
def test_agent_real_tool_execution_fixes_bug(tmp_path):
    """Phase 5 core: agent must really use tools and fix the planted bug."""
    import asyncio

    from google.antigravity import Agent

    from config import DEMO_WORKSPACE, build_config
    from demo_agent import restore_bug_baseline, run_pytest_independently

    restore_bug_baseline()
    pre = run_pytest_independently(DEMO_WORKSPACE)
    assert pre["exit_code"] != 0, "bug baseline missing — demo workspace already fixed?"

    async def run():
        cfg = build_config(model=MODEL, base_url=BASE, workspaces=[DEMO_WORKSPACE])
        tool_calls = []
        async with Agent(cfg) as agent:
            response = await agent.chat(
                "The pytest suite in this workspace is failing. Run the tests, find the bug "
                "in stats_utils.py, fix it minimally, re-run the tests until they pass, then summarize."
            )
            async for call in response.tool_calls:
                tool_calls.append(getattr(call, "name", None))
            async for _ in response:
                pass
        return tool_calls

    tools = asyncio.run(asyncio.wait_for(run(), timeout=1200))
    assert tools, "no tool calls observed — agent did not use the harness"
    post = run_pytest_independently(DEMO_WORKSPACE)
    assert post["exit_code"] == 0, f"tests still failing after agent run:\n{post['stdout'][-800:]}"
