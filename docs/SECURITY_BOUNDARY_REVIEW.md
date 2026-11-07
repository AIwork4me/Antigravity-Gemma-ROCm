# Security Boundary Review (RC1 Phase 1)

- 时间：2026-11-01 21:50–22:10 CST
- 测试套件：`tests/test_security_boundaries.py`（59 passed / 1 skipped，隔离 tmp 沙箱 + 哨兵文件，未接触真实私人数据）
- 复验：修复后另跑一次**真实 Agent 任务** `run-rc1-guard-smoke`（77.9s 全绿，5 工具调用不变）→ 修复无假阳性

## 边界模型：两个独立安全边界

| 边界 | 实现层 | 判定逻辑 |
|---|---|---|
| **B1 文件路径授权** | `policy.allow(file_tools, when=file_pred)` | 一切路径型参数 → `Path.resolve(strict=False)` 解析 reparse point → 剥 `\\?\`/`\\?\UNC\` 前缀 → 词法折叠 `..` → 小写化 → 必须以工作区为前缀（含分隔符） |
| **B2 Shell 进程权限** | `ask_user(run_command, handler=白名单)` | ①无 shell 元字符 ②前缀 ∈ {pytest, python, …} ③含工作区关键词 ④无黑名单词 ⑤`Cwd`（若提供）必须在工作区内 |

两边界互不替代：B1 不约束进程（run_command 可访问任意文件系统对象），B2 不约束文件工具。
**Windows 下 SDK 的 run_command 子进程无系统级沙箱**——本审查不虚构沙箱；以 B2 白名单 +
审计日志 + 已披露限制（人工授权场景可用 `ask_user` 人工 handler / 低权限账户运行驱动进程）为
实际控制。

## 红队基线（修复前）与修复后对照

| 攻击面 | 用例 | 修复前 | 修复后 |
|---|---|---|---|
| 相对路径 | `AbsolutePath: "project.py"` | 拒绝 ✓ | 拒绝 ✓ |
| `..` 穿越（\\/、/、深层、混合大小写） | `ws\..\secret.txt` 等 5 例 | 拒绝 ✓ | 拒绝 ✓ |
| 大小写不敏感（盘符/目录大写） | `C:\USERS\...\PROJECT.PY` | 允许 ✓（正确放行） | 允许 ✓ |
| 前缀伪装 | `ws-lookalike\x`、`ws_evil\x` | 拒绝 ✓ | 拒绝 ✓ |
| **Junction 逃逸** | `ws\linkdir\secret.txt`（junction→外部哨兵目录） | **允许 ✗ 漏洞** | **拒绝 ✓（resolve 解析真实目标）** |
| Junction 指向工作区内部 | `ws\oklink\x.py` | 允许 ✓ | 允许 ✓（非过度封锁） |
| 符号链接文件逃逸 | `ws\linkfile.txt`→外部哨兵 | 需 dev-mode（本机跳过） | 逻辑同 junction（同一 resolve 路径）；标记 SKIP 如实记录 |
| UNC | `\\server\share\s.txt`、`\\?\UNC\...` | 拒绝 ✓ | 拒绝 ✓ |
| 扩展长度 `\\?\` | `\\?\C:\Windows\...` / `\\?\C:\ws\...` | 拒绝（碰巧）/ **拒绝（误伤）** | 拒绝外部 ✓ / **允许内部 ✓（前缀剥离修复）** |
| 不存在目标（新建文件） | `ws\newdir\new.py` | 允许 ✓ | 允许 ✓ |
| 不存在父目录+穿越 | `ws\noexist\..\..\x` | 拒绝 ✓ | 拒绝 ✓ |
| 无路径参数 | `{"Contents": ...}` | 拒绝（保守）✓ | 拒绝 ✓ |
| **Shell 元字符注入** | `; whoami`、`&& calc`、`\| findstr`、`> file`、`< in`、`` `whoami` ``、`$(whoami)`、换行 | **7/9 穿透 ✗ 漏洞** | **9/9 拒绝 ✓（元字符黑名单）** |
| `&` 简单后台 | `pytest & curl` | 拒绝（黑名单 curl）✓ | 拒绝 ✓（双保险） |
| 任意代码 | `python -c`、`python -m http.server`、`python -m pip install` | 拒绝 ✓ | 拒绝 ✓ |
| **进程工作目录（Cwd）** | 合法命令 + `Cwd=外部` | **允许 ✗ 漏洞** | **拒绝 ✓（Cwd 必须在工作区；无 norms 上下文时保守拒绝）** |
| Cwd lookalike | `Cwd=ws+"-evil"` | 允许 ✗ | 拒绝 ✓ |
| 合法 pytest/python | 4 种形态 | 允许 ✓ | 允许 ✓（含 Cwd=工作区时） |

## 发现与修复清单（本轮新发现，全部修复并有测试）

1. **[高] B1 Junction/Reparse 逃逸**：词法归一化可被 `mklink /J` 绕过（无需管理员权限即可创建
   junction）。修复：`Path.resolve()` 沿真实存在部分解析链接后比较。测试：`test_junction_escape`。
2. **[高] B2 Shell 元字符注入**：`; && | > < ` $(` 及换行可在白名单命令后拼接任意命令。
   修复：任一元字符出现即拒绝（白名单命令本身从不需要它们）。测试：`TestShellMetacharInjection` 9 例。
3. **[中] B2 Cwd 未校验**：进程可被重定向到工作区外执行（相对路径文件访问逃逸）。
   修复：Cwd 必须位于工作区内；无法校验时保守拒绝。测试：`TestCwdBoundary` 4 例。
4. **[低] B1 `\\?\` 扩展长度路径**：未剥前缀导致"内部路径误拒、外部路径碰巧拒"。
   修复：剥 `\\?\`/`\\?\UNC\` 前缀后统一归一。测试：`TestUncAndExtendedLength` 3 例。

## 残余风险（如实披露）

- **符号链接**测试因需 Windows 开发者模式/管理员而 SKIP：junction（无需特权、更现实向量）已
  实测拦截；symlink 走同一 `resolve()` 代码路径，理论一致但**未在本机实测**。
- B2 白名单允许的 `python`/`pytest` 进程本身仍可读写文件系统（无 OS 级沙箱）；当前缓解 =
  元字符禁令 + 关键词限定 + 审计日志。高保障场景：驱动进程以低权限账户运行，或 handler 接
  人工确认（SDK 原生 `ask_user` 支持）。
- TOCTOU：resolve 后至实际执行间理论上可替换链接；风险窗口极小，未做内核级句柄校验。
- 路径参数键名枚举（`AbsolutePath`/`Path`/`target`/…）基于 0.1.23 实测 schema；SDK 升级需
  复跑本套件。
