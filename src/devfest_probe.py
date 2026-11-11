"""Isolated admission probe for the DevFest "The Last Seat" stage UI.

Loads the REAL check-in engine (checkin.py) from a given workspace in a
separate, isolated interpreter (``python -I``) and reports what the actual
code does today. The stage UI derives its before/after business behavior
ONLY from this probe — never from hardcoded state changes.

Usage (exit 0 always; JSON on stdout):
    python -I src/devfest_probe.py --workspace <dir> --scenario last [--name "Ada"]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path

# The probe must never load code outside the workspace it is pointed at.
MODULE_FILE = "checkin.py"

SCENARIOS = ("last", "full", "normal", "remaining")


def load_engine(workspace: Path):
    src = workspace / MODULE_FILE
    if not src.is_file():
        raise FileNotFoundError(f"{MODULE_FILE} not found in {workspace}")
    spec = importlib.util.spec_from_file_location("devfest_checkin_engine", src)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {src}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe(workspace: Path, scenario: str, name: str) -> dict:
    engine = load_engine(workspace)
    capacity = int(getattr(engine, "CAPACITY", 40))
    out = {
        "scenario": scenario,
        "capacity": capacity,
        "workspace_has_engine": True,
        "engine_source": MODULE_FILE,
    }
    if scenario == "remaining":
        out["admitted"] = None
        out["seats_remaining_at_39"] = int(engine.seats_remaining(capacity - 1))
        return out

    registered = {"last": capacity - 1, "full": capacity, "normal": capacity - 30}[scenario]
    try:
        rec = engine.register(name, registered)
        out.update(admitted=True, seat=rec.get("seat"), registered_before=registered)
    except Exception as e:  # the engine's own decision, reported verbatim
        out.update(
            admitted=False,
            registered_before=registered,
            error_type=type(e).__name__,
            error_text=str(e)[:300],
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--scenario", default="last", choices=SCENARIOS)
    ap.add_argument("--name", default="Guest Developer")
    args = ap.parse_args()
    try:
        result = probe(args.workspace.resolve(), args.scenario, args.name)
    except Exception as e:
        result = {
            "scenario": args.scenario,
            "workspace_has_engine": False,
            "probe_error": f"{type(e).__name__}: {e}",
        }
        if "--debug" in sys.argv:
            traceback.print_exc()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
