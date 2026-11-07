# check-sdk.ps1 — verify Antigravity SDK install, import, config build, and Ollama reachability.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host 'FAIL: venv python not found at .venv\Scripts\python.exe'; exit 1 }

Write-Host '--- pip show google-antigravity ---'
& $py -m pip show google-antigravity 2>&1 | Select-String -Pattern 'Name|Version|Location'

Write-Host '--- import + config build test ---'
& $py -I -c @"
import sys
sys.path.insert(0, r'$root\src')
from google.antigravity import Agent, LocalOpenAIAgentConfig
from config import build_config
cfg = build_config()
print('SDK_IMPORT_OK google-antigravity')
print('CONFIG_OK model=%s base_url=%s' % (cfg.model, cfg.base_url))
print('LIGHTWEIGHT_OK subagents=%s tools=%s' % (cfg.capabilities.enable_subagents, [t.value for t in cfg.capabilities.enabled_tools]))
"@
if ($LASTEXITCODE -ne 0) { Write-Host 'FAIL: SDK import/config test'; exit 1 }

Write-Host '--- Ollama API reachability ---'
try {
    $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10
    Write-Host ("OLLAMA_OK models={0}" -f (@($tags.models | ForEach-Object { $_.name }) -join ','))
} catch {
    Write-Host 'WARN: Ollama not reachable (start it before the demo)'
}
Write-Host 'CHECK_SDK_DONE'
exit 0
