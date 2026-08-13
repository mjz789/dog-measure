from __future__ import annotations

import sys
from pathlib import Path

import cv2
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from planevision.app import STYLESHEET
from planevision.main_window import MainWindow
from planevision.models import Measurement, MeasurementKind
from planevision.vision import detect_40mm_ruler


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: ui_smoke.py INPUT_IMAGE OUTPUT_SCREENSHOT")
    image = cv2.imread(sys.argv[1])
    if image is None:
        raise SystemExit("unable to read input image")
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.resize(1440, 860)
    window.canvas.set_frame(image, reset_view=True)
    window.canvas.calibration = detect_40mm_ruler(image)
    window.canvas.show_calibration_overlay = True
    window.calibration_overlay_button.setEnabled(True)
    window.calibration_overlay_button.setChecked(True)
    window.canvas.measurements.extend(
        [
            Measurement(
                MeasurementKind.CIRCLE,
                [(1120.0, 700.0)],
                12.0,
                "mm",
                label="Ø 12.000 mm",
                metadata={"radius_px": 84.0, "center_x_mm": 0.0, "center_y_mm": 0.0},
            ),
            Measurement(
                MeasurementKind.CIRCLE,
                [(1123.0, 703.0)],
                23.0,
                "mm",
                label="Ø 23.000 mm",
                metadata={"radius_px": 161.0, "center_x_mm": 0.0, "center_y_mm": 0.0},
            ),
        ]
    )
    window._refresh_results()
    window.results.selectRow(0)
    window.source_status.setText(f"验证图片\n{image.shape[1]} × {image.shape[0]}")
    window._update_calibration_panel()
    window.show()

    def capture() -> None:
        output = Path(sys.argv[2])
        if not window.grab().save(str(output)):
            raise RuntimeError("unable to save screenshot")
        window.close()
        app.quit()

    QTimer.singleShot(800, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
