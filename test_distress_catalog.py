"""
Tests for distress_catalog.py -- the methodology-agnostic distress vocabulary built
from PCIVariables.txt. These tests must not reference any scoring methodology; how
a methodology interprets this catalog is that methodology's adapter's concern (see
test_d6433_adapter.py).
"""
import unittest

from distress_catalog import (
    DISTRESS_DEFINITIONS, NO_SEVERITY, detailed_field_names, get_distress,
)


class TestEveryDistressIsRepresented(unittest.TestCase):
    """Every distress from PCIVariables.txt must show up exactly once, with the right
    severity split (matching the screenshot's Low/Medium/High columns, including the
    blank Low cell for Rutting)."""

    EXPECTED = {
        "potholes": ["Low", "Medium", "High"],
        "patching": ["Low", "Medium", "High"],
        "alligator_cracking": ["Low", "Medium", "High"],
        "rutting": ["Medium", "High"],           # no Low -- PCIVariables.txt has none
        "raveling": [],
        "depression": [],
        "disintegration": [],
        "bleeding": [],
        "edge_breakup": [],
        "other_cracking": [],
    }

    def test_all_ten_distresses_present_with_correct_severities(self):
        actual = {d.distress_id: d.severities for d in DISTRESS_DEFINITIONS}
        self.assertEqual(actual, self.EXPECTED)

    def test_every_severity_has_seven_bucket_options(self):
        # Matches the 7-option lists ("0" through the open-ended top bucket) in
        # PCIVariables.txt for every distress/severity.
        for d in DISTRESS_DEFINITIONS:
            for severity in (d.severities or [NO_SEVERITY]):
                self.assertEqual(len(d.options[severity]), 7,
                                  f"{d.distress_id}/{severity or 'single'} should have 7 buckets")

    def test_no_duplicate_distress_ids(self):
        ids = [d.distress_id for d in DISTRESS_DEFINITIONS]
        self.assertEqual(len(ids), len(set(ids)))


class TestQuantityKindReflectsBucketLabelsOnly(unittest.TestCase):
    """quantity_kind/quantity_unit are a literal transcription of each distress's own
    bucket-label text, not an engineering conclusion -- see the module docstring.
    Distresses whose own bucket list mixes units are left undetermined (None)."""

    def test_pure_percent_distresses(self):
        for distress_id in ["raveling", "depression", "disintegration", "bleeding", "edge_breakup"]:
            d = get_distress(distress_id)
            self.assertEqual(d.quantity_kind, "area_pct", distress_id)
            self.assertEqual(d.quantity_unit, "percent", distress_id)

    def test_pure_count_distresses(self):
        self.assertEqual(get_distress("potholes").quantity_kind, "count")
        self.assertEqual(get_distress("other_cracking").quantity_kind, "count")
        self.assertEqual(get_distress("other_cracking").quantity_unit, "cracks")

    def test_mixed_unit_distresses_left_undetermined(self):
        # Patching/Alligator Cracking/Rutting mix bare numbers or sq.m with % within
        # the same bucket list in the source data -- must not be force-classified.
        for distress_id in ["patching", "alligator_cracking", "rutting"]:
            d = get_distress(distress_id)
            self.assertIsNone(d.quantity_kind, distress_id)
            self.assertIsNone(d.quantity_unit, distress_id)


class TestStorageKeysHaveNoInternalCollisions(unittest.TestCase):
    def test_detailed_field_names_has_no_duplicates(self):
        names = detailed_field_names()
        self.assertEqual(len(names), len(set(names)))

    def test_rutting_has_no_low_storage_key(self):
        self.assertNotIn("rutting_low", detailed_field_names())
        self.assertIn("rutting_medium", detailed_field_names())
        self.assertIn("rutting_high", detailed_field_names())


class TestGetDistress(unittest.TestCase):
    def test_lookup_by_id(self):
        self.assertEqual(get_distress("rutting").label, "Rutting")

    def test_storage_key_with_and_without_severity(self):
        self.assertEqual(get_distress("potholes").storage_key("Medium"), "potholes_medium")
        self.assertEqual(get_distress("raveling").storage_key(), "raveling")


if __name__ == "__main__":
    unittest.main()
