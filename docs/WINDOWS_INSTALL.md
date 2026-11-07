# Windows 原生安装复现指南

目标机：Windows 11（22H2+），AMD Radeon 8060S / Ryzen AI MAX+ 395（gfx1151），≥64GB 内存推荐（模型 ~16GB + KV cache）。

## 1. 安装 Ollama（官方 winget 包）

```powershell
winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
# 安装后托盘应用自动启动服务；验证：
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" --version   # 0.40.13
Invoke-RestMethod http://127.0.0.1:11434/api/version
```

## 2. 拉取 Gemma 4 26B

```powershell
ollama pull gemma4:26b     # ~16-19GB, 默认存 %USERPROFILE%\.ollama
```

**故障排查 — 下载卡死/超时**：若日志出现
`dial tcp [2606:4700:...]:443: connectex ... failed`（本机 IPv6 到 Cloudflare R2 路由坏），
不要改系统网络配置，改用本仓库的 IPv4 并行下载器（等价官方 registry 协议 + sha256 校验）：

```powershell
cd <PROJECT_ROOT>
.\.venv\Scripts\python.exe scripts\pull_parallel.py library\gemma4 26b --chunks 24
ollama list   # 应显示 gemma4:26b
```

如需自定义模型目录：设置用户环境变量 `OLLAMA_MODELS` 后重启 Ollama（官方方法）。

## 3. Python 环境（隔离 venv，不动系统/conda 基础环境）

```powershell
cd <PROJECT_ROOT>
%USERPROFILE%\miniconda3\python.exe -m venv .venv            # 任意 Python 3.11+
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt   # google-antigravity==0.1.23 等
.\scripts\check-sdk.ps1                                       # import + config 构建验证
```

## 4. 验证 GPU 后端

```powershell
Select-String "$env:LOCALAPPDATA\Ollama\server.log" -Pattern 'inference compute|library=|dropping'
# 期望看到: inference compute ... library=ROCm compute=gfx1151 name=ROCm0 ... libdirs=ollama,rocm_v7_1
ollama ps   # 模型加载后应显示 100% GPU（辅助证据，非判定依据）
```

强制后端 A/B（排障用）：

```powershell
$env:OLLAMA_LLM_LIBRARY='rocm_v7_1'; $env:OLLAMA_VULKAN='0'   # 强制 ROCm
$env:OLLAMA_LLM_LIBRARY='vulkan'; Remove-Item Env:OLLAMA_VULKAN -ErrorAction SilentlyContinue  # 强制 Vulkan
# 改完需重启 Ollama 服务（退出托盘应用后重新启动）
```

## 5. 冒烟与演示

```powershell
.\scripts\smoke_api.ps1            # PowerShell 独立冒烟
.\.venv\Scripts\python.exe scripts\smoke_api.py   # Python 冒烟
.\scripts\start-demo.ps1           # 完整 bug 修复演示
```

## 已知注意

- 需要 Adrenalin 驱动较新版本（本机 32.0.22032.6002 / 2025-12-15 实测可用）。
- 虚拟显示适配器（GameViewer）会出现在 Vulkan 枚举中；如选错设备用 `GGML_VK_VISIBLE_DEVICES` 指定。
- 卸载只走"添加/删除程序"；`OLLAMA_MODELS` 自定义目录不会被卸载清除（官方行为）。
