# VaultBubble Installation Health Check
# Windows PowerShell script to verify installation

$InstallDir = $PSScriptRoot
$AllChecksPassed = $true

function Write-Check {
    param(
        [string]$CheckName,
        [bool]$Passed,
        [string]$Message = ""
    )
    
    if ($Passed) {
        Write-Host "[✓] $CheckName" -ForegroundColor Green
        if ($Message) {
            Write-Host "    $Message" -ForegroundColor Gray
        }
    } else {
        Write-Host "[✗] $CheckName" -ForegroundColor Red
        if ($Message) {
            Write-Host "    $Message" -ForegroundColor Yellow
        }
        $script:AllChecksPassed = $false
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "VaultBubble Installation Health Check" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check Docker installation
Write-Host "Checking Docker..." -ForegroundColor Cyan
try {
    $dockerVersion = docker --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Check "Docker Installed" $true $dockerVersion
    } else {
        Write-Check "Docker Installed" $false "Docker command failed"
    }
} catch {
    Write-Check "Docker Installed" $false "Docker not found in PATH"
}

# Check Docker daemon
try {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Check "Docker Daemon Running" $true
    } else {
        Write-Check "Docker Daemon Running" $false "Start Docker Desktop"
    }
} catch {
    Write-Check "Docker Daemon Running" $false "Cannot connect to Docker daemon"
}

# Check docker-compose
try {
    $composeVersion = docker compose version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Check "Docker Compose Available" $true $composeVersion
    } else {
        Write-Check "Docker Compose Available" $false "docker compose command not available"
    }
} catch {
    Write-Check "Docker Compose Available" $false "docker compose not found"
}

Write-Host ""
Write-Host "Checking Installation Files..." -ForegroundColor Cyan

# Check required files
$requiredFiles = @(
    "docker-compose.beta.yml",
    "Dockerfile.beta",
    ".env",
    "app\main.py"
)

foreach ($file in $requiredFiles) {
    $filePath = Join-Path $InstallDir $file
    $exists = Test-Path $filePath
    Write-Check "File: $file" $exists
}

Write-Host ""
Write-Host "Checking Docker Containers..." -ForegroundColor Cyan

# Check if containers are built
try {
    $images = docker images --format "{{.Repository}}:{{.Tag}}" | Select-String -Pattern "text_analyzer"
    if ($images) {
        Write-Check "Docker Images Built" $true "Found: $($images -join ', ')"
    } else {
        Write-Check "Docker Images Built" $false "Run: docker compose -f docker-compose.beta.yml build"
    }
} catch {
    Write-Check "Docker Images Built" $false "Cannot check Docker images"
}

# Check if containers are running
try {
    $containers = docker ps --format "{{.Names}}" | Select-String -Pattern "backend|db|ollama"
    if ($containers) {
        Write-Check "Containers Running" $true "Running: $($containers -join ', ')"
    } else {
        Write-Check "Containers Running" $false "Start with: docker compose -f docker-compose.beta.yml up -d"
    }
} catch {
    Write-Check "Containers Running" $false "Cannot check containers"
}

Write-Host ""
Write-Host "Checking Application Health..." -ForegroundColor Cyan

# Check if application is responding
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/health" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Check "Application Health Endpoint" $true "Application is running and healthy"
    } else {
        Write-Check "Application Health Endpoint" $false "Health check returned status: $($response.StatusCode)"
    }
} catch {
    Write-Check "Application Health Endpoint" $false "Cannot reach http://localhost:8000/health - Is the application running?"
}

# Check port 8000
try {
    $connection = Test-NetConnection -ComputerName localhost -Port 8000 -WarningAction SilentlyContinue
    if ($connection.TcpTestSucceeded) {
        Write-Check "Port 8000 Accessible" $true
    } else {
        Write-Check "Port 8000 Accessible" $false "Port 8000 is not accessible"
    }
} catch {
    Write-Check "Port 8000 Accessible" $false "Cannot test port 8000"
}

Write-Host ""
Write-Host "Checking Extension..." -ForegroundColor Cyan

# Check extension files
$extensionPath = Join-Path $InstallDir "extension-extracted\extension"
$extensionZip = Join-Path $InstallDir "extension\vaultbubble-extension.zip"

if (Test-Path $extensionPath) {
    Write-Check "Extension Extracted" $true "Found at: $extensionPath"
} elseif (Test-Path $extensionZip) {
    Write-Check "Extension Extracted" $true "Zip file found: $extensionZip"
} else {
    Write-Check "Extension Extracted" $false "Run: .\extract-extension.ps1"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
if ($AllChecksPassed) {
    Write-Host "All Checks Passed!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Your VaultBubble installation looks good!" -ForegroundColor Green
    Write-Host "Access the application at: http://localhost:8000" -ForegroundColor Cyan
} else {
    Write-Host "Some Checks Failed" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Please review the failed checks above and:" -ForegroundColor Yellow
    Write-Host "1. Ensure Docker is installed and running" -ForegroundColor White
    Write-Host "2. Run: docker compose -f docker-compose.beta.yml build" -ForegroundColor White
    Write-Host "3. Run: docker compose -f docker-compose.beta.yml up -d" -ForegroundColor White
    Write-Host "4. Check README_BETA.md for troubleshooting" -ForegroundColor White
}
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
