"""
Type system for the legacy VPCI PCI calculation -- the methodology implemented by
the C# VPCI_Application (VPCI_Images.csproj), ported byte-for-byte in
legacy_pci_engine.py. This is a separate methodology from d6433_types.py /
d6433_engine.py -- see legacy_pci_engine.py's module docstring for why, and do not
merge the two.

These types exist to carry the calculation trace end to end (one lookup per cell ->
TDV -> q -> maxDeduct -> finalDeduct -> PCI) so every intermediate value used to
reach a PCI is inspectable, per this project's auditability requirement -- nothing
here is hidden or discarded.
"""
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class LegacyCellResult:
    """One of the 17 distress/severity cells, resolved against VPCI_Config.accdb's
    [Distresses] table (see legacy_pci_provider.py). `distress_type` is the legacy
    database's own DistressType string (e.g. "PotholeLow", "AlligatorHigh",
    "EdgeBreak", "OtherCrack", "RuttingMed") -- see legacy_pci_adapter.py for the
    mapping from distress_catalog.py's (distress_id, severity) to these strings.
    `bucket_index` is the 0-based position within that cell's 7-option dropdown
    (matching distress_catalog.py option order -- "0" first, then ascending
    Quantity).

    `found=False` mirrors the C# DatabaseOperations.GetCorrespondingData()'s "no
    matching row" outcome (there: quantity=0, deduct_value=0, plus a MessageBox) --
    preserved here as an explicit flag instead of a silent zero, since there is no
    direct Python equivalent of a modal dialog in a calculation engine."""
    distress_type: str
    bucket_index: int
    descriptor: str
    quantity: float
    deduct_value: float
    found: bool


@dataclass(frozen=True)
class LegacyCDVLookup:
    """One GetInterpolatedData() call's resolved result -- present only when a
    finalDeduct branch actually needed a table lookup (q in 2..6, or q>=7, AND TDV
    within that branch's open middle range; see legacy_pci_engine.py).

    `q` is the actual q/numberGR5 value (NOT q-1); the q-1 translation happens
    inside the provider, mirroring DatabaseOperations.GetInterpolatedData()."""
    q: int
    total_deduct_rounded: int
    final_deduct: float
    found: bool  # False if no InterpolationCurve row existed for (q-1, total_deduct_rounded)


@dataclass(frozen=True)
class LegacySampleUnitResult:
    """The full calculation trace for one 100m sample unit -- every intermediate
    value used to reach `pci` is present, mirroring Form1.cs RunPCICalcs() exactly."""
    section_id: str
    sample_unit_id: str
    cell_results: Tuple[LegacyCellResult, ...]  # all 17 cells, in legacy_pci_adapter.CELL_ORDER

    total_deduct_value: float        # TDV = sum of all 17 DeductValues (unrounded)
    total_deduct_value_rounded: int  # round(TDV, banker's rounding)
    q: int                           # count(DeductValue > 5) -- NOT > 2
    max_deduct_value: float          # single largest individual DeductValue

    branch: str                                  # which finalDeduct branch fired, e.g. "q==2"
    cdv_lookup: Optional[LegacyCDVLookup]        # present only if that branch used a table lookup
    final_deduct_before_floor: float             # finalDeduct as set by the branch
    final_deduct: float                          # finalDeduct after the maxDeduct floor

    pci: int  # 100 - round(finalDeduct) -- an INTEGER, not a float

    # Reporting-only breakdown (Form1.cs's StructureDeductValue/surfaceDeductValue/
    # otherDeductValue/*Percentage) -- plays no part in TDV/q/finalDeduct/PCI.
    structure_deduct_value: float
    surface_deduct_value: float
    other_deduct_value: float
    structure_deduct_percentage: int  # 0 if total_deduct_value <= 0
    surface_deduct_percentage: int


@dataclass(frozen=True)
class LegacySectionResult:
    """Road/section-level aggregation (ExportClass.cs). The mean of all of this
    section's (non-skipped) sample-unit PCIs, rounded, with the legacy rating band
    and a population standard deviation computed against that *rounded* mean -- an
    intentional, preserved quirk; see legacy_pci_engine.calculate_section_result()."""
    section_id: str
    sample_unit_pcis: Tuple[int, ...]  # excludes any skipped sample units entirely -- never zero-filled
    vpci: int                          # round(mean(sample_unit_pcis))
    rating: str
    standard_deviation: int
