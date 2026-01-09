# Packaging Guide for Beta Distribution

This guide explains how to package the beta distribution for sharing with testers.

## Quick Package

### Option 1: Zip the entire folder

1. **Navigate to the parent directory** (one level above `beta-distribution`)
2. **Create a zip file**:
   - Windows: Right-click `beta-distribution` folder → Send to → Compressed (zipped) folder
   - Linux/macOS: `zip -r vaultbubble-beta.zip beta-distribution/`
3. **Share the zip file** with beta testers

### Option 2: Exclude unnecessary files

Before zipping, you may want to exclude:
- `install.log` (if exists)
- `extension-extracted/` folder (will be created during installation)
- `.env` file (will be created from template)
- Any temporary files

**Windows PowerShell**:
```powershell
Compress-Archive -Path beta-distribution\* -DestinationPath vaultbubble-beta.zip -Force
```

**Linux/macOS**:
```bash
cd beta-distribution
zip -r ../vaultbubble-beta.zip . -x "*.log" -x "extension-extracted/*" -x ".env" -x "__pycache__/*" -x "*.pyc"
```

## Package Contents

The beta distribution package should include:

### Required Files:
- `install.ps1` - Windows installer
- `install.sh` - Linux/macOS installer
- `README_BETA.md` - User instructions
- `docker-compose.beta.yml` - Docker Compose configuration
- `Dockerfile.beta` - Docker image definition
- `env.example` - Environment template
- `app/` - Application code
- `db/` - Database initialization
- `extension/` - Chrome extension source
- `icons/` - Icon files for shortcuts

### Helper Scripts:
- `extract-extension.ps1` / `extract-extension.sh` - Extension extractor
- `create-shortcut.ps1` / `create-shortcut.sh` - Shortcut creator
- `check-installation.ps1` / `check-installation.sh` - Health checker

## Pre-build Option (Advanced)

If you want to speed up installation for testers, you can pre-build the Docker image:

1. **Build the image**:
   ```bash
   cd beta-distribution
   docker compose -f docker-compose.beta.yml build
   ```

2. **Save the image**:
   ```bash
   docker save text_analyzer-backend:latest -o vaultbubble-backend-image.tar
   ```

3. **Include loading instructions** in README:
   ```bash
   docker load -i vaultbubble-backend-image.tar
   ```

**Note**: This creates a large file (several GB) and may not be practical for distribution.

## Distribution Size

Expected package sizes:
- **Source only**: ~5-10 MB (compressed)
- **With pre-built image**: ~2-5 GB (compressed)

## Testing the Package

Before distributing:

1. **Extract to a clean location**
2. **Run the installer**:
   - Windows: `powershell -ExecutionPolicy Bypass -File install.ps1`
   - Linux/macOS: `./install.sh`
3. **Verify installation**: Run health check script
4. **Test the application**: Access http://localhost:8000
5. **Test extension**: Load and use the Chrome extension

## Version Information

Update these files with version info:
- `README_BETA.md` - Version section at bottom
- `extension/manifest.json` - Version field
- Consider adding a `VERSION` file

## Security Notes

- The `.env` file contains default credentials - testers should change these for production use
- Docker images may be flagged by antivirus (false positives)
- Consider code signing for install scripts if distributing widely
