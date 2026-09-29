# Restores a Milestone Surveillance backup into a separate, read-only analysis database on the local SQL Server.
# Run once from an elevated PowerShell. The production Milestone database is never touched.
param(
    [string]$Backup = (Join-Path $PSScriptRoot '..\..\MilestoneDB\Surveillance-28092026.bak'),
    [string]$Database = 'Surveillance_Analysis'
)
$ErrorActionPreference = 'Stop'
$sqlcmd = 'C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\SQLCMD.EXE'
$instance = 'C:\Program Files\Microsoft SQL Server\MSSQL16.MSSQLSERVER\MSSQL'

Start-Service MSSQLSERVER
(Get-Service MSSQLSERVER).WaitForStatus('Running', '00:01:00')

# The SQL Server service account reads backups reliably from its own Backup folder.
$local = Join-Path "$instance\Backup" (Split-Path $Backup -Leaf)
Copy-Item -LiteralPath (Resolve-Path $Backup) -Destination $local -Force

$files = & $sqlcmd -S . -E -b -h -1 -W -s '|' -Q "SET NOCOUNT ON; RESTORE FILELISTONLY FROM DISK=N'$local'"
$moves = foreach ($line in $files | Where-Object { $_ -match '\|' }) {
    $parts = $line.Split('|'); $logical = $parts[0]; $type = $parts[2]
    $ext = if ($type -eq 'L') { '_log.ldf' } else { '.mdf' }
    "MOVE N'$logical' TO N'$instance\DATA\$Database$($logical -replace '\W','_')$ext'"
}
& $sqlcmd -S . -E -b -Q "RESTORE DATABASE [$Database] FROM DISK=N'$local' WITH REPLACE, RECOVERY, $($moves -join ', ')"

# Allow the current Windows user to read it from a normal (non-elevated) session.
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& $sqlcmd -S . -E -b -Q "IF SUSER_ID(N'$user') IS NULL CREATE LOGIN [$user] FROM WINDOWS; USE [$Database]; IF USER_ID(N'$user') IS NULL CREATE USER [$user] FOR LOGIN [$user]; ALTER ROLE db_datareader ADD MEMBER [$user]; ALTER DATABASE [$Database] SET READ_ONLY WITH ROLLBACK IMMEDIATE;"
Remove-Item -LiteralPath $local
Write-Output "Restored $Database (read-only) for $user."
