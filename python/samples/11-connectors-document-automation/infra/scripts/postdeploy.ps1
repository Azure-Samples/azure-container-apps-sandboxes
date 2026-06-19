# Thin wrapper — installs the Python deps the orchestration script
# needs into a local venv and runs it.
#
# Doesn't `Activate` the venv (that scopes PATH down to the venv's
# Scripts/ and we lose access to `az.cmd`). Instead we install via
# the venv's pip and run via the venv's python directly, leaving the
# parent session's PATH intact.

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Venv = Join-Path $Here ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

# Sandbox SDK — published on PyPI as `azure-containerapps-sandbox`.
# Pin to the same version scenario 10's receiver uses.
$SandboxSdkPkg = "azure-containerapps-sandbox==0.1.0b1"

if (-not (Test-Path $VenvPython)) {
    Write-Host "==> creating postdeploy venv at $Venv"
    python -m venv $Venv
}

& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet `
    azure-identity `
    httpx `
    $SandboxSdkPkg

& $VenvPython (Join-Path $Here "postdeploy.py") @Args
exit $LASTEXITCODE
