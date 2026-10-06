"""
Provider abstraction for the legacy VPCI PCI calculation's two reference lookup
tables -- [Distresses] and [InterpolationCurve] from VPCI_Config.accdb (the C#
VPCI_Application's config database; see legacy_pci_data.py for extraction
provenance).

The engine (legacy_pci_engine.py) contains no reference data and no DB access --
only this provider does, and its data is fully verified and extracted -- see
legacy_pci_data.py.
"""
from typing import Dict, List, Protocol, Tuple

import legacy_pci_data
from legacy_pci_types import LegacyCellResult


class LegacyPCIDataUnavailable(Exception):
    """Raised when a calculation needs legacy reference data this provider doesn't
    recognize at all (an unknown distress_type). Note that an *in-range miss*
    (unknown bucket_index, or no InterpolationCurve row) does NOT raise this; see
    LegacyCellResult.found / the get_interpolated_final_deduct() return shape,
    which mirror the C# app's "resolves to zero" behavior instead."""


class LegacyPCIProvider(Protocol):
    def get_cell(self, distress_type: str, bucket_index: int) -> LegacyCellResult: ...

    def get_interpolated_final_deduct(self, q: int, total_deduct_rounded: int) -> Tuple[float, bool]:
        """Returns (final_deduct, found). Mirrors
        DatabaseOperations.GetInterpolatedData(): looks up InterpolationCurve WHERE
        Quantity = q - 1 AND TotalDeduct = total_deduct_rounded. found=False
        (final_deduct=0.0) if no such row exists -- exactly the C# "no match"
        behavior (there, that also pops a MessageBox)."""
        ...


class StaticLegacyPCIProvider:
    """Production provider: loads the verbatim VPCI_Config.accdb extraction from
    legacy_pci_data.py into memory once, then serves lookups from there.

    Lookups are POSITION-based (distress_type, bucket_index), not string-matched
    against the database's own Descriptor text. This is a deliberate adapter-level
    choice, not a database inaccuracy: VPCI_Config.accdb's Descriptor strings
    differ from distress_catalog.py's original bucket labels only in whitespace
    (now corrected to match exactly -- see distress_catalog.py's module docstring),
    and the two orderings were verified to agree position-for-position for all 17
    cells (see test_legacy_pci_provider.py's
    TestProviderOrderMatchesDistressCatalog, which checks this automatically against
    every cell, not just a sample). Position-based lookup is used anyway because it
    is strictly more robust than string matching and cannot be defeated by future
    whitespace drift in either file.
    """

    def __init__(self):
        by_type: Dict[str, List[Tuple[str, float, float]]] = {}
        for distress_type, descriptor, quantity, deduct_value in legacy_pci_data.DISTRESSES:
            by_type.setdefault(distress_type, []).append((descriptor, quantity, deduct_value))

        # legacy_pci_data.DISTRESSES is in the database's raw [ID] order (each
        # type's 6 "real" buckets first, then its "0"/not-observed bucket appended
        # last -- see that module's docstring) -- NOT the "0 first, then ascending
        # Quantity" order the live C# UI actually presents via `ORDER BY
        # [Quantity]`, which is the order distress_catalog.py's option lists use.
        # Re-sort each type's rows by Quantity once, at load time, so bucket_index
        # lines up with distress_catalog.py's option position. Every type's 7 rows
        # have mutually distinct Quantity values (verified at extraction time), so
        # this sort is unambiguous -- no tie-breaking rule is needed.
        for rows in by_type.values():
            rows.sort(key=lambda row: row[1])

        self._by_type_ordered = by_type
        self._interpolation: Dict[Tuple[int, int], float] = {
            (q, td): final_deduct for q, td, final_deduct in legacy_pci_data.INTERPOLATION_CURVE
        }

    def get_cell(self, distress_type: str, bucket_index: int) -> LegacyCellResult:
        rows = self._by_type_ordered.get(distress_type)
        if rows is None:
            raise LegacyPCIDataUnavailable(
                f"Unknown legacy distress_type {distress_type!r} -- not present in "
                f"VPCI_Config.accdb's [Distresses] table (see legacy_pci_data.py)."
            )
        if not (0 <= bucket_index < len(rows)):
            return LegacyCellResult(
                distress_type=distress_type, bucket_index=bucket_index,
                descriptor="", quantity=0.0, deduct_value=0.0, found=False,
            )
        descriptor, quantity, deduct_value = rows[bucket_index]
        return LegacyCellResult(
            distress_type=distress_type, bucket_index=bucket_index,
            descriptor=descriptor, quantity=quantity, deduct_value=deduct_value, found=True,
        )

    def ordered_descriptors(self, distress_type: str) -> List[str]:
        """The descriptor text for distress_type's rows, in bucket_index order --
        used only by tests to cross-check against distress_catalog.py's option
        lists; not used by the calculation engine itself."""
        rows = self._by_type_ordered.get(distress_type, [])
        return [descriptor for descriptor, _quantity, _deduct_value in rows]

    def get_interpolated_final_deduct(self, q: int, total_deduct_rounded: int) -> Tuple[float, bool]:
        key = (q - 1, total_deduct_rounded)
        if key in self._interpolation:
            return self._interpolation[key], True
        return 0.0, False
