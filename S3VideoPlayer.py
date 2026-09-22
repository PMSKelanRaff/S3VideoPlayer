import csv
import os
import datetime
import boto3
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QLabel, QPushButton, QMessageBox,
    QFileDialog, QSizePolicy, QSlider,
    QComboBox, QFrame
)
from PyQt6.QtGui import QPixmap, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer

# Import authentication and configuration modules from local files
from config import AppConfig
from auth import AssumedCredentials
from survey_core import parse_rsp_file, S3FrameSource, RATING_COLORS


class S3ImageSequenceViewer(QMainWindow):
    def __init__(self, config: AppConfig, credentials: AssumedCredentials, prefix: str, username: str):
        super().__init__()
        self.config = config
        self.credentials = credentials
        self.bucket_name = config.bucket_name
        self.prefix = prefix
        self.username = username
        
        self.setWindowTitle("S3 Survey Image Viewer & QA Rating Tool")

        # Initialize S3 Client
        self.s3 = boto3.client(
            's3',
            region_name=self.config.bucket_region,
            **self.credentials.as_boto_kwargs()
        )
        self.frame_source = S3FrameSource(self.s3, self.bucket_name)

        self.image_keys = []
        self.current_index = -1
        self.ratings = {}  
        self.rating_dates = {}  # Tracks the exact datetime a rating was assigned
        self.current_sticky_rating = None  
        
        self.qa_segments = []  
        self.full_metadata_list = [] 
        self.metadata_list = []      
        self.current_pixmap = QPixmap() 

        # --- Rating Color Palette (Red to Green Gradient), shared with the PCI Viewer ---
        self.rating_colors = RATING_COLORS

        # --- Playback Timer ---
        self.current_fps = 10
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(int(1000 / self.current_fps))
        self.play_timer.timeout.connect(self._auto_advance_frame)
        self.is_playing = False
        
        # --- UI Setup ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # --- LEFT PANEL: QA Segment Workflow ---
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        
        qa_label = QLabel("<b>QA Segment Filtering</b>")
        left_layout.addWidget(qa_label)
        
        self.btn_load_qa = QPushButton("Load FileExtentsAWS.csv")
        self.btn_load_qa.setStyleSheet("background-color: #1976d2; color: white; font-weight: bold; padding: 6px;")
        self.btn_load_qa.clicked.connect(self.load_qa_csv)
        left_layout.addWidget(self.btn_load_qa)
        
        self.qa_combo = QComboBox()
        left_layout.addWidget(self.qa_combo)
        
        self.btn_load_segment = QPushButton("Load Selected Target")
        self.btn_load_segment.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold; padding: 6px;")
        self.btn_load_segment.clicked.connect(self.load_selected_qa_segment)
        left_layout.addWidget(self.btn_load_segment)
        
        # Visual Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        left_layout.addWidget(line)
        
        # Image Browser List
        self.image_list_widget = QListWidget()
        self.image_list_widget.setMaximumWidth(260)  
        self.image_list_widget.currentRowChanged.connect(self.load_image_by_index)
        left_layout.addWidget(self.image_list_widget)
        
        main_layout.addLayout(left_layout, 0)
        
        # --- RIGHT PANEL: Image display and controls ---
        right_layout = QVBoxLayout()
        
        self.current_rating_label = QLabel("UNRATED")
        self.current_rating_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_rating_label.setMinimumHeight(45)
        self.current_rating_label.setStyleSheet("font-size: 20pt; font-weight: bold; background-color: #424242; color: white; border-radius: 6px;")
        right_layout.addWidget(self.current_rating_label)

        self.status_label = QLabel(f"Logged in as: {self.username}")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-size: 10pt; font-weight: bold; padding: 2px;")
        right_layout.addWidget(self.status_label)
        
        self.metadata_label = QLabel("Chainage: N/A | GPS: N/A")
        self.metadata_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.metadata_label.setStyleSheet("font-size: 10pt; color: #d32f2f; font-weight: bold;") 
        right_layout.addWidget(self.metadata_label)
        
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image_label.setMinimumSize(400, 300)
        self.image_label.setStyleSheet("background-color: black; color: white;")
        right_layout.addWidget(self.image_label, 1) 
        
        # Rapid Rating Buttons (1 to 10)
        rating_container = QWidget()
        rating_group_layout = QHBoxLayout(rating_container)
        rating_group_layout.setContentsMargins(0, 2, 0, 2)
        
        rating_title = QLabel("<b>Set Rating:</b>")
        rating_title.setStyleSheet("font-size: 11pt;")
        rating_group_layout.addWidget(rating_title)
        
        self.rating_buttons = []
        for score in range(1, 11):
            btn = QPushButton(str(score))
            btn.setMinimumHeight(50) 
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus) 
            btn.clicked.connect(lambda checked, s=score: self.rate_current_image(s))
            rating_group_layout.addWidget(btn, 1) 
            self.rating_buttons.append(btn)
            
        self.btn_export = QPushButton("Export CSV")
        self.btn_export.setMinimumHeight(50)
        self.btn_export.setStyleSheet("font-size: 10pt; font-weight: bold; background-color: #e0e0e0; color: black; border-radius: 6px;")
        self.btn_export.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export.clicked.connect(self.export_ratings)
        rating_group_layout.addWidget(self.btn_export, 1)
        
        right_layout.addWidget(rating_container)
        
        # FPS Slider Section
        fps_container = QWidget()
        fps_layout = QVBoxLayout(fps_container)
        fps_layout.setContentsMargins(0, 2, 0, 5) 
        
        self.fps_label = QLabel(f"<b>Playback Speed:</b> {self.current_fps} FPS")
        self.fps_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fps_label.setStyleSheet("font-size: 10pt;")
        
        self.fps_slider = QSlider(Qt.Orientation.Horizontal)
        self.fps_slider.setMinimum(1)
        self.fps_slider.setMaximum(30)
        self.fps_slider.setValue(self.current_fps)
        self.fps_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.fps_slider.setTickInterval(5)
        self.fps_slider.valueChanged.connect(self._update_fps)
        self.fps_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        fps_layout.addWidget(self.fps_label)
        fps_layout.addWidget(self.fps_slider)
        right_layout.addWidget(fps_container)
        
        # Navigation & Playback Controls
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 5)
        
        self.btn_prev = QPushButton("<< Previous")
        self.btn_prev.setMinimumHeight(35)
        self.btn_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_prev.clicked.connect(self.prev_frame)
        
        self.btn_play = QPushButton(f"▶ Play ({self.current_fps} FPS)")
        self.btn_play.setMinimumHeight(35)
        self.btn_play.setStyleSheet("font-weight: bold; font-size: 11pt; background-color: #2e7d32; color: white;")
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.clicked.connect(self.toggle_playback)
        
        self.btn_next = QPushButton("Next >>")
        self.btn_next.setMinimumHeight(35)
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_next.clicked.connect(self.next_frame)
        
        controls_layout.addWidget(self.btn_prev)
        controls_layout.addWidget(self.btn_play)
        controls_layout.addWidget(self.btn_next)
        
        right_layout.addLayout(controls_layout)
        
        main_layout.addLayout(right_layout, 1)
        
        self._update_rating_buttons_ui(None)
        self._setup_shortcuts()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_image_display()

    def resizeEvent(self, event):
        self._update_image_display()
        super().resizeEvent(event)

    def _update_image_display(self):
        if hasattr(self, 'current_pixmap') and not self.current_pixmap.isNull():
            scaled_pixmap = self.current_pixmap.scaled(
                self.image_label.size(), 
                Qt.AspectRatioMode.KeepAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
            self.image_label.setPixmap(scaled_pixmap)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Space"), self, self.toggle_playback)
        QShortcut(QKeySequence("Left"), self, self.prev_frame)
        QShortcut(QKeySequence("Right"), self, self.next_frame)
        for i in range(1, 10):
            QShortcut(QKeySequence(str(i)), self, lambda checked=False, score=i: self.rate_current_image(score))
        QShortcut(QKeySequence("0"), self, lambda checked=False: self.rate_current_image(10))

    def load_qa_csv(self):
        """Loads FileExtentsAWS.csv and populates the dropdown."""
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
        """Changes S3 bucket focus, loads RSP, and filters frames between chainage limits."""
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

        self.image_list_widget.clear()
        for key in self.image_keys:
            self.image_list_widget.addItem(key.split('/')[-1])
            
        self.metadata_label.setStyleSheet("font-size: 10pt; color: #1565c0; font-weight: bold;")
        self.status_label.setText(f"Filtered {len(self.image_keys)} target frames | Press Space to Play/Stop | Keys 1-0 to Rate")
        
        if self.image_keys:
            self.image_list_widget.setCurrentRow(0)
        else:
            QMessageBox.warning(self, "No Images Found", "No images found in the specified chainage range.")

    def load_image_by_index(self, index):
        if index < 0 or index >= len(self.image_keys):
            return
            
        self.current_index = index
        key = self.image_keys[self.current_index]
        filename = key.split('/')[-1]
        
        if key not in self.ratings and self.current_sticky_rating is not None:
            self.ratings[key] = self.current_sticky_rating
            self.rating_dates[key] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            
        metadata = {}
        if self.current_index < len(self.metadata_list):
            metadata = self.metadata_list[self.current_index]

        if metadata:
            chainage = metadata.get("Chainage", "N/A")
            lat = metadata.get("Lat", "N/A")
            lng = metadata.get("Lng", "N/A")
            self.metadata_label.setText(f"Chainage (m): {chainage} | GPS: {lat}, {lng}")
        else:
            self.metadata_label.setText("Chainage: N/A | GPS: N/A")

        try:
            image_data = self.frame_source.fetch_image_bytes(key)

            self.current_pixmap.loadFromData(image_data)
            self._update_image_display()
            
            current_rating = self.ratings.get(key, "Unrated")
            self.status_label.setText(f"Frame {self.current_index + 1} of {len(self.image_keys)} | {filename}")
            
            self._update_rating_buttons_ui(current_rating)
            
        except Exception as e:
            self.image_label.setText(f"Error loading image:\n{str(e)}")

    def _update_fps(self, value):
        self.current_fps = value
        self.fps_label.setText(f"<b>Playback Speed:</b> {self.current_fps} FPS")
        self.play_timer.setInterval(int(1000 / self.current_fps))
        if not self.is_playing:
            self.btn_play.setText(f"▶ Play ({self.current_fps} FPS)")

    def toggle_playback(self):
        if self.is_playing:
            self.play_timer.stop()
            self.is_playing = False
            self.btn_play.setText(f"▶ Play ({self.current_fps} FPS)")
            self.btn_play.setStyleSheet("font-weight: bold; font-size: 11pt; background-color: #2e7d32; color: white;")
        else:
            self.is_playing = True
            self.btn_play.setText("⏸ Stop")
            self.btn_play.setStyleSheet("font-weight: bold; font-size: 11pt; background-color: #c62828; color: white;")
            self.play_timer.start()

    def _auto_advance_frame(self):
        if self.current_index < len(self.image_keys) - 1:
            self.image_list_widget.setCurrentRow(self.current_index + 1)
        else:
            self.toggle_playback()

    def rate_current_image(self, score: int):
        if self.current_index < 0 or self.current_index >= len(self.image_keys):
            return
            
        key = self.image_keys[self.current_index]
        self.current_sticky_rating = score 
        self.ratings[key] = score
        self.rating_dates[key] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self._update_rating_buttons_ui(score)

    def _update_rating_buttons_ui(self, active_score):
        if isinstance(active_score, int) and active_score in self.rating_colors:
            color = self.rating_colors[active_score]
            text_color = "white" if active_score <= 3 else "black"
            self.current_rating_label.setText(f"CURRENT RATING: {active_score}")
            self.current_rating_label.setStyleSheet(f"font-size: 24pt; font-weight: bold; background-color: {color}; color: {text_color}; border-radius: 6px;")
        else:
            self.current_rating_label.setText("UNRATED")
            self.current_rating_label.setStyleSheet("font-size: 20pt; font-weight: bold; background-color: #424242; color: white; border-radius: 6px;")

        for idx, btn in enumerate(self.rating_buttons, start=1):
            base_color = self.rating_colors[idx]
            btn_text_color = "white" if idx <= 3 else "black"
            
            if idx == active_score:
                btn.setMinimumHeight(25)
                btn.setMaximumHeight(25)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        font-size: 11pt; font-weight: bold; background-color: {base_color}; color: {btn_text_color};
                        border: 3px solid #000000; border-radius: 6px;
                    }}
                """)
            else:
                btn.setMinimumHeight(50)
                btn.setMaximumHeight(50)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        font-size: 14pt; font-weight: bold; background-color: {base_color}; color: {btn_text_color};
                        border: 1px solid #757575; border-radius: 6px;
                    }}
                    QPushButton:hover {{ border: 2px solid #ffffff; }}
                """)

    def export_ratings(self):
        """Exports ratings, only recording rows where the rating changes to save space."""
        if not self.ratings:
            QMessageBox.information(self, "Export Ratings", "No images have been rated yet.")
            return
            
        path, _ = QFileDialog.getSaveFileName(self, "Save Ratings CSV", "survey_ratings.csv", "CSV Files (*.csv)")
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    
                    # 1. Original headers without User/Date columns
                    writer.writerow([
                        "Filename", "Frame", "Chainage", "Lat", "Lng", "Alt", 
                        "IG_E", "IG_N", "IG_Height", "ITM_E", "ITM_N", "ITM_Height", 
                        "Date", "DistanceFromLastReading(m)", "Rating"
                    ])
                    
                    last_written_rating = None
                    last_written_chainage = None
                    
                    sorted_keys = sorted(self.ratings.keys())
                    
                    for key in sorted_keys:
                        score = self.ratings[key]
                        
                        if score == last_written_rating:
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
                        last_written_rating = score
                            
                        # 2. Write the standard data row
                        writer.writerow([
                            filename, frame_num, chainage, lat, lng, alt,
                            "", "", "", "", "", "",  
                            date_val, dist, score
                        ])
                        
                    # 3. Write the Username and Date string on the second to last line
                    current_date = datetime.datetime.now().strftime("%d/%m/%Y")
                    writer.writerow([f"{self.username} - {current_date}"])
                    
                    # 4. Write final END marker line
                    writer.writerow(["END"])
                        
                QMessageBox.information(self, "Export Successful", f"Saved condensed ratings to:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to save CSV file:\n{str(e)}")

    def next_frame(self):
        if self.current_index < len(self.image_keys) - 1:
            self.image_list_widget.setCurrentRow(self.current_index + 1)
            
    def prev_frame(self):
        if self.current_index > 0:
            self.image_list_widget.setCurrentRow(self.current_index - 1)


if __name__ == '__main__':
    # Delegate to the shared entry point so running this file directly still
    # shows the module selector (Image Viewer / PCI Viewer) instead of
    # jumping straight into this viewer. Imported here, not at module level,
    # to avoid a circular import with main.py.
    from main import main
    main()