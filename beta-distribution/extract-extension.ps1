# Extract Chrome Extension from Docker Container
# This script copies the extension from the container to the host filesystem

param(
    [string]$ContainerName = "",
    [string]$OutputDir = ".\extension-extracted"
)

Write-Host "Extracting Chrome extension from container..." -ForegroundColor Cyan

# Auto-detect container name if not provided
if ([string]::IsNullOrEmpty($ContainerName)) {
    $containers = docker ps -a --format "{{.Names}}" | Select-String -Pattern "backend"
    if ($containers) {
        $ContainerName = $containers[0].ToString().Trim()
        Write-Host "Auto-detected container: $ContainerName" -ForegroundColor Gray
    } else {
        Write-Host "Error: No backend container found." -ForegroundColor Red
        Write-Host "Please ensure the container is built and running." -ForegroundColor Yellow
        Write-Host "Try running: docker compose -f docker-compose.beta.yml up -d" -ForegroundColor Yellow
        exit 1
    }
}

# Check if container exists
$containerExists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^$ContainerName$"
if (-not $containerExists) {
    Write-Host "Error: Container '$ContainerName' not found." -ForegroundColor Red
    Write-Host "Please ensure the container is built and running." -ForegroundColor Yellow
    Write-Host "Try running: docker compose -f docker-compose.beta.yml up -d" -ForegroundColor Yellow
    exit 1
}

# Create output directory
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

# Copy extension from container
Write-Host "Copying extension files from container..." -ForegroundColor Cyan
docker cp "${ContainerName}:/code/extension" $OutputDir

if ($LASTEXITCODE -eq 0) {
    Write-Host "Extension extracted successfully to: $OutputDir\extension" -ForegroundColor Green
    
    # Create zip file
    $extensionDir = ".\extension"
    if (-not (Test-Path $extensionDir)) {
        New-Item -ItemType Directory -Path $extensionDir -Force | Out-Null
    }
    $zipPath = ".\extension\vaultbubble-extension.zip"
    Write-Host "Creating zip file: $zipPath" -ForegroundColor Cyan
    
    if (Test-Path $zipPath) {
        Remove-Item $zipPath -Force
    }
    
    # Create zip using .NET compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory("$OutputDir\extension", $zipPath)
    
    Write-Host "Extension packaged as: $zipPath" -ForegroundColor Green
    Write-Host ""
    Write-Host "To load the extension in Chrome:" -ForegroundColor Yellow
    Write-Host "1. Open Chrome and go to chrome://extensions/" -ForegroundColor White
    Write-Host "2. Enable 'Developer mode' (toggle in top right)" -ForegroundColor White
    Write-Host "3. Click 'Load unpacked' and select: $OutputDir\extension" -ForegroundColor White
    Write-Host "   OR click 'Load unpacked' and select the zip file: $zipPath" -ForegroundColor White
} else {
    Write-Host "Error: Failed to extract extension from container." -ForegroundColor Red
    exit 1
}
