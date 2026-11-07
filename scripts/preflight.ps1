# preflight.ps1 — PASS/FAIL/BLOCKED readiness gate before the demo.
# ROCm-first: celebrates a native ROCm detection, maps other backends to
# their documented tiers, works on non-reference GPUs, and auto-restores the
# demo workspace from pristine so a fresh clone is always demo-ready.
$root = Split-Path -Parent $PSScriptRoot
$results = @()
function Check($name, $kind) {
    # kind: PASS / FAIL / BLOCKED / WARN
    $script:results += [pscustomobject]@{ Check = $name; Result = $kind }
    Write-Host ("[{0}] {1}" -f $kind, $name)
}

# OS
$os = Get-CimInstance Win32_OperatingSystem
if ($os.Caption -like '*Windows 11*' -or $os.Caption -like '*Windows 10*') { Check "OS = $($os.Caption) build $($os.BuildNumber)" 'PASS' } else { Check "OS unsupported: $($os.Caption)" 'FAIL' }

# GPU (any GPU is fine; classify into tiers, only OS missing is a warn)
$gpus = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name })
$main = $null
foreach ($g in $gpus) {
    if ($g.Name -like '*Radeon*8060S*') { $main = $g; break }
    if ($g.Name -like '*Radeon*') { $main = $g }
    if (-not $main -and $g.Name -like '*NVIDIA*') { $main = $g }
}
if ($main) {
    $drv = $main.DriverVersion
    if ($main.Name -like '*8060S*') {
        Check "GPU = $($main.Name) driver $drv (reference machine: Ryzen AI MAX class, ROCm gfx1151)" 'PASS'
    } elseif ($main.Name -like '*Radeon*') {
        Check "GPU = $($main.Name) driver $drv (AMD — ROCm expected, see README hardware table)" 'PASS'
    } else {
        Check "GPU = $($main.Name) driver $drv (CUDA path — community tier, see README)" 'PASS'
    }
} elseif ($gpus.Count -gt 0) {
    Check "GPU = $($gpus[0].Name) (unclassified — CPU-tier fallback may apply)" 'WARN'
} else {
    Check 'No GPU detected (CPU-only inference: expect slow generation)' 'WARN'
}

# Model (AGENT_MODEL preferred, legacy GEMMA_MODEL kept)
$model = $(if ($env:AGENT_MODEL) { $env:AGENT_MODEL }
           elseif ($env:GEMMA_MODEL) { $env:GEMMA_MODEL }
           else { 'gemma4:26b' })

# Ollama
try {
    $ver = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 5
    Check "Ollama server $($ver.version)" 'PASS'
} catch { Check 'Ollama server unreachable (start it: ollama serve, or launch the app)' 'BLOCKED' }

try {
    $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
    $names = @($tags.models | ForEach-Object { $_.name })
    if ($names -contains $model) { Check "model $model available" 'PASS' }
    else { Check "model $model missing -> run:  ollama pull $model" 'BLOCKED' }
} catch { Check 'cannot list models' 'BLOCKED' }

# venv + SDK
$py = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $py) {
    & $py -I -c "from google.antigravity import Agent, LocalOpenAIAgentConfig; import google.antigravity" 2>$null
    if ($LASTEXITCODE -eq 0) { Check 'Antigravity SDK import OK' 'PASS' } else { Check 'Antigravity SDK import FAILED (run scripts\setup.ps1)' 'FAIL' }
} else { Check '.venv missing (run scripts\setup.ps1)' 'BLOCKED' }

# Demo workspace: auto-restore from pristine on fresh clones / drift
$dw = Join-Path $root 'demo_workspace'
$pristine = Join-Path $root 'demo_workspace_pristine'
if ((Test-Path (Join-Path $dw 'stats_utils.py')) -and (Test-Path (Join-Path $dw 'tests\test_stats.py'))) {
    Check 'demo_workspace present' 'PASS'
} elseif (Test-Path (Join-Path $pristine 'stats_utils.py')) {
    if (Test-Path $dw) { Remove-Item -Recurse -Force $dw }
    Copy-Item -Recurse $pristine $dw
    if ((Test-Path (Join-Path $dw 'stats_utils.py'))) {
        Check 'demo_workspace restored from pristine' 'PASS'
    } else { Check 'demo_workspace restore FAILED' 'FAIL' }
} else { Check 'demo_workspace_pristine incomplete (repo corrupted?)' 'FAIL' }

# GPU backend evidence (from last server log lines) — ROCm-first verdict
$log = "$env:LOCALAPPDATA\Ollama\server.log"
if (Test-Path $log) {
    $rocm = Select-String -Path $log -Pattern 'library=ROCm' -ErrorAction SilentlyContinue | Select-Object -Last 1
    $vulkan = Select-String -Path $log -Pattern 'library=Vulkan' -ErrorAction SilentlyContinue | Select-Object -Last 1
    $cuda = Select-String -Path $log -Pattern 'library=CUDA' -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($rocm) { Check "[ROCm] native detection: $($rocm.Line.Substring(0, [Math]::Min(140, $rocm.Line.Length)))" 'PASS' }
    elseif ($vulkan) { Check 'Vulkan fallback active (documented tier — see GPU_BACKEND_VALIDATION.md)' 'PASS' }
    elseif ($cuda) { Check 'CUDA backend (community tier — see README hardware table)' 'PASS' }
} else { Check 'no server.log yet (backend shown after first model load)' 'WARN' }

$nFail = @($results | Where-Object Result -eq 'FAIL').Count
$nBlock = @($results | Where-Object Result -eq 'BLOCKED').Count
Write-Host "`nPREFLIGHT SUMMARY: $(if ($nFail -eq 0 -and $nBlock -eq 0) { 'READY FOR DEMO' } else { 'NOT READY' }) (FAIL=$nFail BLOCKED=$nBlock)"
exit $(if ($nFail -eq 0 -and $nBlock -eq 0) { 0 } else { 1 })
