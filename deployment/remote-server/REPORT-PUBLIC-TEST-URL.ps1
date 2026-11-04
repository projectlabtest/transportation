$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms

$installRoot = Join-Path $HOME "LHCTransport"
$accessPath = Join-Path $installRoot "PUBLIC-TEST-ACCESS.txt"
$reportPath = Join-Path $env:TEMP "LHC-Transport-Public-Test-URL.txt"
$tailscale = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
$destination = "desktop-gcpvv4t:"

try {
    if (-not (Test-Path -LiteralPath $accessPath -PathType Leaf)) {
        throw "The public test access note was not found at $accessPath"
    }
    if (-not (Test-Path -LiteralPath $tailscale -PathType Leaf)) {
        throw "The Tailscale command-line tool was not found."
    }

    $content = Get-Content -LiteralPath $accessPath -Raw
    $urlMatch = [regex]::Match(
        $content, "https://[a-z0-9-]+\.trycloudflare\.com",
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $urlMatch.Success) {
        throw "No Cloudflare test URL was found in the access note."
    }
    $baseUrl = $urlMatch.Value
    $apkUrl = "$baseUrl/download/android"

    $response = Invoke-WebRequest -Uri $apkUrl -TimeoutSec 15 `
        -UseBasicParsing
    if ($response.StatusCode -ne 200 -or
        $response.Headers["Content-Disposition"] -notlike "*LHC-Transport.apk*") {
        throw "The URL did not return the expected Android APK."
    }

    Set-Content -LiteralPath $reportPath -Encoding ASCII -Value @(
        $baseUrl,
        $apkUrl
    )
    $choice = [System.Windows.Forms.MessageBox]::Show(
        "Send only the public test URL to the developer PC over encrypted Tailscale file transfer so the APK link can be verified?" +
        "`r`n`r`nNo usernames, passwords, booking data, or app files will be sent.",
        "Share the temporary test URL",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($choice -ne [System.Windows.Forms.DialogResult]::Yes) {
        exit 0
    }

    & $tailscale file cp $reportPath $destination
    if ($LASTEXITCODE -ne 0) {
        throw "Tailscale could not send the URL report."
    }

    [System.Windows.Forms.MessageBox]::Show(
        "Sent only the verified public URL. No credentials were shared.",
        "Public test URL sent",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "Could not report the test URL",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
} finally {
    Remove-Item -LiteralPath $reportPath -Force `
        -ErrorAction SilentlyContinue
}
