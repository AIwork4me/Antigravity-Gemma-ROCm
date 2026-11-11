# DevFest 2026 · Guangzhou —《最后一席》现场演示（中文文档）

一个完全**本地**的 AI Agent 舞台演示：虚构的 DevFest AI Workshop 有 40 个席位，已报名
39 人，报名系统里有一个真实的 off-by-one（差一）缺陷，错误地拒绝了第 40 位报名者。一个
**真实的本地 Agent** —— 由 `gemma4:26b` 驱动、运行在 Ollama + AMD ROCm（Radeon 8060S /
gfx1151）上的 Google Antigravity SDK 骨架 —— 自己读代码、跑失败的 pytest、**只**修改源
码文件、再跑测试。**独立验证子进程** + SHA256/diff/防篡改门全部通过后，舞台才会翻成
Google 绿色的 **YOU'RE IN!** 庆祝画面和明确标注为虚构的 **Builder Pass**。

舞台上没有任何假货：没有预录动画、没有预先注入的正确代码、没有合成工具调用、没有编造
的 GPU 数据、没有伪造的测试结果。回放与设计预览模式确实存在 —— 但处处显著标注、绝不
混淆。

- Issue：[#2 — DevFest 2026 live demo: The Last Seat](https://github.com/AIwork4me/Antigravity-Gemma-ROCm/issues/2)
- 舞台地址：`http://127.0.0.1:8421`（仅回环绑定，1920×1080 投影布局）
- 英文文档：[docs/DEVFEST_THE_LAST_SEAT.md](DEVFEST_THE_LAST_SEAT.md)
- GO/NO-GO 台账：[docs/DEVFEST_GO_NO_GO_LEDGER.md](DEVFEST_GO_NO_GO_LEDGER.md)

## 5 秒看懂的故事

```
左（报名应用）                  右（真实执行轨迹）
┌────────────────────────┐     ┌──────────────────────────────┐
│ 39 / 40 seats          │     │ 👁 view_file checkin.py       │
│ 席位 40：✗ 被拒         │ ←── │ ⚙️ pytest → 1 failed（红）    │
│ "workshop at full      │     │ ✏️ edit_file checkin.py       │
│  capacity (39/40)"     │     │ ⚙️ pytest → 9 passed（绿）    │
│                        │     │ 🛡 ALLOW python -m pytest …    │
│ ［Agent 修复中…］        │     │ ✓ 独立验证：PASS              │
│ 40 / 40 — YOU'RE IN! 🎉│     │ diff: `>= capacity-1` → `>= capacity` │
└────────────────────────┘     └──────────────────────────────┘
```

## 架构（全部在同一台 Windows 机器上）

```
浏览器（1920×1080 投影）
   │  http://127.0.0.1:8421（仅回环绑定；每个会改状态的端点都有
   │  Host/Origin/Sec-Fetch-Site 校验）
   ▼
src/devfest_webui.py —— 舞台仪表盘：SSE 事件流、录制/回放、设计预览、
   │                   报名探针、证据查看器
   ├──► src/devfest_probe.py  —— 报名探针：在隔离的 `python -I` 子进程里
   │         执行 devfest_workspace 中真实的 checkin.py；UI 的前后业务状态
   │         只来源于这个探针，绝不硬编码
   ├──► src/devfest_agent.py  —— 真实 Agent 任务（与 src/demo_agent.py 同一条
   │         已审计流水线）：任务 → SDK Agent → 工具调用流 → 策略审计 →
   │         前后 SHA256 → 独立 pytest → diff → 防篡改验收（冻结测试字节
   │         不变、源码白名单=checkin.py、磁盘复验、整型退出码）
   ▼
Ollama（http://127.0.0.1:11434）→ gemma4:26b → AMD ROCm（gfx1151）上的
llama-server 运行器。舞台上显示 "ROCm VERIFIED" 必须同时满足：推理后
GET /api/ps 的驻留证据（含完整 digest）+ llama-server.exe 进程持有 AMD HIP
DLL（tasklist /m）——绝不引用可能过期的 server.log。
```

**安全边界（与已审计基线一致，未放宽）：** Agent 的文件工具只允许
`devfest_workspace/`（junction 安全、reparse 感知的路径归一化）；`run_command`
是结构化 fail-closed 白名单 —— 只放行 `python -m pytest …` / `pytest …` 且
`Cwd` 必须在工作区内。Windows 策略处理器是**策略边界，不是 OS 沙箱**。工作区重置
复用 `demo_agent.py` 的 junction 安全模式。没有 `allow_all()`、没有任意 shell、
没有外网绑定、没有云端回退。

**写进代码里的诚实规则：**

| 规则 | 强制手段 |
| --- | --- |
| 不放假 Agent 运行 | 运行按钮只启动真实流水线；UI 每次状态变化都来自真实事件或真实探针 |
| 不预注入正确代码 | pristine 场景自带缺陷；测试冻结它（pristine 变绿 = CI 失败） |
| 改测试不算通过 | `files_changed` 涉及 `tests/` → 验收 FAIL；冻结测试必须字节一致 |
| 只允许白名单源码变更 | `MUTABLE_ALLOWLIST = ("checkin.py",)`；其他任何文件 → FAIL |
| 不接受伪造退出码 | 字符串 `"0"` 退出码直接抛错；map diff 后还要对磁盘文件复验 SHA256 |
| 不虚报 GPU | `ROCm VERIFIED` 必须有实时 `/api/ps` 驻留 + 运行器 DLL 证据 |
| 回放 ≠ 实况 | 回放全程琥珀色 REPLAY 横幅+徽章；回放不代表当前硬件或实时验证 |
| 预览 ≠ 验收 | DESIGN PREVIEW 只发 `ok: null`，绝不写证据/工作区 |

## 安装（Windows PowerShell，从全新克隆开始）

```powershell
git clone https://github.com/AIwork4me/Antigravity-Gemma-ROCm.git
cd Antigravity-Gemma-ROCm
git fetch origin feat/devfest-2026-last-seat-demo   # PR 合并前先取该分支
git checkout feat/devfest-2026-last-seat-demo

# 1) Python 虚拟环境（3.12 或 3.13）。已有 .venv 可跳过。
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt

# 2) Ollama 与模型（约 18 GB，一次性；已装可跳过）
ollama --version            # 能打印版本即可；没有就到 ollama.com 安装
ollama pull gemma4:26b      # Ollama 已在运行时切勿重复安装

# 3) 自动化舞台预检（逐项 PASS/WARN/FAIL/BLOCKED；退出码 0 = GO）
powershell -ExecutionPolicy Bypass -File scripts\preflight-devfest.ps1
```

硬件预期：参考机为 Ryzen AI MAX+ 395 + Radeon 8060S 核显（ROCm gfx1151）。任何
可被 Ollama ROCm 运行器接管的 AMD Radeon 均可；NVIDIA GPU 走 CUDA 档；纯 CPU 可
运行但很慢（见 [COMPATIBILITY_MATRIX.md](COMPATIBILITY_MATRIX.md)）。

## 运行

```powershell
# LIVE —— 真实现场运行（先预热模型，再打开浏览器）
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1

# 变体
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -Record recording.json   # LIVE + 录制（供回放/GIF）
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -Replay recording.json   # REPLAY（显著标注）
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -DryRun                  # DESIGN PREVIEW（无需 Ollama）
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -SkipWarmup -Port 8422   # 自定义端口、跳过预热

# 不用脚本直接启动
.venv\Scripts\python -I src\devfest_webui.py --port 8421
```

舞台流程：预检 → 模型预热（keep-alive 5 分钟）→ 浏览器打开
`http://127.0.0.1:8421`。左栏加载即对真实报名代码做探针（第 40 席红色被拒），
右栏待命。点 **▶ 运行**，看真实轨迹：读码 → pytest 红 → 编辑 → pytest 绿 →
独立验证 → diff → GPU 证据 → **YOU'RE IN!**。

## 2–3 分钟舞台脚本

1. **(0:00)** "这是一个真实的 40 席 Workshop 报名系统，已报 39 人。看最后一次报名。"
   点 **报名第 40 席** —— 红色拒绝。缺陷就在这台机器上、此刻发生。
2. **(0:20)** "测试写明了预期行为，现在挂着红。让完全本地的 Gemma 4 Agent 来修 ——
   无云端、无提示。" 点 **▶ 运行**。
3. **(0:30–2:00)** 边流边讲：Agent 列目录、读 `checkin.py` 与冻结测试、跑 pytest
   （红）、定位边界、只改一行、复跑（绿）。指着策略行的 ALLOW/DENY："它只能跑
   pytest、只能在这个文件夹里"。
4. **(2:00)** 独立验证变绿，diff 显示 `>= capacity - 1` → `>= capacity`，GPU 徽章
   显示 `ROCm VERIFIED`。左栏翻转：**40 / 40 — YOU'RE IN!**、四色纸屑、Builder
   Pass（明确虚构）。
5. **(2:20)** 再点一次 **报名第 40 席**：同一个探针这次放行第 40 位开发者 —— 来自
   真实代码，不是硬编码翻转。
6. **(2:30)** "你看到的一切都可以复现" —— 打开 **🗂 验证证据**：工具轨迹、pytest
   前后、SHA256、GPU 归因、耗时。

**90 秒应急兜底：** 演讲前用 `-Record recording.json` 录一次真实运行，现场用
`-Replay recording.json` 启动。UI 会大声标注 REPLAY；讲解词："这是今天早上在同一台
机器上的真实运行"。

## 验证与证据

- 物理运行落盘于 `artifacts/devfest-agent-execution/run-*/`（本地、gitignore），
  **精选脱敏副本**收录在 `evidence/devfest-last-seat/run-*/`（入库）：任务书、
  配置、完整工具调用流、策略审计、pytest 前后、diff.patch、SHA256 映射、Agent
  回复、`gpu_evidence.json`（digest+驻留+运行器 DLL PID+后端）、`env.json`、以及
  增强版 `summary.json`。
- `evidence/devfest-last-seat/validation_ledger.json` 汇总所有运行与逐项验收门 ——
  机器可读的 GO/NO-GO 数据源。
- 任何人可自行复验：
  `python -I src\devfest_probe.py --workspace devfest_workspace --scenario last`
  （真实代码此刻的判定）；以及
  `cd devfest_workspace && ..\.venv\Scripts\python -m pytest tests\ -q`。

## 故障排查

| 症状 | 处理 |
| --- | --- |
| `port 8421 already in use` | `Get-NetTCPConnection -LocalPort 8421` 找到占用进程停掉，或改用 `-Port 8422` |
| 预检 `Ollama unreachable` | 无头启动 `ollama serve` 或启动 Ollama 应用；**不要**重复安装 |
| `model missing` | `ollama pull gemma4:26b`（约 18 GB；IPv6 拉取失败就显式走 IPv4） |
| 首跑很慢（出 token 前约 60–75 秒） | 模型冷加载；启动脚本会预热 —— 别加 `-SkipWarmup` |
| GPU 徽章 `not resident (cold)` | 首次推理前的正常状态；跑一次后再看 |
| GPU 徽章 `resident (backend unverified)` | 模型已驻留但拿不到运行器的 ROCm DLL 证据 —— 查 `tasklist /m amdhip64*`；可能走了非 ROCm 后端档 |
| `缺陷基线 已修复/漂移` 徽章 | 上一次运行把工作区修好了 —— 点 **演示重置** |
| 重置被拒：工作区是 junction | 故意设防；手动移除 junction 后再重置 |
| 某次运行显示 `tests_tampered` | Agent 改了冻结测试 —— 按设计该次验收 FAIL；重置重跑 |
| 浏览器事件不更新 | SSE 流断开；刷新页面（状态会从真实代码重新推导） |
| Agent 内 pytest 找不到 | 运行 `devfest_webui.py` 的解释器必须属于 venv；用 `scripts\start-devfest-demo.ps1` 启动 |

## 范围与限制

- Builder Pass 是**虚构纪念徽章**，不是入场凭证；参会者名字全部虚构；可选的报名
  姓名有界（24 字符）、去控制字符、经 textContent 转义渲染、绝不持久化、绝不送入
  模型。
- 舞台 UI 使用 DevFest 2026 配色的原创 CSS/SVG 几何装饰；不再分发任何主办方模板
  文件或 Logo。主办方模板中的 GDG Shanghai 标识**有意不使用** —— 页头是
  `DevFest 2026 · Guangzhou`。
- 设计预览绝不算验收；回放在所有界面上都有标注；物理硬件不可用时，验证台账如实
  记录 BLOCKED，绝不编造结果。
