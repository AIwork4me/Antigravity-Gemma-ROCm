# DevFest Rehearsal Report (RC1 Phase 3)

- 时间：2026-11-01 21:56–22:06 CST；脚本：`scripts/rehearse.ps1`、`scripts/demo-status.ps1`
- 证据：`artifacts/rehearsal/`（rehearsal-*.log 精确到毫秒、rounds.json、roundN-*.log、roundN-diff.patch）
- 环境基线：本轮全部为默认 ROCm 后端（`artifacts/rc1-serve.log`）

## 1. 演示状态分类器（`demo-status.ps1`，实测）

| 状态 | 判定条件 | 实测 |
|---|---|---|
| **READY** | 服务在 + 模型列出 + **模型驻留 GPU** + 后端=ROCm + SDK/策略自检通过 + 工作区处于 bug 基线 | 彩排每轮 init 后达到 |
| **DEGRADED** | 关键项通过但非关键项失败（如工作区漂移/模型未驻留需冷加载） | 实测命中一次（彩排前工作区漂移，init 步骤后自愈为 READY） |
| **BLOCKED** | 模型不可用 / 非 GPU 后端 / 策略无法确认 / 关键测试失败 | 本轮未出现（分类逻辑就位） |

`preflight.ps1` 连续 4 轮 PASS（1.5–1.7s）。

## 2. 预热与 keep-alive（实测）

- 默认 `OLLAMA_KEEP_ALIVE=5m`：彩排相邻轮间隔 <2min，模型全程驻留
- 两次预热耗时 **3.2s / 1.9s**（驻留态短生成）；对照冷加载 52–73s → **开场必须预热一次**

## 3. 连续计时彩排（每轮：preflight → 初始化测试仓库 → Agent 修复 → 独立 pytest → diff → 恢复）

| 轮次 | preflight | Agent+pytest | 总耗时 | ≤120s | 结果 |
|---|---|---|---|---|---|
| 1 | PASS (1.7s) | GREEN (86.1s) | **88.6s** | ✓ | PASS |
| 2 | PASS (1.5s) | GREEN (102.8s) | **105.1s** | ✓ | PASS |
| 3 | PASS (1.5s) | GREEN (91.1s) | **93.4s** | ✓ | PASS |
| 4*（diff 展示修正后验证轮） | PASS (1.7s) | GREEN (66s) | **68.9s** | ✓ | PASS |

**4/4 通过，未关闭任何验收项**（preflight 全项运行、独立 pytest 照跑、diff 如实展示）。

*轮 1–3 发现并修复一个展示层问题：diff 原本对比 git HEAD（内含历史修复版本），导致部分轮
显示 0 行；改为对比冻结 pristine 基线后，diff 稳定显示 Agent 真实修改（验证轮 24 行）。
计时与 GREEN 判定不受该问题影响。

## 4. 失败恢复路径（已真实验证）

| 故障 | 恢复步骤 | 依据 |
|---|---|---|
| Agent 轮失败/超时 | 重跑 `start-demo.ps1`（内部重新植入 bug 基线；无需手动清理） | demo_agent 每轮 restore 实测 |
| 模型被卸载（>5min 空闲） | 重新预热（60–75s）或提前 3min 内触发生成 | 冷加载 65.1s 实测（phase8） |
| 工作区漂移 | `git checkout -- demo_workspace` 或重跑 init 步骤（<1s） | DEGRADED→READY 实测 |
| 服务无响应 | `scripts\restart-ollama-default.ps1`（实测 20s 内恢复默认 ROCm 后端） | 本轮多次重启实测 |
| 现场彻底失败 | 播放预录证据：`artifacts/agent-execution/run-demo3/`（完整轨迹+diff+红绿证明）+ 可提前录屏 | 证据齐备 |

## 5. 结论

**彩排判定：PASS（4/4 轮 ≤120s，最快 68.9s，含完整验收链）**；状态分类器三种状态实测
（READY/DEGRADED 命中，BLOCKED 逻辑就位未触发）。建议现场流程照 rehearse.ps1 顺序执行，
开场预热一步不可省。
