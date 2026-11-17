# Run the Vault Bubbles app natively (no Docker) on http://127.0.0.1:8000.
# Settings: ~/.vaultbubbles/config.env (KEY=VALUE; or $env:VB_APP_CONFIG). Real environment wins.
# Needs: uv, local badgerdb (docker compose up -d db), `uv sync` once in the repo root.
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$cfg = if ($env:VB_APP_CONFIG) { $env:VB_APP_CONFIG } else { Join-Path $HOME ".vaultbubbles\config.env" }
if (Test-Path $cfg) {
  foreach ($line in Get-Content $cfg) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith("#") -or -not $t.Contains("=")) { continue }
    $k, $v = $t.Split("=", 2)
    $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
    if (-not [Environment]::GetEnvironmentVariable($k)) { [Environment]::SetEnvironmentVariable($k, $v) }
  }
}
$env:HF_HUB_OFFLINE = "1"; $env:TRANSFORMERS_OFFLINE = "1"; $env:PYTHONUNBUFFERED = "1"
Set-Location $repo
& uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port $Port
