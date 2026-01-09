#!/bin/bash
# Create Desktop Shortcut for VaultBubble
# Linux/macOS script

INSTALL_DIR="${1:-$(pwd)}"
ICON_PATH="${2:-./icons/icons_flat/icon-128.png}"
SHORTCUT_NAME="${3:-VaultBubble}"

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    DESKTOP_DIR="$HOME/Desktop"
    SHORTCUT_PATH="$DESKTOP_DIR/${SHORTCUT_NAME}.command"
    
    # Create executable script
    cat > "$SHORTCUT_PATH" << EOF
#!/bin/bash
cd "$INSTALL_DIR"
docker compose -f docker-compose.beta.yml up
EOF
    
    chmod +x "$SHORTCUT_PATH"
    
    # Try to set icon (requires osascript)
    ICON_FILE="$INSTALL_DIR/$ICON_PATH"
    if [ -f "$ICON_FILE" ]; then
        # Convert PNG to ICNS for macOS (simplified - just use PNG)
        # Note: macOS may not display custom icons without additional setup
        echo "Shortcut created: $SHORTCUT_PATH"
        echo "Note: To set a custom icon on macOS, right-click the file, select 'Get Info', and drag an icon to the icon area."
    fi
    
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # Linux
    DESKTOP_DIR="$HOME/Desktop"
    # Fallback to ~/.local/share/applications if Desktop doesn't exist
    if [ ! -d "$DESKTOP_DIR" ]; then
        DESKTOP_DIR="$HOME/.local/share/applications"
        mkdir -p "$DESKTOP_DIR"
    fi
    
    SHORTCUT_PATH="$DESKTOP_DIR/${SHORTCUT_NAME}.desktop"
    ICON_FILE="$INSTALL_DIR/$ICON_PATH"
    
    # Try alternative icon paths if primary doesn't exist
    if [ ! -f "$ICON_FILE" ]; then
        ALT_PATHS=(
            "./icons/icons_hc/icon-128.png"
            "./extension/icons/icon-128.png"
            "./icons/icons_flat/icon-96.png"
        )
        
        for alt in "${ALT_PATHS[@]}"; do
            alt_path="$INSTALL_DIR/$alt"
            if [ -f "$alt_path" ]; then
                ICON_FILE="$alt_path"
                break
            fi
        done
    fi
    
    # Create .desktop file
    cat > "$SHORTCUT_PATH" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$SHORTCUT_NAME
Comment=VaultBubble - Text Analyzer and Research Vault
Exec=sh -c "cd '$INSTALL_DIR' && docker compose -f docker-compose.beta.yml up"
Icon=$ICON_FILE
Terminal=true
Categories=Utility;Development;
EOF
    
    chmod +x "$SHORTCUT_PATH"
    echo "Desktop shortcut created: $SHORTCUT_PATH"
    echo "Icon: $ICON_FILE"
    
    # Update desktop database (if available)
    if command -v update-desktop-database &> /dev/null; then
        update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
    fi
else
    echo "Unsupported OS: $OSTYPE"
    exit 1
fi
