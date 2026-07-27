import sys
import boto3
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QListWidget, QLabel, QPushButton, QMessageBox, QDialog, 
    QStackedWidget, QLineEdit
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt

# Import authentication and configuration modules from your local files
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

        # QStackedWidget allows switching between standard Login and TOTP/MFA views
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
            # Step 1: Initiate auth with Cognito User Pool
            id_token = initiate_login(self.config, username, password)
            self._complete_authentication(id_token)
        except MfaRequired as mfa:
            # Capture session details and switch stack view to TOTP screen
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
            # Step 2: Answer SOFTWARE_TOKEN_MFA challenge
            id_token = respond_to_mfa_challenge(
                self.config, self._pending_username, self._pending_session, totp_code
            )
            self._complete_authentication(id_token)
        except AuthError as err:
            self.mfa_status.setText(str(err))
            self.btn_mfa.setEnabled(True)

    def _complete_authentication(self, id_token: str):
        try:
            # Step 3 & 4: Two-hop credential exchange (Identity Pool -> Cross-Account AssumeRole)
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
        
        self.setWindowTitle("S3 Survey Image Viewer")

        # Initialize S3 Client using temporary cross-account assumed credentials
        self.s3 = boto3.client(
            's3',
            region_name=self.config.bucket_region,
            **self.credentials.as_boto_kwargs()
        )
        
        self.image_keys = []
        self.current_index = -1
        
        # 1. Setup UI Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Left side: Image List (Allows jumping to a specific frame)
        self.image_list_widget = QListWidget()
        self.image_list_widget.currentRowChanged.connect(self.load_image_by_index)
        main_layout.addWidget(self.image_list_widget, 1)
        
        # Right side: Image display and controls
        right_layout = QVBoxLayout()
        
        # Status Label (Shows Frame X of Y)
        self.status_label = QLabel("Loading images from S3...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self.status_label)
        
        # Image Display Area
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(800, 600)
        self.image_label.setStyleSheet("background-color: black; color: white;")
        right_layout.addWidget(self.image_label, 5)
        
        # Controls (Next / Prev)
        controls_layout = QHBoxLayout()
        self.btn_prev = QPushButton("<< Previous Frame")
        self.btn_prev.clicked.connect(self.prev_frame)
        
        self.btn_next = QPushButton("Next Frame >>")
        self.btn_next.clicked.connect(self.next_frame)
        
        controls_layout.addWidget(self.btn_prev)
        controls_layout.addWidget(self.btn_next)
        right_layout.addLayout(controls_layout)
        
        main_layout.addLayout(right_layout, 3)
        
        # 2. Fetch all images on startup
        self.populate_image_list()
        
    def populate_image_list(self):
        """Fetches all JPGs in the prefix, handling folders with >1000 files."""
        try:
            paginator = self.s3.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=self.prefix)
            
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        if key.lower().endswith(('.jpg', '.jpeg')):
                            self.image_keys.append(key)
            
            # Sort alphabetically/numerically based on the file path
            self.image_keys.sort()
            
            if not self.image_keys:
                self.status_label.setText("No images found in this folder.")
                return
                
            # Populate the UI list widget
            for key in self.image_keys:
                filename = key.split('/')[-1]
                self.image_list_widget.addItem(filename)
                
            self.status_label.setText(f"Found {len(self.image_keys)} images. Select one to begin.")
            
            # Automatically load the first image
            self.image_list_widget.setCurrentRow(0)
            
        except Exception as e:
            QMessageBox.critical(self, "S3 Error", f"Could not load bucket data: {str(e)}")

    def load_image_by_index(self, index):
        """Downloads the image directly into memory and displays it."""
        if index < 0 or index >= len(self.image_keys):
            return
            
        self.current_index = index
        key = self.image_keys[self.current_index]
        
        try:
            # Fetch raw object bytes directly from S3
            response = self.s3.get_object(Bucket=self.bucket_name, Key=key)
            image_data = response['Body'].read()
            
            # Load bytes directly into QPixmap
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)
            
            # Scale pixmap maintaining aspect ratio
            scaled_pixmap = pixmap.scaled(
                self.image_label.size(), 
                Qt.AspectRatioMode.KeepAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
            self.image_label.setPixmap(scaled_pixmap)
            
            filename = key.split('/')[-1]
            self.status_label.setText(f"Frame {self.current_index + 1} of {len(self.image_keys)} | {filename}")
            
        except Exception as e:
            self.image_label.setText(f"Error loading image:\n{str(e)}")

    def next_frame(self):
        if self.current_index < len(self.image_keys) - 1:
            self.image_list_widget.setCurrentRow(self.current_index + 1)
            
    def prev_frame(self):
        if self.current_index > 0:
            self.image_list_widget.setCurrentRow(self.current_index - 1)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    # Load configuration settings from config.json
    try:
        config = load_config("config.json")
    except Exception as err:
        QMessageBox.critical(None, "Configuration Error", f"Failed to load config.json:\n{err}")
        sys.exit(1)
        
    # Launch Cognito Sign-In & MFA Dialog
    login_dialog = CognitoLoginDialog(config)
    if login_dialog.exec() != QDialog.DialogCode.Accepted:
        sys.exit(0)  # User closed dialog or login failed
        
    # Target prefix folder path in S3
    PREFIX = '2026/RSP TII Network Survey Imagery 2026/WE20260606/N04D226C/N04D226C_ROW/'
    
    # Launch main application window with authenticated temporary credentials
    viewer = S3ImageSequenceViewer(config, login_dialog.credentials, PREFIX)
    viewer.resize(1200, 800)
    viewer.show()
    
    sys.exit(app.exec())