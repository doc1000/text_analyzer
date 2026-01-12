#!/bin/bash
# VaultBubble Installation Health Check
# Linux/macOS script to verify installation

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALL_CHECKS_PASSED=true

check() {
    local check_name="$1"
    local passed="$2"
    local message="${3:-}"
    
    if [ "$passed" = true ]; then
        echo -e "\033[0;32m[✓]\033[0m $check_name"
        if [ -n "$message" ]; then
            echo "    $message"
        fi
    else
        echo -e "\033[0;31m[✗]\033[0m $check_name"
        if [ -n "$message" ]; then
            echo "    $message"
        fi
        ALL_CHECKS_PASSED=false
    fi
}

echo ""
echo "========================================"
echo "VaultBubble Installation Health Check"
echo "========================================"
echo ""

# Check Docker installation
echo "Checking Docker..."
if command -v docker &> /dev/null; then
    docker_version=$(docker --version 2>&1)
    check "Docker Installed" true "$docker_version"
else
    check "Docker Installed" false "Docker not found in PATH"
fi

# Check Docker daemon
if docker info &> /dev/null; then
    check "Docker Daemon Running" true
else
    check "Docker Daemon Running" false "Start Docker"
fi

# Check docker-compose
if docker compose version &> /dev/null; then
    compose_version=$(docker compose version 2>&1)
    check "Docker Compose Available" true "$compose_version"
else
    check "Docker Compose Available" false "docker compose command not available"
fi

echo ""
echo "Checking Installation Files..."

# Check required files
required_files=(
    "docker-compose.beta.yml"
    "Dockerfile.beta"
    ".env"
    "app/main.py"
)

for file in "${required_files[@]}"; do
    file_path="$INSTALL_DIR/$file"
    if [ -f "$file_path" ]; then
        check "File: $file" true
    else
        check "File: $file" false
    fi
done

echo ""
echo "Checking Docker Containers..."

# Check if containers are built
if docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "text_analyzer"; then
    images=$(docker images --format "{{.Repository}}:{{.Tag}}" | grep "text_analyzer" | tr '\n' ', ')
    check "Docker Images Built" true "Found: $images"
else
    check "Docker Images Built" false "Run: docker compose -f docker-compose.beta.yml build"
fi

# Check if containers are running
containers=$(docker ps --format "{{.Names}}" | grep -E "backend|db|ollama" | tr '\n' ', ' || echo "")
if [ -n "$containers" ]; then
    check "Containers Running" true "Running: $containers"
else
    check "Containers Running" false "Start with: docker compose -f docker-compose.beta.yml up -d"
fi

echo ""
echo "Checking Application Health..."

# Check if application is responding
if command -v curl &> /dev/null; then
    if curl -sf http://localhost:8000/health &> /dev/null; then
        check "Application Health Endpoint" true "Application is running and healthy"
    else
        check "Application Health Endpoint" false "Cannot reach http://localhost:8000/health - Is the application running?"
    fi
elif command -v wget &> /dev/null; then
    if wget -q --spider http://localhost:8000/health &> /dev/null; then
        check "Application Health Endpoint" true "Application is running and healthy"
    else
        check "Application Health Endpoint" false "Cannot reach http://localhost:8000/health - Is the application running?"
    fi
else
    check "Application Health Endpoint" false "curl or wget not available for health check"
fi

# Check port 8000
if command -v nc &> /dev/null; then
    if nc -z localhost 8000 2>/dev/null; then
        check "Port 8000 Accessible" true
    else
        check "Port 8000 Accessible" false "Port 8000 is not accessible"
    fi
elif command -v ss &> /dev/null; then
    if ss -lnt | grep -q ":8000 "; then
        check "Port 8000 Accessible" true
    else
        check "Port 8000 Accessible" false "Port 8000 is not accessible"
    fi
else
    check "Port 8000 Accessible" false "Cannot test port (nc or ss not available)"
fi

echo ""
echo "Checking Extension..."

# Check extension files
extension_path="$INSTALL_DIR/extension-extracted/extension"
extension_zip="$INSTALL_DIR/extension/vaultbubble-extension.zip"

if [ -d "$extension_path" ]; then
    check "Extension Extracted" true "Found at: $extension_path"
elif [ -f "$extension_zip" ]; then
    check "Extension Extracted" true "Zip file found: $extension_zip"
else
    check "Extension Extracted" false "Run: ./extract-extension.sh"
fi

echo ""
echo "========================================"
if [ "$ALL_CHECKS_PASSED" = true ]; then
    echo -e "\033[0;32mAll Checks Passed!\033[0m"
    echo ""
    echo "Your VaultBubble installation looks good!"
    echo "Access the application at: http://localhost:8000"
else
    echo -e "\033[0;33mSome Checks Failed\033[0m"
    echo ""
    echo "Please review the failed checks above and:"
    echo "1. Ensure Docker is installed and running"
    echo "2. Run: docker compose -f docker-compose.beta.yml build"
    echo "3. Run: docker compose -f docker-compose.beta.yml up -d"
    echo "4. Check README_BETA.md for troubleshooting"
fi
echo "========================================"
echo ""
