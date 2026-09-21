import csv
import os
import datetime
import boto3
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QMessageBox, QFileDialog, QSizePolicy,
    QSlider, QComboBox, QFrame, QCheckBox, QTabWidget, QLineEdit,
    QToolBar, QApplication, QSpinBox
)
from PyQt6.QtGui import QPixmap, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer

from config import AppConfig
from auth import AssumedCredentials
from survey_core import (
    parse_rsp_file, S3FrameSource, compute_section_boundaries,
    PSCI_PERCENT_FIELDS, PSCI_RUTTING_DEPTH_FIELD, PSCI_CATEGORY_FIELDS,
    PSCI_BOOLEAN_FIELDS, psci_field_names,
    RATING_COLORS,
)
from psci_scoring import PSCIRatingInputs, compute_psci_rating, compute_psci_score

# PSCI sections are surveyed in fixed 100m lengths (see compute_section_boundaries).
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

_SCORE_MAP_HTML_TEMPLATE = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{margin:0;padding:0;height:100%;}</style>
</head><body><div id="map"></div>
<script>
var map = L.map('map').setView([__LAT__, __LNG__], __ZOOM__);
L.tileLayer('https://a.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);
__MARKERS__
</script></body></html>"""


def build_score_map_html(markers):
    """markers: list of (lat, lng, score) tuples, one per rated PSCI section.
    Returns a Leaflet/OSM HTML page with one colour-coded marker per section
    (colour from survey_core.RATING_COLORS, same palette the Image Viewer uses).
    Pure/no Qt dependency so it can be unit tested directly."""
    if not markers:
        center_lat, center_lng, zoom = 53.4, -6.8, 6
    else:
        center_lat = sum(m[0] for m in markers) / len(markers)
        center_lng = sum(m[1] for m in markers) / len(markers)
        zoom = 15

    marker_lines = []
    for lat, lng, score in markers:
        color = RATING_COLORS.get(score, "#9e9e9e")
        marker_lines.append(
            "L.circleMarker([%s,%s],{radius:8,color:'#222',weight:1,fillColor:'%s',"
            "fillOpacity:0.9}).bindPopup('PSCI rating: %s').addTo(map);"
            % (lat, lng, color, score)
        )

    return (_SCORE_MAP_HTML_TEMPLATE
            .replace("__LAT__", str(center_lat))
            .replace("__LNG__", str(center_lng))
            .replace("__ZOOM__", str(zoom))
            .replace("__MARKERS__", "\n".join(marker_lines)))


class PSCIViewer(QMainWindow):
    def __init__(self, config: AppConfig, credentials: AssumedCredentials, prefix: str, username: str):
        super().__init__()
        self.config = config
        self.credentials = credentials
        self.bucket_name = config.bucket_name
        self.prefix = prefix
        self.username = username

        self.setWindowTitle("PSCI Viewer - Pavement Surface Condition Survey")

        self.s3 = boto3.client(
            's3',
            region_name=self.config.bucket_region,
            **self.credentials.as_boto_kwargs()
        )
        self.frame_source = S3FrameSource(self.s3, self.bucket_name)

        self.image_keys = []
        self.current_index = -1
        self.ratings = {}          # image key -> dict of defect field -> 0-9
        self.rating_dates = {}
        self.qa_segments = []
        self.full_metadata_list = []
        self.metadata_list = []
        self.current_pixmap = QPixmap()
        self.current_project_name = ""

        # Fixed-length (100m) section boundaries for the currently loaded target,
        # in absolute chainage metres. Continuous playback auto-pauses at each one.
        self.section_boundaries = []
        self.next_boundary_index = 0

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

        self._reset_defect_inputs()
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

        layout.addWidget(self._build_defect_grid())

        self.chk_play_after_enter = QCheckBox("Play after Enter")
        layout.addWidget(self.chk_play_after_enter)

        self.btn_enter = QPushButton("Enter")
        self.btn_enter.setMinimumHeight(28)
        self.btn_enter.clicked.connect(self.commit_current_rating)
        layout.addWidget(self.btn_enter)

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
        self.btn_export.clicked.connect(self.export_ratings)
        layout.addWidget(self.btn_export)

        layout.addWidget(self._build_map_tabs(), 1)

        return panel

    # Label text for each PSCI rating field, per Table 1 of the Rural Flexible Roads Manual.
    _PERCENT_FIELD_LABELS = {
        "RavellingPct": "Ravelling (%):",
        "BleedingPct": "Bleeding (%):",
        "OtherCrackingPct": "Other Cracking (%):",
        "StructuralDistressPct": "Structural Distress (%) (rutting/alligator/poor patching):",
    }

    def _build_defect_grid(self):
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(1)

        row = 0
        self.percent_inputs = {}  # field name -> QSpinBox (0-100 %)
        for field_name in PSCI_PERCENT_FIELDS:
            grid.addWidget(QLabel(self._PERCENT_FIELD_LABELS[field_name]), row, 0)
            spin = self._make_percent_spinbox()
            grid.addWidget(spin, row, 1)
            self.percent_inputs[field_name] = spin
            row += 1

        grid.addWidget(QLabel("Rutting Depth (mm):"), row, 0)
        self.rutting_depth_input = QSpinBox()
        self.rutting_depth_input.setRange(0, 200)
        self.rutting_depth_input.setFixedWidth(56)
        self.rutting_depth_input.setMaximumHeight(22)
        grid.addWidget(self.rutting_depth_input, row, 1)
        row += 1

        self.category_inputs = {}  # field name -> QComboBox
        for field_name, options in PSCI_CATEGORY_FIELDS.items():
            grid.addWidget(QLabel(f"{field_name}:"), row, 0)
            combo = QComboBox()
            combo.addItems(options)
            combo.setMaximumHeight(22)
            grid.addWidget(combo, row, 1)
            self.category_inputs[field_name] = combo
            row += 1

        self.boolean_inputs = {}  # field name -> QCheckBox
        for field_name, label in PSCI_BOOLEAN_FIELDS.items():
            checkbox = QCheckBox(label)
            grid.addWidget(checkbox, row, 0, 1, 2)
            self.boolean_inputs[field_name] = checkbox
            row += 1

        return frame

    def _make_percent_spinbox(self):
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setSuffix("%")
        spin.setFixedWidth(56)
        spin.setMaximumHeight(22)
        return spin

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

            self.psci_map_view = QWebEngineView()
            self.psci_map_view.setFixedSize(MAP_WIDTH, MAP_HEIGHT)
            psci_map_index = self.map_tabs.addTab(self.psci_map_view, "PSCI Map")
            self.map_tabs.setTabToolTip(
                psci_map_index,
                "Rating computed per Table 1 of the Rural Flexible Roads Manual (DTTAS, "
                "Nov 2013) -- the published PSCI standard for non-national roads."
            )
        else:
            placeholder = QLabel(
                "GPS map unavailable.\nInstall PyQt6-WebEngine to enable the live map."
            )
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.map_view = None
            self.map_tabs.addTab(placeholder, "GPS")

            score_placeholder = QLabel(
                "PSCI map unavailable.\nInstall PyQt6-WebEngine to enable it."
            )
            score_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.psci_map_view = None
            self.map_tabs.addTab(score_placeholder, "PSCI Map")

        iri_placeholder = QLabel("No IRI data source configured.")
        iri_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_tabs.addTab(iri_placeholder, "IRI")

        return self.map_tabs

    def _refresh_psci_score_map(self):
        if self.psci_map_view is None:
            return

        markers = []
        for key, values in self.ratings.items():
            try:
                idx = self.image_keys.index(key)
                meta = self.metadata_list[idx]
                lat = float(meta.get("Lat"))
                lng = float(meta.get("Lng"))
            except (ValueError, IndexError, TypeError):
                continue
            markers.append((lat, lng, compute_psci_score(values)))

        self.psci_map_view.setHtml(build_score_map_html(markers))

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Space"), self, self.toggle_playback)
        QShortcut(QKeySequence("Right"), self, self.skip_frame)
        QShortcut(QKeySequence("Left"), self, self.prev_frame)
        QShortcut(QKeySequence("Return"), self, self.commit_current_rating)

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
            # discard in-progress ratings.
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
        self.next_boundary_index = 0

        self.field_section.setText(os.path.basename(segment.get('RSPFile', '')))
        self.field_current_project.setText(self.current_project_name)
        length_km = float(segment.get('ChainageTo', 0)) - float(segment.get('ChainageFrom', 0))
        self.field_length.setText(f"{length_km:.3f} km")

        self.status_label.setText(f"Loaded {len(self.image_keys)} frames | Space = Play/Pause | Enter = Save rating")

        if self.image_keys:
            self.current_index = -1
            self._load_frame(0)
            self._refresh_psci_score_map()
        else:
            QMessageBox.warning(self, "No Images Found", "No images found in the specified chainage range.")

    # ------------------------------------------------------------------
    # Frame navigation
    # ------------------------------------------------------------------
    def _load_frame(self, index):
        if index < 0 or index >= len(self.image_keys):
            return

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

        saved = self.ratings.get(key)
        self._populate_defect_inputs(saved)

        self._update_map(metadata.get("Lat"), metadata.get("Lng"))

        crossed = self._sync_section_pointer(metadata.get("Chainage"))
        if crossed and self.is_playing:
            self.pause_playback()
            self.status_label.setText(
                f"Section boundary reached at {metadata.get('Chainage')}m — rate this section, then Play to continue."
            )
        else:
            self.status_label.setText(f"Frame {index + 1} of {len(self.image_keys)} | {filename}")

    def _sync_section_pointer(self, chainage):
        """Advances past any 100m section boundaries this frame's chainage has now
        reached. Returns True if at least one boundary was newly crossed."""
        if chainage is None:
            return False

        crossed = False
        while (self.next_boundary_index < len(self.section_boundaries)
               and chainage >= self.section_boundaries[self.next_boundary_index]):
            self.next_boundary_index += 1
            crossed = True
        return crossed

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
        """Advance without recording a rating for the current frame."""
        self.next_frame()

    def rerun_segment(self):
        """Replay the current segment from its first frame. Already-saved ratings are kept."""
        self.pause_playback()
        self.next_boundary_index = 0
        if self.image_keys:
            self._load_frame(0)

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
    # Defect rating entry
    # ------------------------------------------------------------------
    def _reset_defect_inputs(self):
        self._populate_defect_inputs(None)

    def _populate_defect_inputs(self, saved_values):
        saved_values = saved_values or {}
        for field_name, spin in self.percent_inputs.items():
            spin.setValue(int(saved_values.get(field_name, 0) or 0))
        self.rutting_depth_input.setValue(int(saved_values.get(PSCI_RUTTING_DEPTH_FIELD, 0) or 0))
        for field_name, combo in self.category_inputs.items():
            combo.setCurrentText(saved_values.get(field_name, "None") or "None")
        for field_name, checkbox in self.boolean_inputs.items():
            checkbox.setChecked(bool(saved_values.get(field_name, False)))

    def _current_defect_values(self):
        values = {}
        for field_name, spin in self.percent_inputs.items():
            values[field_name] = spin.value()
        values[PSCI_RUTTING_DEPTH_FIELD] = self.rutting_depth_input.value()
        for field_name, combo in self.category_inputs.items():
            values[field_name] = combo.currentText()
        for field_name, checkbox in self.boolean_inputs.items():
            values[field_name] = checkbox.isChecked()
        return values

    def commit_current_rating(self):
        if self.current_index < 0 or self.current_index >= len(self.image_keys):
            return

        key = self.image_keys[self.current_index]
        self.ratings[key] = self._current_defect_values()
        self.rating_dates[key] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self._refresh_psci_score_map()

        if self.chk_play_after_enter.isChecked():
            self.next_frame()

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
    def export_ratings(self):
        if not self.ratings:
            QMessageBox.information(self, "Export Ratings", "No frames have been rated yet.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Save PSCI Ratings CSV", "psci_ratings.csv", "CSV Files (*.csv)")
        if not path:
            return

        rating_fields = psci_field_names()

        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["Filename", "Frame", "Chainage", "Lat", "Lng", "Alt", "Date",
                     "DistanceFromLastReading(m)"] + rating_fields
                    + ["PSCI_Rating", "PSCI_Rating_Basis"]
                )

                last_written_values = None
                last_written_chainage = None
                sorted_keys = sorted(self.ratings.keys())

                for key in sorted_keys:
                    values = self.ratings[key]
                    if values == last_written_values:
                        continue

                    try:
                        idx = self.image_keys.index(key)
                        meta = self.metadata_list[idx] if idx < len(self.metadata_list) else {}
                        frame_num = idx + 1
                    except ValueError:
                        meta = {}
                        frame_num = "N/A"

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
                    last_written_values = values

                    result = compute_psci_rating(PSCIRatingInputs.from_dict(values))

                    writer.writerow(
                        [filename, frame_num, chainage, lat, lng, alt, date_val, dist]
                        + [values.get(field, 0) for field in rating_fields]
                        + [result.rating, "; ".join(result.reasons)]
                    )

                current_date = datetime.datetime.now().strftime("%d/%m/%Y")
                writer.writerow([f"{self.username} - {current_date}"])
                writer.writerow(["END"])

            QMessageBox.information(self, "Export Successful", f"Saved PSCI ratings to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to save CSV file:\n{str(e)}")
