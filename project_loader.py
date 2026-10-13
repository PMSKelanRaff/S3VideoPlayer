"""
Bridges a per-project .accdb (created by project_setup.py) into pci_viewer.py's
rating workflow.

LOADING (load_project_segments): reads ProjectData, re-derives the job's RSP
folder by content (locate_job_folder_for_roads() -- same strategy
project_setup.py uses; the folder path itself isn't stored in ProjectData), and
returns one dict per ProjectData row shaped exactly like a FileExtentsAWS.csv
row ({"RSPFile", "ChainageFrom", "ChainageTo", "Image0"}, plus extra "_"-
prefixed keys pci_viewer.py's existing CSV-row code already ignores) so the
existing load_selected_qa_segment()/frame-navigation pipeline is reused
UNCHANGED -- this module is an alternative SOURCE for that same shape, not a
parallel code path.

WRITING (commit_rated_sample_unit): writes one InputData row (raw 17
selections) and one VPCIData row (the already-computed LegacySampleUnitResult)
per committed 100m sample unit, using the real schema's column names and the
confirmed Quantity/DeductValue column-name swap in VPCIData (see
legacy_pci_engine.py's module docstring for that swap, and
DISTRESS_TYPE_TO_COLUMN_ROOT below for the column-name mapping -- confirmed
against real InputData/VPCIData rows from "Amarenco Solar farm Mallow Feb24
PCI.accdb", inspected read-only). Also sets the matching ProjectData row's
StartedRating=True on first write. FinishedRating is intentionally NEVER set
automatically here -- see pci_viewer.py for wherever "this road segment is
done" actually gets decided; writing every committed unit is expected to
happen many times per segment (one per 100m), so this function is called once
per commit, not once per segment.

This module performs REAL writes against whatever .accdb path it's given --
unlike project_setup.py's one-time creation of a brand new file, callers (i.e.
pci_viewer.py) are responsible for only ever pointing this at a project the
user explicitly opened to rate, never a read-only reference/archive file.
"""
import os
from typing import Dict, List

import access_db_bridge as bridge
from distress_catalog import get_distress
from legacy_pci_adapter import CELL_ORDER, CELL_TO_DISTRESS_TYPE
from legacy_pci_types import LegacySampleUnitResult
from project_setup import REMOTE_PCI_ROOT, locate_job_folder_for_roads

# VPCI_Config.accdb's own DistressType string (legacy_pci_adapter.
# CELL_TO_DISTRESS_TYPE's values) -> the InputData/VPCIData column name root.
# NOT simple pluralizations for several of these (Raveling -> Ravelling,
# Disintegration -> Disintergration [sic, the database's own typo, not ours],
# EdgeBreak -> EdgeBreakup, OtherCrack -> OtherCracking) -- confirmed against
# real rows, not guessed.
DISTRESS_TYPE_TO_COLUMN_ROOT: Dict[str, str] = {
    "PotholeLow": "PotholesLow",
    "PotholeMed": "PotholesMed",
    "PotholeHigh": "PotholesHigh",
    "PatchLow": "PatchingLow",
    "PatchMed": "PatchingMed",
    "PatchHigh": "PatchingHigh",
    "AlligatorLow": "AlligatorLow",
    "AlligatorMed": "AlligatorMed",
    "AlligatorHigh": "AlligatorHigh",
    "RuttingMed": "RuttingMed",
    "RuttingHigh": "RuttingHigh",
    "Raveling": "Ravelling",
    "Depression": "Depression",
    "Disintegration": "Disintergration",
    "Bleeding": "Bleeding",
    "EdgeBreak": "EdgeBreakup",
    "OtherCrack": "OtherCracking",
}

# One (storage_key, column_root) pair per cell, in distress_catalog.py's own
# canonical order -- derived from CELL_ORDER/CELL_TO_DISTRESS_TYPE/
# DISTRESS_TYPE_TO_COLUMN_ROOT rather than hand-typed twice, so the InputData
# and VPCIData column lists below can never drift out of sync with each other
# or with the adapter's own cell identity.
_CELL_COLUMNS = tuple(
    (get_distress(distress_id).storage_key(severity), DISTRESS_TYPE_TO_COLUMN_ROOT[CELL_TO_DISTRESS_TYPE[(distress_id, severity)]])
    for (distress_id, severity) in CELL_ORDER
)
assert len(_CELL_COLUMNS) == 17


class ProjectLoadError(Exception):
    """Raised when a project .accdb can't be loaded for rating -- missing/
    unreadable ProjectData, or its RSP folder can't be located under
    remote_pci_root."""


def load_project_segments(accdb_path: str, remote_pci_root: str = REMOTE_PCI_ROOT) -> List[dict]:
    """One dict per ProjectData row, shaped like a FileExtentsAWS.csv row for
    load_selected_qa_segment() to consume, plus "_accdb_path"/
    "_project_data_id"/"_identifier" for the write-back path."""
    try:
        rows = bridge.query(accdb_path, "SELECT * FROM [ProjectData]")
    except bridge.AccessQueryError as exc:
        raise ProjectLoadError(f"Could not read ProjectData from {accdb_path}: {exc}") from exc
    if not rows:
        raise ProjectLoadError(f"{accdb_path} has no ProjectData rows.")

    roads = [row["FileName"] for row in rows]
    try:
        job_folder = locate_job_folder_for_roads(roads, remote_pci_root=remote_pci_root)
    except Exception as exc:
        raise ProjectLoadError(
            f"Could not locate the RSP folder for {accdb_path} under {remote_pci_root}: {exc}"
        ) from exc

    segments = []
    for row in rows:
        segments.append({
            "RSPFile": os.path.join(job_folder, f"{row['FileName']}.RSP"),
            "ChainageFrom": row["ChainageFrom"],
            "ChainageTo": row["ChainageTo"],
            "Image0": row["JPEGstem"],
            "_accdb_path": accdb_path,
            "_project_data_id": row["ID"],
            "_identifier": row["Identifier"],
        })
    return segments


def commit_rated_sample_unit(
    accdb_path: str,
    project_data_id: int,
    identifier: str,
    unit_chainage_from_km: float,
    unit_chainage_to_km: float,
    selections: Dict[str, str],
    result: LegacySampleUnitResult,
) -> None:
    """Writes one InputData row + one VPCIData row for a single committed 100m
    sample unit, and marks the owning ProjectData row StartedRating=True. Safe
    to call more than once for conceptually "the same" unit (e.g. after Rerun
    + re-commit) -- each call appends a new InputData/VPCIData row rather than
    updating in place, matching the real schema (no unique constraint prevents
    this, and repeat commits during active rating are expected, not an error).

    `selections`: {storage_key: selected_label} for this unit, exactly the
    shape pci_viewer.py's self.unit_selections[unit_index] already holds.
    `result`: the already-computed LegacySampleUnitResult for this same unit --
    no calculation happens in this module, only column mapping."""
    input_columns = ", ".join(column for _, column in _CELL_COLUMNS)
    input_values = [selections.get(storage_key, "0") for storage_key, _ in _CELL_COLUMNS]
    bridge.execute(
        accdb_path,
        f"INSERT INTO [InputData] (Road, Chainage, {input_columns}) "
        f"VALUES (?,?,{','.join(['?'] * 17)})",
        [identifier, unit_chainage_from_km] + input_values,
    )

    cell_by_root = {
        DISTRESS_TYPE_TO_COLUMN_ROOT[cell.distress_type]: cell
        for cell in result.cell_results
    }
    deduct_columns = [column for _, column in _CELL_COLUMNS]
    quantity_columns = [f"{column}Quantity" for _, column in _CELL_COLUMNS]
    deduct_values = [cell_by_root[column].deduct_value for column in deduct_columns]
    quantity_values = [cell_by_root[column].quantity for column in deduct_columns]

    vpci_columns = (
        "Road, ChainageStart, ChainageEnd, LatFrom, LongFrom, LatTo, LongTo, "
        "PCI, StructureDeductValue, structureDeductPercentage, "
        "surfaceDeductValue, surfaceDeductPercentage, otherDeductValue, "
        "TotalDeduct, ColumnGreater5, "
        + ", ".join(deduct_columns) + ", " + ", ".join(quantity_columns)
    )
    vpci_values = [
        identifier, unit_chainage_from_km, unit_chainage_to_km, "", "", "", "",
        float(result.pci), result.structure_deduct_value, result.structure_deduct_percentage,
        result.surface_deduct_value, result.surface_deduct_percentage, result.other_deduct_value,
        result.total_deduct_value, float(result.q),
    ] + deduct_values + quantity_values

    bridge.execute(
        accdb_path,
        f"INSERT INTO [VPCIData] ({vpci_columns}) VALUES ({','.join(['?'] * len(vpci_values))})",
        vpci_values,
    )

    bridge.execute(
        accdb_path,
        "UPDATE [ProjectData] SET StartedRating = True WHERE ID = ?",
        [project_data_id],
    )
