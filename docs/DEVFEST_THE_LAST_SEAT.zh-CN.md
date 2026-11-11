# Google DevFest 2026 —《最后一席》现场演示 V2（中文文档）

一个完全**本地**的 AI Agent 舞台演示，为演讲
《从 Gemma 4 到本地个人智能体：Google Antigravity × AMD ROCm 实战》打造。

观众在 3 分钟内看到的故事：

1. 虚构 DevFest Workshop 有 **40 个席位，已报名 39 人**。
2. 最后一位开发者尝试报名 —— 系统**错误拒绝**。
3. 我们把问题交给 **Google Antigravity SDK** 驱动的本地 Agent。
4. **Gemma 4 26B** 在本地 AMD GPU 上推理，决定调用哪些工具。
5. Antigravity SDK **真正执行**读文件、跑测试、改代码。
6. **独立验证器**重新跑冻结测试并审计每一处变更。
7. 最后一席开放 —— 画面翻成 **Google 绿色 40/40**。
8. 出现 **YOU'RE IN! / The Last Seat Is Saved!** 与 Google 四色庆祝。

三层信息：

- **Gemma 4 = 大脑**（本地模型、本地决策）
- **Antigravity SDK = Agent 骨架**（真实工具执行 + 策略控制）
- **AMD ROCm = 本地算力**（Radeon 8060S / gfx1151，无云端）

舞台上没有任何假货：没有预录动画、没有预先注入的正确代码、没有合成工具调用、
没有编造的 GPU 数据、没有伪造的测试结果。回放与设计预览模式确实存在 ——
但处处显著标注、绝不混淆。

- Issue：[#2 — DevFest 2026 live demo: The Last Seat](https://github.com/AIwork4me/Antigravity-Gemma-ROCm/issues/2)
- 舞台地址：`http://127.0.0.1:8421`（仅回环绑定，1920×1080 投影布局）
- 英文文档：[docs/DEVFEST_THE_LAST_SEAT.md](DEVFEST_THE_LAST_SEAT.md)
- GO/NO-GO 台账：[docs/DEVFEST_GO_NO_GO_LEDGER.md](DEVFEST_GO_NO_GO_LEDGER.md)

## 5 秒看懂的故事（V2 布局）

```
左 —— 剧情（STAGE VIEW）           右 —— Google Antigravity SDK
┌──────────────────────────────┐   ┌────────────────────────────────────┐
│ Google DevFest 2026          │   │ Gemma4→SDK→工具→应用 管线           │
│ THE LAST SEAT                │   │ AMD ROCm · Local GPU Inference     │
│ [问题→…→庆祝 六幕条]          │   │ ┌────────────────────────────────┐ │
│ 39 / 40 · 席位 40 ✗ 被拒      │   │ │ Antigravity SDK → view_file    │ │
│ "workshop at full capacity"  │   │ │ Antigravity SDK → run_command  │ │
│ ［Agent 修复中…］              │   │ │ Antigravity SDK → edit_file    │ │
│ 40/40 — YOU'RE IN! 🎉        │   │ │ Antigravity SDK → run_command  │ │
│ Builder Pass（明确虚构）       │   │ └────────────────────────────────┘ │
└──────────────────────────────┘   │ ✨ Behind the Magic（真实代码）     │
                                   └────────────────────────────────────┘
```

随时切换 **DEVELOPER VIEW** 查看技术证据：SDK 生命周期事件、真实 `pytest`
结果、白名单 diff、SHA256、策略审计、Ollama 模型与 GPU 证据（分来源诚实
标注）、运行耗时。

## 品牌与视觉设计

- 顶部主品牌为 **Google DevFest 2026**：Google 六个字母使用经典品牌色
  （G 蓝 `#4285F4`、o 红 `#EA4335`、o 黄 `#FBBC04`、g 蓝、l 绿 `#34A853`、
  e 红），`DevFest 2026` 用高对比度米白。
- 演示标题：**THE LAST SEAT**；副标题：*Can a Local AI Agent Save the
  Final Seat?*；场景行：*39 Registered · 1 Seat Left · Registration
  Denied.*
- 配色与几何装饰沿用 DevFest 深色模板体系（`#1E1E1E` 底、`#F0F0F0` 米白、
  Google 四色、简洁几何），全部为原创 CSS/SVG —— **不分发主办方模板文件、
  字体或 Logo**，也不宣称任何城市品牌（V2 已移除旧的 `· Guangzhou` 后缀）。
- 字体使用系统字体栈（存在 Google Sans 时优先）；仓库不携带字体文件。

## 架构（全部在同一台 Windows 机器上）

```
浏览器（1920×1080 投影）
   │  http://127.0.0.1:8421（仅回环绑定；每个会改状态的端点都有
   │  Host/Origin/Sec-Fetch-Site 校验）
   ▼
src/devfest_webui.py —— V2 舞台面板：STAGE/DEVELOPER 双视图、六幕剧情
   │                   状态机、SSE 事件流、录制/回放、设计预览、探针端点、
   │                   证据查看器
   ├──► src/devfest_probe.py  —— 报名探针：在隔离的 `python -I` 子进程里
   │         执行 devfest_workspace 中真实的 checkin.py；UI 的前后业务状态
   │         只来源于这个探针，绝不硬编码
   ├──► src/devfest_agent.py  —— 真实 Agent 任务：任务 → SDK Agent →
   │         工具调用流 → 策略审计 → 前后 SHA256 → 独立 pytest → diff →
   │         防篡改验收（冻结测试字节不变、源码白名单=checkin.py、磁盘
   │         复验、整型退出码）；发出 SDK 生命周期事件
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

**写进代码里的诚实规则（V2 增强）：**

| 规则 | 强制手段 |
| --- | --- |
| 不放假 Agent 运行 | 运行按钮只启动真实流水线；UI 每次状态变化都来自真实事件或真实探针 |
| 不预注入正确代码 | pristine 场景自带缺陷；测试冻结它（pristine 变绿 = CI 失败） |
| 改测试不算通过 | `files_changed` 涉及 `tests/` → 验收 FAIL；冻结测试必须字节一致 |
| 只允许白名单源码变更 | `MUTABLE_ALLOWLIST = ("checkin.py",)`；其他任何文件 → FAIL |
| 不接受伪造退出码 | 字符串 `"0"` 退出码直接抛错；map diff 后还要对磁盘文件复验 SHA256 |
| 不虚报 GPU | `ROCm VERIFIED` 必须有实时 `/api/ps` 驻留 + 运行器 DLL 证据 |
| 观察到 ≠ 成功 | 工具事件只标 `observed`；六幕状态机无法被计时器或乱序事件推进 |
| 庆祝需双 PASS | `acceptance.ok` **且** 最终业务探针 PASS **且** 同一 run 的 verified 席位事件 |
| 回放 ≠ 实况 | 回放全程琥珀色 REPLAY 横幅+徽章；回放结束后**保持** RECORDED RUN 身份；历史成功带 REPLAY 水印；回放期间禁真实运行 |
| 预览 ≠ 验收 | DESIGN PREVIEW 只发 `ok: null`，绝不写证据/工作区 |

## 六幕剧情与背后的真实事件

| 剧情幕 | 观众看到 | 唯一依据（真实事件） |
| --- | --- | --- |
| THE PROBLEM | 最后一席被错误拒绝！ | 真实业务探针（`seat` 事件，`admitted:false`） |
| THE MISSION | Antigravity 接受任务 | `sdk: agent_session_started` + `mission_submitted` |
| THE INVESTIGATION | Agent 正在调查报名程序 | 观察到的非编辑类 SDK 工具调用（`view_file`、`run_command`…） |
| THE FIX | Agent 正在修复报名逻辑 | 观察到的 `edit_file`/`create_file`/`replace_file_content` 调用 |
| THE CHECK | 正在确认修复是否有效 | `sdk: independent_verification_started` → 独立 pytest |
| THE CELEBRATION | The Last Seat Is Saved! | 验收 `ok:true` **且**最终业务探针 PASS（`seat` `verified:true`） |

剧情状态机单调前进（幕不回退）、失败即终态、且不由任何计时器推动：
`setTimeout` 不驱动任何剧情。工具调用被**观察到**绝不等于执行成功 ——
只有验收链（冻结测试逐字节一致、白名单 diff、磁盘 SHA256 复核、整型退出码）
加上真实业务探针同时通过，并且**属于同一次运行**，才能解锁庆祝画面。
失败、取消、超时或重置都会清除成功状态。

### 成功条件（必须同时满足）

1. Agent 已完成任务（`done`）。
2. 独立 pytest 子进程：修复后全绿。
3. 冻结测试前后逐字节一致。
4. 白名单之外零文件变更（仅 `checkin.py`）。
5. SHA256 映射与磁盘文件复核一致。
6. 真实业务探针（隔离 `python -I`）确认第 40 位可报名。
7. 事件属于当前有效 LIVE 运行（按 run_id 绑定）。

## STAGE VIEW / DEVELOPER VIEW

- **STAGE VIEW（默认）** —— 给观众：六幕条、大字剧情叙述、座位板、报名
  按钮，以及右侧 **Google Antigravity SDK — Live Agent Execution** 面板
  （管线、动态工具卡片、Behind the Magic 代码）。1920×1080 无纵向滚动；
  支持 `prefers-reduced-motion`；可键盘操作。
- **DEVELOPER VIEW** —— 持久头部（SDK 标题、管线、实时 Agent 状态行、
  任务按钮）+ **四 Tab 技术工作区**：`SDK Timeline`（默认）· `Code Diff`
  · `Model Output` · `Evidence`（pytest 前后、运行摘要 + SHA256、GPU 证
  据）。同一时间只显示一个内容区；每个 Tab 面板都是独立受限滚动容器，
  长日志/长 diff/长输出再也不会互相挤压或重叠。
- 切换视图或 Tab 只影响显示：绝不打断 Agent 运行、绝不重开 SSE、不丢历
  史（单一 EventSource 持续接收；滚动位置保留，只有当你滚回底部时才恢
  复自动跟随）。Tab 支持键盘操作（←/→/Home/End），正确维护
  `aria-selected`/`tabindex`。
- 开发者视图中的成功态完整显示 YOU'RE IN! 与 Builder Pass；小视口下面板
  滚动而非裁切（徽章始终可达），已验证到 1280×720。

### 开发者视图布局修复（V3）—— 根因与设计

在 1704×864 上报告了两个 bug（右面板重叠；Builder Pass 被裁），修复前
先在真实浏览器中定位：

- **右面板重叠**：SDK 事件流的 `flex:1` 写在非 flex 的 `.devsec` 父容器
  里，导致无界增长（压力回放下实测 5715px）；同时兄弟区块被 flex-shrink
  压到内容之下、以可见溢出互相叠压，最后被面板 `overflow:hidden` 裁掉。
  修复：工作区改为 `display:grid; grid-template-rows:auto minmax(0,1fr)`；
  每个 Tab 面板 `height:100%; min-height:0; overflow:auto` —— 任何元素
  都逃不出自己的盒子，隐藏的 Tab 零布局足迹。
- **Builder Pass 裁切**：`#win`（为纸屑动画使用 `overflow:hidden`）自身
  被左面板 flex 压缩，把徽章裁在了内部——面板滚动根本救不到。修复：
  `#win{flex:none}` 保持固有高度；`.panel{overflow:auto}` 让小视口的溢出
  变为可滚动的可达内容。座位格列改为 `minmax(0,38px)`，在 1366×768 下
  干净收缩而不再把面板撑宽 37px。
- **回放页**：回放/预览页加载时不再执行启动业务探针（此前会把回放出的
  庆祝态冲掉），非 LIVE 模式下报名按钮保持禁用。

**自动化视觉回归**（`tests/test_devfest_devview_layout.py`，Playwright
驱动本机 Chromium 系通道）：五个视口（1920×1080、1704×864、1366×768、
1280×720、1536×864=1080p@125% 缩放）× 四个 Tab 的 `getBoundingClientRect`
几何断言（面板有界、无横向溢出、页脚不遮挡、徽章可见/可滚动到达、隐藏
Tab 零尺寸），外加 Tab 状态保持 + 单一 EventSource、键盘导航、滚动跟随
语义、回放身份重载存活、舞台视图回归、Agent 错误态与 DESIGN PREVIEW。
夹具为确定性 REPLAY 录制（100 条工具事件、100 条策略行、超长参数、300
行 diff、200 段中英混排 token）—— 仅用于 UI 测试，绝不冒充 LIVE。无浏
览器的 CI 跳过该模块；参考 Windows 机实跑（8/8 通过）。

## Antigravity SDK 可见性（V2 的核心）

右侧面板标题即 **Google Antigravity SDK — Live Agent Execution**，常驻显示
`Gemma 4 26B → Antigravity SDK → Real Tools → Application`。每次真实工具
调用都变成一张观众可读的卡片：

```
Antigravity SDK → view_file     正在阅读报名代码   observed · #2
Antigravity SDK → run_command   正在运行 Python 测试  observed · #3
Antigravity SDK → edit_file     正在修改报名逻辑   observed · #4
```

卡片由真实 `response.tool_calls` 流生成 —— 数量与顺序逐次可变，不是固定
流水线。**Behind the Magic** 展开即仓库真实核心调用：

```python
from google.antigravity import Agent, LocalOpenAIAgentConfig
from google.antigravity.hooks import policy

config = LocalOpenAIAgentConfig(
    model="gemma4:26b",                    # 本地 Ollama（OpenAI 兼容端点）
    base_url="http://127.0.0.1:11434/v1",
    workspaces=[str(DEVFEST_WORKSPACE)],   # 唯一可写工作区
    policies=[policy.allow(t, when=in_workspace_path) for t in FILE_TOOLS]
    + [policy.ask_user("run_command", handler=pytest_only)],
).lightweight()

async with Agent(config) as agent:
    response = await agent.chat(MISSION)   # Gemma 4 决定调用哪些工具
    async for call in response.tool_calls: ...
```

（舞台展示的是忠实节选；可执行的完整实现在 `src/config.py` 与
`src/devfest_agent.py`。）

## 从零启动（Windows PowerShell）

```powershell
git clone https://github.com/AIwork4me/Antigravity-Gemma-ROCm.git
cd Antigravity-Gemma-ROCm
git fetch origin feat/devfest-2026-last-seat-demo   # PR 合并前
git checkout feat/devfest-2026-last-seat-demo

# 1) Python venv（3.12 或 3.13；.venv 已存在则跳过）
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt

# 2) Ollama 与模型（约 18 GB，一次性；已装则跳过，不要重装）
ollama --version            # 能打印版本即可
ollama pull gemma4:26b

# 3) 舞台自动预检（逐项 PASS/WARN/FAIL/BLOCKED；exit 0 = GO）
powershell -ExecutionPolicy Bypass -File scripts\preflight-devfest.ps1
```

参考硬件：Ryzen AI MAX+ 395 / Radeon 8060S iGPU（ROCm gfx1151）。任何有
ROCm 运行器的 AMD Radeon 均可；NVIDIA 走 CUDA 档；纯 CPU 慢但可用
（见 [COMPATIBILITY_MATRIX.md](COMPATIBILITY_MATRIX.md)）。

## 运行

```powershell
# LIVE —— 真实现场运行（先预热模型，再打开浏览器）
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1

# 变体
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -Record recording.json   # LIVE + 录制（供回放/GIF）
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -Replay recording.json   # REPLAY（显著标注，身份保持）
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -DryRun                  # DESIGN PREVIEW（无需 Ollama）
powershell -ExecutionPolicy Bypass -File scripts\start-devfest-demo.ps1 -SkipWarmup -Port 8422   # 自定义端口、跳过预热

# 不用脚本直接启动
.venv\Scripts\python -I src\devfest_webui.py --port 8421
```

舞台流程：预检 → 模型预热（keep-alive 5 分钟）→ 浏览器打开
`http://127.0.0.1:8421`（STAGE VIEW）。页面加载即探测真实报名代码
（THE PROBLEM 已经在台上）：输入虚构开发者姓名 → 点 **报名第 40 席**
（红色拒绝）→ 点 **▶ Give Mission to Antigravity**，看六幕由真实事件推进：
读码 → pytest 红 → 编辑 → pytest 绿 → 独立验证 → **YOU'RE IN!**。

## 3 分钟口播稿

1. **(0:00)**「这是一个真实的 40 席 Workshop 报名系统，已报 39 人。看最后
   一位报名。」—— 输入姓名，点 **报名第 40 席** —— 红色拒绝。缺陷就在
   这台机器上、此刻发生。
2. **(0:20)**「交给完全本地的 Agent —— Gemma 4 决策、Antigravity SDK 执行、
   AMD ROCm 提供算力，无云端、无提示。」点 **▶ Give Mission to
   Antigravity**。
3. **(0:30–2:00)** 随六幕推进讲解：Agent 读代码与冻结测试、跑 pytest
   （红）、只改一行、重跑（绿）。指着管线与工具卡片说「模型决策、骨架
   执行、工具回报证据」；切 DEVELOPER VIEW 指一条策略 ALLOW/DENY：「它
   只能跑 pytest、只能在这个文件夹里」。
4. **(2:00)** THE CHECK 转绿，diff 显示 `>= capacity - 1` →
   `>= capacity`，GPU 徽章显示 `ROCm VERIFIED`。左侧翻面：
   **40 / 40 — YOU'RE IN!**、四色纸屑、Builder Pass（明确虚构）。
5. **(2:20)** 再点一次 **报名第 40 席**：同一个探针这次放行第 40 位开发
   者 —— 来自真实代码，不是硬编码翻转。
6. **(2:30)**「你看到的一切都可以复现」—— 打开 **🗂 验证证据** 或切
   DEVELOPER VIEW：工具轨迹、pytest 前后、SHA256、GPU 归因、耗时。

**90 秒应急兜底：** 演讲前用 `-Record recording.json` 录一次真实运行，现场
`-Replay recording.json` 启动。UI 从横幅到徽章水印全程标注 REPLAY；讲解词：
「这是今天早上在同一台机器上的真实运行」。

## 回放语义（V2）

- 回放**永久保持 RECORDED RUN / REPLAY 身份** —— `replay_done` 后横幅变为
  「回放结束」，但页面绝不重新自称 LIVE；只有**新的真实运行**才能唤回
  LIVE。
- 历史成功画面持续带对角 **REPLAY 水印**。
- 回放期间 run/reset 端点一律 409 —— 录制永远无法触发真实 Agent 运行或
  工作区写入。
- GPU 徽章对回放的 GPU 事件加 `[历史]` 前缀。
- 刷新/断线重连身份不丢（模式是服务端状态，经 `/api/status` 重读）。

## GPU 证据与显存口径（V2 实测发现）

每次运行的 GPU 归因要求实时证据（见架构图）。V2 验证期间在参考机器上
实测到**三个不同的"大小"**，绝不能混为一谈：

| 来源 | 数值（参考机） | 含义 |
| --- | --- | --- |
| `/api/tags` 模型条目 | 18.73 GB | 磁盘上的模型工件 |
| `llama-server.exe` 工作集（`tasklist`） | 40.58 GB（V2 运行） | 运行器的 Windows 进程工作集（操作系统视角的权重 + KV + 计算缓冲）——非独立测量的 GPU 显存值；V1 证据文件早于该字段加入 |
| `/api/ps` `size`/`size_vram` | 2.92 GB（恒定） | Ollama 内部记账 —— **不是**模型占用 |

因此舞台绝不用 `/api/ps` size 冒充「模型显存占用」；DEVELOPER VIEW 分
来源列出三者；`gpu_evidence.json` 逐运行记录。`100% GPU` 驻留
（`size == size_vram`）仍然有意义 —— 它就是 `ollama ps` 打印的
`100% GPU`（放置声明），按原样转述。

## 舞台截图与录制（真实运行，2026-11-11）

全部截自参考机上 1920×1080 的真实 LIVE/REPLAY 运行（除注明外均为 STAGE VIEW）：

| 文件 | 时刻 |
| --- | --- |
| `docs/assets/v2-01-problem.png` | THE PROBLEM —— 报名被拒（真实探针） |
| `docs/assets/v2-03-investigating.png` | THE INVESTIGATION —— 真实 `view_file`/`run_command` 卡片流入 |
| `docs/assets/v2-02-fixing.png` | THE FIX —— 观察到 `edit_file`（LIVE） |
| `docs/assets/v2-04-success.png` | THE CELEBRATION —— 40/40、YOU'RE IN!、Builder Pass、`ROCm VERIFIED` |
| `docs/assets/v2-05-developer.png` | DEVELOPER VIEW —— pytest/SHA256/GPU 证据 |
| `docs/assets/v2-06-replay.png` | REPLAY —— 回放结束后保持 RECORDED RUN 身份 + 水印 |
| `docs/assets/devfest-last-seat-v2.gif` | 由真实事件录制渲染的剧情弧 GIF |
| `docs/assets/last-seat-recording.json` | 真实录制的事件流（回放兜底用） |
| `docs/assets/devfest-last-seat-hero.gif` | README 首屏 Hero —— 真实浏览器捕获本舞台 UI 回放 `last-seat-recording.json`（run `stage-201902`，1.5× 回放剪辑成分幕精选，960×540 循环；再生：`scripts/capture_devfest_hero.py`） |
| `docs/assets/devfest-last-seat-poster.png` | Hero 静态首帧海报（同一次捕获） |
| `docs/assets/devfest-last-seat-hero.meta.json` | Hero 捕获元数据 —— 源录制 SHA256、run id、页面身份断言、场景时间线 |
| `docs/assets/devfest-agent-flow.png` | README 模型/Harness/算力关系图（`scripts/render_agent_flow.py`） |
| `docs/assets/developer-before-fix.png` | （V3）修复前 —— 右面板重叠（压力回放，1920×1080） |
| `docs/assets/developer-before-fix-1704-badge-clip.png` | （V3）修复前 —— 1704×864 徽章被裁 |
| `docs/assets/developer-sdk-timeline-fixed.png` | （V3）修复后 —— SDK Timeline（真实运行） |
| `docs/assets/developer-code-diff-fixed.png` | （V3）修复后 —— Code Diff（真实运行） |
| `docs/assets/developer-model-output-fixed.png` | （V3）修复后 —— Model Output（真实运行） |
| `docs/assets/developer-evidence-fixed.png` | （V3）修复后 —— Evidence（真实运行） |
| `docs/assets/developer-builder-pass-fixed.png` | （V3）修复后 —— 1704×864 完整 Builder Pass |
| `docs/assets/stage-view-regression.png` | （V3）布局修复后舞台视图无回归 |

## 验证与证据

- 物理运行落盘于 `artifacts/devfest-agent-execution/run-*/`（本地、
  gitignore），**精选脱敏副本**收录在 `evidence/devfest-last-seat/run-*/`
  （入库）：任务书、配置、完整工具调用流、策略审计、pytest 前后、
  diff.patch、SHA256 映射、Agent 回复、`gpu_evidence.json`（digest+驻留+
  运行器 DLL PID+后端+显存三来源）、`env.json`、增强版 `summary.json`。
- `evidence/devfest-last-seat/validation_ledger.json` 汇总所有运行与逐项
  验收门 —— 机器可读的 GO/NO-GO 数据源。
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
| 首跑很慢（出 token 前约 45–75 秒） | 模型冷加载；启动脚本会预热 —— 别加 `-SkipWarmup` |
| GPU 徽章 `not resident (cold)` | 首次推理前的正常状态；跑一次后再看 |
| GPU 徽章 `resident (backend unverified)` | 模型已驻留但拿不到运行器的 ROCm DLL 证据 —— 查 `tasklist /m amdhip64*`；可能走了非 ROCm 后端档 |
| 徽章 `场景 BLOCKED · 异常` | 工作区漂移到意外状态 —— 点 ↺ 重置恢复缺陷基线 |
| 重置被拒：工作区是 junction | 故意设防；手动移除 junction 后再重置 |
| 某次运行显示 `tests_tampered` | Agent 改了冻结测试 —— 按设计该次验收 FAIL；重置重跑 |
| 浏览器事件不更新 | SSE 流断开；刷新页面（状态会从真实代码重新推导） |
| Agent 内 pytest 找不到 | 运行 `devfest_webui.py` 的解释器必须属于 venv；用 `scripts\start-devfest-demo.ps1` 启动 |

## 范围与限制

- Builder Pass 是**虚构纪念徽章**，不是入场凭证；参会者名字全部虚构；可选的
  报名姓名有界（24 字符）、去控制字符、经 textContent 渲染、绝不持久化、
  绝不送入模型。
- 舞台 UI 使用 Google/DevFest 2026 配色的原创 CSS/SVG 几何装饰；不分发任何
  主办方模板文件、Logo 或字体。页头 **Google DevFest 2026** 为文字排版
  （Google 品牌色），非官方 Logo 素材；不使用 GDG Shanghai 标识，也不宣称
  任何城市品牌。
- 设计预览绝不算验收；回放在所有界面上都有标注且绝不重贴 LIVE 标；物理硬件
  不可用时，验证台账如实记录 BLOCKED，绝不编造结果。

## V2 相比 V1 改了什么

| 方面 | V1 | V2 |
| --- | --- | --- |
| 品牌 | "The Last Seat · DevFest 2026 · Guangzhou" | **Google DevFest 2026**（四色字母）+ THE LAST SEAT + 副标题 |
| 布局 | 单一混合视图 | STAGE VIEW（剧情）+ DEVELOPER VIEW（证据），1080p 无滚动 |
| 叙事 | 原始事件流 | 六幕剧情状态机，只由真实事件推进 |
| SDK 可见性 | 页脚小字 | 专属 **Antigravity SDK — Live Agent Execution** 面板：管线、工具卡片、生命周期事件、Behind the Magic |
| 庆祝 | 任何 admitted 探针都可能触发（缺陷） | 需验收 PASS **且** 业务探针 PASS **且** 同运行 verified 席位 |
| 回放结束 | 页面重新自称 LIVE（缺陷） | 保持 RECORDED RUN 身份；水印持续；刷新安全 |
| 工作区徽章 | "已修复/漂移" 红色 | READY / FIXING / FIXED / BLOCKED |
| GPU 显存 | 原样展示 `size==size_vram` | 三来源分列标注；舞台只展示后端 + digest |
