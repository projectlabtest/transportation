$ErrorActionPreference = "Stop"

$downloads = Join-Path $HOME "Downloads"
$archive = Join-Path $downloads `
    "LHC-Transport-Full-Server-20261027-160702.zip"
$extraction = Join-Path $downloads `
    "LHC-Transport-Full-Server-20261027-160702"

if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
    throw "Deployment archive not found at $archive. Receive the Tailscale file first."
}

Expand-Archive -LiteralPath $archive -DestinationPath $extraction -Force
$installer = Join-Path $extraction "INSTALL-NEW-SERVER.ps1"
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "The deployment archive did not contain its installer."
}

& $installer -TargetRoot (Join-Path $HOME "LHCTransport")
