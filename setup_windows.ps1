[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvironmentRoot = Join-Path $RepoRoot ".venv-windows-v1"
$EnvironmentPython = Join-Path $EnvironmentRoot "Scripts\python.exe"
$FrontendRoot = Join-Path $RepoRoot "webui\frontend"
$LockFile = Join-Path $RepoRoot "requirements-windows-v1.lock.txt"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher (py.exe) is required. Install 64-bit Python 3.11, then rerun setup_windows.ps1."
}

& py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11), sys.version"
if ($LASTEXITCODE -ne 0) {
    throw "A usable Python 3.11 installation was not found."
}

if (-not (Test-Path -LiteralPath $EnvironmentPython -PathType Leaf)) {
    & py -3.11 -m venv $EnvironmentRoot
    if ($LASTEXITCODE -ne 0) { throw "Failed to create $EnvironmentRoot" }
}

& $EnvironmentPython -m pip install --upgrade "pip==26.0.1"
if ($LASTEXITCODE -ne 0) { throw "Failed to install the locked pip version." }
& $EnvironmentPython -m pip install --requirement $LockFile
if ($LASTEXITCODE -ne 0) { throw "Failed to install Python dependencies." }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is required to build the Web workbench. Install Node.js 22.19 or newer."
}
& node -e "const [major, minor] = process.versions.node.split('.').map(Number); if (major < 22 || (major === 22 && minor < 19)) process.exit(1)"
if ($LASTEXITCODE -ne 0) { throw "Node.js 22.19 or newer is required." }

Push-Location $FrontendRoot
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
}
finally {
    Pop-Location
}

& $EnvironmentPython -c "import fastapi, numpy, scipy, PyQt6, uvicorn; print('Windows v1 Python environment OK')"
if ($LASTEXITCODE -ne 0) { throw "Python environment smoke check failed." }

Write-Host "Setup complete. Start with .\start_windows.ps1" -ForegroundColor Green
