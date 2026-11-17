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

if ($Remove) {
  foreach ($n in $names) {
    if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
      Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
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
  $ps = "& '$exe' $cmd *>> '$log'"
  $action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$ps`""
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
