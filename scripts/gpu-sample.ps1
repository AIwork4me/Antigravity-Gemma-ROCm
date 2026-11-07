# gpu-sample.ps1 — samples GPU utilization + GPU memory while a workload runs.
# Usage: powershell -File scripts\gpu-sample.ps1 -Seconds 60 -OutFile artifacts\gpu-backend\sample.json
param(
    [int]$Seconds = 60,
    [string]$OutFile = ""
)
if (-not $OutFile) { $OutFile = Join-Path $PSScriptRoot "..\artifacts\gpu-backend\gpu-sample.json" }
$dir = Split-Path -Parent $OutFile
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$samples = @()
$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
    try {
        $eng = (Get-Counter '\GPU Engine(*engtype_3D)\Utilization Percentage' -ErrorAction Stop).CounterSamples |
            Where-Object { $_.CookedValue -gt 0 } | Measure-Object -Property CookedValue -Sum
        $engUtil = [math]::Round($eng.Sum, 1)
    } catch { $engUtil = $null }
    try {
        # GPU Process Memory: dedicated + shared across all processes
        $mem = (Get-Counter '\GPU Process Memory(*)\Local Usage' -ErrorAction Stop).CounterSamples |
            Measure-Object -Property CookedValue -Sum
        $memLocalGB = [math]::Round($mem.Sum / 1GB, 3)
        $memAll = (Get-Counter '\GPU Process Memory(*)\Total Committed' -ErrorAction Stop).CounterSamples |
            Measure-Object -Property CookedValue -Sum
        $memTotalGB = [math]::Round($memAll.Sum / 1GB, 3)
    } catch { $memLocalGB = $null; $memTotalGB = $null }
    try {
        $cpu = (Get-Counter '\Processor(_Total)\% Processor Time' -ErrorAction Stop).CounterSamples[0].CookedValue
        $cpuUtil = [math]::Round($cpu, 1)
    } catch { $cpuUtil = $null }
    $samples += [pscustomobject]@{
        t = (Get-Date).ToString('HH:mm:ss'); gpu_engine_pct = $engUtil
        gpu_mem_local_gb = $memLocalGB; gpu_mem_total_committed_gb = $memTotalGB; cpu_pct = $cpuUtil
    }
    Start-Sleep -Milliseconds 1000
}
$maxEng = ($samples | Measure-Object -Property gpu_engine_pct -Maximum).Maximum
$maxLocal = ($samples | Measure-Object -Property gpu_mem_local_gb -Maximum).Maximum
$maxTotal = ($samples | Measure-Object -Property gpu_mem_total_committed_gb -Maximum).Maximum
$avgCpu = [math]::Round(($samples | Measure-Object -Property cpu_pct -Average).Average, 1)
$summary = [pscustomobject]@{
    sampled_at = (Get-Date).ToString('s'); duration_s = $Seconds; n = $samples.Count
    max_gpu_engine_pct = $maxEng; max_gpu_mem_local_gb = $maxLocal
    max_gpu_mem_total_committed_gb = $maxTotal; avg_cpu_pct = $avgCpu
    samples = $samples
}
$summary | ConvertTo-Json -Depth 4 | Set-Content -Path $OutFile -Encoding UTF8
Write-Host ("GPU sample: maxEngine={0}% maxLocalMem={1}GB maxTotalCommitted={2}GB avgCPU={3}%" -f $maxEng, $maxLocal, $maxTotal, $avgCpu)
