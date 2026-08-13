from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QThreadPool
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeyEvent, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .camera import CameraController, camera_device_names
from .canvas import ImageCanvas
from .detection_worker import CircleDetectionRequest, CircleDetectionTask
from .models import (
    Calibration,
    Measurement,
    MeasurementKind,
    Tool,
    angle_degrees,
    arc_geometry,
    circumcircle,
    distance,
    polygon_area,
    polyline_length,
)
from .vision import (
    CircleDetection,
    LineDetection,
    detect_metric_ruler,
    detect_angle_candidates_near,
    detect_line_near,
    project_point_to_line,
    snap_point_near,
)


APP_TITLE = "狗狗测量"

CAMERA_RESOLUTIONS = (
    (3840, 2160),
    (2560, 1440),
    (1920, 1080),
    (1600, 1200),
    (1280, 960),
    (1280, 720),
    (1024, 768),
    (800, 600),
    (640, 480),
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1500, 920)
        self.setMinimumSize(760, 520)
        self.settings = QSettings("Codex", "PlaneVision")
        self.camera = CameraController(self)
        self.current_path: Path | None = None
        self.last_live_frame = None
        self.frozen = False
        self._pending_circle_ids: list[str] = []
        self._detection_pool = QThreadPool(self)
        self._detection_pool.setMaxThreadCount(1)
        self._circle_detection_busy = False
        self._active_detection_task: CircleDetectionTask | None = None
        self._last_preview_time = 0.0
        self._candidate_measurement: Measurement | None = None
        self._angle_candidates = []
        self._redo_stack: list[Measurement] = []
        self._copied_measurements: list[dict[str, object]] = []
        self._clipboard_offset_count = 0
        self._export_sequence = 1
        self._camera_restart_pending = False
        self._requested_camera_resolution = CAMERA_RESOLUTIONS[0]
        self.compute_backend = self._detect_compute_backend()
        self._restored_calibration: Calibration | None = self._load_calibration()
        self._build_ui()
        self._connect_signals()
        self._restore_window_state()
        self._update_calibration_panel()
        self._set_tool(Tool.SELECT)
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)

    def _build_ui(self) -> None:
        self._build_toolbar()
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.canvas = ImageCanvas()
        root_layout.addWidget(self.canvas, 1)

        self.side_scroll = QScrollArea()
        self.side_scroll.setObjectName("sideScroll")
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.side_scroll.setMinimumWidth(310)
        self.side_scroll.setMaximumWidth(355)

        side = QFrame()
        side.setObjectName("sidePanel")
        side.setMinimumWidth(285)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(16, 14, 16, 14)
        side_layout.setSpacing(10)

        camera_heading = QLabel("图像源")
        camera_heading.setObjectName("sectionHeading")
        side_layout.addWidget(camera_heading)
        source_row = QHBoxLayout()
        self.camera_index = QComboBox()
        camera_names = camera_device_names()
        for index, name in enumerate(camera_names or [f"摄像头 {index}" for index in range(6)]):
            self.camera_index.addItem(f"摄像头 {index} · {name}" if camera_names else name, index)
        saved_camera_index = int(self.settings.value("camera/index", 0))
        self.camera_index.setCurrentIndex(max(0, min(saved_camera_index, self.camera_index.count() - 1)))
        self.connect_button = QPushButton("连接")
        self.connect_button.setObjectName("primaryButton")
        source_row.addWidget(self.camera_index, 1)
        source_row.addWidget(self.connect_button)
        side_layout.addLayout(source_row)

        resolution_row = QHBoxLayout()
        resolution_row.addWidget(QLabel("分辨率"))
        self.camera_resolution = QComboBox()
        saved_resolution = str(self.settings.value("camera/resolution", "3840x2160"))
        saved_index = 0
        for item_index, (width, height) in enumerate(CAMERA_RESOLUTIONS):
            self.camera_resolution.addItem(f"{width} × {height}", (width, height))
            if saved_resolution == f"{width}x{height}":
                saved_index = item_index
        self.camera_resolution.setCurrentIndex(saved_index)
        resolution_row.addWidget(self.camera_resolution, 1)
        side_layout.addLayout(resolution_row)

        source_actions = QHBoxLayout()
        self.freeze_button = QPushButton("冻结画面")
        self.freeze_button.setCheckable(True)
        self.freeze_button.setEnabled(False)
        self.open_button = QPushButton("打开图片")
        source_actions.addWidget(self.freeze_button)
        source_actions.addWidget(self.open_button)
        side_layout.addLayout(source_actions)
        self.source_status = QLabel("未连接")
        self.source_status.setObjectName("secondaryText")
        self.source_status.setWordWrap(True)
        side_layout.addWidget(self.source_status)

        self._add_separator(side_layout)
        calibration_heading = QLabel("比例标定")
        calibration_heading.setObjectName("sectionHeading")
        side_layout.addWidget(calibration_heading)
        self.auto_calibration_button = QPushButton("自动识别画面标尺")
        self.auto_calibration_button.setObjectName("primaryButton")
        side_layout.addWidget(self.auto_calibration_button)
        calibration_actions = QHBoxLayout()
        self.manual_calibration_button = QPushButton("两点划线")
        self.clear_calibration_button = QPushButton("清除标定")
        calibration_actions.addWidget(self.manual_calibration_button)
        calibration_actions.addWidget(self.clear_calibration_button)
        side_layout.addLayout(calibration_actions)
        manual_length_row = QHBoxLayout()
        manual_length_row.addWidget(QLabel("划线实际长度"))
        self.manual_calibration_length = QDoubleSpinBox()
        self.manual_calibration_length.setRange(0.001, 10000.0)
        self.manual_calibration_length.setDecimals(3)
        self.manual_calibration_length.setValue(float(self.settings.value("calibration/manual_length_mm", 40.0)))
        self.manual_calibration_length.setSuffix(" mm")
        manual_length_row.addWidget(self.manual_calibration_length, 1)
        side_layout.addLayout(manual_length_row)
        self.calibration_overlay_button = QPushButton("显示标定辅助线")
        self.calibration_overlay_button.setCheckable(True)
        self.calibration_overlay_button.setEnabled(False)
        side_layout.addWidget(self.calibration_overlay_button)
        self.calibration_status = QLabel("尚未标定")
        self.calibration_status.setObjectName("calibrationStatus")
        self.calibration_status.setWordWrap(True)
        side_layout.addWidget(self.calibration_status)

        self._add_separator(side_layout)
        tool_heading = QLabel("智能测量工具")
        tool_heading.setObjectName("sectionHeading")
        side_layout.addWidget(tool_heading)
        tool_grid = QGridLayout()
        tool_grid.setSpacing(6)
        side_tools = [
            (Tool.SELECT, "选择 (V)"),
            (Tool.LENGTH, "智能长度 (L)"),
            (Tool.ANGLE, "自动角度 (A)"),
            (Tool.POINT, "点坐标 (P)"),
            (Tool.POINT_LINE_DISTANCE, "点到直线 (D)"),
            (Tool.CIRCLE, "检测圆形 (C)"),
            (Tool.POLYLINE, "折线总长 (W)"),
            (Tool.AREA, "多边形面积 (G)"),
            (Tool.ARC, "三点圆弧 (B)"),
            (Tool.THREE_POINT_CIRCLE, "三点圆 (Q)"),
            (Tool.CONCENTRICITY, "两圆同心度 (T)"),
            (Tool.ORIGIN, "设置原点 (O)"),
        ]
        self.side_tool_buttons: dict[Tool, QPushButton] = {}
        for index, (tool, label) in enumerate(side_tools):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, selected=tool: self._set_tool(selected))
            self.side_tool_buttons[tool] = button
            tool_grid.addWidget(button, index // 2, index % 2)
        side_layout.addLayout(tool_grid)
        self.circle_button = self.side_tool_buttons[Tool.CIRCLE]
        self.concentricity_button = self.side_tool_buttons[Tool.CONCENTRICITY]

        self._add_separator(side_layout)
        parameter_heading = QLabel("工具参数")
        parameter_heading.setObjectName("sectionHeading")
        side_layout.addWidget(parameter_heading)
        self.tool_parameters = QStackedWidget()
        self._tool_parameter_indexes: dict[Tool, int] = {}

        no_parameters = QWidget()
        no_parameters_layout = QHBoxLayout(no_parameters)
        no_parameters_layout.setContentsMargins(0, 0, 0, 0)
        no_parameters_layout.addWidget(QLabel("当前工具无需调整参数"))
        no_parameters_index = self.tool_parameters.addWidget(no_parameters)

        snap_parameters = QWidget()
        snap_layout = QHBoxLayout(snap_parameters)
        snap_layout.setContentsMargins(0, 0, 0, 0)
        snap_layout.addWidget(QLabel("点吸附半径"))
        self.snap_radius = QSpinBox()
        self.snap_radius.setRange(8, 1200)
        self.snap_radius.setValue(int(self.settings.value("vision/snap_radius", 42)))
        self.snap_radius.setSuffix(" px")
        snap_layout.addWidget(self.snap_radius, 1)
        snap_parameters_index = self.tool_parameters.addWidget(snap_parameters)

        angle_parameters = QWidget()
        angle_layout = QHBoxLayout(angle_parameters)
        angle_layout.setContentsMargins(0, 0, 0, 0)
        angle_layout.addWidget(QLabel("直线搜索半径"))
        self.angle_search_radius = QSpinBox()
        self.angle_search_radius.setRange(60, 1200)
        self.angle_search_radius.setValue(int(self.settings.value("vision/angle_search_radius", 170)))
        self.angle_search_radius.setSuffix(" px")
        angle_layout.addWidget(self.angle_search_radius, 1)
        angle_parameters_index = self.tool_parameters.addWidget(angle_parameters)

        point_line_parameters = QWidget()
        point_line_layout = QGridLayout(point_line_parameters)
        point_line_layout.setContentsMargins(0, 0, 0, 0)
        point_line_layout.addWidget(QLabel("点吸附"), 0, 0)
        self.point_line_snap_radius = QSpinBox()
        self.point_line_snap_radius.setRange(8, 1200)
        self.point_line_snap_radius.setValue(int(self.settings.value("vision/point_line_snap_radius", 42)))
        self.point_line_snap_radius.setSuffix(" px")
        point_line_layout.addWidget(self.point_line_snap_radius, 0, 1)
        point_line_layout.addWidget(QLabel("直线搜索"), 1, 0)
        self.line_search_radius = QSpinBox()
        self.line_search_radius.setRange(48, 1200)
        self.line_search_radius.setValue(int(self.settings.value("vision/line_search_radius", 150)))
        self.line_search_radius.setSuffix(" px")
        point_line_layout.addWidget(self.line_search_radius, 1, 1)
        point_line_parameters_index = self.tool_parameters.addWidget(point_line_parameters)

        circle_parameters = QWidget()
        circle_layout = QVBoxLayout(circle_parameters)
        circle_layout.setContentsMargins(0, 0, 0, 0)
        circle_row = QHBoxLayout()
        circle_row.addWidget(QLabel("红环半径"))
        self.circle_radius = QSpinBox()
        self.circle_radius.setRange(12, 1200)
        self.circle_radius.setValue(int(self.settings.value("circle/search_radius", 280)))
        self.circle_radius.setSuffix(" px")
        circle_row.addWidget(self.circle_radius, 1)
        circle_layout.addLayout(circle_row)
        self.compute_status = QLabel(f"计算后端：{self.compute_backend}")
        self.compute_status.setObjectName("secondaryText")
        circle_layout.addWidget(self.compute_status)
        circle_parameters_index = self.tool_parameters.addWidget(circle_parameters)

        origin_parameters = QWidget()
        origin_layout = QHBoxLayout(origin_parameters)
        origin_layout.setContentsMargins(0, 0, 0, 0)
        origin_layout.addWidget(QLabel("原点位置"))
        self.origin_center_button = QPushButton("设为画面中心")
        origin_layout.addWidget(self.origin_center_button, 1)
        origin_parameters_index = self.tool_parameters.addWidget(origin_parameters)

        for tool in Tool:
            self._tool_parameter_indexes[tool] = no_parameters_index
        for tool in {
            Tool.LENGTH,
            Tool.POINT,
            Tool.POLYLINE,
            Tool.AREA,
            Tool.ARC,
            Tool.THREE_POINT_CIRCLE,
        }:
            self._tool_parameter_indexes[tool] = snap_parameters_index
        self._tool_parameter_indexes[Tool.ANGLE] = angle_parameters_index
        self._tool_parameter_indexes[Tool.POINT_LINE_DISTANCE] = point_line_parameters_index
        self._tool_parameter_indexes[Tool.CIRCLE] = circle_parameters_index
        self._tool_parameter_indexes[Tool.ORIGIN] = origin_parameters_index
        side_layout.addWidget(self.tool_parameters)

        self._add_separator(side_layout)
        array_heading = QLabel("圆心阵列")
        array_heading.setObjectName("sectionHeading")
        side_layout.addWidget(array_heading)
        circle_measure_row = QHBoxLayout()
        self.circle_array_button = QPushButton("点选三圆心")
        self.circle_array_button.setCheckable(True)
        self.side_tool_buttons[Tool.CIRCLE_ARRAY] = self.circle_array_button
        self.draw_array_button = QPushButton("选中三圆自动绘制")
        self.draw_array_button.setEnabled(False)
        circle_measure_row.addWidget(self.circle_array_button)
        circle_measure_row.addWidget(self.draw_array_button)
        side_layout.addLayout(circle_measure_row)

        measure_heading_row = QHBoxLayout()
        measure_heading = QLabel("测量结果")
        measure_heading.setObjectName("sectionHeading")
        self.result_count = QLabel("0 项")
        self.result_count.setObjectName("secondaryText")
        measure_heading_row.addWidget(measure_heading)
        measure_heading_row.addStretch()
        measure_heading_row.addWidget(self.result_count)
        side_layout.addLayout(measure_heading_row)

        self.results = QTableWidget(0, 3)
        self.results.setHorizontalHeaderLabels(["类型", "数值", "中心/顶点"])
        self.results.verticalHeader().setVisible(False)
        self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results.setMinimumHeight(170)
        self.results.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.results.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.results.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        side_layout.addWidget(self.results, 1)

        result_actions = QHBoxLayout()
        self.delete_button = QPushButton("删除")
        self.clear_button = QPushButton("清空")
        self.export_button = QPushButton("导出截图 + CSV")
        result_actions.addWidget(self.delete_button)
        result_actions.addWidget(self.clear_button)
        result_actions.addWidget(self.export_button)
        side_layout.addLayout(result_actions)
        self.side_scroll.setWidget(side)
        root_layout.addWidget(self.side_scroll)
        self.setCentralWidget(root)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_message = QLabel("就绪")
        self.cursor_position = QLabel("X: -, Y: -")
        self.zoom_status = QLabel("适配窗口")
        status.addWidget(self.status_message, 1)
        status.addPermanentWidget(self.cursor_position)
        status.addPermanentWidget(self.zoom_status)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("测量工具")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        style = QApplication.style()

        open_action = QAction(style.standardIcon(style.StandardPixmap.SP_DialogOpenButton), "打开", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_image)
        toolbar.addAction(open_action)
        save_action = QAction(style.standardIcon(style.StandardPixmap.SP_DialogSaveButton), "导出截图 + CSV", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.export_results)
        toolbar.addAction(save_action)
        toolbar.addSeparator()

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        tool_specs = [
            (Tool.SELECT, "选择", "V"),
            (Tool.LENGTH, "智能长度", "L"),
            (Tool.ANGLE, "自动角度", "A"),
            (Tool.POINT, "点坐标", "P"),
            (Tool.POINT_LINE_DISTANCE, "点线距离", "D"),
            (Tool.POLYLINE, "折线", "W"),
            (Tool.AREA, "面积", "G"),
            (Tool.ARC, "圆弧", "B"),
            (Tool.THREE_POINT_CIRCLE, "三点圆", "Q"),
            (Tool.CIRCLE, "圆形", "C"),
            (Tool.CONCENTRICITY, "同心度", "T"),
            (Tool.CIRCLE_ARRAY, "圆形阵列", "R"),
            (Tool.ORIGIN, "设置原点", "O"),
        ]
        self.tool_actions: dict[Tool, QAction] = {}
        for tool, label, shortcut in tool_specs:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(shortcut))
            action.setData(tool.value)
            action.triggered.connect(lambda checked=False, selected=tool: self._set_tool(selected))
            self.tool_group.addAction(action)
            self.tool_actions[tool] = action
            toolbar.addAction(action)
        toolbar.addSeparator()
        undo_action = QAction(style.standardIcon(style.StandardPixmap.SP_ArrowBack), "撤销", self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(self.undo_measurement)
        toolbar.addAction(undo_action)
        toolbar.addSeparator()
        fit_action = QAction("适配", self)
        fit_action.setShortcut(QKeySequence("F"))
        fit_action.triggered.connect(self.canvas_fit)
        toolbar.addAction(fit_action)
        actual_action = QAction("1:1", self)
        actual_action.setShortcut(QKeySequence("1"))
        actual_action.triggered.connect(self.canvas_actual)
        toolbar.addAction(actual_action)
        delete_action = QAction("删除选中测量", self)
        delete_action.setShortcut(QKeySequence("Delete"))
        delete_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        delete_action.triggered.connect(self.delete_selected)
        self.addAction(delete_action)
        self.delete_shortcut_action = delete_action
        self._add_shortcut("Ctrl+C", self.copy_selected, "复制测量")
        self._add_shortcut("Ctrl+V", self.paste_measurements, "粘贴测量")
        self._add_shortcut("Ctrl+A", self.select_all_measurements, "全选测量")
        self._add_shortcut("Ctrl+Y", self.redo_measurement, "重做")
        self._add_shortcut("Ctrl+Shift+Z", self.redo_measurement, "重做")
        self._add_shortcut("Ctrl+R", self.refresh_source, "刷新图像")
        self._add_shortcut("F5", self.refresh_source, "刷新图像")
        self._add_shortcut("Ctrl+Shift+S", self.export_results, "导出截图和结果")

    def _add_shortcut(self, shortcut: str, callback, label: str) -> QAction:  # type: ignore[no-untyped-def]
        action = QAction(label, self)
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(callback)
        self.addAction(action)
        return action

    @staticmethod
    def _add_separator(layout: QVBoxLayout) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("separator")
        layout.addWidget(line)

    @staticmethod
    def _detect_compute_backend() -> str:
        try:
            if hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0:
                return "NVIDIA CUDA"
        except cv2.error:
            pass
        return "OpenCV CPU 后台（CUDA 不可用）"

    def _connect_signals(self) -> None:
        self.connect_button.clicked.connect(self.toggle_camera)
        self.camera_resolution.currentIndexChanged.connect(self._camera_resolution_changed)
        self.freeze_button.toggled.connect(self.set_frozen)
        self.open_button.clicked.connect(self.open_image)
        self.auto_calibration_button.clicked.connect(self.auto_calibrate)
        self.manual_calibration_button.clicked.connect(lambda: self._set_tool(Tool.MANUAL_CALIBRATION))
        self.clear_calibration_button.clicked.connect(self.clear_calibration)
        self.calibration_overlay_button.toggled.connect(self._toggle_calibration_overlay)
        self.circle_array_button.clicked.connect(lambda: self._set_tool(Tool.CIRCLE_ARRAY))
        self.draw_array_button.clicked.connect(self.draw_array_from_selection)
        self.circle_radius.valueChanged.connect(self._circle_radius_changed)
        self.snap_radius.valueChanged.connect(self._snap_radius_changed)
        self.angle_search_radius.valueChanged.connect(self._angle_radius_changed)
        self.point_line_snap_radius.valueChanged.connect(self._point_line_snap_radius_changed)
        self.line_search_radius.valueChanged.connect(self._line_radius_changed)
        self.manual_calibration_length.valueChanged.connect(
            lambda value: self.settings.setValue("calibration/manual_length_mm", value)
        )
        self.origin_center_button.clicked.connect(self.set_origin_to_image_center)
        self.delete_button.clicked.connect(self.delete_selected)
        self.clear_button.clicked.connect(self.clear_measurements)
        self.export_button.clicked.connect(self.export_results)
        self.results.itemSelectionChanged.connect(self._table_selection_changed)
        self.canvas.imageClicked.connect(self._canvas_clicked)
        self.canvas.mouseImagePosition.connect(self._cursor_moved)
        self.canvas.zoomChanged.connect(self._zoom_changed)
        self.canvas.measurementSelected.connect(self._canvas_selection_changed)
        self.canvas.measurementChanging.connect(self._measurement_geometry_changed)
        self.canvas.measurementEdited.connect(self._measurement_geometry_edited)
        self.canvas.measurementHandleReleased.connect(self._measurement_handle_released)
        self.canvas.calibrationEdited.connect(self._calibration_geometry_edited)
        self.canvas.finishMeasurementRequested.connect(self.finish_variable_measurement)
        self.canvas.circleRadiusChanged.connect(self._circle_radius_from_wheel)
        self.canvas.toolSearchRadiusChanged.connect(self._tool_search_radius_from_wheel)
        self.camera.frameReady.connect(self._camera_frame)
        self.camera.opened.connect(self._camera_opened)
        self.camera.error.connect(self._camera_error)
        self.camera.stopped.connect(self._camera_stopped)
        self.canvas.circle_search_radius = float(self.circle_radius.value())
        self.canvas.snap_search_radius = float(self.snap_radius.value())
        self.canvas.angle_search_radius = float(self.angle_search_radius.value())
        self.canvas.line_search_radius = float(self.line_search_radius.value())

    def _set_tool(self, tool: Tool) -> None:
        if tool not in {Tool.SELECT, Tool.ORIGIN} and not self._ensure_image():
            tool = Tool.SELECT
        if tool in {
            Tool.LENGTH,
            Tool.ANGLE,
            Tool.POINT,
            Tool.POINT_LINE_DISTANCE,
            Tool.POLYLINE,
            Tool.AREA,
            Tool.ARC,
            Tool.THREE_POINT_CIRCLE,
            Tool.CIRCLE,
            Tool.CONCENTRICITY,
            Tool.CIRCLE_ARRAY,
            Tool.MANUAL_CALIBRATION,
        } and self.camera.running and not self.frozen:
            self.freeze_button.setChecked(True)
        self._pending_circle_ids.clear()
        self._clear_candidate()
        self.canvas.set_tool(tool)
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        else:
            self.tool_group.setExclusive(False)
            for action in self.tool_actions.values():
                action.setChecked(False)
            self.tool_group.setExclusive(True)
        for button_tool, button in self.side_tool_buttons.items():
            button.blockSignals(True)
            button.setChecked(button_tool == tool)
            button.blockSignals(False)
        if tool == Tool.POINT_LINE_DISTANCE:
            self.canvas.snap_search_radius = float(self.point_line_snap_radius.value())
            self.canvas.line_search_radius = float(self.line_search_radius.value())
        elif tool == Tool.ANGLE:
            self.canvas.angle_search_radius = float(self.angle_search_radius.value())
        elif tool != Tool.CIRCLE:
            self.canvas.snap_search_radius = float(self.snap_radius.value())
        self.tool_parameters.setCurrentIndex(self._tool_parameter_indexes.get(tool, 0))
        instructions = {
            Tool.SELECT: "选择工具：单击测量标注可选中",
            Tool.LENGTH: "智能长度：粗略点击两个端点，自动吸附后连续测量；Esc 退出",
            Tool.ANGLE: "自动角度：点击角点附近，自动拟合最近两条直线并立即完成",
            Tool.POINT: "点坐标：点击目标附近，自动吸附后立即完成",
            Tool.POINT_LINE_DISTANCE: "点到直线：先点击目标点，再点击直线附近，随后立即完成",
            Tool.POLYLINE: "折线总长：依次点击各顶点；双击、右键或 Enter 结束",
            Tool.AREA: "多边形面积：依次点击至少三个顶点；双击、右键或 Enter 结束",
            Tool.ARC: "三点圆弧：依次点击起点、弧上点和终点，完成后可继续测量；Esc 退出",
            Tool.THREE_POINT_CIRCLE: "三点圆：依次点击圆周上的三个点，自动拟合完整圆；Esc 退出",
            Tool.CIRCLE: "圆形识别：滚轮调整红环，使红线贴近目标圆周，然后单击",
            Tool.CONCENTRICITY: "同心度：依次点击两个已检测圆的彩色圆周",
            Tool.CIRCLE_ARRAY: "圆形阵列：依次点击 3 个已检测圆的圆心，或在右侧列表选择 3 个圆后绘制",
            Tool.ORIGIN: "原点工具：单击图像设置坐标原点",
            Tool.MANUAL_CALIBRATION: "两点划线标定：设置右侧实际长度，再依次点击线段两端",
        }
        self.status_message.setText(instructions[tool])

    def _ensure_image(self) -> bool:
        if not self.canvas.has_image:
            QMessageBox.information(self, "没有图像", "请先打开图片或连接 UVC 摄像头。")
            return False
        return True

    def toggle_camera(self) -> None:
        if self.camera.running:
            self._camera_restart_pending = False
            self.camera.stop()
            return
        self._start_camera()

    def _selected_camera_resolution(self) -> tuple[int, int]:
        value = self.camera_resolution.currentData()
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return int(value[0]), int(value[1])
        return CAMERA_RESOLUTIONS[0]

    def _start_camera(self) -> None:
        index = int(self.camera_index.currentData())
        width, height = self._selected_camera_resolution()
        self._requested_camera_resolution = (width, height)
        self.settings.setValue("camera/index", index)
        self.settings.setValue("camera/resolution", f"{width}x{height}")
        self.source_status.setText(f"正在连接 {width} × {height} UVC 视频流…")
        self.connect_button.setText("连接中…")
        self.connect_button.setEnabled(False)
        self.camera.start(index, width, height, 10)

    def _camera_resolution_changed(self) -> None:
        width, height = self._selected_camera_resolution()
        self.settings.setValue("camera/resolution", f"{width}x{height}")
        if not self.camera.running:
            return
        if self.frozen:
            self.freeze_button.setChecked(False)
        self._camera_restart_pending = True
        self.connect_button.setEnabled(False)
        self.connect_button.setText("切换中…")
        self.source_status.setText(f"正在切换到 {width} × {height}…")
        self.camera.stop()

    def _camera_opened(self, width: int, height: int, fps: float) -> None:
        self.current_path = None
        self.connect_button.setEnabled(True)
        self.connect_button.setText("断开")
        self.freeze_button.setEnabled(True)
        requested_width, requested_height = self._requested_camera_resolution
        if (width, height) == (requested_width, requested_height):
            self.source_status.setText(f"实时：{width} × {height}  |  {fps:.1f} FPS")
            self.status_message.setText("UVC 摄像头已连接")
        else:
            self.source_status.setText(
                f"实际：{width} × {height}  |  {fps:.1f} FPS\n"
                f"请求：{requested_width} × {requested_height}（设备未采用）"
            )
            self.status_message.setText("摄像头不支持所选模式，已使用设备返回的分辨率")

    def _camera_frame(self, packet) -> None:  # type: ignore[no-untyped-def]
        if isinstance(packet, tuple):
            frame, preview = packet
        else:
            frame, preview = packet, None
        self.last_live_frame = frame
        if self.frozen:
            return
        previous_size = self.canvas.image_size
        frame_size = (int(frame.shape[1]), int(frame.shape[0]))
        source_changed = previous_size != frame_size
        self.canvas.set_frame(frame, reset_view=source_changed, display_frame=preview)
        if source_changed:
            self._apply_saved_calibration_if_compatible()

    def _camera_error(self, message: str) -> None:
        QMessageBox.critical(self, "摄像头错误", message)
        self.source_status.setText(message)

    def _camera_stopped(self) -> None:
        if self._camera_restart_pending:
            self._camera_restart_pending = False
            self._start_camera()
            return
        self.connect_button.setEnabled(True)
        self.connect_button.setText("连接")
        self.freeze_button.setEnabled(False)
        self.freeze_button.setChecked(False)
        self.source_status.setText("未连接")

    def set_frozen(self, frozen: bool) -> None:
        self.frozen = frozen
        self.freeze_button.setText("恢复实时" if frozen else "冻结画面")
        if frozen and self.last_live_frame is not None:
            self.canvas.set_frame(self.last_live_frame)
        if self.camera.running:
            state = "画面已冻结，可进行测量" if frozen else "实时画面"
            self.status_message.setText(state)

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开测量图像",
            str(self.current_path.parent if self.current_path else Path.home()),
            "图像 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;所有文件 (*)",
        )
        if not path:
            return
        data = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if data is None:
            QMessageBox.critical(self, "打开失败", "无法解码该图像文件。")
            return
        if self.camera.running:
            self.camera.stop()
        self.current_path = Path(path)
        self._clear_candidate()
        self._redo_stack.clear()
        self.canvas.set_frame(data, reset_view=True)
        self.canvas.measurements.clear()
        self._refresh_results()
        self.source_status.setText(f"图片：{self.current_path.name}\n{data.shape[1]} × {data.shape[0]}")
        self._apply_saved_calibration_if_compatible()
        self._set_tool(Tool.SELECT)

    def auto_calibrate(self) -> None:
        if not self._ensure_image() or self.canvas.frame is None:
            return
        if self.camera.running and not self.frozen:
            self.freeze_button.setChecked(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.status_message.setText("正在扫描整幅画面中的公制标尺…")
        QApplication.processEvents()
        success_message: str | None = None
        try:
            calibration = detect_metric_ruler(self.canvas.frame)
        except ValueError as error:
            QMessageBox.warning(
                self,
                "自动标定未完成",
                f"{error}\n\n请点击“两点划线”，在右侧输入线段的实际毫米长度，再点击线段两端。",
            )
            self.status_message.setText("自动标定失败，比例未更改")
        else:
            self._set_calibration(calibration)
            self.status_message.setText(
                f"已识别 {calibration.reference_mm:g} mm 标尺：1 px = {calibration.mm_per_pixel:.6f} mm"
            )
            micron_text = (
                f"\n并使用 {calibration.micron_circle_count} 个微米圆进行联合校验。"
                if calibration.micron_circle_count
                else ""
            )
            success_message = (
                f"已识别 {calibration.reference_mm:g} mm 标尺，标定成功。\n"
                f"{calibration.pixels_per_mm:.3f} px/mm\n"
                f"{calibration.mm_per_pixel:.6f} mm/px"
                f"{micron_text}"
            )
        finally:
            QApplication.restoreOverrideCursor()
        if success_message is not None:
            QMessageBox.information(self, "标定成功", success_message)

    def _set_calibration(self, calibration: Calibration) -> None:
        self.canvas.calibration = calibration
        self.canvas.show_calibration_overlay = False
        self.calibration_overlay_button.blockSignals(True)
        self.calibration_overlay_button.setChecked(False)
        self.calibration_overlay_button.setText("显示标定辅助线")
        self.calibration_overlay_button.setEnabled(True)
        self.calibration_overlay_button.blockSignals(False)
        self._save_calibration(calibration)
        self._update_calibration_panel()
        self.canvas.update()

    def clear_calibration(self) -> None:
        self.canvas.calibration = None
        self._restored_calibration = None
        self.settings.remove("calibration")
        self.canvas.show_calibration_overlay = False
        self.calibration_overlay_button.setChecked(False)
        self.calibration_overlay_button.setEnabled(False)
        self._update_calibration_panel()
        self.canvas.update()

    def _toggle_calibration_overlay(self, visible: bool) -> None:
        self.canvas.show_calibration_overlay = visible and self.canvas.calibration is not None
        self.calibration_overlay_button.setText("隐藏标定辅助线" if visible else "显示标定辅助线")
        if self.canvas.show_calibration_overlay and self.canvas.tool != Tool.SELECT:
            self._set_tool(Tool.SELECT)
        if self.canvas.show_calibration_overlay:
            self.status_message.setText("标定辅助线已显示；拖动黄色端点可调整标定长度")
        self.canvas.update()

    def _canvas_clicked(self, x: float, y: float) -> None:
        point = (x, y)
        tool = self.canvas.tool
        if tool == Tool.ORIGIN:
            try:
                snapped = snap_point_near(self.canvas.frame, point) if self.canvas.frame is not None else None
            except ValueError:
                snapped = None
            self.canvas.origin = snapped.point if snapped is not None else point
            self.canvas.update()
            ox, oy = self.canvas.origin
            self.status_message.setText(f"坐标原点设为 ({ox:.1f}, {oy:.1f}) px")
            self._set_tool(Tool.SELECT)
            return
        if tool == Tool.CIRCLE:
            self._detect_circle(point)
            return
        if tool == Tool.CONCENTRICITY:
            self._select_circle_for_composite_measurement(point, tool)
            return
        if tool == Tool.CIRCLE_ARRAY:
            self._select_circle_for_composite_measurement(point, tool)
            return
        if self._candidate_measurement is not None and tool not in {Tool.POLYLINE, Tool.AREA}:
            self.status_message.setText("请先按 Enter、双击或右键结束当前多点测量，或按 Esc 取消")
            return
        if tool == Tool.ANGLE:
            self._create_angle_candidates(point)
            return
        if tool == Tool.MANUAL_CALIBRATION:
            self.canvas.pending_points.append(point)
            if len(self.canvas.pending_points) < 2:
                self.canvas.update()
                return
            points = list(self.canvas.pending_points)
            self.canvas.pending_points.clear()
            pixel_length = distance(points[0], points[1])
            reference_mm = self.manual_calibration_length.value()
            if pixel_length < 20:
                QMessageBox.warning(self, "标定点无效", "两个标定点距离过近，请重新划线。")
            else:
                width, height = self.canvas.image_size or (0, 0)
                self._set_calibration(
                    Calibration(
                        mm_per_pixel=reference_mm / pixel_length,
                        start=points[0],
                        end=points[1],
                        method=f"手动两点 {reference_mm:g} mm 标定",
                        reference_mm=reference_mm,
                        image_size=(width, height),
                    )
                )
                self.status_message.setText(f"手动 {reference_mm:g} mm 标定完成")
            self._set_tool(Tool.SELECT)
            return
        if self.canvas.frame is None:
            return
        if tool in {
            Tool.LENGTH,
            Tool.POINT,
            Tool.POINT_LINE_DISTANCE,
            Tool.POLYLINE,
            Tool.AREA,
            Tool.ARC,
            Tool.THREE_POINT_CIRCLE,
        }:
            if self.canvas.calibration is None:
                self._need_calibration_message()
                return
            try:
                snap_radius = (
                    self.point_line_snap_radius.value()
                    if tool == Tool.POINT_LINE_DISTANCE
                    else self.snap_radius.value()
                )
                snapped = snap_point_near(self.canvas.frame, point, search_radius=snap_radius)
            except ValueError as error:
                self.status_message.setText(f"自动吸附失败：{error}")
                return
            if tool == Tool.POINT:
                self._create_point_measurement(snapped.point, snapped.confidence, snapped.method)
                return
            if tool == Tool.POINT_LINE_DISTANCE:
                if not self.canvas.pending_points:
                    self.canvas.pending_points.append(snapped.point)
                    self.status_message.setText("目标点已自动吸附；现在点击直线边缘附近")
                    self.canvas.update()
                    return
                source = self.canvas.pending_points[0]
                try:
                    line = detect_line_near(self.canvas.frame, point, search_radius=self.line_search_radius.value())
                except ValueError as error:
                    self.status_message.setText(f"直线识别失败：{error}")
                    return
                foot = project_point_to_line(source, line)
                value = distance(source, foot) * self.canvas.calibration.mm_per_pixel
                self.canvas.pending_points.clear()
                self._add_measurement(
                    Measurement(
                        MeasurementKind.POINT_LINE_DISTANCE,
                        [source, foot, line.start, line.end],
                        value,
                        "mm",
                        label=f"点线距 {value:.3f} mm",
                        metadata={"confidence": line.confidence, "method": "自动点吸附 + 局部直线拟合"},
                    )
                )
                self.status_message.setText(f"点到直线距离：{value:.3f} mm；可继续测量，Esc 后可拖动控制点")
                return

            if (
                tool in {Tool.POLYLINE, Tool.AREA}
                and self.canvas.pending_points
                and distance(self.canvas.pending_points[-1], snapped.point) <= 3.0
            ):
                self.finish_variable_measurement()
                return
            self.canvas.pending_points.append(snapped.point)
            if tool == Tool.LENGTH and len(self.canvas.pending_points) >= 2:
                points = list(self.canvas.pending_points[:2])
                self.canvas.pending_points.clear()
                value = distance(points[0], points[1]) * self.canvas.calibration.mm_per_pixel
                self._add_measurement(
                    Measurement(
                        MeasurementKind.LENGTH,
                        points,
                        value,
                        "mm",
                        label=f"{value:.3f} mm",
                        metadata={"confidence": snapped.confidence, "method": "两端自动边缘/角点吸附"},
                    )
                )
                self.status_message.setText(f"长度：{value:.3f} mm；可继续测量，Esc 后可拖动两端控制点")
                return
            if tool in {Tool.POLYLINE, Tool.AREA}:
                self._update_multi_point_candidate(tool)
                minimum = 2 if tool == Tool.POLYLINE else 3
                self.status_message.setText(
                    f"已自动吸附 {len(self.canvas.pending_points)} 个点；"
                    + ("可双击、右键或按 Enter 结束，也可继续添加点" if len(self.canvas.pending_points) >= minimum else f"至少还需 {minimum - len(self.canvas.pending_points)} 个点")
                )
                self.canvas.update()
                return
            if tool == Tool.ARC and len(self.canvas.pending_points) >= 3:
                points = list(self.canvas.pending_points[:3])
                self.canvas.pending_points.clear()
                try:
                    measurement = self._make_arc_measurement(points)
                except ValueError as error:
                    self.status_message.setText(str(error))
                    return
                self._add_measurement(measurement)
                self.status_message.setText("圆弧测量完成；可继续测量，Esc 后可拖动三个弧点")
                return
            if tool == Tool.THREE_POINT_CIRCLE and len(self.canvas.pending_points) >= 3:
                points = list(self.canvas.pending_points[:3])
                self.canvas.pending_points.clear()
                try:
                    measurement = self._make_three_point_circle_measurement(points)
                except ValueError as error:
                    self.status_message.setText(str(error))
                    return
                self._add_measurement(measurement)
                self.status_message.setText("三点圆测量完成；可继续测量，Esc 后可拖动三个圆周点")
                return
            if tool == Tool.ARC:
                self.status_message.setText(f"已自动吸附 {len(self.canvas.pending_points)}/3 个弧点")
            elif tool == Tool.THREE_POINT_CIRCLE:
                self.status_message.setText(f"已自动吸附 {len(self.canvas.pending_points)}/3 个圆周点")
            else:
                self.status_message.setText("第一个端点已自动吸附；请粗略点击另一个端点")
            self.canvas.update()

    def _create_point_measurement(self, point: tuple[float, float], confidence: float, method: str) -> None:
        if self.canvas.calibration is None:
            return
        ox, oy = self.canvas.origin
        x_mm = (point[0] - ox) * self.canvas.calibration.mm_per_pixel
        y_mm = (point[1] - oy) * self.canvas.calibration.mm_per_pixel
        self._add_measurement(
            Measurement(
                MeasurementKind.POINT,
                [point],
                0.0,
                "mm",
                label=f"({x_mm:.3f}, {y_mm:.3f}) mm",
                metadata={
                    "center_x_mm": x_mm,
                    "center_y_mm": y_mm,
                    "confidence": confidence,
                    "method": method,
                },
            )
        )
        self.status_message.setText("点坐标已自动吸附并测量；可继续测量，Esc 后可拖动")

    def _create_angle_candidates(self, point: tuple[float, float]) -> None:
        if self.canvas.frame is None:
            return
        try:
            self._angle_candidates = detect_angle_candidates_near(
                self.canvas.frame,
                point,
                search_radius=self.angle_search_radius.value(),
            )
        except ValueError as error:
            self._clear_candidate()
            self.status_message.setText(f"自动角度识别失败：{error}")
            return
        detected = self._angle_candidates[0]
        points = list(detected.points)
        value = angle_degrees(points[0], points[1], points[2])
        measurement = Measurement(
                MeasurementKind.ANGLE,
                points,
                value,
                "deg",
                label=f"{value:.2f} deg",
                metadata={"confidence": detected.confidence, "method": detected.method},
        )
        self._angle_candidates.clear()
        self._add_measurement(measurement)
        self.status_message.setText(f"自动角度：{value:.2f} deg；可继续测量，Esc 后可拖动三点调整")

    def _update_multi_point_candidate(self, tool: Tool) -> None:
        if self.canvas.calibration is None:
            return
        points = list(self.canvas.pending_points)
        if tool == Tool.POLYLINE and len(points) >= 2:
            value = polyline_length(points) * self.canvas.calibration.mm_per_pixel
            candidate = Measurement(
                MeasurementKind.POLYLINE,
                points,
                value,
                "mm",
                label=f"总长 {value:.3f} mm",
                metadata={"method": "多点自动吸附"},
            )
            self._set_candidate(candidate, update_status=False)
        elif tool == Tool.AREA and len(points) >= 3:
            value = polygon_area(points) * self.canvas.calibration.mm_per_pixel**2
            candidate = Measurement(
                MeasurementKind.AREA,
                points,
                value,
                "mm^2",
                label=f"面积 {value:.3f} mm^2",
                metadata={"method": "多顶点自动吸附"},
            )
            self._set_candidate(candidate, update_status=False)

    def _set_candidate(self, measurement: Measurement, message: str = "候选已生成", update_status: bool = True) -> None:
        self._candidate_measurement = measurement
        self.canvas.candidate_measurement = measurement
        if update_status:
            self.status_message.setText(message)
        self.canvas.update()

    def _clear_candidate(self) -> None:
        self._candidate_measurement = None
        self.canvas.candidate_measurement = None
        self._angle_candidates.clear()

    def finish_variable_measurement(self) -> None:
        candidate = self._candidate_measurement
        if candidate is None:
            return
        self.canvas.pending_points.clear()
        self._clear_candidate()
        self._add_measurement(candidate)
        self.status_message.setText(f"测量完成：{candidate.label or candidate.display_value}；可继续测量，Esc 退出")

    def _make_arc_measurement(self, points: list[tuple[float, float]]) -> Measurement:
        if self.canvas.calibration is None:
            raise ValueError("请先完成比例标定。")
        center, radius_px, sweep_degrees, arc_length_px = arc_geometry(points[0], points[1], points[2])
        radius_mm = radius_px * self.canvas.calibration.mm_per_pixel
        arc_length_mm = arc_length_px * self.canvas.calibration.mm_per_pixel
        sweep_radians = math.radians(abs(sweep_degrees))
        ox, oy = self.canvas.origin
        return Measurement(
            MeasurementKind.ARC,
            points,
            arc_length_mm,
            "mm",
            label=(
                f"R {radius_mm:.3f} mm | {abs(sweep_degrees):.2f} deg "
                f"({sweep_radians:.3f} rad) | L {arc_length_mm:.3f} mm"
            ),
            metadata={
                "radius_px": radius_px,
                "radius_mm": radius_mm,
                "sweep_degrees": sweep_degrees,
                "sweep_radians": sweep_radians,
                "center_x_mm": (center[0] - ox) * self.canvas.calibration.mm_per_pixel,
                "center_y_mm": (center[1] - oy) * self.canvas.calibration.mm_per_pixel,
                "method": "三点圆弧",
            },
        )

    def _make_three_point_circle_measurement(self, points: list[tuple[float, float]]) -> Measurement:
        if self.canvas.calibration is None:
            raise ValueError("请先完成比例标定。")
        center, radius_px = circumcircle(points[0], points[1], points[2])
        diameter_mm = 2.0 * radius_px * self.canvas.calibration.mm_per_pixel
        ox, oy = self.canvas.origin
        return Measurement(
            MeasurementKind.THREE_POINT_CIRCLE,
            [center, *points],
            diameter_mm,
            "mm",
            label=f"三点圆 Ø {diameter_mm:.3f} mm",
            metadata={
                "radius_px": radius_px,
                "center_x_mm": (center[0] - ox) * self.canvas.calibration.mm_per_pixel,
                "center_y_mm": (center[1] - oy) * self.canvas.calibration.mm_per_pixel,
                "method": "三个自动吸附圆周点拟合",
            },
        )

    def set_origin_to_image_center(self) -> None:
        image_size = self.canvas.image_size
        if image_size is None:
            self._ensure_image()
            return
        self.canvas.origin = (image_size[0] / 2.0, image_size[1] / 2.0)
        self.canvas.update()
        self.status_message.setText(
            f"坐标原点已设为画面中心 ({self.canvas.origin[0]:.1f}, {self.canvas.origin[1]:.1f}) px"
        )

    def cancel_measurement(self) -> None:
        self._pending_circle_ids.clear()
        self.canvas.pending_points.clear()
        self._clear_candidate()
        self._set_tool(Tool.SELECT)
        self.status_message.setText("已退出测量，切换到选择工具")

    def _detect_circle(self, click: tuple[float, float]) -> None:
        if self.canvas.calibration is None:
            self._need_calibration_message()
            return
        if self.canvas.frame is None:
            return
        if self._circle_detection_busy:
            self.status_message.setText("圆形检测正在进行，请稍候")
            return
        existing = [
            CircleDetection(
                center=item.points[0],
                radius=float(item.metadata["radius_px"]),
                confidence=float(item.metadata.get("confidence", 1.0)),
                method=str(item.metadata.get("method", "已测圆")),
            )
            for item in self.canvas.measurements
            if item.kind == MeasurementKind.CIRCLE
        ]
        radius = self.circle_radius.value()
        request = CircleDetectionRequest(
            image=self.canvas.frame,
            click=click,
            search_radius=max(radius + 90, int(radius * 1.45)),
            target_radius=float(radius),
            excluded=existing,
        )
        task = CircleDetectionTask(request)
        task.signals.completed.connect(self._circle_detection_completed)
        task.signals.failed.connect(self._circle_detection_failed)
        self._circle_detection_busy = True
        self._active_detection_task = task
        self.circle_button.setEnabled(False)
        self.status_message.setText("正在后台检测红色圆环附近的圆…")
        self._detection_pool.start(task)

    def _circle_detection_completed(self, found: CircleDetection, click: tuple[float, float]) -> None:
        self._circle_detection_busy = False
        self._active_detection_task = None
        self.circle_button.setEnabled(True)
        if self.canvas.calibration is None or self.canvas.tool != Tool.CIRCLE:
            return
        diameter_mm = 2.0 * found.radius * self.canvas.calibration.mm_per_pixel
        ox, oy = self.canvas.origin
        center_x_mm = (found.center[0] - ox) * self.canvas.calibration.mm_per_pixel
        center_y_mm = (found.center[1] - oy) * self.canvas.calibration.mm_per_pixel
        measurement = Measurement(
            MeasurementKind.CIRCLE,
            [found.center],
            diameter_mm,
            "mm",
            label=f"Ø {diameter_mm:.3f} mm",
            metadata={
                "radius_px": found.radius,
                "center_x_mm": center_x_mm,
                "center_y_mm": center_y_mm,
                "confidence": found.confidence,
                "method": found.method,
            },
        )
        self._add_measurement(measurement)
        self.status_message.setText(
            f"圆形：直径 {diameter_mm:.3f} mm，圆心 ({center_x_mm:.3f}, {center_y_mm:.3f}) mm；"
            "可继续检测，Esc 后可拖动圆心或直径"
        )

    def _circle_detection_failed(self, message: str, click: tuple[float, float]) -> None:
        self._circle_detection_busy = False
        self._active_detection_task = None
        self.circle_button.setEnabled(True)
        self.status_message.setText(f"未识别圆形：{message}")

    def _circle_at_point(
        self,
        point: tuple[float, float],
        use_edge: bool,
        excluded_ids: set[str] | None = None,
    ) -> Measurement | None:
        excluded_ids = excluded_ids or set()
        circles = [
            item for item in self.canvas.measurements
            if item.kind == MeasurementKind.CIRCLE and item.id not in excluded_ids
        ]
        if not circles:
            return None
        image_rect = self.canvas._image_rect()
        image_size = self.canvas.image_size
        if image_size is None:
            return None
        tolerance = max(7.0, 14.0 * image_size[0] / max(image_rect.width(), 1.0))
        if use_edge:
            metric = lambda item: abs(distance(point, item.points[0]) - float(item.metadata["radius_px"]))
        else:
            metric = lambda item: distance(point, item.points[0])
        ranked = sorted(circles, key=metric)
        best = ranked[0]
        return best if metric(best) <= tolerance else None

    def _select_circle_for_composite_measurement(self, point: tuple[float, float], tool: Tool) -> None:
        if self.canvas.calibration is None:
            self._need_calibration_message()
            return
        use_edge = tool == Tool.CONCENTRICITY
        selected = self._circle_at_point(point, use_edge=use_edge, excluded_ids=set(self._pending_circle_ids))
        if selected is None:
            target = "彩色圆周" if use_edge else "圆心十字"
            QMessageBox.information(
                self,
                "请选择已检测圆",
                f"请点击一个已检测圆的{target}；如尚未识别，请先使用“圆形”工具。",
            )
            return
        if selected.id in self._pending_circle_ids:
            self.status_message.setText("该圆已经选择，请选择另一个圆")
            return
        self._pending_circle_ids.append(selected.id)
        self.canvas.pending_points.append(selected.points[0])
        required = 2 if tool == Tool.CONCENTRICITY else 3
        if len(self._pending_circle_ids) < required:
            remaining = required - len(self._pending_circle_ids)
            self.status_message.setText(f"已选择 {len(self._pending_circle_ids)} 个圆，还需选择 {remaining} 个")
            self.canvas.update()
            return
        selected_circles = [
            next(item for item in self.canvas.measurements if item.id == measurement_id)
            for measurement_id in self._pending_circle_ids
        ]
        self._pending_circle_ids.clear()
        self.canvas.pending_points.clear()
        if tool == Tool.CONCENTRICITY:
            first, second = selected_circles
            value = distance(first.points[0], second.points[0]) * self.canvas.calibration.mm_per_pixel
            measurement = Measurement(
                MeasurementKind.CONCENTRICITY,
                [first.points[0], second.points[0]],
                value,
                "mm",
                label=f"同心度 {value:.3f} mm",
                metadata={"circle_1": first.id, "circle_2": second.id},
            )
            self._add_measurement(measurement)
            self.status_message.setText(f"两个圆的圆心距离（同心度）为 {value:.3f} mm；可继续测量，Esc 退出")
            return

        centers = [circle.points[0] for circle in selected_circles]
        try:
            center, radius = circumcircle(centers[0], centers[1], centers[2])
        except ValueError as error:
            QMessageBox.warning(self, "无法测量圆形阵列", str(error))
            return
        diameter_mm = 2.0 * radius * self.canvas.calibration.mm_per_pixel
        ox, oy = self.canvas.origin
        measurement = Measurement(
            MeasurementKind.CIRCLE_ARRAY,
            [center, *centers],
            diameter_mm,
            "mm",
            label=f"阵列 Ø {diameter_mm:.3f} mm",
            metadata={
                "radius_px": radius,
                "center_x_mm": (center[0] - ox) * self.canvas.calibration.mm_per_pixel,
                "center_y_mm": (center[1] - oy) * self.canvas.calibration.mm_per_pixel,
                "circle_1": selected_circles[0].id,
                "circle_2": selected_circles[1].id,
                "circle_3": selected_circles[2].id,
            },
        )
        self._add_measurement(measurement)
        self.status_message.setText(f"三圆心阵列外接圆直径为 {diameter_mm:.3f} mm；可继续测量，Esc 退出")

    def _need_calibration_message(self) -> None:
        self.canvas.pending_points.clear()
        QMessageBox.information(self, "需要标定", "请先自动识别画面标尺，或用“两点划线 + 实际长度”建立比例。")

    def _add_measurement(self, measurement: Measurement) -> None:
        self.canvas.measurements.append(measurement)
        self._redo_stack.clear()
        self.canvas.selected_id = measurement.id
        self.canvas.selected_ids = {measurement.id}
        self._refresh_results(measurement.id)
        self.canvas.update()

    def _refresh_results(self, selected_id: str | None = None) -> None:
        self.results.blockSignals(True)
        self.results.setRowCount(0)
        kind_names = {
            MeasurementKind.LENGTH: "长度",
            MeasurementKind.ANGLE: "角度",
            MeasurementKind.POINT: "点坐标",
            MeasurementKind.POINT_LINE_DISTANCE: "点线距离",
            MeasurementKind.POLYLINE: "折线总长",
            MeasurementKind.AREA: "面积",
            MeasurementKind.ARC: "圆弧",
            MeasurementKind.THREE_POINT_CIRCLE: "三点圆",
            MeasurementKind.CIRCLE: "圆形",
            MeasurementKind.CONCENTRICITY: "同心度",
            MeasurementKind.CIRCLE_ARRAY: "圆形阵列",
        }
        selected_row = -1
        for row, item in enumerate(self.canvas.measurements):
            self.results.insertRow(row)
            kind_cell = QTableWidgetItem(kind_names[item.kind])
            kind_cell.setData(Qt.ItemDataRole.UserRole, item.id)
            value = (
                f"Ø {item.value:.3f} mm"
                if item.kind in {
                    MeasurementKind.CIRCLE,
                    MeasurementKind.THREE_POINT_CIRCLE,
                    MeasurementKind.CIRCLE_ARRAY,
                }
                else (f"L {item.value:.3f} mm" if item.kind == MeasurementKind.ARC else item.display_value)
            )
            if item.kind == MeasurementKind.ARC:
                point_text = (
                    f"R {float(item.metadata['radius_mm']):.3f} · "
                    f"{abs(float(item.metadata['sweep_degrees'])):.2f} deg · "
                    f"{float(item.metadata['sweep_radians']):.3f} rad"
                )
            elif item.kind in {
                MeasurementKind.CIRCLE,
                MeasurementKind.THREE_POINT_CIRCLE,
                MeasurementKind.CIRCLE_ARRAY,
                MeasurementKind.POINT,
            }:
                point_text = f"{float(item.metadata['center_x_mm']):.2f}, {float(item.metadata['center_y_mm']):.2f} mm"
            elif item.kind == MeasurementKind.ANGLE:
                point_text = f"{item.points[1][0]:.0f}, {item.points[1][1]:.0f} px"
            else:
                point_text = "—"
            self.results.setItem(row, 0, kind_cell)
            self.results.setItem(row, 1, QTableWidgetItem(value))
            self.results.setItem(row, 2, QTableWidgetItem(point_text))
            if item.id == selected_id:
                selected_row = row
        if selected_row >= 0:
            self.results.selectRow(selected_row)
        self.results.blockSignals(False)
        self.result_count.setText(f"{len(self.canvas.measurements)} 项")

    def _table_selection_changed(self) -> None:
        selected_ids = {
            str(self.results.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
            for index in self.results.selectionModel().selectedRows()
            if self.results.item(index.row(), 0) is not None
        }
        self.canvas.selected_ids = selected_ids
        self.canvas.selected_id = next(iter(selected_ids)) if len(selected_ids) == 1 else None
        selected_circles = [
            item for item in self.canvas.measurements if item.id in selected_ids and item.kind == MeasurementKind.CIRCLE
        ]
        self.draw_array_button.setEnabled(len(selected_ids) == 3 and len(selected_circles) == 3)
        self.canvas.update()

    def _canvas_selection_changed(self, measurement: Measurement | None) -> None:
        self.canvas.selected_ids = {measurement.id} if measurement else set()
        self._refresh_results(measurement.id if measurement else None)

    def _measurement_geometry_changed(self, measurement: Measurement) -> None:
        self._recalculate_measurement(measurement)
        self.canvas.selected_id = measurement.id
        self.canvas.selected_ids = {measurement.id}
        self.canvas.update()

    def _measurement_geometry_edited(self, measurement: Measurement) -> None:
        self._recalculate_measurement(measurement)
        self._recalculate_dependents(measurement.id)
        self._refresh_results(measurement.id)
        self.canvas.selected_id = measurement.id
        self.canvas.selected_ids = {measurement.id}
        self.canvas.update()
        self.status_message.setText(f"测量已调整：{measurement.label or measurement.display_value}")

    def _calibration_geometry_edited(self, calibration: Calibration) -> None:
        calibration.mm_per_pixel = calibration.reference_mm / calibration.pixel_length
        calibration.method = f"{calibration.reference_mm:g} mm 标尺（端点调整）"
        calibration.micron_circle_count = 0
        calibration.micron_scale_mm_per_pixel = None
        calibration.micron_circles.clear()
        self._save_calibration(calibration)
        self._update_calibration_panel()
        for measurement in self.canvas.measurements:
            self._recalculate_measurement(measurement)
        for measurement in self.canvas.measurements:
            if measurement.kind == MeasurementKind.CIRCLE:
                self._recalculate_dependents(measurement.id)
        self._refresh_results(self.canvas.selected_id)
        self.canvas.update()
        self.status_message.setText(
            f"标定已调整：{calibration.reference_mm:g} mm = {calibration.pixel_length:.1f} px"
        )

    def _measurement_handle_released(self, measurement: Measurement, point_index: int, snap_requested: bool) -> None:
        if not snap_requested or self.canvas.frame is None or not 0 <= point_index < len(measurement.points):
            return
        try:
            snapped = snap_point_near(self.canvas.frame, measurement.points[point_index], search_radius=28)
        except ValueError:
            return
        measurement.points[point_index] = snapped.point

    def _recalculate_measurement(self, item: Measurement) -> None:
        calibration = self.canvas.calibration
        scale = calibration.mm_per_pixel if calibration is not None else 1.0
        ox, oy = self.canvas.origin
        if item.kind == MeasurementKind.LENGTH:
            item.value = distance(item.points[0], item.points[1]) * scale
            item.label = f"{item.value:.3f} mm"
        elif item.kind == MeasurementKind.ANGLE:
            item.value = angle_degrees(item.points[0], item.points[1], item.points[2])
            item.label = f"{item.value:.2f} deg"
        elif item.kind == MeasurementKind.POINT:
            x_mm = (item.points[0][0] - ox) * scale
            y_mm = (item.points[0][1] - oy) * scale
            item.metadata["center_x_mm"] = x_mm
            item.metadata["center_y_mm"] = y_mm
            item.label = f"({x_mm:.3f}, {y_mm:.3f}) mm"
        elif item.kind == MeasurementKind.POINT_LINE_DISTANCE and len(item.points) >= 4:
            line = LineDetection(item.points[2], item.points[3], 1.0, "手动调整")
            item.points[1] = project_point_to_line(item.points[0], line)
            item.value = distance(item.points[0], item.points[1]) * scale
            item.label = f"点线距 {item.value:.3f} mm"
        elif item.kind == MeasurementKind.POLYLINE:
            item.value = polyline_length(item.points) * scale
            item.label = f"总长 {item.value:.3f} mm"
        elif item.kind == MeasurementKind.AREA:
            item.value = polygon_area(item.points) * scale**2
            item.label = f"面积 {item.value:.3f} mm^2"
        elif item.kind == MeasurementKind.ARC:
            try:
                updated = self._make_arc_measurement(list(item.points))
            except ValueError:
                item.value = 0.0
                item.label = "无效圆弧：三点接近共线"
            else:
                item.value = updated.value
                item.label = updated.label
                item.metadata.update(updated.metadata)
        elif item.kind == MeasurementKind.THREE_POINT_CIRCLE and len(item.points) >= 4:
            try:
                updated = self._make_three_point_circle_measurement(list(item.points[1:4]))
            except ValueError:
                item.value = 0.0
                item.label = "无效三点圆：三个点接近共线"
            else:
                item.points[0] = updated.points[0]
                item.value = updated.value
                item.label = updated.label
                item.metadata.update(updated.metadata)
        elif item.kind in {MeasurementKind.CIRCLE, MeasurementKind.CIRCLE_ARRAY}:
            radius = float(item.metadata.get("radius_px", 0.0))
            item.value = 2.0 * radius * scale
            prefix = "阵列 Ø" if item.kind == MeasurementKind.CIRCLE_ARRAY else "Ø"
            item.label = f"{prefix} {item.value:.3f} mm"
            item.metadata["center_x_mm"] = (item.points[0][0] - ox) * scale
            item.metadata["center_y_mm"] = (item.points[0][1] - oy) * scale
        elif item.kind == MeasurementKind.CONCENTRICITY and len(item.points) >= 2:
            item.value = distance(item.points[0], item.points[1]) * scale
            item.label = f"同心度 {item.value:.3f} mm"

    def _recalculate_dependents(self, changed_id: str) -> None:
        circles = {item.id: item for item in self.canvas.measurements if item.kind == MeasurementKind.CIRCLE}
        scale = self.canvas.calibration.mm_per_pixel if self.canvas.calibration is not None else 1.0
        ox, oy = self.canvas.origin
        for item in self.canvas.measurements:
            referenced = {str(value) for key, value in item.metadata.items() if key.startswith("circle_")}
            if changed_id not in referenced:
                continue
            if item.kind == MeasurementKind.CONCENTRICITY:
                first = circles.get(str(item.metadata.get("circle_1", "")))
                second = circles.get(str(item.metadata.get("circle_2", "")))
                if first is None or second is None:
                    continue
                item.points = [first.points[0], second.points[0]]
                item.value = distance(item.points[0], item.points[1]) * scale
                item.label = f"同心度 {item.value:.3f} mm"
            elif item.kind == MeasurementKind.CIRCLE_ARRAY:
                selected = [circles.get(str(item.metadata.get(f"circle_{index}", ""))) for index in range(1, 4)]
                if any(circle is None for circle in selected):
                    continue
                centers = [circle.points[0] for circle in selected if circle is not None]
                try:
                    center, radius = circumcircle(*centers)
                except ValueError:
                    continue
                item.points = [center, *centers]
                item.metadata["radius_px"] = radius
                item.metadata["center_x_mm"] = (center[0] - ox) * scale
                item.metadata["center_y_mm"] = (center[1] - oy) * scale
                item.value = 2.0 * radius * scale
                item.label = f"阵列 Ø {item.value:.3f} mm"

    def draw_array_from_selection(self) -> None:
        rows = sorted(index.row() for index in self.results.selectionModel().selectedRows())
        selected = []
        for row in rows:
            cell = self.results.item(row, 0)
            if cell is None:
                continue
            measurement_id = str(cell.data(Qt.ItemDataRole.UserRole))
            item = next((candidate for candidate in self.canvas.measurements if candidate.id == measurement_id), None)
            if item is not None and item.kind == MeasurementKind.CIRCLE:
                selected.append(item)
        if len(selected) != 3:
            QMessageBox.information(self, "请选择三个圆", "请在测量列表中按住 Ctrl 选择恰好三个“圆形”结果。")
            return
        self._create_circle_array(selected)

    def _create_circle_array(self, selected_circles: list[Measurement]) -> None:
        if self.canvas.calibration is None:
            self._need_calibration_message()
            return
        centers = [circle.points[0] for circle in selected_circles]
        try:
            center, radius = circumcircle(centers[0], centers[1], centers[2])
        except ValueError as error:
            QMessageBox.warning(self, "无法测量圆形阵列", str(error))
            return
        diameter_mm = 2.0 * radius * self.canvas.calibration.mm_per_pixel
        ox, oy = self.canvas.origin
        measurement = Measurement(
            MeasurementKind.CIRCLE_ARRAY,
            [center, *centers],
            diameter_mm,
            "mm",
            label=f"阵列 Ø {diameter_mm:.3f} mm",
            metadata={
                "radius_px": radius,
                "center_x_mm": (center[0] - ox) * self.canvas.calibration.mm_per_pixel,
                "center_y_mm": (center[1] - oy) * self.canvas.calibration.mm_per_pixel,
                "circle_1": selected_circles[0].id,
                "circle_2": selected_circles[1].id,
                "circle_3": selected_circles[2].id,
            },
        )
        self._add_measurement(measurement)
        if self.canvas.tool == Tool.CIRCLE_ARRAY:
            self.status_message.setText(f"三圆心阵列外接圆直径为 {diameter_mm:.3f} mm；可继续测量，Esc 退出")
        else:
            self.status_message.setText(f"三圆心阵列外接圆直径为 {diameter_mm:.3f} mm")

    def delete_selected(self) -> None:
        measurement_ids = set(self.canvas.selected_ids)
        for index in self.results.selectionModel().selectedRows():
            cell = self.results.item(index.row(), 0)
            if cell is not None:
                measurement_ids.add(str(cell.data(Qt.ItemDataRole.UserRole)))
        if not measurement_ids and self.canvas.selected_id is not None:
            measurement_ids.add(self.canvas.selected_id)
        if not measurement_ids:
            return
        dependent_ids = {
            item.id
            for item in self.canvas.measurements
            if any(
                key.startswith("circle_") and str(value) in measurement_ids
                for key, value in item.metadata.items()
            )
        }
        measurement_ids.update(dependent_ids)
        removed = [item for item in self.canvas.measurements if item.id in measurement_ids]
        self.canvas.measurements[:] = [item for item in self.canvas.measurements if item.id not in measurement_ids]
        if removed:
            self._redo_stack.extend(removed)
        self.canvas.selected_id = None
        self.canvas.selected_ids.clear()
        self._refresh_results()
        self.canvas.update()
        if dependent_ids:
            self.status_message.setText(f"已删除选中测量及 {len(dependent_ids)} 项关联结果")

    def clear_measurements(self) -> None:
        if not self.canvas.measurements:
            return
        if QMessageBox.question(self, "清空测量", "确定删除当前全部测量结果吗？") != QMessageBox.StandardButton.Yes:
            return
        self._redo_stack.extend(self.canvas.measurements)
        self.canvas.measurements.clear()
        self.canvas.selected_id = None
        self.canvas.selected_ids.clear()
        self.draw_array_button.setEnabled(False)
        self._refresh_results()
        self.canvas.update()

    def undo_measurement(self) -> None:
        if self._candidate_measurement is not None and self.canvas.tool in {Tool.POLYLINE, Tool.AREA}:
            if self.canvas.pending_points:
                self.canvas.pending_points.pop()
            self._clear_candidate()
            self._update_multi_point_candidate(self.canvas.tool)
        elif self._candidate_measurement is not None:
            self._clear_candidate()
        elif self.canvas.pending_points:
            self.canvas.pending_points.pop()
            if self._pending_circle_ids:
                self._pending_circle_ids.pop()
            if self.canvas.tool in {Tool.POLYLINE, Tool.AREA}:
                self._update_multi_point_candidate(self.canvas.tool)
        elif self.canvas.measurements:
            self._redo_stack.append(self.canvas.measurements.pop())
            self._refresh_results()
        self.canvas.update()

    def redo_measurement(self) -> None:
        if not self._redo_stack:
            return
        measurement = self._redo_stack.pop()
        self.canvas.measurements.append(measurement)
        self.canvas.selected_id = measurement.id
        self.canvas.selected_ids = {measurement.id}
        self._refresh_results(measurement.id)
        self.canvas.update()

    def _selected_measurements(self) -> list[Measurement]:
        selected_ids = set(self.canvas.selected_ids)
        if self.canvas.selected_id:
            selected_ids.add(self.canvas.selected_id)
        for index in self.results.selectionModel().selectedRows():
            cell = self.results.item(index.row(), 0)
            if cell is not None:
                selected_ids.add(str(cell.data(Qt.ItemDataRole.UserRole)))
        return [item for item in self.canvas.measurements if item.id in selected_ids]

    def copy_selected(self) -> None:
        selected = self._selected_measurements()
        if not selected:
            return
        payload = [asdict(item) for item in selected]
        text = json.dumps(payload, ensure_ascii=False)
        QApplication.clipboard().setText(text)
        self._copied_measurements = payload
        self._clipboard_offset_count = 0
        self.status_message.setText(f"已复制 {len(selected)} 项测量")

    def paste_measurements(self) -> None:
        raw = QApplication.clipboard().text()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            payload = self._copied_measurements
        if not isinstance(payload, list) or not payload:
            return
        self._clipboard_offset_count += 1
        offset = 12.0 * self._clipboard_offset_count
        pasted: list[Measurement] = []
        id_mapping: dict[str, str] = {}
        try:
            for item in payload:
                if not isinstance(item, dict):
                    continue
                points = [(float(point[0]) + offset, float(point[1]) + offset) for point in item["points"]]
                measurement = Measurement(
                    kind=MeasurementKind(str(item["kind"])),
                    points=points,
                    value=float(item["value"]),
                    unit=str(item["unit"]),
                    label=str(item.get("label", "")),
                    metadata=dict(item.get("metadata", {})),
                )
                if item.get("id"):
                    id_mapping[str(item["id"])] = measurement.id
                if (
                    measurement.kind in {
                        MeasurementKind.POINT,
                        MeasurementKind.CIRCLE,
                        MeasurementKind.THREE_POINT_CIRCLE,
                        MeasurementKind.CIRCLE_ARRAY,
                        MeasurementKind.ARC,
                    }
                    and "center_x_mm" in measurement.metadata
                    and self.canvas.calibration is not None
                ):
                    measurement.metadata["center_x_mm"] = float(measurement.metadata["center_x_mm"]) + offset * self.canvas.calibration.mm_per_pixel
                    measurement.metadata["center_y_mm"] = float(measurement.metadata["center_y_mm"]) + offset * self.canvas.calibration.mm_per_pixel
                pasted.append(measurement)
            for measurement in pasted:
                for key, value in list(measurement.metadata.items()):
                    if key.startswith("circle_") and str(value) in id_mapping:
                        measurement.metadata[key] = id_mapping[str(value)]
        except (KeyError, TypeError, ValueError):
            self.status_message.setText("剪贴板中的测量数据格式无效")
            return
        if not pasted:
            return
        self.canvas.measurements.extend(pasted)
        self._redo_stack.clear()
        self.canvas.selected_ids = {item.id for item in pasted}
        self.canvas.selected_id = pasted[0].id if len(pasted) == 1 else None
        self._refresh_results()
        self._select_rows_by_ids(self.canvas.selected_ids)
        self.status_message.setText(f"已粘贴 {len(pasted)} 项测量")
        self.canvas.update()

    def select_all_measurements(self) -> None:
        if not self.canvas.measurements:
            return
        measurement_ids = {item.id for item in self.canvas.measurements}
        self.canvas.selected_ids = measurement_ids
        self.canvas.selected_id = None
        self._select_rows_by_ids(measurement_ids)
        self.canvas.update()

    def _select_rows_by_ids(self, measurement_ids: set[str]) -> None:
        from PySide6.QtCore import QItemSelectionModel

        self.results.clearSelection()
        for row in range(self.results.rowCount()):
            cell = self.results.item(row, 0)
            if cell is not None and str(cell.data(Qt.ItemDataRole.UserRole)) in measurement_ids:
                self.results.selectionModel().select(
                    self.results.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )

    def refresh_source(self) -> None:
        self.cancel_measurement()
        if self.current_path is not None:
            data = cv2.imdecode(np.fromfile(str(self.current_path), dtype=np.uint8), cv2.IMREAD_COLOR)
            if data is None:
                self.status_message.setText("刷新失败：原图片无法重新读取")
                return
            self.canvas.set_frame(data, reset_view=False)
            self.source_status.setText(f"图片：{self.current_path.name}\n{data.shape[1]} × {data.shape[0]}")
            self.status_message.setText("已从磁盘刷新图片，测量结果已保留")
        elif self.camera.running:
            if self.last_live_frame is not None:
                self.canvas.set_frame(self.last_live_frame, reset_view=False)
            self.status_message.setText("已刷新摄像头画面")
        else:
            self.canvas.update()
            self.status_message.setText("画面已重绘")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_measurement()
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.finish_variable_measurement()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if event.type() == QEvent.Type.KeyPress and self.isActiveWindow():
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_measurement()
                return True
            if self._candidate_measurement is not None and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                self.finish_variable_measurement()
                return True
        return super().eventFilter(watched, event)

    def export_csv(self) -> None:
        if not self.canvas.measurements:
            QMessageBox.information(self, "没有结果", "当前没有可导出的测量结果。")
            return
        suggested = (self.current_path.stem if self.current_path else "measurement") + "_results.csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出测量结果", suggested, "CSV 文件 (*.csv)")
        if not path:
            return
        self._write_csv(Path(path))
        self.status_message.setText(f"已导出：{path}")

    def _write_csv(self, path: Path) -> None:
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["编号", "类型", "数值", "单位", "圆心X(mm)", "圆心Y(mm)", "图像坐标点(px)", "检测方法", "置信度"])
            for index, item in enumerate(self.canvas.measurements, 1):
                writer.writerow(
                    [
                        index,
                        item.kind.value,
                        f"{item.value:.6f}",
                        item.unit,
                        item.metadata.get("center_x_mm", ""),
                        item.metadata.get("center_y_mm", ""),
                        json.dumps(item.points, ensure_ascii=False),
                        item.metadata.get("method", ""),
                        item.metadata.get("confidence", ""),
                    ]
                )

    def export_results(self) -> None:
        if not self._ensure_image():
            return
        if not self.canvas.measurements:
            QMessageBox.information(self, "没有结果", "当前没有可导出的测量结果。")
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suggested = f"me_{timestamp}_{self._export_sequence:03d}"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出截图和测量结果",
            suggested,
            "测量结果 (*.png *.csv)",
        )
        if not path:
            return
        base_path = Path(path)
        if base_path.suffix.lower() in {".png", ".csv"}:
            base_path = base_path.with_suffix("")
        png_path = base_path.with_suffix(".png")
        csv_path = base_path.with_suffix(".csv")
        pixmap: QPixmap = self.canvas.grab()
        if not pixmap.save(str(png_path), "PNG"):
            QMessageBox.critical(self, "保存失败", f"无法写入截图文件：\n{png_path}")
            return
        try:
            self._write_csv(csv_path)
        except OSError as error:
            QMessageBox.critical(self, "保存失败", f"截图已保存，但无法写入 CSV：\n{error}")
            return
        self._export_sequence += 1
        self.status_message.setText(f"已导出同名文件：{png_path.name} 和 {csv_path.name}")

    def save_snapshot(self) -> None:
        if not self._ensure_image():
            return
        suggested = (self.current_path.stem if self.current_path else "measurement") + "_annotated.png"
        path, _ = QFileDialog.getSaveFileName(self, "保存带标注截图", suggested, "PNG 图像 (*.png);;JPEG 图像 (*.jpg)")
        if not path:
            return
        pixmap: QPixmap = self.canvas.grab()
        if not pixmap.save(path):
            QMessageBox.critical(self, "保存失败", "无法写入截图文件。")
            return
        self.status_message.setText(f"截图已保存：{path}")

    def canvas_fit(self) -> None:
        self.canvas.fit_to_window()

    def canvas_actual(self) -> None:
        self.canvas.actual_pixels()

    def _cursor_moved(self, point) -> None:  # type: ignore[no-untyped-def]
        if point is None:
            self.cursor_position.setText("X: -, Y: -")
            return
        x, y = point
        if self.canvas.calibration:
            ox, oy = self.canvas.origin
            mm_x = (x - ox) * self.canvas.calibration.mm_per_pixel
            mm_y = (y - oy) * self.canvas.calibration.mm_per_pixel
            self.cursor_position.setText(f"X: {x:.1f} px / {mm_x:.3f} mm   Y: {y:.1f} px / {mm_y:.3f} mm")
        else:
            self.cursor_position.setText(f"X: {x:.1f} px   Y: {y:.1f} px")

    def _zoom_changed(self, zoom: float) -> None:
        self.zoom_status.setText(f"缩放 {zoom * 100:.0f}%")

    def _circle_radius_changed(self, radius: int) -> None:
        self.settings.setValue("circle/search_radius", radius)
        self.canvas.circle_search_radius = float(radius)
        self.canvas.update()

    def _snap_radius_changed(self, radius: int) -> None:
        self.settings.setValue("vision/snap_radius", radius)
        self.canvas.snap_search_radius = float(radius)
        self.canvas.update()

    def _angle_radius_changed(self, radius: int) -> None:
        self.settings.setValue("vision/angle_search_radius", radius)
        self.canvas.angle_search_radius = float(radius)
        self.canvas.update()

    def _point_line_snap_radius_changed(self, radius: int) -> None:
        self.settings.setValue("vision/point_line_snap_radius", radius)
        if self.canvas.tool == Tool.POINT_LINE_DISTANCE and not self.canvas.pending_points:
            self.canvas.snap_search_radius = float(radius)
            self.canvas.update()

    def _line_radius_changed(self, radius: int) -> None:
        self.settings.setValue("vision/line_search_radius", radius)
        self.canvas.line_search_radius = float(radius)
        self.canvas.update()

    def _tool_search_radius_from_wheel(self, radius: int) -> None:
        if self.canvas.tool == Tool.ANGLE:
            spinbox = self.angle_search_radius
        elif self.canvas.tool == Tool.POINT_LINE_DISTANCE and self.canvas.pending_points:
            spinbox = self.line_search_radius
        elif self.canvas.tool == Tool.POINT_LINE_DISTANCE:
            spinbox = self.point_line_snap_radius
        else:
            spinbox = self.snap_radius
        spinbox.setValue(radius)
        self.status_message.setText(f"当前搜索半径：{radius} px")

    def _circle_radius_from_wheel(self, radius: int) -> None:
        self.circle_radius.blockSignals(True)
        self.circle_radius.setValue(radius)
        self.circle_radius.blockSignals(False)
        self.settings.setValue("circle/search_radius", radius)
        self.status_message.setText(f"红色选择环半径：{radius} px")

    def _update_calibration_panel(self) -> None:
        calibration = self.canvas.calibration if hasattr(self, "canvas") else None
        if calibration is None:
            self.calibration_status.setText("尚未标定\n长度和直径测量暂不可用")
            self.calibration_status.setProperty("valid", False)
            self.calibration_overlay_button.setEnabled(False)
        else:
            method_text = (
                f"联合标定 · {calibration.micron_circle_count} 个微米圆"
                if calibration.micron_circle_count
                else calibration.method
            )
            self.calibration_status.setText(
                f"已标定 · {method_text}\n"
                f"{calibration.pixels_per_mm:.3f} px/mm  ·  {calibration.mm_per_pixel:.6f} mm/px\n"
                f"置信度 {calibration.confidence * 100:.0f}%"
            )
            self.calibration_status.setProperty("valid", True)
            self.calibration_overlay_button.setEnabled(True)
        self.calibration_status.style().unpolish(self.calibration_status)
        self.calibration_status.style().polish(self.calibration_status)

    def _save_calibration(self, calibration: Calibration) -> None:
        payload = {
            "mm_per_pixel": calibration.mm_per_pixel,
            "start": calibration.start,
            "end": calibration.end,
            "method": calibration.method,
            "confidence": calibration.confidence,
            "reference_mm": calibration.reference_mm,
            "image_size": calibration.image_size,
            "micron_circle_count": calibration.micron_circle_count,
            "micron_scale_mm_per_pixel": calibration.micron_scale_mm_per_pixel,
            "micron_circles": calibration.micron_circles,
        }
        self.settings.setValue("calibration", json.dumps(payload))
        self._restored_calibration = calibration

    def _load_calibration(self) -> Calibration | None:
        raw = self.settings.value("calibration", "")
        if not raw:
            return None
        try:
            payload = json.loads(str(raw))
            return Calibration(
                mm_per_pixel=float(payload["mm_per_pixel"]),
                start=tuple(payload["start"]),
                end=tuple(payload["end"]),
                method=str(payload["method"]),
                confidence=float(payload.get("confidence", 1.0)),
                reference_mm=float(payload.get("reference_mm", 40.0)),
                image_size=tuple(payload["image_size"]) if payload.get("image_size") else None,
                micron_circle_count=int(payload.get("micron_circle_count", 0)),
                micron_scale_mm_per_pixel=(
                    float(payload["micron_scale_mm_per_pixel"])
                    if payload.get("micron_scale_mm_per_pixel") is not None
                    else None
                ),
                micron_circles=[
                    (tuple(item[0]), float(item[1]), float(item[2]))
                    for item in payload.get("micron_circles", [])
                ],
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def _apply_saved_calibration_if_compatible(self) -> None:
        saved = self._restored_calibration
        if saved is None or saved.image_size is None or self.canvas.image_size != saved.image_size:
            self.canvas.calibration = None
        else:
            self.canvas.calibration = saved
        self.canvas.show_calibration_overlay = False
        self.calibration_overlay_button.blockSignals(True)
        self.calibration_overlay_button.setChecked(False)
        self.calibration_overlay_button.setText("显示标定辅助线")
        self.calibration_overlay_button.blockSignals(False)
        self._update_calibration_panel()
        self.canvas.update()

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("camera/index", int(self.camera_index.currentData()))
        width, height = self._selected_camera_resolution()
        self.settings.setValue("camera/resolution", f"{width}x{height}")
        self._camera_restart_pending = False
        self.camera.stop()
        self._detection_pool.waitForDone(1500)
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        event.accept()
