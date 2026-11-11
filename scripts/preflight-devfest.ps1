# preflight-devfest.ps1 — automated stage readiness gate for "The Last Seat".
# Checks everything the on-stage run needs and prints PASS/WARN/FAIL/BLOCKED
# per item. Exit code: 0 = READY (GO), 1 = at least one BLOCKED/FAIL (NO-GO).
param(
    [int]$Port = 8421,
    [string]$Model = $(if ($env:AGENT_MODEL) { $env:AGENT_MODEL }
                       elseif ($env:GEMMA_MODEL) { $env:GEMMA_MODEL }
                       else { 'gemma4:26b' })
)
$root = Split-Path -Parent $PSScriptRoot
$results = @()
function Check($name, $kind) {
    $script:results += [pscustomobject]@{ Check = $name; Result = $kind }
    Write-Host ("[{0}] {1}" -f $kind, $name)
}

# OS
$os = Get-CimInstance Win32_OperatingSystem
if ($os.Caption -like '*Windows 10*' -or $os.Caption -like '*Windows 11*') {
    Check "OS = $($os.Caption) build $($os.BuildNumber)" 'PASS'
} else { Check "OS unsupported: $($os.Caption)" 'FAIL' }

# GPU
$gpus = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name })
$radeon = $gpus | Where-Object { $_.Name -like '*Radeon*8060S*' } | Select-Object -First 1
if ($radeon) {
    Check "GPU = $($radeon.Name) (Ryzen AI MAX class, ROCm gfx1151 expected)" 'PASS'
} elseif ($gpus = @($gpus | Where-Object { $_.Name -notlike '*GameViewer*' })) {
    Check "GPU = $($gpus[0].Name) (non-reference — backend may differ; see README hardware table)" 'WARN'
} else { Check 'No usable GPU detected (CPU-only inference: very slow)' 'WARN' }

# Ollama + model
try {
    $ver = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 5
    Check "Ollama server $($ver.version)" 'PASS'
} catch { Check 'Ollama unreachable (start: ollama serve, or launch the app)' 'BLOCKED' }
try {
    $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
    if (@($tags.models | ForEach-Object { $_.name }) -contains $Model) {
        Check "model $Model available" 'PASS'
    } else { Check "model $Model missing -> ollama pull $Model" 'BLOCKED' }
} catch { Check 'cannot list models' 'BLOCKED' }

# residency hint (warm = fast start)
try {
    $ps = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/ps' -TimeoutSec 5
    $live = @($ps.models | Where-Object { $_.name -eq $Model })
    if ($live.Count -gt 0) {
        Check "model resident in VRAM (digest $($live[0].digest.Substring(0,12)))" 'PASS'
    } else {
        Check 'model NOT resident — cold load takes ~60-75s; warm up before the talk' 'WARN'
    }
} catch { Check 'cannot query residency' 'WARN' }

# venv + SDK + new modules
$py = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $py) {
    Check 'venv present' 'PASS'
    $sdk = & $py -c "import google.antigravity, sys; print('ok')" 2>$null
    if ($sdk -eq 'ok') { Check 'Antigravity SDK importable' 'PASS' }
    else { Check 'Antigravity SDK missing in venv (scripts\setup.ps1)' 'FAIL' }
} else { Check 'venv missing — run scripts\setup.ps1 first' 'BLOCKED' }

foreach ($m in 'devfest_agent', 'devfest_webui', 'devfest_probe') {
    $out = & $py -c "import sys; sys.path.insert(0,'src'); import $m; print('ok')" 2>$null
    if ($out -eq 'ok') { Check "src\$m.py importable" 'PASS' }
    else { Check "src\$m.py failed to import" 'FAIL' }
}

# scenario baseline: pristine defective state must be intact (1 failing test)
$pristine = Join-Path $root 'devfest_workspace_pristine'
if (Test-Path (Join-Path $pristine 'checkin.py')) {
    Push-Location $pristine
    $pt = & (Join-Path $root '.venv\Scripts\python.exe') -m pytest tests/ -q --no-header 2>$null
    Pop-Location
    if ($LASTEXITCODE -eq 1) { Check 'pristine scenario: exactly the last-seat test fails (RED baseline intact)' 'PASS' }
    elseif ($LASTEXITCODE -eq 0) { Check 'pristine scenario is GREEN — defect baseline was modified/leaked!' 'FAIL' }
    else { Check "pristine pytest exited $LASTEXITCODE" 'FAIL' }
} else { Check 'devfest_workspace_pristine missing' 'BLOCKED' }

# port free?
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Check "port $Port already in use (PID $($busy[0].OwningProcess)) — stop it or use -Port" 'FAIL'
} else { Check "port $Port free" 'PASS' }

# summary
$bad = @($results | Where-Object { $_.Result -in 'FAIL', 'BLOCKED' })
$warn = @($results | Where-Object { $_.Result -eq 'WARN' })
Write-Host ''
if ($bad.Count -eq 0) {
    Write-Host ("STAGE READY (GO): {0} PASS, {1} WARN" -f
        @($results | Where-Object { $_.Result -eq 'PASS' }).Count, $warn.Count) -ForegroundColor Green
    exit 0
} else {
    Write-Host ("NOT READY (NO-GO): {0} blocking item(s)" -f $bad.Count) -ForegroundColor Red
    $bad | ForEach-Object { Write-Host ("  - {0} [{1}]" -f $_.Check, $_.Result) }
    exit 1
}
