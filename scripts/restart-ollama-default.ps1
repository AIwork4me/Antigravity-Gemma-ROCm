# restart-ollama-default.ps1 — bring Ollama back to its normal tray-app/default-backend state.
$ErrorActionPreference = 'Continue'
Stop-Process -Name llama-server -Force -ErrorAction SilentlyContinue
Stop-Process -Name ollama -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$app = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
if (Test-Path $app) { Start-Process $app } else { Write-Host "tray app not found; starting ollama serve headless"; Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" 'serve' }
$ok = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    try { $v = (Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3).version; Write-Host "server up: $v"; $ok = $true; break } catch {}
}
if (-not $ok) { Write-Host 'server did not come up'; exit 1 }
Select-String "$env:LOCALAPPDATA\Ollama\server.log" -Pattern 'inference compute' |
    Select-Object -Last 1 | ForEach-Object { Write-Host ($_.Line.Substring(0, [Math]::Min(200, $_.Line.Length))) }
