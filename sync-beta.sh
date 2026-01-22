#!/bin/bash
# sync-beta.sh
# Syncs current app changes to beta-distribution folder
# Usage: ./sync-beta.sh [--bump-version] [--version-type patch|minor|major] [--health-check] [--dry-run]

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Defaults
BUMP_VERSION=false
VERSION_TYPE="patch"
HEALTH_CHECK=false
DRY_RUN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --bump-version) BUMP_VERSION=true; shift ;;
        --version-type) VERSION_TYPE="$2"; shift 2 ;;
        --health-check) HEALTH_CHECK=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
BETA_APP_DIR="$SCRIPT_DIR/beta-distribution/app"
BETA_MANIFEST="$SCRIPT_DIR/beta-distribution/extension/manifest.json"
BETA_EXTENSION_DIR="$SCRIPT_DIR/beta-distribution/extension"

echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  Beta Distribution Sync Tool${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# Files to sync
FILES_TO_SYNC=(
    "config.py"
    "db.py"
    "helpers.py"
    "main.py"
    "mmr.py"
    "models.py"
    "schemas.py"
    "topics.py"
    "requirements.txt"
    "__init__.py"
)

STATIC_FILES=(
    "static/index.html"
    "static/document.html"
)

echo -e "${YELLOW}[1/5] Checking file differences...${NC}"
echo ""

declare -a CHANGES_MADE
declare -a FILES_IDENTICAL

for file in "${FILES_TO_SYNC[@]}"; do
    source_path="$APP_DIR/$file"
    dest_path="$BETA_APP_DIR/$file"
    
    if [[ -f "$source_path" ]]; then
        if [[ -f "$dest_path" ]]; then
            source_hash=$(sha256sum "$source_path" | cut -d' ' -f1)
            dest_hash=$(sha256sum "$dest_path" | cut -d' ' -f1)
            
            if [[ "$source_hash" != "$dest_hash" ]]; then
                CHANGES_MADE+=("$file")
                echo -e "  ${YELLOW}[CHANGED]${NC} $file"
            else
                FILES_IDENTICAL+=("$file")
                echo -e "  ${GREEN}[OK]${NC} $file"
            fi
        else
            CHANGES_MADE+=("$file")
            echo -e "  ${MAGENTA}[NEW]${NC} $file"
        fi
    else
        echo -e "  ${RED}[MISSING]${NC} $file (source not found)"
    fi
done

# Check static files
for file in "${STATIC_FILES[@]}"; do
    source_path="$APP_DIR/$file"
    dest_path="$BETA_APP_DIR/$file"
    
    if [[ -f "$source_path" ]]; then
        if [[ -f "$dest_path" ]]; then
            source_hash=$(sha256sum "$source_path" | cut -d' ' -f1)
            dest_hash=$(sha256sum "$dest_path" | cut -d' ' -f1)
            
            if [[ "$source_hash" != "$dest_hash" ]]; then
                CHANGES_MADE+=("$file")
                echo -e "  ${YELLOW}[CHANGED]${NC} $file"
            else
                FILES_IDENTICAL+=("$file")
                echo -e "  ${GREEN}[OK]${NC} $file"
            fi
        else
            CHANGES_MADE+=("$file")
            echo -e "  ${MAGENTA}[NEW]${NC} $file"
        fi
    fi
done

echo ""
echo -e "${YELLOW}[2/5] Summary${NC}"
echo -e "  ${GREEN}Files identical: ${#FILES_IDENTICAL[@]}${NC}"
echo -e "  ${YELLOW}Files to update: ${#CHANGES_MADE[@]}${NC}"

if [[ ${#CHANGES_MADE[@]} -eq 0 ]]; then
    echo ""
    echo -e "${GREEN}No changes to sync. Beta distribution is up to date!${NC}"
    
    if [[ "$HEALTH_CHECK" == "false" && "$BUMP_VERSION" == "false" ]]; then
        exit 0
    fi
fi

# Sync files
if [[ ${#CHANGES_MADE[@]} -gt 0 ]]; then
    echo ""
    echo -e "${YELLOW}[3/5] Syncing files...${NC}"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "  ${CYAN}[DRY RUN] Would sync the following files:${NC}"
        for file in "${CHANGES_MADE[@]}"; do
            echo "    - $file"
        done
    else
        for file in "${CHANGES_MADE[@]}"; do
            source_path="$APP_DIR/$file"
            dest_path="$BETA_APP_DIR/$file"
            
            # Ensure destination directory exists
            dest_dir=$(dirname "$dest_path")
            mkdir -p "$dest_dir"
            
            cp "$source_path" "$dest_path"
            echo -e "  ${GREEN}Copied:${NC} $file"
        done
    fi
else
    echo ""
    echo -e "${GREEN}[3/5] No files to sync${NC}"
fi

# Version bump
if [[ "$BUMP_VERSION" == "true" ]]; then
    echo ""
    echo -e "${YELLOW}[4/5] Updating version...${NC}"
    
    if command -v jq &> /dev/null; then
        current_version=$(jq -r '.version' "$BETA_MANIFEST")
        
        IFS='.' read -r major minor patch <<< "$current_version"
        
        case $VERSION_TYPE in
            "major") major=$((major + 1)); minor=0; patch=0 ;;
            "minor") minor=$((minor + 1)); patch=0 ;;
            "patch") patch=$((patch + 1)) ;;
        esac
        
        new_version="$major.$minor.$patch"
        
        echo -e "  ${CYAN}Current version: $current_version${NC}"
        echo -e "  ${GREEN}New version: $new_version${NC}"
        
        if [[ "$DRY_RUN" == "false" ]]; then
            jq ".version = \"$new_version\"" "$BETA_MANIFEST" > "${BETA_MANIFEST}.tmp"
            mv "${BETA_MANIFEST}.tmp" "$BETA_MANIFEST"
            echo -e "  ${GREEN}Version updated!${NC}"
        else
            echo -e "  ${CYAN}[DRY RUN] Would update to version $new_version${NC}"
        fi
    else
        echo -e "  ${RED}jq not installed. Cannot update version.${NC}"
        echo -e "  ${YELLOW}Install with: brew install jq (Mac) or apt install jq (Linux)${NC}"
    fi
else
    echo ""
    echo -e "${GRAY}[4/5] Skipping version bump (use --bump-version to enable)${NC}"
fi

# Health check
if [[ "$HEALTH_CHECK" == "true" ]]; then
    echo ""
    echo -e "${YELLOW}[5/5] Running health check...${NC}"
    
    if command -v curl &> /dev/null; then
        health_response=$(curl -s -w "%{http_code}" --connect-timeout 5 "http://localhost:8000/health" 2>/dev/null || echo "000")
        http_code="${health_response: -3}"
        body="${health_response:0:${#health_response}-3}"
        
        if [[ "$http_code" == "200" ]]; then
            echo -e "  ${GREEN}Backend health check: PASSED${NC}"
        else
            echo -e "  ${RED}Backend health check: FAILED (HTTP $http_code)${NC}"
            echo -e "  ${YELLOW}Make sure the backend is running (docker compose up)${NC}"
        fi
        
        # Test model config
        config_response=$(curl -s --connect-timeout 5 "http://localhost:8000/model_configs" 2>/dev/null || echo "{}")
        if [[ "$config_response" != "{}" ]]; then
            echo -e "  ${GREEN}Model config check: PASSED${NC}"
        else
            echo -e "  ${RED}Model config check: FAILED${NC}"
        fi
    else
        echo -e "  ${RED}curl not installed. Cannot run health check.${NC}"
    fi
else
    echo ""
    echo -e "${GRAY}[5/5] Skipping health check (use --health-check to enable)${NC}"
fi

echo ""
echo -e "${CYAN}============================================${NC}"
echo -e "${GREEN}  Sync Complete!${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# Show next steps
if [[ ${#CHANGES_MADE[@]} -gt 0 && "$DRY_RUN" == "false" ]]; then
    echo -e "${YELLOW}Next steps:${NC}"
    echo -e "  ${GRAY}1. Review changes in beta-distribution/app/${NC}"
    echo -e "  ${GRAY}2. Test the beta distribution locally${NC}"
    echo -e "  ${GRAY}3. Commit changes: git add . && git commit -m 'Sync beta distribution'${NC}"
fi
