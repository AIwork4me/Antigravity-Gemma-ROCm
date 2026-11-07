# 最终验收矩阵 P0–P14

状态只允许 PASS / FAIL / BLOCKED / NOT RUN。所有 PASS 附真实证据路径。
采集时间：2026-11-01（CST）。机器：ASUS ProArt PX13 / Ryzen AI MAX+ 395 / 8060S gfx1151 / 128GB。

| # | 验收项 | 状态 | 关键证据 |
|---|---|---|---|
| P0 | Windows 原生执行（无 WSL/VM/云） | **PASS** | 全部命令在本机 Win11 26200 执行；`artifacts/environment-baseline.json`；进程路径均在用户配置文件目录下（详见 environment-baseline.json）；推理期仅回环连接（`artifacts/stability/phase8_checks.json` loopback 审计） |
| P1 | Ollama 安装与模型加载 | **PASS** | winget 装 0.40.13（`artifacts/ollama/pull-*.log`）；`ollama list` = gemma4:26b 18GB；blob 全 sha256 校验 |
| P2 | Gemma 4 API 推理 | **PASS** | Python+PowerShell 双冒烟 PASS（`artifacts/ollama/smoke_*`）；精确指令遵循（LOCAL_OK / BACKEND_PROBE_OK / 1-20 计数） |
| P3 | AMD GPU 确认 | **PASS** | DEV_1586 REV_C1；hipInfo `gcnArchName: gfx1151`；`ollama ps` 100% GPU；GPU 驻留 30.98GB；推理期 CPU≈8.6% |
| P4 | ROCm/HIP 后端确认 | **PASS** | 默认路径四重证据：server.log `library=ROCm compute=gfx1151 libdirs=ollama,rocm_v7_1` + `sched_reserve: ROCm0` 运行时缓冲 + llama-server 加载 amdhip64_7/ggml-hip/libhipblas/rocblas DLL + 两次服务重启复现默认选择（`docs/GPU_BACKEND_VALIDATION.md`）。Vulkan 对照（强制切换）另行单独验证，**未**计入本项 |
| P5 | Antigravity SDK 初始化 | **PASS** | google-antigravity 0.1.23 win；Test A/B PASS（`artifacts/sdk/`） |
| P6 | SDK 调用本地 Gemma 4 | **PASS** | Test C/D PASS；Test E：死端口报 `AntigravityExecutionError`（唯一 URL 为本地端点 → 无云端回退） |
| P7 | 真实工具调用 | **PASS** | demo3：5 次真实 harness 工具调用（view_file×2 / run_command×2 / edit_file），策略审计链（`artifacts/agent-execution/run-demo3/`） |
| P8 | 文件修改 | **PASS** | 仅 `stats_utils.py` 变化（SHA256 前后对比 + git diff patch）；越界写入实测被拒（phase8_checks） |
| P9 | pytest 修复闭环 | **PASS** | 独立子进程先红（2 failed）后绿（5 passed）；agent 未接触验证进程 |
| P10 | 长上下文回归 | **PASS** | ROCm 18/18（3.1k→49.4k tokens × text/json/tool）+ 敏感频段 5/5；Vulkan 对照 6/6+2/2；agent 场景 D/E/F 3/3（`docs/GEMMA4_GFX1151_REGRESSION.md`） |
| P11 | 10 次稳定性验证 | **PASS（10/10）** | `artifacts/stability/stability.json`：10 轮全绿，64–107s/轮（目标 ≥9/10） |
| P12 | 离线验证 | **PASS（软件级）+ 物理断网 BLOCKED** | 进程级代理隔离（HTTP(S)_PROXY→死端口 + NO_PROXY=localhost）下完整 Agent 任务；推理期回环审计零外联。物理断网需人工：见 STABILITY_AND_OFFLINE_REPORT.md 复验步骤 |
| P13 | Windows 安装复现文档 | **PASS** | `docs/WINDOWS_INSTALL.md` + `docs/REPRODUCIBILITY.md`（含 IPv6 故障与 pull_parallel 替代路径） |
| P14 | Demo 一键启动 | **PASS** | `scripts/start-demo.ps1`（含 preflight 门禁）；preflight 实测全 PASS；README 一键命令 |

## 成功级别判定

- **L1（GPU 本地推理）**：达成（P1–P4）
- **L2（SDK Agent 真实工具任务）**：达成（P5–P9）
- **L3（ROCm 加速证明 + 端到端修复闭环）**：达成（P4 + P9 于默认 ROCm 后端完成；P10 护航）
