$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms

$installRoot = Join-Path $HOME "LHCTransport"
$databasePath = Join-Path $env:LOCALAPPDATA "LHCTransport\transport.db"
$pythonPath = Join-Path $installRoot ".venv\Scripts\python.exe"
$apkPath = Join-Path $installRoot `
    "driver-app\app\build\outputs\apk\debug\app-debug.apk"
$credentialsPath = Join-Path $installRoot "PRIVATE-TEST-ACCESS.txt"
$tailscaleCandidates = @(
    (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Tailscale IPN\tailscale.exe")
)

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

    $tailscale = $tailscaleCandidates |
        Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
        Select-Object -First 1
    if (-not $tailscale) {
        $command = Get-Command tailscale.exe -ErrorAction SilentlyContinue
        if ($command) {
            $tailscale = $command.Source
        }
    }
    if (-not $tailscale) {
        throw "Tailscale CLI was not found on this server."
    }

    $localCheck = Invoke-WebRequest `
        -Uri "http://127.0.0.1:5001/download/android" `
        -TimeoutSec 5 -UseBasicParsing
    if ($localCheck.StatusCode -ne 200 -or
        $localCheck.Headers["Content-Disposition"] -notlike "*LHC-Transport.apk*") {
        throw "The local APK download test did not return the expected file."
    }

    $serveBefore = (& $tailscale serve status 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read Tailscale Serve status: $serveBefore"
    }
    if ($serveBefore -notmatch "No serve config") {
        throw "Tailscale Serve already has a configuration. No routes or passwords were changed. Review: $serveBefore"
    }

    $choice = [System.Windows.Forms.MessageBox]::Show(
        "Enable private HTTPS access for the server's Tailscale network?" +
        "`r`n`r`nThis will replace the initial admin password with a random one," +
        " then serve the app only to authorized devices in this tailnet." +
        "`r`n`r`nIt will not enable public Internet access.",
        "LHC Transport private test setup",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($choice -ne [System.Windows.Forms.DialogResult]::Yes) {
        exit 0
    }

    $alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    $randomBytes = New-Object "System.Byte[]" 20
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($randomBytes)
    } finally {
        $generator.Dispose()
    }
    $password = -join ($randomBytes | ForEach-Object {
        $alphabet[[int]$_ % $alphabet.Length]
    })

    $env:TRANSPORT_DB_PATH = $databasePath
    $env:LHC_TRANSPORT_INITIAL_ADMIN_PASSWORD = $password
    Push-Location $installRoot
    try {
        & $pythonPath -c "import database, os; conn=database.get_conn(); cur=conn.execute('UPDATE users SET password_hash=? WHERE username=? AND role=? AND active=1', (database.hash_password(os.environ['LHC_TRANSPORT_INITIAL_ADMIN_PASSWORD']), 'admin', 'admin')); conn.commit(); conn.close(); (_ for _ in ()).throw(RuntimeError('Expected exactly one active admin account')) if cur.rowcount != 1 else None"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not set a unique password for the initial admin account."
        }
    } finally {
        Pop-Location
        Remove-Item Env:\LHC_TRANSPORT_INITIAL_ADMIN_PASSWORD `
            -ErrorAction SilentlyContinue
    }

    $status = (& $tailscale status --json 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Admin password was secured, but the Tailscale device name could not be read: $status"
    }
    $statusObject = $status | ConvertFrom-Json
    $dnsName = [string]$statusObject.Self.DNSName
    $dnsName = $dnsName.TrimEnd(".")
    if ([string]::IsNullOrWhiteSpace($dnsName)) {
        throw "Admin password was secured, but this server has no Tailscale HTTPS name."
    }

    $url = "https://$dnsName/download/android"
    Set-Content -LiteralPath $credentialsPath -Encoding UTF8 -Value @(
        "LHC Transport - private test access",
        "",
        "Download the Android APK:",
        $url,
        "",
        "Android app server URL: https://$dnsName",
        "Admin username: admin",
        "New admin password: $password",
        "",
        "Private to devices authorized on this Tailscale network.",
        "Delete this file after safely saving the password."
    )

    $serveResult = (& $tailscale serve --bg 5001 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "The admin password was secured, but Tailscale Serve could not start: $serveResult"
    }

    $verified = $false
    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        Start-Sleep -Seconds 2
        try {
            $response = Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing
            if ($response.StatusCode -eq 200 -and
                $response.Headers["Content-Disposition"] -like "*LHC-Transport.apk*") {
                $verified = $true
                break
            }
        } catch {
        }
    }
    if (-not $verified) {
        $serveAfter = (& $tailscale serve status 2>&1 | Out-String)
        throw "HTTPS was configured, but the APK download did not verify yet. Check the Tailscale status and retry the URL: $serveAfter"
    }

    [System.Windows.Forms.MessageBox]::Show(
        "Private HTTPS setup succeeded. The Android APK download returned HTTP 200." +
        "`r`n`r`nThe URL and the new admin password are in the private file:" +
        "`r`n$credentialsPath",
        "LHC Transport is ready",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    Start-Process notepad.exe -ArgumentList "`"$credentialsPath`""
} catch {
    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "LHC Transport setup needs attention",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
    if (Test-Path -LiteralPath $credentialsPath -PathType Leaf) {
        Start-Process notepad.exe -ArgumentList "`"$credentialsPath`""
    }
}
