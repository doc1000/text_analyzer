# Register (or remove) two per-user scheduled tasks that start vb-orchard at sign-in:
#   "vb-orchard sync"  - the Postgres -> orchard-db sync service (the only writer)
#   "vb-orchard serve" - viewer + /do API on 127.0.0.1:$VB_ORCHARD_PORT (default 8766)
# Both restart every minute on failure and log to ~/.vb-orchard/logs. Settings come from
# ~/.vb-orchard/config.env. Install the command first:  uv tool install --force services/vb-orchard
#
#   powershell -File install-windows-tasks.ps1            # install / update and start now
#   powershell -File install-windows-tasks.ps1 -Remove    # stop and remove both tasks
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$names = @("vb-orchard sync", "vb-orchard serve")

function Stop-TaskTree([string]$taskName, [string]$match) {
  # Stop-ScheduledTask ends only the task's own process; kill the whole tree it started too.
  if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  }
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($match) -and $_.Name -in @("powershell.exe", "cmd.exe") -and $_.ProcessId -ne $PID } |
    ForEach-Object { & taskkill.exe /T /F /PID $_.ProcessId 2>$null | Out-Null }
  Start-Sleep -Milliseconds 500
}

if ($Remove) {
  foreach ($n in $names) {
    if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
      Stop-TaskTree $n ("logs\" + $n.Split(" ")[1] + ".log")
      Stop-TaskTree $n ("run-" + $n.Split(" ")[1] + ".cmd")
      Unregister-ScheduledTask -TaskName $n -Confirm:$false
      Write-Host "removed $n"
    }
  }
  return
}

$exe = (Get-Command vb-orchard -ErrorAction Stop).Source
$logs = Join-Path $HOME ".vb-orchard\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$user = "$env:USERDOMAIN\$env:USERNAME"

foreach ($cmd in @("sync", "serve")) {
  $log = Join-Path $logs "$cmd.log"
  # supervise.ps1 restarts the command whenever it exits and logs in plain UTF-8.
  $sup = Join-Path $PSScriptRoot "supervise.ps1"
  Stop-TaskTree "vb-orchard $cmd" "logs\$cmd.log"
  Stop-TaskTree "vb-orchard $cmd" "run-$cmd.cmd"
  $action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$sup`" -Command `"\`"$exe\`" $cmd`" -Log `"$log`""
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName "vb-orchard $cmd" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "vb-orchard $cmd (Vault Bubbles x Data Orchard)" -Force | Out-Null
  Start-ScheduledTask -TaskName "vb-orchard $cmd"
  Write-Host "registered and started 'vb-orchard $cmd' (log $log)"
}
