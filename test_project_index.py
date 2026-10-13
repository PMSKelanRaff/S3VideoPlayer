"""
Tests for project_index.py -- scanning a "Remote PCI"-shaped root folder for
per-project .accdb files and building the sorted, status-labeled dropdown list.

All fixtures are built fresh under the user's Desktop (per project rule) --
never against the real network share, so this suite is fully self-contained
and doesn't depend on any real client job existing.
"""
import os
import shutil
import unittest

import access_db_bridge as bridge
import project_index as pi

_TEMPLATE_PATH = r"P:\Remote PCI\VPCI Application\Resources\Default_VPCI.accdb"
_TEST_ROOT = os.path.join(os.path.expanduser("~"), "Desktop", "pci_app_test_dbs", "fake_remote_pci_root")


def _template_available():
    return os.path.isfile(_TEMPLATE_PATH)


def _python32_available():
    try:
        bridge._find_python32()
        return True
    except bridge.Access32BridgeError:
        return False


def _make_project_db(job_folder, filename, rows):
    """rows: list of (FileDescription, FinishedRating) -- one ProjectData row
    each, all other required columns filled with harmless placeholders."""
    os.makedirs(job_folder, exist_ok=True)
    accdb_path = os.path.join(job_folder, filename)
    shutil.copyfile(_TEMPLATE_PATH, accdb_path)
    os.chmod(accdb_path, 0o666)
    for file_description, finished in rows:
        bridge.execute(
            accdb_path,
            "INSERT INTO [ProjectData] "
            "(FileName, FileDescription, Section, Direction, DateCreated, "
            " JPEGstem, JPEGext, ChainageFrom, ChainageTo, Identifier, "
            " StartedRating, FinishedRating) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ["ROAD", file_description, "", "", "01/01/2026", "", "", 0.0, 1.0, "ROAD0.01.0",
             finished, finished],
        )
    return accdb_path


@unittest.skipUnless(_template_available(), f"Default_VPCI.accdb template not reachable at {_TEMPLATE_PATH}")
@unittest.skipUnless(_python32_available(), "No 32-bit Python interpreter found for the Access bridge")
class TestScanProjects(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)
        os.makedirs(_TEST_ROOT, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(_TEST_ROOT, ignore_errors=True)

    def test_all_rows_finished_gives_finished_status(self):
        job = os.path.join(_TEST_ROOT, "Finished Job 2026")
        _make_project_db(job, "Finished Job 2026 PCI.accdb", [("A", True), ("B", True)])

        summaries = pi.scan_projects(_TEST_ROOT)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].status, pi.STATUS_FINISHED)
        self.assertEqual(summaries[0].segment_count, 2)
        self.assertEqual(summaries[0].finished_segment_count, 2)

    def test_any_unfinished_row_gives_in_progress_status(self):
        job = os.path.join(_TEST_ROOT, "Partial Job 2026")
        _make_project_db(job, "Partial Job 2026 PCI.accdb", [("A", True), ("B", False)])

        summaries = pi.scan_projects(_TEST_ROOT)
        self.assertEqual(summaries[0].status, pi.STATUS_IN_PROGRESS)

    def test_no_rows_at_all_gives_in_progress_not_finished(self):
        # A freshly set-up project with no rating done yet (StartedRating and
        # FinishedRating both false) must never read as "Finished" just
        # because there's nothing to disprove it.
        job = os.path.join(_TEST_ROOT, "New Job 2026")
        _make_project_db(job, "New Job 2026 PCI.accdb", [("A", False)])

        summaries = pi.scan_projects(_TEST_ROOT)
        self.assertEqual(summaries[0].status, pi.STATUS_IN_PROGRESS)

    def test_job_folder_with_no_accdb_is_skipped_not_fatal(self):
        os.makedirs(os.path.join(_TEST_ROOT, "Empty Folder"), exist_ok=True)
        job = os.path.join(_TEST_ROOT, "Real Job 2026")
        _make_project_db(job, "Real Job 2026 PCI.accdb", [("A", True)])

        summaries = pi.scan_projects(_TEST_ROOT)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].job_folder, "Real Job 2026")

    def test_non_project_accdb_is_skipped_not_fatal(self):
        job = os.path.join(_TEST_ROOT, "Weird Folder")
        os.makedirs(job, exist_ok=True)
        # A real .accdb that exists but has no ProjectData table shaped like
        # ours (simulating a stray/unrelated Access file sitting in the folder).
        bogus_path = os.path.join(job, "unrelated.accdb")
        shutil.copyfile(_TEMPLATE_PATH, bogus_path)
        os.chmod(bogus_path, 0o666)
        bridge.execute(bogus_path, "DROP TABLE [ProjectData]")

        summaries = pi.scan_projects(_TEST_ROOT)
        self.assertEqual(summaries, [])

    def test_missing_root_folder_raises(self):
        with self.assertRaises(FileNotFoundError):
            pi.scan_projects(os.path.join(_TEST_ROOT, "does_not_exist"))


class TestSortForDropdown(unittest.TestCase):
    def _summary(self, job_folder, status):
        return pi.ProjectSummary(
            job_folder=job_folder, accdb_path="", file_description=job_folder,
            status=status, segment_count=1, finished_segment_count=1 if status == pi.STATUS_FINISHED else 0,
        )

    def test_in_progress_sorted_before_finished(self):
        summaries = [
            self._summary("Z Finished Job", pi.STATUS_FINISHED),
            self._summary("A In Progress Job", pi.STATUS_IN_PROGRESS),
        ]
        sorted_summaries = pi.sort_for_dropdown(summaries)
        self.assertEqual([s.job_folder for s in sorted_summaries], ["A In Progress Job", "Z Finished Job"])

    def test_alphabetical_within_each_status_group(self):
        summaries = [
            self._summary("Zebra Job", pi.STATUS_IN_PROGRESS),
            self._summary("Alpha Job", pi.STATUS_IN_PROGRESS),
            self._summary("Yankee Job", pi.STATUS_FINISHED),
            self._summary("Bravo Job", pi.STATUS_FINISHED),
        ]
        sorted_summaries = pi.sort_for_dropdown(summaries)
        self.assertEqual(
            [s.job_folder for s in sorted_summaries],
            ["Alpha Job", "Zebra Job", "Bravo Job", "Yankee Job"],
        )

    def test_status_colors_defined_for_both_statuses(self):
        self.assertIn(pi.STATUS_IN_PROGRESS, pi.STATUS_COLORS)
        self.assertIn(pi.STATUS_FINISHED, pi.STATUS_COLORS)


if __name__ == "__main__":
    unittest.main()
