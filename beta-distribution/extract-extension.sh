#!/bin/bash
# Extract Chrome Extension from Docker Container
# This script copies the extension from the container to the host filesystem

CONTAINER_NAME="${1:-}"
OUTPUT_DIR="${2:-./extension-extracted}"

echo "Extracting Chrome extension from container..."

# Auto-detect container name if not provided
if [ -z "$CONTAINER_NAME" ]; then
    CONTAINER_NAME=$(docker ps -a --format "{{.Names}}" | grep "backend" | head -n1)
    if [ -n "$CONTAINER_NAME" ]; then
        echo "Auto-detected container: $CONTAINER_NAME"
    else
        echo "Error: No backend container found."
        echo "Please ensure the container is built and running."
        echo "Try running: docker compose -f docker-compose.beta.yml up -d"
        exit 1
    fi
fi

# Check if container exists
if ! docker ps -a --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
    echo "Error: Container '$CONTAINER_NAME' not found."
    echo "Please ensure the container is built and running."
    echo "Try running: docker compose -f docker-compose.beta.yml up -d"
    exit 1
fi

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Copy extension from container
echo "Copying extension files from container..."
docker cp "${CONTAINER_NAME}:/code/extension" "$OUTPUT_DIR"

if [ $? -eq 0 ]; then
    echo "Extension extracted successfully to: $OUTPUT_DIR/extension"
    
    # Create zip file
    EXTENSION_DIR="./extension"
    mkdir -p "$EXTENSION_DIR"
    ZIP_PATH="./extension/vaultbubble-extension.zip"
    echo "Creating zip file: $ZIP_PATH"
    
    # Remove existing zip if present
    rm -f "$ZIP_PATH"
    
    # Create zip file
    cd "$OUTPUT_DIR"
    zip -r "../extension/vaultbubble-extension.zip" extension/
    cd - > /dev/null
    
    echo "Extension packaged as: $ZIP_PATH"
    echo ""
    echo "To load the extension in Chrome:"
    echo "1. Open Chrome and go to chrome://extensions/"
    echo "2. Enable 'Developer mode' (toggle in top right)"
    echo "3. Click 'Load unpacked' and select: $OUTPUT_DIR/extension"
    echo "   OR extract and load the zip file: $ZIP_PATH"
else
    echo "Error: Failed to extract extension from container."
    exit 1
fi
