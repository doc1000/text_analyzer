param(
    [string]$SourceDir = ".",
    [string]$TargetDir = "."
)

# Example call:
# .\Convert-ToWebP.ps1 -SourceDir ".\landing_reel" -TargetDir ".\landing_reel"

$src  = (Resolve-Path $SourceDir).Path
$dest = (Resolve-Path $TargetDir).Path

$webpDir = Join-Path $dest "webp"
New-Item -ItemType Directory -Path $webpDir -Force | Out-Null

Get-ChildItem -Path $src -Filter *.png | ForEach-Object {
    $outName = ($_.BaseName + ".webp")
    $outPath = Join-Path $webpDir $outName
    & magick $_.FullName -resize 1400x -quality 75 -define webp:method=6 $outPath
}
