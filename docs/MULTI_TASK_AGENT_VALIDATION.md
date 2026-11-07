# Multi-Task Agent Validation (RC1 Phase 2)

- 时间：2026-11-01 22:15–22:45 CST；模型 gemma4:26b @ 默认 ROCm 后端（加固后策略，Phase 1 产物）
- 驱动器：`src/scenario_agent.py`；矩阵运行器：`scripts/multitask_matrix.py`
- 证据：`artifacts/multitask/{A_boundary,B_writetests,C_excfix}-run{1,2,3}/`
  （每轮含 before/after SHA 快照、agent.json 工具轨迹、pytest_before/after.json、diff.patch、acceptance.json）
- 原始 Demo（median）保持原样，其历史结果不用于本轮判定。

## 场景设计（全部冻结基线 + 独立验收，无预注入答案）

| 场景 | 任务类型 | 冻结件 | 允许变更 | 成功准则（独立进程判定） |
|---|---|---|---|---|
| **A** `A_boundary` | 边界条件 bug 修复（`clamp` 下界返回 `hi` 的植入缺陷） | `tests/test_textutils.py` | 仅 `textutils.py` | 冻结测试先红后绿 + 真实工具轨迹 + 无越权变更 |
| **B** `B_writetests` | 按 REQUIREMENTS.md 编写 pytest（实现视为正确） | `grading.py`、`REQUIREMENTS.md` | 仅新建 `tests/test_grading.py` | 套件存在且绿 + **变异测试：3 个冻结变异体（边界漂移/缺失校验/区间错位）至少杀 2** |
| **C** `C_excfix` | 依据**真实崩溃日志**（authentic traceback：`int('port=8080')` ValueError）定位修复 | `tests/`、`crash.log` | 仅 `settings_parser.py` | 冻结测试先红后绿 + 真实工具轨迹 + 无越权变更 |

任务提示为纯自然语言（见 `src/scenario_agent.py::MISSIONS`），不含任何修复暗示。
变异测试杜绝"空洞测试自评通过"：Agent 写的测试必须在错误实现上失败才算数。

## 结果矩阵（9/9 ACCEPT）

| 场景 | 成功率 | Agent 均耗时 | min/max | 平均工具调用 | 变异击杀 | 失败原因 |
|---|---|---|---|---|---|---|
| A 边界修复 | **3/3** | 76.7s | 60.6 / 85.5 | 9.0 | — | 无 |
| B 补写测试 | **3/3** | 64.4s | 47.0 / 78.4 | 4.0 | **3/3 × 3 轮**（run1/2/3 全杀） | 无 |
| C 日志定位异常 | **3/3** | 110.0s | 94.6 / 121.7 | 6.0 | — | 无 |

驱动器墙钟（含冻结恢复+独立 pytest+SHA 审计）：47.0s–123.4s。

## 验收完整性抽查（由 acceptance.json 逐轮核实）

- 全部 9 轮 `illegal_changes=[]`：变更严格落在允许集合内（A/C 只动目标源文件；B 只新建测试文件）
- A/C 每轮 `bug_present_before=true`（冻结测试先红），`all_green_after=true`（独立 pytest 退出码 0）
- B 每轮 `grading.py`/`REQUIREMENTS.md` SHA 与冻结基线一致；`tests/test_grading.py` 为新建
- B 变异体验收：m1（85→A 边界漂移）/m2（负分不抛错）/m3（75 档位错位）**每轮 3/3 被杀**
- 示例 diff（C run1，`diff.patch`）：在 `parse_port` 中按 `=` 分割并 strip 后 `int()`

## 结论

三种**不同形态**的任务（修边界 bug／为规格补测试并接受变异检验／从崩溃日志定位异常）
各 3 次独立运行全部通过独立验收进程；Agent 的工具使用随任务形态合理变化
（A 需多轮读-跑-改，B 一次成套编写，C 读日志+复现+修复）。
**Multi-task 能力判定：PASS（9/9）**，无失败分类项。
