param(
    [Parameter(Mandatory = $true)]
    [string]$Config,
    [switch]$Resume,
    [switch]$AllowSimindExecution,
    [switch]$NoFinalize
)

# Compatibility entry point only.  It owns no case range, path, /NN, SMC,
# completion or resume defaults.  All effective values and validation rules
# come from the same PipelineConfig/PipelineRunner used by the GUI and CLI.
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = (Resolve-Path -LiteralPath $Config).Path
$env:PYTHONPATH = Join-Path $repoRoot "src"
$cliArgs = @("-m", "cli", "run", "--config", $configPath)

if ($Resume) {
    $cliArgs += "--resume"
}
if ($AllowSimindExecution) {
    $cliArgs += "--allow-simind-execution"
}
if ($NoFinalize) {
    $cliArgs += "--no-finalize"
}

Push-Location $repoRoot
try {
    & python @cliArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
