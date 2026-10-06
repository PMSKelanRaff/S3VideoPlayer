"""
Adapter: translates distress_catalog.py bucket selections for a single 100m sample
unit into the legacy PCI engine's input shape -- a sequence of 17
(distress_type, bucket_index) pairs (see legacy_pci_engine.py).

The legacy methodology scores ALL 10 distresses / 17 cells -- there is no
"unsupported distress" concept here -- using position-based bucket selection as
its only quantity input; there is no density calculation in the legacy
methodology at all.
"""
from typing import Dict, List, Tuple

from distress_catalog import DISTRESS_DEFINITIONS, NO_SEVERITY, get_distress

# distress_catalog.py's (distress_id, severity) -> the legacy VPCI_Config.accdb's
# own DistressType string (DatabaseOperations.GetCorrespondingData()'s first
# argument, as called from Form1.cs RunPCICalcs()). This is a complete, exhaustive
# mapping for all 17 cells.
CELL_TO_DISTRESS_TYPE: Dict[Tuple[str, str], str] = {
    ("potholes", "Low"): "PotholeLow",
    ("potholes", "Medium"): "PotholeMed",
    ("potholes", "High"): "PotholeHigh",
    ("patching", "Low"): "PatchLow",
    ("patching", "Medium"): "PatchMed",
    ("patching", "High"): "PatchHigh",
    ("alligator_cracking", "Low"): "AlligatorLow",
    ("alligator_cracking", "Medium"): "AlligatorMed",
    ("alligator_cracking", "High"): "AlligatorHigh",
    ("rutting", "Medium"): "RuttingMed",
    ("rutting", "High"): "RuttingHigh",
    ("raveling", NO_SEVERITY): "Raveling",
    ("depression", NO_SEVERITY): "Depression",
    ("disintegration", NO_SEVERITY): "Disintegration",
    ("bleeding", NO_SEVERITY): "Bleeding",
    ("edge_breakup", NO_SEVERITY): "EdgeBreak",
    ("other_cracking", NO_SEVERITY): "OtherCrack",
}

# Canonical cell order: distress_catalog.py's own DISTRESS_DEFINITIONS order, with
# severities in each distress's own severities-list order. The legacy TDV/q
# calculation is order-independent (plain sum/count), so this order has no effect
# on the PCI result -- it exists only for a stable, readable trace/export order.
CELL_ORDER: Tuple[Tuple[str, str], ...] = tuple(
    (d.distress_id, severity)
    for d in DISTRESS_DEFINITIONS
    for severity in (d.severities or [NO_SEVERITY])
)

assert len(CELL_ORDER) == 17
assert set(CELL_ORDER) == set(CELL_TO_DISTRESS_TYPE)


def build_cells(selections: Dict[Tuple[str, str], int]) -> List[Tuple[str, int]]:
    """selections: {(distress_id, severity): bucket_index} -- severity is
    NO_SEVERITY for the 7 single-severity distresses; bucket_index is the 0-based
    position in that cell's distress_catalog.py option list, matching the
    inspector's dropdown selection 1:1.

    A cell missing from `selections` defaults to bucket_index 0 (distress_catalog's
    "0" / not-observed bucket for every cell) -- matching the legacy UI's
    zeroVPCICharacteristics(), which resets every combo box to its "0" item before
    each sample unit is rated."""
    cells = []
    for key in CELL_ORDER:
        distress_type = CELL_TO_DISTRESS_TYPE[key]
        bucket_index = selections.get(key, 0)
        cells.append((distress_type, bucket_index))
    return cells


def bucket_index_for_label(distress_id: str, severity: str, label: str) -> int:
    """Resolve a distress_catalog.py option label (e.g. "c. 2%") back to its 0-based
    bucket_index for that cell -- for UI code that has a selected label string
    rather than an index already. Raises ValueError if label isn't one of that
    cell's options (same as list.index())."""
    distress = get_distress(distress_id)
    options = distress.options[severity or NO_SEVERITY]
    return options.index(label)


def selections_from_labels(labels_by_storage_key: Dict[str, str]) -> Dict[Tuple[str, str], int]:
    """Translates a {storage_key: selected_label} dict -- e.g.
    {"potholes_high": "> 5", "bleeding": "c. 5%", ...}, exactly the shape
    pci_viewer.py's _current_distress_selections() / self.observations[key]
    already stores -- into the {(distress_id, severity): bucket_index} shape
    build_cells() expects.

    A storage_key missing from the input, or whose label isn't a valid option for
    that cell, is simply left out (build_cells() then defaults it to bucket_index 0,
    the same as an inspector who never touched that combo box -- in practice the
    combo boxes can never actually produce an invalid label, since they're
    populated directly from distress_catalog.py's own option lists)."""
    selections: Dict[Tuple[str, str], int] = {}
    for d in DISTRESS_DEFINITIONS:
        for severity in (d.severities or [NO_SEVERITY]):
            storage_key = d.storage_key(severity)
            label = labels_by_storage_key.get(storage_key)
            if not label:
                continue
            try:
                bucket_index = bucket_index_for_label(d.distress_id, severity, label)
            except ValueError:
                continue
            selections[(d.distress_id, severity)] = bucket_index
    return selections


def build_cells_from_labels(labels_by_storage_key: Dict[str, str]) -> List[Tuple[str, int]]:
    """selections_from_labels() + build_cells() in one call -- the full, one-call
    translation from a raw UI/observation label dict straight to the legacy
    engine's input shape. This is the only translation pci_viewer.py needs to call;
    it must never compute TDV/q/finalDeduct/PCI itself -- that's
    legacy_pci_engine.calculate_sample_unit_pci()'s job."""
    return build_cells(selections_from_labels(labels_by_storage_key))
