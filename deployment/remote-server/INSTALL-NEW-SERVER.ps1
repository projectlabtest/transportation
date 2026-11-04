param(
    [string]$TargetRoot = (Join-Path $HOME "LHCTransport")
)

$ErrorActionPreference = "Stop"

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$requiredFiles = @(
    "app.py",
    "database.py",
    "wsgi.py",
    "requirements.txt",
    "SERVER-DEPLOYMENT.txt",
    "static",
    "templates",
    "driver-app\app\build\outputs\apk\debug\app-debug.apk"
)

foreach ($relativePath in $requiredFiles) {
    $packagePath = Join-Path $packageRoot $relativePath
    if (-not (Test-Path -LiteralPath $packagePath)) {
        throw "The deployment package is missing: $relativePath"
    }
}

$targetRoot = [System.IO.Path]::GetFullPath($TargetRoot)

if (Test-Path -LiteralPath $targetRoot) {
    $existingItems = @(Get-ChildItem -LiteralPath $targetRoot -Force)
    if ($existingItems.Count -gt 0) {
        throw "The destination is not empty; nothing was overwritten: $targetRoot"
    }
}

$listeners = @(Get-NetTCPConnection -LocalPort 5001 -State Listen `
    -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    throw "Port 5001 is already in use; nothing was installed. Check the existing server first."
}

$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($launcher) {
    $pythonCommand = $launcher.Source
    $pythonPrefix = @("-3")
} else {
    $launcher = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $launcher) {
        throw "Python 3.10 or later is required. Install Python, then run this installer again."
    }
    $pythonCommand = $launcher.Source
    $pythonPrefix = @()
}

$pythonVersionOutput = (& $pythonCommand @pythonPrefix --version 2>&1 | Out-String)
if ($pythonVersionOutput -notmatch "Python\s+(\d+)\.(\d+)") {
    throw "Could not read the Python version: $pythonVersionOutput"
}
$pythonVersion = [version]::new([int]$Matches[1], [int]$Matches[2])
if ($pythonVersion -lt [version]"3.10") {
    throw "Python 3.10 or later is required; found $pythonVersionOutput"
}

Write-Host ""
Write-Host "Creating a separate LHC Transport installation at:"
Write-Host $targetRoot
Write-Host "Existing inventory projects and databases will not be changed."

New-Item -ItemType Directory -Force -Path $targetRoot | Out-Null
foreach ($relativePath in @(
        "app.py",
        "database.py",
        "wsgi.py",
        "requirements.txt",
        "SERVER-DEPLOYMENT.txt")) {
    Copy-Item -LiteralPath (Join-Path $packageRoot $relativePath) `
        -Destination (Join-Path $targetRoot $relativePath)
}
foreach ($directory in @("static", "templates", "driver-app")) {
    $sourceDirectory = Join-Path $packageRoot $directory
    if (Test-Path -LiteralPath $sourceDirectory -PathType Container) {
        Copy-Item -LiteralPath $sourceDirectory -Destination $targetRoot `
            -Recurse -Force
    }
}

$dataRoot = Join-Path $env:LOCALAPPDATA "LHCTransport"
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$databasePath = Join-Path $dataRoot "transport.db"
$secret = [Environment]::GetEnvironmentVariable(
    "TRANSPORT_SECRET_KEY", "User")
if ([string]::IsNullOrWhiteSpace($secret)) {
    $randomBytes = New-Object "System.Byte[]" 32
    $randomGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $randomGenerator.GetBytes($randomBytes)
    } finally {
        $randomGenerator.Dispose()
    }
    $secret = [Convert]::ToBase64String($randomBytes)
    [Environment]::SetEnvironmentVariable(
        "TRANSPORT_SECRET_KEY", $secret, "User")
}

[Environment]::SetEnvironmentVariable("TRANSPORT_ENV", "production", "User")
[Environment]::SetEnvironmentVariable(
    "TRANSPORT_DB_PATH", $databasePath, "User")
$env:TRANSPORT_ENV = "production"
$env:TRANSPORT_SECRET_KEY = $secret
$env:TRANSPORT_DB_PATH = $databasePath

$venvPath = Join-Path $targetRoot ".venv"
$venvArguments = @()
$venvArguments += $pythonPrefix
$venvArguments += @("-m", "venv", $venvPath)
& $pythonCommand @venvArguments
if ($LASTEXITCODE -ne 0) {
    throw "Python could not create the virtual environment."
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install -r (Join-Path $targetRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed. The new project files remain at $targetRoot."
}

Push-Location $targetRoot
try {
    & $venvPython -c "import database; database.init_db()"
    if ($LASTEXITCODE -ne 0) {
        throw "The transport database could not be initialized."
    }
} finally {
    Pop-Location
}

$stdoutLog = Join-Path $dataRoot "server.log"
$stderrLog = Join-Path $dataRoot "server-error.log"
$server = Start-Process -FilePath $venvPython `
    -ArgumentList @("-m", "waitress", "--listen=127.0.0.1:5001", "wsgi:app") `
    -WorkingDirectory $targetRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    $server.Refresh()
    if ($server.HasExited) {
        Get-Content -LiteralPath $stderrLog -ErrorAction SilentlyContinue
        throw "The app server exited during startup. Review $stderrLog."
    }
    try {
        $response = Invoke-WebRequest `
            -Uri "http://127.0.0.1:5001/download/android" `
            -TimeoutSec 2 -UseBasicParsing
        if ($response.StatusCode -eq 200 -and
            $response.Headers["Content-Disposition"] -like "*LHC-Transport.apk*") {
            $ready = $true
            break
        }
    } catch {
    }
}

if (-not $ready) {
    Stop-Process -Id $server.Id -ErrorAction SilentlyContinue
    Get-Content -LiteralPath $stderrLog -ErrorAction SilentlyContinue
    throw "The download route did not become ready. Review $stderrLog."
}

$installInfo = @(
    "Install folder: $targetRoot",
    "Database: $databasePath",
    "App log: $stdoutLog",
    "Error log: $stderrLog",
    "Local address: http://127.0.0.1:5001",
    "Download route: /download/android",
    "Server process ID: $($server.Id)"
)
Set-Content -LiteralPath (Join-Path $targetRoot "SERVER-INSTALL-INFO.txt") `
    -Value $installInfo -Encoding ASCII

Write-Host ""
Write-Host "LHC Transport is running locally at http://127.0.0.1:5001"
Write-Host "The APK download route returned HTTP 200."
Write-Host "The app is not yet shared over Tailscale."
Write-Host "First log in locally as admin / admin123 and change the admin password."
Write-Host "Then use the private HTTPS steps in SERVER-DEPLOYMENT.txt."
Write-Host "The new database is stored separately at $databasePath"
