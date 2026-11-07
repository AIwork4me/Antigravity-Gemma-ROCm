# Compatibility Matrix (Phase 1 — Official Sources vs Machine Reality)

Verified: 2026-11-01 (+08:00). All URLs fetched live on this date; ground truth for acceptance is **machine measurement** (Phase 0 baseline + later phases).

## A. Google Antigravity SDK

| Question | Official answer | Source |
|---|---|---|
| Package / install | `pip install google-antigravity`; compiled runtime binary ships in **platform-specific wheels on PyPI** — cloning repo alone is insufficient | GitHub README `google-antigravity/antigravity-sdk-python` (fetched 2026-11-01) |
| Local models announcement | 2026-09-23 blog: local workflows, Gemma 4 26B A4B via LiteRT-LM; **plug-and-play OpenAI-compatible servers (Ollama/LM Studio/vLLM) via `LocalOpenAIAgentConfig`** | developers.googleblog.com "Introducing Support for Local AI Models in the Antigravity SDK" |
| Local configs | `LiteRTAgentConfig(model_path=...)` (lightweight preset auto-applied) and `LocalOpenAIAgentConfig(model=..., base_url=...)` + `.lightweight()` | official example `examples/getting_started/local_models.py` @ main (fetched 2026-11-01) |
| `.lightweight()` semantics | optimizes prompt overhead, **restricts tools to essential actions**, disables subagents, tunes compaction thresholds for local hardware | local_models.py docstring |
| Default safety | `Agent` runs **read-only by default**; `capabilities=CapabilitiesConfig()` enables all tools incl. writes | README |
| Policies | `from google.antigravity.hooks.policy import deny, allow, ask_user, enforce`; e.g. `deny("*")`, `allow("view_file")`, `ask_user("run_command")` | README |
| Workspaces param | shown in blog code for `LiteRTAgentConfig(model_path=..., workspaces=[WORKING_DIR], policies=[policy.allow_all()])`; **whether `LocalOpenAIAgentConfig` accepts `workspaces` is not shown in official example — must be verified by introspection on the installed wheel (Phase 4)** | blog post |
| Windows support | wheels are platform-specific; **to verify: does PyPI serve a win_amd64 wheel and does the binary run? (Phase 4 install is the acceptance test)** | README note + PyPI (pending) |
| API key | LocalOpenAIAgentConfig path needs **no API key** (local server) | local_models.py |

Conflict note: the task brief expected `workspaces=[...]` on `LocalOpenAIAgentConfig`; the official example constructs it with only `model` + `base_url`. **Installed-package introspection wins**; discrepancy recorded here per policy.

## B. Gemma 4 on Ollama

| Question | Official answer | Source |
|---|---|---|
| Tags | `gemma4:26b` — **MoE 26B total / 3.8B active (A4B), 16–19 GB, 256K context**; badges: vision, **tools**, thinking, audio(partial) | ollama.com/library/gemma4 (fetched 2026-11-01) |
| Backup model | `gemma4:12b` — 12B dense, 7.7–8.0 GB, 256K context | same |
| Tool calling | supported family-wide (`tools` badge) | same |
| Library updated | ~2 weeks before fetch; 28.3M pulls | same |

## C. Ollama on Windows / AMD

| Question | Official answer | Source |
|---|---|---|
| Install | OllamaSetup.exe (user-dir, no admin) / `/DIR=` custom / zip; models default `%USERPROFILE%\.ollama`; change via `OLLAMA_MODELS`; logs at `%LOCALAPPDATA%\Ollama\server.log` | docs.ollama.com/windows (fetched 2026-11-01) |
| AMD on Windows | **ROCm needs ROCm v7/HIP7-capable driver; Windows ROCm list = RX 7600+ (7000 series), PRO W7500–W7900. gfx1151/Ryzen AI Max+ 395 is in the ROCm supported list for LINUX.** Vulkan is enabled **by default** on Windows as broader fallback | docs.ollama.com/gpu (fetched 2026-11-01) |
| Backend steering | `OLLAMA_VULKAN=0` (disable Vulkan), `GGML_VK_VISIBLE_DEVICES` (Vulkan device), `ROCR_VISIBLE_DEVICES` (ROCm device) | docs.ollama.com/gpu |
| OpenAI compat | `/v1/models`, `/v1/chat/completions` etc. on port 11434 | docs.ollama.com/api/openai-compatibility |

**Official docs vs reality tension**: docs' Windows ROCm table does not list gfx1151 iGPU, but community issue #17498 documents the **bundled `rocm_v7_1` runner running on gfx1151 under Windows** with `OLLAMA_IGPU_ENABLE=1`, `OLLAMA_LLM_LIBRARY=rocm_v7_1`, `OLLAMA_VULKAN=0`. → Machine test in Phase 3 decides.

## D. Issue #17498 (regression target)

| Field | Value |
|---|---|
| Title | "Gemma 4 12B produces corrupted output with ROCm on gfx1151 at around 1,200 prompt tokens" |
| Filed | 2026-08-01, Ollama **0.32.5**, Windows 11, Radeon 8060S, 64 GB RAM, ctx 4096 |
| Symptom | ROCm + gemma4:12b: normal ≤ ~1,125 prompt tokens; degrades ~1,166; unstable ~1,182; collapse ~1,200 (malformed/repetitive/unusable) |
| Controls | same model + Vulkan → normal; qwen2.5:14b + ROCm → normal (incl. 1,798-token prompt) |
| Status as fetched | issue body only retrieved; no fix/PR confirmed on the fetched page — **treat as unfixed until proven otherwise**; regression suite in Phase 6 (1k/1.2k/2k/4k/8k/16k tokens, both backends) |
| Environment knobs used | `OLLAMA_IGPU_ENABLE=1`, `OLLAMA_LLM_LIBRARY=rocm_v7_1`, `OLLAMA_VULKAN=0`, `ROCR_VISIBLE_DEVICES=0`, `GGML_VK_VISIBLE_DEVICES=-1`; log markers: `library=ROCm`, `compute=gfx1151`, `name=ROCm0` |

## E. ROCm vs Vulkan — observability plan (how we will distinguish)

1. `%LOCALAPPDATA%\Ollama\server.log` runner selection lines: `library=ROCm` + `compute=gfx1151` vs `library=Vulkan` (`vulkan_v7`? actual marker read from log).
2. Runner process/DLL set (hip DLLs vs vulkan loader) — via Process Explorer-style module query (PowerShell `Get-Process -Module`).
3. Controlled A/B with `OLLAMA_VULKAN=0` vs unset.
4. GPU compute activity + VRAM (dxdiag/Task Manager GPU counters) — supporting only, never primary proof.

## F. Versions being installed (decision record)

| Component | Version chosen | Why |
|---|---|---|
| Ollama | 0.40.13 (winget `Ollama.Ollama`, official) | latest stable, far newer than issue's 0.32.5 → regression meaningful |
| Model | `gemma4:26b` (primary), `gemma4:12b` (backup + issue reproduction) | per mission |
| google-antigravity | latest stable from PyPI at install time | official channel |
| Python | isolated venv from miniconda 3.13 | keep base env untouched |
