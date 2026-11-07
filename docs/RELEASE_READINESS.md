# Release Readiness

> **版本状态分层（T1.2 起）**：本文件分三层记录 —— ① v1.0.1 当前候选状态；
> ② T1/T1.1 工程加固结果；③ 下方的 RC1 历史记录（2026-11-01，保持原样存档）。
> 测试计数按阶段区分（RC1: 62 → T1: 170/177 → T1.1: 193/258/269 → T1.2: 见下），
> 不得混用。

---

## ① v1.0.1 候选状态（T1.2，2026-11-07）

| 项 | 状态 |
|---|---|
| 代码基线 | dev 分支 `t1.2-release-closure`（自 `t1-trending-readiness`@304d7d8 切出），公开导出经 `scripts/publish_export.py` 单向发布 |
| 变更范围 | GPU 验证器非破坏化（共享 Ollama 不再被默认卸载模型）、GPU workflow 收紧（仅 main、ENV_PROBE 独立分类）、证据链归属加固（真实 DLL 名、runner PPID 归属、run seal）、文档时态修正 |
| 单元/安全测试 | `pytest -q`：**323 passed, 1 skipped**（T1.1 基线 269 → +54；含两轮对抗复审修复的非干扰/归属/封条/扫描器正负测试） |
| live 测试 | `pytest -m live -q`：3 项（真机 Ollama） |
| CI | GitHub-hosted windows-latest × Python 3.12/3.13：ruff + format + pytest + release_scan --enforce + pip-audit |
| 物理 GPU 验证 | `scripts/gpu_validate.py --with-agent` 在 Radeon 8060S（gfx1151）真机执行；v1.0.1 发布以 T1.2 新鲜 PASS 为准（不沿用 T1.1 历史 PASS） |
| 证据资产 | `evidence-bundle-v1.0.1.zip`（脱敏后构建，含内层 SHA256SUMS 与外层 .sha256 sidecar） |
| 安全边界声明 | 见 [SECURITY.md](SECURITY.md) 与 [GPU_BACKEND_VALIDATION.md](GPU_BACKEND_VALIDATION.md)：无 OS 级沙箱；pytest allowlist ≠ 执行隔离；loopback ≠ 认证；证据 sidecar ≠ 数字签名 |
| 历史资产披露 | v1.0.0 的 `evidence-bundle-v1.0.0.zip` 内含开发机用户名路径（`C:\Users\<name>\…`）—— 作为 legacy disclosure 如实记载于 v1.0.1 Release Notes；历史资产不删除、不改写 |
| 发布门禁顺序 | 修复 → 验证 → 治理（main 保护 + GPU environment 审批）→ 合并 PR #1 → tag v1.0.1 → Release |

## ② T1 / T1.1 工程加固（历史记录，2026-11-07）

- T1：安全与发布加固、CI 双腿（3.12/3.13）、GPU 物理验证 v2（归因优先证据链）、
  公开仓 Draft PR #1。修正了 T0 时代"引用 3 天前共享 server.log"的假阳性 GPU PASS。
- T1.1：两轮独立复审（1 MED+8 LOW；3 MED+4 LOW+5 NIT）全部修复带测试；发布面三表面
  扫描（dev + export + zip）0 发现；测试线 170→177→193→258→**269**；
  GPU 旗舰证据 `gpuval-20261107-202720`（PASS, full scope）。
- 公开审计摘要：[SECURITY_AUDIT_T1.md](SECURITY_AUDIT_T1.md)。

---

## ③ RC1 历史记录（2026-11-01，原样存档）

- 时间：2026-11-01 22:20 CST｜扫描工具：`scripts/release_scan.py`（可重复执行）
- 目标：面向普通开发者的 GitHub 公开仓库准备情况；**本地 RC1 commit，不建远程、不推送**

### 3.1 发布面定义（两级模型）

| 层 | 内容 | 是否发布 |
|---|---|---|
| **PUBLISH**（代码+文档） | `src/ tests/ scripts/ scenarios/ docs/ README*.md LICENSE pytest.ini requirements.lock.txt .gitignore` | ✅ 可公开 |
| **LOCAL-ONLY**（审计证据） | `artifacts/`（含用户名/绝对路径/机器原始日志）、`.venv/`、`demo_workspace*/`（演示运行区） | ❌ 不随 Release 发布（保留在本地 git 供审计；如需公开证据，另出脱敏摘要包） |

### 3.2 隐私 / 凭据扫描结果（sanitization 后复扫，`artifacts/rc1/release_scan.json`）

| 检查类 | 结果 |
|---|---|
| 绝对用户路径（`C:\Users\<name>\...`） | **0 处**（脚本改为 `%USERPROFILE%`/`$env:USERPROFILE%`/脚本相对路径动态推导；文档改为 `<PROJECT_ROOT>`/环境变量写法） |
| 路径形态用户名泄露（`/c/Users/<name>`） | **0 处** |
| 机器 SKU（HN####） | **0 处**（已移除；`release_scan.py` 自身模式定义除外） |
| API key / token / 密码类 | **0 处**（无任何凭据；本项目全程无云密钥） |
| SSH 公钥/邮箱 | **0 处** |
| "ROCm" 命中 | 均为 AMD 平台名称的正当技术表述（大小写不敏感误报） |
| "ASUS ProArt PX13" | **有意保留** —— 任务要求的"实测配置"说明（不含序列号/SKU） |

清理后关键脚本复验可运行：`preflight.ps1`（READY）、`rc1_audit.py`（正常产出）、
全量 pytest **62 passed +1 skipped**（RC1 时点计数）。

### 3.3 README（英文优先 + 中文辅助）

- `README.md`（英文主文档）：项目价值/用途、ASCII 架构图、MAX+ 395 实测配置表、
  **ROCm 后端实测证据**入口、官方安装路径（winget + `ollama pull`）、一键启动、
  模型与 SDK 职责表、**演示视频占位**、可复现测试、安全边界与局限、
  **官方支持状态 vs 实测差异**章节（Windows ROCm 表未列 gfx1151 而 0.40.13 实测
  默认 ROCm 的如实陈述）。
- `README.zh-CN.md`（中文版，头部互链）。
- `scripts/pull_parallel.py` 在两份 README 中均定位为**网络故障时的备用下载方案**，
  不在默认安装路径中（默认 = 官方 `ollama pull`）。

### 3.4 许可证与外部归属

- 本仓库代码 MIT、文档 CC-BY-4.0（`LICENSE`，含归属说明）。
- 未 vendor 任何外部代码：google-antigravity（Apache-2.0）经 PyPI 运行时安装；
  模型权重不分发（Gemma 条款经 Ollama 获取）。
- 无第三方源码拷贝；引用的官方文档链接均在 docs 中可溯。

### 3.5 未随发布的风险项（如实，RC1 时点）

- `artifacts/` 证据含真实用户名/路径/原始日志 → **不进 Release**；若公开须先出
  脱敏版（后续动作）。
- 演示视频为占位 → 发布后补录（README 已留位置与替代文本证据入口）。
- `requirements.lock.txt` 为精确锁定（含传递依赖），利于复现，不利于依赖安全更新
  —— 发布时附 `pip-audit` 建议于 README 或 CI（后续动作：CI 已含 pip-audit）。

### 3.6 RC1 发布检查单（已由 T1/T1.1/T1.2 流程取代，存档）

1. `git log --oneline` 确认 RC1 SHA；`scripts/release_scan.py` 复扫 0 泄露
2. 新建 GitHub 仓库 → push PUBLISH 面（或 `git archive` 排除 artifacts/ 后上传）
3. 打 tag `rc1`；Release notes 引用 docs/ 下各验证报告
4. 补录演示视频嵌入 README 占位处
5. （推荐）开启 CI：`pytest -m "not live"` + release_scan 每次提交运行
   （现状：CI 已按此建议落地，另加 ruff 与 pip-audit）
