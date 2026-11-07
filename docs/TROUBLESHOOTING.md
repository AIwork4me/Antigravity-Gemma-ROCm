# Troubleshooting

## 下载 / 模型

| 症状 | 原因 | 处置 |
|---|---|---|
| `ollama pull` 卡 "pulling manifest" 后报 `dial tcp [2606:...]` | 本机 IPv6 到 Cloudflare R2 路由坏 | 用 `scripts\pull_parallel.py`（curl -4 并行分段，sha256 校验）；不改系统网络 |
| pull 中断后想续传 | — | pull_parallel.py 按 chunk 断点续传，重复执行即可 |
| `Error: model not found` 但 blob 已就位 | manifest 未安装 | 检查 `%USERPROFILE%\.ollama\models\manifests\registry.ollama.ai\library\gemma4\26b` 是否存在 |
| 磁盘不足 | 模型 ~16GB | 设 `OLLAMA_MODELS` 指向大盘后重启 Ollama（官方支持路径） |

## GPU / 后端

| 症状 | 原因 | 处置 |
|---|---|---|
| server.log 无 `library=ROCm` | 驱动太旧 / HIP 运行时不可用 | 升级 Adrenalin（≥2025-12 版实测可用）；或 Vulkan 路径 |
| Vulkan 侧 `dropping integrated GPU` | Ollama 默认丢弃 iGPU 的 Vulkan 设备 | `OLLAMA_IGPU_ENABLE=1`（官方开关） |
| 想强制某后端 | — | `OLLAMA_LLM_LIBRARY=rocm_v7_1` 或 `vulkan`；改后重启服务 |
| `ollama ps` 显示 CPU 或 0% GPU | 模型未全量 offload | 看 server.log `offloaded X/Y layers`；显存/UMA 不足时关大应用 |
| 长输入输出乱码/复读 | 疑似 issue #17498（ROCm+gfx1151 ≥~1.2k tokens） | 跑 `src\regression_ctx.py` 复现；切 Vulkan 后端对比；保留证据 |

## Antigravity SDK

| 症状 | 原因 | 处置 |
|---|---|---|
| `pip install google-antigravity` 无 Windows wheel | PyPI 未提供该平台 | 记录为 BLOCKED；本机 0.1.23 实测有 win_amd64 支持路径（venv 内 pywin32） |
| Agent 一直不调用工具 | lightweight 预设裁剪了工具 | 确认 capabilities.enabled_tools 含 run_command/edit_file；或去掉 `.lightweight()` 观察 |
| run_command 全被拒 | 白名单 handler 判定 | 查 `artifacts/agent-execution/run-*/policy_audit.txt` 的 DENY 原因 |
| 模型回复空 | base_url 错 / 服务未起 | `Invoke-RestMethod http://127.0.0.1:11434/v1/models` 自检 |
| Windows 长路径/权限错误 | 工作区路径含空格或 OneDrive | 使用本项目固定路径 `<PROJECT_ROOT>` |

## 演示现场

- 首次冷加载 ~1-2 分钟：提前 `ollama run` 预热
- 若系统休眠 → GPU 上下文丢失：重新跑 start-demo.ps1
- 兜底：展示 artifacts 预录证据 + summary.json
