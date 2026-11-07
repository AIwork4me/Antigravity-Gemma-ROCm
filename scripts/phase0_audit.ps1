# phase0_audit.ps1 - Read-only environment audit for GDG DevFest Gemma4 Agent project
# Collects real observed values only. No system modifications.
# Output: artifacts/environment-baseline.json + artifacts/environment-baseline.md
$ErrorActionPreference = 'Continue'
$proj = Split-Path -Parent $PSScriptRoot
$art = Join-Path $proj 'artifacts'
New-Item -ItemType Directory -Force -Path $art | Out-Null
$ts = Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'

function Safe($script) {
    try { & $script } catch { "ERROR: $($_.Exception.Message)" }
}

$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor
$gpus = Get-CimInstance Win32_VideoController

# --- PnP hardware IDs for display devices (gfx architecture evidence) ---
$pnpIds = @()
foreach ($d in (Get-PnpDevice -Class Display -ErrorAction SilentlyContinue)) {
    $hw = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction SilentlyContinue).Data
    $pnpIds += [pscustomobject]@{ InstanceId = $d.InstanceId; Status = $d.Status; HardwareIds = ($hw -join '; ') }
}

# --- RAM sticks ---
$ramSticks = Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer, Capacity, Speed, PartNumber

# --- Tools ---
function ToolInfo($name) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { [pscustomobject]@{ Name = $name; Found = $true; Source = $c.Source } }
    else { [pscustomobject]@{ Name = $name; Found = $false; Source = $null } }
}
$tools = @('ollama','python','python3','py','pip','git','pwsh','node','winget','docker','wsl','codex','claude') | ForEach-Object { ToolInfo $_ }

$pyLaunchers = try { (py -0p 2>&1 | Out-String).Trim() } catch { "py launcher not available" }
$ollamaVersion = try { (ollama --version 2>&1 | Out-String).Trim() } catch { 'NOT INSTALLED' }
$gitVersion = try { (git --version 2>&1 | Out-String).Trim() } catch { 'NOT FOUND' }
$wingetVersion = try { (winget --version 2>&1 | Out-String).Trim() } catch { 'NOT FOUND' }

# --- Ollama state ---
$port11434 = Get-NetTCPConnection -LocalPort 11434 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, State, OwningProcess
$ollamaProcs = Get-Process ollama* -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path
$ollamaUserEnv = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')
$ollamaMachineEnv = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'Machine')
$ollamaHomeModels = Test-Path "$env:USERPROFILE\.ollama\models"
$ollamaLocalAppData = Test-Path "$env:LOCALAPPDATA\Ollama"

# --- Disks ---
$disks = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID, VolumeName,
    @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}},
    @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}}

# --- ROCm / HIP evidence ---
$rocmDirs = @()
foreach ($p in @('C:\Program Files\AMD\ROCm', 'C:\Program Files\AMD', 'C:\Program Files (x86)\AMD')) {
    if (Test-Path $p) { $rocmDirs += (Get-ChildItem $p -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name | ForEach-Object { "$p\$_" }) }
}
$condaRocm = try { (conda list 2>$null | Select-String -Pattern 'rocm|hip' | Out-String).Trim() } catch { 'conda not on PATH' }
$hipTools = @('hipcc','hipInfo','rocminfo','rocm-smi') | ForEach-Object { ToolInfo $_ }

# --- Relevant env vars ---
$envVars = @{}
foreach ($n in @('OLLAMA_MODELS','OLLAMA_HOST','OLLAMA_DEBUG','HIP_PATH','ROCM_PATH','HSA_OVERRIDE_GFX_VERSION','GGML_VULKAN_DEVICE','Path')) {
    $envVars[$n] = [Environment]::GetEnvironmentVariable($n)
}

# --- Installed programs (registry, filtered) ---
$uninstallKeys = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$programs = Get-ItemProperty $uninstallKeys -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match 'AMD|Ollama|Python|Git|ROCm|HIP|Adrenalin|Radeon|Vulkan|Miniconda|Anaconda' } |
    Select-Object DisplayName, DisplayVersion, Publisher, InstallDate |
    Sort-Object DisplayName

# --- Assemble JSON ---
$report = [ordered]@{
    collected_at        = $ts
    collected_by        = 'scripts/phase0_audit.ps1 (read-only)'
    os                  = [ordered]@{
        caption = $os.Caption; version = $os.Version; build = $os.BuildNumber
        arch = $os.OSArchitecture
        total_visible_mem_KB = $os.TotalVisibleMemorySize
        free_physical_mem_KB = $os.FreePhysicalMemory
        last_boot = $os.LastBootUpTime
    }
    machine             = [ordered]@{ manufacturer = $cs.Manufacturer; model = $cs.Model; total_physical_mem_bytes = $cs.TotalPhysicalMemory }
    cpu                 = [ordered]@{ name = $cpu.Name; cores = $cpu.NumberOfCores; logical = $cpu.NumberOfLogicalProcessors; max_mhz = $cpu.MaxClockSpeed }
    gpu_controllers    = $gpus | Select-Object Name, DriverVersion, DriverDate, AdapterRAM, VideoProcessor, PNPDeviceID, Status
    gpu_pnp_hwids      = $pnpIds
    ram_sticks         = $ramSticks
    tools              = $tools
    py_launchers       = $pyLaunchers
    versions            = [ordered]@{ ollama = $ollamaVersion; git = $gitVersion; winget = $wingetVersion; powershell = $PSVersionTable.PSVersion.ToString() }
    ollama_state        = [ordered]@{
        port_11434 = $port11434; processes = $ollamaProcs
        env_models_user = $ollamaUserEnv; env_models_machine = $ollamaMachineEnv
        user_models_dir_exists = $ollamaHomeModels; localappdata_exists = $ollamaLocalAppData
    }
    disks               = $disks
    rocm                = [ordered]@{
        program_dirs = $rocmDirs; conda_packages = $condaRocm; hip_tools = $hipTools
    }
    env_vars            = $envVars
    installed_programs_filtered = $programs
}
$json = $report | ConvertTo-Json -Depth 6
$jsonPath = Join-Path $art 'environment-baseline.json'
$json | Set-Content -Path $jsonPath -Encoding UTF8
Write-Host "JSON written: $jsonPath"

# --- dxdiag for true GPU memory topology (dedicated vs shared) ---
$dxdiagXml = Join-Path $art 'dxdiag.xml'
try {
    Start-Process -FilePath 'dxdiag.exe' -ArgumentList "/whql:off /x `"$dxdiagXml`"" -Wait -NoNewWindow
    Write-Host "dxdiag written: $dxdiagXml"
} catch { Write-Host "dxdiag failed: $($_.Exception.Message)" }

Write-Host 'AUDIT DONE'
