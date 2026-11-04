$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms

$ruleName = "LHCTransport-LocalApkDownload"
$pidPath = Join-Path $env:LOCALAPPDATA `
    "LHCTransport\local-apk-server.pid"

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Start-Process -FilePath "powershell.exe" -Verb RunAs `
            -ArgumentList @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", "`"$PSCommandPath`""
            )
        exit 0
    }

    if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
        $serverInfo = Get-Content -LiteralPath $pidPath -Raw |
            ConvertFrom-Json
        $serverId = [int]$serverInfo.process_id
        $serverScript = [string]$serverInfo.script_path
        $pythonPath = [string]$serverInfo.python_path
        $process = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $serverId" `
            -ErrorAction SilentlyContinue
        if ($process) {
            if ($process.ExecutablePath -ne $pythonPath -or
                -not $process.CommandLine -or
                -not $process.CommandLine.Contains($serverScript)) {
                throw "The saved process ID does not match the local APK server; it was not stopped."
            }
            Stop-Process -Id $serverId -ErrorAction Stop
        }
        Remove-Item -LiteralPath $pidPath -Force
    }

    $rule = Get-NetFirewallRule -Name $ruleName `
        -ErrorAction SilentlyContinue
    if ($rule) {
        Remove-NetFirewallRule -InputObject $rule
    }
    [System.Windows.Forms.MessageBox]::Show(
        "The local APK server and its local-network firewall rule were stopped.",
        "LHC Transport local download stopped",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message,
        "LHC Transport stop needs attention",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
}
