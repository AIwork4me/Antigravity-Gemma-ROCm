---
name: 🧰 Hardware report
about: Tell us how it ran on your machine — feeds the README hardware table
title: "[hw] "
labels: hardware-report
assignees: ""
---

Thanks for testing! Your report becomes a row in the README **Hardware compatibility**
table (a maintainer will add it, or feel free to PR it yourself — see CONTRIBUTING.md).

**Hardware**

| Item | Value |
|---|---|
| CPU | e.g. Ryzen AI MAX+ 395 |
| GPU / iGPU | e.g. Radeon 8060S (gfx1151) |
| RAM / VRAM | e.g. 128 GB unified |
| GPU driver | |
| OS + build | |
| Ollama version | |
| Backend (from preflight / server log) | ROCm / Vulkan / CUDA / CPU |

**Software**

| Item | Value |
|---|---|
| Model (`AGENT_MODEL`) | e.g. gemma4:26b |
| Quant / size | |
| Cold load time | |
| Bug-fix demo outcome | SUCCESS ×N / FAIL (N runs) |
| Rough generation speed | |

**Evidence (optional but golden)**

<!-- artifacts/agent-execution/run-*/summary.json or preflight output. -->
