# demo-status.ps1 — classify the demo machine into READY / DEGRADED / BLOCKED.
# READY    : model resident, ROCm confirmed, tool policies effective
# DEGRADED : demo can still run, but a non-critical check failed (cold model,
#            Vulkan fallback in use, minor workspace drift)
# BLOCKED  : model unavailable / backend not GPU / policies unverifiable /
#            critical tests failing
$root = Split-Path -Parent $PSScriptRoot
$checks = [ordered]@{}
function Note($k, $v) { $script:checks[$k] = $v }

# --- server & model ---
$server = $false; $modelListed = $false; $modelResident = $false
try {
    $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 4
    $server = $true
    $tags = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 4
    $modelListed = (@($tags.models | ForEach-Object { $_.name }) -contains 'gemma4:26b')
    $ps = & "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" ps 2>$null | Out-String
    $modelResident = ($ps -match 'gemma4:26b') -and ($ps -match 'GPU')
} catch {}
Note 'server_up' $server
Note 'model_listed' $modelListed
Note 'model_resident_on_gpu' $modelResident

# --- backend ---
$backend = 'UNKNOWN'
$candidates = @("$root\artifacts\rc1-serve.log", "$env:LOCALAPPDATA\Ollama\server.log")
foreach ($c in $candidates) {
    if (Test-Path $c) {
        $line = Select-String -Path $c -Pattern 'inference compute' -ErrorAction SilentlyContinue | Select-Object -Last 1
        if ($line -and $line.Line -match 'library=ROCm') { $backend = 'ROCm'; break }
        if ($line -and $line.Line -match 'library=Vulkan') { $backend = 'Vulkan'; break }
    }
}
Note 'gpu_backend' $backend

# --- SDK & policies ---
$sdk = $false
$py = "$root\.venv\Scripts\python.exe"
if (Test-Path $py) {
    & $py -I -c "import sys; sys.path.insert(0, r'$root\src'); from google.antigravity import Agent; from config import build_config; c = build_config(); assert len(c.policies) >= 3" 2>$null
    $sdk = ($LASTEXITCODE -eq 0)
}
Note 'sdk_and_policies_ok' $sdk

# --- workspace baseline integrity (frozen bug must be present for the demo) ---
$wsOk = $false
$pristine = Get-FileHash "$root\demo_workspace_pristine\stats_utils.py" -Algorithm SHA256
$current = if (Test-Path "$root\demo_workspace\stats_utils.py") {
    (Get-FileHash "$root\demo_workspace\stats_utils.py" -Algorithm SHA256).Hash
} else { $null }
Note 'workspace_at_bug_baseline' ($pristine.Hash -eq $current)

# --- classification ---
$criticalOk = ($server -and $modelListed -and ($backend -eq 'ROCm') -and $sdk)
if ($criticalOk -and $modelResident -and $checks['workspace_at_bug_baseline']) { $status = 'READY' }
elseif ($criticalOk) { $status = 'DEGRADED' }
else { $status = 'BLOCKED' }

$obj = [pscustomobject]($checks + @{ status = $status })
$obj | ConvertTo-Json | Write-Host
exit ($(if ($status -eq 'BLOCKED') { 2 } elseif ($status -eq 'DEGRADED') { 1 } else { 0 }))
