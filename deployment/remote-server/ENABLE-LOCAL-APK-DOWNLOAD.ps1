param(
    [string]$InstallRoot
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms

$serverScript = Join-Path $PSScriptRoot "LOCAL-APK-DOWNLOAD-SERVER.py"
$port = 8765
$ruleName = "LHCTransport-LocalApkDownload"
$dataRoot = Join-Path $env:LOCALAPPDATA "LHCTransport"
$pidPath = Join-Path $dataRoot "local-apk-server.pid"
$stdoutLog = Join-Path $dataRoot "local-apk-server.log"
$stderrLog = Join-Path $dataRoot "local-apk-server-error.log"
$serverProcess = $null
$firewallRuleCreated = $false
$pidFileCreated = $false

function Test-ApkEndpoint {
    param([string]$Url)

    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.Method = "HEAD"
    $request.Timeout = 3000
    $request.ReadWriteTimeout = 3000
    $request.Proxy = $null
    $response = $request.GetResponse()
    try {
        return $response.StatusCode -eq [System.Net.HttpStatusCode]::OK -and
            $response.Headers["Content-Disposition"] -like "*LHC-Transport.apk*"
    } finally {
        $response.Close()
    }
}

function Find-TransportInstallRoot {
    param([string]$RequestedRoot)

    $profiles = @()
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        $profiles = @([System.IO.Path]::GetFullPath($RequestedRoot))
    } else {
        $profiles = @(
            (Join-Path $HOME "LHCTransport")
            Get-ChildItem -LiteralPath "C:\Users" -Directory -Force `
                -ErrorAction Stop |
                ForEach-Object { Join-Path $_.FullName "LHCTransport" }
        )
    }

    $validRoots = @(
        $profiles |
            Where-Object {
                (Test-Path -LiteralPath (
                    Join-Path $_ ".venv\Scripts\python.exe"
                ) -PathType Leaf) -and
                (Test-Path -LiteralPath (
                    Join-Path $_ "driver-app\app\build\outputs\apk\debug\app-debug.apk"
                ) -PathType Leaf)
            } |
            ForEach-Object { [System.IO.Path]::GetFullPath($_) } |
            Select-Object -Unique
    )
    if ($validRoots.Count -eq 1) {
        return $validRoots[0]
    }
    if ($validRoots.Count -gt 1) {
        throw "More than one LHC Transport installation was found. Run the helper from the account that owns the server installation."
    }
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        throw "The LHC Transport installation at '$RequestedRoot' is missing its Python environment or APK."
    }
    throw "Could not find LHC Transport in a Windows user profile. The installation must contain .venv\Scripts\python.exe and the built APK."
}

try {
    $installRoot = Find-TransportInstallRoot -RequestedRoot $InstallRoot
    $pythonPath = Join-Path $installRoot ".venv\Scripts\python.exe"
    $apkPath = Join-Path $installRoot `
        "driver-app\app\build\outputs\apk\debug\app-debug.apk"

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Start-Process -FilePath "powershell.exe" -Verb RunAs `
            -ArgumentList @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", "`"$PSCommandPath`"",
                "-InstallRoot", "`"$installRoot`""
            )
        exit 0
    }

    foreach ($path in @($pythonPath, $apkPath, $serverScript)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required file not found: $path"
        }
    }

    $addresses = @(
        Get-NetAdapter -Physical -State Up -ErrorAction Stop |
            ForEach-Object {
                Get-NetIPAddress -InterfaceIndex $_.ifIndex `
                    -AddressFamily IPv4 -ErrorAction SilentlyContinue
            } |
            Where-Object {
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*"
            } |
            Select-Object -ExpandProperty IPAddress -Unique
    )
    if ($addresses.Count -eq 0) {
        throw "No active Wi-Fi/Ethernet IPv4 address was found."
    }

    $existingListener = Get-NetTCPConnection -LocalPort $port `
        -State Listen -ErrorAction SilentlyContinue
    if ($existingListener) {
        throw "Port $port is already in use; no server or firewall rule was changed."
    }

    New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
    New-NetFirewallRule -Name $ruleName `
        -DisplayName "LHC Transport local APK download" `
        -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $port -RemoteAddress LocalSubnet -Profile Any `
        -Description "Serves only the LHC Transport APK to devices on the local subnet." |
        Out-Null
    $firewallRuleCreated = $true

    $serverProcess = Start-Process -FilePath $pythonPath `
        -ArgumentList @(
            "-u", "`"$serverScript`"", "`"$apkPath`"", "$port"
        ) -WorkingDirectory $installRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog
    Set-Content -LiteralPath $pidPath -Value (
        [pscustomobject]@{
            process_id = $serverProcess.Id
            script_path = [System.IO.Path]::GetFullPath($serverScript)
            install_root = $installRoot
            python_path = $pythonPath
        } | ConvertTo-Json
    ) -Encoding UTF8
    $pidFileCreated = $true

    $ready = $false
    $lastCheckError = "No response was received."
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Seconds 1
        $serverProcess.Refresh()
        if ($serverProcess.HasExited) {
            throw "The local APK server exited. Review $stderrLog."
        }
        try {
            if (Test-ApkEndpoint -Url "http://127.0.0.1:$port/download/android") {
                $ready = $true
                break
            }
        } catch {
            $lastCheckError = $_.Exception.Message
        }
    }
    if (-not $ready) {
        throw "The local APK download did not verify at 127.0.0.1. $lastCheckError Review $stderrLog."
    }

    $urls = @()
    foreach ($address in $addresses) {
        $url = "http://$address`:$port/download/android"
        try {
            if (Test-ApkEndpoint -Url $url) {
                $urls += $url
            }
        } catch {
            $lastCheckError = "$url : $($_.Exception.Message)"
        }
    }
    if ($urls.Count -eq 0) {
        throw "The server responds on loopback but not on any advertised LAN address. Last check: $lastCheckError Review $stderrLog."
    }

    [System.Windows.Forms.MessageBox]::Show(
        "The APK-only server responded on the listed server-PC network address(es)." +
        "`r`n`r`nOn the phone connected to this same Wi-Fi, open:" +
        "`r`n$($urls -join "`r`n")" +
        "`r`n`r`nOnly the APK is shared, and the firewall rule is limited to the local subnet." +
        "`r`nIf the phone still cannot connect, check the request log at $stdoutLog; a phone request should appear there." +
        "`r`nTo stop sharing, double-click STOP-LOCAL-APK-DOWNLOAD.cmd.",
        "LHC Transport local download is ready",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
} catch {
    $failureMessage = $_.Exception.Message
    if ($serverProcess) {
        try {
            $serverProcess.Refresh()
            if (-not $serverProcess.HasExited) {
                Stop-Process -Id $serverProcess.Id -ErrorAction Stop
            }
        } catch {
            $failureMessage += "`r`nCould not stop the partial server: $($_.Exception.Message)"
        }
    }
    if ($pidFileCreated -and (Test-Path -LiteralPath $pidPath)) {
        Remove-Item -LiteralPath $pidPath -Force
    }
    if ($firewallRuleCreated) {
        $createdRule = Get-NetFirewallRule -Name $ruleName `
            -ErrorAction SilentlyContinue
        if ($createdRule) {
            Remove-NetFirewallRule -InputObject $createdRule
        }
    }
    [System.Windows.Forms.MessageBox]::Show(
        $failureMessage,
        "LHC Transport local download needs attention",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
}
