# RC1 Independent Audit (Phase 0)

- 审计时间：2026-11-01 21:30–21:45 CST（RC1 轮，**全部从原始工件独立重算**，不复制历史 PASS）
- 审计脚本：`scripts/rc1_audit.py`（可重复执行）｜原始输出：`artifacts/rc1/audit.json`
- 被审计基线：git `eaa4d241ab212f61c8809bc13972d1ccae46fc17`（审计开始时工作树干净）

## 复核方法

每项声明都从**对应执行轮次的原始工件**重算：每轮 `summary.json` 只作交叉对照，判据来自
`pytest_before/after.json`、`workspace_before/after.json`（SHA256 重算）、`tool_calls.json`
重数、`policy_audit.txt`、以及本轮**新鲜采集**的服务日志与硬件查询。

## 1. 后端身份（本轮新鲜证据 + 历史时间戳链）

| 来源 | 时间 | library / compute |
|---|---|---|
| **本轮重启采集** `artifacts/rc1-serve.log` | 21:28 | **library=ROCm compute=gfx1151** |
| 历史 `server-startup.log` | 17:15:27 | ROCm / gfx1151 |
| 历史 `default-serve.log` | 18:56:31 | ROCm / gfx1151 |
| 历史 `default-serve2.log` | 20:25:29 | ROCm / gfx1151 |
| 历史 `vulkan-serve.log`（强制 A/B） | 18:52:42 | Vulkan（按设计，单独记证） |

本轮冷加载 + 推理 73s 后日志出现 `sched_reserve: graph: nodes=2647, splits=2`（ROCm 调度器
实际建图执行）。

## 2. GPU 身份（本轮重新查询）

`hipInfo.exe` 实跑：`gcnArchName: gfx1151`，`AMD Radeon(TM) 8060S Graphics`。（与 Phase 0 基线
DEV_1586 REV_C1 一致。）

## 3. 模型 digest（本轮重算）

- 本地 manifest 文件逐字节 sha256 = `sha256:001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c`
- `ollama list` ID = `001e5dafc3c7`（12 位短前缀）→ **前缀匹配 ✓**
- manifest 引用的 config + 6 个 layer blob **全部存在且大小一致**（`audit.json → model.blobs`）
- 交叉：`ollama show --modelfile` 的 FROM 指向 17.07GB 主 blob（sha256-a8bdd07a49b8…）

> 审计过程注记：初版审计脚本有两次**比较逻辑 bug**（缺 `sha256:` 前缀 / 用全等比较短 ID），
> 曾误报 digest 不匹配；修正为前缀匹配后确认一致。误报与修正均如实记录。

## 4. 依赖版本（本轮 pip freeze vs 锁文件）

`pip freeze` 与 `requirements.lock.txt` **逐行集合相等**（freeze_eq_lock=True）；
`google-antigravity==0.1.23`。

## 5. 10 次任务逐轮重算（不信任聚合文件）

对 `run-stability-01..10` 逐轮重算，**10/10 全部满足**：

| 复核项 | 结果 |
|---|---|
| 独立 pytest 先红后绿（exit 1→0） | 10/10（且 stdout 文本含 failed→passed） |
| 文件变更集重算（SHA256 前后对照） | 10/10 均仅 `('stats_utils.py',)` |
| 每轮 before 哈希 == pristine 基线哈希 | 10/10（证明每轮从同一 bug 基线恢复） |
| 工具调用重数 == summary 声明 | 10/10 |
| 每轮含真实 edit/create_file 调用 | 10/10 |
| 每轮 policy_audit 含被 ALLOW 的 `python -m pytest` | 10/10 |

## 6. 成功率与耗时（从各轮 summary.json 重算）

- **成功率：10/10**（目标 ≥9）；与 `stability.json` 聚合声明交叉一致（matches_recomputed=true）
- 耗时（Agent 阶段 elapsed_s）：**均值 85.5s，min 62.3s，max 105.2s**
  （stability 外层 wall 时间 64.0–106.8s 略高，差值=驱动进程开销，两处独立计时互证）

## 7. 证据出处一致性（provenance）

- 10 个 summary.json 的 mtime **严格单调递增**（20:30:18 → 20:43:39），无跨轮拼接迹象
- 每轮证据目录内文件构成完整（mission/config/tool_calls/policy_audit/pytest×2/workspace×2/summary）

## 结论

Phase 0 复核未发现任何虚假 PASS、跨轮证据混用或声明与原始数据不符。
基线 `eaa4d24` 的全部关键声明（ROCm gfx1151 / 模型完整性 / 依赖锁定 / 10/10 先红后绿闭环）
**在本轮独立重算下成立**。
