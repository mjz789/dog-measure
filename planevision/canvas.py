from __future__ import annotations

import math
import time

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from .models import Calibration, Measurement, MeasurementKind, Point, Tool, arc_geometry


ACCENT = QColor("#16a085")
ACCENT_LIGHT = QColor("#61d4bd")
ANGLE_COLOR = QColor("#f0a44b")
CIRCLE_COLOR = QColor("#4aa3df")
CONCENTRICITY_COLOR = QColor("#e56b75")
CENTER_DISTANCE_COLOR = QColor("#24c7b1")
ARRAY_COLOR = QColor("#b98adb")
POINT_COLOR = QColor("#ef7fa0")
POLYLINE_COLOR = QColor("#55c2a3")
AREA_COLOR = QColor("#5bc0de")
ARC_COLOR = QColor("#ff8c69")
SELECTED_COLOR = QColor("#ffd166")
PRESELECT_COLOR = QColor("#ef4444")
CANDIDATE_COLOR = QColor("#f5f7f8")
TEXT_BG = QColor(20, 24, 28, 215)


class ImageCanvas(QWidget):
    imageClicked = Signal(float, float)
    mouseImagePosition = Signal(object)
    zoomChanged = Signal(float)
    measurementSelected = Signal(object)
    circleRadiusChanged = Signal(int)
    toolSearchRadiusChanged = Signal(int)
    measurementChanging = Signal(object)
    measurementEdited = Signal(object)
    measurementHandleReleased = Signal(object, int, bool)
    calibrationEdited = Signal(object)
    finishMeasurementRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(420, 300)
        self._frame: np.ndarray | None = None
        self._qimage: QImage | None = None
        self.measurements: list[Measurement] = []
        self.candidate_measurement: Measurement | None = None
        self.calibration: Calibration | None = None
        self.origin: Point = (0.0, 0.0)
        self.tool = Tool.SELECT
        self.pending_points: list[Point] = []
        self.hover_point: Point | None = None
        self.selected_id: str | None = None
        self.selected_ids: set[str] = set()
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self._fit_scale = 1.0
        self._drag_last: QPointF | None = None
        self._panning = False
        self._space_pressed = False
        self._drag_measurement: Measurement | None = None
        self._drag_handle: tuple[str, int] | None = None
        self._drag_calibration_handle: int | None = None
        self._drag_snap_requested = False
        self._drag_label_measurement: Measurement | None = None
        self._drag_label_offset = QPointF()
        self._label_rects: dict[str, QRectF] = {}
        self.circle_search_radius: float | None = None
        self.snap_search_radius = 42.0
        self.line_search_radius = 150.0
        self.angle_search_radius = 170.0
        self.show_calibration_overlay = False
        self._last_hover_update = 0.0

    @property
    def frame(self) -> np.ndarray | None:
        return self._frame

    @property
    def has_image(self) -> bool:
        return self._frame is not None

    @property
    def image_size(self) -> tuple[int, int] | None:
        if self._frame is None:
            return None
        height, width = self._frame.shape[:2]
        return width, height

    def set_frame(
        self,
        frame: np.ndarray,
        reset_view: bool = False,
        display_frame: np.ndarray | None = None,
    ) -> None:
        self._frame = np.ascontiguousarray(frame)
        display = self._frame if display_frame is None else np.ascontiguousarray(display_frame)
        height, width, channels = display.shape
        self._qimage = QImage(
            display.data,
            width,
            height,
            channels * width,
            QImage.Format.Format_BGR888,
        ).copy()
        if reset_view:
            self.fit_to_window()
        self.update()

    def set_tool(self, tool: Tool) -> None:
        self.tool = tool
        self.pending_points.clear()
        self.candidate_measurement = None
        self.hover_point = None
        if tool != Tool.SELECT:
            self.selected_id = None
            self.selected_ids.clear()
        self.setCursor(Qt.CursorShape.ArrowCursor if tool == Tool.SELECT else Qt.CursorShape.CrossCursor)
        self.update()

    def fit_to_window(self) -> None:
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self.zoomChanged.emit(self.zoom)
        self.update()

    def actual_pixels(self) -> None:
        if self._qimage is None:
            return
        self._update_fit_scale()
        self.zoom = 1.0 / max(self._fit_scale, 1e-9)
        self.pan = QPointF(0.0, 0.0)
        self.zoomChanged.emit(self.zoom)
        self.update()

    def set_zoom(self, zoom: float) -> None:
        self.zoom = float(np.clip(zoom, 0.15, 16.0))
        self.zoomChanged.emit(self.zoom)
        self.update()

    def _update_fit_scale(self) -> None:
        if self._qimage is None:
            self._fit_scale = 1.0
            return
        margin = 14.0
        source_width, source_height = self.image_size or (self._qimage.width(), self._qimage.height())
        self._fit_scale = min(
            max(1.0, self.width() - margin * 2) / source_width,
            max(1.0, self.height() - margin * 2) / source_height,
        )

    def _image_rect(self) -> QRectF:
        if self._qimage is None:
            return QRectF()
        self._update_fit_scale()
        scale = self._fit_scale * self.zoom
        source_width, source_height = self.image_size or (self._qimage.width(), self._qimage.height())
        width = source_width * scale
        height = source_height * scale
        center = QPointF(self.width() / 2.0, self.height() / 2.0) + self.pan
        return QRectF(center.x() - width / 2.0, center.y() - height / 2.0, width, height)

    def image_to_widget(self, point: Point) -> QPointF:
        rect = self._image_rect()
        if self._qimage is None:
            return QPointF()
        return QPointF(
            rect.left() + point[0] / (self.image_size or (self._qimage.width(), 1))[0] * rect.width(),
            rect.top() + point[1] / (self.image_size or (1, self._qimage.height()))[1] * rect.height(),
        )

    def widget_to_image(self, point: QPointF) -> Point | None:
        rect = self._image_rect()
        if self._qimage is None or not rect.contains(point):
            return None
        return (
            (point.x() - rect.left()) / rect.width() * (self.image_size or (self._qimage.width(), 1))[0],
            (point.y() - rect.top()) / rect.height() * (self.image_size or (1, self._qimage.height()))[1],
        )

    def _pixel_radius_to_widget(self, radius: float) -> float:
        rect = self._image_rect()
        if self._qimage is None:
            return radius
        return radius * rect.width() / (self.image_size or (self._qimage.width(), 1))[0]

    def _draw_cross(
        self,
        painter: QPainter,
        point: QPointF,
        color: QColor,
        size: float = 7.0,
        width: float = 1.5,
    ) -> None:
        painter.setPen(QPen(color, width))
        painter.drawLine(QPointF(point.x() - size, point.y()), QPointF(point.x() + size, point.y()))
        painter.drawLine(QPointF(point.x(), point.y() - size), QPointF(point.x(), point.y() + size))

    def _draw_arrowhead(self, painter: QPainter, start: QPointF, end: QPointF, color: QColor) -> None:
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        size = 8.0
        polygon = QPolygonF(
            [
                end,
                QPointF(end.x() - size * math.cos(angle - 0.48), end.y() - size * math.sin(angle - 0.48)),
                QPointF(end.x() - size * math.cos(angle + 0.48), end.y() - size * math.sin(angle + 0.48)),
            ]
        )
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(polygon)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_label(self, painter: QPainter, position: QPointF, text: str, color: QColor) -> QRectF:
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        bounds = metrics.boundingRect(text).adjusted(-7, -4, 7, 4)
        bounds.moveCenter(position.toPoint())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(TEXT_BG)
        painter.drawRoundedRect(QRectF(bounds), 4, 4)
        painter.setPen(color)
        painter.drawText(QRectF(bounds), Qt.AlignmentFlag.AlignCenter, text)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        return QRectF(bounds)

    def _widget_to_image_unbounded(self, point: QPointF) -> Point:
        rect = self._image_rect()
        width, height = self.image_size or (1, 1)
        return (
            (point.x() - rect.left()) / max(rect.width(), 1e-9) * width,
            (point.y() - rect.top()) / max(rect.height(), 1e-9) * height,
        )

    def _label_at_widget_point(self, point: QPointF) -> Measurement | None:
        for item in reversed(self.measurements):
            bounds = self._label_rects.get(item.id)
            if bounds is not None and bounds.adjusted(-3, -3, 3, 3).contains(point):
                return item
        return None

    def _measurement_hit(self, image_point: Point) -> Measurement | None:
        if not self.measurements:
            return None
        rect = self._image_rect()
        if self._qimage is None:
            return None
        tolerance = 10.0 * (self.image_size or (self._qimage.width(), 1))[0] / max(rect.width(), 1.0)
        p = np.asarray(image_point)
        best: tuple[float, Measurement] | None = None
        for item in self.measurements:
            points = [np.asarray(point) for point in item.points]
            distances: list[float] = []
            if item.kind in {
                MeasurementKind.CIRCLE,
                MeasurementKind.THREE_POINT_CIRCLE,
                MeasurementKind.CIRCLE_ARRAY,
            }:
                radius = float(item.metadata.get("radius_px", 0.0))
                distances.append(abs(float(np.linalg.norm(p - points[0])) - radius))
                if item.kind == MeasurementKind.CIRCLE:
                    distances.append(float(np.linalg.norm(p - points[0])))
            elif item.kind == MeasurementKind.ARC and len(points) >= 3:
                try:
                    center, radius, _, _ = arc_geometry(item.points[0], item.points[1], item.points[2])
                    distances.append(abs(math.dist(image_point, center) - radius))
                except ValueError:
                    pass
            elif item.kind == MeasurementKind.POINT:
                distances.append(float(np.linalg.norm(p - points[0])))
            else:
                segments = list(zip(points[:-1], points[1:]))
                if item.kind == MeasurementKind.AREA and len(points) >= 3:
                    segments.append((points[-1], points[0]))
                for a, b in segments:
                    vector = b - a
                    t = float(np.clip(np.dot(p - a, vector) / (np.dot(vector, vector) + 1e-9), 0.0, 1.0))
                    distances.append(float(np.linalg.norm(p - (a + t * vector))))
            distance = min(distances, default=1e9)
            if distance <= tolerance and (best is None or distance < best[0]):
                best = (distance, item)
        return best[1] if best else None

    def _measurement_color(self, item: Measurement) -> QColor:
        colors = {
            MeasurementKind.LENGTH: ACCENT,
            MeasurementKind.ANGLE: ANGLE_COLOR,
            MeasurementKind.POINT: POINT_COLOR,
            MeasurementKind.POINT_LINE_DISTANCE: ANGLE_COLOR,
            MeasurementKind.POLYLINE: POLYLINE_COLOR,
            MeasurementKind.AREA: AREA_COLOR,
            MeasurementKind.ARC: ARC_COLOR,
            MeasurementKind.THREE_POINT_CIRCLE: CIRCLE_COLOR,
            MeasurementKind.CIRCLE: CIRCLE_COLOR,
            MeasurementKind.CIRCLE_CENTER_DISTANCE: CENTER_DISTANCE_COLOR,
            MeasurementKind.CONCENTRICITY: CONCENTRICITY_COLOR,
            MeasurementKind.CIRCLE_ARRAY: ARRAY_COLOR,
        }
        return colors.get(item.kind, ACCENT_LIGHT)

    def _paint_measurement(
        self,
        painter: QPainter,
        item: Measurement,
        selected: bool = False,
        candidate: bool = False,
    ) -> None:
        color = CANDIDATE_COLOR if candidate else (SELECTED_COLOR if selected else self._measurement_color(item))
        width = 2.5 if candidate or selected else 2.0
        pen_style = Qt.PenStyle.DashLine if candidate else Qt.PenStyle.SolidLine
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, width, pen_style))
        points = [self.image_to_widget(point) for point in item.points]
        if not points:
            return

        if item.kind in {MeasurementKind.LENGTH, MeasurementKind.CIRCLE_CENTER_DISTANCE}:
            if len(points) < 2:
                return
            painter.drawLine(points[0], points[1])
            self._draw_arrowhead(painter, points[1], points[0], color)
            self._draw_arrowhead(painter, points[0], points[1], color)
            self._draw_cross(painter, points[0], color, 6)
            self._draw_cross(painter, points[1], color, 6)
            label_pos = (points[0] + points[1]) / 2 + QPointF(0, -17)
        elif item.kind == MeasurementKind.ANGLE:
            if len(points) < 3:
                return
            painter.drawLine(points[1], points[0])
            painter.drawLine(points[1], points[2])
            for point in points[:3]:
                self._draw_cross(painter, point, color, 6)
            radius = min(38.0, max(22.0, min(
                math.dist((points[1].x(), points[1].y()), (points[0].x(), points[0].y())),
                math.dist((points[1].x(), points[1].y()), (points[2].x(), points[2].y())),
            ) * 0.28))
            a1 = math.atan2(points[0].y() - points[1].y(), points[0].x() - points[1].x())
            a2 = math.atan2(points[2].y() - points[1].y(), points[2].x() - points[1].x())
            delta = (a2 - a1) % (2 * math.pi)
            if delta > math.pi:
                a1, a2 = a2, a1
                delta = 2 * math.pi - delta
            path = QPainterPath()
            arc_points = [
                QPointF(points[1].x() + radius * math.cos(a1 + delta * index / 30), points[1].y() + radius * math.sin(a1 + delta * index / 30))
                for index in range(31)
            ]
            path.moveTo(arc_points[0])
            for arc_point in arc_points[1:]:
                path.lineTo(arc_point)
            painter.drawPath(path)
            mid = a1 + delta / 2
            label_pos = QPointF(points[1].x() + (radius + 24) * math.cos(mid), points[1].y() + (radius + 24) * math.sin(mid))
        elif item.kind == MeasurementKind.POINT:
            self._draw_cross(painter, points[0], color, 10)
            painter.drawEllipse(points[0], 4, 4)
            label_pos = points[0] + QPointF(0, -20)
        elif item.kind == MeasurementKind.POINT_LINE_DISTANCE:
            if len(points) < 4:
                return
            painter.setPen(QPen(color, 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(points[2], points[3])
            painter.setPen(QPen(color, width, pen_style))
            painter.drawLine(points[0], points[1])
            self._draw_arrowhead(painter, points[1], points[0], color)
            self._draw_arrowhead(painter, points[0], points[1], color)
            self._draw_cross(painter, points[0], color, 7)
            self._draw_cross(painter, points[1], color, 6)
            label_pos = (points[0] + points[1]) / 2 + QPointF(0, -17)
        elif item.kind in {MeasurementKind.POLYLINE, MeasurementKind.AREA}:
            if len(points) < 2:
                return
            polygon = QPolygonF(points)
            if item.kind == MeasurementKind.AREA and len(points) >= 3:
                fill = QColor(color)
                fill.setAlpha(35)
                painter.setBrush(fill)
                painter.drawPolygon(polygon)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                painter.drawPolyline(polygon)
            for point in points:
                self._draw_cross(painter, point, color, 5)
            label_pos = QPointF(
                sum(point.x() for point in points) / len(points),
                sum(point.y() for point in points) / len(points) - 17,
            )
        elif item.kind == MeasurementKind.ARC:
            if len(points) < 3:
                return
            try:
                center_px, radius_px, sweep, _ = arc_geometry(item.points[0], item.points[1], item.points[2])
            except ValueError:
                return
            center = self.image_to_widget(center_px)
            radius = self._pixel_radius_to_widget(radius_px)
            start_angle = math.atan2(points[0].y() - center.y(), points[0].x() - center.x())
            samples = max(24, int(abs(sweep) / 3.0))
            arc_path = QPainterPath()
            arc_path.moveTo(points[0])
            for index in range(1, samples + 1):
                angle = start_angle + math.radians(sweep) * index / samples
                arc_path.lineTo(QPointF(center.x() + radius * math.cos(angle), center.y() + radius * math.sin(angle)))
            painter.drawPath(arc_path)
            painter.setPen(QPen(color, 1.2, Qt.PenStyle.DashLine))
            painter.drawLine(center, points[0])
            painter.drawLine(center, points[2])
            self._draw_cross(painter, center, color, 7)
            for point in points[:3]:
                self._draw_cross(painter, point, color, 6)
            label_pos = points[1] + QPointF(0, -20)
        elif item.kind in {
            MeasurementKind.CIRCLE,
            MeasurementKind.THREE_POINT_CIRCLE,
            MeasurementKind.CIRCLE_ARRAY,
        }:
            radius = self._pixel_radius_to_widget(float(item.metadata.get("radius_px", 0.0)))
            center = points[0]
            painter.drawEllipse(center, radius, radius)
            self._draw_cross(painter, center, color, 9)
            if item.kind == MeasurementKind.CIRCLE:
                painter.drawLine(QPointF(center.x() - radius, center.y()), QPointF(center.x() + radius, center.y()))
            else:
                for source in points[1:]:
                    painter.setPen(QPen(color, 1.5, Qt.PenStyle.DashLine))
                    painter.drawLine(center, source)
                    self._draw_cross(painter, source, color, 6)
                painter.setPen(QPen(color, width, pen_style))
            label_pos = QPointF(center.x(), center.y() - radius - 16)
        else:
            if len(points) < 2:
                return
            painter.setPen(QPen(color, width, Qt.PenStyle.DashLine))
            painter.drawLine(points[0], points[1])
            self._draw_cross(painter, points[0], color, 8)
            self._draw_cross(painter, points[1], color, 8)
            label_pos = (points[0] + points[1]) / 2 + QPointF(0, -17)
        default_label_pos = label_pos
        if "label_x_px" in item.metadata and "label_y_px" in item.metadata:
            label_pos = self.image_to_widget(
                (float(item.metadata["label_x_px"]), float(item.metadata["label_y_px"]))
            )
            painter.setPen(QPen(color, 1.0, Qt.PenStyle.DotLine))
            painter.drawLine(default_label_pos, label_pos)
        bounds = self._draw_label(painter, label_pos, item.label or item.display_value, color)
        if not candidate:
            self._label_rects[item.id] = bounds

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#20252a"))
        self._label_rects.clear()
        if self._qimage is None:
            painter.setPen(QColor("#9aa4ad"))
            painter.setFont(QFont("Segoe UI", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "打开图片或连接 UVC 摄像头")
            return

        image_rect = self._image_rect()
        painter.setPen(QPen(QColor("#3a4249"), 1))
        painter.setBrush(QColor("#14181b"))
        painter.drawRect(image_rect.adjusted(-1, -1, 1, 1))
        painter.drawImage(image_rect, self._qimage)

        if self.calibration is not None and self.show_calibration_overlay:
            start = self.image_to_widget(self.calibration.start)
            end = self.image_to_widget(self.calibration.end)
            painter.setPen(QPen(ACCENT_LIGHT, 2, Qt.PenStyle.DashLine))
            painter.drawLine(start, end)
            self._draw_cross(painter, start, PRESELECT_COLOR, 8, 1.0)
            self._draw_cross(painter, end, PRESELECT_COLOR, 8, 1.0)
            self._draw_label(
                painter,
                (start + end) / 2 + QPointF(0, -18),
                f"{self.calibration.reference_mm:.3f} mm",
                ACCENT_LIGHT,
            )
            micron_centers: list[QPointF] = []
            for center_px, radius_px, _ in self.calibration.micron_circles:
                center = self.image_to_widget(center_px)
                radius = self._pixel_radius_to_widget(radius_px)
                micron_centers.append(center)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(ACCENT_LIGHT, 1.5, Qt.PenStyle.DashLine))
                painter.drawEllipse(center, radius, radius)
            if micron_centers:
                group_center = QPointF(
                    sum(point.x() for point in micron_centers) / len(micron_centers),
                    min(point.y() for point in micron_centers) - 18.0,
                )
                self._draw_label(
                    painter,
                    group_center,
                    f"微米圆组 ({len(micron_centers)})",
                    ACCENT_LIGHT,
                )

        for item in self.measurements:
            selected = item.id == self.selected_id or item.id in self.selected_ids
            self._paint_measurement(painter, item, selected=selected)

        selected_items = [item for item in self.measurements if item.id == self.selected_id or item.id in self.selected_ids]
        if self.tool == Tool.SELECT and len(selected_items) == 1:
            self._paint_edit_handles(painter, selected_items[0])

        if self.candidate_measurement is not None:
            self._paint_measurement(painter, self.candidate_measurement, candidate=True)

        origin = self.image_to_widget(self.origin)
        if image_rect.contains(origin):
            painter.setPen(QPen(QColor("#e85d68"), 1.5))
            painter.drawLine(origin, QPointF(origin.x() + 30, origin.y()))
            painter.drawLine(origin, QPointF(origin.x(), origin.y() - 30))
            self._draw_cross(painter, origin, QColor("#e85d68"), 5)
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(origin + QPointF(7, -7), "O")

        preview = [] if self.candidate_measurement is not None else list(self.pending_points)
        if self.hover_point is not None and self.tool in {
            Tool.LENGTH,
            Tool.POINT_LINE_DISTANCE,
            Tool.POLYLINE,
            Tool.AREA,
            Tool.ARC,
            Tool.THREE_POINT_CIRCLE,
            Tool.CIRCLE_CENTER_DISTANCE,
            Tool.CONCENTRICITY,
            Tool.CIRCLE_ARRAY,
            Tool.MANUAL_CALIBRATION,
        }:
            preview.append(self.hover_point)
        if preview:
            preview_points = [self.image_to_widget(point) for point in preview]
            painter.setPen(QPen(QColor("#f5f7f8"), 1.5, Qt.PenStyle.DashLine))
            for a, b in zip(preview_points[:-1], preview_points[1:]):
                painter.drawLine(a, b)
            for point in preview_points:
                self._draw_cross(painter, point, QColor("#f5f7f8"), 5)

        if self.tool == Tool.CIRCLE and self.hover_point is not None and self.circle_search_radius:
            center = self.image_to_widget(self.hover_point)
            radius = self._pixel_radius_to_widget(self.circle_search_radius)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(PRESELECT_COLOR, 2, Qt.PenStyle.DashLine))
            painter.drawEllipse(center, radius, radius)
        elif self.hover_point is not None:
            search_radius = self._active_tool_search_radius()
            if search_radius is not None:
                center = self.image_to_widget(self.hover_point)
                radius = self._pixel_radius_to_widget(search_radius)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(PRESELECT_COLOR, 2, Qt.PenStyle.DashLine))
                painter.drawEllipse(center, radius, radius)

    def _active_tool_search_radius(self) -> float | None:
        if self.tool == Tool.ANGLE:
            return self.angle_search_radius
        if self.tool == Tool.POINT_LINE_DISTANCE:
            return self.line_search_radius if self.pending_points else self.snap_search_radius
        if self.tool in {
            Tool.LENGTH,
            Tool.POINT,
            Tool.POLYLINE,
            Tool.AREA,
            Tool.ARC,
            Tool.THREE_POINT_CIRCLE,
        }:
            return self.snap_search_radius
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton and self.tool == Tool.SELECT and self._space_pressed
        ):
            self._panning = True
            self._drag_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            if event.button() == Qt.MouseButton.RightButton and self.tool in {Tool.POLYLINE, Tool.AREA}:
                self.finishMeasurementRequested.emit()
                event.accept()
            return
        additive = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self.tool == Tool.SELECT and not additive:
            label_item = self._label_at_widget_point(event.position())
            if label_item is not None:
                bounds = self._label_rects[label_item.id]
                self._drag_label_measurement = label_item
                self._drag_label_offset = bounds.center() - event.position()
                self.selected_id = label_item.id
                self.selected_ids = {label_item.id}
                self.measurementSelected.emit(label_item)
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self.update()
                return
        image_point = self.widget_to_image(event.position())
        if image_point is None:
            return
        if self.tool == Tool.SELECT:
            if not additive:
                calibration_handle = self._calibration_handle_hit(image_point)
                if calibration_handle is not None:
                    self._drag_calibration_handle = calibration_handle
                    self.setCursor(Qt.CursorShape.BlankCursor)
                    self.update()
                    return
                handle = self._edit_handle_hit(image_point)
                if handle is not None:
                    self._drag_measurement, self._drag_handle = handle
                    self.selected_id = self._drag_measurement.id
                    self.selected_ids = {self._drag_measurement.id}
                    self.measurementSelected.emit(self._drag_measurement)
                    self.setCursor(Qt.CursorShape.BlankCursor)
                    self.update()
                    return
            selected = self._measurement_hit(image_point)
            if selected is None:
                if not additive:
                    self.selected_ids.clear()
            elif additive:
                if selected.id in self.selected_ids:
                    self.selected_ids.remove(selected.id)
                else:
                    self.selected_ids.add(selected.id)
            else:
                self.selected_ids = {selected.id}
            self.selected_id = next(iter(self.selected_ids)) if len(self.selected_ids) == 1 else None
            self.measurementSelected.emit(selected if selected and selected.id in self.selected_ids else None)
            self.update()
            return
        self.imageClicked.emit(*image_point)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_calibration_handle is not None and self.calibration is not None:
            point = self.widget_to_image(event.position())
            if point is None:
                return
            other = self.calibration.end if self._drag_calibration_handle == 0 else self.calibration.start
            if math.dist(point, other) <= 1e-6:
                return
            if self._drag_calibration_handle == 0:
                self.calibration.start = point
            else:
                self.calibration.end = point
            self.calibration.mm_per_pixel = self.calibration.reference_mm / self.calibration.pixel_length
            self.update()
            return
        if self._drag_label_measurement is not None:
            target = event.position() + self._drag_label_offset
            label_x, label_y = self._widget_to_image_unbounded(target)
            self._drag_label_measurement.metadata["label_x_px"] = label_x
            self._drag_label_measurement.metadata["label_y_px"] = label_y
            self.update()
            return
        if self._drag_measurement is not None and self._drag_handle is not None:
            point = self.widget_to_image(event.position())
            if point is None:
                return
            kind, index = self._drag_handle
            self._drag_snap_requested = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if kind == "radius":
                center = self._drag_measurement.points[0]
                self._drag_measurement.metadata["radius_px"] = max(2.0, math.dist(center, point))
            else:
                self._drag_measurement.points[index] = point
            self.measurementChanging.emit(self._drag_measurement)
            self.update()
            return
        if self._panning and self._drag_last is not None:
            delta = event.position() - self._drag_last
            self.pan += delta
            self._drag_last = event.position()
            self.update()
            return
        self.hover_point = self.widget_to_image(event.position())
        now = time.perf_counter()
        if now - self._last_hover_update >= 1.0 / 40.0:
            self._last_hover_update = now
            self.mouseImagePosition.emit(self.hover_point)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_calibration_handle is not None:
            self._drag_calibration_handle = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if self.calibration is not None:
                self.calibrationEdited.emit(self.calibration)
            self.update()
            return
        if self._drag_label_measurement is not None:
            measurement = self._drag_label_measurement
            self._drag_label_measurement = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.measurementEdited.emit(measurement)
            self.update()
            return
        if self._drag_measurement is not None:
            measurement = self._drag_measurement
            handle = self._drag_handle
            snap_requested = self._drag_snap_requested
            self._drag_measurement = None
            self._drag_handle = None
            self._drag_snap_requested = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if handle is not None and handle[0] == "point":
                self.measurementHandleReleased.emit(measurement, handle[1], snap_requested)
            self.measurementEdited.emit(measurement)
            self.update()
            return
        if self._panning:
            self._panning = False
            self._drag_last = None
            self.setCursor(Qt.CursorShape.ArrowCursor if self.tool == Tool.SELECT else Qt.CursorShape.CrossCursor)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.tool in {Tool.POLYLINE, Tool.AREA}:
            self.finishMeasurementRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pressed = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pressed = False
            if not self._panning:
                self.setCursor(Qt.CursorShape.ArrowCursor if self.tool == Tool.SELECT else Qt.CursorShape.CrossCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _handle_tolerance(self) -> float:
        rect = self._image_rect()
        image_width = (self.image_size or (1, 1))[0]
        return max(5.0, 12.0 * image_width / max(rect.width(), 1.0))

    def _calibration_handle_hit(self, image_point: Point) -> int | None:
        if (
            self.calibration is None
            or not self.show_calibration_overlay
            or self.tool != Tool.SELECT
        ):
            return None
        distances = [
            math.dist(image_point, self.calibration.start),
            math.dist(image_point, self.calibration.end),
        ]
        index = 0 if distances[0] <= distances[1] else 1
        return index if distances[index] <= self._handle_tolerance() else None

    def _measurement_handles(self, item: Measurement) -> list[tuple[str, int, Point]]:
        handles = [("point", index, point) for index, point in enumerate(item.points)]
        if item.kind == MeasurementKind.CIRCLE and item.points:
            center = item.points[0]
            radius = float(item.metadata.get("radius_px", 0.0))
            handles = [("point", 0, center), ("radius", 0, (center[0] + radius, center[1]))]
        elif item.kind == MeasurementKind.THREE_POINT_CIRCLE and len(item.points) >= 4:
            handles = [("point", index, item.points[index]) for index in (1, 2, 3)]
        elif item.kind == MeasurementKind.CIRCLE_ARRAY:
            handles = []
        elif item.kind == MeasurementKind.POINT_LINE_DISTANCE and len(item.points) >= 4:
            handles = [("point", index, item.points[index]) for index in (0, 2, 3)]
        elif item.kind == MeasurementKind.CONCENTRICITY:
            handles = []
        elif item.kind == MeasurementKind.CIRCLE_CENTER_DISTANCE:
            handles = []
        return handles

    def _edit_handle_hit(self, image_point: Point) -> tuple[Measurement, tuple[str, int]] | None:
        candidates = [item for item in self.measurements if item.id == self.selected_id or item.id in self.selected_ids]
        if len(candidates) != 1:
            return None
        item = candidates[0]
        ranked = sorted(
            ((math.dist(image_point, point), kind, index) for kind, index, point in self._measurement_handles(item)),
            key=lambda entry: entry[0],
        )
        if ranked and ranked[0][0] <= self._handle_tolerance():
            _, kind, index = ranked[0]
            return item, (kind, index)
        return None

    def _paint_edit_handles(self, painter: QPainter, item: Measurement) -> None:
        for _, _, image_point in self._measurement_handles(item):
            point = self.image_to_widget(image_point)
            self._draw_cross(painter, point, PRESELECT_COLOR, 7, 1.0)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.hover_point = None
        self.mouseImagePosition.emit(None)
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._qimage is None:
            return
        if self.tool == Tool.CIRCLE:
            steps = event.angleDelta().y() / 120.0
            current = float(self.circle_search_radius or 120.0)
            factor = 1.12 ** steps
            source_width, source_height = self.image_size or (self._qimage.width(), self._qimage.height())
            self.circle_search_radius = float(
                np.clip(current * factor, 12.0, min(source_width, source_height) * 0.48)
            )
            self.circleRadiusChanged.emit(int(round(self.circle_search_radius)))
            self.update()
            event.accept()
            return
        current_search_radius = self._active_tool_search_radius()
        if current_search_radius is not None:
            steps = event.angleDelta().y() / 120.0
            factor = 1.12 ** steps
            source_width, source_height = self.image_size or (self._qimage.width(), self._qimage.height())
            maximum = min(source_width, source_height) * 0.48
            value = float(np.clip(current_search_radius * factor, 8.0, maximum))
            if self.tool == Tool.ANGLE:
                self.angle_search_radius = value
            elif self.tool == Tool.POINT_LINE_DISTANCE and self.pending_points:
                self.line_search_radius = value
            else:
                self.snap_search_radius = value
            self.toolSearchRadiusChanged.emit(int(round(value)))
            self.update()
            event.accept()
            return
        before = self.widget_to_image(event.position())
        factor = 1.18 if event.angleDelta().y() > 0 else 1.0 / 1.18
        old_zoom = self.zoom
        self.zoom = float(np.clip(self.zoom * factor, 0.15, 16.0))
        if before is not None and old_zoom != self.zoom:
            after_widget = self.image_to_widget(before)
            self.pan += event.position() - after_widget
        self.zoomChanged.emit(self.zoom)
        self.update()
