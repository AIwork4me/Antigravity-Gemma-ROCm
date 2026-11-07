# Architecture

## 组件拓扑（全部 Windows 原生，无 WSL/无云）

```
┌────────────────────────────────────────────────────────────────────┐
│ Windows 11 26200 (ASUS ProArt PX13, Ryzen AI MAX+ 395, 128GB UMA)  │
│                                                                    │
│  demo_agent.py / tests / scripts                                   │
│        │  LocalOpenAIAgentConfig(model=gemma4:26b,                 │
│        │        base_url=http://127.0.0.1:11434/v1).lightweight()  │
│        ▼                                                           │
│  google-antigravity 0.1.23 (Python SDK)                            │
│   ├─ Agent (async ctx) ── harness runtime: bin/localharness.exe    │
│   ├─ tools: run_command / view_file / create_file / edit_file      │
│   ├─ policies: workspace_only(demo_workspace) +                    │
│   │            ask_user(run_command, strict whitelist handler)     │
│   └─ OpenAI-compatible HTTP ─────────────┐                         │
│                                          ▼                         │
│  Ollama 0.40.13 (server @127.0.0.1:11434)                           │
│   ├─ /v1/chat/completions /v1/models /api/tags /api/generate       │
│   ├─ runner: rocm_v7_1 (ggml-hip.dll, amdhip64_7.dll, rocblas)     │
│   │         或 vulkan (备用后端, OLLAMA_LLM_LIBRARY 可切换)          │
│   └─ model store: %USERPROFILE%\.ollama\models (digest 校验)        │
│                                      ▼                             │
│  AMD Radeon 8060S (gfx1151, PCI DEV_1586, ROCm 可见 78GB 统一内存)  │
└────────────────────────────────────────────────────────────────────┘
```

## 数据流（一次 bug 修复任务）

1. `demo_agent.py` 将 `demo_workspace` 重置为 bug 基线，记录 SHA256，独立 pytest 确认红。
2. SDK Agent 以自然语言接收任务，由 **本地 Gemma 4 26B** 产出工具调用。
3. harness 在策略约束下执行工具：
   - 文件读/写 → 仅限 demo_workspace（workspace_only）
   - shell → 白名单 handler 判定（仅 pytest/python + 工作区关键词）
4. 工具结果回传模型循环，直到模型给出总结。
5. 驱动程序再次独立 pytest 验证绿，输出前后 SHA256 与 diff。

## 验证独立性

- 模型输出**不**作为成功判据；判据是 agent 进程之外的 pytest 子进程退出码。
- 后端判定不依赖 `ollama ps` 的 "100% GPU"，而是 server.log 的 `library=ROCm compute=gfx1151`
  + runner 进程加载的 DLL + GPU 计数器三方交叉。

## 已知限制

- SDK 的 run_command 子进程在 Windows 无系统级沙箱：工作区策略只约束文件工具；
  以命令白名单缓解，并在演示中披露。
- Ollama ROCm 支持以官方 Windows 表格为准较窄（gfx1151 未列），实测可用但需
  长上下文回归（issue #17498）护航；Vulkan 路径保留为后备。
