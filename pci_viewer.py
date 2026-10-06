"""
PCI Viewer.

Lets an inspector step through S3 survey frames, record distress observations
(distress type, severity, quantity bucket) using the methodology-agnostic vocabulary
in distress_catalog.py, and get a PCI for each ~100m sample unit (one per "Enter"
commit -- the UI already pauses playback at each sample-unit boundary) using the
LEGACY VPCI methodology ported from the production C# VPCI_Application -- see
legacy_pci_engine.py's module docstring for exactly what that is and is not. This
viewer contains NO calculation logic of its own: selections are translated by
legacy_pci_adapter.py and scored by legacy_pci_engine.py; this module only displays
and exports the result.

ASTM D6433 PCI and PSCI (the Irish Rural Flexible Roads Manual scheme) were both
explored as alternative methodologies for this application and have since been
removed as a project decision -- this module's PCI is the legacy VPCI methodology's
PCI only.
"""
import csv
import os
import datetime
import boto3
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QMessageBox, QFileDialog, QSizePolicy,
    QSlider, QComboBox, QFrame, QCheckBox, QTabWidget, QLineEdit,
    QToolBar, QApplication
)
from PyQt6.QtGui import QPixmap, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer

from config import AppConfig
from auth import AssumedCredentials
from survey_core import parse_rsp_file, S3FrameSource, compute_section_boundaries
from distress_catalog import (
    DISTRESS_DEFINITIONS, NO_SEVERITY, detailed_field_names, get_distress,
)
from legacy_pci_adapter import build_cells_from_labels
from legacy_pci_engine import calculate_sample_unit_pci, calculate_section_result
from legacy_pci_provider import StaticLegacyPCIProvider

# Sample units are surveyed in fixed 100m lengths (see compute_section_boundaries).
SECTION_LENGTH_M = 100.0

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    _WEBENGINE_AVAILABLE = True
except ImportError:
    _WEBENGINE_AVAILABLE = False


_MAP_HTML_TEMPLATE = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{margin:0;padding:0;height:100%;}</style>
</head><body><div id="map"></div>
<script>
var map = L.map('map').setView([__LAT__, __LNG__], 17);
L.tileLayer('https://__SUBDOMAIN__.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);
L.marker([__LAT__, __LNG__]).addTo(map);
</script></body></html>"""


class PCIViewer(QMainWindow):
    def __init__(self, config: AppConfig, credentials: AssumedCredentials, prefix: str, username: str):
        super().__init__()
        self.config = config
        self.credentials = credentials
        self.bucket_name = config.bucket_name
        self.prefix = prefix
        self.username = username

        self.setWindowTitle("PCI Viewer")

        self.s3 = boto3.client(
            's3',
            region_name=self.config.bucket_region,
            **self.credentials.as_boto_kwargs()
        )
        self.frame_source = S3FrameSource(self.s3, self.bucket_name)

        self.image_keys = []
        self.current_index = -1

        # --- Sample-unit state (NOT frame state) ---
        # The 17 distress inputs describe one ~100m sample unit, not one frame --
        # many frames (visited while scrubbing/playing through the video) belong
        # to the same still-open unit before a boundary is crossed. Everything
        # below is keyed by `unit_index` (0, 1, 2, ... -- which 100m unit within
        # the CURRENTLY loaded target; reset whenever a new target is loaded, see
        # load_selected_qa_segment()), never by frame/image key, so navigating
        # between frames within one unit never loses what's been entered.
        #
        # self.current_unit_index is kept in sync by _load_frame() via
        # _unit_index_for_chainage() on every navigation (Next/Prev/Skip/
        # playback/Rerun alike) -- not just on Enter.
        self.current_unit_index = 0
        self.unit_selections = {}          # unit_index -> {storage_key: label} -- the LIVE (possibly uncommitted) 17-value state for that unit, updated on every frame change, not just Enter
        self.unit_observation_dates = {}   # unit_index -> timestamp of its last Enter commit
        self.last_committed_unit_index = None

        # Legacy VPCI calculation (legacy_pci_engine.py) -- one result per
        # committed sample unit, keyed the same way as unit_selections above. A
        # skipped sample unit never calls commit_current_observation(), so it is
        # simply absent from this dict -- never scored as PCI=0 -- matching the
        # legacy C# app's "no VPCIData row" semantics exactly, with no
        # special-casing needed here.
        self.legacy_provider = StaticLegacyPCIProvider()
        self.legacy_results = {}       # unit_index -> LegacySampleUnitResult

        self.qa_segments = []
        self.full_metadata_list = []
        self.metadata_list = []
        self.current_pixmap = QPixmap()
        self.current_project_name = ""

        # Fixed-length (100m) sample-unit boundaries for the currently loaded target,
        # in absolute chainage metres. Continuous playback auto-pauses at each one.
        self.section_boundaries = []

        self.current_fps = 10
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(int(1000 / self.current_fps))
        self.play_timer.timeout.connect(self._auto_advance_frame)
        self.is_playing = False

        self._build_menu_and_toolbar()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        outer_layout = QVBoxLayout(central_widget)
        outer_layout.setContentsMargins(3, 3, 3, 3)
        outer_layout.setSpacing(3)

        outer_layout.addWidget(self._build_info_bar())

        body_layout = QHBoxLayout()
        body_layout.setSpacing(3)
        body_layout.addWidget(self._build_left_panel(), 1)
        body_layout.addWidget(self._build_right_panel(), 0)
        outer_layout.addLayout(body_layout, 1)

        self._reset_distress_inputs()
        self._setup_shortcuts()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_menu_and_toolbar(self):
        menu_bar = self.menuBar()

        project_menu = menu_bar.addMenu("Project")
        act_close = project_menu.addAction("Close")
        act_close.triggered.connect(self.close)

        menu_bar.addMenu("Files")

        toolbar = QToolBar("Segments")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        btn_load_csv = QPushButton("Load FileExtentsAWS.csv")
        btn_load_csv.setStyleSheet("background-color: #1976d2; color: white; font-weight: bold; padding: 6px;")
        btn_load_csv.clicked.connect(self.load_qa_csv)
        toolbar.addWidget(btn_load_csv)

        self.qa_combo = QComboBox()
        self.qa_combo.setMinimumWidth(280)
        toolbar.addWidget(self.qa_combo)

        btn_load_segment = QPushButton("Load Selected Target")
        btn_load_segment.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold; padding: 6px;")
        btn_load_segment.clicked.connect(self.load_selected_qa_segment)
        toolbar.addWidget(btn_load_segment)

    def _build_info_bar(self):
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setMaximumHeight(64)
        grid = QGridLayout(frame)
        grid.setContentsMargins(6, 3, 6, 3)
        grid.setVerticalSpacing(2)

        self.field_section = self._add_info_field(grid, 0, 0, "Section:")
        self.field_dir = self._add_info_field(grid, 0, 2, "Dir:")
        self.field_date = self._add_info_field(grid, 0, 4, "Date:")
        self.field_current_project = self._add_info_field(grid, 0, 6, "Current Project:")

        self.field_filename = self._add_info_field(grid, 1, 0, "Filename:")
        self.field_chainage = self._add_info_field(grid, 1, 2, "Chainage:")
        self.field_length = self._add_info_field(grid, 1, 4, "Length:")
        self.field_image_info = self._add_info_field(grid, 1, 6, "Image Info:")

        self.fps_label = QLabel(f"Video Speed: {self.current_fps} FPS")
        grid.addWidget(self.fps_label, 0, 8)
        self.fps_slider = QSlider(Qt.Orientation.Horizontal)
        self.fps_slider.setMinimum(1)
        self.fps_slider.setMaximum(30)
        self.fps_slider.setValue(self.current_fps)
        self.fps_slider.setFixedWidth(120)
        self.fps_slider.valueChanged.connect(self._update_fps)
        grid.addWidget(self.fps_slider, 1, 8)

        return frame

    def _add_info_field(self, grid, row, col, label_text):
        grid.addWidget(QLabel(label_text), row, col)
        field = QLineEdit()
        field.setReadOnly(True)
        field.setFixedWidth(120)
        field.setMaximumHeight(20)
        grid.addWidget(field, row, col + 1)
        return field

    def _build_left_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image_label.setMinimumSize(400, 300)
        self.image_label.setStyleSheet("background-color: black; color: white;")
        layout.addWidget(self.image_label, 1)

        self.status_label = QLabel(f"Logged in as: {self.username}")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-size: 8pt; font-weight: bold; padding: 1px;")
        self.status_label.setMaximumHeight(16)
        layout.addWidget(self.status_label)

        return panel

    def _build_right_panel(self):
        panel = QWidget()
        panel.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        # Hard ceiling on the whole column: whatever any child widget's internal size hint
        # claims (QWebEngineView is the known offender — see _build_map_tabs), the panel
        # itself can never grow past this, so the image column always keeps its space.
        panel.setMaximumWidth(360)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        layout.addWidget(self._build_distress_observation_grid())

        self.chk_play_after_enter = QCheckBox("Play after Enter")
        layout.addWidget(self.chk_play_after_enter)

        self.btn_enter = QPushButton("Enter")
        self.btn_enter.setMinimumHeight(28)
        self.btn_enter.clicked.connect(self.commit_current_observation)
        layout.addWidget(self.btn_enter)

        # Legacy VPCI calculation result for the most recently committed sample
        # unit, plus the running section/road aggregate -- both computed entirely
        # by legacy_pci_engine.py; this module only formats the display text.
        self.lbl_sample_unit_result = QLabel("Sample Unit PCI: —")
        self.lbl_sample_unit_result.setStyleSheet("font-weight: bold;")
        self.lbl_sample_unit_result.setWordWrap(True)
        layout.addWidget(self.lbl_sample_unit_result)

        self.lbl_section_result = QLabel("Section vPCI: —")
        self.lbl_section_result.setWordWrap(True)
        layout.addWidget(self.lbl_section_result)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(3)
        self.btn_play = QPushButton(f"▶ Play ({self.current_fps} FPS)")
        self.btn_play.setStyleSheet("font-weight: bold; background-color: #2e7d32; color: white;")
        self.btn_play.clicked.connect(self.toggle_playback)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self.pause_playback)
        self.btn_rerun = QPushButton("Rerun")
        self.btn_rerun.clicked.connect(self.rerun_segment)
        self.btn_skip = QPushButton("Skip")
        self.btn_skip.clicked.connect(self.skip_frame)
        for btn in (self.btn_play, self.btn_pause, self.btn_rerun, self.btn_skip):
            btn.setMinimumHeight(26)
            controls_layout.addWidget(btn)
        layout.addLayout(controls_layout)

        self.chk_continuous_vcs = QCheckBox("Run Continuous VCS (loop at end of segment)")
        layout.addWidget(self.chk_continuous_vcs)

        self.btn_export = QPushButton("Export CSV")
        self.btn_export.setMinimumHeight(26)
        self.btn_export.clicked.connect(self.export_observations)
        layout.addWidget(self.btn_export)

        layout.addWidget(self._build_map_tabs(), 1)

        return panel

    def _build_distress_observation_grid(self):
        """Config-driven distress entry grid (vocabulary from distress_catalog.py):
        one dropdown per (distress, severity) cell, matching the Low/Medium/High
        severity-bucket layout the grid was originally built from. Purely records
        what the inspector selects -- no rating or score is calculated here;
        legacy_pci_adapter.py translates these raw selections into legacy_pci_engine.py's
        input cells (see commit_current_observation())."""
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(1)

        columns = ["Low", "Medium", "High"]
        for col, heading in enumerate(columns, start=1):
            label = QLabel(f"<b>{heading}</b>")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(label, 0, col)

        self.distress_inputs = {}  # (distress_id, severity) -> QComboBox
        row = 1
        severity_distresses = [d for d in DISTRESS_DEFINITIONS if d.severities]
        single_distresses = [d for d in DISTRESS_DEFINITIONS if not d.severities]

        for d in severity_distresses:
            grid.addWidget(QLabel(f"{d.label}:"), row, 0)
            for col, heading in enumerate(columns, start=1):
                if heading not in d.severities:
                    continue  # e.g. Rutting has no Low severity in the catalog
                combo = QComboBox()
                combo.addItems(d.options[heading])
                combo.setMaximumHeight(22)
                grid.addWidget(combo, row, col)
                self.distress_inputs[(d.distress_id, heading)] = combo
            row += 1

        for d in single_distresses:
            grid.addWidget(QLabel(f"{d.label}:"), row, 0)
            combo = QComboBox()
            combo.addItems(d.options[NO_SEVERITY])
            combo.setMaximumHeight(22)
            grid.addWidget(combo, row, 1)
            self.distress_inputs[(d.distress_id, NO_SEVERITY)] = combo
            row += 1

        return frame

    def _build_map_tabs(self):
        # QWebEngineView reports a large default size hint (both dimensions) once it's
        # actually rendering a page, independent of the space it's given, and a QTabWidget
        # sizes itself to fit the largest of ALL its pages' hints, not just the visible one.
        # Left unconstrained, that lets the map balloon in width AND height and squeeze the
        # rest of the panel. Cap both the views and the tab widget on both axes.
        MAP_WIDTH = 340
        MAP_HEIGHT = 260
        self.map_tabs = QTabWidget()
        self.map_tabs.setMaximumSize(MAP_WIDTH + 8, MAP_HEIGHT + 32)  # + borders / tab bar

        if _WEBENGINE_AVAILABLE:
            self.map_view = QWebEngineView()
            self.map_view.setFixedSize(MAP_WIDTH, MAP_HEIGHT)
            self.map_tabs.addTab(self.map_view, "GPS")
        else:
            placeholder = QLabel(
                "GPS map unavailable.\nInstall PyQt6-WebEngine to enable the live map."
            )
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.map_view = None
            self.map_tabs.addTab(placeholder, "GPS")

        return self.map_tabs

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Space"), self, self.toggle_playback)
        QShortcut(QKeySequence("Right"), self, self.skip_frame)
        QShortcut(QKeySequence("Left"), self, self.prev_frame)
        QShortcut(QKeySequence("Return"), self, self.commit_current_observation)

    # ------------------------------------------------------------------
    # Segment / frame loading (mirrors the Image Viewer's QA workflow)
    # ------------------------------------------------------------------
    def load_qa_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select QA Segments CSV", "", "CSV Files (*.csv)")
        if not path:
            return

        try:
            self.qa_segments.clear()
            self.qa_combo.clear()

            with open(path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self.qa_segments.append(row)
                    fname = os.path.basename(row.get('RSPFile', 'Unknown RSP'))
                    c_from = row.get('ChainageFrom', '0')
                    c_to = row.get('ChainageTo', '0')
                    self.qa_combo.addItem(f"{fname} ({c_from} to {c_to} km)")

            QMessageBox.information(self, "Success", f"Loaded {len(self.qa_segments)} QA segments.")

            # Load the first target immediately so there's no extra button press to see
            # something on screen. Switching targets afterwards still requires an explicit
            # "Load Selected Target" click, so an accidental combo-box change can't silently
            # discard in-progress observations.
            if self.qa_combo.count() > 0:
                self.load_selected_qa_segment()
        except Exception as e:
            QMessageBox.critical(self, "Error Loading QA CSV", f"Failed to read CSV:\n{e}")

    def load_selected_qa_segment(self):
        idx = self.qa_combo.currentIndex()
        if idx < 0:
            return

        segment = self.qa_segments[idx]
        image0 = segment.get('Image0', '')

        if "://" in image0:
            key_part = image0.split("://")[1]
            if key_part.startswith(self.bucket_name + "/"):
                key_part = key_part[len(self.bucket_name) + 1:]
        else:
            key_part = image0

        self.prefix = os.path.dirname(key_part) + "/"

        try:
            qa_chainage_from = float(segment.get('ChainageFrom', 0)) * 1000
            qa_chainage_to = float(segment.get('ChainageTo', 0)) * 1000
        except ValueError:
            qa_chainage_from = 0.0
            qa_chainage_to = 9999999.0

        rsp_path = segment.get('RSPFile', '')
        if not os.path.exists(rsp_path):
            QMessageBox.warning(self, "RSP Not Found", f"RSP file not found at:\n{rsp_path}\n\nPlease locate it manually.")
            rsp_path, _ = QFileDialog.getOpenFileName(self, "Locate RSP File", "", "RSP Files (*.rsp *.RSP)")
            if not rsp_path:
                return

        self.full_metadata_list = parse_rsp_file(rsp_path)
        self.current_project_name = os.path.splitext(os.path.basename(rsp_path))[0]

        self.status_label.setText("Fetching new images from S3...")
        QApplication.processEvents()

        try:
            full_image_keys = self.frame_source.list_frames(self.prefix)
        except Exception as e:
            QMessageBox.critical(self, "S3 Error", f"Could not load bucket data: {str(e)}")
            return

        self.image_keys, self.metadata_list = self.frame_source.filter_by_chainage(
            full_image_keys, self.full_metadata_list, qa_chainage_from, qa_chainage_to
        )

        self.section_boundaries = compute_section_boundaries(
            qa_chainage_from, qa_chainage_to, SECTION_LENGTH_M
        )

        # unit_index is only unique WITHIN a loaded target (it restarts at 0 for
        # every target), so loading a different target must not carry over the
        # previous target's sample-unit state -- otherwise e.g. unit 0 of a newly
        # loaded road would show the previous road's unit 0 selections.
        self.current_unit_index = 0
        self.unit_selections = {}
        self.unit_observation_dates = {}
        self.last_committed_unit_index = None
        self.legacy_results = {}

        self.field_section.setText(os.path.basename(segment.get('RSPFile', '')))
        self.field_current_project.setText(self.current_project_name)
        length_km = float(segment.get('ChainageTo', 0)) - float(segment.get('ChainageFrom', 0))
        self.field_length.setText(f"{length_km:.3f} km")

        self.status_label.setText(f"Loaded {len(self.image_keys)} frames | Space = Play/Pause | Enter = Save observation")

        if self.image_keys:
            self.current_index = -1
            self._load_frame(0)
        else:
            QMessageBox.warning(self, "No Images Found", "No images found in the specified chainage range.")

    # ------------------------------------------------------------------
    # Frame navigation
    # ------------------------------------------------------------------
    def _load_frame(self, index):
        if index < 0 or index >= len(self.image_keys):
            return

        if self.current_index >= 0:
            # Stash whatever is currently in the 17 widgets as the OUTGOING
            # frame's sample unit's live state before anything else changes.
            # Runs on every navigation (Next/Prev/Skip/playback tick/Rerun
            # alike), not just on Enter, so in-progress edits are never lost
            # merely because another frame was loaded within (or even out of)
            # the same 100m sample unit.
            self.unit_selections[self.current_unit_index] = self._current_distress_selections()

        self.current_index = index
        key = self.image_keys[index]
        filename = key.split('/')[-1]
        metadata = self.metadata_list[index] if index < len(self.metadata_list) else {}

        self.field_filename.setText(metadata.get("Filename", filename))
        self.field_chainage.setText(str(metadata.get("Chainage", "N/A")))
        self.field_date.setText(str(metadata.get("Date", "N/A")))
        self.field_image_info.setText(f"{index + 1} / {len(self.image_keys)}")

        try:
            image_data = self.frame_source.fetch_image_bytes(key)
            self.current_pixmap.loadFromData(image_data)
            self._update_image_display()
        except Exception as e:
            self.image_label.setText(f"Error loading image:\n{str(e)}")

        new_unit_index = self._unit_index_for_chainage(metadata.get("Chainage"))
        crossed = new_unit_index != self.current_unit_index
        self.current_unit_index = new_unit_index

        # Restore (or, for a unit never visited before, default to "0") the 17
        # widgets for the sample unit this frame now belongs to -- the widgets
        # are a view onto self.unit_selections, not independent state.
        self._populate_distress_inputs(self.unit_selections.get(self.current_unit_index))

        if index == 0 or crossed:
            # Only refresh the map once per ~100m sample unit (its first frame, or
            # whenever a new boundary is crossed) instead of on every single frame --
            # reloading the embedded web view that often during playback is what was
            # making it visibly redraw/flicker continuously.
            self._update_map(metadata.get("Lat"), metadata.get("Lng"))

        if crossed and self.is_playing:
            self.pause_playback()
            self.status_label.setText(
                f"Sample unit boundary reached at {metadata.get('Chainage')}m — "
                f"record this section's observations, then Play to continue."
            )
        else:
            self.status_label.setText(f"Frame {index + 1} of {len(self.image_keys)} | {filename}")

    def _unit_index_for_chainage(self, chainage):
        """Which 100m sample unit (0-based, within the currently loaded target)
        a given chainage belongs to: the count of section_boundaries at or
        before it -- a boundary marks where one unit ends and the next begins.
        Matches the original playback-pause comparison exactly (`>=`), but,
        unlike a monotonic running pointer, works correctly for backward
        navigation too (Left arrow, Rerun), not just forward playback."""
        if chainage is None:
            return self.current_unit_index
        idx = 0
        for boundary in self.section_boundaries:
            if chainage >= boundary:
                idx += 1
            else:
                break
        if self.section_boundaries:
            # section_boundaries[i] is the END of unit i, so valid unit indices
            # are 0..len-1. A chainage exactly AT the final boundary (the last
            # frame of the whole target) would otherwise count as entering one
            # past the last real unit -- clamp it back to the true last unit.
            idx = min(idx, len(self.section_boundaries) - 1)
        return idx

    def _first_frame_index_for_unit(self, unit_index):
        """The index (into self.image_keys/self.metadata_list) of the first
        frame belonging to the given sample unit, or None if that unit has no
        frames in the currently loaded target."""
        for i, meta in enumerate(self.metadata_list):
            if self._unit_index_for_chainage(meta.get("Chainage")) == unit_index:
                return i
        return None

    def showEvent(self, event):
        super().showEvent(event)
        self._update_image_display()

    def resizeEvent(self, event):
        self._update_image_display()
        super().resizeEvent(event)

    def _update_image_display(self):
        if not self.current_pixmap.isNull():
            scaled = self.current_pixmap.scaled(
                self.image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.image_label.setPixmap(scaled)

    def next_frame(self):
        if self.current_index < len(self.image_keys) - 1:
            self._load_frame(self.current_index + 1)

    def prev_frame(self):
        if self.current_index > 0:
            self._load_frame(self.current_index - 1)

    def skip_frame(self):
        """Advance without pressing Enter for the current sample unit. Purely a
        navigation action -- whatever is currently in the widgets is still
        preserved via _load_frame()'s save-before-leaving step above, so this
        never discards in-progress distress selections; it only means no
        legacy_results entry gets created unless/until Enter is pressed."""
        self.next_frame()

    def rerun_segment(self):
        """Step back exactly one 100m sample unit from the current position and
        reopen it for editing: jumps to that unit's first frame and restores
        whatever selections are currently held for it (committed or not) into
        the 17 widgets, so the inspector can review/correct it -- pressing
        Enter again overwrites that unit's committed result."""
        self.pause_playback()
        if not self.image_keys:
            return
        target_unit_index = max(0, self.current_unit_index - 1)
        target_frame_index = self._first_frame_index_for_unit(target_unit_index)
        if target_frame_index is not None:
            self._load_frame(target_frame_index)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def _update_fps(self, value):
        self.current_fps = value
        self.play_timer.setInterval(int(1000 / self.current_fps))
        self.fps_label.setText(f"Video Speed: {self.current_fps} FPS")
        if not self.is_playing:
            self.btn_play.setText(f"▶ Play ({self.current_fps} FPS)")

    def toggle_playback(self):
        if self.is_playing:
            self.pause_playback()
            return

        if not self.image_keys:
            return

        self.is_playing = True
        self.btn_play.setText("⏸ Pause")
        self.btn_play.setStyleSheet("font-weight: bold; background-color: #c62828; color: white;")
        self.play_timer.start()

    def pause_playback(self):
        if self.is_playing:
            self.play_timer.stop()
            self.is_playing = False
            self.btn_play.setText(f"▶ Play ({self.current_fps} FPS)")
            self.btn_play.setStyleSheet("font-weight: bold; background-color: #2e7d32; color: white;")

    def _auto_advance_frame(self):
        """Timer-driven playback tick: advances a frame, looping at the end
        of the segment if 'Run Continuous VCS' is checked, otherwise pausing."""
        if self.current_index < len(self.image_keys) - 1:
            self._load_frame(self.current_index + 1)
        elif self.chk_continuous_vcs.isChecked() and self.image_keys:
            self._load_frame(0)
        else:
            self.pause_playback()

    # ------------------------------------------------------------------
    # Distress observation entry
    # ------------------------------------------------------------------
    def _reset_distress_inputs(self):
        self._populate_distress_inputs(None)

    def _populate_distress_inputs(self, saved_values):
        saved_values = saved_values or {}
        for (distress_id, severity), combo in self.distress_inputs.items():
            storage_key = self._distress_storage_key(distress_id, severity)
            saved_label = saved_values.get(storage_key)
            index = combo.findText(saved_label) if saved_label else -1
            combo.setCurrentIndex(index if index >= 0 else 0)

    def _distress_storage_key(self, distress_id, severity):
        return get_distress(distress_id).storage_key(severity)

    def _current_distress_selections(self):
        """The inspector's raw, currently-displayed selections -- distress storage
        key -> selected bucket label -- for whichever sample unit is on screen.
        No calculation happens here; legacy_pci_adapter.build_cells_from_labels()
        is the only translation layer into legacy_pci_engine.py."""
        values = {}
        for (distress_id, severity), combo in self.distress_inputs.items():
            storage_key = self._distress_storage_key(distress_id, severity)
            values[storage_key] = combo.currentText()
        return values

    def commit_current_observation(self):
        if self.current_index < 0 or self.current_index >= len(self.image_keys):
            return

        unit_index = self.current_unit_index
        # Snapshot whatever is currently in the widgets as this sample unit's
        # final, official selections -- overwrites any earlier commit for this
        # same unit (e.g. after Rerun + edits).
        self.unit_selections[unit_index] = self._current_distress_selections()
        self.unit_observation_dates[unit_index] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        first_frame = self._first_frame_index_for_unit(unit_index)
        start_chainage = (self.metadata_list[first_frame].get("Chainage")
                           if first_frame is not None and first_frame < len(self.metadata_list) else None)
        sample_unit_id = f"{self.current_project_name or 'unknown'}#{unit_index}" \
            + (f"@{start_chainage}" if start_chainage is not None else "")

        # legacy_pci_adapter.py is the ONLY translation layer between these raw
        # selections and legacy_pci_engine.py -- no TDV/q/finalDeduct/PCI
        # arithmetic happens in this module.
        cells = build_cells_from_labels(self.unit_selections[unit_index])
        self.legacy_results[unit_index] = calculate_sample_unit_pci(
            section_id=self.current_project_name or "unknown",
            sample_unit_id=sample_unit_id,
            cells=cells,
            provider=self.legacy_provider,
        )
        self.last_committed_unit_index = unit_index
        self._update_legacy_pci_display()

        if self.chk_play_after_enter.isChecked():
            self.next_frame()

    def _update_legacy_pci_display(self):
        """Refreshes the sample-unit and section-level legacy PCI labels. All
        arithmetic is legacy_pci_engine.py's; this method only formats its results
        for display."""
        latest = self.legacy_results.get(self.last_committed_unit_index)
        if latest is None:
            self.lbl_sample_unit_result.setText("Sample Unit PCI: —")
        else:
            self.lbl_sample_unit_result.setText(
                f"Sample Unit PCI: {latest.pci}  "
                f"(TDV={latest.total_deduct_value:.1f}, q={latest.q}, "
                f"maxDeduct={latest.max_deduct_value:.1f}, "
                f"finalDeduct={latest.final_deduct:.1f})"
            )

        if not self.legacy_results:
            self.lbl_section_result.setText("Section vPCI: —")
            return

        section = calculate_section_result(
            self.current_project_name or "unknown",
            [r.pci for r in self.legacy_results.values()],
        )
        self.lbl_section_result.setText(
            f"Section vPCI: {section.vpci}  Rating: {section.rating}  "
            f"StdDev: {section.standard_deviation}  (n={len(section.sample_unit_pcis)})"
        )

    # ------------------------------------------------------------------
    # GPS map
    # ------------------------------------------------------------------
    def _update_map(self, lat, lng):
        if self.map_view is None:
            return
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (TypeError, ValueError):
            return

        subdomain = "a"
        html = (_MAP_HTML_TEMPLATE
                .replace("__LAT__", str(lat_f))
                .replace("__LNG__", str(lng_f))
                .replace("__SUBDOMAIN__", subdomain))
        self.map_view.setHtml(html)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_observations(self):
        if not self.unit_selections:
            QMessageBox.information(self, "Export Observations", "No frames have been recorded yet.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Pavement Inspection CSV", "pavement_inspection.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        detail_fields = detailed_field_names()

        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["Filename", "Frame", "Chainage", "Lat", "Lng", "Alt", "Date",
                     "DistanceFromLastReading(m)"] + detail_fields
                    + ["PCI", "TDV", "q", "MaxDeduct", "FinalDeduct"]
                )

                # One row per 100m sample unit (not per frame) -- each row is
                # whatever is currently held in self.unit_selections for that
                # unit, whether or not Enter was ever pressed for it.
                last_written_chainage = None

                for unit_index in sorted(self.unit_selections.keys()):
                    values = self.unit_selections[unit_index]

                    frame_num = self._first_frame_index_for_unit(unit_index)
                    meta = (self.metadata_list[frame_num]
                            if frame_num is not None and frame_num < len(self.metadata_list) else {})

                    filename = meta.get("Filename", "")
                    chainage = meta.get("Chainage", 0.0)
                    lat = meta.get("Lat", "")
                    lng = meta.get("Lng", "")
                    alt = meta.get("Alt", "")
                    date_val = meta.get("Date", "")

                    if last_written_chainage is not None and isinstance(chainage, (int, float)):
                        dist = round(abs(chainage - last_written_chainage), 3)
                    else:
                        dist = 0

                    if isinstance(chainage, (int, float)):
                        last_written_chainage = chainage

                    legacy_result = self.legacy_results.get(unit_index)
                    if legacy_result is not None:
                        legacy_fields = [
                            legacy_result.pci, legacy_result.total_deduct_value,
                            legacy_result.q, legacy_result.max_deduct_value,
                            legacy_result.final_deduct,
                        ]
                    else:
                        legacy_fields = ["", "", "", "", ""]

                    writer.writerow(
                        [filename, (frame_num + 1) if frame_num is not None else "N/A",
                         chainage, lat, lng, alt, date_val, dist]
                        + [values.get(field, "") for field in detail_fields]
                        + legacy_fields
                    )

                if self.legacy_results:
                    # Section/road-level aggregate -- legacy_pci_engine.py's
                    # calculate_section_result(), not recomputed here.
                    section = calculate_section_result(
                        self.current_project_name or "unknown",
                        [r.pci for r in self.legacy_results.values()],
                    )
                    writer.writerow([])
                    writer.writerow([
                        "Section vPCI", section.vpci, "Rating", section.rating,
                        "Standard Deviation", section.standard_deviation,
                    ])

                current_date = datetime.datetime.now().strftime("%d/%m/%Y")
                writer.writerow([f"{self.username} - {current_date}"])
                writer.writerow(["END"])

            QMessageBox.information(self, "Export Successful", f"Saved pavement inspection data to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to save CSV file:\n{str(e)}")
