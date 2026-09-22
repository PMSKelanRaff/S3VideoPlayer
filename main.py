import sys
from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog

from config import load_config
from login_dialog import CognitoLoginDialog
from module_selector_dialog import ModuleSelectorDialog
from S3VideoPlayer import S3ImageSequenceViewer
from pci_viewer import PCIViewer

# Default S3 prefix new sessions start from; both modules let the user pick
# a different segment afterwards via "Load Segments CSV".
DEFAULT_PREFIX = '2026/RSP TII Network Survey Imagery 2026/WE20260606/N04D226C/N04D226C_ROW/'


def main():
    app = QApplication(sys.argv)

    try:
        config = load_config("config.json")
    except Exception as err:
        QMessageBox.critical(None, "Configuration Error", f"Failed to load config.json:\n{err}")
        sys.exit(1)

    login_dialog = CognitoLoginDialog(config)
    if login_dialog.exec() != QDialog.DialogCode.Accepted:
        sys.exit(0)

    username = login_dialog.username_input.text().strip()
    credentials = login_dialog.credentials

    selector = ModuleSelectorDialog()
    if selector.exec() != QDialog.DialogCode.Accepted:
        sys.exit(0)

    if selector.selected_module == ModuleSelectorDialog.PCI_VIEWER:
        window = PCIViewer(config, credentials, DEFAULT_PREFIX, username)
    else:
        window = S3ImageSequenceViewer(config, credentials, DEFAULT_PREFIX, username)

    window.resize(1450, 900)
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
