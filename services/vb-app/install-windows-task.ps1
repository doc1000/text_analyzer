# Register (or remove) the per-user scheduled task "vaultbubbles app" that runs run.ps1 at sign-in,
# restarting every minute on failure. Log: ~/.vaultbubbles/logs/app.log
#   powershell -File install-windows-task.ps1          # install / update and start now
#   powershell -File install-windows-task.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$name = "vaultbubbles app"

function Stop-TaskTree([string]$taskName, [string]$match) {
  # Stop-ScheduledTask ends only the task's own process; kill the whole tree it started too.
  if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  }
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($match) -and $_.Name -eq "powershell.exe" } |
    ForEach-Object { & taskkill.exe /T /F /PID $_.ProcessId 2>$null | Out-Null }
}
if ($Remove) {
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Stop-TaskTree $name "run-app.cmd"
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "removed $name"
  }
  return
}
$run = Join-Path $PSScriptRoot "run.ps1"
$logs = Join-Path $HOME ".vaultbubbles\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs "app.log"
$user = "$env:USERDOMAIN\$env:USERNAME"
# A .cmd wrapper does the redirect, so the log is plain UTF-8 (PowerShell 5's *>> writes UTF-16).
$wrapper = Join-Path (Join-Path $HOME ".vaultbubbles") "run-app.cmd"
Stop-TaskTree $name "run-app.cmd"
Set-Content -Path $wrapper -Encoding ASCII -Value "@powershell -NoProfile -ExecutionPolicy Bypass -File `"$run`" >> `"$log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"& '$wrapper'`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
  -Principal $principal -Description "Vault Bubbles app (native, port 8000)" -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "registered and started '$name' (log $log)"
