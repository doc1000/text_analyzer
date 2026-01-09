# Create Desktop Shortcut for VaultBubble
# Windows PowerShell script

param(
    [string]$InstallDir = $PSScriptRoot,
    [string]$IconPath = ".\icons\icons_flat\icon-128.png",
    [string]$ShortcutName = "VaultBubble"
)

$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "$ShortcutName.lnk"
$TargetPath = "powershell.exe"
$WorkingDirectory = $InstallDir
$IconFile = Join-Path $InstallDir $IconPath

# Convert icon path to absolute if relative
if (-not [System.IO.Path]::IsPathRooted($IconFile)) {
    $IconFile = Join-Path $InstallDir $IconFile
}

# If icon doesn't exist, try alternative locations
if (-not (Test-Path $IconFile)) {
    $altPaths = @(
        ".\icons\icons_hc\icon-128.png",
        ".\extension\icons\icon-128.png",
        ".\icons\icons_flat\icon-96.png"
    )
    foreach ($alt in $altPaths) {
        $altPath = Join-Path $InstallDir $alt
        if (Test-Path $altPath) {
            $IconFile = $altPath
            break
        }
    }
}

# Create shortcut
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetPath
$Shortcut.Arguments = "-NoExit -Command `"cd '$WorkingDirectory'; docker compose -f docker-compose.beta.yml up`""
$Shortcut.WorkingDirectory = $WorkingDirectory
$Shortcut.Description = "VaultBubble - Text Analyzer and Research Vault"
$Shortcut.IconLocation = $IconFile
$Shortcut.Save()

Write-Host "Desktop shortcut created: $ShortcutPath" -ForegroundColor Green
Write-Host "Icon: $IconFile" -ForegroundColor Gray
