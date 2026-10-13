"""
Scans the shared network "Remote PCI" root for per-project .accdb files
(created by project_setup.py) and builds the list pci_viewer.py's project
dropdown will eventually show: ALL projects, rated and unrated (confirmed by
the manager -- a finished project must stay visible so its results can be
re-exported), sorted with in-progress projects first, each carrying a status
label for display ("In Progress" shown yellow/orange, "Finished" shown green
-- see STATUS_COLORS; this module only decides WHICH color/label, rendering
them is left to whatever UI toolkit ends up wiring this in).

A job's .accdb file is found by convention: any *.accdb file directly inside
one of the root's immediate subfolders (one level deep, not recursive) that
actually has a ProjectData table -- not a single fixed filename, since real
archived project files carry descriptive, job-specific names (e.g. "Adman
L3001 Rathangan Jan23 PCI.accdb"), not one generic name. A file that isn't a
real project database (wrong schema, locked, corrupt, unrelated .accdb left in
the folder) is skipped, not treated as fatal -- one bad file must never
prevent the rest of the list from showing.

A job's overall status is STATUS_FINISHED only when EVERY ProjectData row in
its .accdb has FinishedRating=True; otherwise STATUS_IN_PROGRESS (covers:
nothing rated yet, partially rated, or multiple road segments at different
stages of completion).
"""
import glob
import os
from dataclasses import dataclass
from typing import List

import access_db_bridge as bridge

STATUS_IN_PROGRESS = "In Progress"
STATUS_FINISHED = "Finished"

# Framework-agnostic: (label, hex color) pairs for whichever UI toolkit ends up
# rendering this -- kept here, not duplicated per-widget-library, so there's
# one place that defines what "finished" vs "in progress" looks like.
STATUS_COLORS = {
    STATUS_IN_PROGRESS: "#F57C00",  # yellow-orange
    STATUS_FINISHED: "#2E7D32",     # green
}


@dataclass(frozen=True)
class ProjectSummary:
    job_folder: str        # e.g. "Coir Dromcolliher Limerick Sep26 PCI"
    accdb_path: str
    file_description: str
    status: str             # STATUS_IN_PROGRESS or STATUS_FINISHED
    segment_count: int
    finished_segment_count: int


def _probe_project_db(accdb_path: str):
    """Returns (file_description, finished_count, total_count), or None if
    accdb_path isn't a usable project database -- missing/mismatched schema,
    locked, corrupt, etc. Callers skip it rather than failing the whole scan."""
    try:
        rows = bridge.query(
            accdb_path, "SELECT FileDescription, FinishedRating FROM [ProjectData]", readonly=True
        )
    except bridge.AccessQueryError:
        return None
    if not rows:
        return None
    file_description = rows[0].get("FileDescription") or os.path.splitext(os.path.basename(accdb_path))[0]
    finished_count = sum(1 for r in rows if r.get("FinishedRating"))
    return file_description, finished_count, len(rows)


def scan_projects(root_folder: str) -> List[ProjectSummary]:
    """One level deep: root_folder/<job folder>/*.accdb. Skips job folders
    with no .accdb and .accdb files that don't look like a real project
    database. Only raises for root_folder itself being unreachable -- never
    for an individual bad subfolder/file."""
    if not os.path.isdir(root_folder):
        raise FileNotFoundError(f"Project root not found or not reachable: {root_folder}")

    summaries = []
    for entry in sorted(os.listdir(root_folder)):
        job_folder_path = os.path.join(root_folder, entry)
        if not os.path.isdir(job_folder_path):
            continue
        for accdb_path in glob.glob(os.path.join(job_folder_path, "*.accdb")):
            probed = _probe_project_db(accdb_path)
            if probed is None:
                continue
            file_description, finished_count, total_count = probed
            status = (
                STATUS_FINISHED if total_count > 0 and finished_count == total_count
                else STATUS_IN_PROGRESS
            )
            summaries.append(ProjectSummary(
                job_folder=entry, accdb_path=accdb_path, file_description=file_description,
                status=status, segment_count=total_count, finished_segment_count=finished_count,
            ))
            break  # one project .accdb per job folder is the expected shape
    return summaries


def sort_for_dropdown(summaries: List[ProjectSummary]) -> List[ProjectSummary]:
    """In Progress first, then Finished -- each group alphabetical by job
    folder name. Matches the confirmed requirement that in-progress projects
    surface to the top of the list."""
    return sorted(summaries, key=lambda s: (s.status == STATUS_FINISHED, s.job_folder.lower()))
