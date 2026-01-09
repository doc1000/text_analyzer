# VaultBubble Beta Testing Guide

Welcome to the VaultBubble beta! This guide will help you install and use VaultBubble on your system.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Installation](#installation)
  - [Windows](#windows-installation)
  - [Linux](#linux-installation)
  - [macOS](#macos-installation)
- [Loading the Chrome Extension](#loading-the-chrome-extension)
- [Using VaultBubble](#using-vaultbubble)
- [Troubleshooting](#troubleshooting)
- [Stopping and Starting](#stopping-and-starting)
- [Uninstalling](#uninstalling)
- [Feedback](#feedback)

## Prerequisites

Before installing VaultBubble, you need:

1. **Docker** - VaultBubble runs in Docker containers
   - **Windows**: [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop)
   - **Linux**: Docker Engine and Docker Compose plugin
   - **macOS**: [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop)

2. **Chrome Browser** - For the browser extension

3. **System Requirements**:
   - At least 4GB RAM (8GB recommended)
   - At least 5GB free disk space
   - Internet connection (for initial model downloads)

## Quick Start

1. **Install Docker** (if not already installed)
2. **Run the installer**:
   - Windows: Double-click `install.ps1` or run `powershell -ExecutionPolicy Bypass -File install.ps1`
   - Linux/macOS: Run `./install.sh` in terminal
3. **Load the Chrome extension** (see instructions below)
4. **Start VaultBubble** using the desktop shortcut or run `docker compose -f docker-compose.beta.yml up`
5. **Access the application** at http://localhost:8000

## Installation

### Windows Installation

1. **Install Docker Desktop** (if needed):
   - Download from: https://www.docker.com/products/docker-desktop
   - Run the installer and follow the prompts
   - Restart your computer if prompted
   - Start Docker Desktop

2. **Run the installer**:
   - Right-click `install.ps1` and select "Run with PowerShell"
   - Or open PowerShell in this folder and run:
     ```powershell
     powershell -ExecutionPolicy Bypass -File install.ps1
     ```

3. **Follow the prompts**:
   - The installer will check for Docker
   - Build the Docker containers (this may take several minutes)
   - Extract the Chrome extension
   - Create a desktop shortcut
   - Optionally configure startup on login

4. **Verify installation**:
   ```powershell
   .\check-installation.ps1
   ```

### Linux Installation

1. **Install Docker** (if needed):
   
   **Ubuntu/Debian**:
   ```bash
   sudo apt-get update
   sudo apt-get install docker.io docker-compose-plugin
   sudo systemctl start docker
   sudo systemctl enable docker
   ```
   
   **Fedora/RHEL**:
   ```bash
   sudo dnf install docker docker-compose-plugin
   sudo systemctl start docker
   sudo systemctl enable docker
   ```
   
   **Add your user to the docker group** (to run Docker without sudo):
   ```bash
   sudo usermod -aG docker $USER
   ```
   Then log out and log back in.

2. **Run the installer**:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

3. **Follow the prompts** (same as Windows)

4. **Verify installation**:
   ```bash
   ./check-installation.sh
   ```

### macOS Installation

1. **Install Docker Desktop** (if needed):
   - Download from: https://www.docker.com/products/docker-desktop
   - Open the .dmg file and drag Docker to Applications
   - Start Docker Desktop from Applications
   - Wait for Docker to finish starting (whale icon in menu bar)

2. **Run the installer**:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

3. **Follow the prompts** (same as Windows/Linux)

4. **Verify installation**:
   ```bash
   ./check-installation.sh
   ```

## Loading the Chrome Extension

After installation, you need to load the Chrome extension:

1. **Extract the extension** (if not done automatically):
   - Windows: Run `.\extract-extension.ps1`
   - Linux/macOS: Run `./extract-extension.sh`
   
   Or use the zip file: `extension/vaultbubble-extension.zip`

2. **Load in Chrome**:
   - Open Chrome and navigate to `chrome://extensions/`
   - Enable "Developer mode" (toggle in top right)
   - Click "Load unpacked"
   - Select the `extension-extracted/extension` folder
   - The VaultBubble extension should now appear in your extensions list

3. **Pin the extension** (optional):
   - Click the puzzle piece icon in Chrome toolbar
   - Find VaultBubble and click the pin icon

## Using VaultBubble

### Starting VaultBubble

**Option 1: Desktop Shortcut**
- Double-click the VaultBubble shortcut on your desktop

**Option 2: Command Line**
- Windows:
  ```powershell
  cd path\to\beta-distribution
  docker compose -f docker-compose.beta.yml up
  ```
- Linux/macOS:
  ```bash
  cd path/to/beta-distribution
  docker compose -f docker-compose.beta.yml up
  ```

**Option 3: Background Mode**
- Add `-d` flag to run in background:
  ```bash
  docker compose -f docker-compose.beta.yml up -d
  ```

### Accessing the Application

Once started, access VaultBubble at:
- **Web Interface**: http://localhost:8000
- **Health Check**: http://localhost:8000/health

### Using the Browser Extension

1. **Capture web content**:
   - Navigate to any webpage
   - Select text you want to save
   - Right-click and select "Save to VaultBubble" (or use the extension icon)
   - Or click the extension icon and use the popup interface

2. **View your vault**:
   - Open the VaultBubble web interface at http://localhost:8000
   - Browse your saved documents
   - Search and query your content

## Troubleshooting

### Docker Issues

**Problem**: "Docker is not installed" or "Docker daemon is not running"
- **Solution**: Install Docker Desktop and ensure it's running
- Check Docker status: `docker info` (should not show errors)

**Problem**: "Permission denied" on Linux
- **Solution**: Add your user to the docker group:
  ```bash
  sudo usermod -aG docker $USER
  ```
  Then log out and log back in.

**Problem**: Port 8000 already in use
- **Solution**: Stop the service using port 8000, or modify `docker-compose.beta.yml` to use a different port

### Container Issues

**Problem**: Containers won't start
- **Solution**: Check logs:
  ```bash
  docker compose -f docker-compose.beta.yml logs
  ```

**Problem**: "Image not found" or build errors
- **Solution**: Rebuild containers:
  ```bash
  docker compose -f docker-compose.beta.yml build --no-cache
  ```

**Problem**: Database connection errors
- **Solution**: Ensure all containers are running:
  ```bash
  docker compose -f docker-compose.beta.yml ps
  ```
  All services should show "Up"

### Extension Issues

**Problem**: Extension won't load
- **Solution**: 
  - Ensure VaultBubble backend is running (http://localhost:8000)
  - Check Chrome console for errors (F12 → Console)
  - Try reloading the extension

**Problem**: Extension can't connect to backend
- **Solution**: 
  - Verify backend is running: `docker compose -f docker-compose.beta.yml ps`
  - Check http://localhost:8000/health in your browser
  - Ensure no firewall is blocking port 8000

### Performance Issues

**Problem**: Slow startup or high CPU usage
- **Solution**: 
  - First startup downloads AI models (this is normal and takes time)
  - Ensure Docker has enough resources allocated (Docker Desktop → Settings → Resources)
  - Close other resource-intensive applications

**Problem**: Out of memory errors
- **Solution**: 
  - Increase Docker memory limit (Docker Desktop → Settings → Resources → Memory)
  - Recommended: At least 4GB RAM allocated to Docker

### General Issues

**Problem**: Installation script fails
- **Solution**: 
  - Check the `install.log` file for detailed error messages
  - Run health check: `.\check-installation.ps1` (Windows) or `./check-installation.sh` (Linux/macOS)
  - Ensure you have administrator/sudo privileges if needed

**Problem**: Can't access http://localhost:8000
- **Solution**:
  - Verify containers are running: `docker compose -f docker-compose.beta.yml ps`
  - Check if port 8000 is accessible: `curl http://localhost:8000/health` or open in browser
  - Check firewall settings

## Stopping and Starting

### Stop VaultBubble

**Stop containers** (keeps data):
```bash
docker compose -f docker-compose.beta.yml stop
```

**Stop and remove containers** (keeps data):
```bash
docker compose -f docker-compose.beta.yml down
```

**Stop and remove everything including data** (⚠️ deletes all your saved content):
```bash
docker compose -f docker-compose.beta.yml down -v
```

### Start VaultBubble

**Start in foreground** (see logs):
```bash
docker compose -f docker-compose.beta.yml up
```

**Start in background**:
```bash
docker compose -f docker-compose.beta.yml up -d
```

**View logs**:
```bash
docker compose -f docker-compose.beta.yml logs -f
```

## Uninstalling

### Remove VaultBubble

1. **Stop and remove containers**:
   ```bash
   docker compose -f docker-compose.beta.yml down -v
   ```

2. **Remove Docker images** (optional):
   ```bash
   docker images | grep text_analyzer
   docker rmi <image-id>
   ```

3. **Remove desktop shortcut**:
   - Windows: Delete the shortcut from Desktop
   - Linux: Delete `~/.local/share/applications/VaultBubble.desktop` or `~/Desktop/VaultBubble.desktop`
   - macOS: Delete `~/Desktop/VaultBubble.command`

4. **Remove startup configuration**:
   - Windows: Open Task Scheduler, delete "VaultBubble-Startup" task
   - Linux: Delete `~/.config/autostart/vaultbubble.desktop`
   - macOS: Run `launchctl unload ~/Library/LaunchAgents/com.vaultbubble.startup.plist`

5. **Remove Chrome extension**:
   - Go to `chrome://extensions/`
   - Find VaultBubble and click "Remove"

6. **Delete installation folder** (optional):
   - Delete the `beta-distribution` folder

**Note**: This will delete all your saved data. Export any important data first if needed.

## Feedback

We value your feedback! Please report issues, suggestions, or questions:

- **Bug Reports**: Include:
  - Your operating system and version
  - Docker version (`docker --version`)
  - Error messages from `install.log` or container logs
  - Steps to reproduce the issue

- **Feature Requests**: Let us know what features would be helpful

- **General Questions**: We're here to help!

Thank you for being a beta tester!

## Additional Resources

- **Health Check**: Run `.\check-installation.ps1` (Windows) or `./check-installation.sh` (Linux/macOS) to verify your installation
- **Docker Documentation**: https://docs.docker.com/
- **Docker Compose Documentation**: https://docs.docker.com/compose/

## Known Issues

- First startup may take 5-10 minutes while AI models download
- Some antivirus software may flag Docker containers (false positive)
- On Linux, you may need to run Docker commands with `sudo` if not added to docker group
- Port conflicts may occur if another service uses port 8000

## Version Information

- **Beta Version**: 0.5.1
- **Last Updated**: 2026-01-09
