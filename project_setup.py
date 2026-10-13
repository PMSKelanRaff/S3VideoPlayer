"""
"Setup a new project" utility -- turns Paraic's StartEnds.xlsx (the validated
start/end chainages for a job) plus the job's RSP files into a per-project
.accdb file the new app's rating workflow (and eventually the project dropdown)
can use, mirroring the old C# app's Default_VPCI.accdb template-per-project
pattern (see access_db_bridge.py for why Access I/O goes through a 32-bit
helper, and [[project_per_project_accdb_architecture]] in project memory for
why .accdb was kept rather than switching formats).

INPUT SHAPE (confirmed against real files, read-only, during design):
  - StartEnds.xlsx: columns Filename, Location, Lane/Dir, Start, End, Length,
    Width, Comments. One row per road/direction segment. Filename + ".RSP" is
    the RSP file's name; Start/End are chainage in METRES (StartEnds) but the
    app's chainage fields are in KM, so these are divided by 1000 here.
    "Width" (Full/Half lane coverage) is explicitly NOT needed per the team --
    parsed but unused. "Lane/Dir" is kept but not yet acted on.
  - The job's RSP files, one per road, found in the job's folder on the
    network backup share (e.g. "<job folder>\\<ROAD>.RSP").
  - An S3 bucket + per-job prefix (e.g.
    "2026/RSP Project Level Imagery 2026/<job folder name as it appears on S3>")
    -- confirmed pattern: images for a road live under
    "<bucket>/<job_s3_prefix>/<ROAD>/<ROAD>_ROW/<ROAD>    <chainage>.000 1.JPG".
    CONFIRMED (by the manager) to be mechanically derivable from the
    StartEnds.xlsx path itself: "S:\\RSP\\RSP<year>\\Project Level <year>\\<job>"
    -> "<year>/RSP Project Level Imagery <year>/<job>" -- see
    derive_s3_job_prefix(). Not guaranteed byte-perfect though: one real job had
    the job folder name differ by a single punctuation character (comma vs.
    dash) between its S: path and its actual S3 path, so an explicit override is
    still accepted and should be preferred whenever the derived prefix doesn't
    resolve to real objects.

OUTPUT SHAPE, calibrated against a real, fully-rated completed project
(Amarenco Solar farm Mallow Feb24 PCI.accdb, inspected read-only):
  One .accdb per JOB (not per road) -- copied from Default_VPCI.accdb -- with
  one ProjectData row PER ROAD SEGMENT (one per StartEnds.xlsx row), each
  independently StartedRating/FinishedRating. This mirrors the old workflow's
  granularity (one "target" = one RSP file + chainage range, same as
  pci_viewer.py's existing self.qa_segments concept) while keeping "one archive
  file per job" intact. CONFIRMED (by the manager): the dropdown shows ALL
  projects, rated and unrated both -- a finished project is NOT removed from
  the list, since whoever is running the project also needs to reopen finished
  projects to export their results. (An earlier version of this module assumed
  finished projects drop out of the list once FinishedRating=True everywhere;
  that assumption was wrong and has been corrected here before the
  dropdown-scanning code was ever built against it.)

  Identifier (ProjectData, 5mData, and InputData.Road all use this) is
  road + chainage_from_km + chainage_to_km concatenated with no separator --
  e.g. "AO24RA286A" + "1.56" + "5.62" -> "AO24RA286A1.565.62" -- confirmed
  against the real completed project above and against production 5mData seen
  earlier. This is NOT the bare road code: a road can have multiple StartEnds
  rows (Coir Dromcolliher's CR26RB919B does), so the bare road code alone
  would collide between them -- see segment_identifier().

  JPEGstem/JPEGext in the real completed project hold the OLD app's local-file
  naming template (a literal "XXXX.XXX" chainage placeholder in a Windows
  path). That doesn't apply to this app (images are listed from an S3 prefix,
  not addressed by local template substitution), so JPEGstem here instead
  holds the S3 Image0 URI (see build_image0_uri()) and JPEGext is left blank --
  a deliberate repurposing of those two columns, not a literal port of their
  old meaning.

  5mData is populated with GPS only (Identifier, Chainage-in-km, Frame, X/Y/Z
  as text, matching the template's actual column types -- see
  test_access_db_bridge.py's discovery that X/Y/Z are TEXT, not numeric) for
  each road's chainage range as given in StartEnds.xlsx, NOT the RSP file's
  full extent (which may include excluded buffer/rerun footage the
  data-analysis team already stripped out). IRIL/IRIR/IRIC are left NULL --
  the real completed project has them populated with actual roughness
  equipment data, which confirms NULL is correct here, not a gap: we have no
  such data and shouldn't fabricate it.

Does not touch legacy_pci_engine.py/provider.py/types.py/data.py/adapter.py or
pci_viewer.py's rating workflow at all -- this is purely project-creation
plumbing upstream of rating.
"""
import datetime
import os
import re
import shutil
from dataclasses import dataclass
from typing import List, Optional

import openpyxl

import access_db_bridge as bridge
from survey_core import parse_rsp_file

DEFAULT_TEMPLATE_PATH = r"P:\Remote PCI\VPCI Application\Resources\Default_VPCI.accdb"
S3_BUCKET = "pms-survey-imagery"
# Confirmed: this is where completed/in-progress jobs' per-project folders
# (RSP files + local image copies + eventually the generated .accdb) live, and
# "that won't change". Distinct from VPCI ApplicationV2's own Resources\Projects
# folder (a flat store of active .accdb files the OLD app's dropdown may read
# from directly) -- which one the NEW app's project store should mirror is
# still unconfirmed, so this constant is only used for locating a job's RSP
# files / as create_project_database()'s output location, not as a dropdown
# source yet.
REMOTE_PCI_ROOT = r"\\pq05758\2026-LCMS-Backup\Remote PCI"

_RSP_YEAR_FOLDER_RE = re.compile(r"^RSP(\d{4})$", re.IGNORECASE)


class S3PrefixDerivationError(Exception):
    """Raised when a StartEnds.xlsx path doesn't follow the expected
    S:\\RSP\\RSP<year>\\Project Level <year>\\<job>\\... shape closely enough
    to derive an S3 prefix from it -- callers should fall back to an explicit
    s3_job_prefix in this case, not guess further."""


def derive_s3_job_prefix(start_ends_xlsx_path: str) -> str:
    """Confirmed (by the manager) mechanical transform:
    "S:\\RSP\\RSP<year>\\Project Level <year>\\<job>\\..." ->
    "<year>/RSP Project Level Imagery <year>/<job>".
    Not guaranteed byte-perfect -- one real job's folder name differed by a
    single punctuation character between its S: path and its actual S3 path --
    so treat this as a strong default to verify, not an infallible answer."""
    parts = [p for p in start_ends_xlsx_path.replace("/", "\\").split("\\") if p]
    for i, part in enumerate(parts):
        match = _RSP_YEAR_FOLDER_RE.match(part)
        if not match:
            continue
        year = match.group(1)
        if i + 2 < len(parts) and parts[i + 1].lower() == f"project level {year}".lower():
            job_folder = parts[i + 2]
            return f"{year}/RSP Project Level Imagery {year}/{job_folder}"

    raise S3PrefixDerivationError(
        f"Could not find a 'RSP{{year}}\\Project Level {{year}}\\<job>' pattern in "
        f"{start_ends_xlsx_path!r} -- pass s3_job_prefix explicitly instead."
    )


@dataclass(frozen=True)
class RoadSegment:
    road: str                  # e.g. "CR26RB920A" -- matches the RSP filename stem
    chainage_from_km: float
    chainage_to_km: float
    location: str
    lane_dir: str
    width: str                 # parsed, deliberately unused (explicitly not needed)


def parse_start_ends(xlsx_path: str) -> List[RoadSegment]:
    """Reads a StartEnds.xlsx into RoadSegments. Start/End columns are metres;
    converted to km here since that's the unit the app's chainage fields use."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    try:
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    header = [str(h).strip() if h is not None else "" for h in rows[0]]

    def col(name):
        return header.index(name)

    segments = []
    for row in rows[1:]:
        if row[col("Filename")] is None:
            continue
        segments.append(RoadSegment(
            road=str(row[col("Filename")]).strip(),
            chainage_from_km=float(row[col("Start")]) / 1000.0,
            chainage_to_km=float(row[col("End")]) / 1000.0,
            location=str(row[col("Location")] or ""),
            lane_dir=str(row[col("Lane/Dir")] or ""),
            width=str(row[col("Width")] or ""),
        ))
    return segments


class JobFolderLocationError(Exception):
    """Raised when locate_job_folder() can't find exactly one matching job
    folder -- zero candidates, or more than one (ambiguous) -- rather than
    guessing. Callers should report this to the person running setup and let
    them supply the job folder explicitly."""


def locate_job_folder_for_roads(roads, remote_pci_root: str = REMOTE_PCI_ROOT) -> str:
    """Finds the one immediate subfolder of remote_pci_root that contains a
    matching "<road>.RSP" for every road in `roads`. CONTENT-based, not
    name-based: the S: path's job folder name and the Remote PCI job folder
    name follow completely different, unrelated naming conventions in practice
    (confirmed: "CR26RB919- Coir vPCI Dromcolliher, Co. Limerick Sept 2026" on
    S: vs. "Coir Dromcolliher Limerick Sep26 PCI" on Remote PCI, for the exact
    same job) -- so there's no string transform to rely on here, unlike
    derive_s3_job_prefix(). Used both at project-setup time (from a
    StartEnds.xlsx's roads, see locate_job_folder()) and at project-load time
    (from a project .accdb's own ProjectData.FileName values, see
    project_loader.py) -- the same search either way. Raises
    JobFolderLocationError if zero or more than one subfolder qualifies --
    never guesses between multiple candidates."""
    if not os.path.isdir(remote_pci_root):
        raise JobFolderLocationError(f"Remote PCI root not found or not reachable: {remote_pci_root}")

    roads = sorted(set(roads))
    if not roads:
        raise JobFolderLocationError("No roads given to search for.")

    matches = []
    for entry in sorted(os.listdir(remote_pci_root)):
        candidate = os.path.join(remote_pci_root, entry)
        if not os.path.isdir(candidate):
            continue
        if all(os.path.isfile(os.path.join(candidate, f"{road}.RSP")) for road in roads):
            matches.append(candidate)

    if not matches:
        raise JobFolderLocationError(
            f"No folder under {remote_pci_root} contains all of: "
            f"{', '.join(road + '.RSP' for road in roads)}"
        )
    if len(matches) > 1:
        raise JobFolderLocationError(
            f"Multiple folders under {remote_pci_root} contain all the expected RSP files "
            f"-- ambiguous, pass job_folder explicitly instead: {matches}"
        )
    return matches[0]


def locate_job_folder(start_ends_xlsx_path: str, remote_pci_root: str = REMOTE_PCI_ROOT) -> str:
    """locate_job_folder_for_roads(), with the road list taken from a
    StartEnds.xlsx -- see that function for the matching strategy."""
    segments = parse_start_ends(start_ends_xlsx_path)
    roads = [segment.road for segment in segments]
    if not roads:
        raise JobFolderLocationError(f"No road segments parsed from {start_ends_xlsx_path}")
    return locate_job_folder_for_roads(roads, remote_pci_root=remote_pci_root)


def segment_identifier(segment: "RoadSegment") -> str:
    """The real app's Identifier convention, confirmed against actual rated
    project data (ProjectData/5mData/InputData.Road all use this exact form):
    road + chainage_from_km + chainage_to_km, concatenated with NO separator
    and no padding (e.g. "AO24RA286A" + "1.56" + "5.62" ->
    "AO24RA286A1.565.62"). This is what disambiguates multiple StartEnds rows
    for the same road (bare road code alone collides whenever a road has more
    than one segment, as Coir Dromcolliher's CR26RB919B does)."""
    return f"{segment.road}{segment.chainage_from_km}{segment.chainage_to_km}"


def build_image0_uri(s3_job_prefix: str, road: str) -> str:
    """The S3 URI the app's existing CSV loader needs (it only ever takes
    os.path.dirname() of this to find the road's image folder -- see
    pci_viewer.load_selected_qa_segment() -- so this doesn't need to be an
    exact, existing frame, just a correctly-shaped key under the right prefix).
    Confirmed pattern: "<ROAD>/<ROAD>_ROW/<ROAD>    0.000 1.JPG"."""
    prefix = s3_job_prefix.strip("/")
    return f"s3://{S3_BUCKET}/{prefix}/{road}/{road}_ROW/{road}    0.000 1.JPG"


def _rsp_path(job_folder: str, road: str) -> str:
    return os.path.join(job_folder, f"{road}.RSP")


_GPS_GRID_STEP_M = 5.0


def _gps_rows_for_segment(job_folder: str, segment: RoadSegment):
    """(Identifier, Chainage_km, Frame, X, Y, Z) tuples for one road, one row
    every 5 metres across the StartEnds chainage range -- NOT one row per raw
    RSP record. The RSP file's own GPS records are much denser than 5m apart
    (confirmed: ~0.9m spacing in a real file), but the real production 5mData
    table's rows step by exactly 0.005km/5m (confirmed against real data during
    design) -- so each 5m grid point here is filled from whichever RSP record
    is chainage-nearest to it, not every RSP record verbatim."""
    rsp_path = _rsp_path(job_folder, segment.road)
    frames = sorted(parse_rsp_file(rsp_path), key=lambda f: f.get("Chainage", 0.0))
    from_m = segment.chainage_from_km * 1000.0
    to_m = segment.chainage_to_km * 1000.0

    frame_chainages = [f["Chainage"] for f in frames]
    identifier = segment_identifier(segment)
    rows = []
    frame_number = 1
    target_m = from_m
    search_start = 0
    while target_m <= to_m + 1e-9:
        search_start = _nearest_index(frame_chainages, target_m, search_start)
        nearest = frames[search_start]
        rows.append([
            identifier,
            round(target_m / 1000.0, 6),
            frame_number,
            str(nearest.get("Lat", "")),
            str(nearest.get("Lng", "")),
            str(nearest.get("Alt", "")),
        ])
        frame_number += 1
        target_m += _GPS_GRID_STEP_M
    return rows


def _nearest_index(sorted_chainages, target, search_start):
    """Index of the chainage closest to target, searching forward from
    search_start (both target and the grid step advance monotonically, so each
    call can resume where the last left off instead of rescanning from zero)."""
    i = search_start
    n = len(sorted_chainages)
    while i + 1 < n and sorted_chainages[i + 1] <= target:
        i += 1
    if i + 1 < n and abs(sorted_chainages[i + 1] - target) < abs(sorted_chainages[i] - target):
        return i + 1
    return i


def create_project_database(
    job_folder: str,
    start_ends_xlsx: str,
    output_db_path: str,
    file_description: str,
    s3_job_prefix: Optional[str] = None,
    template_path: str = DEFAULT_TEMPLATE_PATH,
) -> List[RoadSegment]:
    """Builds a new project .accdb at output_db_path: copies the template, adds
    one ProjectData row per road segment, and populates 5mData with that
    segment's GPS. Returns the parsed segments (useful for also emitting a
    FileExtentsAWS.csv-equivalent, or for a caller to report what was created).

    s3_job_prefix: pass explicitly when known; if omitted, it's derived from
    start_ends_xlsx's own path via derive_s3_job_prefix() (confirmed mechanical
    transform -- see that function's docstring for the one known way it can be
    wrong). Raises S3PrefixDerivationError if omitted and derivation fails --
    callers should then re-call with s3_job_prefix supplied explicitly rather
    than this function silently guessing further.

    Does not touch job_folder, start_ends_xlsx, or template_path -- all reads.
    output_db_path must not already exist (refuses to overwrite silently)."""
    if s3_job_prefix is None:
        s3_job_prefix = derive_s3_job_prefix(start_ends_xlsx)

    if os.path.exists(output_db_path):
        raise FileExistsError(f"{output_db_path} already exists -- refusing to overwrite.")
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")

    segments = parse_start_ends(start_ends_xlsx)
    if not segments:
        raise ValueError(f"No road segments parsed from {start_ends_xlsx}")

    os.makedirs(os.path.dirname(output_db_path) or ".", exist_ok=True)
    shutil.copyfile(template_path, output_db_path)
    os.chmod(output_db_path, 0o666)  # the template is typically locked read-only

    now_str = datetime.datetime.now().strftime("%d/%m/%Y")

    for segment in segments:
        identifier = segment_identifier(segment)
        bridge.execute(
            output_db_path,
            "INSERT INTO [ProjectData] "
            "(FileName, FileDescription, Section, Direction, DateCreated, "
            " JPEGstem, JPEGext, ChainageFrom, ChainageTo, Identifier, "
            " StartedRating, FinishedRating) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                segment.road, file_description, segment.location,
                segment.lane_dir, now_str, build_image0_uri(s3_job_prefix, segment.road), "",
                segment.chainage_from_km, segment.chainage_to_km, identifier,
                False, False,
            ],
        )

        gps_rows = _gps_rows_for_segment(job_folder, segment)
        if gps_rows:
            bridge.execute_many(
                output_db_path,
                "INSERT INTO [5mData] (Identifier, Chainage, Frame, X, Y, Z) "
                "VALUES (?,?,?,?,?,?)",
                gps_rows,
            )

    return segments


def setup_project_from_request(
    start_ends_xlsx: str,
    output_db_path: str,
    file_description: str,
    remote_pci_root: str = REMOTE_PCI_ROOT,
    s3_job_prefix: Optional[str] = None,
    template_path: str = DEFAULT_TEMPLATE_PATH,
) -> List[RoadSegment]:
    """The end-to-end entry point: given just the StartEnds.xlsx "request",
    locates its job folder under remote_pci_root by RSP-file content (see
    locate_job_folder()), derives the S3 prefix from the xlsx path itself
    unless given explicitly (see derive_s3_job_prefix()), and builds the
    project .accdb (see create_project_database()) -- i.e. everything needed
    to go from "here is a validated start/end chainage spreadsheet" to "this
    project is ready for Brian to rate", with no manually-supplied job folder
    or S3 path required for the common case.

    Raises JobFolderLocationError if the job folder can't be found
    unambiguously, or S3PrefixDerivationError if s3_job_prefix is omitted and
    can't be derived -- both cases should be reported to whoever is running
    setup rather than guessed further."""
    job_folder = locate_job_folder(start_ends_xlsx, remote_pci_root=remote_pci_root)
    return create_project_database(
        job_folder=job_folder,
        start_ends_xlsx=start_ends_xlsx,
        output_db_path=output_db_path,
        file_description=file_description,
        s3_job_prefix=s3_job_prefix,
        template_path=template_path,
    )
