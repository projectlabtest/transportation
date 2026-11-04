$ErrorActionPreference = "Stop"

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$relativeFiles = @(
    "app.py",
    "templates\base.html",
    "templates\login.html",
    "README.md",
    "driver-app\app\build\outputs\apk\debug\app-debug.apk"
)

foreach ($relativePath in $relativeFiles) {
    $packagePath = Join-Path $packageRoot $relativePath
    if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) {
        throw "The update package is missing: $relativePath"
    }
}

$targetInput = Read-Host "Enter the server's existing LHC Transport project folder"
if ([string]::IsNullOrWhiteSpace($targetInput)) {
    throw "A project folder is required."
}

$targetRoot = (Resolve-Path -LiteralPath $targetInput).Path
if (-not (Test-Path -LiteralPath (Join-Path $targetRoot "app.py") -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $targetRoot "templates") -PathType Container)) {
    throw "The selected folder does not look like the LHC Transport project."
}

Write-Host ""
Write-Host "Project to update: $targetRoot"
Write-Host "Files: app.py, two templates, README.md, and the Android APK"
$confirmation = Read-Host "Type INSTALL to back up and replace these files"
if ($confirmation -cne "INSTALL") {
    Write-Host "No files were changed."
    exit 0
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupRoot = Join-Path $targetRoot ".server-update-backup-$timestamp"

New-Item -ItemType Directory -Path $backupRoot | Out-Null
foreach ($relativePath in $relativeFiles) {
    $targetPath = Join-Path $targetRoot $relativePath
    if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
        $backupPath = Join-Path $backupRoot $relativePath
        $backupDirectory = Split-Path -Parent $backupPath
        New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
        Copy-Item -LiteralPath $targetPath -Destination $backupPath
    }
}

foreach ($relativePath in $relativeFiles) {
    $packagePath = Join-Path $packageRoot $relativePath
    $targetPath = Join-Path $targetRoot $relativePath
    $targetDirectory = Split-Path -Parent $targetPath
    New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null
    Copy-Item -LiteralPath $packagePath -Destination $targetPath -Force
}

Write-Host ""
Write-Host "Update files installed. Previous files are backed up at:"
Write-Host $backupRoot
Write-Host "Restart the existing LHC Transport server process to load the new route."
Write-Host "Do not start a second server process if the current one is managed as a service."
Write-Host "After restart, test /download/android on the server before configuring Tailscale Serve."
