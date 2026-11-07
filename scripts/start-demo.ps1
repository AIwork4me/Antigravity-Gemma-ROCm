# start-demo.ps1 — run the live demo: local agent fixes the planted bug.
# Resets demo_workspace to the buggy baseline, runs the agent (Antigravity SDK
# + local Ollama Gemma 4), verifies with independent pytest, shows the diff.
param(
    [string]$RunId = $(Get-Date -Format 'HHmmss'),
    [string]$Model = $(if ($env:AGENT_MODEL) { $env:AGENT_MODEL }
                       elseif ($env:GEMMA_MODEL) { $env:GEMMA_MODEL }
                       else { 'gemma4:26b' }),
    [int]$TimeoutSec = 900
)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'

Write-Host "=== local agent demo run-$RunId (model=$Model) ===" -ForegroundColor Cyan

# Pre-check
& (Join-Path $PSScriptRoot 'preflight.ps1')
if ($LASTEXITCODE -ne 0) { Write-Host 'Preflight failed — fix above items first.'; exit 1 }

# Snapshot the buggy baseline diff-expectation
Write-Host "`n--- baseline pytest (bug must be present) ---"
Push-Location (Join-Path $root 'demo_workspace')
& $py -m pytest tests/ -q | Select-Object -Last 3
Pop-Location

# Run the agent
Write-Host "`n--- agent run (watch it work) ---"
& $py src\demo_agent.py --run-id $RunId --model $Model --timeout $TimeoutSec
$agentExit = $LASTEXITCODE

# Show the diff the agent produced
Write-Host "`n--- git diff of demo_workspace ---"
git -C $root diff -- demo_workspace | Out-String | Write-Host

Write-Host "`nDEMO RESULT: $(if ($agentExit -eq 0) { 'SUCCESS - bug found & fixed, tests green' } else { 'INCOMPLETE - see artifacts\agent-execution\run-' + $RunId })" -ForegroundColor $(if ($agentExit -eq 0) { 'Green' } else { 'Yellow' })
exit $agentExit
