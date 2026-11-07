"""Minimal local agent runner (Phase 4 smoke tests).

Usage:
    python src/agent.py --prompt "..." [--model gemma4:26b] [--base-url http://127.0.0.1:11434/v1]

Streams the model reply token by token. Exit code 0 on success.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DEFAULT_BASE_URL, DEFAULT_MODEL, build_config


async def run(prompt: str, model: str, base_url: str, workspace: Path | None = None) -> int:
    workspaces = [workspace] if workspace else None
    if workspaces:
        config = build_config(model=model, base_url=base_url, workspaces=workspaces)
    else:
        config = build_config(model=model, base_url=base_url, workspaces=[Path.cwd()])
    async with __import__("google.antigravity", fromlist=["Agent"]).Agent(config) as agent:
        response = await agent.chat(prompt)
        async for token in response:
            print(token, end="", flush=True)
        print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Gemma 4 agent via Antigravity SDK")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args()
    return asyncio.run(run(args.prompt, args.model, args.base_url, args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())
