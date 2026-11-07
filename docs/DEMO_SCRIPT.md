# DevFest 现场演示脚本（约 8 分钟）

## 开场前（观众入场时已在后台完成）
- `scripts\preflight.ps1` 全绿截图留存
- Ollama 服务运行中，模型已在 `ollama list`
- （可选）`ollama run gemma4:26b "ping"` 预热，避免现场冷加载等待

## 1. 开场主张（30s）
"这台 Windows 笔记本上跑着一个 26B 的 Gemma 4——不联网、不云端、不出本机内存。
今天看它作为真正的 Agent，自己读代码、跑测试、修 bug。"

## 2. 展示硬件与后端（1min）
```powershell
Get-CimInstance Win32_VideoController | Select Name, DriverVersion
Select-String "$env:LOCALAPPDATA\Ollama\server.log" -Pattern 'inference compute' | Select -Last 1
```
指出：`library=ROCm compute=gfx1151` —— AMD ROCm/HIP runner 在 Windows 上原生驱动这块 8060S。
（如现场为 Vulkan 路径，如实说明并指向 docs/GPU_BACKEND_VALIDATION.md 的 A/B 证据。）

## 3. 展示要修的 bug（1.5min）
```powershell
cd demo_workspace; Get-Content stats_utils.py | Select -Last 8
..\.venv\Scripts\python.exe -m pytest tests\ -q        # 2 failed, 3 passed
```
讲解：median 偶数长度返回了下界中位数而非两中位数均值——真实、可复现、有测试定义的正确性。

## 4. 运行 Agent（3-4min，核心）
```powershell
cd <PROJECT_ROOT>
.\scripts\start-demo.ps1
```
口播要点（对照终端输出）：
- 任务是纯自然语言；模型自己决定工具调用顺序
- 工具执行发生在 Antigravity SDK harness（真实进程，非模拟）
- run_command 被白名单策略约束（展示 policy_audit.txt）
- 观众能看到工具调用列表：view_file → run_command(pytest) → edit_file → run_command(pytest) → 总结

## 5. 验证闭环（1min）
- 独立 pytest：全绿（强调：不是模型说修好了，是测试进程退出码 0）
- `git diff demo_workspace`：一行修复
- `workspace_before/after.json` SHA256 对比：只有 stats_utils.py 变化

## 6. 收尾主张（30s）
"全部 token 在本地生成；关掉 Wi-Fi 也能跑（我们有离线验证报告）。
Model=Gemma 4 26B，Harness=Antigravity SDK，推理=Ollama，算力=这块 AMD gfx1151。"

## 备援路径
- 模型超时/挂起 → `ollama stop gemma4:26b` 后重跑 start-demo.ps1
- 现场完全失败 → 播放 artifacts/agent-execution 预录 run 的证据 + summary.json
- 降级 → GEMMA_MODEL=gemma4:12b 环境变量切换备用模型
