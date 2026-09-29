$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    python -m pytest -q --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed.' }
    foreach ($project in @('MilestoneSearch.EventServer','MilestoneSearch.Management','MilestoneSearch.SmartClient')) {
        dotnet build "plugins/$project/$project.csproj" -c Release -v:q
        if ($LASTEXITCODE -ne 0) { throw "$project build failed." }
    }
    dotnet run --project plugins/MilestoneSearch.Tests/MilestoneSearch.Tests.csproj
    if ($LASTEXITCODE -ne 0) { throw 'Plugin tests failed.' }
    node --check web/app.js
    if ($LASTEXITCODE -ne 0) { throw 'JavaScript syntax check failed.' }
} finally { Pop-Location }
