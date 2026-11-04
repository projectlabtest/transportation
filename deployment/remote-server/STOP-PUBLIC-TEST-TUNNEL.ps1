$ErrorActionPreference = "Stop"

$pidPath = Join-Path $env:LOCALAPPDATA "LHCTransport\cloudflared-test.pid"
if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
    Write-Host "No LHC Transport test tunnel process ID was recorded."
    exit 0
}

$processId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
$process = Get-CimInstance Win32_Process `
    -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Host "The temporary tunnel is already stopped."
    exit 0
}

if ($process.Name -ne "cloudflared.exe" -or
    $process.CommandLine -notmatch "127\.0\.0\.1:5001") {
    throw "The recorded process is not the LHC Transport temporary tunnel; nothing was stopped."
}

Stop-Process -Id $processId -ErrorAction Stop
Remove-Item -LiteralPath $pidPath -Force
Write-Host "The temporary public link has been stopped."
