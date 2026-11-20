# Register (or remove) the per-user scheduled tasks that run the Vault Bubbles app at sign-in,
# restarting every minute on failure:
#   "vaultbubbles app"     run.ps1          web app on port 8000   log ~/.vaultbubbles/logs/app.log
#   "vaultbubbles worker"  run.ps1 -Worker  ingest queue, dedupe, vault/todo sources (batch33)
#                                           log ~/.vaultbubbles/logs/worker.log
# With the worker task registered, run.ps1 starts the web app with VB_BACKGROUND=worker, so the
# embedding work runs only in the worker. -NoWorker registers the app alone (jobs stay in-process).
#   powershell -File install-windows-task.ps1          # install / update and start now
#   powershell -File install-windows-task.ps1 -Remove
param([switch]$Remove, [switch]$NoWorker)
$ErrorActionPreference = "Stop"
$appName = "vaultbubbles app"
$workerName = "vaultbubbles worker"
$logs = Join-Path $HOME ".vaultbubbles\logs"

function Stop-TaskTree([string]$taskName, [string]$match) {
  # Stop-ScheduledTask ends only the task's own process; kill the whole tree it started too.
  if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  }
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($match) -and $_.Name -in @("powershell.exe", "cmd.exe") -and $_.ProcessId -ne $PID } |
    ForEach-Object { try { & cmd.exe /d /c "taskkill /T /F /PID $($_.ProcessId) >nul 2>&1" } catch {} }
}
function Remove-VbTask([string]$name, [string[]]$patterns) {
  foreach ($m in $patterns) { Stop-TaskTree $name $m }
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "removed $name"
  }
}
$appPatterns = @("logs\app.log", "run-app.cmd")
$workerPatterns = @("logs\worker.log")
if ($Remove) {
  Remove-VbTask $workerName $workerPatterns
  Remove-VbTask $appName $appPatterns
  return
}

function Register-VbTask([string]$name, [string]$runArgs, [string]$logName, [string]$desc, [string[]]$patterns) {
  $run = Join-Path $PSScriptRoot "run.ps1"
  $log = Join-Path $logs $logName
  $user = "$env:USERDOMAIN\$env:USERNAME"
  # supervise.ps1 restarts run.ps1 whenever it exits and logs in plain UTF-8.
  $sup = Join-Path $PSScriptRoot "supervise.ps1"
  foreach ($m in $patterns) { Stop-TaskTree $name $m }
  # conhost --headless: no console window at all. A plain "powershell -WindowStyle Hidden" still
  # pops up a window when Windows Terminal is the default terminal (Windows 11).
  $action = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$sup`" -Command `"powershell -NoProfile -ExecutionPolicy Bypass -File \`"$run\`"$runArgs`" -Log `"$log`""
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description $desc -Force | Out-Null
  Start-ScheduledTask -TaskName $name
  Write-Host "registered and started '$name' (log $log)"
}

New-Item -ItemType Directory -Force $logs | Out-Null
if ($NoWorker) {
  Remove-VbTask $workerName $workerPatterns
} else {
  # the worker task first: run.ps1 checks for it when the app starts
  Register-VbTask $workerName " -Worker" "worker.log" "Vault Bubbles worker (ingest queue, vault and todo sources)" $workerPatterns
}
Register-VbTask $appName "" "app.log" "Vault Bubbles app (native, port 8000)" $appPatterns
