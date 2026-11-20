# Keep a command running: start it, append its output to a log (plain UTF-8 via cmd), and restart it
# after it exits for any reason (crash, Ctrl+C broadcast, Postgres not up yet at sign-in).
# Backoff: the wait starts at 5 s and doubles after each quick failure (run shorter than 2 min),
# capped at 5 min, so a dependency that stays down (e.g. Docker/Postgres) is not hammered every 5 s.
# A run that lasted 2 min or more counts as healthy and resets the wait to 5 s.
# Ctrl+C does not stop this loop; stop the scheduled task (or kill this process tree) instead.
param([Parameter(Mandatory)][string]$Command, [Parameter(Mandatory)][string]$Log)
try { [Console]::TreatControlCAsInput = $true } catch {}
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"  # Python output to the log as UTF-8
New-Item -ItemType Directory -Force (Split-Path $Log -Parent) | Out-Null
$minDelay = 5; $maxDelay = 300; $healthyRun = 120
$delay = $minDelay
while ($true) {
  Add-Content -Path $Log -Encoding UTF8 -Value ("[{0}] supervise: starting {1}" -f (Get-Date -Format s), $Command)
  # The command line goes into a small .cmd file, so cmd's quote handling stays simple.
  $runner = "$Log.run.cmd"
  Set-Content -Path $runner -Encoding ASCII -Value "@$Command >> `"$Log`" 2>&1"
  $started = Get-Date
  & cmd.exe /d /c $runner
  $code = $LASTEXITCODE
  if (((Get-Date) - $started).TotalSeconds -ge $healthyRun) { $delay = $minDelay }
  Add-Content -Path $Log -Encoding UTF8 -Value ("[{0}] supervise: exited with {1}; restarting in {2} s" -f (Get-Date -Format s), $code, $delay)
  Start-Sleep -Seconds $delay
  $delay = [Math]::Min($delay * 2, $maxDelay)
}
