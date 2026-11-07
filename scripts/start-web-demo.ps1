# start-web-demo.ps1 — launch the browser dashboard for the demo.
# Starts the local web UI and opens the default browser.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host 'venv missing — run scripts\setup.ps1 first'; exit 1 }

# preflight (non-blocking warning if not READY)
& "$PSScriptRoot\preflight.ps1"
if ($LASTEXITCODE -ne 0) { Write-Host '⚠ preflight 未全绿 —— 仪表盘仍会启动，运行前请按提示修复。' -ForegroundColor Yellow }

Start-Process "http://127.0.0.1:8420"
Write-Host '仪表盘已打开: http://127.0.0.1:8420 （关闭本窗口即停止）' -ForegroundColor Cyan
& $py -I src\webui.py --port 8420
