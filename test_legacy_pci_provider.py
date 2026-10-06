"""
Tests for legacy_pci_provider.py and legacy_pci_data.py -- verifies the verbatim
VPCI_Config.accdb extraction is loaded correctly and that position-based lookup
(distress_type, bucket_index) agrees with distress_catalog.py's bucket order for
every one of the 17 cells, not just a manually-checked sample.
"""
import unittest

import legacy_pci_data
from distress_catalog import DISTRESS_DEFINITIONS, NO_SEVERITY
from legacy_pci_adapter import CELL_TO_DISTRESS_TYPE
from legacy_pci_provider import LegacyPCIDataUnavailable, StaticLegacyPCIProvider


class TestExtractedDataShape(unittest.TestCase):
    """Confirms the raw extraction in legacy_pci_data.py matches the row counts and
    duplicate-free keys verified directly against VPCI_Config.accdb at extraction
    time (2026-10-06, P:\\Remote PCI\\VPCI Application\\Resources\\VPCI_Config.accdb)."""

    def test_distresses_row_count(self):
        self.assertEqual(len(legacy_pci_data.DISTRESSES), 119)

    def test_interpolation_curve_row_count(self):
        self.assertEqual(len(legacy_pci_data.INTERPOLATION_CURVE), 997)

    def test_distresses_has_no_duplicate_type_descriptor_keys(self):
        keys = [(dt, desc) for dt, desc, _q, _dv in legacy_pci_data.DISTRESSES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_interpolation_curve_has_no_duplicate_keys(self):
        keys = [(q, td) for q, td, _fd in legacy_pci_data.INTERPOLATION_CURVE]
        self.assertEqual(len(keys), len(set(keys)))

    def test_seventeen_distinct_distress_types(self):
        types = {dt for dt, _desc, _q, _dv in legacy_pci_data.DISTRESSES}
        self.assertEqual(len(types), 17)
        self.assertEqual(types, set(CELL_TO_DISTRESS_TYPE.values()))

    def test_each_distress_type_has_exactly_seven_rows(self):
        from collections import Counter
        counts = Counter(dt for dt, _desc, _q, _dv in legacy_pci_data.DISTRESSES)
        for distress_type, count in counts.items():
            self.assertEqual(count, 7, distress_type)

    def test_interpolation_curve_quantity_range_is_1_to_6(self):
        # Quantity here is q-1 -- so this confirms the table only supports q in
        # 2..7 inclusive; q>=8 is a real, confirmed lookup miss (see
        # legacy_pci_engine.py's module docstring), not an assumption.
        quantities = {q for q, _td, _fd in legacy_pci_data.INTERPOLATION_CURVE}
        self.assertEqual(quantities, {1, 2, 3, 4, 5, 6})

    def test_interpolation_curve_branch_boundaries_match_engine_literals(self):
        # Cross-check: for each q, the table's own min/max TotalDeduct should match
        # the literal lower/upper bounds used in legacy_pci_engine._final_deduct_branch
        # (lower_bound+0 .. upper_bound+0, both inclusive, since the literal
        # comparisons are strict < / > ).
        expected_bounds = {
            1: (15, 170),   # q=2
            2: (20, 180),   # q=3
            3: (30, 200),   # q=4
            4: (30, 200),   # q=5
            5: (32, 200),   # q=6
            6: (32, 200),   # q=7
        }
        by_q = {}
        for q, td, _fd in legacy_pci_data.INTERPOLATION_CURVE:
            by_q.setdefault(q, []).append(td)
        for q, tds in by_q.items():
            with self.subTest(q=q):
                self.assertEqual((min(tds), max(tds)), expected_bounds[q])


class TestStaticLegacyPCIProviderCells(unittest.TestCase):
    def setUp(self):
        self.provider = StaticLegacyPCIProvider()

    def test_known_cell_alligator_low_zero_bucket(self):
        cell = self.provider.get_cell("AlligatorLow", 0)
        self.assertTrue(cell.found)
        self.assertEqual(cell.descriptor, "0")
        self.assertEqual(cell.quantity, 0.0)
        self.assertEqual(cell.deduct_value, 0.0)

    def test_known_cell_alligator_low_last_bucket(self):
        cell = self.provider.get_cell("AlligatorLow", 6)
        self.assertTrue(cell.found)
        self.assertEqual(cell.descriptor, "> 20%")
        self.assertEqual(cell.quantity, 30.0)
        self.assertEqual(cell.deduct_value, 45.0)

    def test_known_cell_pothole_high_last_bucket(self):
        cell = self.provider.get_cell("PotholeHigh", 6)
        self.assertEqual(cell.descriptor, "> 5")
        self.assertEqual(cell.quantity, 7.0)
        self.assertEqual(cell.deduct_value, 90.0)

    def test_unknown_distress_type_raises(self):
        with self.assertRaises(LegacyPCIDataUnavailable):
            self.provider.get_cell("NotARealDistressType", 0)

    def test_out_of_range_bucket_index_resolves_to_zero_not_found(self):
        # Mirrors GetCorrespondingData()'s "no matching row" behavior: quantity=0,
        # deduct_value=0, found=False -- no exception.
        cell = self.provider.get_cell("AlligatorLow", 99)
        self.assertFalse(cell.found)
        self.assertEqual(cell.quantity, 0.0)
        self.assertEqual(cell.deduct_value, 0.0)

    def test_negative_bucket_index_resolves_to_zero_not_found(self):
        cell = self.provider.get_cell("AlligatorLow", -1)
        self.assertFalse(cell.found)


class TestStaticLegacyPCIProviderInterpolation(unittest.TestCase):
    def setUp(self):
        self.provider = StaticLegacyPCIProvider()

    def test_known_lookup_lower_boundary(self):
        final_deduct, found = self.provider.get_interpolated_final_deduct(q=2, total_deduct_rounded=15)
        self.assertTrue(found)
        self.assertEqual(final_deduct, 10.0)

    def test_known_lookup_upper_boundary(self):
        final_deduct, found = self.provider.get_interpolated_final_deduct(q=2, total_deduct_rounded=170)
        self.assertTrue(found)
        self.assertEqual(final_deduct, 100.0)

    def test_q_greater_than_seven_misses(self):
        # VPCI_Config.accdb's InterpolationCurve has no rows for q>=8 (Quantity=7+)
        # -- a real, confirmed data boundary, not an assumption.
        final_deduct, found = self.provider.get_interpolated_final_deduct(q=8, total_deduct_rounded=100)
        self.assertFalse(found)
        self.assertEqual(final_deduct, 0.0)

    def test_q_within_range_but_tdv_out_of_table_misses(self):
        final_deduct, found = self.provider.get_interpolated_final_deduct(q=2, total_deduct_rounded=14)
        self.assertFalse(found)
        self.assertEqual(final_deduct, 0.0)


class TestProviderOrderMatchesDistressCatalog(unittest.TestCase):
    """The critical cross-check for position-based lookup: for EVERY one of the 17
    cells, the provider's bucket_index-ordered descriptor list must exactly equal
    distress_catalog.py's option list (same length, same order, same text) -- not
    just the handful manually checked while deciding on this approach."""

    def test_every_cell_matches_positionally_and_textually(self):
        provider = StaticLegacyPCIProvider()
        checked = 0
        for d in DISTRESS_DEFINITIONS:
            for severity in (d.severities or [NO_SEVERITY]):
                distress_type = CELL_TO_DISTRESS_TYPE[(d.distress_id, severity)]
                with self.subTest(distress_id=d.distress_id, severity=severity):
                    catalog_options = d.options[severity]
                    provider_descriptors = provider.ordered_descriptors(distress_type)
                    self.assertEqual(provider_descriptors, catalog_options)
                checked += 1
        self.assertEqual(checked, 17)


if __name__ == "__main__":
    unittest.main()
