[CmdletBinding()]
param(
    [switch]$SkipBrowserTests,
    [switch]$SkipRealSimind,
    [string]$Python
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$FrontendRoot = Join-Path $RepoRoot "webui\frontend"
$ManagedPython = Join-Path $RepoRoot ".venv-windows-v1\Scripts\python.exe"
$SimindExe = Join-Path $RepoRoot "simind\simind.exe"
$SmcFile = Join-Path $RepoRoot "simind\ge870_czt.smc"
$ExpectedExeHash = "F984B8753F54B9F671F9FC1BCB2B45461E7CAE8D027376B446DD1ED55A9A8319"
$ExpectedSmcHash = "4D10EAB246A7A6690663230D2F33AEB3C32F67C598AF36B56D1575F0E3551D10"
$VerificationRoot = Join-Path $RepoRoot (".test_tmp\windows-v1-verify-" + (Get-Date -Format "yyyyMMdd-HHmmss"))

if ([string]::IsNullOrWhiteSpace($Python)) { $Python = $ManagedPython }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Managed Python is missing at $Python. Run .\setup_windows.ps1 first, or pass -Python."
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "dist\index.html") -PathType Leaf)) {
    throw "Built Web assets are missing. Run .\setup_windows.ps1 first."
}

function Invoke-Checked {
    param([string]$Label, [scriptblock]$Action)
    Write-Host "`n== $Label ==" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

function Assert-FileHash {
    param([string]$Path, [string]$Expected)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required runtime file is missing: $Path" }
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($Actual -ne $Expected) { throw "Runtime hash mismatch for $Path. Expected $Expected; found $Actual" }
    Write-Host "Validated SHA-256: $Path" -ForegroundColor Green
}

function New-CliConfig {
    param(
        [string]$RunId,
        [string]$Mode,
        [string]$CohortMode,
        [int]$PositiveCases,
        [int]$NegativeCases,
        [int]$Nn
    )
    $ConfigPath = Join-Path $VerificationRoot "$RunId.json"
    $InitOutput = & $Python -m cli init --run-id $RunId --runs-root (Join-Path $VerificationRoot "runs") `
        --cohort-mode $CohortMode --positive-cases $PositiveCases --negative-cases $NegativeCases `
        --mode $Mode --simind-exe $SimindExe --smc $SmcFile --nn $Nn --workers 1 --output $ConfigPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to create $Mode verification config." }
    if ($InitOutput) { Write-Host ($InitOutput -join [Environment]::NewLine) }
    return $ConfigPath
}

New-Item -ItemType Directory -Path $VerificationRoot -Force | Out-Null
$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $RepoRoot "src"

try {
    Assert-FileHash -Path $SimindExe -Expected $ExpectedExeHash
    Assert-FileHash -Path $SmcFile -Expected $ExpectedSmcHash

    # The label intentionally records the equivalent portable command: python -m pytest.
    Invoke-Checked "Python full suite (python -m pytest)" { & $Python -m pytest -q }
    Invoke-Checked "Ruff active Windows v1 path" {
        & $Python -m ruff check `
            main.py `
            src\cli.py `
            src\windows_launcher.py `
            src\core\windows_v1.py `
            src\core\limited_activity.py `
            src\core\phantom_generator.py `
            src\core\hybrid_v2_adapter.py `
            src\core\interfile_writer.py `
            src\core\windows_runtime.py `
            src\pipeline\provenance.py `
            src\pipeline\runner.py `
            src\pipeline\qc.py `
            src\pipeline\simind.py `
            scripts\freeze_windows_v1_run.py `
            scripts\compare_windows_v1_runs.py `
            webui\server\app.py `
            webui\server\fsapi.py
    }

    Push-Location $FrontendRoot
    try {
        Invoke-Checked "Frontend lint" { & npm.cmd run lint }
        Invoke-Checked "Frontend unit tests" { & npm.cmd run test:unit }
        Invoke-Checked "Frontend build" { & npm.cmd run build }
        if (-not $SkipBrowserTests) {
            Invoke-Checked "Frontend E2E" { & npm.cmd run test:e2e }
            Invoke-Checked "Frontend accessibility" { & npm.cmd run test:a11y }
            Invoke-Checked "Frontend visual regression" { & npm.cmd run test:visual }
        }
    }
    finally {
        Pop-Location
    }

    Write-Host "`n== Loopback launcher smoke ==" -ForegroundColor Cyan
    $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $Listener.Start()
    $Port = ([System.Net.IPEndPoint]$Listener.LocalEndpoint).Port
    $Listener.Stop()
    $LauncherOut = Join-Path $VerificationRoot "launcher.stdout.log"
    $LauncherErr = Join-Path $VerificationRoot "launcher.stderr.log"
    $Launcher = Start-Process -FilePath $Python -ArgumentList @((Join-Path $RepoRoot "main.py"), "--port", $Port, "--no-browser") `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $LauncherOut -RedirectStandardError $LauncherErr -PassThru
    try {
        $Ready = $false
        for ($Attempt = 0; $Attempt -lt 100; $Attempt++) {
            if ($Launcher.HasExited) { throw "Launcher exited early. See $LauncherErr" }
            try {
                $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 1
                if ($Health.service -eq "par-s") { $Ready = $true; break }
            }
            catch { Start-Sleep -Milliseconds 100 }
        }
        if (-not $Ready) { throw "Launcher health endpoint did not become ready." }
    }
    finally {
        if (-not $Launcher.HasExited) { Stop-Process -Id $Launcher.Id -Force }
        $Launcher.WaitForExit()
    }

    Write-Host "`n== Prepare and mock state machines ==" -ForegroundColor Cyan
    $PrepareConfig = New-CliConfig -RunId "verify-prepare" -Mode "prepare" -CohortMode "positive_only" -PositiveCases 1 -NegativeCases 0 -Nn 1
    & $Python -m cli run --config $PrepareConfig
    if ($LASTEXITCODE -ne 0) { throw "Prepare verification failed." }

    $MockConfig = New-CliConfig -RunId "verify-mock-negative" -Mode "mock" -CohortMode "true_negative_only" -PositiveCases 0 -NegativeCases 1 -Nn 1
    & $Python -m cli run --config $MockConfig
    if ($LASTEXITCODE -ne 0) { throw "Mock verification failed." }

    if (-not $SkipRealSimind) {
        $Consent = Read-Host "Run the verified two-case native SIMIND acceptance now (NN=10, worker=1)? Type RUN SIMIND to continue"
        if ($Consent -eq "RUN SIMIND") {
            $RealConfig = New-CliConfig -RunId "verify-real-mixed" -Mode "execute" -CohortMode "mixed" -PositiveCases 1 -NegativeCases 1 -Nn 10
            & $Python -m cli run --config $RealConfig --allow-simind-execution
            if ($LASTEXITCODE -ne 0) { throw "Real SIMIND acceptance failed." }
        }
        else {
            Write-Warning "Real SIMIND acceptance was not run; this verification is not release-complete."
        }
    }

    Write-Host "`nWindows v1 verification completed. Evidence: $VerificationRoot" -ForegroundColor Green
}
finally {
    $env:PYTHONPATH = $PreviousPythonPath
}
