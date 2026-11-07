# Flagship run — what to look at

One complete, walkable proof that the local agent really fixes a bug.
All files in this directory come from actual runs on the reference machine
(AMD Ryzen AI MAX+ 395 / Radeon 8060S / gfx1151, Ollama, ROCm backend).

## The paper trail — CLI run `demo3` (gemma4:26b, 74 s, 5 tool calls)

| Step | File | What it proves |
|---|---|---|
| 1 | `mission.txt` | the natural-language task given to the agent |
| 2 | `tool_calls.json` | the real tool sequence: `view_file → view_file → run_command (pytest, RED) → edit_file → run_command (pytest, GREEN)` |
| 3 | `policy_audit.txt` | every `run_command` ALLOW/DENY decision from the policy sandbox |
| 4 | `pytest_before.json` / `pytest_after.json` | independent pytest **outside** the agent process: red before, green after |
| 5 | `workspace_before.json` / `workspace_after.json` | SHA256 of every workspace file before/after — only `stats_utils.py` changed |
| 6 | `git-diff.patch` | the exact diff the agent produced (one function, minimal fix) |
| 7 | `agent_reply.txt` | the model's own root-cause summary |
| — | `config.txt` | locked-down config used (model, endpoint, policies) |
| — | `summary.json` | machine-readable outcome (`bug_present_before: true`, `bug_fixed_after: true`) |

## The screen recording source — web run `web-170827` (gemma4:26b, 158 s)

`webui-recording.json` is the recorded SSE event stream of a live web-dashboard
run (same task, same config, captured 2026-11-07). It powers the README demo GIF
and the on-stage REPLAY fallback (`python -I src/webui.py --replay <file>`).

> The full evidence tree (345+ files: A/B backend probes, 10-run stability
> matrix, offline isolation proof, RC1 audit) ships as
> `evidence-bundle-v1.0.0.zip` attached to the
> [v1.0.0 release](../../releases) — see its `SHA256SUMS.txt`.

## Redaction note

Files under `evidence/` are machine-generated on the dev machine. The
published copy you are reading has absolute filesystem paths
(`C:\Users\<name>\...`) replaced deterministically with `<repo>` / `<home>`
placeholders by `scripts/publish_export.py` (the rule set is public and
stable). Workspace SHA256 values refer to file contents, not to these text
files, so the verification chain is unaffected.
