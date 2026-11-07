# Stability & Offline Report (Phase 8)

- 时间：2026-11-01 20:20–21:00 CST；模型 gemma4:26b @ 默认 ROCm 后端
- 原始数据：`artifacts/stability/`

## 1. 运维健壮性检查（5/5 PASS）— `phase8_checks.json`

| 检查 | 结果 | 实测 |
|---|---|---|
| 冷启动后模型重载 | ✅ | 卸载后重载+推理 **65.1s**，回复精确 `COLD_START_OK` |
| Ollama 重启后 SDK 重连 | ✅ | 杀服务→无头重启→SDK 会话成功 `RECONNECT_OK` |
| 请求超时可诊断 | ✅ | 客户端 0.35s 超时 → `TimeoutError` 0.39s 抛出，不悬挂 |
| 工具执行失败反馈 | ✅ | 场景 F（regression）：首次工具异常 → Agent 收到错误并重试恢复 |
| 越权修改非演示目录 | ✅ | 显式指示写 `Desktop\agent_escape_probe.txt` → **文件未创建**（路径守卫拒绝） |

## 2. 10 次独立代码修复任务 — `stability.json`

每轮：从 pristine 恢复同一 bug 基线 → 自然语言任务 → Agent 真实工具修复 → 独立 pytest 验证。

| 轮次 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 结果 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 耗时 s | 70.6 | 64.0 | 90.1 | 97.0 | 76.4 | 92.6 | 83.5 | 84.5 | 106.2 | 106.8 |

**成功率 10/10（目标 ≥9/10）；单轮均值 ~87s，min 64s / max 107s；失败分类：无。**
每轮证据：`artifacts/agent-execution/run-stability-01..10/`（summary.json / tool_calls.json /
pytest_before|after.json / workspace SHA256）。

## 3. 离线验证

### 软件级隔离（PASS ×2）

方法：以 `HTTP(S)_PROXY=http://127.0.0.1:9`（死端口）+ `NO_PROXY=localhost,127.0.0.1,::1`
重启 Ollama 服务并在同一环境下运行 **完整 Agent 任务**——服务端与 SDK 的一切外部 HTTP(S)
请求会即刻失败，本地回环不受影响（进程级、完全可逆、零系统网络改动）。

| 运行 | 结果 | 证据 |
|---|---|---|
| run-offline2（直跑） | ✅ 96.4s，5 工具调用，先红后绿 | `artifacts/agent-execution/run-offline2/` |
| run-offline（脚本化复验） | ✅ 106.8s，task_green=true | `artifacts/stability/offline_agent_test.json` + `artifacts/agent-execution/run-offline/` |

辅助证据：推理期连接审计仅 127.0.0.1 回环（`phase8_checks.json` loopback 审计）；
SDK 死端点错误中唯一 URL 为本地端点（Test E，无云端回退路径）。

首跑（run-offline 第一版脚本）900s 超时的根因是**测试脚本自身的管道死锁**
（`Popen(stdout=PIPE)` 未排水，服务器日志写满 OS 缓冲后阻塞）——与离线无关，已修复并复验通过。

### 物理断网（BLOCKED — 需人工）

禁用网卡/防火墙属系统级网络改动，超出本项目权限边界（工作原则 #6/#9）。人工复验步骤：

1. 断开 Wi-Fi / 拔网线（或手机热点关闭）
2. `.\scripts\preflight.ps1` → 仍应全 PASS（本地检查）
3. `.\.venv\Scripts\python.exe src\demo_agent.py --run-id manual-offline`
4. 期望：summary.json `bug_fixed_after=true`；若失败，保存 `artifacts/agent-execution/run-manual-offline/`

## 4. 结论

- 稳定性目标（≥9/10）**超额达成：10/10**
- 离线能力：**软件级隔离下完整任务通过**；物理断网留待现场 30 秒人工复验（步骤如上）
