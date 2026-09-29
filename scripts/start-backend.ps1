param([string]$Config = 'config.local.json')
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if (-not (Test-Path -LiteralPath $Config)) { throw 'Create local configuration with python -m search_engine.cli init first.' }
    python -m search_engine.cli serve --config $Config
    if ($LASTEXITCODE -ne 0) { throw 'Backend exited with an error.' }
} finally { Pop-Location }
