$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms

$installRoot = Join-Path $HOME "LHCTransport"
$databasePath = Join-Path $env:LOCALAPPDATA "LHCTransport\transport.db"
$pythonPath = Join-Path $installRoot ".venv\Scripts\python.exe"
$apkPath = Join-Path $installRoot `
    "driver-app\app\build\outputs\apk\debug\app-debug.apk"
$cloudflaredPath = Join-Path $env:LOCALAPPDATA `
    "LHCTransport\cloudflared.exe"
$accessPath = Join-Path $installRoot "PUBLIC-TEST-ACCESS.txt"
$stdoutLog = Join-Path $env:LOCALAPPDATA `
    "LHCTransport\cloudflared-test.log"
$stderrLog = Join-Path $env:LOCALAPPDATA `
    "LHCTransport\cloudflared-test-error.log"
$verificationLog = Join-Path $env:LOCALAPPDATA `
    "LHCTransport\public-apk-verification.log"
$pidPath = Join-Path $env:LOCALAPPDATA "LHCTransport\cloudflared-test.pid"
$temporaryScript = Join-Path $env:TEMP `
    "lhc-transport-create-test-accounts.py"
$tunnelProcess = $null

function New-TestPassword {
    $alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    $bytes = New-Object "System.Byte[]" 20
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return -join ($bytes | ForEach-Object {
        $alphabet[[int]$_ % $alphabet.Length]
    })
}

try {
    foreach ($path in @(
            (Join-Path $installRoot "app.py"),
            $databasePath,
            $pythonPath,
            $apkPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required installed app file not found: $path"
        }
    }

    $localCheck = Invoke-WebRequest `
        -Uri "http://127.0.0.1:5001/download/android" `
        -TimeoutSec 5 -UseBasicParsing
    if ($localCheck.StatusCode -ne 200 -or
        $localCheck.Headers["Content-Disposition"] -notlike "*LHC-Transport.apk*") {
        throw "The local APK download test did not return the expected file."
    }

    $confirm = [System.Windows.Forms.MessageBox]::Show(
        "Create a temporary, public HTTPS test link for LHC Transport?" +
        "`r`n`r`nCloudflare will relay browser and app traffic while the test is running." +
        "`r`nDo not use real or sensitive booking data." +
        "`r`n`r`nThis rotates the passwords for the demo accounts and creates a test requester." +
        "`r`nThe link will stop working when the tunnel is stopped.",
        "Temporary public test link",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Warning)
    if ($confirm -ne [System.Windows.Forms.DialogResult]::Yes) {
        exit 0
    }

    if (-not (Test-Path -LiteralPath $cloudflaredPath -PathType Leaf)) {
        $downloadUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        $temporaryBinary = "$cloudflaredPath.download"
        Invoke-WebRequest -Uri $downloadUrl -OutFile $temporaryBinary `
            -TimeoutSec 180
        $signature = Get-AuthenticodeSignature -FilePath $temporaryBinary
        if ($signature.Status -ne "Valid" -or
            $signature.SignerCertificate.Subject -notmatch "Cloudflare") {
            Remove-Item -LiteralPath $temporaryBinary -Force `
                -ErrorAction SilentlyContinue
            throw "The downloaded cloudflared signature did not verify as Cloudflare."
        }
        Move-Item -LiteralPath $temporaryBinary -Destination $cloudflaredPath `
            -Force
    }

    $existingSignature = Get-AuthenticodeSignature -FilePath $cloudflaredPath
    if ($existingSignature.Status -ne "Valid" -or
        $existingSignature.SignerCertificate.Subject -notmatch "Cloudflare") {
        throw "The cloudflared executable signature did not verify as Cloudflare."
    }

    $accounts = @(
        [pscustomobject]@{ username = "admin"; role = "admin"; full_name = "System Administrator" },
        [pscustomobject]@{ username = "coordinator"; role = "coordinator"; full_name = "Main Coordinator" },
        [pscustomobject]@{ username = "driver1"; role = "driver"; full_name = "Driver One" },
        [pscustomobject]@{ username = "driver2"; role = "driver"; full_name = "Driver Two" },
        [pscustomobject]@{ username = "requester_test"; role = "requester"; full_name = "Test Requester" }
    )
    foreach ($account in $accounts) {
        $account | Add-Member -NotePropertyName password `
            -NotePropertyValue (New-TestPassword)
    }
    $env:LHC_TRANSPORT_TEST_ACCOUNTS = $accounts | ConvertTo-Json -Compress
    $env:TRANSPORT_DB_PATH = $databasePath
    $pythonSource = @'
import json
import os
import database

conn = database.get_conn()
try:
    booking_count = conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"]
    if booking_count:
        raise RuntimeError("The test database contains bookings; refusing to publish it.")

    accounts = json.loads(os.environ["LHC_TRANSPORT_TEST_ACCOUNTS"])
    existing = {
        row["username"]: row["role"]
        for row in conn.execute("SELECT username, role FROM users").fetchall()
    }
    expected = {
        "admin": "admin",
        "coordinator": "coordinator",
        "driver1": "driver",
        "driver2": "driver",
    }
    if any(existing.get(username) != role for username, role in expected.items()):
        raise RuntimeError("The expected initial demo accounts were not found.")
    if any(username not in expected and username != "requester_test"
           for username in existing):
        raise RuntimeError("Unexpected user accounts exist; refusing to publish them.")
    if existing.get("requester_test") not in (None, "requester"):
        raise RuntimeError("The requester_test account has an unexpected role.")

    for account in accounts:
        password_hash = database.hash_password(account["password"])
        if account["username"] in existing:
            conn.execute(
                "UPDATE users SET password_hash=?, active=1 WHERE username=?",
                (password_hash, account["username"]),
            )
        else:
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role)"
                " VALUES (?, ?, ?, ?)",
                (account["username"], password_hash, account["full_name"],
                 account["role"]),
            )
    conn.commit()
finally:
    conn.close()
'@
    $pythonSource = "import os, sys`nsys.path.insert(0, os.environ['LHC_TRANSPORT_APP_ROOT'])`n" +
        $pythonSource

    Push-Location $installRoot
    try {
        $env:LHC_TRANSPORT_APP_ROOT = $installRoot
        Set-Content -LiteralPath $temporaryScript -Value $pythonSource `
            -Encoding UTF8
        & $pythonPath $temporaryScript
        if ($LASTEXITCODE -ne 0) {
            throw "Could not secure the demo accounts for public testing."
        }
    } finally {
        Pop-Location
        Remove-Item Env:\LHC_TRANSPORT_APP_ROOT `
            -ErrorAction SilentlyContinue
        Remove-Item Env:\LHC_TRANSPORT_TEST_ACCOUNTS `
            -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $temporaryScript -Force `
            -ErrorAction SilentlyContinue
    }

    $tunnelProcess = Start-Process -FilePath $cloudflaredPath `
        -ArgumentList @("tunnel", "--no-autoupdate", "--url",
            "http://127.0.0.1:5001") `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog
    Set-Content -LiteralPath $pidPath -Value $tunnelProcess.Id `
        -Encoding ASCII

    $url = $null
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        Start-Sleep -Seconds 2
        $tunnelProcess.Refresh()
        if ($tunnelProcess.HasExited) {
            throw "cloudflared stopped unexpectedly. Review $stderrLog"
        }
        foreach ($logPath in @($stdoutLog, $stderrLog)) {
            if (Test-Path -LiteralPath $logPath) {
                $match = Select-String -LiteralPath $logPath `
                    -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" `
                    -AllMatches | Select-Object -Last 1
                if ($match) {
                    $url = $match.Matches.Value | Select-Object -Last 1
                    break
                }
            }
        }
        if ($url) {
            break
        }
    }
    if (-not $url) {
        throw "No temporary HTTPS URL appeared within three minutes. Review $stderrLog"
    }

    $accessLines = @(
        "LHC Transport - temporary public test access",
        "",
        "Temporary APK download URL:",
        "$url/download/android",
        "",
        "Android app server URL:",
        $url,
        "",
        "Demo accounts. Each password is random and unique:",
        ($accounts | ForEach-Object { "$($_.role): $($_.username) / $($_.password)" }),
        "",
        "Cloudflare relays traffic while the tunnel is running.",
        "Do not enter real or sensitive booking data.",
        "The URL stops working when cloudflared is stopped."
    )
    Set-Content -LiteralPath $accessPath -Value $accessLines -Encoding UTF8

    $verified = $false
    $verificationDetails = [System.Collections.Generic.List[string]]::new()
    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        Start-Sleep -Seconds 2
        try {
            $response = Invoke-WebRequest `
                -Uri "$url/download/android" -TimeoutSec 10 -UseBasicParsing
            if ($response.StatusCode -eq 200 -and
                $response.Headers["Content-Disposition"] -like "*LHC-Transport.apk*") {
                $verified = $true
                break
            }
            $verificationDetails.Add(
                "Attempt $($attempt + 1): HTTP $($response.StatusCode); Content-Disposition: $($response.Headers['Content-Disposition'])")
        } catch {
            $verificationDetails.Add(
                "Attempt $($attempt + 1): $($_.Exception.Message)")
        }
    }
    if (-not $verified) {
        Set-Content -LiteralPath $verificationLog -Value @(
            "Temporary APK URL: $url/download/android",
            "",
            $verificationDetails
        ) -Encoding UTF8
        throw "The tunnel URL started, but the APK download did not verify. Diagnostic logs will be sent securely if Tailscale is available."
    }

    [System.Windows.Forms.MessageBox]::Show(
        "The temporary public HTTPS link is running and the APK download returned HTTP 200." +
        "`r`n`r`nThe link and demo passwords are in:" +
        "`r`n$accessPath" +
        "`r`n`r`nDo not use real or sensitive booking data.",
        "LHC Transport public test is ready",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    Start-Process notepad.exe -ArgumentList "`"$accessPath`""

    $tailscalePath = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path -LiteralPath $tailscalePath -PathType Leaf) {
        $reportChoice = [System.Windows.Forms.MessageBox]::Show(
            "Send only the public APK URL to the developer PC over encrypted Tailscale file transfer so it can be verified?" +
            "`r`n`r`nNo usernames, passwords, booking data, or app files will be sent.",
            "Verify the public APK link",
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($reportChoice -eq [System.Windows.Forms.DialogResult]::Yes) {
            $reportPath = Join-Path $env:TEMP `
                "LHC-Transport-Public-Test-URL.txt"
            try {
                Set-Content -LiteralPath $reportPath -Encoding ASCII `
                    -Value @($url, "$url/download/android")
                & $tailscalePath file cp --name `
                    "LHC-Transport-Public-Test-URL.txt" `
                    $reportPath "desktop-gcpvv4t:"
                if ($LASTEXITCODE -ne 0) {
                    throw "Tailscale URL report transfer failed."
                }
            } catch {
                [System.Windows.Forms.MessageBox]::Show(
                    "The public test is still running, but the URL report could not be sent: $($_.Exception.Message)",
                    "Could not report test URL",
                    [System.Windows.Forms.MessageBoxButtons]::OK,
                    [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
            } finally {
                Remove-Item -LiteralPath $reportPath -Force `
                    -ErrorAction SilentlyContinue
            }
        }
    }
} catch {
    if ($tunnelProcess) {
        $tunnelProcess.Refresh()
        if (-not $tunnelProcess.HasExited) {
            Stop-Process -Id $tunnelProcess.Id `
                -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $pidPath -Force `
        -ErrorAction SilentlyContinue
    $diagnosticsSent = $false
    $tailscalePath = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path -LiteralPath $tailscalePath -PathType Leaf) {
        $diagnosticFiles = @($stderrLog, $stdoutLog, $verificationLog) |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
        if ($diagnosticFiles.Count -gt 0) {
            $diagnosticsSent = $true
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            foreach ($diagnosticFile in $diagnosticFiles) {
                $remoteName = "LHC-Transport-$stamp-$([System.IO.Path]::GetFileName($diagnosticFile))"
                & $tailscalePath file cp --name $remoteName `
                    $diagnosticFile "desktop-gcpvv4t:" | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    $diagnosticsSent = $false
                }
            }
        }
    }
    $errorMessage = $_.Exception.Message
    if ($diagnosticsSent) {
        $errorMessage += "`r`n`r`nDiagnostic logs were sent to the developer PC over encrypted Tailscale file transfer."
    } elseif (Test-Path -LiteralPath $verificationLog -PathType Leaf) {
        $errorMessage += "`r`n`r`nSee $verificationLog and $stderrLog for details."
    }
    [System.Windows.Forms.MessageBox]::Show(
        $errorMessage,
        "LHC Transport public test needs attention",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
    if (Test-Path -LiteralPath $accessPath -PathType Leaf) {
        Start-Process notepad.exe -ArgumentList "`"$accessPath`""
    }
}
