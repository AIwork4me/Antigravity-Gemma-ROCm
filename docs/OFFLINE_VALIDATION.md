# Offline Validation (RC1 Phase 4)

- 时间：2026-11-01 22:10 CST；全部为本轮**新鲜证据**（不复用上一轮 PASS）
- 结论先行：**软件级隔离 = PASS（本轮复验）**；**物理断网 = MANUAL_REQUIRED**（不冒充 PASS）

## 1. 端点盘点（出网面分析）

### Ollama 0.40.13（server 进程潜在出网点）

| 端点 | 用途 | 本项目是否触及 |
|---|---|---|
| `registry.ollama.ai` / R2 blob 存储 | 模型 pull | 仅安装期（已完成；演示期无 pull） |
| `ollama.com`（`OLLAMA_REMOTES`） | 云模型/账号能力 | 未用（本地 tag；`/api/me` 401 即返回） |
| `/api/experimental/model-recommendations` | 推荐缓存（服务日志见 3h33m 周期任务） | 存在周期性外呼可能 —— **离线演示建议设 `OLLAMA_NO_CLOUD=true`**（官方开关，随受控启动脚本设置，不改系统） |
| 本地 `127.0.0.1:11434` | 推理 API | 唯一必需端点 |

### Antigravity SDK 0.1.23 + google-genai 2.34.0（python 进程）

- 对 `site-packages/google/antigravity/**` 与 `google/genai/**` 全量 URL 扫描：**运行时代码
  无遥测/上报端点**（命中的 https URL 均为 docstring 文档链接与 `example.com` 占位符；
  真实外部端点 `aiplatform.googleapis.com` 等仅存在于**云配置**代码路径）。
- `LocalOpenAIAgentConfig` 路径实测无 API key、无外呼（死端点错误唯一 URL 为本地端点，
  RC1 前轮 Test E；本轮软件隔离下完整任务通过再次佐证）。

## 2. 本轮新鲜证据

| 验证 | 方法 | 结果 | 证据 |
|---|---|---|---|
| 推理期零外联 | 生成期间枚举 ollama/llama-server 全部 ESTABLISHED 套接字 | **仅 127.0.0.1↔127.0.0.1**，无非回环远端 | `artifacts/rc1/loopback_rc1.json` |
| 软件级隔离全任务 | `HTTP(S)_PROXY=http://127.0.0.1:9`（死端口）+ `NO_PROXY=localhost` 重启服务并运行**完整 Agent Demo**（外呼即失败的环境） | **PASS**：exit 0，先红后绿，124.1s | `artifacts/stability/offline_agent_test.json`（本轮覆盖）+ `artifacts/agent-execution/run-offline/` |

## 3. 物理断网流程（无需管理员权限，人工执行）— **MANUAL_REQUIRED**

本项目脚本不做任何系统级网络改动。人工步骤（约 30 秒）：

1. **断网**：`Win+A` 快速设置 → 关闭 Wi-Fi（或拔网线 / 开启飞行模式）
2. **验证**：`.\scripts\preflight.ps1` → 应仍全 PASS（所有检查均为本地）
3. **演示**：`.\.venv\Scripts\python.exe src\demo_agent.py --run-id manual-offline`
4. **判定**：`artifacts\agent-execution\run-manual-offline\summary.json` 中
   `bug_present_before=true` 且 `bug_fixed_after=true` 即离线成功
5. **恢复联网**：同步骤 1 反向操作；然后 `.\scripts\demo-status.ps1` 应回 `READY`
   （服务与模型全程未动，无需重启）

> 若现场无法执行物理断网，以 §2 软件级隔离证据 + 回环审计作为离线能力说明，并如实告知
> 观众"物理断网为手册步骤"。

## 4. 残余说明

- Ollama 推荐缓存周期任务在默认配置下可能外呼：离线场景按 §1 建议加 `OLLAMA_NO_CLOUD=true`
  （受控启动，官方开关，随进程失效，不落系统配置）。
- 断网期间 SDK 的 pip 检查等不适用（运行期无 pip 活动）；Windows NCSI 探测属 OS 行为，与
  本项目进程无关。
