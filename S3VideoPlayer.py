import sys
import csv
import os
import re
import datetime
import boto3
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QListWidget, QLabel, QPushButton, QMessageBox, QDialog, 
    QStackedWidget, QLineEdit, QFileDialog, QSizePolicy, QSlider,
    QComboBox, QFrame
)
from PyQt6.QtGui import QPixmap, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QTimer

# Import authentication and configuration modules from local files
from config import load_config, AppConfig
from auth import (
    AssumedCredentials, AuthError, MfaRequired,
    initiate_login, respond_to_mfa_challenge, sign_in_and_get_bucket_credentials
)


class CognitoLoginDialog(QDialog):
    """PyQt6 Login Dialog that handles Cognito User Pool authentication."""
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.credentials: AssumedCredentials | None = None
        self._pending_session: str | None = None
        self._pending_username: str | None = None

        self.setWindowTitle("S3 Viewer - Sign In")
        self.setFixedSize(280, 140) 

        self.stack = QStackedWidget(self)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)
        main_layout.addWidget(self.stack)

        self._build_login_view()
        self._build_mfa_view()

    def _build_login_view(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        layout.addWidget(QLabel("Username:"))
        self.username_input = QLineEdit()
        layout.addWidget(self.username_input)

        layout.addWidget(QLabel("Password:"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.returnPressed.connect(self._handle_login)
        layout.addWidget(self.password_input)

        self.login_status = QLabel("")
        self.login_status.setStyleSheet("color: red; font-size: 8pt;")
        layout.addWidget(self.login_status)

        self.btn_login = QPushButton("Sign In")
        self.btn_login.clicked.connect(self._handle_login)
        layout.addWidget(self.btn_login)

        self.stack.addWidget(widget)

    def _build_mfa_view(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        layout.addWidget(QLabel("Enter 6-digit Authenticator Code:"))
        self.totp_input = QLineEdit()
        self.totp_input.returnPressed.connect(self._handle_mfa)
        layout.addWidget(self.totp_input)

        self.mfa_status = QLabel("")
        self.mfa_status.setStyleSheet("color: red; font-size: 8pt;")
        layout.addWidget(self.mfa_status)

        self.btn_mfa = QPushButton("Verify Code")
        self.btn_mfa.clicked.connect(self._handle_mfa)
        layout.addWidget(self.btn_mfa)

        self.stack.addWidget(widget)

    def _handle_login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()

        if not username or not password:
            self.login_status.setText("Username and password are required.")
            return

        self.login_status.setText("Authenticating...")
        self.btn_login.setEnabled(False)

        try:
            id_token = initiate_login(self.config, username, password)
            self._complete_authentication(id_token)
        except MfaRequired as mfa:
            self._pending_session = mfa.session
            self._pending_username = mfa.username
            self.stack.setCurrentIndex(1)
            self.totp_input.setFocus()
        except AuthError as err:
            self.login_status.setText(str(err))
            self.btn_login.setEnabled(True)

    def _handle_mfa(self):
        totp_code = self.totp_input.text().strip()
        if not totp_code:
            self.mfa_status.setText("Please enter your TOTP code.")
            return

        self.mfa_status.setText("Verifying MFA...")
        self.btn_mfa.setEnabled(False)

        try:
            id_token = respond_to_mfa_challenge(
                self.config, self._pending_username, self._pending_session, totp_code
            )
            self._complete_authentication(id_token)
        except AuthError as err:
            self.mfa_status.setText(str(err))
            self.btn_mfa.setEnabled(True)

    def _complete_authentication(self, id_token: str):
        try:
            self.credentials = sign_in_and_get_bucket_credentials(self.config, id_token)
            self.accept()
        except AuthError as err:
            QMessageBox.critical(self, "Authentication Error", str(err))
            self.reject()


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
        
        self.image_keys = []
        self.current_index = -1
        self.ratings = {}  
        self.rating_dates = {}  # Tracks the exact datetime a rating was assigned
        self.current_sticky_rating = None  
        
        self.qa_segments = []  
        self.full_metadata_list = [] 
        self.metadata_list = []      
        self.current_pixmap = QPixmap() 

        # --- Rating Color Palette (Red to Green Gradient) ---
        self.rating_colors = {
            1: "#D32F2F",   # Red
            2: "#F4511E",   # Deep Orange
            3: "#FB8C00",   # Orange
            4: "#FFB300",   # Amber
            5: "#FDD835",   # Dark Yellow
            6: "#FFEE58",   # Yellow
            7: "#D4E157",   # Lime
            8: "#9CCC65",   # Yellow-Green
            9: "#66BB6A",   # Light Green
            10: "#00E676"   # Bright Green
        }

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
                
        self._parse_rsp_file(rsp_path)
        
        self.status_label.setText("Fetching new images from S3...")
        QApplication.processEvents()
        
        full_image_keys = []
        try:
            paginator = self.s3.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=self.prefix)
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        if key.lower().endswith(('.jpg', '.jpeg')):
                            full_image_keys.append(key)
            full_image_keys.sort()
        except Exception as e:
            QMessageBox.critical(self, "S3 Error", f"Could not load bucket data: {str(e)}")
            return
            
        self.image_keys = []
        self.metadata_list = []
        
        for idx_img, key in enumerate(full_image_keys):
            meta = self.full_metadata_list[idx_img] if idx_img < len(self.full_metadata_list) else None
            if meta is not None:
                c = meta.get("Chainage", 0.0)
                if qa_chainage_from <= c <= qa_chainage_to:
                    self.image_keys.append(key)
                    self.metadata_list.append(meta)
                    
        self.image_list_widget.clear()
        for key in self.image_keys:
            self.image_list_widget.addItem(key.split('/')[-1])
            
        self.metadata_label.setStyleSheet("font-size: 10pt; color: #1565c0; font-weight: bold;")
        self.status_label.setText(f"Filtered {len(self.image_keys)} target frames | Press Space to Play/Stop | Keys 1-0 to Rate")
        
        if self.image_keys:
            self.image_list_widget.setCurrentRow(0)
        else:
            QMessageBox.warning(self, "No Images Found", "No images found in the specified chainage range.")

    def _parse_rsp_file(self, path):
        """Universal parser for RSP files, storing data sequentially into full_metadata_list."""
        self.full_metadata_list = []
        filename_val = os.path.splitext(os.path.basename(path))[0].upper()
        date_val = "Unknown"
        
        with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            content = f.read()
            lines = re.split(r'\r\n|\r|\n', content)
            
            for line in lines:
                parts = [p.strip().replace('"', '') for p in line.split(',')]
                
                if line.startswith("5011,") and len(parts) >= 6:
                    date_val = f"{parts[3]}/{parts[4]}/{parts[5]}"
                elif line.startswith("5003,") and len(parts) >= 4:
                    filename_val = parts[3]
                elif line.startswith("5280,") and len(parts) > 7:
                    try:
                        chainage_km = float(parts[1])
                        chainage_m = round(chainage_km * 1000, 3)
                    except ValueError:
                        chainage_m = 0.0
                        
                    self.full_metadata_list.append({
                        "Filename": filename_val,
                        "Date": date_val,
                        "Chainage": chainage_m,
                        "Lat": parts[5],
                        "Lng": parts[6],
                        "Alt": parts[7]
                    })

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
            response = self.s3.get_object(Bucket=self.bucket_name, Key=key)
            image_data = response['Body'].read()
            
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
    app = QApplication(sys.argv)
    
    try:
        config = load_config("config.json")
    except Exception as err:
        QMessageBox.critical(None, "Configuration Error", f"Failed to load config.json:\n{err}")
        sys.exit(1)
        
    login_dialog = CognitoLoginDialog(config)
    if login_dialog.exec() != QDialog.DialogCode.Accepted:
        sys.exit(0)
        
    # Extract username after successful login
    username = login_dialog.username_input.text().strip()
        
    PREFIX = '2026/RSP TII Network Survey Imagery 2026/WE20260606/N04D226C/N04D226C_ROW/'
    
    viewer = S3ImageSequenceViewer(config, login_dialog.credentials, PREFIX, username)
    viewer.resize(1450, 900)
    viewer.show()
    
    sys.exit(app.exec())