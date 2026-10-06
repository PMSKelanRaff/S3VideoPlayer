"""
Tests for pci_viewer.py's sample-unit state model: persistence of the 17
distress inputs across frames within one ~100m sample unit, fresh defaults for
a newly-entered unit, Rerun stepping back exactly one unit and restoring its
selections, and that re-committing a unit updates (never duplicates) its
legacy_results entry.

These tests never touch S3, AWS, or any real RSP file -- PCIViewer's
constructor is exercised with a patched boto3.client (never actually called,
since fetch_image_bytes is monkeypatched per-instance too), and a target's
frames/metadata/section_boundaries are injected directly exactly as
load_selected_qa_segment() would set them, bypassing the S3 listing/RSP
parsing it would otherwise do. Qt runs headless (QT_QPA_PLATFORM=offscreen).

Does not touch legacy_pci_engine.py/provider.py/types.py/data.py/adapter.py --
those remain exactly as already validated (see test_legacy_pci_golden.py's
1315-case production differential).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from auth import AssumedCredentials
from config import AppConfig
from survey_core import compute_section_boundaries

_app = QApplication.instance() or QApplication(sys.argv)

_CONFIG = AppConfig(
    region="eu-west-1", user_pool_id="x", app_client_id="x",
    identity_pool_id="x", cross_account_role_arn="x",
    bucket_name="test-bucket", bucket_region="eu-west-1",
)
_CREDENTIALS = AssumedCredentials(access_key_id="x", secret_access_key="x", session_token="x")

SECTION_LENGTH_M = 100.0


def _make_viewer():
    with patch("pci_viewer.boto3.client", return_value=MagicMock()):
        from pci_viewer import PCIViewer
        viewer = PCIViewer(_CONFIG, _CREDENTIALS, prefix="test/", username="tester")
    viewer.frame_source.fetch_image_bytes = MagicMock(return_value=b"")
    return viewer


def _load_synthetic_target(viewer, chainages, chainage_from_m, chainage_to_m):
    """Injects frames/metadata/section_boundaries the way
    load_selected_qa_segment() would, without any S3/RSP access, then loads
    frame 0 -- mirroring exactly what that method does after its own setup."""
    viewer.image_keys = [f"frame_{i}.jpg" for i in range(len(chainages))]
    viewer.metadata_list = [
        {"Filename": f"frame_{i}.jpg", "Chainage": c, "Date": "01/01/2026",
         "Lat": "53.0", "Lng": "-7.0", "Alt": "50"}
        for i, c in enumerate(chainages)
    ]
    viewer.current_project_name = "TestRoad"
    viewer.section_boundaries = compute_section_boundaries(
        chainage_from_m, chainage_to_m, SECTION_LENGTH_M
    )
    viewer.current_unit_index = 0
    viewer.unit_selections = {}
    viewer.unit_observation_dates = {}
    viewer.last_committed_unit_index = None
    viewer.legacy_results = {}
    viewer.current_index = -1
    viewer._load_frame(0)


def _three_unit_target(viewer):
    """1000m-1300m, three 100m units (boundaries at 1100/1200/1300), 10 frames
    per unit plus one extra frame exactly on the final boundary (1300) to
    exercise the last-frame-on-final-boundary edge case."""
    chainages = [1000.0 + 10.0 * i for i in range(30)] + [1300.0]
    _load_synthetic_target(viewer, chainages, 1000.0, 1300.0)


def _first_distress_key(viewer):
    return next(iter(viewer.distress_inputs.keys()))


class TestInputsPersistWithinAUnit(unittest.TestCase):
    """Test A."""

    def test_selections_survive_navigating_within_the_same_unit(self):
        viewer = _make_viewer()
        _three_unit_target(viewer)
        key = _first_distress_key(viewer)
        combo = viewer.distress_inputs[key]

        combo.setCurrentIndex(3)
        viewer.next_frame()  # frame 1, still unit 0
        self.assertEqual(viewer.current_unit_index, 0)
        self.assertEqual(combo.currentIndex(), 3)

        viewer.next_frame()  # frame 2, still unit 0
        self.assertEqual(viewer.current_unit_index, 0)
        self.assertEqual(combo.currentIndex(), 3)


class TestNewUnitStartsFresh(unittest.TestCase):
    """Test B."""

    def test_crossing_a_boundary_resets_to_defaults_not_the_previous_units_values(self):
        viewer = _make_viewer()
        _three_unit_target(viewer)
        key = _first_distress_key(viewer)
        combo = viewer.distress_inputs[key]

        combo.setCurrentIndex(3)
        viewer.commit_current_observation()  # unit 0 "committed" with index 3

        # Advance from frame 9 (last frame of unit 0, chainage 1090) to frame
        # 10 (chainage 1100 -- crosses into unit 1).
        for _ in range(10):
            viewer.next_frame()

        self.assertEqual(viewer.current_unit_index, 1)
        self.assertEqual(combo.currentIndex(), 0, "unit 1 must start from defaults, not unit 0's value")


class TestRerunStepsBackOneUnitAtATime(unittest.TestCase):
    """Test C."""

    def test_rerun_moves_c_to_b_to_a_not_to_the_beginning(self):
        viewer = _make_viewer()
        _three_unit_target(viewer)

        viewer._load_frame(25)  # chainage 1250 -> unit 2 ("C")
        self.assertEqual(viewer.current_unit_index, 2)

        viewer.rerun_segment()
        self.assertEqual(viewer.current_unit_index, 1, "first Rerun should land on unit 1 (B), not unit 0")

        viewer.rerun_segment()
        self.assertEqual(viewer.current_unit_index, 0, "second Rerun should land on unit 0 (A)")

        viewer.rerun_segment()
        self.assertEqual(viewer.current_unit_index, 0, "Rerun at the first unit must not go negative/wrap")

    def test_rerun_uses_real_chainages_not_frame_index_minus_100(self):
        """Frames are NOT one-per-metre, so a naive 'index - 100' or
        'chainage - 100' shortcut would land on the wrong frame; the previous
        unit's actual first frame must be found via its real chainage."""
        viewer = _make_viewer()
        _three_unit_target(viewer)

        viewer._load_frame(25)  # unit 2
        viewer.rerun_segment()  # -> unit 1
        landed_chainage = viewer.metadata_list[viewer.current_index]["Chainage"]
        self.assertEqual(landed_chainage, 1100.0)
        self.assertEqual(viewer.current_index, viewer._first_frame_index_for_unit(1))


class TestRerunRestoresPreviousInputs(unittest.TestCase):
    """Test D."""

    def test_rerun_restores_the_values_previously_entered_for_that_unit(self):
        viewer = _make_viewer()
        _three_unit_target(viewer)
        key = _first_distress_key(viewer)
        combo = viewer.distress_inputs[key]

        combo.setCurrentIndex(4)  # e.g. "Potholes Low = c. 10%"-equivalent bucket
        for _ in range(10):  # move into unit 1
            viewer.next_frame()
        self.assertEqual(viewer.current_unit_index, 1)
        combo.setCurrentIndex(1)  # different value in unit 1, must not affect unit 0

        viewer.rerun_segment()  # back to unit 0
        self.assertEqual(viewer.current_unit_index, 0)
        self.assertEqual(combo.currentIndex(), 4, "unit 0's previously entered value must be restored")


class TestRerunDoesNotDuplicateResults(unittest.TestCase):
    """Test E."""

    def test_recommitting_a_rerun_unit_replaces_its_result_not_appends(self):
        viewer = _make_viewer()
        _three_unit_target(viewer)
        key = _first_distress_key(viewer)
        combo = viewer.distress_inputs[key]

        combo.setCurrentIndex(1)
        viewer.commit_current_observation()
        first_result = viewer.legacy_results[0]
        self.assertEqual(len(viewer.legacy_results), 1)

        for _ in range(10):
            viewer.next_frame()
        viewer.rerun_segment()
        self.assertEqual(viewer.current_unit_index, 0)

        combo.setCurrentIndex(5)
        viewer.commit_current_observation()

        self.assertEqual(len(viewer.legacy_results), 1, "re-committing unit 0 must replace, not duplicate, its entry")
        self.assertIsNot(viewer.legacy_results[0], first_result)


class TestSectionAggregationUsesLatestResultOnce(unittest.TestCase):
    """Test F."""

    def test_recalculated_unit_contributes_exactly_once_to_the_section_aggregate(self):
        viewer = _make_viewer()
        _three_unit_target(viewer)
        key = _first_distress_key(viewer)
        combo = viewer.distress_inputs[key]

        combo.setCurrentIndex(1)
        viewer.commit_current_observation()  # unit 0
        for _ in range(10):
            viewer.next_frame()
        combo.setCurrentIndex(2)
        viewer.commit_current_observation()  # unit 1

        self.assertEqual(len(viewer.legacy_results), 2)

        viewer.rerun_segment()  # back to unit 0
        viewer.rerun_segment()  # stays at unit 0 (already first)
        combo.setCurrentIndex(6)
        viewer.commit_current_observation()  # recompute unit 0 again

        self.assertEqual(len(viewer.legacy_results), 2, "recomputing unit 0 must not add a third entry")
        pcis = [r.pci for r in viewer.legacy_results.values()]
        self.assertEqual(len(pcis), 2)


class TestSkippedUnitsRemainExcluded(unittest.TestCase):
    """Test G."""

    def test_skipping_through_a_unit_without_enter_leaves_it_out_of_results(self):
        viewer = _make_viewer()
        _three_unit_target(viewer)
        key = _first_distress_key(viewer)
        combo = viewer.distress_inputs[key]

        combo.setCurrentIndex(3)  # entered but never committed for unit 0
        for _ in range(10):  # skip through unit 0's frames into unit 1
            viewer.skip_frame()
        self.assertEqual(viewer.current_unit_index, 1)

        viewer.commit_current_observation()  # only unit 1 committed

        self.assertNotIn(0, viewer.legacy_results, "a skipped (never-Entered) unit must not appear in legacy_results")
        self.assertIn(1, viewer.legacy_results)
        self.assertEqual(len(viewer.legacy_results), 1)


if __name__ == "__main__":
    unittest.main()
