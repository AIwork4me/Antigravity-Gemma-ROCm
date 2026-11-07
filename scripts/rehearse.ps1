# rehearse.ps1 — DevFest rehearsal: N timed end-to-end rounds.
# Round = preflight -> init test repo (restore bug baseline) -> agent fix ->
#          pytest verify -> git diff -> restore workspace. Budget: 120s/round.
param(
    [int]$Rounds = 3,
    [int]$BudgetSec = 120,
    [string]$Model = 'gemma4:26b'
)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = "$root\.venv\Scripts\python.exe"
$out = "$root\artifacts\rehearsal"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$log = "$out\rehearsal-$(Get-Date -Format HHmmss).log"
$results = @()

function Log($m) {
    $line = "[{0:HH:mm:ss.fff}] {1}" -f (Get-Date), $m
    $line | Tee-Object -FilePath $log -Append
}

Log "REHEARSAL start (rounds=$Rounds budget=${BudgetSec}s)"

# warm the model BEFORE timing (keep-alive check)
Log "warming model (load + short gen)..."
$t0 = Get-Date
$null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/v1/chat/completions' -Method Post -TimeoutSec 300 -ContentType 'application/json' -Body (@{ model = $Model; stream = $false; messages = @(@{ role = 'user'; content = 'Reply with exactly: WARM' }) } | ConvertTo-Json -Depth 5)
Log ("warm done in {0:N1}s" -f ((Get-Date) - $t0).TotalSeconds)

for ($i = 1; $i -le $Rounds; $i++) {
    Log "=== ROUND $i ==="
    $roundT0 = Get-Date

    # 1. preflight
    $t = Get-Date
    & "$PSScriptRoot\preflight.ps1" *> "$out\round$i-preflight.log"
    $preflightOk = ($LASTEXITCODE -eq 0)
    Log ("  preflight: {0} ({1:N1}s)" -f ($(if ($preflightOk) { 'PASS' } else { 'FAIL' })), ((Get-Date) - $t).TotalSeconds)

    # 2. init test repo = restore frozen bug baseline + git-clean workspace copy
    $t = Get-Date
    & $py -I -c "import sys; sys.path.insert(0, r'$root\src'); from demo_agent import restore_bug_baseline; restore_bug_baseline(); print('baseline restored')"
    git -C $root checkout -- demo_workspace 2>$null
    Log ("  init test repo ({0:N1}s)" -f ((Get-Date) - $t).TotalSeconds)

    # 3. agent fix + 4. independent pytest (demo_agent does both)
    $t = Get-Date
    & $py -I src\demo_agent.py --run-id "rehearse-$i" --timeout 105 *> "$out\round$i-agent.log"
    $agentOk = ($LASTEXITCODE -eq 0)
    Log ("  agent+pytest: {0} ({1:N1}s)" -f ($(if ($agentOk) { 'GREEN' } else { 'RED' })), ((Get-Date) - $t).TotalSeconds)

    # 5. diff vs frozen pristine (NOT git HEAD: HEAD contains a historical fix,
    #    which made rounds show 0 lines; pristine shows what the agent changed)
    $t = Get-Date
    $diff = git diff --no-index -- "$root\demo_workspace_pristine" "$root\demo_workspace"
    $diffLines = ($diff | Measure-Object -Line).Lines
    $diff | Set-Content "$out\round$i-diff.patch" -Encoding UTF8
    Log ("  diff vs pristine: {0} lines ({1:N1}s)" -f $diffLines, ((Get-Date) - $t).TotalSeconds)

    # 6. restore workspace for next round (already done via git checkout in step 2 of next round)
    $t = Get-Date
    git -C $root checkout -- demo_workspace 2>$null
    Log ("  restore workspace ({0:N1}s)" -f ((Get-Date) - $t).TotalSeconds)

    $roundSec = [math]::Round(((Get-Date) - $roundT0).TotalSeconds, 1)
    $pass = $preflightOk -and $agentOk -and ($roundSec -le $BudgetSec)
    Log ("  ROUND $i RESULT: {0} total={1:N1}s (budget {2}s)" -f ($(if ($pass) { 'PASS' } else { 'FAIL' })), $roundSec, $BudgetSec)
    $results += [pscustomobject]@{ round = $i; preflight = $preflightOk; agent_green = $agentOk;
                                  round_sec = $roundSec; within_budget = ($roundSec -le $BudgetSec); pass = $pass }
}

# keep-alive behavior note: default OLLAMA_KEEP_ALIVE=5m — model stays resident between rounds
$results | ConvertTo-Json | Set-Content "$out\rounds.json" -Encoding UTF8
$nPass = @($results | Where-Object pass).Count
Log "REHEARSAL RESULT: $nPass/$Rounds rounds PASS (<= ${BudgetSec}s, preflight+agent green)"
exit ($(if ($nPass -eq $Rounds) { 0 } else { 1 }))
