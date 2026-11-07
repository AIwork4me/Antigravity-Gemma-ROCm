# Reproducibility

## 固定版本（本仓库实测组合）

| 组件 | 版本 | 锁定位置 |
|---|---|---|
| OS | Windows 11 家庭版 26200 (x64) | — |
| GPU 驱动 | Adrenalin 32.0.22032.6002 (2025-12-15) | — |
| Ollama | 0.40.13 (winget Ollama.Ollama) | `artifacts/ollama/pull-*.log` |
| 模型 | gemma4:26b（digest 见 `ollama list` / `artifacts/ollama/ollama-list.txt`） | manifest+blobs sha256 全校验 |
| Python | 3.13.13 (miniconda venv) | `.venv` |
| google-antigravity | 0.1.23 | `requirements.lock.txt`（pip freeze 全量） |
| google-genai | 2.34.0 | 同上 |

## 复现步骤（全新 Windows 同型机）

1. `winget install --id Ollama.Ollama -e`
2. `git clone <本仓库>` → `scripts\setup.ps1`
3. `python scripts\pull_parallel.py library\gemma4 26b`（或直接 `ollama pull`，若网络正常）
4. `scripts\preflight.ps1` → 期望全 PASS
5. `scripts\run-tests.ps1` → 单元 + API 冒烟 + live agent 测试
6. `src\regression_ctx.py --full` → 长上下文回归矩阵
7. `scripts\collect-evidence.ps1` → 产出证据包

## 证据链设计（为什么可信）

- **安装**：winget 输出、registry 卸载项、exe 版本
- **模型完整性**：registry manifest digest → 每 blob sha256 独立复算
- **后端**：server.log 原文行（时间戳）+ runner DLL 清单 + GPU 性能计数器采样
- **Agent 真实性**：任务前/后全文件 SHA256、工具调用事件流、独立 pytest 子进程退出码、
  git diff；测试先红后绿证明 bug 曾存在且被修复
- **离线**：断网前后 `/api/generate` 对比 + 无外部 DNS 查询声明（见 STABILITY_AND_OFFLINE_REPORT）

## 时间戳与机器标识

所有 artifacts 文件带采集时刻；机器为 ASUS ProArt PX13 / Ryzen AI MAX+ 395。
结果在其他 gfx1151 机器上预期可复现；不同驱动版本的后端行为差异已在
GPU_BACKEND_VALIDATION.md 记录判定方法。
