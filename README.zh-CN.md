# Antigravity-Gemma-ROCm

**Windows 上完全本地运行的 AI 编程 Agent —— AMD ROCm 优先（Ryzen AI MAX 395/495），兼容任意 Ollama 模型（ROCm/Vulkan/CUDA/CPU）。**

[![CI](https://github.com/AIwork4me/Antigravity-Gemma-ROCm/actions/workflows/ci.yml/badge.svg)](https://github.com/AIwork4me/Antigravity-Gemma-ROCm/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12–3.13](https://img.shields.io/badge/python-3.12%20%E2%80%93%203.13-blue.svg)](requirements.txt)
[![code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](ruff.toml)

[English README](README.md)

![demo](docs/assets/demo.gif)

给定一段自然语言任务，**由 Ollama 本地服务的 Gemma 模型**在 Google Antigravity SDK
驱动下**真正执行工具**——列出/读取/修改文件、运行 pytest——在锁定的沙箱工作区内，
修复一个真实 Python 项目里预埋的 bug；随后由**独立验证流程**证明修复成立：运行前
测试为红、运行后为绿，全程留有字节级 diff 与 SHA256 证据链。零云端、零 API 花费、
可离线复现。

> **本仓库不是什么：** *Antigravity IDE/桌面应用* 是 Google 的 agentic 开发环境。
> 本项目只使用 **Antigravity Python SDK**（`pip install google-antigravity`）对接本地
> Ollama 服务——不需要、也不会启动那个 IDE。

## 为什么值得关注

- **成本** —— 每个 token 都在本地生成。没有 API key，没有计量计费。
- **隐私** —— 代码永不离机（推理期间仅回环 socket 已实测验证；本地配置下 SDK 无
  云端回退）。
- **可靠性** —— 断网可用。软件级隔离已证明；物理断网操作流程见
  [docs/OFFLINE_VALIDATION.md](docs/OFFLINE_VALIDATION.md)。
- **真实 agentic loop** —— 通过 SDK harness 的真实工具调用，全程受策略沙箱审计。
  不是聊天演示，没有任何 mock。

## Windows 上的 ROCm：官方文档 vs 实测

- AMD 与 Ollama 官方文档只列出 **Windows ROCm 对 RX 7000+ 独显**的支持；
  Ryzen AI MAX 核显（gfx1151）仅在 **Linux** 列表中。
- **实测（Ollama 0.40.x，可复现）：** 自带的 `rocm_v7_1` runner 在 Windows 上
  **默认即检测并以原生方式运行 gfx1151** —— 依据：服务端日志
  （`library=ROCm compute=gfx1151`）、运行时 ROCm 调度缓冲、runner 进程加载的 HIP
  DLL，以及 49K token 回归的正确输出。Vulkan 后端另做了强制切换的 A/B 验证，作为
  有文档记录的回退路径（[docs/GPU_BACKEND_VALIDATION.md](docs/GPU_BACKEND_VALIDATION.md)）。
- 我们只陈述**实测事实，而非厂商支持承诺**。如果你有一台 Strix Halo 机器
  （Ryzen AI MAX+ 395/495，64–128 GB 统一内存），本仓库给你一条经过验证的
  本地 agent 落地路径。

## 快速开始

```powershell
# 1) 安装 Ollama（官方 winget 包）
winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements

# 2) 拉取模型（约 18 GB；参考配置 = gemma4:26b）
ollama pull gemma4:26b

# 3) 项目依赖（独立 venv —— 不触碰你的基础 Python）
#    Python 3.12-3.13 为 CI 实测版本（SDK 最低要求 3.10）
git clone https://github.com/AIwork4me/Antigravity-Gemma-ROCm.git
cd Antigravity-Gemma-ROCm
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 4) 检查并运行
.\scripts\preflight.ps1      # PASS/FAIL/BLOCKED 就绪门禁（自动恢复工作区）
.\scripts\start-demo.ps1     # 完整现场演示：agent 修复预埋 bug，测试由红变绿
```

**显存更小 / 想换模型？** 任何支持工具调用的 Ollama 模型都能跑：

```powershell
$env:AGENT_MODEL = "你的模型:标签"   # 所有脚本都会读取
```

参考体验实测于 `gemma4:26b`（25.2B MoE，激活 3.8B，约 31 GB 显存）。
[Ollama 模型库](https://ollama.com/library)里更轻量的工具型模型理论上可在更小的卡上
运行——这些路径属于**社区验证档**：欢迎试跑并
[提交硬件报告](.github/ISSUE_TEMPLATE/hardware_report.md)，让下面的表格丰富起来。

> `scripts/pull_parallel.py`（IPv4、分块、sha256 校验的注册表拉取）是
> **`ollama pull` 因 IPv6 路由损坏而卡住时的回退方案**——不属于默认安装路径。

## Web 仪表盘（直播 + REPLAY 回放）

```powershell
.\scripts\start-web-demo.ps1                      # http://127.0.0.1:8420
# 录制一次运行，用于视频 / GIF / 演讲现场备份：
.\.venv\Scripts\python.exe -I src\webui.py --record run.json
# 之后回放 —— 显眼标注 REPLAY，回放期间禁止真实运行：
.\.venv\Scripts\python.exe -I src\webui.py --replay run.json --replay-speed 1.5
```

![dashboard](docs/assets/dashboard.png)

实时状态芯片（服务/模型驻留/**后端（含 ROCm 检测）**/工作区基线）、在进程内执行
真实任务的 RUN 按钮，以及 SSE 实时流：阶段、工具调用、**策略决策**、模型 token、
pytest 红转绿、最终 diff。REPLAY 模式带回放横幅重演录制的事件流——
[`evidence/flagship-run/webui-recording.json`](evidence/flagship-run/) 就是一份这样的录制。

## 架构

```
┌───────────────────────────────────────────────────────────────────┐
│ Windows 11（Ryzen AI MAX+ 395，128 GB 统一内存）                   │
│                                                                   │
│  演示驱动 (src/demo_agent.py) / Web 仪表盘 (src/webui.py)          │
│      │ LocalOpenAIAgentConfig(model=AGENT_MODEL).lightweight()    │
│      ▼                                                            │
│  google-antigravity SDK 0.1.23 ── harness: bin/localharness.exe   │
│      ├─ 工具: view_file / create_file / edit_file / run_command   │
│      ├─ B1 文件边界 : 路径必须解析进工作区                          │
│      ├─ B2 shell 边界: 结构化仅限 pytest + 工作区 Cwd               │
│      └─ OpenAI 兼容 HTTP ──► Ollama @ 127.0.0.1:11434             │
│                                       └─ rocm_v7_1 runner         │
│                                            └─► Radeon 8060S       │
│                                                (gfx1151, HIP)     │
└───────────────────────────────────────────────────────────────────┘
```

| 组件 | 职责 |
|---|---|
| **Gemma（或你的模型）** | 推理、代码理解、工具调用决策 |
| **Ollama** | 推理服务 + 模型仓库；OpenAI 兼容 API；GPU runner |
| **AMD ROCm/HIP** | GPU 算力：Ollama 自带 rocm_v7_1 runner 中的 llama.cpp HIP 内核 |
| **Antigravity SDK** | agent harness：工具执行、策略引擎、流式输出 —— 「双手」 |
| 本仓库 | 锁定配置、演示工作区、场景任务、验证器、证据链 |

## 参考机型实测数据

ASUS ProArt PX13 —— Ryzen AI MAX+ 395（16C/32T）、Radeon 8060S（gfx1151）、
128 GB 统一内存、驱动 32.0.22032.6002、Ollama 0.40.x，**默认 ROCm 后端**。

| 项目 | 数值 |
|---|---|
| 模型 | `gemma4:26b` —— 25.2B MoE（激活 3.8B），Q4_K_M，256K 上下文，支持工具+思考 |
| 冷加载 | ~52–73 s（之后常驻；keep-alive 默认 5 分钟） |
| 热请求 | ~1 s（prompt 缓存命中） |
| 生成速度 | 短上下文 ~48–50 tok/s；49K token 提示 ~27 tok/s |
| 显存占用 | ~31 GB（实测 30.98 GB，权重 + KV + 计算缓冲） |
| Bug 修复任务 | **10/10 成功**，单次 64–107 s（均值 87 s） |
| 长上下文 | 49.4K 提示 token 内全部正确（文本 / JSON / 工具模式） |

完整方法论：[docs/GEMMA4_GFX1151_REGRESSION.md](docs/GEMMA4_GFX1151_REGRESSION.md)、
[docs/COMPATIBILITY_MATRIX.md](docs/COMPATIBILITY_MATRIX.md)。

## 硬件兼容性

| 机器 / GPU | 后端 | 状态 |
|---|---|---|
| **AMD Ryzen AI MAX+ 395**（Radeon 8060S, gfx1151） | ROCm | ✅ **已实测** —— Windows 默认 runner（[A/B 证据](docs/GPU_BACKEND_VALIDATION.md)） |
| AMD Ryzen AI MAX+ 395 | Vulkan | ✅ **已实测** —— 有文档的回退路径，已强制切换验证 |
| AMD Ryzen AI MAX+ 495 | ROCm | 🟡 预期兼容 —— 同为 gfx1151 级核显；**欢迎社区确认** |
| 其他 AMD 显卡（RX 7000+） | ROCm | 🟡 预期兼容 —— 官方支持的 Windows 档位 |
| NVIDIA | CUDA | 🟡 社区验证档 |
| 任意 CPU | CPU | 🟡 社区验证档（慢；每步工具调用以分钟计） |

在你的机器上跑过？欢迎提交[硬件报告](.github/ISSUE_TEMPLATE/hardware_report.md)
或直接 PR 一行进这张表 —— 🟡 就是这样变成 ✅ 的。

## 安全边界（诚实版）

- **三道策略边界**守护 agent：文件路径授权（感知 reparse point 的工作区路径解析）；
  shell 进程许可（对可执行文件 + 参数 + 工作区内 `Cwd` 做结构化 fail-closed 校验——
  仅放行 `python -m pytest` / `pytest` 形态；未知参数结构、任意脚本、`-c` 代码执行、
  任何 shell 元字符一律拒绝）；以及仅回环绑定的 Web 控制台（Host/Origin/CSRF 校验、
  非阻塞 SSE、回放文件大小与结构校验）。红队套件：`tests/test_security_boundaries.py`
  （junction 逃逸、`..` 穿越、`\?\` 路径、元字符注入、Cwd 重定向、前缀撞名……）
  与 `tests/test_webui_http.py`（跨源触发运行、DNS rebinding、回放边界）。
- SDK 的 shell 子进程在 Windows 上**没有 OS 级沙箱**，pytest 白名单也不等于沙箱——
  pytest 会执行工作区里的任何测试代码。请把演示限制在一次性 demo 工作区内；对任意
  导入的仓库请用 `build_config(enable_shell=False)` 或人工审批 + 低权限账户。详见
  [SECURITY.md](SECURITY.md)。
- 物理断网需要手动操作；软件级隔离证明与手动流程见
  [docs/OFFLINE_VALIDATION.md](docs/OFFLINE_VALIDATION.md)。

## 场景任务

[`scenarios/`](scenarios/) 内置三个分级 bug 修复任务，各带原始工作区，必要时附带
用于测试编写任务的突变体：

- **A_boundary** —— 修复 `textutils.py` 中的逻辑 bug（入门）
- **B_writetests** —— 编写能杀死三个预埋突变体的 pytest 套件（测试质量）
- **C_excfix** —— 依据真实 `crash.log` 定位并修复崩溃（读栈回溯）

多任务验证结果：[docs/MULTI_TASK_AGENT_VALIDATION.md](docs/MULTI_TASK_AGENT_VALIDATION.md)。

## 可复现性与证据

```powershell
.\scripts\run-tests.ps1                                # 单元 + API 冒烟 + live agent 测试
.\.venv\Scripts\python.exe -m pytest -q                # 单元 + 安全红队（无需模型）
.\.venv\Scripts\python.exe -m pytest -m live -q        # live agent 测试（需已加载模型）
.\.venv\Scripts\python.exe src\regression_ctx.py --full  # 长上下文回归
```

从 [`evidence/flagship-run/`](evidence/flagship-run/) 开始 —— 一份完整证明：
任务 → 工具调用 → 策略审计 → pytest 红转绿 → 前后 SHA256 → diff。
**完整证据树**（345+ 文件：后端 A/B 探测、10 次稳定性矩阵、离线隔离证明、独立 RC1
审计）以 `evidence-bundle-v1.0.0.zip` 附在 [v1.0.0 release](../../releases)，内含
`SHA256SUMS.txt`。深度报告在 [docs/](docs/)：
[RC1 独立审计](docs/RC1_INDEPENDENT_AUDIT.md) ·
[安全边界评审](docs/SECURITY_BOUNDARY_REVIEW.md) ·
[离线验证](docs/OFFLINE_VALIDATION.md) ·
[稳定性与离线报告](docs/STABILITY_AND_OFFLINE_REPORT.md) ·
[故障排查](docs/TROUBLESHOOTING.md)。

## 路线图

- **v1.1** —— 更多场景（重构任务、多文件修复）；ask-a-human 策略演示；基于社区
  报告的轻量模型指引表
- **v1.2** —— Linux CI；容器化证据校验；同一任务集上的本地模型评测基准

欢迎贡献 —— 从 [CONTRIBUTING.md](CONTRIBUTING.md) 开始。最快的第一个 PR 就是
硬件表加一行。

## 演讲与致谢

本项目在一场 GDG DevFest 演讲（Ryzen AI MAX 上的 ROCm + Gemma + Antigravity SDK
全本地 AI 编程 agent）中首次公开。站在巨人肩上：
[Gemma](https://ai.google.dev/gemma) · [Ollama](https://ollama.com) ·
[AMD ROCm](https://www.amd.com/en/products/software/adrenalin/rocm.html) ·
google-antigravity SDK。

## 许可与归属

- 本仓库：项目代码 MIT；文档 CC-BY-4.0（见 [LICENSE](LICENSE)）。
- `google-antigravity` SDK：Apache-2.0（Google LLC）—— 从 PyPI 安装。
- 模型权重（如 `gemma4:26b`）由 Ollama 按各自条款服务，本仓库**不分发**权重。
