# Phase 2 API smoke test (PowerShell, independent of Python)
# Checks /api/tags, /v1/models, /v1/chat/completions on local Ollama.
$ErrorActionPreference = 'Stop'
$Base = if ($env:OLLAMA_BASE) { $env:OLLAMA_BASE } else { 'http://127.0.0.1:11434' }
$Model = if ($env:GEMMA_MODEL) { $env:GEMMA_MODEL } else { 'gemma4:26b' }
$Out = Join-Path $PSScriptRoot '..\artifacts\ollama\smoke_api_powershell.json'
$ok = $true
$report = @{ base = $Base; model = $Model; checks = @{} }

try {
    $tags = Invoke-RestMethod -Uri "$Base/api/tags" -TimeoutSec 30
    $names = @($tags.models | ForEach-Object { $_.name })
    $report.checks['api_tags'] = @{ status = 200; models = $names }
    Write-Host "[1] /api/tags -> 200 models=$($names -join ',')"
    if ($names.Count -eq 0) { $ok = $false }
} catch {
    $report.checks['api_tags'] = @{ error = $_.Exception.Message }
    Write-Host "[1] /api/tags FAILED: $($_.Exception.Message)"; $ok = $false
}

try {
    $mods = Invoke-RestMethod -Uri "$Base/v1/models" -TimeoutSec 30
    $ids = @($mods.data | ForEach-Object { $_.id })
    $report.checks['v1_models'] = @{ status = 200; ids = $ids }
    Write-Host "[2] /v1/models -> 200 ids=$($ids -join ',')"
    if ($ids.Count -eq 0) { $ok = $false }
} catch {
    $report.checks['v1_models'] = @{ error = $_.Exception.Message }
    Write-Host "[2] /v1/models FAILED: $($_.Exception.Message)"; $ok = $false
}

try {
    $body = @{ model = $Model; stream = $false; messages = @(@{ role = 'user'; content = 'Reply with exactly: LOCAL_OK' }) } | ConvertTo-Json -Depth 5
    $t0 = Get-Date
    $comp = Invoke-RestMethod -Uri "$Base/v1/chat/completions" -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 600
    $dt = [math]::Round(((Get-Date) - $t0).TotalSeconds, 2)
    $text = $comp.choices[0].message.content
    $report.checks['v1_chat'] = @{ status = 200; reply = $text; usage = $comp.usage; total_sec = $dt }
    Write-Host "[3] /v1/chat/completions -> 200 reply='$text' usage=$($comp.usage | ConvertTo-Json -Compress) total_sec=$dt"
    if (-not $comp.usage.completion_tokens) { $ok = $false }
} catch {
    $report.checks['v1_chat'] = @{ error = $_.Exception.Message }
    Write-Host "[3] /v1/chat/completions FAILED: $($_.Exception.Message)"; $ok = $false
}

$report | ConvertTo-Json -Depth 6 | Set-Content -Path $Out -Encoding UTF8
Write-Host "SMOKE_RESULT: $(if ($ok) { 'PASS' } else { 'FAIL' })"
if (-not $ok) { exit 1 }
