# GPU Backend Validation (Phase 3)

- Machine / driver baseline: see `artifacts/environment-baseline.{md,json}`
- All timestamps CST (UTC+8), 2026-11-01. Ollama **0.40.13** (winget, official).
- Raw evidence: `artifacts/gpu-backend/`

## 结论

**BACKEND = ROCm（默认，Windows 原生 gfx1151 上经 Ollama 自带 rocm_v7_1 runner）**
Vulkan 备用路径经强制切换验证同样可用（工程后备，非 ROCm 证据）。
CPU 推理被排除（推理期 CPU ~8.6%，GPU 内存驻留 31-39GB）。

## 判定方法（多重独立证据交叉，不依赖单一 "100% GPU"）

| # | 证据 | ROCm 默认路径 | Vulkan 强制路径 |
|---|---|---|---|
| 1 | server 启动探测行 | `inference compute id=0 library=ROCm compute=gfx1151 name=ROCm0 libdirs=ollama,rocm_v7_1 type=discrete total="78.2 GiB"` | `inference compute ... library=Vulkan name=Vulkan0 type=iGPU total="102.9 GiB"` + `skipping available library at user's request ... rocm_v7_1` |
| 2 | 运行时调度缓冲 | `sched_reserve: ROCm0 compute buffer size = 1608.09 MiB`（+`ROCm_Host 1124.11 MiB`） | (Vulkan 设备命名不同；见 #3/#4) |
| 3 | **推理进程 DLL** | llama-server (PID 30664) 实际加载 `amdhip64_7.dll`, `ggml-hip.dll`, `libhipblas.dll`, `libhipblaslt.dll`, `rocblas.dll` | llama-server 加载 `ggml-vulkan.dll`, `vulkan-1.dll`（无 HIP DLL） |
| 4 | 输出正确性 | `BACKEND_PROBE_OK` 精确回复；1-20 计数正确 | 同样正确 |
| 5 | ollama ps | `100% GPU`, ctx 262144 | `100% GPU` |
| 6 | GPU 内存 | llama-server Total Committed **30.98 GB**（权重+KV+缓冲）；采样峰值全系统 39.07GB | 类似量级 |
| 7 | GPU 计算活动 | 采样窗口 GPU engine 10-14%（3D 队列计数器；CNQ 计算队列不经此计数器完整暴露） | — |
| 8 | CPU 参与度 | 平均 8.6% / 峰值 11.5%（32 线程）→ 排除 CPU 推理 | — |

## 环境矩阵（三次独立服务启动）

1. **tray 默认**（2026-11-01 17:15）：`library=ROCm compute=gfx1151`（Vulkan iGPU 被默认丢弃：`dropping integrated GPU; to enable, set OLLAMA_IGPU_ENABLE=1`）
2. **受控强制 Vulkan**（18:52，`OLLAMA_LLM_LIBRARY=vulkan OLLAMA_IGPU_ENABLE=1 OLLAMA_DEBUG=1`）：`library=Vulkan`，系统 loader `C:\Windows\system32\vulkan-1.dll`
3. **受控默认重启**（18:56）：再次 `library=ROCm compute=gfx1151` → 默认选择可复现

## 官方支持口径 vs 实测

- docs.ollama.com/gpu 的 Windows ROCm 支持表未列 gfx1151 iGPU（列 RX 7600+ 与 PRO W7xxx）
- 实测 0.40.13 自带 `rocm_v7_1` runner 在 Windows 原生探测并运行 gfx1151（与 issue #17498 用户在 0.32.5 的观察一致）
- **以实测为验收依据**：ROCm 路径 = 上述 8 项证据；非官方表格扩张性声明

## 已知风险与缓解

- **issue #17498**（ROCm+gfx1151 ≥~1.2k prompt tokens 输出损坏，0.32.5/gemma4:12b）：0.40.13 上以
  `src/regression_ctx.py --full`（1k/1.2k/2k/4k/8k/16k × text/json/tool）+ 双后端对照复测，
  结果见 `docs/GEMMA4_GFX1151_REGRESSION.md`。若复现：演示路径切 Vulkan（`OLLAMA_LLM_LIBRARY=vulkan OLLAMA_IGPU_ENABLE=1`），
  并如实将 P4 标注为条件性通过/失败。
- 长上下文显存：ROCm 视角可用 78GB（Vulkan 视角 102.9GB 共享）；262144 默认 ctx 的 KV 分配
  在 128GB UMA 上可行但加载变慢，演示可用 `OLLAMA_CONTEXT_LENGTH` 调低。

## 复现命令

```powershell
# 默认路径证据
Select-String "$env:LOCALAPPDATA\Ollama\server.log" -Pattern 'inference compute|sched_reserve'
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" ps
# runner DLL 证据
powershell -File scripts\phase3_backend_probe.ps1 -Label myprobe
# Vulkan 强制 A/B
$env:OLLAMA_LLM_LIBRARY='vulkan'; $env:OLLAMA_IGPU_ENABLE='1'; & "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve
```
