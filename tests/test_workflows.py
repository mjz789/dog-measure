import math
import re

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from planevision.main_window import MainWindow
from planevision.models import Calibration, Measurement, MeasurementKind, Tool
from planevision.vision import SnapDetection


def _circle(center: tuple[float, float], radius: float) -> Measurement:
    return Measurement(
        MeasurementKind.CIRCLE,
        [center],
        radius * 2.0,
        "mm",
        metadata={
            "radius_px": radius,
            "center_x_mm": center[0],
            "center_y_mm": center[1],
            "confidence": 1.0,
            "method": "test",
        },
    )


def _window() -> MainWindow:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.canvas.set_frame(np.full((600, 800, 3), 180, dtype=np.uint8))
    window.canvas.calibration = Calibration(1.0, (0, 0), (40, 0), "test", image_size=(800, 600))
    return window


def test_concentricity_selects_same_center_circles_by_edge() -> None:
    window = _window()
    inner = _circle((300, 260), 50)
    outer = _circle((303, 264), 100)
    window.canvas.measurements.extend([inner, outer])
    window._set_tool(Tool.CONCENTRICITY)
    window._select_circle_for_composite_measurement((350, 260), Tool.CONCENTRICITY)
    window._select_circle_for_composite_measurement((403, 264), Tool.CONCENTRICITY)
    result = window.canvas.measurements[-1]
    assert result.kind == MeasurementKind.CONCENTRICITY
    assert math.isclose(result.value, 5.0)
    window.close()


def test_concentricity_can_select_two_close_circle_edges_from_same_click() -> None:
    window = _window()
    inner = _circle((300, 260), 100)
    outer = _circle((300, 260), 106)
    window.canvas.measurements.extend([inner, outer])
    window._set_tool(Tool.CONCENTRICITY)
    window._select_circle_for_composite_measurement((400, 260), Tool.CONCENTRICITY)
    window._select_circle_for_composite_measurement((400, 260), Tool.CONCENTRICITY)
    result = window.canvas.measurements[-1]
    assert result.kind == MeasurementKind.CONCENTRICITY
    assert result.metadata["circle_1"] != result.metadata["circle_2"]
    window.close()


def test_circle_array_uses_three_detected_centers() -> None:
    window = _window()
    circles = [_circle((400, 200), 25), _circle((500, 300), 25), _circle((400, 400), 25)]
    window.canvas.measurements.extend(circles)
    window._set_tool(Tool.CIRCLE_ARRAY)
    for circle in circles:
        window._select_circle_for_composite_measurement(circle.points[0], Tool.CIRCLE_ARRAY)
    result = window.canvas.measurements[-1]
    assert result.kind == MeasurementKind.CIRCLE_ARRAY
    assert math.isclose(result.value, 200.0, abs_tol=1e-6)
    assert result.points[0] == (400.0, 300.0)
    window.close()


def test_circle_center_distance_uses_two_detected_centers() -> None:
    window = _window()
    first = _circle((100, 100), 20)
    second = _circle((400, 500), 30)
    window.canvas.measurements.extend([first, second])
    window._refresh_results()
    window._set_tool(Tool.CIRCLE_CENTER_DISTANCE)
    window._select_circle_for_composite_measurement(first.points[0], Tool.CIRCLE_CENTER_DISTANCE)
    assert window.canvas.selected_ids == {first.id}
    assert {index.row() for index in window.results.selectionModel().selectedRows()} == {0}
    window._select_circle_for_composite_measurement(second.points[0], Tool.CIRCLE_CENTER_DISTANCE)
    result = window.canvas.measurements[-1]
    assert result.kind == MeasurementKind.CIRCLE_CENTER_DISTANCE
    assert math.isclose(result.value, 500.0)
    assert result.metadata == {"circle_1": first.id, "circle_2": second.id}
    window.close()


def test_center_distance_can_be_measured_from_two_selected_rows() -> None:
    from PySide6.QtCore import QItemSelectionModel

    window = _window()
    circles = [_circle((100, 100), 20), _circle((160, 180), 20)]
    window.canvas.measurements.extend(circles)
    window._refresh_results()
    for row in range(2):
        window.results.selectionModel().select(
            window.results.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    assert window.measure_center_distance_button.isEnabled()
    assert window.canvas.selected_ids == {circle.id for circle in circles}
    window.measure_center_distance_from_selection()
    assert window.canvas.measurements[-1].kind == MeasurementKind.CIRCLE_CENTER_DISTANCE
    assert math.isclose(window.canvas.measurements[-1].value, 100.0)
    window.close()


def test_canvas_circle_multi_selection_highlights_matching_rows() -> None:
    window = _window()
    circles = [_circle((100, 100), 20), _circle((200, 100), 20), _circle((150, 200), 20)]
    window.canvas.measurements.extend(circles)
    window._refresh_results()
    window.canvas.selected_ids = {circle.id for circle in circles}
    window.canvas.selected_id = None
    window._canvas_selection_changed(circles[-1])
    selected_rows = {index.row() for index in window.results.selectionModel().selectedRows()}
    assert selected_rows == {0, 1, 2}
    assert window.draw_array_button.isEnabled()
    window.draw_array_from_selection()
    assert window.canvas.measurements[-1].kind == MeasurementKind.CIRCLE_ARRAY
    window.close()


def test_ctrl_clicking_circles_on_canvas_syncs_multi_selection_to_rows() -> None:
    window = _window()
    circles = [_circle((200, 200), 30), _circle((400, 200), 30), _circle((300, 400), 30)]
    window.canvas.measurements.extend(circles)
    window._refresh_results()
    window.resize(1200, 800)
    window.show()
    QApplication.processEvents()
    for circle in circles:
        point = window.canvas.image_to_widget(circle.points[0]).toPoint()
        QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, point)
    assert window.canvas.selected_ids == {circle.id for circle in circles}
    assert {index.row() for index in window.results.selectionModel().selectedRows()} == {0, 1, 2}
    assert window.draw_array_button.isEnabled()
    window.close()


def test_center_distance_recalculates_when_source_circle_moves() -> None:
    window = _window()
    first = _circle((100, 100), 20)
    second = _circle((200, 100), 20)
    window.canvas.measurements.extend([first, second])
    window._create_circle_center_distance([first, second])
    result = window.canvas.measurements[-1]
    first.points[0] = (150, 100)
    window._measurement_geometry_edited(first)
    assert result.points == [(150, 100), (200, 100)]
    assert math.isclose(result.value, 50.0)
    window.close()


def test_array_can_be_drawn_from_three_selected_rows() -> None:
    window = _window()
    circles = [_circle((400, 200), 25), _circle((500, 300), 25), _circle((400, 400), 25)]
    window.canvas.measurements.extend(circles)
    window._refresh_results()
    for row in range(3):
        window.results.selectRow(row)
    # selectRow replaces selection on some Qt styles; select through selection model.
    from PySide6.QtCore import QItemSelectionModel

    for row in range(3):
        window.results.selectionModel().select(
            window.results.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    window.draw_array_from_selection()
    assert window.canvas.measurements[-1].kind == MeasurementKind.CIRCLE_ARRAY
    window.close()


def test_delete_removes_all_selected_rows() -> None:
    from PySide6.QtCore import QItemSelectionModel

    window = _window()
    circles = [_circle((200, 200), 20), _circle((300, 200), 20), _circle((400, 200), 20)]
    window.canvas.measurements.extend(circles)
    window._refresh_results()
    for row in (0, 2):
        window.results.selectionModel().select(
            window.results.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
    window.delete_selected()
    assert [item.id for item in window.canvas.measurements] == [circles[1].id]
    window.close()


def test_deleting_circle_removes_dependent_concentricity() -> None:
    window = _window()
    first = _circle((200, 200), 50)
    second = _circle((202, 203), 80)
    dependent = Measurement(
        MeasurementKind.CONCENTRICITY,
        [first.points[0], second.points[0]],
        3.6,
        "mm",
        metadata={"circle_1": first.id, "circle_2": second.id},
    )
    window.canvas.measurements.extend([first, second, dependent])
    window._refresh_results(first.id)
    window.canvas.selected_ids = {first.id}
    window.canvas.selected_id = first.id
    window.delete_selected()
    assert [item.id for item in window.canvas.measurements] == [second.id]
    window.close()


def test_downsampled_preview_keeps_full_resolution_coordinates() -> None:
    window = _window()
    full = np.full((2160, 3840, 3), 180, dtype=np.uint8)
    preview = np.full((810, 1440, 3), 180, dtype=np.uint8)
    window.canvas.resize(960, 540)
    window.canvas.set_frame(full, reset_view=True, display_frame=preview)
    widget_center = window.canvas.image_to_widget((1920.0, 1080.0))
    restored = window.canvas.widget_to_image(widget_center)
    assert restored is not None
    assert math.isclose(restored[0], 1920.0, abs_tol=1e-6)
    assert math.isclose(restored[1], 1080.0, abs_tol=1e-6)
    window.close()


def test_escape_cancels_candidate_and_returns_to_select() -> None:
    window = _window()
    candidate = Measurement(MeasurementKind.LENGTH, [(10, 10), (50, 10)], 40.0, "mm")
    window._set_tool(Tool.LENGTH)
    window._set_candidate(candidate)
    window.canvas.pending_points.append((10, 10))
    window.cancel_measurement()
    assert window.canvas.tool == Tool.SELECT
    assert window.canvas.candidate_measurement is None
    assert window.canvas.pending_points == []
    assert window.side_tool_buttons[Tool.SELECT].isChecked()
    window.close()


def test_fixed_measurement_is_added_without_confirmation_and_tool_stays_active() -> None:
    window = _window()
    measurement = Measurement(MeasurementKind.LENGTH, [(10, 10), (50, 10)], 40.0, "mm")
    window._set_tool(Tool.LENGTH)
    window._add_measurement(measurement)
    assert window.canvas.measurements == [measurement]
    assert window.canvas.tool == Tool.LENGTH
    assert window.side_tool_buttons[Tool.LENGTH].isChecked()
    assert window.canvas.selected_id == measurement.id
    window.close()


def test_copy_paste_selected_measurement() -> None:
    window = _window()
    measurement = Measurement(MeasurementKind.LENGTH, [(10, 10), (50, 10)], 40.0, "mm")
    window._add_measurement(measurement)
    window.copy_selected()
    window.paste_measurements()
    assert len(window.canvas.measurements) == 2
    assert window.canvas.measurements[1].points == [(22.0, 22.0), (62.0, 22.0)]
    window.close()


def test_escape_shortcut_really_switches_to_select() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window = _window()
    window.show()
    window._set_tool(Tool.LENGTH)
    candidate = Measurement(MeasurementKind.LENGTH, [(10, 10), (50, 10)], 40.0, "mm")
    window._set_candidate(candidate)
    window.results.setFocus()
    QTest.keyClick(window.results, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert window.canvas.tool == Tool.SELECT
    assert window.canvas.candidate_measurement is None
    window.close()


def test_dragged_length_recalculates_value() -> None:
    window = _window()
    measurement = Measurement(MeasurementKind.LENGTH, [(10, 10), (50, 10)], 40.0, "mm")
    window._add_measurement(measurement)
    measurement.points[1] = (10, 70)
    window._measurement_geometry_edited(measurement)
    assert math.isclose(measurement.value, 60.0)
    assert measurement.label == "60.000 mm"
    window.close()


def test_mouse_drag_length_handle_updates_geometry() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window = _window()
    window.resize(1100, 700)
    measurement = Measurement(MeasurementKind.LENGTH, [(100, 100), (300, 100)], 200.0, "mm")
    window._add_measurement(measurement)
    window.show()
    QApplication.processEvents()
    start = window.canvas.image_to_widget(measurement.points[1]).toPoint()
    target_image = (300.0, 240.0)
    target = window.canvas.image_to_widget(target_image).toPoint()
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.canvas, target, delay=10)
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=target)
    QApplication.processEvents()
    assert math.isclose(measurement.points[1][0], target_image[0], abs_tol=3.0)
    assert math.isclose(measurement.points[1][1], target_image[1], abs_tol=3.0)
    assert measurement.value > 240.0
    window.close()


def test_circle_center_and_radius_edit_recalculates_metadata() -> None:
    window = _window()
    circle = _circle((100, 100), 20)
    window._add_measurement(circle)
    circle.points[0] = (130, 150)
    circle.metadata["radius_px"] = 35.0
    window._measurement_geometry_edited(circle)
    assert circle.value == 70.0
    assert circle.metadata["center_x_mm"] == 130.0
    assert circle.metadata["center_y_mm"] == 150.0
    window.close()


def test_arc_measurement_and_edit() -> None:
    window = _window()
    arc = window._make_arc_measurement([(-10, 0), (0, -10), (10, 0)])
    window._add_measurement(arc)
    assert arc.kind == MeasurementKind.ARC
    assert math.isclose(arc.value, math.pi * 10.0)
    assert math.isclose(abs(float(arc.metadata["sweep_degrees"])), 180.0)
    window.close()


def test_three_arc_clicks_complete_and_keep_arc_tool_active(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    window = _window()
    monkeypatch.setattr(
        "planevision.main_window.snap_point_near",
        lambda image, point, search_radius=42: SnapDetection(point, 1.0, "test"),
    )
    window._set_tool(Tool.ARC)
    for point in ((200.0, 300.0), (300.0, 200.0), (400.0, 300.0)):
        window._canvas_clicked(*point)
    assert window.canvas.measurements[-1].kind == MeasurementKind.ARC
    assert window.canvas.tool == Tool.ARC
    assert window.canvas.pending_points == []
    window.close()


def test_circle_array_tool_accepts_three_detected_centers_from_canvas() -> None:
    window = _window()
    circles = [_circle((250, 200), 20), _circle((350, 300), 20), _circle((250, 400), 20)]
    window.canvas.measurements.extend(circles)
    window._set_tool(Tool.CIRCLE_ARRAY)
    for circle in circles:
        window._canvas_clicked(*circle.points[0])
    result = window.canvas.measurements[-1]
    assert result.kind == MeasurementKind.CIRCLE_ARRAY
    assert window.canvas.tool == Tool.CIRCLE_ARRAY
    assert window.canvas.pending_points == []
    assert window._pending_circle_ids == []
    window.close()


def test_three_point_circle_is_distinct_from_arc_and_recalculates() -> None:
    window = _window()
    measurement = window._make_three_point_circle_measurement(
        [(200.0, 300.0), (300.0, 200.0), (400.0, 300.0)]
    )
    window._add_measurement(measurement)
    assert measurement.kind == MeasurementKind.THREE_POINT_CIRCLE
    assert math.isclose(measurement.value, 200.0)
    measurement.points[2] = (300.0, 180.0)
    window._measurement_geometry_edited(measurement)
    assert measurement.kind == MeasurementKind.THREE_POINT_CIRCLE
    assert measurement.value > 200.0
    window.close()


def test_origin_can_be_set_to_image_center() -> None:
    window = _window()
    window._set_tool(Tool.ORIGIN)
    window.set_origin_to_image_center()
    assert window.canvas.origin == (400.0, 300.0)
    assert window.canvas.tool == Tool.ORIGIN
    window.close()


def test_selected_tool_shows_corresponding_parameter_panel() -> None:
    window = _window()
    window._set_tool(Tool.CIRCLE)
    assert window.tool_parameters.currentWidget().isAncestorOf(window.circle_radius)
    window._set_tool(Tool.ORIGIN)
    assert window.tool_parameters.currentWidget().isAncestorOf(window.origin_center_button)
    window.close()


def test_manual_calibration_uses_entered_real_length() -> None:
    window = _window()
    window.manual_calibration_length.setValue(10.0)
    window._set_tool(Tool.MANUAL_CALIBRATION)
    window._canvas_clicked(100.0, 100.0)
    window._canvas_clicked(300.0, 100.0)
    assert window.canvas.calibration is not None
    assert math.isclose(window.canvas.calibration.mm_per_pixel, 0.05)
    assert window.canvas.calibration.reference_mm == 10.0
    window.close()


def test_calibration_endpoints_can_be_dragged_and_recalculate_measurements() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window = _window()
    calibration = Calibration(0.05, (100.0, 100.0), (300.0, 100.0), "test", reference_mm=10.0)
    window._set_calibration(calibration)
    measurement = Measurement(MeasurementKind.LENGTH, [(100, 200), (200, 200)], 5.0, "mm")
    window._add_measurement(measurement)
    window._set_tool(Tool.SELECT)
    window.canvas.show_calibration_overlay = True
    window.resize(1100, 700)
    window.show()
    QApplication.processEvents()

    start = window.canvas.image_to_widget(calibration.end).toPoint()
    target = window.canvas.image_to_widget((400.0, 100.0)).toPoint()
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.canvas, target, delay=10)
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=target)
    QApplication.processEvents()

    assert math.isclose(calibration.end[0], 400.0, abs_tol=3.0)
    assert math.isclose(calibration.pixel_length, 300.0, abs_tol=3.0)
    assert math.isclose(calibration.mm_per_pixel, calibration.reference_mm / calibration.pixel_length)
    assert math.isclose(measurement.value, 100.0 * calibration.mm_per_pixel)
    assert window._restored_calibration is calibration
    window.close()


def test_auto_calibration_reports_detected_length_and_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    window = _window()
    detected = Calibration(
        0.02,
        (100.0, 100.0),
        (1100.0, 100.0),
        "自动识别 20 mm 标尺",
        reference_mm=20.0,
        image_size=(800, 600),
    )
    messages = []
    monkeypatch.setattr("planevision.main_window.detect_metric_ruler", lambda image: detected)
    monkeypatch.setattr(
        "planevision.main_window.QMessageBox.information",
        lambda parent, title, message: messages.append((title, message)),
    )

    window.auto_calibrate()

    assert window.canvas.calibration is detected
    assert messages and messages[0][0] == "标定成功"
    assert "20 mm 标尺" in messages[0][1]
    assert "标定成功" in messages[0][1]
    window.close()


def test_tool_search_wheel_updates_visible_radius() -> None:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    window = _window()
    window._set_tool(Tool.LENGTH)
    start = window.snap_radius.value()
    event = QWheelEvent(
        QPointF(300, 300),
        QPointF(300, 300),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    window.canvas.wheelEvent(event)
    assert window.snap_radius.value() > start
    assert window.canvas.snap_search_radius == float(window.snap_radius.value())
    window.close()


def test_measurement_label_can_be_dragged() -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    window = _window()
    measurement = Measurement(MeasurementKind.LENGTH, [(100, 200), (300, 200)], 200.0, "mm", label="200 mm")
    window._add_measurement(measurement)
    window._set_tool(Tool.SELECT)
    window.resize(1100, 700)
    window.show()
    QApplication.processEvents()
    bounds = window.canvas._label_rects[measurement.id]
    start = bounds.center().toPoint()
    target = start + QPoint(90, -55)
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.canvas, target, delay=10)
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=target)
    QApplication.processEvents()
    assert "label_x_px" in measurement.metadata
    assert "label_y_px" in measurement.metadata
    window.close()


def test_export_results_writes_same_named_png_and_csv(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    window = _window()
    window._add_measurement(Measurement(MeasurementKind.LENGTH, [(10, 10), (50, 10)], 40.0, "mm"))
    target = tmp_path / "inspection_report.png"
    monkeypatch.setattr(
        "planevision.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(target), "测量结果 (*.png *.csv)"),
    )
    window.show()
    QApplication.processEvents()
    window.export_results()
    assert (tmp_path / "inspection_report.png").is_file()
    assert (tmp_path / "inspection_report.csv").is_file()
    window.close()


def test_export_default_name_uses_me_timestamp_and_increasing_sequence(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    window = _window()
    window._add_measurement(Measurement(MeasurementKind.LENGTH, [(10, 10), (50, 10)], 40.0, "mm"))
    suggestions = []

    def choose_path(*args, **kwargs):  # type: ignore[no-untyped-def]
        suggestion = str(args[2])
        suggestions.append(suggestion)
        return str(tmp_path / suggestion), "测量结果 (*.png *.csv)"

    monkeypatch.setattr("planevision.main_window.QFileDialog.getSaveFileName", choose_path)
    window.show()
    QApplication.processEvents()
    window.export_results()
    window.export_results()

    assert re.fullmatch(r"me_\d{8}_\d{6}_001", suggestions[0])
    assert re.fullmatch(r"me_\d{8}_\d{6}_002", suggestions[1])
    assert (tmp_path / f"{suggestions[0]}.png").is_file()
    assert (tmp_path / f"{suggestions[0]}.csv").is_file()
    assert (tmp_path / f"{suggestions[1]}.png").is_file()
    assert (tmp_path / f"{suggestions[1]}.csv").is_file()
    window.close()


def test_cancelled_export_does_not_advance_sequence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    window = _window()
    window._add_measurement(Measurement(MeasurementKind.LENGTH, [(10, 10), (50, 10)], 40.0, "mm"))
    monkeypatch.setattr(
        "planevision.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: ("", ""),
    )
    window.export_results()
    assert window._export_sequence == 1
    window.close()


def test_small_window_uses_scrollable_side_panel() -> None:
    window = _window()
    window.resize(900, 600)
    window.show()
    QApplication.processEvents()
    assert window.side_scroll.verticalScrollBar().maximum() > 0
    buttons = list(window.side_tool_buttons.values())
    geometries = [button.geometry() for button in buttons if button.parentWidget() is buttons[0].parentWidget()]
    assert all(not first.intersects(second) for index, first in enumerate(geometries) for second in geometries[index + 1 :])
    window.close()


def test_selected_camera_resolution_is_passed_to_controller() -> None:
    window = _window()
    saved = {}

    class MemorySettings:
        @staticmethod
        def setValue(key, value) -> None:  # type: ignore[no-untyped-def]
            saved[key] = value

    window.settings = MemorySettings()
    resolution_index = window.camera_resolution.findText("1920 × 1080")
    window.camera_resolution.setCurrentIndex(resolution_index)
    calls = []
    window.camera.start = lambda index, width, height, fps: calls.append((index, width, height, fps))
    window.toggle_camera()
    assert calls == [(int(window.camera_index.currentData()), 1920, 1080, 10)]
    assert saved["camera/resolution"] == "1920x1080"
    window.close()


def test_camera_opened_reports_unsupported_requested_resolution() -> None:
    window = _window()
    window._requested_camera_resolution = (3840, 2160)
    window._camera_opened(1920, 1080, 10.0)
    assert "实际：1920 × 1080" in window.source_status.text()
    assert "请求：3840 × 2160（设备未采用）" in window.source_status.text()
    window.close()


def test_resolution_change_restarts_running_camera() -> None:
    window = _window()
    calls = []
    class RunningThread:
        @staticmethod
        def isRunning() -> bool:
            return True

    window.camera.thread = RunningThread()
    window.camera.stop = lambda: calls.append("stop")
    resolution_index = window.camera_resolution.findText("1280 × 720")
    window.camera_resolution.setCurrentIndex(resolution_index)
    assert calls == ["stop"]
    assert window._camera_restart_pending

    window.camera.thread = None
    window.camera.start = lambda index, width, height, fps: calls.append((width, height, fps))
    window._camera_stopped()
    assert calls[-1] == (1280, 720, 10)
    assert not window._camera_restart_pending
    window.close()
