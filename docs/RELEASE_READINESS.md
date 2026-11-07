# Release Readiness (RC1 Phase 5)

- 时间：2026-11-01 22:20 CST｜扫描工具：`scripts/release_scan.py`（可重复执行）
- 目标：面向普通开发者的 GitHub 公开仓库准备情况；**本地 RC1 commit，不建远程、不推送**

## 1. 发布面定义（两级模型）

| 层 | 内容 | 是否发布 |
|---|---|---|
| **PUBLISH**（代码+文档） | `src/ tests/ scripts/ scenarios/ docs/ README*.md LICENSE pytest.ini requirements.lock.txt .gitignore` | ✅ 可公开 |
| **LOCAL-ONLY**（审计证据） | `artifacts/`（含用户名/绝对路径/机器原始日志）、`.venv/`、`demo_workspace*/`（演示运行区） | ❌ 不随 Release 发布（保留在本地 git 供审计；如需公开证据，另出脱敏摘要包） |

## 2. 隐私 / 凭据扫描结果（ sanitization 后复扫，`artifacts/rc1/release_scan.json`）

| 检查类 | 结果 |
|---|---|
| 绝对用户路径（`C:\Users\<name>\...`） | **0 处**（脚本改为 `%USERPROFILE%`/`$env:USERPROFILE`/脚本相对路径动态推导；文档改为 `<PROJECT_ROOT>`/环境变量写法） |
| 路径形态用户名泄露（`/c/Users/<name>`） | **0 处** |
| 机器 SKU（HN7306…） | **0 处**（已移除；`release_scan.py` 自身模式定义除外） |
| API key / token / 密码类 | **0 处**（无任何凭据；本项目全程无云密钥） |
| SSH 公钥/邮箱 | **0 处** |
| "ROCm" 命中 | 均为 AMD 平台名称的正当技术表述（大小写不敏感误报） |
| "ASUS ProArt PX13" | **有意保留** —— 任务要求的"实测配置"说明（不含序列号/SKU） |

清理后关键脚本复验可运行：`preflight.ps1`（READY）、`rc1_audit.py`（正常产出）、全量 pytest 62 passed +1 skipped。

## 3. README（英文优先 + 中文辅助）

- `README.md`（英文主文档）：项目价值/用途、ASCII 架构图、MAX+ 395 实测配置表、**ROCm 后端实测证据**入口、官方安装路径（winget + `ollama pull`）、一键启动、模型与 SDK 职责表、**演示视频占位**、可复现测试、安全边界与局限、**官方支持状态 vs 实测差异**章节（Windows ROCm 表未列 gfx1151 而 0.40.13 实测默认 ROCm 的如实陈述）。
- `README.zh-CN.md`（中文版，头部互链）。
- `scripts/pull_parallel.py` 在两份 README 中均定位为**网络故障时的备用下载方案**，不在默认安装路径中（默认 = 官方 `ollama pull`）。

## 4. 许可证与外部归属

- 本仓库代码 MIT、文档 CC-BY-4.0（`LICENSE`，含归属说明）。
- 未 vendor 任何外部代码：google-antigravity（Apache-2.0）经 PyPI 运行时安装；模型权重不分发（Gemma 条款经 Ollama 获取）。
- 无第三方源码拷贝；引用的官方文档链接均在 docs 中可溯。

## 5. 未随发布的风险项（如实）

- `artifacts/` 证据含真实用户名/路径/原始日志 → **不进 Release**；若公开须先出脱敏版（后续动作）。
- 演示视频为占位 → 发布后补录（README 已留位置与替代文本证据入口）。
- `requirements.lock.txt` 为精确锁定（含传递依赖），利于复现，不利于依赖安全更新 —— 发布时附 `pip-audit` 建议于 README 或 CI（后续动作）。

## 6. 发布检查单（发布时执行）

1. `git log --oneline` 确认 RC1 SHA；`scripts/release_scan.py` 复扫 0 泄露
2. 新建 GitHub 仓库 → push PUBLISH 面（或 `git archive` 排除 artifacts/ 后上传）
3. 打 tag `rc1`；Release notes 引用 docs/ 下各验证报告
4. 补录演示视频嵌入 README 占位处
5. （推荐）开启 CI：`pytest -m "not live"` + release_scan 每次提交运行
