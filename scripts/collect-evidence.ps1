# collect-evidence.ps1 — gather environment, logs, versions, hashes, diffs, perf
# into artifacts/evidence-bundle/ for auditability. Read-only w.r.t. the system.
$root = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $root 'artifacts\evidence-bundle'
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

# System + GPU
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, BuildNumber, OSArchitecture |
    ConvertTo-Json | Set-Content "$bundle\os-$stamp.json"
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, PNPDeviceID |
    ConvertTo-Json | Set-Content "$bundle\gpu-$stamp.json"

# Ollama state
try {
    Invoke-RestMethod 'http://127.0.0.1:11434/api/version' | ConvertTo-Json | Set-Content "$bundle\ollama-version-$stamp.json"
    Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' | ConvertTo-Json -Depth 5 | Set-Content "$bundle\ollama-models-$stamp.json"
} catch { Write-Host 'WARN: Ollama API unreachable' }
if (Test-Path "$env:LOCALAPPDATA\Ollama\server.log") {
    Copy-Item "$env:LOCALAPPDATA\Ollama\server.log" "$bundle\ollama-server-$stamp.log" -ErrorAction SilentlyContinue
    # keep only backend-relevant lines to avoid bulky/sensitive content
    Select-String -Path "$bundle\ollama-server-$stamp.log" -Pattern 'library=|compute=|inference compute|runner|offloaded|dropping' |
        ForEach-Object { $_.Line } | Set-Content "$bundle\ollama-backend-lines-$stamp.txt"
    Remove-Item "$bundle\ollama-server-$stamp.log" -ErrorAction SilentlyContinue
}

# SDK versions
$py = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $py) {
    & $py -m pip freeze | Set-Content "$bundle\pip-freeze-$stamp.txt"
}

# Git state
git -C $root log --oneline -20 | Set-Content "$bundle\git-log-$stamp.txt"
git -C $root status --porcelain | Set-Content "$bundle\git-status-$stamp.txt"
git -C $root diff | Set-Content "$bundle\git-diff-$stamp.patch"

# File hashes of source + workspace
$files = Get-ChildItem -Recurse "$root\src", "$root\demo_workspace", "$root\tests" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '__pycache__|\.pytest_cache' }
$files | ForEach-Object {
    [pscustomobject]@{ File = $_.FullName.Replace($root, ''); SHA256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash }
} | ConvertTo-Json | Set-Content "$bundle\sha256-$stamp.json"

Write-Host "EVIDENCE BUNDLE written to $bundle (stamp $stamp)"
