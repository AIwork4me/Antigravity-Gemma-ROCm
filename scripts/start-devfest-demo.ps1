# start-devfest-demo.ps1 — one-command stage launch for "The Last Seat".
# Runs the readiness gate, warms the model (keep-alive 5m) so the first
# on-stage run skips the ~60-75s cold load, then opens the dashboard.
#
# Usage:
#   scripts\start-devfest-demo.ps1                 # LIVE  (real local agent)
#   scripts\start-devfest-demo.ps1 -Replay rec.json  # REPLAY (recorded run)
#   scripts\start-devfest-demo.ps1 -DryRun          # DESIGN PREVIEW (no Ollama)
#   scripts\start-devfest-demo.ps1 -Record rec.json # LIVE + record the stream
param(
    [int]$Port = 8421,
    [string]$Model = $(if ($env:AGENT_MODEL) { $env:AGENT_MODEL }
                       elseif ($env:GEMMA_MODEL) { $env:GEMMA_MODEL }
                       else { 'gemma4:26b' }),
    [string]$Record = '',
    [string]$Replay = '',
    [double]$ReplaySpeed = 1.0,
    [switch]$DryRun,
    [switch]$SkipWarmup
)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host 'venv missing — run scripts\setup.ps1 first'; exit 1 }

# 1) readiness gate (blocking for dry-run too — cheap and catches module errors)
& "$PSScriptRoot\preflight-devfest.ps1" -Port $Port -Model $Model
if ($LASTEXITCODE -ne 0 -and -not $DryRun) {
    Write-Host '⚠ preflight NOT READY — dashboard blocked. Fix the [FAIL]/[BLOCKED] items above.' -ForegroundColor Red
    exit 1
}

# 2) warm the model so the stage run starts fast (skipped for replay/dry-run)
if (-not $DryRun -and -not $Replay -and -not $SkipWarmup) {
    Write-Host "预热 $Model (keep_alive 5m) — 冷加载约 60-75s，请稍候…" -ForegroundColor Cyan
    try {
        $body = @{ model = $Model; keep_alive = '5m' } | ConvertTo-Json
        Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' -Method Post `
            -Body $body -ContentType 'application/json' -TimeoutSec 240 | Out-Null
        Write-Host '模型已驻留 GPU ✓' -ForegroundColor Green
    } catch { Write-Host "预热失败（服务仍会启动，首跑会冷加载）: $_" -ForegroundColor Yellow }
}

# 3) launch
$args = @('-I', 'src\devfest_webui.py', '--port', $Port)
if ($Record) { $args += @('--record', $Record) }
if ($Replay) { $args += @('--replay', $Replay, '--replay-speed', $ReplaySpeed) }
if ($DryRun) { $args += '--dry-run' }

Start-Process "http://127.0.0.1:$Port"
$mode = if ($DryRun) { 'DESIGN PREVIEW（非真实运行）' } elseif ($Replay) { 'REPLAY 回放' } else { 'LIVE 真实运行' }
Write-Host "舞台已打开: http://127.0.0.1:$Port — $mode （关闭本窗口即停止）" -ForegroundColor Cyan
& $py @args
