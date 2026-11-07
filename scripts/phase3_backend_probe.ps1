# phase3_backend_probe.ps1 — load model, capture backend evidence bundle.
# Usage: powershell -File scripts\phase3_backend_probe.ps1 [-Label rocm-default]
param(
    [string]$Label = "probe",
    [string]$Model = $(if ($env:GEMMA_MODEL) { $env:GEMMA_MODEL } else { 'gemma4:26b' }),
    [string]$Prompt = 'Reply with exactly: BACKEND_PROBE_OK'
)
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "artifacts\gpu-backend\$Label"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$log = "$env:LOCALAPPDATA\Ollama\server.log"
$marker = (Get-Item $log).Length   # only read new bytes appended after now

# 1. trigger load + inference with timing
$body = @{ model = $Model; stream = $false; messages = @(@{ role = 'user'; content = $Prompt }) } | ConvertTo-Json -Depth 5
$t0 = Get-Date
$resp = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/v1/chat/completions' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 900
$dt = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
"{0}`nload+infer wall_s={1}`nreply={2}`nusage={3}" -f $Prompt, $dt, $resp.choices[0].message.content, ($resp.usage | ConvertTo-Json -Compress) | Set-Content "$out\chat.txt"

# 2. ollama ps state
Start-Sleep -Seconds 2
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" ps 2>&1 | Out-String | Set-Content "$out\ollama-ps.txt"

# 3. new server.log lines since marker
$fs = [System.IO.File]::Open($log, 'Open', 'Read', 'ReadWrite')
$fs.Seek($marker, 'Begin') | Out-Null
$sr = New-Object System.IO.StreamReader($fs)
$newLog = $sr.ReadToEnd(); $sr.Close(); $fs.Close()
$newLog | Set-Content "$out\server-log-delta.txt"

# 4. runner processes + loaded modules (hip vs vulkan DLLs)
$procs = Get-Process | Where-Object { $_.ProcessName -like 'ollama*' -or $_.ProcessName -like 'llama*' }
$procs | Select-Object Id, ProcessName, Path | ConvertTo-Json | Set-Content "$out\processes.json"
$mods = @()
foreach ($p in $procs) {
    try {
        foreach ($m in $p.Modules) {
            if ($m.ModuleName -match 'hip|rocblas|vulkan|ggml') {
                $mods += [pscustomobject]@{ Proc = $p.ProcessName; PID = $p.Id; Module = $m.ModuleName }
            }
        }
    } catch {}
}
$mods | Sort-Object Proc, Module -Unique | ConvertTo-Json | Set-Content "$out\runner-modules.json"

# 5. GPU memory snapshot (process memory counters)
try {
    $mem = (Get-Counter '\GPU Process Memory(*)\Total Committed' -ErrorAction Stop).CounterSamples | Where-Object { $_.CookedValue -gt 100MB }
    $mem | ForEach-Object { [pscustomobject]@{ Instance = $_.InstanceName; GB = [math]::Round($_.CookedValue / 1GB, 2) } } |
        ConvertTo-Json | Set-Content "$out\gpu-process-memory.json"
} catch {}

Write-Host "PHASE3_PROBE done -> $out"
Get-Content "$out\chat.txt"
