"""
Tests for legacy_pci_adapter.py.
"""
import unittest

from distress_catalog import NO_SEVERITY, get_distress
from legacy_pci_adapter import (
    CELL_ORDER, CELL_TO_DISTRESS_TYPE, bucket_index_for_label, build_cells,
    build_cells_from_labels, selections_from_labels,
)


class TestCellOrder(unittest.TestCase):
    def test_has_seventeen_cells(self):
        self.assertEqual(len(CELL_ORDER), 17)

    def test_matches_distress_type_mapping_keys(self):
        self.assertEqual(set(CELL_ORDER), set(CELL_TO_DISTRESS_TYPE))

    def test_rutting_has_no_low_cell(self):
        self.assertNotIn(("rutting", "Low"), CELL_ORDER)
        self.assertIn(("rutting", "Medium"), CELL_ORDER)
        self.assertIn(("rutting", "High"), CELL_ORDER)


class TestBuildCells(unittest.TestCase):
    def test_empty_selections_default_every_cell_to_bucket_zero(self):
        cells = build_cells({})
        self.assertEqual(len(cells), 17)
        self.assertTrue(all(bucket_index == 0 for _distress_type, bucket_index in cells))

    def test_selections_are_threaded_through_by_distress_type(self):
        cells = build_cells({("potholes", "High"): 3, ("bleeding", NO_SEVERITY): 5})
        as_dict = dict(cells)
        self.assertEqual(as_dict["PotholeHigh"], 3)
        self.assertEqual(as_dict["Bleeding"], 5)
        # Everything else still defaults to 0.
        self.assertEqual(as_dict["PotholeLow"], 0)

    def test_output_order_matches_cell_order(self):
        cells = build_cells({})
        self.assertEqual(
            [distress_type for distress_type, _idx in cells],
            [CELL_TO_DISTRESS_TYPE[key] for key in CELL_ORDER],
        )


class TestBucketIndexForLabel(unittest.TestCase):
    def test_round_trips_against_distress_catalog(self):
        self.assertEqual(bucket_index_for_label("potholes", "High", "0"), 0)
        self.assertEqual(bucket_index_for_label("potholes", "High", "> 5"), 6)
        self.assertEqual(bucket_index_for_label("bleeding", NO_SEVERITY, "c. 5%"), 2)

    def test_unknown_label_raises_value_error(self):
        with self.assertRaises(ValueError):
            bucket_index_for_label("potholes", "High", "not a real option")


def _all_zero_labels():
    """Exactly what pci_viewer.py's self.observations[key] looks like before an
    inspector touches any combo box -- every storage_key mapped to its cell's "0"
    option (distress_catalog.py's combo boxes always default to index 0)."""
    labels = {}
    for distress_id, severity in CELL_ORDER:
        distress = get_distress(distress_id)
        labels[distress.storage_key(severity)] = distress.options[severity][0]
    return labels


class TestSelectionsFromLabels(unittest.TestCase):
    """These exercise the exact translation pci_viewer.py's commit_current_observation()
    relies on -- a raw {storage_key: label} dict, as produced by
    _current_distress_selections() -- with no Qt/PyQt involved at all."""

    def test_all_zero_labels_resolve_to_bucket_zero_everywhere(self):
        selections = selections_from_labels(_all_zero_labels())
        self.assertEqual(len(selections), 17)
        self.assertTrue(all(index == 0 for index in selections.values()))

    def test_a_real_label_resolves_to_its_bucket_index(self):
        labels = _all_zero_labels()
        labels["potholes_high"] = "> 5"
        selections = selections_from_labels(labels)
        self.assertEqual(selections[("potholes", "High")], 6)

    def test_missing_storage_key_is_simply_omitted(self):
        selections = selections_from_labels({})
        self.assertEqual(selections, {})

    def test_unrecognized_label_is_simply_omitted_not_raised(self):
        labels = _all_zero_labels()
        labels["bleeding"] = "not a real option"
        selections = selections_from_labels(labels)
        self.assertNotIn(("bleeding", NO_SEVERITY), selections)


class TestBuildCellsFromLabels(unittest.TestCase):
    def test_matches_build_cells_plus_selections_from_labels(self):
        labels = _all_zero_labels()
        labels["potholes_high"] = "> 5"
        direct = build_cells(selections_from_labels(labels))
        combined = build_cells_from_labels(labels)
        self.assertEqual(direct, combined)

    def test_produces_seventeen_cells(self):
        self.assertEqual(len(build_cells_from_labels(_all_zero_labels())), 17)


if __name__ == "__main__":
    unittest.main()
