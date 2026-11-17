# Keep a command running: start it, append its output to a log (plain UTF-8 via cmd), and restart it
# 5 s after it exits for any reason (crash, Ctrl+C broadcast, Postgres not up yet at sign-in).
# Ctrl+C does not stop this loop; stop the scheduled task (or kill this process tree) instead.
param([Parameter(Mandatory)][string]$Command, [Parameter(Mandatory)][string]$Log)
try { [Console]::TreatControlCAsInput = $true } catch {}
New-Item -ItemType Directory -Force (Split-Path $Log -Parent) | Out-Null
while ($true) {
  Add-Content -Path $Log -Encoding UTF8 -Value ("[{0}] supervise: starting {1}" -f (Get-Date -Format s), $Command)
  # The command line goes into a small .cmd file, so cmd's quote handling stays simple.
  $runner = "$Log.run.cmd"
  Set-Content -Path $runner -Encoding ASCII -Value "@$Command >> `"$Log`" 2>&1"
  & cmd.exe /d /c $runner
  Add-Content -Path $Log -Encoding UTF8 -Value ("[{0}] supervise: exited with {1}; restarting in 5 s" -f (Get-Date -Format s), $LASTEXITCODE)
  Start-Sleep -Seconds 5
}
