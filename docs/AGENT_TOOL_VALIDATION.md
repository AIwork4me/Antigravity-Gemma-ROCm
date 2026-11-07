# Agent Tool Execution Validation (Phase 4 + 5)

## Phase 4 — SDK 接入测试（全部 PASS）

| Test | 内容 | 结果 | 证据 |
|---|---|---|---|
| A | `from google.antigravity import Agent, LocalOpenAIAgentConfig, LiteRTAgentConfig` | ✅ PASS | `artifacts/sdk/sdk_introspection.txt` |
| B | `async with Agent(config)` 初始化（对本地服务） | ✅ PASS | `artifacts/sdk/test_B_init.txt` |
| C | SDK 请求本地模型（OpenAI 兼容端点） | ✅ PASS | `artifacts/sdk/test_CD_agent_chat.txt` |
| D | 完整读取并流式输出模型回复（99s 一轮） | ✅ PASS | 同上 |
| E | 关闭/指向死端口时产生**可诊断错误而非云端回退** | ✅ PASS | `artifacts/sdk/test_E_dead_endpoint.txt`：`AntigravityExecutionError: ... Post "http://127.0.0.1:9/v1/chat/completions": dial tcp 127.0.0.1:9: connectex: ... actively refused`（错误中唯一 URL 为本地端点 → 无云端回退路径） |

安装事实：`google-antigravity==0.1.23`（PyPI 官方，win_amd64 路径可用，运行时二进制
`.venv/Lib/site-packages/google/antigravity/bin/localharness.exe`）。依赖锁定：`requirements.lock.txt`。

## Phase 5 — 真实工具执行与 Bug 修复闭环

任务：`artifacts/agent-execution/run-demo3/mission.txt`（纯自然语言：读码→跑测→定位→修复→复测→总结）

### 结果（run-demo3，2026-11-01，gemma4:26b @ ROCm 默认后端）

| 验收点 | 结果 | 证据 |
|---|---|---|
| Bug 先存在 | ✅ 独立 pytest 退出码 1（2 failed / 3 passed） | `pytest_before.json` |
| 真实工具调用 | ✅ 5 次：`view_file`, `view_file`, `run_command`(pytest), **`edit_file`**, `run_command`(pytest) | `tool_calls.json` |
| 策略真实执行 | ✅ `Get-ChildItem` DENY ×N；`python -m pytest tests/ -q` ALLOW ×2；文件路径守卫 ALLOW 工作区内 | `policy_audit.txt` |
| 源文件被 Agent 修改 | ✅ 仅 `stats_utils.py` 变化（SHA256 前后对比） | `workspace_before/after.json` |
| 修复正确 | ✅ `-return vals[mid-1]` → `+return (vals[mid-1]+vals[mid])/2`（最小 diff） | `git-diff.patch` |
| 失败测试复跑通过 | ✅ 独立子进程 pytest 退出码 0（5 passed） | `pytest_after.json` |
| 总结生成 | ✅ 正确描述根因与修复 | `agent_reply.txt` |
| 端到端耗时 | 74.0s | `summary.json` |

### 迭代记录（真实排障过程，非一次性成功）

1. **demo1**（157s, 0 工具）：发现两点 —— ① run_command 参数键为 `CommandLine`（非 `command`），
   白名单全 DENY；② `policy.workspace_only()` 在 0.1.23 中**无条件 deny 所有文件工具**（忽略
   workspaces 参数，`del workspaces`，docstring 与实现不符）。
2. **demo2**（185s, 4 工具）：view/pytest 通，但模型调用 Gemini-CLI 风格工具名
   `replace_file_content`（参数键不含 AbsolutePath）被守卫拒绝 → 诊断输出正确但无法落盘。
3. **修复**：策略改为 `allow(file_tools+发现类工具, when=路径谓词)`，谓词对**一切路径型参数**做
   词法归一（折叠 `..`）后校验工作区前缀；白名单读取 `CommandLine`。15/15 单测覆盖
   （`tests/test_config.py`，含越界/逃逸用例）。
4. **demo3**：全链路 GREEN。

### 安全披露

- 未使用 `policy.allow_all()`。文件工具 = 工作区路径谓词；shell = pytest/python 白名单 +
  `..`/删除/注册表/网络命令黑名单。
- **已知限制**：SDK 的 run_command 子进程在 Windows 无系统级沙箱（工作区策略只约束文件工具，
   不约束进程范围）。缓解 = 命令白名单 + 审计日志；演示中如实披露。
- 越界写入验证：`tests/test_config.py::test_path_guard_accepts_workspace_only` 断言
   `demo_workspace/../secrets.txt` 被拒；Phase 8 另做进程外验证。
