#!/bin/bash
# VaultBubble Beta Installer for Linux/macOS
# This script installs and configures VaultBubble for beta testing

set -e

SKIP_DOCKER_CHECK=false
SKIP_BUILD=false
NO_SHORTCUT=false
NO_STARTUP=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-docker-check)
            SKIP_DOCKER_CHECK=true
            shift
            ;;
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --no-shortcut)
            NO_SHORTCUT=true
            shift
            ;;
        --no-startup)
            NO_STARTUP=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$INSTALL_DIR/install.log"

log() {
    local message="$1"
    local color="${2:-white}"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $message" >> "$LOG_FILE"
    
    case $color in
        red) echo -e "\033[0;31m[$timestamp] $message\033[0m" ;;
        green) echo -e "\033[0;32m[$timestamp] $message\033[0m" ;;
        yellow) echo -e "\033[0;33m[$timestamp] $message\033[0m" ;;
        cyan) echo -e "\033[0;36m[$timestamp] $message\033[0m" ;;
        *) echo "[$timestamp] $message" ;;
    esac
}

test_docker_installed() {
    log "Checking for Docker installation..." "cyan"
    
    if command -v docker &> /dev/null; then
        local docker_version=$(docker --version 2>&1)
        log "Docker found: $docker_version" "green"
        return 0
    fi
    
    # Check for Docker Desktop on macOS
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if [ -d "/Applications/Docker.app" ]; then
            log "Docker Desktop found but docker command not in PATH." "yellow"
            log "Please add Docker to your PATH or start Docker Desktop." "yellow"
            return 1
        fi
    fi
    
    return 1
}

test_docker_running() {
    log "Checking if Docker daemon is running..." "cyan"
    if docker info &> /dev/null; then
        log "Docker daemon is running." "green"
        return 0
    else
        log "Docker daemon is not running." "red"
        return 1
    fi
}

show_docker_install_instructions() {
    log "Docker is not installed or not running." "red"
    echo ""
    echo "========================================"
    echo "Docker Installation Required"
    echo "========================================"
    echo ""
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "Please install Docker Desktop for macOS:"
        echo "1. Download from: https://www.docker.com/products/docker-desktop"
        echo "2. Open the .dmg file and drag Docker to Applications"
        echo "3. Start Docker Desktop from Applications"
        echo "4. Run this installer again"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "Please install Docker for Linux:"
        echo ""
        echo "For Ubuntu/Debian:"
        echo "  sudo apt-get update"
        echo "  sudo apt-get install docker.io docker-compose-plugin"
        echo "  sudo systemctl start docker"
        echo "  sudo systemctl enable docker"
        echo ""
        echo "For Fedora/RHEL:"
        echo "  sudo dnf install docker docker-compose-plugin"
        echo "  sudo systemctl start docker"
        echo "  sudo systemctl enable docker"
        echo ""
        echo "For other distributions, see: https://docs.docker.com/engine/install/"
        echo ""
        echo "Note: You may need to add your user to the docker group:"
        echo "  sudo usermod -aG docker $USER"
        echo "  (Then log out and log back in)"
    fi
    echo ""
}

new_env_file() {
    log "Creating .env file from template..." "cyan"
    local env_example="$INSTALL_DIR/env.example"
    local env_file="$INSTALL_DIR/.env"
    
    if [ -f "$env_file" ]; then
        log ".env file already exists, skipping creation." "yellow"
        return
    fi
    
    if [ -f "$env_example" ]; then
        cp "$env_example" "$env_file"
        log ".env file created from template." "green"
    else
        # Create default .env file
        cat > "$env_file" << EOF
# VaultBubble Beta - Environment Configuration
DATABASE_URL=postgresql+psycopg2://badger:badgerpass@db:5432/badgerdb
MODEL_PROVIDER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_CHAT_MODEL=gemma3:1b-it-q4_K_M
OLLAMA_EMBED_MODEL=all-minilm
EOF
        log ".env file created with defaults." "green"
    fi
}

build_docker_container() {
    log "Building Docker containers..." "cyan"
    cd "$INSTALL_DIR"
    
    if docker compose -f docker-compose.beta.yml build; then
        log "Docker containers built successfully." "green"
    else
        log "Error building Docker containers." "red"
        exit 1
    fi
}

extract_extension() {
    log "Extracting Chrome extension from container..." "cyan"
    
    cd "$INSTALL_DIR"
    
    # Start containers temporarily to extract extension
    docker compose -f docker-compose.beta.yml up -d backend || true
    sleep 5
    
    # Wait for container to be ready
    local max_attempts=30
    local attempt=0
    local container_ready=false
    
    while [ $attempt -lt $max_attempts ]; do
        if docker ps --format "{{.Names}}" | grep -q "backend"; then
            container_ready=true
            break
        fi
        sleep 1
        attempt=$((attempt + 1))
    done
    
    if [ "$container_ready" = true ]; then
        bash "$INSTALL_DIR/extract-extension.sh" || {
            log "Warning: Extension extraction failed." "yellow"
            log "You can extract it later by running: ./extract-extension.sh" "yellow"
        }
    else
        log "Warning: Could not extract extension. Container may not be ready." "yellow"
        log "You can extract it later by running: ./extract-extension.sh" "yellow"
    fi
}

new_desktop_shortcut() {
    log "Creating desktop shortcut..." "cyan"
    if bash "$INSTALL_DIR/create-shortcut.sh" "$INSTALL_DIR"; then
        log "Desktop shortcut created successfully." "green"
    else
        log "Warning: Failed to create desktop shortcut." "yellow"
    fi
}

set_startup_configuration() {
    log "Configuring startup options..." "cyan"
    
    echo -n "Would you like VaultBubble to start automatically when you log in? (y/n): "
    read -r response
    
    if [[ "$response" =~ ^[Yy] ]]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS: Create LaunchAgent
            local plist_path="$HOME/Library/LaunchAgents/com.vaultbubble.startup.plist"
            cat > "$plist_path" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.vaultbubble.startup</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/docker</string>
        <string>compose</string>
        <string>-f</string>
        <string>$INSTALL_DIR/docker-compose.beta.yml</string>
        <string>up</string>
        <string>-d</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
</dict>
</plist>
EOF
            launchctl load "$plist_path" 2>/dev/null || true
            log "Startup configuration created for macOS." "green"
            log "To disable: launchctl unload $plist_path" "gray"
            
        elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
            # Linux: Create systemd user service or autostart entry
            local autostart_dir="$HOME/.config/autostart"
            mkdir -p "$autostart_dir"
            
            local desktop_file="$autostart_dir/vaultbubble.desktop"
            cat > "$desktop_file" << EOF
[Desktop Entry]
Type=Application
Name=VaultBubble
Comment=Start VaultBubble on login
Exec=sh -c "cd '$INSTALL_DIR' && docker compose -f docker-compose.beta.yml up -d"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
            chmod +x "$desktop_file"
            log "Startup configuration created for Linux." "green"
            log "To disable: Remove $desktop_file" "gray"
        fi
    else
        log "Startup configuration skipped." "gray"
    fi
}

test_port_available() {
    local port=$1
    if command -v nc &> /dev/null; then
        if nc -z localhost "$port" 2>/dev/null; then
            return 1
        fi
    elif command -v ss &> /dev/null; then
        if ss -lnt | grep -q ":$port "; then
            return 1
        fi
    fi
    return 0
}

# Main installation process
echo ""
echo "========================================"
echo "VaultBubble Beta Installer"
echo "========================================"
echo ""

log "Starting installation in: $INSTALL_DIR" "cyan"

# Check Docker
if [ "$SKIP_DOCKER_CHECK" = false ]; then
    if ! test_docker_installed; then
        show_docker_install_instructions
        exit 1
    fi
    
    if ! test_docker_running; then
        log "Docker is installed but not running. Please start Docker." "red"
        exit 1
    fi
fi

# Check port availability
if ! test_port_available 8000; then
    log "Warning: Port 8000 is already in use. VaultBubble may not start correctly." "yellow"
    echo -n "Continue anyway? (y/n): "
    read -r continue_response
    if [[ ! "$continue_response" =~ ^[Yy] ]]; then
        exit 1
    fi
fi

# Create .env file
new_env_file

# Build containers
if [ "$SKIP_BUILD" = false ]; then
    build_docker_container
else
    log "Skipping Docker build (--skip-build specified)" "yellow"
fi

# Extract extension
extract_extension

# Create desktop shortcut
if [ "$NO_SHORTCUT" = false ]; then
    new_desktop_shortcut
else
    log "Skipping desktop shortcut creation (--no-shortcut specified)" "yellow"
fi

# Configure startup
if [ "$NO_STARTUP" = false ]; then
    set_startup_configuration
else
    log "Skipping startup configuration (--no-startup specified)" "yellow"
fi

echo ""
echo "========================================"
echo "Installation Complete!"
echo "========================================"
echo ""
log "Installation completed successfully." "green"
echo ""
echo "Next steps:"
echo "1. Load the Chrome extension from: ./extension-extracted/extension"
echo "   Or use the zip file: ./extension/vaultbubble-extension.zip"
echo "2. Start VaultBubble by double-clicking the desktop shortcut"
echo "   Or run: docker compose -f docker-compose.beta.yml up"
echo "3. Access the application at: http://localhost:8000"
echo ""
echo "For help, see README_BETA.md"
echo ""
