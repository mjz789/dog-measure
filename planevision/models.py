from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import uuid

import numpy as np


Point = tuple[float, float]


class Tool(str, Enum):
    SELECT = "select"
    LENGTH = "length"
    ANGLE = "angle"
    POINT = "point"
    POINT_LINE_DISTANCE = "point_line_distance"
    POLYLINE = "polyline"
    AREA = "area"
    ARC = "arc"
    THREE_POINT_CIRCLE = "three_point_circle"
    CIRCLE = "circle"
    CIRCLE_CENTER_DISTANCE = "circle_center_distance"
    CONCENTRICITY = "concentricity"
    CIRCLE_ARRAY = "circle_array"
    ORIGIN = "origin"
    MANUAL_CALIBRATION = "manual_calibration"


class MeasurementKind(str, Enum):
    LENGTH = "length"
    ANGLE = "angle"
    POINT = "point"
    POINT_LINE_DISTANCE = "point_line_distance"
    POLYLINE = "polyline"
    AREA = "area"
    ARC = "arc"
    THREE_POINT_CIRCLE = "three_point_circle"
    CIRCLE = "circle"
    CIRCLE_CENTER_DISTANCE = "circle_center_distance"
    CONCENTRICITY = "concentricity"
    CIRCLE_ARRAY = "circle_array"


@dataclass(slots=True)
class Calibration:
    mm_per_pixel: float
    start: Point
    end: Point
    method: str
    confidence: float = 1.0
    reference_mm: float = 40.0
    image_size: tuple[int, int] | None = None
    micron_circle_count: int = 0
    micron_scale_mm_per_pixel: float | None = None
    micron_circles: list[tuple[Point, float, float]] = field(default_factory=list)

    @property
    def pixel_length(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def pixels_per_mm(self) -> float:
        return 1.0 / self.mm_per_pixel


@dataclass(slots=True)
class Measurement:
    kind: MeasurementKind
    points: list[Point]
    value: float
    unit: str
    label: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    metadata: dict[str, float | str] = field(default_factory=dict)

    @property
    def display_value(self) -> str:
        if self.kind == MeasurementKind.ANGLE:
            return f"{self.value:.2f} deg"
        if self.kind == MeasurementKind.AREA:
            return f"{self.value:.3f} mm^2"
        return f"{self.value:.3f} {self.unit}"


def distance(a: Point, b: Point) -> float:
    return math.dist(a, b)


def angle_degrees(a: Point, vertex: Point, c: Point) -> float:
    first = np.asarray(a, dtype=np.float64) - np.asarray(vertex, dtype=np.float64)
    second = np.asarray(c, dtype=np.float64) - np.asarray(vertex, dtype=np.float64)
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator <= 1e-12:
        return 0.0
    cosine = float(np.clip(np.dot(first, second) / denominator, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def polyline_length(points: list[Point]) -> float:
    return sum(distance(a, b) for a, b in zip(points[:-1], points[1:]))


def polygon_area(points: list[Point]) -> float:
    if len(points) < 3:
        return 0.0
    coordinates = np.asarray(points, dtype=np.float64)
    x = coordinates[:, 0]
    y = coordinates[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) * 0.5


def arc_geometry(a: Point, through: Point, c: Point) -> tuple[Point, float, float, float]:
    """Return center, radius, signed sweep in degrees, and arc length in pixels."""
    center, radius = circumcircle(a, through, c)
    cx, cy = center
    start_angle = math.atan2(a[1] - cy, a[0] - cx)
    through_angle = math.atan2(through[1] - cy, through[0] - cx)
    end_angle = math.atan2(c[1] - cy, c[0] - cx)
    counterclockwise_sweep = (end_angle - start_angle) % (2.0 * math.pi)
    through_from_start = (through_angle - start_angle) % (2.0 * math.pi)
    if through_from_start <= counterclockwise_sweep + 1e-9:
        signed_sweep = counterclockwise_sweep
    else:
        signed_sweep = -(2.0 * math.pi - counterclockwise_sweep)
    sweep_degrees = math.degrees(signed_sweep)
    return center, radius, sweep_degrees, radius * abs(signed_sweep)


def circumcircle(a: Point, b: Point, c: Point) -> tuple[Point, float]:
    """Return the unique circle through three non-collinear points."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    determinant = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    scale = max(distance(a, b), distance(b, c), distance(c, a), 1.0)
    if abs(determinant) <= scale * scale * 1e-6:
        raise ValueError("三个圆心接近共线，无法确定唯一的圆形阵列外接圆。")
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / determinant
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / determinant
    center = (ux, uy)
    return center, distance(center, a)
