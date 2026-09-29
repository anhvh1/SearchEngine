$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    & (Join-Path $PSScriptRoot 'verify.ps1')
    $bundle = Join-Path $projectRoot ('dist\MilestoneSearch-project-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $bundle -Force | Out-Null
    foreach ($folder in @('backend','web','tests','scripts')) {
        Get-ChildItem -LiteralPath (Join-Path $projectRoot $folder) -Recurse -File |
            Where-Object { $_.FullName -notmatch '[\\/](__pycache__|[^\\/]*\.egg-info)[\\/]' } |
            ForEach-Object {
                $relative = $_.FullName.Substring($projectRoot.Length + 1)
                $target = Join-Path $bundle $relative
                New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
                Copy-Item -LiteralPath $_.FullName -Destination $target
            }
    }
    foreach ($folder in @('plugins\MilestoneSearch','plugins\MilestoneSearch.EventServer','plugins\MilestoneSearch.Management','plugins\MilestoneSearch.SmartClient','plugins\MilestoneSearch.Tests')) {
        Get-ChildItem -LiteralPath (Join-Path $projectRoot $folder) -File | ForEach-Object {
            $target = Join-Path $bundle ($_.FullName.Substring($projectRoot.Length + 1))
            New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target
        }
    }
    New-Item -ItemType Directory -Path (Join-Path $bundle 'docs') -Force | Out-Null
    foreach ($file in @('README.md','pyproject.toml','.gitignore','docs\DEPLOYMENT.md','docs\ACCEPTANCE.md','docs\openapi.json')) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination (Join-Path $bundle $file)
    }
    Compress-Archive -Path (Join-Path $bundle '*') -DestinationPath ($bundle+'.zip')
    Write-Output "Project bundle: $bundle.zip"
} finally { Pop-Location }
