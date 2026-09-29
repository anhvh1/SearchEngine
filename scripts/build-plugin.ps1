param([string]$SdkPath = 'C:\Program Files\Milestone\XProtect Smart Client')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $SdkPath 'VideoOS.Platform.dll'))) { throw 'MIP SDK assembly not found.' }
    $projects = @('MilestoneSearch.EventServer','MilestoneSearch.Management','MilestoneSearch.SmartClient')
    foreach ($project in $projects) {
        dotnet build "plugins/$project/$project.csproj" -c Release "-p:SdkPath=$SdkPath" -v:minimal
        if ($LASTEXITCODE -ne 0) { throw "$project build failed." }
    }
    dotnet run --project plugins/MilestoneSearch.Tests/MilestoneSearch.Tests.csproj "-p:SdkPath=$SdkPath"
    if ($LASTEXITCODE -ne 0) { throw 'Plugin tests failed.' }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    foreach ($project in $projects) {
        $output = Join-Path $projectRoot "plugins\$project\bin\Release\net48"
        $package = Join-Path $projectRoot ("dist\$project-$stamp")
        New-Item -ItemType Directory -Path $package -Force | Out-Null
        Get-ChildItem -LiteralPath $output -File | Where-Object { $_.Name -notlike 'VideoOS.*' -and $_.Extension -in '.dll','.def','.config' } | Copy-Item -Destination $package
        if (Test-Path -LiteralPath (Join-Path $output 'runtimes')) { Copy-Item -LiteralPath (Join-Path $output 'runtimes') -Destination $package -Recurse }
        Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\DEPLOYMENT.md') -Destination $package
        Compress-Archive -Path (Join-Path $package '*') -DestinationPath ($package + '.zip')
        Write-Output "Package: $package.zip"
    }
} finally { Pop-Location }
