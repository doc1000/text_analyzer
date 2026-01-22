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
   
   **Ubuntu 20.04+ / Debian 11+** (newer systems):
   ```bash
   sudo apt-get update
   sudo apt-get install docker.io docker-compose-plugin
   sudo systemctl start docker
   sudo systemctl enable docker
   ```
   
   **Ubuntu 18.04 (Bionic)** - use Docker's official repository:
   ```bash
   # Remove old distro docker if installed
   sudo apt-get remove -y docker.io docker-doc docker-compose
   
   # Install prerequisites
   sudo apt-get update
   sudo apt-get install -y ca-certificates curl gnupg
   
   # Add Docker GPG key
   sudo install -m 0755 -d /etc/apt/keyrings
   curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
     | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
   sudo chmod a+r /etc/apt/keyrings/docker.gpg
   
   # Add Docker repository for bionic
   echo \
     "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu bionic stable" \
     | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
   
   # Install Docker engine + plugins
   sudo apt-get update
   sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   
   # Verify installation
   docker --version
   docker compose version
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

**Problem**: "Unable to locate package docker-compose-plugin" on Ubuntu 18.04
- **Solution**: The default Ubuntu 18.04 repositories don't include docker-compose-plugin. Use Docker's official repository instead - see the "Ubuntu 18.04 (Bionic)" instructions in the Linux Installation section above.

**Problem**: "Permission denied" on Linux
- **Solution**: Add your user to the docker group:
  ```bash
  sudo usermod -aG docker $USER
  ```
  Then log out and log back in.

**Problem**: "/bin/bash^M: bad interpreter" when running install.sh
- **Solution**: The script has Windows line endings. Fix with:
  ```bash
  sed -i 's/\r$//' install.sh
  ```

**Problem**: Port 8000 already in use
- **Solution**: Stop the service using port 8000, or modify `docker-compose.beta.yml` to use a different port

**Problem**: "bind: address already in use" for port 5433 (database port)
- **Quick fix** (most common - stuck container):
  ```bash
  # Stop and remove all containers
  docker compose -f docker-compose.beta.yml down
  
  # If that doesn't work, force remove the stuck container
  docker rm -f beta-distribution-db-1
  
  # Then start fresh
  docker compose -f docker-compose.beta.yml up -d db
  ```
- **If port is used by another service**:
  1. **Find what's using the port**:
     ```bash
     # Linux:
     sudo lsof -i :5433
     # Or:
     sudo netstat -tulpn | grep 5433
     ```
  2. **Stop the conflicting service**:
     - If it's another Docker container: `docker ps` to find it, then `docker stop <container-id>`
     - If it's a local PostgreSQL: `sudo systemctl stop postgresql` (or check with `sudo systemctl status postgresql`)
  3. **Or change the port** in `docker-compose.beta.yml`:
     ```yaml
     ports:
       - "5434:5432"  # Change 5433 to 5434 or another free port
     ```
     Then update `DATABASE_URL` in `.env` if you changed the port.

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

**Problem**: Database container hangs during startup
- **Debug steps** (run these to see what's happening):
  1. **Check container status and health**:
     ```bash
     docker compose -f docker-compose.beta.yml ps
     # Look for "health: starting" or "health: unhealthy"
     ```
  2. **View database logs** (this should show what's happening):
     ```bash
     docker compose -f docker-compose.beta.yml logs db
     # Or follow in real-time:
     docker compose -f docker-compose.beta.yml logs -f db
     ```
  3. **Check if container is actually running**:
     ```bash
     docker ps | grep db
     # Should show container status
     ```
  4. **Check database process inside container**:
     ```bash
     docker compose -f docker-compose.beta.yml exec db pg_isready -U badger
     # Should return "badgerdb:5432 - accepting connections"
     ```
  5. **Check healthcheck status**:
     ```bash
     docker inspect beta-distribution-db-1 | grep -A 15 Health
     # Shows healthcheck history and current status
     ```
  6. **Try connecting to database manually**:
     ```bash
     docker compose -f docker-compose.beta.yml exec db psql -U badger -d badgerdb -c "SELECT 1;"
     ```
  7. **Check if init.sql ran successfully**:
     ```bash
     docker compose -f docker-compose.beta.yml exec db psql -U badger -d badgerdb -c "\dx"
     # Should show "vector" extension if init.sql ran
     ```
- **Common causes and solutions**:
  - **If logs show nothing**: Container might be stuck. Try:
    ```bash
    docker compose -f docker-compose.beta.yml restart db
    docker compose -f docker-compose.beta.yml logs -f db
    ```
  - **If healthcheck keeps failing**: Database might need more time. The healthcheck now has a 30s start period, but you can temporarily remove it:
    ```bash
    # Edit docker-compose.beta.yml, comment out healthcheck section
    # Then restart: docker compose -f docker-compose.beta.yml up -d db
    ```
  - **If database volume is corrupted**: Remove and recreate:
    ```bash
    docker compose -f docker-compose.beta.yml down -v
    docker compose -f docker-compose.beta.yml up -d db
    # Wait 30-60 seconds, then check logs
    docker compose -f docker-compose.beta.yml logs db
    ```
  - **If backend is waiting indefinitely**: Start db separately first:
    ```bash
    docker compose -f docker-compose.beta.yml up -d db
    # Wait until healthy (check with: docker compose -f docker-compose.beta.yml ps)
    # Then start backend:
    docker compose -f docker-compose.beta.yml up backend
    ```

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

- **Beta Version**: 0.6.0
- **Last Updated**: 2026-01-12

## What's New in 0.6.0

### Major Features
- **Settings UI**: New settings modal to configure providers and API keys from the web interface
- **Multiple Provider Support**: Separate providers for chat, topic generation, and embeddings
- **Topic Management**: Documents can now be assigned to topics, with automatic topic persistence
- **Document Reader**: Click on a document twice to open it in a dedicated reader view
- **Improved Topic Titles**: Topics now use broader, higher-level titles (3-6 words) for better organization
- **Re-clustering**: Automatic re-clustering when topic count exceeds threshold

### Improvements
- Removed analyzer/scoring functionality for simplified experience
- Enhanced query results using sentence-level embeddings
- Resizable side panel in the topics view
- Settings are now persisted to disk between sessions
- Better deduplication of search results
