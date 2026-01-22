# sync-beta.ps1
# Syncs current app changes to beta-distribution folder
# Usage: .\sync-beta.ps1 [-BumpVersion] [-VersionType patch|minor|major] [-HealthCheck] [-DryRun]

param(
    [switch]$BumpVersion,
    [ValidateSet("patch", "minor", "major")]
    [string]$VersionType = "patch",
    [switch]$HealthCheck,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# Paths
$ProjectRoot = $PSScriptRoot
$AppDir = Join-Path $ProjectRoot "app"
$BetaAppDir = Join-Path $ProjectRoot "beta-distribution\app"
$BetaManifest = Join-Path $ProjectRoot "beta-distribution\extension\manifest.json"
$BetaExtensionDir = Join-Path $ProjectRoot "beta-distribution\extension"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Beta Distribution Sync Tool" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Files to sync from app/ to beta-distribution/app/
$FilesToSync = @(
    "config.py",
    "db.py", 
    "helpers.py",
    "main.py",
    "mmr.py",
    "models.py",
    "schemas.py",
    "topics.py",
    "requirements.txt",
    "__init__.py"
)

# Static files to sync
$StaticFilesToSync = @(
    "static\index.html",
    "static\document.html"
)

Write-Host "[1/5] Checking file differences..." -ForegroundColor Yellow
Write-Host ""

$changesMade = @()
$filesIdentical = @()

foreach ($file in $FilesToSync) {
    $sourcePath = Join-Path $AppDir $file
    $destPath = Join-Path $BetaAppDir $file
    
    if (Test-Path $sourcePath) {
        if (Test-Path $destPath) {
            $sourceHash = (Get-FileHash $sourcePath -Algorithm SHA256).Hash
            $destHash = (Get-FileHash $destPath -Algorithm SHA256).Hash
            
            if ($sourceHash -ne $destHash) {
                $changesMade += $file
                Write-Host "  [CHANGED] $file" -ForegroundColor Yellow
            } else {
                $filesIdentical += $file
                Write-Host "  [OK] $file" -ForegroundColor Green
            }
        } else {
            $changesMade += $file
            Write-Host "  [NEW] $file" -ForegroundColor Magenta
        }
    } else {
        Write-Host "  [MISSING] $file (source not found)" -ForegroundColor Red
    }
}

# Check static files
foreach ($file in $StaticFilesToSync) {
    $sourcePath = Join-Path $AppDir $file
    $destPath = Join-Path $BetaAppDir $file
    
    if (Test-Path $sourcePath) {
        if (Test-Path $destPath) {
            $sourceHash = (Get-FileHash $sourcePath -Algorithm SHA256).Hash
            $destHash = (Get-FileHash $destPath -Algorithm SHA256).Hash
            
            if ($sourceHash -ne $destHash) {
                $changesMade += $file
                Write-Host "  [CHANGED] $file" -ForegroundColor Yellow
            } else {
                $filesIdentical += $file
                Write-Host "  [OK] $file" -ForegroundColor Green
            }
        } else {
            $changesMade += $file
            Write-Host "  [NEW] $file" -ForegroundColor Magenta
        }
    }
}

Write-Host ""
Write-Host "[2/5] Summary" -ForegroundColor Yellow
Write-Host "  Files identical: $($filesIdentical.Count)" -ForegroundColor Green
Write-Host "  Files to update: $($changesMade.Count)" -ForegroundColor Yellow

if ($changesMade.Count -eq 0) {
    Write-Host ""
    Write-Host "No changes to sync. Beta distribution is up to date!" -ForegroundColor Green
    
    if (-not $HealthCheck -and -not $BumpVersion) {
        exit 0
    }
}

# Sync files
if ($changesMade.Count -gt 0) {
    Write-Host ""
    Write-Host "[3/5] Syncing files..." -ForegroundColor Yellow
    
    if ($DryRun) {
        Write-Host "  [DRY RUN] Would sync the following files:" -ForegroundColor Cyan
        foreach ($file in $changesMade) {
            Write-Host "    - $file"
        }
    } else {
        foreach ($file in $changesMade) {
            $sourcePath = Join-Path $AppDir $file
            $destPath = Join-Path $BetaAppDir $file
            
            # Ensure destination directory exists
            $destDir = Split-Path $destPath -Parent
            if (-not (Test-Path $destDir)) {
                New-Item -ItemType Directory -Path $destDir -Force | Out-Null
            }
            
            Copy-Item $sourcePath $destPath -Force
            Write-Host "  Copied: $file" -ForegroundColor Green
        }
    }
} else {
    Write-Host ""
    Write-Host "[3/5] No files to sync" -ForegroundColor Green
}

# Version bump
if ($BumpVersion) {
    Write-Host ""
    Write-Host "[4/5] Updating version..." -ForegroundColor Yellow
    
    $manifest = Get-Content $BetaManifest -Raw | ConvertFrom-Json
    $currentVersion = $manifest.version
    $versionParts = $currentVersion.Split('.')
    
    $major = [int]$versionParts[0]
    $minor = [int]$versionParts[1]
    $patch = [int]$versionParts[2]
    
    switch ($VersionType) {
        "major" { $major++; $minor = 0; $patch = 0 }
        "minor" { $minor++; $patch = 0 }
        "patch" { $patch++ }
    }
    
    $newVersion = "$major.$minor.$patch"
    
    Write-Host "  Current version: $currentVersion" -ForegroundColor Cyan
    Write-Host "  New version: $newVersion" -ForegroundColor Green
    
    if (-not $DryRun) {
        $manifest.version = $newVersion
        $manifest | ConvertTo-Json -Depth 10 | Set-Content $BetaManifest -Encoding UTF8
        
        # Also update web-ext-artifacts zip name reference if needed
        $zipPath = Join-Path $BetaExtensionDir "web-ext-artifacts\vaultbubble-$currentVersion.zip"
        if (Test-Path $zipPath) {
            $newZipPath = Join-Path $BetaExtensionDir "web-ext-artifacts\vaultbubble-$newVersion.zip"
            Rename-Item $zipPath $newZipPath
            Write-Host "  Renamed artifact: vaultbubble-$newVersion.zip" -ForegroundColor Green
        }
        
        Write-Host "  Version updated!" -ForegroundColor Green
    } else {
        Write-Host "  [DRY RUN] Would update to version $newVersion" -ForegroundColor Cyan
    }
} else {
    Write-Host ""
    Write-Host "[4/5] Skipping version bump (use -BumpVersion to enable)" -ForegroundColor Gray
}

# Health check
if ($HealthCheck) {
    Write-Host ""
    Write-Host "[5/5] Running health check..." -ForegroundColor Yellow
    
    $healthUrl = "http://localhost:8000/health"
    
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 5
        if ($response.status -eq "ok") {
            Write-Host "  Backend health check: PASSED" -ForegroundColor Green
        } else {
            Write-Host "  Backend health check: UNEXPECTED RESPONSE" -ForegroundColor Yellow
            Write-Host "  Response: $($response | ConvertTo-Json)"
        }
    } catch {
        Write-Host "  Backend health check: FAILED" -ForegroundColor Red
        Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "  Make sure the backend is running (docker compose up)" -ForegroundColor Yellow
    }
    
    # Test a quick API call
    try {
        $configUrl = "http://localhost:8000/model_configs"
        $configResponse = Invoke-RestMethod -Uri $configUrl -Method Get -TimeoutSec 5
        Write-Host "  Model config check: PASSED" -ForegroundColor Green
        Write-Host "    - Chat provider: $($configResponse.chat_provider)"
        Write-Host "    - Embedding model: $($configResponse.embedding_model)"
    } catch {
        Write-Host "  Model config check: FAILED" -ForegroundColor Red
    }
} else {
    Write-Host ""
    Write-Host "[5/5] Skipping health check (use -HealthCheck to enable)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Sync Complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Show what to do next
if ($changesMade.Count -gt 0 -and -not $DryRun) {
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Review changes in beta-distribution/app/" -ForegroundColor Gray
    Write-Host "  2. Test the beta distribution locally" -ForegroundColor Gray
    Write-Host "  3. Commit changes: git add . && git commit -m 'Sync beta distribution'" -ForegroundColor Gray
    if ($BumpVersion) {
        $manifest = Get-Content $BetaManifest -Raw | ConvertFrom-Json
        Write-Host "  4. Update beta-distribution.zip for release (version $($manifest.version))" -ForegroundColor Gray
    }
}
