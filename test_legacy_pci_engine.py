"""
Tests for legacy_pci_engine.py -- the ported Form1.cs RunPCICalcs()/ExportClass.cs
calculation. Every q-branch and threshold documented in that module's docstring is
tested explicitly, using a FakeLegacyPCIProvider so these tests are independent of
VPCI_Config.accdb's real data (see test_legacy_pci_provider.py for tests against
the real data, and test_legacy_pci_integration.py for end-to-end golden cases using
the real provider).
"""
import unittest
from typing import Dict, Tuple

from legacy_pci_adapter import CELL_ORDER, CELL_TO_DISTRESS_TYPE, build_cells
from legacy_pci_engine import (
    _final_deduct_branch, _round_half_to_even, calculate_sample_unit_pci,
    calculate_section_result, compute_max_deduct_value, compute_q,
    compute_total_deduct_value, rating_for_vpci, resolve_cells,
    _STRUCTURE_TYPES, _SURFACE_TYPES, _OTHER_TYPES,
)
from legacy_pci_types import LegacyCellResult


class FakeLegacyPCIProvider:
    """TEST-ONLY. Lets these tests control deduct values and interpolation results
    directly, without going through the ~1100-row real reference data. Mirrors
    StaticLegacyPCIProvider's "miss resolves to zero, never raises" semantics
    exactly, since that miss-handling is itself part of what several tests check."""

    def __init__(self, cells=None, interpolation=None):
        self._cells: Dict[Tuple[str, int], float] = dict(cells or {})
        self._interpolation: Dict[Tuple[int, int], float] = dict(interpolation or {})

    def get_cell(self, distress_type: str, bucket_index: int) -> LegacyCellResult:
        key = (distress_type, bucket_index)
        if key not in self._cells:
            return LegacyCellResult(distress_type, bucket_index, "", 0.0, 0.0, found=False)
        return LegacyCellResult(distress_type, bucket_index, "fake", 0.0, self._cells[key], found=True)

    def get_interpolated_final_deduct(self, q: int, total_deduct_rounded: int):
        key = (q - 1, total_deduct_rounded)
        if key in self._interpolation:
            return self._interpolation[key], True
        return 0.0, False


def _all_zero_cells():
    """17 cells, all resolving to deduct_value=0 via a provider that has nothing
    configured -- every lookup is an explicit miss, not a trivial "no cells"."""
    return [(distress_type, 0) for distress_type in CELL_TO_DISTRESS_TYPE.values()]


class TestRoundHalfToEven(unittest.TestCase):
    """Confirms Python's round() matches .NET's default Math.Round()/
    Convert.ToInt32(double) banker's-rounding convention for the exact midpoint
    values the task called out."""

    def test_10_5_rounds_to_10(self):
        self.assertEqual(_round_half_to_even(10.5), 10)

    def test_11_5_rounds_to_12(self):
        self.assertEqual(_round_half_to_even(11.5), 12)

    def test_12_5_rounds_to_12(self):
        self.assertEqual(_round_half_to_even(12.5), 12)

    def test_non_midpoint_values_round_normally(self):
        self.assertEqual(_round_half_to_even(10.4), 10)
        self.assertEqual(_round_half_to_even(10.6), 11)


class TestComputeTDVQMaxDeduct(unittest.TestCase):
    def test_tdv_is_unfiltered_sum(self):
        provider = FakeLegacyPCIProvider(cells={
            ("PotholeLow", 0): 1.0, ("PotholeMed", 0): 2.0, ("PotholeHigh", 0): 3.0,
        })
        cells = [("PotholeLow", 0), ("PotholeMed", 0), ("PotholeHigh", 0)]
        results = resolve_cells(provider, cells)
        self.assertEqual(compute_total_deduct_value(results), 6.0)

    def test_q_counts_strictly_greater_than_five_not_two(self):
        # Three deduct values: 5 (not >5), 5.01 (>5), 6 (>5) -> q should be 2, NOT 3
        # (which is what a >2 threshold would have counted, since 5 itself is >2).
        provider = FakeLegacyPCIProvider(cells={
            ("PotholeLow", 0): 5.0, ("PotholeMed", 0): 5.01, ("PotholeHigh", 0): 6.0,
        })
        cells = [("PotholeLow", 0), ("PotholeMed", 0), ("PotholeHigh", 0)]
        results = resolve_cells(provider, cells)
        self.assertEqual(compute_q(results), 2)

    def test_max_deduct_is_single_largest_value(self):
        provider = FakeLegacyPCIProvider(cells={
            ("PotholeLow", 0): 1.0, ("PotholeMed", 0): 40.0, ("PotholeHigh", 0): 3.0,
        })
        cells = [("PotholeLow", 0), ("PotholeMed", 0), ("PotholeHigh", 0)]
        results = resolve_cells(provider, cells)
        self.assertEqual(compute_max_deduct_value(results), 40.0)

    def test_max_deduct_floor_is_zero_when_all_zero(self):
        results = resolve_cells(FakeLegacyPCIProvider(), _all_zero_cells())
        self.assertEqual(compute_max_deduct_value(results), 0.0)


class TestFinalDeductBranches(unittest.TestCase):
    """Every documented branch/threshold, exercised directly against
    _final_deduct_branch() for full boundary coverage (white-box: this is a
    private helper, tested directly here on purpose for exhaustive threshold
    coverage; calculate_sample_unit_pci() end-to-end wiring is tested separately
    below)."""

    def test_q_le_1_tdv_le_100_uses_rounded_tdv(self):
        final_deduct, branch, lookup = _final_deduct_branch(0, 50, max_deduct=3.0, provider=FakeLegacyPCIProvider())
        self.assertEqual(final_deduct, 50.0)
        self.assertEqual(branch, "q<=1/TDV<=100")
        self.assertIsNone(lookup)

    def test_q_le_1_tdv_eq_100_is_still_le_100(self):
        final_deduct, branch, _ = _final_deduct_branch(1, 100, max_deduct=0.0, provider=FakeLegacyPCIProvider())
        self.assertEqual(final_deduct, 100.0)
        self.assertEqual(branch, "q<=1/TDV<=100")

    def test_q_le_1_tdv_gt_100_uses_max_deduct(self):
        final_deduct, branch, lookup = _final_deduct_branch(1, 101, max_deduct=77.0, provider=FakeLegacyPCIProvider())
        self.assertEqual(final_deduct, 77.0)
        self.assertEqual(branch, "q<=1/TDV>100")
        self.assertIsNone(lookup)

    # --- q == 2 : <15 -> 10, >170 -> 100, else lookup ---

    def test_q2_below_lower_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(2, 14, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (10.0, "q==2/TDV<15"))

    def test_q2_at_lower_threshold_uses_lookup_not_literal(self):
        # tdv_rounded=15 is NOT < 15, so this must fall into the lookup branch.
        provider = FakeLegacyPCIProvider(interpolation={(1, 15): 10.0})
        final_deduct, branch, lookup = _final_deduct_branch(2, 15, 0.0, provider)
        self.assertEqual((final_deduct, branch), (10.0, "q==2/lookup"))
        self.assertTrue(lookup.found)

    def test_q2_above_upper_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(2, 171, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (100.0, "q==2/TDV>170"))

    def test_q2_at_upper_threshold_uses_lookup_not_literal(self):
        provider = FakeLegacyPCIProvider(interpolation={(1, 170): 100.0})
        final_deduct, branch, lookup = _final_deduct_branch(2, 170, 0.0, provider)
        self.assertEqual((final_deduct, branch), (100.0, "q==2/lookup"))
        self.assertTrue(lookup.found)

    def test_q2_middle_range_uses_lookup(self):
        provider = FakeLegacyPCIProvider(interpolation={(1, 50): 42.5})
        final_deduct, branch, lookup = _final_deduct_branch(2, 50, 0.0, provider)
        self.assertEqual((final_deduct, branch), (42.5, "q==2/lookup"))
        self.assertEqual(lookup.q, 2)
        self.assertEqual(lookup.total_deduct_rounded, 50)
        self.assertTrue(lookup.found)

    def test_q2_middle_range_missing_lookup_resolves_to_zero(self):
        final_deduct, branch, lookup = _final_deduct_branch(2, 50, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (0.0, "q==2/lookup"))
        self.assertFalse(lookup.found)

    # --- q == 3 : <20 -> 10, >180 -> 99, else lookup ---

    def test_q3_below_lower_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(3, 19, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (10.0, "q==3/TDV<20"))

    def test_q3_above_upper_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(3, 181, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (99.0, "q==3/TDV>180"))

    def test_q3_middle_range_uses_lookup(self):
        provider = FakeLegacyPCIProvider(interpolation={(2, 100): 55.0})
        final_deduct, branch, _ = _final_deduct_branch(3, 100, 0.0, provider)
        self.assertEqual((final_deduct, branch), (55.0, "q==3/lookup"))

    # --- q == 4 : <30 -> 11, >200 -> 98, else lookup ---

    def test_q4_below_lower_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(4, 29, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (11.0, "q==4/TDV<30"))

    def test_q4_above_upper_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(4, 201, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (98.0, "q==4/TDV>200"))

    def test_q4_middle_range_uses_lookup(self):
        provider = FakeLegacyPCIProvider(interpolation={(3, 150): 70.0})
        final_deduct, branch, _ = _final_deduct_branch(4, 150, 0.0, provider)
        self.assertEqual((final_deduct, branch), (70.0, "q==4/lookup"))

    # --- q == 5 : <30 -> 10, >200 -> 93, else lookup ---

    def test_q5_below_lower_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(5, 29, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (10.0, "q==5/TDV<30"))

    def test_q5_above_upper_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(5, 201, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (93.0, "q==5/TDV>200"))

    def test_q5_middle_range_uses_lookup(self):
        provider = FakeLegacyPCIProvider(interpolation={(4, 150): 65.0})
        final_deduct, branch, _ = _final_deduct_branch(5, 150, 0.0, provider)
        self.assertEqual((final_deduct, branch), (65.0, "q==5/lookup"))

    # --- q == 6 : <32 -> 10, >200 -> 90, else lookup ---

    def test_q6_below_lower_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(6, 31, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (10.0, "q==6/TDV<32"))

    def test_q6_above_upper_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(6, 201, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (90.0, "q==6/TDV>200"))

    def test_q6_middle_range_uses_lookup(self):
        provider = FakeLegacyPCIProvider(interpolation={(5, 150): 60.0})
        final_deduct, branch, _ = _final_deduct_branch(6, 150, 0.0, provider)
        self.assertEqual((final_deduct, branch), (60.0, "q==6/lookup"))

    # --- q >= 7 : <32 -> 10, >200 -> 82, else lookup; q is NOT clamped to 7 ---

    def test_q7_below_lower_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(7, 31, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (10.0, "q>=7/TDV<32"))

    def test_q7_above_upper_threshold(self):
        final_deduct, branch, _ = _final_deduct_branch(7, 201, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (82.0, "q>=7/TDV>200"))

    def test_q7_middle_range_uses_lookup(self):
        provider = FakeLegacyPCIProvider(interpolation={(6, 150): 50.0})
        final_deduct, branch, lookup = _final_deduct_branch(7, 150, 0.0, provider)
        self.assertEqual((final_deduct, branch), (50.0, "q>=7/lookup"))
        self.assertEqual(lookup.q, 7)

    def test_q8_is_not_clamped_to_7_in_lookup_key(self):
        # If q were wrongly clamped to 7, this would hit the (6, 150) key below and
        # return 50.0. It must instead query (7, 150) -- which isn't configured --
        # and miss.
        provider = FakeLegacyPCIProvider(interpolation={(6, 150): 50.0})
        final_deduct, branch, lookup = _final_deduct_branch(8, 150, 0.0, provider)
        self.assertEqual(branch, "q>=7/lookup")
        self.assertFalse(lookup.found)
        self.assertEqual(final_deduct, 0.0)
        self.assertEqual(lookup.q, 8)

    def test_q8_below_lower_threshold_same_literal_as_q7(self):
        final_deduct, branch, _ = _final_deduct_branch(8, 31, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (10.0, "q>=7/TDV<32"))

    def test_q9_above_upper_threshold_same_literal_as_q7(self):
        final_deduct, branch, _ = _final_deduct_branch(9, 201, 0.0, FakeLegacyPCIProvider())
        self.assertEqual((final_deduct, branch), (82.0, "q>=7/TDV>200"))

    def test_q9_middle_range_queries_its_own_row(self):
        provider = FakeLegacyPCIProvider(interpolation={(8, 150): 71.0})
        final_deduct, branch, lookup = _final_deduct_branch(9, 150, 0.0, provider)
        self.assertEqual((final_deduct, branch), (71.0, "q>=7/lookup"))
        self.assertEqual(lookup.q, 9)


class TestMaxDeductFloor(unittest.TestCase):
    """The unconditional post-hoc floor: if a branch's finalDeduct comes out lower
    than the single largest individual deduct value, it is replaced by that max --
    exercised here through the lookup path (a pre-tabulated CDV value happening to
    be lower than one raw cell's deduct value), since that is the only branch where
    this can occur with internally-consistent q/TDV/maxDeduct values (the literal
    branches are shown, by construction, to never need the floor for a
    realistically-derived q/TDV/maxDeduct triple)."""

    def test_floor_overrides_a_lookup_result_smaller_than_max_deduct(self):
        # Two cells >5: 40 and 10 -> q=2, TDV=50 (rounded=50), falls in q==2's
        # middle/lookup range (15 <= 50 <= 170). Fake interpolation table
        # deliberately returns something smaller than maxDeduct=40.
        provider = FakeLegacyPCIProvider(
            cells={("PotholeLow", 0): 40.0, ("PotholeMed", 0): 10.0},
            interpolation={(1, 50): 5.0},
        )
        cells = [c for c in _all_zero_cells() if c[0] not in ("PotholeLow", "PotholeMed")]
        cells += [("PotholeLow", 0), ("PotholeMed", 0)]
        result = calculate_sample_unit_pci("Road", "SU", cells, provider)

        self.assertEqual(result.q, 2)
        self.assertEqual(result.total_deduct_value, 50.0)
        self.assertEqual(result.max_deduct_value, 40.0)
        self.assertEqual(result.final_deduct_before_floor, 5.0)
        self.assertEqual(result.final_deduct, 40.0)  # floor overrides the 5.0 lookup result
        self.assertEqual(result.pci, 60)  # 100 - round(40)

    def test_floor_is_a_noop_when_branch_result_already_exceeds_max_deduct(self):
        provider = FakeLegacyPCIProvider(cells={("PotholeLow", 0): 3.0})
        cells = [c for c in _all_zero_cells() if c[0] != "PotholeLow"] + [("PotholeLow", 0)]
        result = calculate_sample_unit_pci("Road", "SU", cells, provider)
        self.assertEqual(result.q, 0)
        self.assertEqual(result.final_deduct_before_floor, result.final_deduct)


class TestCalculateSampleUnitPciEndToEnd(unittest.TestCase):
    def test_all_zero_cells_gives_pci_100(self):
        result = calculate_sample_unit_pci("Road", "SU", _all_zero_cells(), FakeLegacyPCIProvider())
        self.assertEqual(result.total_deduct_value, 0.0)
        self.assertEqual(result.q, 0)
        self.assertEqual(result.max_deduct_value, 0.0)
        self.assertEqual(result.final_deduct, 0.0)
        self.assertEqual(result.pci, 100)
        self.assertIsInstance(result.pci, int)
        self.assertEqual(len(result.cell_results), 17)

    def test_pci_is_always_an_int_not_a_float(self):
        provider = FakeLegacyPCIProvider(cells={("PotholeLow", 0): 7.0})
        cells = [c for c in _all_zero_cells() if c[0] != "PotholeLow"] + [("PotholeLow", 0)]
        result = calculate_sample_unit_pci("Road", "SU", cells, provider)
        self.assertIsInstance(result.pci, int)

    def test_structure_surface_other_groups_partition_all_17_cells(self):
        all_types = set(CELL_TO_DISTRESS_TYPE.values())
        union = _STRUCTURE_TYPES | _SURFACE_TYPES | _OTHER_TYPES
        self.assertEqual(union, all_types)
        self.assertEqual(len(_STRUCTURE_TYPES) + len(_SURFACE_TYPES) + len(_OTHER_TYPES), 17)
        self.assertEqual(len(_STRUCTURE_TYPES & _SURFACE_TYPES), 0)
        self.assertEqual(len(_STRUCTURE_TYPES & _OTHER_TYPES), 0)
        self.assertEqual(len(_SURFACE_TYPES & _OTHER_TYPES), 0)

    def test_percentages_are_zero_when_total_deduct_is_zero(self):
        result = calculate_sample_unit_pci("Road", "SU", _all_zero_cells(), FakeLegacyPCIProvider())
        self.assertEqual(result.structure_deduct_percentage, 0)
        self.assertEqual(result.surface_deduct_percentage, 0)

    def test_structure_surface_percentages_weighted_by_tdv(self):
        provider = FakeLegacyPCIProvider(cells={("PotholeLow", 0): 10.0, ("Bleeding", 0): 10.0})
        cells = [c for c in _all_zero_cells() if c[0] not in ("PotholeLow", "Bleeding")]
        cells += [("PotholeLow", 0), ("Bleeding", 0)]
        result = calculate_sample_unit_pci("Road", "SU", cells, provider)
        self.assertEqual(result.total_deduct_value, 20.0)
        self.assertEqual(result.structure_deduct_value, 10.0)  # PotholeLow
        self.assertEqual(result.surface_deduct_value, 10.0)    # Bleeding
        self.assertEqual(result.structure_deduct_percentage, 50)
        self.assertEqual(result.surface_deduct_percentage, 50)


class TestRatingBands(unittest.TestCase):
    def test_boundaries(self):
        cases = [
            (100, "Very Good"), (85, "Very Good"), (84, "Good"),
            (65, "Good"), (64, "Fair"),
            (50, "Fair"), (49, "Poor"),
            (40, "Poor"), (39, "Very Poor"),
            (20, "Very Poor"), (19, "Failed"),
            (0, "Failed"),
        ]
        for pci, expected in cases:
            with self.subTest(pci=pci):
                self.assertEqual(rating_for_vpci(pci), expected)


class TestCalculateSectionResult(unittest.TestCase):
    def test_vpci_is_rounded_mean(self):
        result = calculate_section_result("Road", [80, 90])
        self.assertEqual(result.vpci, 85)

    def test_skipped_units_are_simply_absent_not_zero_filled(self):
        # A caller who filters out skipped units before calling this function gets
        # the mean of only the rated ones -- there is no "zero" entry to filter.
        result = calculate_section_result("Road", [80, 90])  # 2 rated, N skipped elsewhere
        self.assertEqual(result.sample_unit_pcis, (80, 90))
        self.assertNotIn(0, result.sample_unit_pcis)

    def test_empty_sequence_raises(self):
        with self.assertRaises(ValueError):
            calculate_section_result("Road", [])

    def test_standard_deviation_uses_rounded_mean_not_raw_mean(self):
        # raw mean of [80, 81] is 80.5 -> banker's-rounds to 80 (nearest even).
        # Variance against the ROUNDED mean (80): ((80-80)^2+(81-80)^2)/2 = 0.5,
        # sqrt=0.7071, rounds to 1.
        # Variance against the TRUE mean (80.5) would instead be 0.25, sqrt=0.5,
        # which banker's-rounds to 0 -- a different, distinguishable answer. This
        # test fails if the implementation is "corrected" to use the true mean.
        result = calculate_section_result("Road", [80, 81])
        self.assertEqual(result.vpci, 80)
        self.assertEqual(result.standard_deviation, 1)

    def test_rating_attached_to_vpci(self):
        result = calculate_section_result("Road", [90, 90])
        self.assertEqual(result.rating, "Very Good")


if __name__ == "__main__":
    unittest.main()
