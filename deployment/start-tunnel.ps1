# Cloudflare Quick Tunnel for Transport Booking
# Automatically updates the public_url in the database
# No domain required

$ErrorActionPreference = "Stop"

$transportDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$dbPath = Join-Path $transportDir "transport.db"
$pythonExe = "C:\Program1\python.exe"

# Check cloudflared
$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
$cfPath = $null
if ($cf) { $cfPath = $cf.Source }
else {
    $local = Join-Path $transportDir "cloudflared.exe"
    if (Test-Path $local) { $cfPath = $local }
    else {
        Write-Host "ERROR: cloudflared not found." -ForegroundColor Red
        Write-Host "Download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        Write-Host "Place cloudflared.exe in: $transportDir"
        pause
        exit 1
    }
}

Write-Host ""
Write-Host "  Starting Cloudflare Tunnel..." -ForegroundColor Cyan
Write-Host "  The public HTTPS URL will be captured and saved automatically."
Write-Host ""

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $cfPath
$psi.Arguments = "tunnel --url http://localhost:5019"
$psi.RedirectStandardError = $true
$psi.RedirectStandardOutput = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $false

$proc = [System.Diagnostics.Process]::Start($psi)

$urlFound = $false
while (-not $proc.HasExited) {
    $line = $proc.StandardError.ReadLine()
    if ($line) {
        Write-Host $line
        if ($line -match "https://[a-zA-Z0-9-]+\.trycloudflare\.com") {
            $url = $Matches[0]
            Write-Host ""
            Write-Host "  Tunnel URL: $url" -ForegroundColor Green
            Write-Host "  Saving to database..." -ForegroundColor Green
            
            # Update database via Python
            $pyScript = @"
import sqlite3
c = sqlite3.connect(r'$dbPath')
c.execute("INSERT INTO app_settings (key, value) VALUES ('public_url', ?) ON CONFLICT(key) DO UPDATE SET value=?", ($url, $url))
c.commit()
c.close()
print('saved')
"@
            $result = & $pythonExe -c $pyScript 2>&1
            if ($result -match "saved") {
                Write-Host "  Saved! QR codes will now use: $url" -ForegroundColor Green
            } else {
                Write-Host "  Could not save to DB: $result" -ForegroundColor Yellow
                Write-Host "  Please set it manually in: Gear -> QR CODE SETTINGS" -ForegroundColor Yellow
            }
            $urlFound = $true
        }
    }
    Start-Sleep -Milliseconds 100
}

Write-Host ""
Write-Host "  Tunnel stopped." -ForegroundColor Yellow
pause