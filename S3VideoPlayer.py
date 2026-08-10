import sys
import csv
import boto3
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QListWidget, QLabel, QPushButton, QMessageBox, QDialog, 
    QStackedWidget, QLineEdit, QFileDialog
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt, QTimer

# Import authentication and configuration modules from local files
from config import load_config, AppConfig
from auth import (
    AssumedCredentials, AuthError, MfaRequired,
    initiate_login, respond_to_mfa_challenge, sign_in_and_get_bucket_credentials
)


class CognitoLoginDialog(QDialog):
    """PyQt6 Login Dialog that handles Cognito User Pool authentication,
    MFA challenge verification, and two-hop credential exchange.
    """
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.credentials: AssumedCredentials | None = None
        self._pending_session: str | None = None
        self._pending_username: str | None = None

        self.setWindowTitle("S3 Viewer - Cognito Sign In")
        self.setFixedSize(360, 220)

        self.stack = QStackedWidget(self)
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.stack)

        self._build_login_view()
        self._build_mfa_view()

    def _build_login_view(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel("Username:"))
        self.username_input = QLineEdit()
        layout.addWidget(self.username_input)

        layout.addWidget(QLabel("Password:"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.returnPressed.connect(self._handle_login)
        layout.addWidget(self.password_input)

        self.login_status = QLabel("")
        self.login_status.setStyleSheet("color: red;")
        layout.addWidget(self.login_status)

        self.btn_login = QPushButton("Sign In")
        self.btn_login.clicked.connect(self._handle_login)
        layout.addWidget(self.btn_login)

        self.stack.addWidget(widget)

    def _build_mfa_view(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel("Enter 6-digit Authenticator (TOTP) Code:"))
        self.totp_input = QLineEdit()
        self.totp_input.returnPressed.connect(self._handle_mfa)
        layout.addWidget(self.totp_input)

        self.mfa_status = QLabel("")
        self.mfa_status.setStyleSheet("color: red;")
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
    def __init__(self, config: AppConfig, credentials: AssumedCredentials, prefix: str):
        super().__init__()
        self.config = config
        self.credentials = credentials
        self.bucket_name = config.bucket_name
        self.prefix = prefix
        
        self.setWindowTitle("S3 Survey Image Viewer & Rapid Rating Tool")

        # Initialize S3 Client
        self.s3 = boto3.client(
            's3',
            region_name=self.config.bucket_region,
            **self.credentials.as_boto_kwargs()
        )
        
        self.image_keys = []
        self.current_index = -1
        self.ratings = {}  # Format: { 'image_key': rating_int }
        self.current_sticky_rating = None # Tracks the rating to carry forward

        # --- Playback Timer (4 frames per second = 250ms interval) ---
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(250)
        self.play_timer.timeout.connect(self._auto_advance_frame)
        self.is_playing = False
        
        # --- UI Setup ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Left side: Compact Image List Sidebar
        self.image_list_widget = QListWidget()
        self.image_list_widget.setMaximumWidth(180)  # Keep sidebar narrow
        self.image_list_widget.currentRowChanged.connect(self.load_image_by_index)
        main_layout.addWidget(self.image_list_widget, 0)
        
        # Right side: Maximized Image display and enlarged controls
        right_layout = QVBoxLayout()
        
        # Status Bar Header
        self.status_label = QLabel("Loading images from S3...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-size: 11pt; font-weight: bold; padding: 4px;")
        right_layout.addWidget(self.status_label)
        
        # Enlarged Image Viewport
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(950, 650)
        self.image_label.setStyleSheet("background-color: black; color: white;")
        right_layout.addWidget(self.image_label, 1)  # Stretch factor 1 fills viewport
        
        # --- Large Rapid Rating Buttons (1 to 10) ---
        rating_container = QWidget()
        rating_group_layout = QHBoxLayout(rating_container)
        rating_group_layout.setContentsMargins(0, 5, 0, 5)
        
        rating_title = QLabel("<b>Rating:</b>")
        rating_title.setStyleSheet("font-size: 13pt;")
        rating_group_layout.addWidget(rating_title)
        
        self.rating_buttons = []
        for score in range(1, 11):
            btn = QPushButton(str(score))
            btn.setMinimumHeight(55)  # Large hit target
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Prevents key focus interference
            btn.clicked.connect(lambda checked, s=score: self.rate_current_image(s))
            rating_group_layout.addWidget(btn, 1)  # Equal expanding width
            self.rating_buttons.append(btn)
            
        self.btn_export = QPushButton("Export CSV")
        self.btn_export.setMinimumHeight(55)
        self.btn_export.setStyleSheet("font-size: 11pt; font-weight: bold;")
        self.btn_export.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_export.clicked.connect(self.export_ratings)
        rating_group_layout.addWidget(self.btn_export, 1)
        
        right_layout.addWidget(rating_container)
        
        # Navigation & Playback Controls
        controls_layout = QHBoxLayout()
        
        self.btn_prev = QPushButton("<< Previous")
        self.btn_prev.setMinimumHeight(40)
        self.btn_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_prev.clicked.connect(self.prev_frame)
        
        self.btn_play = QPushButton("▶ Play (4 FPS)")
        self.btn_play.setMinimumHeight(40)
        self.btn_play.setStyleSheet("font-weight: bold; font-size: 12pt; background-color: #2e7d32; color: white;")
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.clicked.connect(self.toggle_playback)
        
        self.btn_next = QPushButton("Next >>")
        self.btn_next.setMinimumHeight(40)
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_next.clicked.connect(self.next_frame)
        
        controls_layout.addWidget(self.btn_prev)
        controls_layout.addWidget(self.btn_play)
        controls_layout.addWidget(self.btn_next)
        right_layout.addLayout(controls_layout)
        
        main_layout.addLayout(right_layout, 1)
        
        # Reset button styles to default state
        self._update_rating_buttons_ui(None)
        
        # Fetch images on startup
        self.populate_image_list()

    def keyPressEvent(self, event):
        """Keyboard Shortcuts for rapid rating:
        - Keys 1-9: Rate 1 through 9
        - Key 0: Rate 10
        - Spacebar: Play / Pause toggle
        - Left / Right arrows: Navigate frames
        """
        key = event.key()
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            score = key - Qt.Key.Key_0
            self.rate_current_image(score)
        elif key == Qt.Key.Key_0:
            self.rate_current_image(10)
        elif key == Qt.Key.Key_Space:
            self.toggle_playback()
        elif key == Qt.Key.Key_Left:
            self.prev_frame()
        elif key == Qt.Key.Key_Right:
            self.next_frame()
        else:
            super().keyPressEvent(event)

    def populate_image_list(self):
        """Fetches all JPGs under the prefix."""
        try:
            paginator = self.s3.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=self.prefix)
            
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        if key.lower().endswith(('.jpg', '.jpeg')):
                            self.image_keys.append(key)
            
            self.image_keys.sort()
            
            if not self.image_keys:
                self.status_label.setText("No images found in this folder.")
                return
                
            for key in self.image_keys:
                filename = key.split('/')[-1]
                self.image_list_widget.addItem(filename)
                
            self.status_label.setText(f"Loaded {len(self.image_keys)} images | Press Space to Play/Stop | Keys 1-0 to Rate")
            self.image_list_widget.setCurrentRow(0)
            
        except Exception as e:
            QMessageBox.critical(self, "S3 Error", f"Could not load bucket data: {str(e)}")

    def load_image_by_index(self, index):
        """Downloads and displays image, auto-applying active sticky rating if unrated."""
        if index < 0 or index >= len(self.image_keys):
            return
            S
        self.current_index = index
        key = self.image_keys[self.current_index]
        
        # If unrated yet a sticky rating exists, auto-apply it to this new frame
        if key not in self.ratings and self.current_sticky_rating is not None:
            self.ratings[key] = self.current_sticky_rating
            
        try:
            response = self.s3.get_object(Bucket=self.bucket_name, Key=key)
            image_data = response['Body'].read()
            
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            
            scaled_pixmap = pixmap.scaled(
                self.image_label.size(), 
                Qt.AspectRatioMode.KeepAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
            self.image_label.setPixmap(scaled_pixmap)
            
            filename = key.split('/')[-1]
            current_rating = self.ratings.get(key, "Unrated")
            self.status_label.setText(
                f"Frame {self.current_index + 1} of {len(self.image_keys)} | {filename} | Current Rating: [{current_rating}]"
            )
            
            self._update_rating_buttons_ui(current_rating)
            
        except Exception as e:
            self.image_label.setText(f"Error loading image:\n{str(e)}")

    def toggle_playback(self):
        """Starts or stops 4 FPS playback."""
        if self.is_playing:
            self.play_timer.stop()
            self.is_playing = False
            self.btn_play.setText("▶ Play (4 FPS)")
            self.btn_play.setStyleSheet("font-weight: bold; font-size: 12pt; background-color: #2e7d32; color: white;")
        else:
            self.is_playing = True
            self.btn_play.setText("⏸ Stop")
            self.btn_play.setStyleSheet("font-weight: bold; font-size: 12pt; background-color: #c62828; color: white;")
            self.play_timer.start()

    def _auto_advance_frame(self):
        """Timer callback to move to next frame, pausing at sequence end."""
        if self.current_index < len(self.image_keys) - 1:
            self.image_list_widget.setCurrentRow(self.current_index + 1)
        else:
            self.toggle_playback()

    def rate_current_image(self, score: int):
        """Assigns a score (1-10) to the current frame and sets it as sticky going forward."""
        if self.current_index < 0 or self.current_index >= len(self.image_keys):
            return
            
        key = self.image_keys[self.current_index]
        self.current_sticky_rating = score  # Carry this rating forward to future images
        self.ratings[key] = score
        self._update_rating_buttons_ui(score)
        
        filename = key.split('/')[-1]
        self.status_label.setText(
            f"Frame {self.current_index + 1} of {len(self.image_keys)} | {filename} | Current Rating: [{score}]"
        )

    def _update_rating_buttons_ui(self, active_score):
        """Highlights the active rating button with a bright color and resets others."""
        for idx, btn in enumerate(self.rating_buttons, start=1):
            if idx == active_score:
                btn.setStyleSheet("""
                    QPushButton {
                        font-size: 15pt;
                        font-weight: bold;
                        background-color: #1565c0;
                        color: white;
                        border: 3px solid #0d47a1;
                        border-radius: 6px;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        font-size: 13pt;
                        font-weight: bold;
                        background-color: #f0f0f0;
                        color: #212121;
                        border: 2px solid #bdbdbd;
                        border-radius: 6px;
                    }
                    QPushButton:hover {
                        background-color: #e0e0e0;
                    }
                """)

    def export_ratings(self):
        """Exports ratings dictionary to a local CSV file."""
        if not self.ratings:
            QMessageBox.information(self, "Export Ratings", "No images have been rated yet.")
            return
            
        path, _ = QFileDialog.getSaveFileName(self, "Save Ratings CSV", "survey_ratings.csv", "CSV Files (*.csv)")
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Image_Key", "Filename", "Rating"])
                    for key, score in sorted(self.ratings.items()):
                        filename = key.split('/')[-1]
                        writer.writerow([key, filename, score])
                QMessageBox.information(self, "Export Successful", f"Saved ratings for {len(self.ratings)} images to:\n{path}")
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
        
    PREFIX = '2026/RSP TII Network Survey Imagery 2026/WE20260606/N04D226C/N04D226C_ROW/'
    
    viewer = S3ImageSequenceViewer(config, login_dialog.credentials, PREFIX)
    viewer.resize(1450, 900)
    viewer.show()
    
    sys.exit(app.exec())