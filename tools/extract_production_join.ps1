<#
.SYNOPSIS
    Read-only extraction of a production VPCI Access database's InputData/VPCIData
    join, for building/refreshing the local differential-test fixture
    (legacy_pci_production_fixture.py).

.DESCRIPTION
    SAFETY: this script NEVER connects to a network/production path directly. You
    must first make your own local copy of the production .accdb (e.g. via
    Copy-Item from the network share) and pass that LOCAL copy's path as -DbPath.
    This script only ever runs SELECT queries (an INNER JOIN read), never INSERT/
    UPDATE/DELETE/ALTER, and the ReadOnly=1 connection flag is set as a second,
    redundant safeguard on top of that.

    Requires 32-bit PowerShell (SysWOW64) -- see extract_legacy_pci_data.ps1's
    header for why.

.PARAMETER DbPath
    Path to a LOCAL copy of the production VPCI database (NOT a network path).

.PARAMETER OutCsv
    Where to write the joined CSV. Feed this into
    tools/csv_to_production_fixture.py next.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$DbPath,
    [string]$OutCsv = "$PSScriptRoot\_extracted\production_join.csv"
)

$ErrorActionPreference = "Stop"

if ($DbPath -like "\\*") {
    throw "DbPath looks like a network path ($DbPath). Copy it to a local file first " +
          "and pass that local path instead -- this script must never open a network " +
          "production database directly."
}

$outDir = Split-Path -Parent $OutCsv
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir | Out-Null
}

$connStr = "Driver={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=$DbPath;ReadOnly=1;"
$conn = New-Object System.Data.Odbc.OdbcConnection($connStr)
$conn.Open()
Write-Host "Connected (ReadOnly=1) to local copy: $DbPath"

# InputData.Chainage and VPCIData.ChainageStart are both populated from the same
# GlobalStorage.CurrentSectionChainage value at commit time in the C# app (see
# Form1.cs RunPCICalcs()/runEnter()) -- confirmed as the correct join key: every
# row joined 1:1 with zero orphans when this was first run (1315 InputData rows,
# 1315 VPCIData rows, 1315 joined rows).
$sql = @"
SELECT
    i.ID AS InputID, v.ID AS VPCIID, i.Road AS Road, i.Chainage AS Chainage,
    i.PotholesLow AS In_PotholesLow, i.PotholesMed AS In_PotholesMed, i.PotholesHigh AS In_PotholesHigh,
    i.PatchingLow AS In_PatchingLow, i.PatchingMed AS In_PatchingMed, i.PatchingHigh AS In_PatchingHigh,
    i.AlligatorLow AS In_AlligatorLow, i.AlligatorMed AS In_AlligatorMed, i.AlligatorHigh AS In_AlligatorHigh,
    i.RuttingMed AS In_RuttingMed, i.RuttingHigh AS In_RuttingHigh,
    i.Ravelling AS In_Ravelling, i.Depression AS In_Depression, i.Disintergration AS In_Disintergration,
    i.Bleeding AS In_Bleeding, i.EdgeBreakup AS In_EdgeBreakup, i.OtherCracking AS In_OtherCracking,
    v.PCI AS V_PCI, v.TotalDeduct AS V_TotalDeduct, v.ColumnGreater5 AS V_ColumnGreater5,
    v.StructureDeductValue AS V_StructureDeductValue, v.structureDeductPercentage AS V_StructureDeductPercentage,
    v.surfaceDeductValue AS V_SurfaceDeductValue, v.surfaceDeductPercentage AS V_SurfaceDeductPercentage,
    v.otherDeductValue AS V_OtherDeductValue
FROM InputData AS i INNER JOIN VPCIData AS v
    ON i.Road = v.Road AND i.Chainage = v.ChainageStart
ORDER BY i.Road, i.Chainage
"@

$cmd = $conn.CreateCommand()
$cmd.CommandText = $sql
$adapter = New-Object System.Data.Odbc.OdbcDataAdapter($cmd)
$dt = New-Object System.Data.DataTable
$n = $adapter.Fill($dt)
Write-Host "Joined rows: $n"

$dt | Export-Csv -Path $OutCsv -NoTypeInformation
Write-Host "Wrote $($dt.Rows.Count) rows to $OutCsv"

$conn.Close()
Write-Output ""
Write-Output "Next step: python tools\csv_to_production_fixture.py --csv `"$OutCsv`""
