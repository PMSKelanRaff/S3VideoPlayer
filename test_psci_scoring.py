"""
Tests for psci_scoring.py against Table 1 of the Rural Flexible Roads Manual (DTTAS,
Nov 2013) -- see psci_scoring.py's module docstring for the source.

No PSCI-rated survey data exists yet in this repo (survey_ratings.csv is the *other*
Image Viewer module's generic 1-10 QA rating, unrelated to PSCI defect fields), so the
"representative record" cases below are built directly from Table 1's own worked examples
(the photo captions under each rating, e.g. "Rating 4: Alligator cracking, rutting and
pothole formation in wheelpath") rather than from an existing exported CSV row.
"""
import unittest

from psci_scoring import (
    PSCIRatingInputs, PSCIResult, compute_psci_rating, compute_psci_score,
    evaluate_psci_bands,
)
from survey_core import psci_field_names


class TestPSCIRatingInputsRoundTrip(unittest.TestCase):
    def test_field_names_match_to_dict_keys(self):
        # CSV export writes columns in psci_field_names() order and reads them back with
        # to_dict()/from_dict() -- these must stay in lockstep or the export silently
        # misaligns columns.
        inputs = PSCIRatingInputs(ravelling_pct=5)
        self.assertEqual(set(psci_field_names()), set(inputs.to_dict().keys()))

    def test_from_dict_round_trips_through_to_dict(self):
        original = PSCIRatingInputs(
            ravelling_pct=12, bleeding_pct=3, other_cracking_pct=22,
            structural_distress_pct=40, rutting_depth_mm=80,
            surface_distortion="Significant", patching_condition="Fair",
            pothole_frequency="More Frequent", edge_breakup_extent="Continuous lengths",
            disintegration_present=False, localised_structural_distress=True,
        )
        restored = PSCIRatingInputs.from_dict(original.to_dict())
        self.assertEqual(original, restored)

    def test_from_dict_defaults_missing_fields(self):
        inputs = PSCIRatingInputs.from_dict({})
        self.assertEqual(inputs, PSCIRatingInputs())

    def test_from_dict_tolerates_ui_value_types(self):
        # QSpinBox.value() gives int, QComboBox.currentText() gives str, QCheckBox.isChecked()
        # gives bool -- exactly what psci_viewer._current_defect_values() produces.
        inputs = PSCIRatingInputs.from_dict({
            "RavellingPct": 15, "PatchingCondition": "Good", "DisintegrationPresent": False,
        })
        self.assertEqual(inputs.ravelling_pct, 15.0)
        self.assertEqual(inputs.patching_condition, "Good")
        self.assertFalse(inputs.disintegration_present)


class TestNoDefects(unittest.TestCase):
    def test_blank_section_rates_10(self):
        result = compute_psci_rating(PSCIRatingInputs())
        self.assertEqual(result.rating, 10)
        self.assertEqual(compute_psci_score({}), 10)

    def test_empty_dict_via_compute_psci_score(self):
        self.assertEqual(compute_psci_score({}), 10)


class TestEachBandTriggersFromItsOwnThreshold(unittest.TestCase):
    """Each case below uses only the exact numeric thresholds Table 1 itself gives
    (percentages, mm of rutting) or the categorical value Table 1 names -- nothing invented."""

    def test_rating_9_minor_surface_defects(self):
        result = compute_psci_rating(PSCIRatingInputs(ravelling_pct=5))
        self.assertEqual(result.rating, 9)

    def test_rating_8_moderate_surface_defects_lower_bound(self):
        result = compute_psci_rating(PSCIRatingInputs(bleeding_pct=10))
        self.assertEqual(result.rating, 8)

    def test_rating_8_moderate_surface_defects_upper_bound(self):
        result = compute_psci_rating(PSCIRatingInputs(ravelling_pct=30))
        self.assertEqual(result.rating, 8)

    def test_rating_7_extensive_surface_defects(self):
        result = compute_psci_rating(PSCIRatingInputs(bleeding_pct=31))
        self.assertEqual(result.rating, 7)

    def test_surface_defects_combine_via_max_not_sum(self):
        # Ravelling 5% + Bleeding 8% must not sum to 13% (which would wrongly cross into
        # the 10-30% / rating 8 band) -- Table 1 treats them as one "Ravelling or Bleeding"
        # category, so this takes the worse of the two.
        result = compute_psci_rating(PSCIRatingInputs(ravelling_pct=5, bleeding_pct=8))
        self.assertEqual(result.rating, 9)

    def test_rating_6_moderate_other_pavement_defects(self):
        result = compute_psci_rating(PSCIRatingInputs(other_cracking_pct=15))
        self.assertEqual(result.rating, 6)

    def test_rating_6_good_patching_alone_triggers(self):
        result = compute_psci_rating(PSCIRatingInputs(patching_condition="Good"))
        self.assertEqual(result.rating, 6)

    def test_rating_5_significant_other_pavement_defects(self):
        result = compute_psci_rating(PSCIRatingInputs(other_cracking_pct=25))
        self.assertEqual(result.rating, 5)

    def test_rating_5_localised_structural_distress_clause(self):
        result = compute_psci_rating(PSCIRatingInputs(localised_structural_distress=True))
        self.assertEqual(result.rating, 5)

    def test_rating_4_structural_distress_present(self):
        # Table 1's own worked example: "Alligator cracking, rutting and pothole formation
        # in wheelpath" (Rating 4 photo caption).
        result = compute_psci_rating(PSCIRatingInputs(
            structural_distress_pct=15, pothole_frequency="Frequent",
        ))
        self.assertEqual(result.rating, 4)

    def test_rating_3_significant_areas_of_structural_distress(self):
        result = compute_psci_rating(PSCIRatingInputs(structural_distress_pct=40))
        self.assertEqual(result.rating, 3)

    def test_rating_2_severe_rutting(self):
        result = compute_psci_rating(PSCIRatingInputs(rutting_depth_mm=80))
        self.assertEqual(result.rating, 2)
        self.assertIn("80mm", result.reasons[0])

    def test_rating_2_rutting_exactly_at_threshold_does_not_trigger(self):
        # ">75mm" per Table 1 -- exactly 75mm is not yet "severe".
        result = compute_psci_rating(PSCIRatingInputs(rutting_depth_mm=75))
        self.assertEqual(result.rating, 10)

    def test_rating_1_road_disintegration(self):
        result = compute_psci_rating(PSCIRatingInputs(disintegration_present=True))
        self.assertEqual(result.rating, 1)

    def test_rating_1_failed_patching(self):
        result = compute_psci_rating(PSCIRatingInputs(patching_condition="Failed"))
        self.assertEqual(result.rating, 1)


class TestWorstGoverns(unittest.TestCase):
    def test_worst_matching_band_wins_over_better_ones(self):
        # Minor surface defects (would be rating 9) AND road disintegration (rating 1)
        # present together -> PSCI must be rated at the worse band.
        inputs = PSCIRatingInputs(ravelling_pct=5, disintegration_present=True)
        result = compute_psci_rating(inputs)
        self.assertEqual(result.rating, 1)
        self.assertIn("Road disintegration present", result.reasons)

    def test_all_bands_are_reported_for_audit_trail(self):
        inputs = PSCIRatingInputs(ravelling_pct=5, disintegration_present=True)
        result = compute_psci_rating(inputs)
        ratings_seen = {b.rating for b in result.all_bands}
        self.assertEqual(ratings_seen, {9, 8, 7, 6, 5, 4, 3, 2, 1})
        band_9 = next(b for b in result.all_bands if b.rating == 9)
        self.assertTrue(band_9.reasons)  # still reported even though it didn't govern
        band_1 = next(b for b in result.all_bands if b.rating == 1)
        self.assertEqual(band_1.reasons, result.reasons)


class TestFullPipelineFromUIStyleValuesDict(unittest.TestCase):
    """Mirrors psci_viewer.py's real path: PyQt widgets -> _current_defect_values() dict
    -> stored in self.ratings -> CSV export row, via PSCIRatingInputs.from_dict()."""

    def test_representative_section_end_to_end(self):
        # Table 1's Rating 3 worked example: "Edge problems/cracking, alligator cracking,
        # rutting and potholes."
        ui_values = {
            "RavellingPct": 0, "BleedingPct": 0, "OtherCrackingPct": 0,
            "StructuralDistressPct": 35, "RuttingDepthMm": 40,
            "SurfaceDistortion": "None", "PatchingCondition": "None",
            "PotholeFrequency": "More Frequent", "EdgeBreakupExtent": "Continuous lengths",
            "DisintegrationPresent": False, "LocalisedStructuralDistress": False,
        }
        inputs = PSCIRatingInputs.from_dict(ui_values)
        result = compute_psci_rating(inputs)

        self.assertEqual(result.rating, 3)
        self.assertTrue(any("35%" in r for r in result.reasons))
        self.assertTrue(any("continuous lengths" in r.lower() for r in result.reasons))
        self.assertTrue(any("more frequent" in r.lower() for r in result.reasons))

        # What export_ratings() writes to the audit column.
        basis_column = "; ".join(result.reasons)
        self.assertIn("Structural distress", basis_column)


if __name__ == "__main__":
    unittest.main()
