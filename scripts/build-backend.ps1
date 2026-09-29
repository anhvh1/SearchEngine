param([switch]$SkipTests)
# Builds MilestoneSearch.Backend.exe (self-installing Windows service) and a backend source package in dist.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $venv = Join-Path $projectRoot '.build\venv'
    $python = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create build virtual environment.' }
    }
    & $python -m pip install --disable-pip-version-check -q -e '.[test,build]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if (-not $SkipTests) {
        & $python -m pytest -q --tb=short
        if ($LASTEXITCODE -ne 0) { throw 'Python tests failed.' }
    }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $work = Join-Path $projectRoot '.build\pyinstaller'
    & $python -m PyInstaller --noconfirm --clean --onefile --console --name MilestoneSearch.Backend `
        --distpath (Join-Path $work 'out') --workpath (Join-Path $work 'work') --specpath $work `
        --paths (Join-Path $projectRoot 'backend') --add-data ((Join-Path $projectRoot 'web') + ';web') `
        --collect-submodules uvicorn --collect-submodules search_engine --collect-all psycopg_binary `
        --hidden-import win32timezone --hidden-import servicemanager `
        (Join-Path $projectRoot 'packaging\backend_entry.py')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    $exe = Join-Path $work 'out\MilestoneSearch.Backend.exe'
    & $exe status | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Built executable failed to run.' }

    $package = Join-Path $projectRoot "dist\MilestoneSearch.Backend-$stamp"
    New-Item -ItemType Directory -Path $package -Force | Out-Null
    Copy-Item -LiteralPath $exe -Destination $package
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\DEPLOYMENT.md') -Destination $package
    Compress-Archive -Path (Join-Path $package '*') -DestinationPath ($package + '.zip')
    Write-Output "Executable package: $package.zip"

    $source = Join-Path $projectRoot "dist\MilestoneSearch.Backend-source-$stamp"
    foreach ($folder in @('backend', 'web', 'tests', 'packaging')) {
        Get-ChildItem -LiteralPath (Join-Path $projectRoot $folder) -Recurse -File |
            Where-Object { $_.FullName -notmatch '[\\/](__pycache__|[^\\/]*\.egg-info)[\\/]' } |
            ForEach-Object {
                $target = Join-Path $source $_.FullName.Substring($projectRoot.Length + 1)
                New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
                Copy-Item -LiteralPath $_.FullName -Destination $target
            }
    }
    foreach ($file in @('README.md', 'pyproject.toml', '.gitignore', 'scripts\build-backend.ps1', 'scripts\start-backend.ps1',
                        'docs\DEPLOYMENT.md', 'docs\ACCEPTANCE.md', 'docs\openapi.json')) {
        $target = Join-Path $source $file
        New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $target
    }
    Compress-Archive -Path (Join-Path $source '*') -DestinationPath ($source + '.zip')
    Write-Output "Source package: $source.zip"
} finally { Pop-Location }
