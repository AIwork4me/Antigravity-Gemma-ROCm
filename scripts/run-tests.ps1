# run-tests.ps1 — automated test suite with acceptance levels.
# L1: local inference works (Ollama API + GPU backend evidence)
# L2: SDK agent completes real tool task (requires model loaded)
# Unit tests always run; live tests are skipped gracefully if server/model absent.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'

Write-Host '=== [1/4] unit tests (no model needed) ===' -ForegroundColor Cyan
& $py -m pytest tests/test_config.py -q
if ($LASTEXITCODE -ne 0) { Write-Host 'UNIT TESTS FAILED'; exit 1 }

Write-Host '=== [2/4] API smoke (model needed) ===' -ForegroundColor Cyan
$tags = $null
try { $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5 } catch {}
$model = $(if ($env:GEMMA_MODEL) { $env:GEMMA_MODEL } else { 'gemma4:26b' })
$has = $false
if ($tags) { $has = (@($tags.models | ForEach-Object { $_.name }) -contains $model) }
if ($has) {
    & $py scripts\smoke_api.py
    $smoke = $LASTEXITCODE
    Write-Host "API SMOKE: $(if ($smoke -eq 0) { 'PASS (L1 evidence)' } else { 'FAIL' })"
} else {
    Write-Host "API SMOKE: SKIPPED (model $model not present)" -ForegroundColor Yellow
}

Write-Host '=== [3/4] agent tool-execution test (L2) ===' -ForegroundColor Cyan
if ($has) {
    & $py -m pytest tests/test_agent.py -q -m live
    Write-Host "AGENT TEST: $(if ($LASTEXITCODE -eq 0) { 'PASS (L2 evidence)' } else { 'FAIL' })"
} else {
    Write-Host 'AGENT TEST: SKIPPED (model absent)' -ForegroundColor Yellow
}

Write-Host '=== [4/4] demo workspace reference tests ===' -ForegroundColor Cyan
Push-Location (Join-Path $root 'demo_workspace')
& $py -m pytest tests/ -q
$demoExit = $LASTEXITCODE
Pop-Location
Write-Host "DEMO WORKSPACE: $(if ($demoExit -eq 0) { 'GREEN (bug currently fixed)' } else { 'RED (bug present — expected before agent run)' })"
exit 0
