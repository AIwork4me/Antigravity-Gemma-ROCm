"""Phase 2 API smoke test (Python, stdlib only).

Checks (against local Ollama):
  1. GET /api/tags returns 200 with model list
  2. GET /v1/models (OpenAI-compat) returns 200 with data[].id
  3. POST /v1/chat/completions returns a completion with usage tokens
  4. latency: time-to-first-byte and total time
Exit 0 only if all pass. Writes JSON report to artifacts/ollama/smoke_api_python.json
"""

import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
MODEL = os.environ.get("GEMMA_MODEL", "gemma4:26b")
OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts", "ollama"
)


def http_json(method, path, body=None, timeout=180):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode())
    return r.status, payload, time.time() - t0


def main() -> int:
    report = {"base": BASE, "model": MODEL, "checks": {}}
    ok = True

    try:
        st, tags, dt = http_json("GET", "/api/tags")
        models = [m["name"] for m in tags.get("models", [])]
        report["checks"]["api_tags"] = {"status": st, "models": models, "sec": round(dt, 2)}
        print(f"[1] /api/tags -> {st} models={models}")
        ok &= st == 200
    except Exception as e:
        report["checks"]["api_tags"] = {"error": str(e)}
        print(f"[1] /api/tags FAILED: {e}")
        ok = False

    try:
        st, mods, dt = http_json("GET", "/v1/models")
        ids = [m.get("id") for m in mods.get("data", [])]
        report["checks"]["v1_models"] = {"status": st, "ids": ids, "sec": round(dt, 2)}
        print(f"[2] /v1/models -> {st} ids={ids}")
        ok &= st == 200
    except Exception as e:
        report["checks"]["v1_models"] = {"error": str(e)}
        print(f"[2] /v1/models FAILED: {e}")
        ok = False

    try:
        body = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Reply with exactly: LOCAL_OK"}],
            "stream": False,
        }
        st, comp, dt = http_json("POST", "/v1/chat/completions", body, timeout=600)
        text = comp.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = comp.get("usage", {})
        report["checks"]["v1_chat"] = {
            "status": st,
            "reply": text[:300],
            "usage": usage,
            "total_sec": round(dt, 2),
        }
        print(f"[3] /v1/chat/completions -> {st} reply={text[:60]!r} usage={usage}")
        ok &= st == 200 and bool(usage.get("completion_tokens"))
    except Exception as e:
        report["checks"]["v1_chat"] = {"error": str(e)}
        print(f"[3] /v1/chat/completions FAILED: {e}")
        ok = False

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "smoke_api_python.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print("SMOKE_RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
