# Run the Vault Bubbles app natively (no Docker) on http://127.0.0.1:8000.
# Settings: ~/.vaultbubbles/config.env (KEY=VALUE; or $env:VB_APP_CONFIG). The file wins over the
# inherited environment (VB_APP_ENV_WINS=1 reverses that).
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
    # config.env wins over the inherited environment, so a stale Windows-level variable
    # (e.g. an old OPENAI_API_KEY) cannot shadow it; set VB_APP_ENV_WINS=1 to reverse that.
    if ($env:VB_APP_ENV_WINS -and [Environment]::GetEnvironmentVariable($k)) { continue }
    [Environment]::SetEnvironmentVariable($k, $v)
  }
}
$env:HF_HUB_OFFLINE = "1"; $env:TRANSFORMERS_OFFLINE = "1"; $env:PYTHONUNBUFFERED = "1"
# UTF-8 stdout: the app prints non-ASCII (e.g. a check mark) and cp1252 would raise mid-insert
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
Set-Location $repo
& uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port $Port
