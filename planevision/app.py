from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def _resource_path(relative_path: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return bundle_root / relative_path


STYLESHEET = """
QWidget {
    color: #e6ebef;
    background: #272d32;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
    letter-spacing: 0px;
}
QMainWindow, QToolBar, QStatusBar { background: #252a2f; }
QToolBar {
    border: none;
    border-bottom: 1px solid #3a4249;
    spacing: 3px;
    padding: 5px 8px;
}
QToolBar QToolButton {
    min-height: 30px;
    padding: 0 10px;
    border: 1px solid transparent;
    border-radius: 4px;
}
QToolBar QToolButton:hover { background: #343b41; border-color: #465058; }
QToolBar QToolButton:checked { background: #164b45; border-color: #21a58f; color: #8ce7d5; }
QScrollArea#sideScroll { background: #2c3237; border-left: 1px solid #3e474e; }
QScrollArea#sideScroll > QWidget > QWidget { background: #2c3237; }
QFrame#sidePanel { background: #2c3237; }
QScrollBar:vertical { background: #252b30; width: 11px; margin: 0; }
QScrollBar::handle:vertical { background: #56616a; min-height: 32px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel#sectionHeading { font-size: 13px; font-weight: 700; color: #f5f7f8; }
QLabel#secondaryText { color: #9da8b1; font-size: 12px; }
QLabel#calibrationStatus {
    color: #b9c2c9;
    background: #23282c;
    border: 1px solid #444d54;
    border-radius: 5px;
    padding: 9px;
}
QLabel#calibrationStatus[valid="true"] { color: #8be0cf; border-color: #218875; background: #203934; }
QPushButton, QComboBox, QSpinBox, QDoubleSpinBox {
    min-height: 30px;
    border: 1px solid #4a545c;
    border-radius: 4px;
    background: #343b41;
    padding: 0 9px;
}
QPushButton:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: #73818b; background: #3b434a; }
QPushButton:pressed, QPushButton:checked { background: #1f6e61; border-color: #26a991; }
QPushButton:disabled { color: #747e85; background: #30363a; border-color: #3d454a; }
QPushButton#primaryButton { background: #168b77; border-color: #21a991; color: white; font-weight: 700; }
QPushButton#primaryButton:hover { background: #1b9c86; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #30373d; border: 1px solid #56616a; selection-background-color: #187e6d; }
QTableWidget {
    background: #242a2e;
    alternate-background-color: #293035;
    border: 1px solid #414a51;
    border-radius: 4px;
    gridline-color: #384047;
    selection-background-color: #176b5e;
}
QHeaderView::section { background: #343b41; color: #bbc4cb; border: none; border-bottom: 1px solid #4a545b; padding: 6px; }
QFrame#separator { color: #41494f; max-height: 1px; }
QStatusBar { border-top: 1px solid #3b4349; color: #aeb7be; }
QStatusBar QLabel { padding: 0 8px; color: #aeb7be; }
QMessageBox QPushButton { min-width: 78px; }
QToolTip { color: #f4f6f7; background: #181c1f; border: 1px solid #4d565d; padding: 5px; }
"""


def main() -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    app.setApplicationName("狗狗测量")
    app.setApplicationDisplayName("狗狗测量")
    app.setOrganizationName("Codex")
    app.setWindowIcon(QIcon(str(_resource_path("assets/dog_measure.ico"))))
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#272d32"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e6ebef"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#242a2e"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#293035"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e6ebef"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#343b41"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e6ebef"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#168b77"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
