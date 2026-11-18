# Redeploy the local Vault Bubbles services from this checkout:
# stop the three sign-in tasks, refresh the app environment (uv sync), reinstall the vb-orchard
# command (uv tool install), then re-register and start the tasks.
#   powershell -File services/update-windows.ps1
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$orchardTasks = Join-Path $PSScriptRoot "vb-orchard\scripts\install-windows-tasks.ps1"
$appTask = Join-Path $PSScriptRoot "vb-app\install-windows-task.ps1"
& $orchardTasks -Remove
& $appTask -Remove
Push-Location $repo
try {
  & uv sync
  if ($LASTEXITCODE) { throw "uv sync failed" }
  # --reinstall: rebuild even when the version number is unchanged (uv caches the built wheel)
  & uv tool install --force --reinstall (Join-Path $PSScriptRoot "vb-orchard")
  if ($LASTEXITCODE) { throw "uv tool install failed" }
} finally { Pop-Location }
& $orchardTasks
& $appTask
