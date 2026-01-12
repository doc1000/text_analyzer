# VaultBubble Beta Installer for Windows
# This script installs and configures VaultBubble for beta testing

param(
    [switch]$SkipDockerCheck,
    [switch]$SkipBuild,
    [switch]$NoShortcut,
    [switch]$NoStartup
)

$ErrorActionPreference = "Stop"
$InstallDir = $PSScriptRoot
$LogFile = Join-Path $InstallDir "install.log"

function Write-Log {
    param([string]$Message, [string]$Color = "White")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $Message"
    Add-Content -Path $LogFile -Value $logMessage
    Write-Host $logMessage -ForegroundColor $Color
}

function Test-DockerInstalled {
    Write-Log "Checking for Docker installation..." "Cyan"
    
    # Check if docker command exists
    try {
        $dockerVersion = docker --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Docker found: $dockerVersion" "Green"
            return $true
        }
    } catch {
        # Docker not in PATH
    }
    
    # Check common Docker Desktop installation paths
    $dockerPaths = @(
        "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
    )
    
    foreach ($path in $dockerPaths) {
        if (Test-Path $path) {
            Write-Log "Docker Desktop found at: $path" "Yellow"
            Write-Log "Please start Docker Desktop and run this script again." "Yellow"
            return $false
        }
    }
    
    return $false
}

function Test-DockerRunning {
    Write-Log "Checking if Docker daemon is running..." "Cyan"
    try {
        docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Docker daemon is running." "Green"
            return $true
        }
    } catch {
        Write-Log "Docker daemon is not running." "Red"
        return $false
    }
    return $false
}

function Show-DockerInstallInstructions {
    Write-Log "Docker is not installed or not running." "Red"
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host "Docker Installation Required" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Please install Docker Desktop for Windows:" -ForegroundColor White
    Write-Host "1. Download from: https://www.docker.com/products/docker-desktop" -ForegroundColor Cyan
    Write-Host "2. Run the installer" -ForegroundColor White
    Write-Host "3. Restart your computer if prompted" -ForegroundColor White
    Write-Host "4. Start Docker Desktop" -ForegroundColor White
    Write-Host "5. Run this installer again" -ForegroundColor White
    Write-Host ""
    Write-Host "For WSL2 users, you can also install Docker Engine in WSL2." -ForegroundColor Gray
    Write-Host ""
}

function New-EnvFile {
    Write-Log "Creating .env file from template..." "Cyan"
    $envExample = Join-Path $InstallDir "env.example"
    $envFile = Join-Path $InstallDir ".env"
    
    if (Test-Path $envFile) {
        Write-Log ".env file already exists, skipping creation." "Yellow"
        return
    }
    
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Log ".env file created from template." "Green"
    } else {
        # Create default .env file
        @"
# VaultBubble Beta - Environment Configuration
DATABASE_URL=postgresql+psycopg2://badger:badgerpass@db:5432/badgerdb
MODEL_PROVIDER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_CHAT_MODEL=gemma3:1b-it-q4_K_M
OLLAMA_EMBED_MODEL=all-minilm
"@ | Out-File -FilePath $envFile -Encoding utf8
        Write-Log ".env file created with defaults." "Green"
    }
}

function Build-DockerContainer {
    Write-Log "Building Docker containers..." "Cyan"
    Push-Location $InstallDir
    
    try {
        docker compose -f docker-compose.beta.yml build
        if ($LASTEXITCODE -ne 0) {
            throw "Docker build failed"
        }
        Write-Log "Docker containers built successfully." "Green"
    } catch {
        Write-Log "Error building Docker containers: $_" "Red"
        throw
    } finally {
        Pop-Location
    }
}

function Extract-Extension {
    Write-Log "Extracting Chrome extension from container..." "Cyan"
    
    # Start containers temporarily to extract extension
    Push-Location $InstallDir
    try {
        docker compose -f docker-compose.beta.yml up -d backend
        Start-Sleep -Seconds 5
        
        # Wait for container to be ready
        $maxAttempts = 30
        $attempt = 0
        $containerReady = $false
        
        while ($attempt -lt $maxAttempts) {
            $containerName = docker ps --format "{{.Names}}" | Select-String -Pattern "backend"
            if ($containerName) {
                $containerReady = $true
                break
            }
            Start-Sleep -Seconds 1
            $attempt++
        }
        
        if ($containerReady) {
            & "$InstallDir\extract-extension.ps1"
            Write-Log "Extension extracted successfully." "Green"
        } else {
            Write-Log "Warning: Could not extract extension. Container may not be ready." "Yellow"
            Write-Log "You can extract it later by running: .\extract-extension.ps1" "Yellow"
        }
    } catch {
        Write-Log "Warning: Extension extraction failed: $_" "Yellow"
        Write-Log "You can extract it later by running: .\extract-extension.ps1" "Yellow"
    } finally {
        Pop-Location
    }
}

function New-DesktopShortcut {
    Write-Log "Creating desktop shortcut..." "Cyan"
    try {
        & "$InstallDir\create-shortcut.ps1" -InstallDir $InstallDir
        Write-Log "Desktop shortcut created successfully." "Green"
    } catch {
        Write-Log "Warning: Failed to create desktop shortcut: $_" "Yellow"
    }
}

function Set-StartupConfiguration {
    Write-Log "Configuring startup options..." "Cyan"
    
    $response = Read-Host "Would you like VaultBubble to start automatically when you log in? (Y/N)"
    if ($response -match "^[Yy]") {
        try {
            # Create a scheduled task for startup
            $taskName = "VaultBubble-Startup"
            $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" `
                -Argument "-NoProfile -WindowStyle Hidden -Command `"cd '$InstallDir'; docker compose -f docker-compose.beta.yml up -d`""
            $taskTrigger = New-ScheduledTaskTrigger -AtLogOn
            $taskPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
            $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            
            Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger `
                -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
            
            Write-Log "Startup configuration created successfully." "Green"
            Write-Log "To disable: Open Task Scheduler and delete the 'VaultBubble-Startup' task." "Gray"
        } catch {
            Write-Log "Warning: Failed to configure startup: $_" "Yellow"
            Write-Log "You can manually configure startup if needed." "Yellow"
        }
    } else {
        Write-Log "Startup configuration skipped." "Gray"
    }
}

function Test-PortAvailable {
    param([int]$Port)
    $connection = Test-NetConnection -ComputerName localhost -Port $Port -WarningAction SilentlyContinue
    return -not $connection.TcpTestSucceeded
}

function Find-AvailablePort {
    param([int]$StartPort)
    $port = $StartPort
    $maxPort = $StartPort + 10
    
    while ($port -le $maxPort) {
        if (Test-PortAvailable -Port $port) {
            return $port
        }
        $port++
    }
    
    # If no port found, return original
    return $StartPort
}

function Update-DockerComposePort {
    param([int]$OldPort, [int]$NewPort)
    $composeFile = Join-Path $InstallDir "docker-compose.beta.yml"
    
    if (Test-Path $composeFile) {
        $content = Get-Content $composeFile -Raw
        $content = $content -replace "`"$OldPort`:5432`"", "`"$NewPort`:5432`""
        Set-Content -Path $composeFile -Value $content -NoNewline
        Write-Log "Updated docker-compose.beta.yml to use port $NewPort for database" "Green"
    }
}

# Main installation process
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "VaultBubble Beta Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Log "Starting installation in: $InstallDir" "Cyan"

# Check Docker
if (-not $SkipDockerCheck) {
    if (-not (Test-DockerInstalled)) {
        Show-DockerInstallInstructions
        exit 1
    }
    
    if (-not (Test-DockerRunning)) {
        Write-Log "Docker is installed but not running. Please start Docker Desktop." "Red"
        exit 1
    }
}

# Check port availability
$dbPort = 5433
if (-not (Test-PortAvailable -Port 8000)) {
    Write-Log "Warning: Port 8000 is already in use. VaultBubble may not start correctly." "Yellow"
    $continue = Read-Host "Continue anyway? (Y/N)"
    if ($continue -notmatch "^[Yy]") {
        exit 1
    }
}

# Check database port and find alternative if needed
if (-not (Test-PortAvailable -Port $dbPort)) {
    Write-Log "Port $dbPort is in use. Searching for alternative port..." "Yellow"
    $dbPort = Find-AvailablePort -StartPort $dbPort
    if ($dbPort -ne 5433) {
        Write-Log "Using port $dbPort for database instead of 5433" "Green"
        Update-DockerComposePort -OldPort 5433 -NewPort $dbPort
    } else {
        Write-Log "Warning: Could not find available port. Using 5433 anyway." "Yellow"
    }
}

# Create .env file
New-EnvFile

# Build containers
if (-not $SkipBuild) {
    Build-DockerContainer
} else {
    Write-Log "Skipping Docker build (--SkipBuild specified)" "Yellow"
}

# Extract extension
Extract-Extension

# Create desktop shortcut
if (-not $NoShortcut) {
    New-DesktopShortcut
} else {
    Write-Log "Skipping desktop shortcut creation (--NoShortcut specified)" "Yellow"
}

# Configure startup
if (-not $NoStartup) {
    Set-StartupConfiguration
} else {
    Write-Log "Skipping startup configuration (--NoStartup specified)" "Yellow"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Installation Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Log "Installation completed successfully." "Green"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Load the Chrome extension from: .\extension-extracted\extension" -ForegroundColor White
Write-Host "   Or use the zip file: .\extension\vaultbubble-extension.zip" -ForegroundColor White
Write-Host "2. Start VaultBubble by double-clicking the desktop shortcut" -ForegroundColor White
Write-Host "   Or run: docker compose -f docker-compose.beta.yml up" -ForegroundColor White
Write-Host "3. Access the application at: http://localhost:8000" -ForegroundColor White
Write-Host ""
Write-Host "For help, see README_BETA.md" -ForegroundColor Gray
Write-Host ""
