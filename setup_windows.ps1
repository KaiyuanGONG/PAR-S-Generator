[CmdletBinding()]
param(
    [string]$Python
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvironmentRoot = Join-Path $RepoRoot ".venv-windows-v1"
$EnvironmentPython = Join-Path $EnvironmentRoot "Scripts\python.exe"
$FrontendRoot = Join-Path $RepoRoot "webui\frontend"
$LockFile = Join-Path $RepoRoot "requirements-windows-v1.lock.txt"

function Test-Python311 {
    param([string]$Candidate)
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { return $false }
    & $Candidate -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and sys.maxsize > 2**32 else 1)"
    return $LASTEXITCODE -eq 0
}

if (-not [string]::IsNullOrWhiteSpace($Python)) {
    $BasePython = (Resolve-Path -LiteralPath $Python).Path
}
else {
    $BasePython = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $Discovered = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $Discovered) { $BasePython = $Discovered.Trim() }
    }
    if (-not $BasePython -and (Get-Command conda -ErrorAction SilentlyContinue)) {
        $CondaEnvironments = (& conda info --envs --json | ConvertFrom-Json).envs
        foreach ($Environment in $CondaEnvironments) {
            $Candidate = Join-Path $Environment "python.exe"
            if (Test-Python311 $Candidate) { $BasePython = $Candidate; break }
        }
    }
    if (-not $BasePython -and (Get-Command python -ErrorAction SilentlyContinue)) {
        $Candidate = (Get-Command python).Source
        if (Test-Python311 $Candidate) { $BasePython = $Candidate }
    }
}

if (-not $BasePython -or -not (Test-Python311 $BasePython)) {
    throw "A usable 64-bit Python 3.11 installation was not found. Pass -Python C:\path\to\python.exe if it is not registered with py.exe or Conda."
}

if (-not (Test-Path -LiteralPath $EnvironmentPython -PathType Leaf)) {
    & $BasePython -m venv $EnvironmentRoot
    if ($LASTEXITCODE -ne 0) { throw "Failed to create $EnvironmentRoot" }
}

& $EnvironmentPython -m pip install --upgrade "pip==26.0.1"
if ($LASTEXITCODE -ne 0) { throw "Failed to install the locked pip version." }
& $EnvironmentPython -m pip install --requirement $LockFile
if ($LASTEXITCODE -ne 0) { throw "Failed to install Python dependencies." }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is required to build the Web workbench. Install a supported release: 22.22.2+, 24.15+, or 26+."
}
& node -e "const [major, minor, patch] = process.versions.node.split('.').map(Number); const atLeast = (m, n, p) => major === m && (minor > n || (minor === n && patch >= p)); const supported = atLeast(22, 22, 2) || atLeast(24, 15, 0) || major >= 26; process.exit(supported ? 0 : 1)"
if ($LASTEXITCODE -ne 0) { throw "Unsupported Node.js version. Use 22.22.2+, 24.15+, or 26+." }

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

& $EnvironmentPython -c "import fastapi, numpy, scipy, PyQt6, uvicorn, websockets; print('Windows v1 Python environment OK')"
if ($LASTEXITCODE -ne 0) { throw "Python environment smoke check failed." }

Write-Host "Setup complete. Start with .\start_windows.ps1" -ForegroundColor Green
