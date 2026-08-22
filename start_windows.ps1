[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvironmentPython = Join-Path $RepoRoot ".venv-windows-v1\Scripts\python.exe"
$MainScript = Join-Path $RepoRoot "main.py"
$FrontendIndex = Join-Path $RepoRoot "webui\frontend\dist\index.html"

if (-not (Test-Path -LiteralPath $EnvironmentPython -PathType Leaf)) {
    throw "Windows v1 environment is missing. Run .\setup_windows.ps1 first."
}
if (-not (Test-Path -LiteralPath $FrontendIndex -PathType Leaf)) {
    throw "Built Web assets are missing. Run .\setup_windows.ps1 first."
}

$Arguments = @($MainScript, "--port", $Port)
if ($NoBrowser) { $Arguments += "--no-browser" }
& $EnvironmentPython @Arguments
exit $LASTEXITCODE
