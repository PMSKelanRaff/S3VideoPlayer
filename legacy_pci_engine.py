"""
Legacy VPCI PCI calculation engine -- a byte-for-byte port of the C# VPCI_Application's
production PCI calculation (MainForm.RunPCICalcs() in Form1.cs, plus
DatabaseOperations.GetCorrespondingData()/GetInterpolatedData(), plus ExportClass.cs's
road-level aggregation).

This is NOT ASTM D6433, and is not a correction or modernization of it. The legacy
C# application (plus its VPCI_Config.accdb reference data, see legacy_pci_data.py)
is the sole specification for this module. It is also a separate methodology from
d6433_types.py/d6433_engine.py/d6433_adapter.py -- those remain an unfinished,
unrelated placeholder for a real ASTM D6433 implementation; do not merge the two or
import this module from them (or vice versa).

Deliberately preserved "unusual" behaviors -- do not "fix" any of these:
  - q = count(DeductValue > 5), NOT > 2. (d6433_engine.compute_q() uses a different,
    unrelated, still-unverified >2 definition for a different methodology -- the two
    are not interchangeable and this is not an inconsistency to resolve.)
  - TDV is the unfiltered sum of all 17 cells' deduct values.
  - q is NOT clamped to 7: if q >= 8 occurs, a corrected-deduct-value lookup is
    still attempted at (q-1, TDV) and MISSES (VPCI_Config.accdb's
    InterpolationCurve table only has rows for q in 2..7 inclusive -- verified at
    extraction time, see legacy_pci_data.py), producing final_deduct=0 before the
    maxDeduct floor is applied -- exactly mirroring GetInterpolatedData()'s
    "no match -> 0" behavior.
  - The "maxDeduct floor" (`if finalDeduct < maxDeduct: finalDeduct = maxDeduct`) is
    applied unconditionally, AFTER every branch, including branches that already
    used maxDeduct directly.
  - PCI is an integer: 100 - round(finalDeduct), not a float.
  - All `round()` calls use Python's native round-half-to-even, matching .NET's
    default Math.Round()/Convert.ToInt32(double) behavior (also round-half-to-even)
    -- see test_legacy_pci_engine.py's explicit banker's-rounding tests.
  - Road-level standard deviation (calculate_section_result()) is computed against
    the ALREADY-ROUNDED mean (vpci), not the true mean -- ExportClass.cs reuses the
    same `sumPCI` variable for both. Preserved even though it looks like a bug.
  - A missing [Distresses] or [InterpolationCurve] row does not raise -- it
    resolves to quantity=0/deduct_value=0 (or final_deduct=0), with `found=False`
    recorded in the trace instead of the C# app's MessageBox. This is the one
    language-level behavior difference from the C# app, and is deliberate -- see
    legacy_pci_types.LegacyCellResult's docstring.

The engine does not import Qt and does not access any database directly -- see
legacy_pci_provider.py for where VPCI_Config.accdb's data is actually read from,
and legacy_pci_adapter.py for how UI/observation selections become this engine's
input.
"""
from typing import List, Optional, Sequence, Tuple

from legacy_pci_provider import LegacyPCIProvider
from legacy_pci_types import (
    LegacyCDVLookup, LegacyCellResult, LegacySampleUnitResult, LegacySectionResult,
)

# The 17 distress_type -> reporting-category assignments from Form1.cs
# RunPCICalcs() (StructureDeductValue / surfaceDeductValue / otherDeductValue).
# These three groups partition all 17 cells exactly (10 + 2 + 5 = 17) and feed ONLY
# the Excel export's reporting percentages -- they play no part in
# TDV/q/finalDeduct/PCI.
_STRUCTURE_TYPES = frozenset({
    "PotholeLow", "PotholeMed", "PotholeHigh", "RuttingMed", "RuttingHigh",
    "Disintegration", "AlligatorLow", "AlligatorMed", "AlligatorHigh", "EdgeBreak",
})
_SURFACE_TYPES = frozenset({"Bleeding", "Raveling"})
_OTHER_TYPES = frozenset({"OtherCrack", "Depression", "PatchLow", "PatchMed", "PatchHigh"})
# (pairwise-disjointness and exact 17-cell coverage of these three groups are
# checked in test_legacy_pci_engine.py, not re-asserted here)


def _round_half_to_even(value: float) -> int:
    """Math.Round(value, 0) in .NET defaults to MidpointRounding.ToEven, the same
    convention as Python's built-in round() for floats -- this wrapper exists purely
    to name/document that equivalence at every call site, not to change behavior."""
    return int(round(value))


def resolve_cells(
    provider: LegacyPCIProvider, cells: Sequence[Tuple[str, int]],
) -> List[LegacyCellResult]:
    """cells: a sequence of (distress_type, bucket_index) pairs -- see
    legacy_pci_adapter.build_cells() for building this from distress_catalog.py
    selections."""
    return [provider.get_cell(distress_type, bucket_index) for distress_type, bucket_index in cells]


def compute_total_deduct_value(cell_results: Sequence[LegacyCellResult]) -> float:
    """TDV = sum of ALL cells' DeductValue, unfiltered -- Form1.cs's `totalDeduct`."""
    return sum(c.deduct_value for c in cell_results)


def compute_q(cell_results: Sequence[LegacyCellResult]) -> int:
    """q ("numberGR5" in Form1.cs) = count of individual DeductValues > 5. NOT > 2 --
    this is a deliberate, verified divergence from generic ASTM-style q definitions;
    do not change it."""
    return sum(1 for c in cell_results if c.deduct_value > 5)


def compute_max_deduct_value(cell_results: Sequence[LegacyCellResult]) -> float:
    """maxDeduct = largest individual DeductValue, floor 0 (Form1.cs initializes
    `maxDeduct = 0` and only ever replaces it via strict `>`)."""
    max_deduct = 0.0
    for c in cell_results:
        if c.deduct_value > max_deduct:
            max_deduct = c.deduct_value
    return max_deduct


def _final_deduct_branch(
    q: int, tdv_rounded: int, max_deduct: float, provider: LegacyPCIProvider,
) -> Tuple[float, str, Optional[LegacyCDVLookup]]:
    """Implements Form1.cs RunPCICalcs()'s q-branch exactly (the maxDeduct floor is
    applied by the caller afterward, unconditionally -- see
    calculate_sample_unit_pci(); `max_deduct` is used here only for the q<=1/TDV>100
    sub-branch, which the C# source sets explicitly to maxDeduct rather than
    leaving it to the floor). Returns (final_deduct_before_floor, branch_name,
    cdv_lookup_or_None)."""

    def _lookup(q_value: int) -> Tuple[float, Optional[LegacyCDVLookup]]:
        final_deduct, found = provider.get_interpolated_final_deduct(q_value, tdv_rounded)
        return final_deduct, LegacyCDVLookup(
            q=q_value, total_deduct_rounded=tdv_rounded, final_deduct=final_deduct, found=found,
        )

    if q <= 1:
        if tdv_rounded > 100:
            return max_deduct, "q<=1/TDV>100", None
        return float(tdv_rounded), "q<=1/TDV<=100", None
    if q == 2:
        if tdv_rounded < 15:
            return 10.0, "q==2/TDV<15", None
        if tdv_rounded > 170:
            return 100.0, "q==2/TDV>170", None
        final_deduct, lookup = _lookup(q)
        return final_deduct, "q==2/lookup", lookup
    if q == 3:
        if tdv_rounded < 20:
            return 10.0, "q==3/TDV<20", None
        if tdv_rounded > 180:
            return 99.0, "q==3/TDV>180", None
        final_deduct, lookup = _lookup(q)
        return final_deduct, "q==3/lookup", lookup
    if q == 4:
        if tdv_rounded < 30:
            return 11.0, "q==4/TDV<30", None
        if tdv_rounded > 200:
            return 98.0, "q==4/TDV>200", None
        final_deduct, lookup = _lookup(q)
        return final_deduct, "q==4/lookup", lookup
    if q == 5:
        if tdv_rounded < 30:
            return 10.0, "q==5/TDV<30", None
        if tdv_rounded > 200:
            return 93.0, "q==5/TDV>200", None
        final_deduct, lookup = _lookup(q)
        return final_deduct, "q==5/lookup", lookup
    if q == 6:
        if tdv_rounded < 32:
            return 10.0, "q==6/TDV<32", None
        if tdv_rounded > 200:
            return 90.0, "q==6/TDV>200", None
        final_deduct, lookup = _lookup(q)
        return final_deduct, "q==6/lookup", lookup
    # q >= 7 -- NOT clamped: q itself (7, 8, 9, ...) is passed into the lookup, which
    # misses (final_deduct=0.0, found=False) for any q >= 8 since the database only
    # has rows through q=7 (see this module's docstring).
    if tdv_rounded < 32:
        return 10.0, "q>=7/TDV<32", None
    if tdv_rounded > 200:
        return 82.0, "q>=7/TDV>200", None
    final_deduct, lookup = _lookup(q)
    return final_deduct, "q>=7/lookup", lookup


def calculate_sample_unit_pci(
    section_id: str, sample_unit_id: str,
    cells: Sequence[Tuple[str, int]], provider: LegacyPCIProvider,
) -> LegacySampleUnitResult:
    """Full port of Form1.cs RunPCICalcs()'s calculation (persistence/export side
    effects excluded -- those belong to the UI/export layer, not this engine).
    `cells` must contain exactly the 17 (distress_type, bucket_index) pairs; see
    legacy_pci_adapter.CELL_ORDER for the canonical order and
    legacy_pci_adapter.build_cells() for building this from an inspection session."""
    cell_results = resolve_cells(provider, cells)

    tdv = compute_total_deduct_value(cell_results)
    q = compute_q(cell_results)
    max_deduct = compute_max_deduct_value(cell_results)
    tdv_rounded = _round_half_to_even(tdv)

    final_deduct_before_floor, branch, cdv_lookup = _final_deduct_branch(
        q, tdv_rounded, max_deduct, provider,
    )

    # Unconditional post-hoc floor (Form1.cs: `if (finalDeduct < maxDeduct)
    # finalDeduct = maxDeduct;`) -- applied after every branch, including branches
    # that already used maxDeduct directly (a no-op there, but not special-cased).
    final_deduct = final_deduct_before_floor
    if final_deduct < max_deduct:
        final_deduct = max_deduct

    pci = 100 - _round_half_to_even(final_deduct)

    by_type = {c.distress_type: c.deduct_value for c in cell_results}
    structure_deduct_value = sum(v for t, v in by_type.items() if t in _STRUCTURE_TYPES)
    surface_deduct_value = sum(v for t, v in by_type.items() if t in _SURFACE_TYPES)
    other_deduct_value = sum(v for t, v in by_type.items() if t in _OTHER_TYPES)

    if tdv > 0:
        structure_deduct_percentage = _round_half_to_even(100 * (structure_deduct_value / tdv))
        surface_deduct_percentage = _round_half_to_even(100 * (surface_deduct_value / tdv))
    else:
        structure_deduct_percentage = 0
        surface_deduct_percentage = 0

    return LegacySampleUnitResult(
        section_id=section_id, sample_unit_id=sample_unit_id,
        cell_results=tuple(cell_results),
        total_deduct_value=tdv, total_deduct_value_rounded=tdv_rounded,
        q=q, max_deduct_value=max_deduct, branch=branch, cdv_lookup=cdv_lookup,
        final_deduct_before_floor=final_deduct_before_floor, final_deduct=final_deduct,
        pci=pci,
        structure_deduct_value=structure_deduct_value, surface_deduct_value=surface_deduct_value,
        other_deduct_value=other_deduct_value,
        structure_deduct_percentage=structure_deduct_percentage,
        surface_deduct_percentage=surface_deduct_percentage,
    )


# --- Section/road-level aggregation (ExportClass.cs) ---

_RATING_BANDS = (
    (85, "Very Good"),
    (65, "Good"),
    (50, "Fair"),
    (40, "Poor"),
    (20, "Very Poor"),
)


def rating_for_vpci(vpci: int) -> str:
    """Form1.cs/ExportClass.cs's rating bands, checked in this exact descending
    order (>=85, >=65, >=50, >=40, >=20, else Failed). These are NOT the ASTM
    rating bands -- do not substitute ASTM's cut points/labels here."""
    for threshold, label in _RATING_BANDS:
        if vpci >= threshold:
            return label
    return "Failed"


def calculate_section_result(section_id: str, sample_unit_pcis: Sequence[int]) -> LegacySectionResult:
    """ExportClass.cs's road-level aggregation. `sample_unit_pcis` must already
    exclude any skipped sample units -- a skipped unit produces no VPCIData row in
    the C# app and is therefore absent from this calculation entirely, never
    zero-filled (see SkipPCICalcs() in Form1.cs). A road with zero rated sample
    units is never passed to this function at all in the C# app (ExportClass.cs's
    `dataExists` guard skips writing any Table 2 row for it) -- callers should do
    the same rather than calling this with an empty sequence.

    Preserves an intentional-looking-unusual C# quirk: the population standard
    deviation is computed against the mean AFTER it has already been rounded to an
    integer (vpci), not against the raw mean -- ExportClass.cs reuses the same
    `sumPCI` variable for both steps."""
    if not sample_unit_pcis:
        raise ValueError(
            "calculate_section_result() requires at least one (non-skipped) "
            "sample-unit PCI -- the C# app never aggregates a road with zero rated "
            "sample units (see this function's docstring)."
        )

    mean_pci = sum(sample_unit_pcis) / len(sample_unit_pcis)
    vpci = _round_half_to_even(mean_pci)

    variance = sum((pci - vpci) ** 2 for pci in sample_unit_pcis) / len(sample_unit_pcis)
    standard_deviation = _round_half_to_even(variance ** 0.5)

    return LegacySectionResult(
        section_id=section_id,
        sample_unit_pcis=tuple(sample_unit_pcis),
        vpci=vpci,
        rating=rating_for_vpci(vpci),
        standard_deviation=standard_deviation,
    )
