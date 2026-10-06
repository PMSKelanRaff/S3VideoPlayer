<#
.SYNOPSIS
    Read-only extraction of the legacy VPCI PCI calculation's reference tables
    ([Distresses], [InterpolationCurve]) from VPCI_Config.accdb into CSV files.

.DESCRIPTION
    This is step 1 of regenerating legacy_pci_data.py (step 2 is
    csv_to_legacy_pci_data.py in this same directory). Run this only if
    VPCI_Config.accdb's reference data has actually changed -- legacy_pci_data.py
    must not be hand-edited; it should only ever be regenerated from the database.

    Requires the 32-bit "Microsoft Access Driver (*.mdb, *.accdb)" ODBC driver (no
    ACE OLEDB provider is required/assumed). Because that driver is 32-bit-only on
    most machines, this script must be run under 32-bit PowerShell:

        & "$env:windir\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" `
            -File tools\extract_legacy_pci_data.ps1 -OutDir tools\_extracted

    Performs SELECT-only queries -- never writes to the database.

.PARAMETER DbPath
    Path to VPCI_Config.accdb. Defaults to the production location this project's
    data was extracted from on 2026-10-06.

.PARAMETER OutDir
    Directory to write Distresses.csv and InterpolationCurve.csv into. Created if
    it doesn't exist.
#>
param(
    [string]$DbPath = "P:\Remote PCI\VPCI Application\Resources\VPCI_Config.accdb",
    [string]$OutDir = "$PSScriptRoot\_extracted"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir | Out-Null
}

function Connect-Legacy {
    param([string]$path)
    $drivers = @("Microsoft Access Driver (*.mdb, *.accdb)", "Microsoft Access Driver (*.mdb)")
    foreach ($driver in $drivers) {
        try {
            $connStr = "Driver={$driver};DBQ=$path;"
            $conn = New-Object System.Data.Odbc.OdbcConnection($connStr)
            $conn.Open()
            Write-Output "Connected using driver: $driver"
            return $conn
        } catch {
            Write-Output "Driver '$driver' failed: $($_.Exception.Message)"
        }
    }
    throw "Could not connect with any known Access ODBC driver. Are you running " +
          "32-bit PowerShell (SysWOW64)? Is the Microsoft Access Database Engine installed?"
}

function Export-LegacyTable {
    param($Connection, [string]$TableName, [string]$OutCsv)
    $cmd = $Connection.CreateCommand()
    $cmd.CommandText = "SELECT * FROM [$TableName]"
    $adapter = New-Object System.Data.Odbc.OdbcDataAdapter($cmd)
    $dt = New-Object System.Data.DataTable
    [void]$adapter.Fill($dt)
    $dt | Export-Csv -Path $OutCsv -NoTypeInformation
    Write-Output "Wrote $($dt.Rows.Count) rows to $OutCsv"
}

$conn = Connect-Legacy -path $DbPath
try {
    Export-LegacyTable -Connection $conn -TableName "Distresses" -OutCsv (Join-Path $OutDir "Distresses.csv")
    Export-LegacyTable -Connection $conn -TableName "InterpolationCurve" -OutCsv (Join-Path $OutDir "InterpolationCurve.csv")
} finally {
    $conn.Close()
}

Write-Output ""
Write-Output "Next step: python tools\csv_to_legacy_pci_data.py --csv-dir `"$OutDir`""
