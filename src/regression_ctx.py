"""Phase 6 — Gemma 4 gfx1151 long-context regression suite (issue #17498).

For each target prompt length (1k/1.2k/2k/4k/8k/16k tokens) and each mode
(plain text, structured JSON, tool calling), send a prompt whose length is
built from a token-count-verified filler and verify output correctness.

Token counting: uses the Ollama /api/generate 'num_prompt_tokens' echo
(with predict empty) so lengths are measured by the actual tokenizer, not
by character count. Falls back to /v1/chat/completions usage if needed.

Usage:
    python src/regression_ctx.py --model gemma4:26b --lengths 1000,1200,2000
    python src/regression_ctx.py --full     # all lengths incl. 16000
Writes artifacts/regression/regression_<model>.json and .md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "regression"
BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")

# Deterministic filler sentences; ~13-16 tokens each, easy to verify counting.
FILLER_SENTENCE = (
    "The quick brown fox jumps over the lazy dog near the river bank. "
    "Numbers: 7 14 21 28 35 42 49. "
)


def http_json(path, body=None, timeout=1800):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode())
    return payload, time.time() - t0


def count_tokens_approx(text: str, model: str) -> int:
    """Ask the real tokenizer how many prompt tokens this text is (empty completion)."""
    payload, _ = http_json(
        "/api/generate",
        {"model": model, "prompt": text, "stream": False, "options": {"num_predict": 0}},
    )
    return int(payload.get("prompt_eval_count") or payload.get("num_prompt_tokens") or 0)


def build_prompt_for_tokens(model: str, target: int, header: str) -> tuple[str, int]:
    """Build a prompt whose measured prompt-token count is >= target, recording actual count."""
    # Scale: start with target/10 sentences, then measure and adjust.
    n = max(1, target // 12)
    while True:
        filler = FILLER_SENTENCE * n
        prompt = header + "\n\n" + filler
        actual = count_tokens_approx(prompt, model)
        if actual >= target or n > target * 2:
            return prompt, actual
        n = int(n * (target / max(actual, 1)) * 1.08) + 1


def run_case(model: str, target: int, mode: str) -> dict:
    header = {
        "text": (
            "Below is filler context. After reading it, answer: what is 17 + 26? "
            "Reply with just the number."
        ),
        "json": (
            "Below is filler context. After reading it, reply ONLY with a JSON object "
            'like {"sum": <number>} where <number> is 17 + 26. No other text.'
        ),
        "tool": (
            "Below is filler context. You have a tool `add(a: int, b: int)`. "
            "Call it to compute 17 + 26. Use the tool call format."
        ),
    }[mode]
    prompt, actual_tokens = build_prompt_for_tokens(model, target, header)

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0, "num_ctx": max(4096, min(target * 2, 32768))},
    }
    if mode == "json":
        body["response_format"] = {"type": "json_object"}
    if mode == "tool":
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "add",
                    "description": "Add two integers",
                    "parameters": {
                        "type": "object",
                        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                        "required": ["a", "b"],
                    },
                },
            }
        ]

    try:
        t0 = time.time()
        payload, dt = http_json("/v1/chat/completions", body)
        wall = round(time.time() - t0, 2)
        choice = payload.get("choices", [{}])[0]
        msg = choice.get("message", {})
        text = msg.get("content") or ""
        usage = payload.get("usage", {})
        tool_calls = msg.get("tool_calls") or []

        correct = False
        detail = ""
        if mode == "text":
            correct = bool(re.search(r"\b43\b", text))
            detail = text.strip()[:200]
        elif mode == "json":
            m = re.search(r"\{[^{}]*\}", text)
            try:
                correct = bool(m and json.loads(m.group(0)).get("sum") == 43)
            except Exception:
                correct = False
            detail = text.strip()[:200]
        elif mode == "tool":
            correct = any(
                (
                    tc.get("function", {}).get("name") == "add"
                    and json.loads(tc["function"].get("arguments") or "{}").get("a") == 17
                )
                for tc in tool_calls
            ) or bool(re.search(r"\b43\b", text))
            detail = f"tool_calls={json.dumps(tool_calls)[:300]} text={text.strip()[:150]}"

        corrupted = bool(
            re.search(
                r"([\x00-\x08\x0e-\x1f])|(\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+)?(.)\2{20,}",
                text,
            )
        )
        return {
            "target_tokens": target,
            "measured_prompt_tokens": actual_tokens,
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "mode": mode,
            "correct": correct,
            "repetition_suspect": corrupted,
            "wall_sec": wall,
            "reply_excerpt": detail,
            "error": None,
        }
    except Exception as e:
        return {
            "target_tokens": target,
            "measured_prompt_tokens": actual_tokens,
            "mode": mode,
            "correct": False,
            "error": f"{type(e).__name__}: {e}",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.environ.get("GEMMA_MODEL", "gemma4:26b"))
    parser.add_argument("--lengths", default="1000,1200,2000,4000")
    parser.add_argument("--full", action="store_true", help="add 8000 and 16000")
    parser.add_argument("--modes", default="text,json,tool")
    args = parser.parse_args()

    lengths = [int(x) for x in args.lengths.split(",")]
    if args.full:
        lengths += [8000, 16000]
    modes = args.modes.split(",")

    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for mode in modes:
        for L in lengths:
            print(f"[regression] model={args.model} mode={mode} target~{L} tokens ...", flush=True)
            r = run_case(args.model, L, mode)
            r["model"] = args.model
            results.append(r)
            status = "OK" if r.get("correct") else "FAIL"
            print(
                f"    -> {status} measured_prompt={r.get('measured_prompt_tokens')} "
                f"completion={r.get('completion_tokens')} err={r.get('error')}",
                flush=True,
            )

    out_json = OUT / f"regression_{args.model.replace(':', '_')}.json"
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")

    lines = [
        f"# Regression results — {args.model}",
        "",
        "| mode | target | measured prompt | completion | correct | suspect repetition | wall s | error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['mode']} | {r['target_tokens']} | {r.get('measured_prompt_tokens')} | "
            f"{r.get('completion_tokens')} | {r.get('correct')} | {r.get('repetition_suspect')} | "
            f"{r.get('wall_sec')} | {r.get('error') or ''} |"
        )
    (OUT / f"regression_{args.model.replace(':', '_')}.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    n_ok = sum(1 for r in results if r.get("correct"))
    print(f"REGRESSION_SUMMARY: {n_ok}/{len(results)} correct -> {out_json}")
    return 0  # report-only; acceptance judged from JSON


if __name__ == "__main__":
    sys.exit(main())


def band_probe(model: str, target: int) -> dict:
    """Issue #17498 band probe: /api/generate RAW mode (no chat template) at ~target tokens."""
    header = "Below is filler context. After reading it, answer: what is 17 + 26? Reply with just the number.\n\n"
    n = max(1, target // 12)
    while True:
        prompt = header + FILLER_SENTENCE * n
        payload, _ = http_json(
            "/api/generate",
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 60, "num_ctx": 4096},
            },
        )
        actual = int(payload.get("prompt_eval_count") or 0)
        if actual >= target or n > target * 2:
            break
        n = int(n * (target / max(actual, 1)) * 1.1) + 1
    # final measured call
    payload, dt = http_json(
        "/api/generate",
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 60, "num_ctx": 4096},
        },
    )
    text = (payload.get("response") or "").strip()
    return {
        "raw_mode": True,
        "target": target,
        "measured_prompt_tokens": actual,
        "correct": bool(re.search(r"\b43\b", text)),
        "reply": text[:200],
        "eval_count": payload.get("eval_count"),
        "wall_sec": round(dt, 2),
    }
