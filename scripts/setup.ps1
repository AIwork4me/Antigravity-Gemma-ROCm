# setup.ps1 — idempotent project setup for the Antigravity-Gemma-ROCm local agent.
# - creates .venv if missing (from miniconda python)
# - installs deps from requirements-dev.txt (runtime + dev tools)
#   (requirements.lock.txt stays as the reference-machine pin, for exact repro)
# - verifies Ollama is installed (does NOT install silently if absent; prints instructions)
# - verifies the model exists (offers pull command if missing)
param(
    [string]$Model = $(if ($env:AGENT_MODEL) { $env:AGENT_MODEL }
                       elseif ($env:GEMMA_MODEL) { $env:GEMMA_MODEL }
                       else { 'gemma4:26b' })
)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1. Python venv
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host '[setup] creating .venv ...'
    $basePy = "$env:USERPROFILE\miniconda3\python.exe"
    if (-not (Test-Path $basePy)) { $basePy = (Get-Command python -ErrorAction SilentlyContinue).Source }
    & $basePy -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host 'FAIL: venv creation'; exit 1 }
} else {
    Write-Host '[setup] .venv exists (reusing)'
}

# 2. Dependencies (idempotent)
Write-Host '[setup] installing dependencies ...'
$dev = Join-Path $root 'requirements-dev.txt'
& $py -m pip install -r $dev --quiet
if ($LASTEXITCODE -ne 0) { Write-Host 'FAIL: pip install'; exit 1 }

# 3. Ollama present?
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    $cand = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $cand) { $ollama = $cand } else {
        Write-Host '[setup] Ollama NOT installed. Install it with:'
        Write-Host '        winget install --id Ollama.Ollama -e'
        exit 1
    }
}
Write-Host "[setup] Ollama found: $ollama"

# 4. Server up?
try {
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 5
    Write-Host '[setup] Ollama server is UP'
} catch {
    Write-Host '[setup] Ollama server not responding — starting app ...'
    Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe" -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5
}

# 5. Model present?
$tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10
$names = @($tags.models | ForEach-Object { $_.name })
if ($names -contains $Model) {
    Write-Host "[setup] model $Model present"
} else {
    Write-Host "[setup] model $Model MISSING. Run:  ollama pull $Model"
    Write-Host '        (if downloads stall with IPv6 errors, use: python scripts\pull_parallel.py library/gemma4 26b)'
}
Write-Host '[setup] DONE'
exit 0
