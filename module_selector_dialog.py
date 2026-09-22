from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton


class ModuleSelectorDialog(QDialog):
    """Shown after successful sign-in. Lets the user pick which module to open."""

    IMAGE_VIEWER = "image_viewer"
    PSCI_VIEWER = "psci_viewer"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_module: str | None = None

        self.setWindowTitle("Select Module")
        self.setFixedSize(260, 160)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose a module to open:"))

        btn_image_viewer = QPushButton("Image Viewer")
        btn_image_viewer.setMinimumHeight(40)
        btn_image_viewer.clicked.connect(lambda: self._select(self.IMAGE_VIEWER))
        layout.addWidget(btn_image_viewer)

        btn_psci_viewer = QPushButton("PSCI Viewer")
        btn_psci_viewer.setMinimumHeight(40)
        btn_psci_viewer.clicked.connect(lambda: self._select(self.PSCI_VIEWER))
        layout.addWidget(btn_psci_viewer)

    def _select(self, module_key: str):
        self.selected_module = module_key
        self.accept()
