from __future__ import annotations

from dataclasses import dataclass
import math

import cv2
import numpy as np

from .models import Calibration, Point


@dataclass(slots=True)
class CircleDetection:
    center: Point
    radius: float
    confidence: float
    method: str


@dataclass(slots=True)
class SnapDetection:
    point: Point
    confidence: float
    method: str


@dataclass(slots=True)
class LineDetection:
    start: Point
    end: Point
    confidence: float
    method: str


@dataclass(slots=True)
class AngleDetection:
    points: tuple[Point, Point, Point]
    confidence: float
    method: str


@dataclass(slots=True)
class MicronCircleCalibration:
    mm_per_pixel: float
    circles: list[tuple[Point, float, float]]
    residual: float


@dataclass(slots=True)
class _RulerCandidate:
    start: Point
    end: Point
    score: float
    tick_count: int
    spacing_cv: float

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)


def _gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _local_gray(image: np.ndarray, click: Point, radius: int) -> tuple[np.ndarray, int, int, np.ndarray]:
    gray = _gray(image)
    height, width = gray.shape
    cx, cy = click
    x0 = max(0, int(round(cx)) - radius)
    y0 = max(0, int(round(cy)) - radius)
    x1 = min(width, int(round(cx)) + radius + 1)
    y1 = min(height, int(round(cy)) + radius + 1)
    patch = gray[y0:y1, x0:x1]
    if min(patch.shape, default=0) < 16:
        raise ValueError("点击位置太靠近图像边缘，自动搜索区域不足。")
    return patch, x0, y0, np.asarray([cx - x0, cy - y0], dtype=np.float64)


def snap_point_near(image: np.ndarray, click: Point, search_radius: int = 42) -> SnapDetection:
    """Snap an approximate click to the nearest repeatable corner or edge point."""
    patch, x0, y0, local_click = _local_gray(image, click, max(12, int(search_radius)))
    normalized = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(4, 4)).apply(patch)
    blurred = cv2.GaussianBlur(normalized, (3, 3), 0)
    corners = cv2.goodFeaturesToTrack(
        blurred,
        maxCorners=24,
        qualityLevel=0.035,
        minDistance=6,
        blockSize=5,
        useHarrisDetector=False,
    )
    ranked: list[tuple[float, np.ndarray, str, float]] = []
    if corners is not None:
        for corner in corners.reshape(-1, 2):
            gap = float(np.linalg.norm(corner - local_click))
            if gap <= search_radius:
                proximity = max(0.0, 1.0 - gap / max(search_radius, 1))
                ranked.append((proximity + 0.14, corner.astype(np.float64), "自动角点", 0.72 + 0.25 * proximity))

    edges = cv2.Canny(blurred, 45, 135, L2gradient=True)
    edge_y, edge_x = np.nonzero(edges)
    if edge_x.size:
        edge_points = np.column_stack([edge_x, edge_y]).astype(np.float64)
        gaps = np.linalg.norm(edge_points - local_click, axis=1)
        index = int(np.argmin(gaps))
        if gaps[index] <= search_radius:
            proximity = max(0.0, 1.0 - float(gaps[index]) / max(search_radius, 1))
            ranked.append((proximity, edge_points[index], "自动边缘", 0.58 + 0.32 * proximity))

    if not ranked:
        raise ValueError("附近没有找到清晰边缘或角点，请点击更靠近目标边界的位置。")
    _, point, method, confidence = max(ranked, key=lambda item: item[0])
    refined = np.asarray([[point]], dtype=np.float32)
    try:
        cv2.cornerSubPix(
            blurred,
            refined,
            (4, 4),
            (-1, -1),
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 20, 0.02),
        )
        point = refined[0, 0].astype(np.float64)
    except cv2.error:
        pass
    return SnapDetection((float(point[0] + x0), float(point[1] + y0)), confidence, method)


def _point_segment_distance(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
    vector = end - start
    factor = float(np.clip(np.dot(point - start, vector) / (np.dot(vector, vector) + 1e-9), 0.0, 1.0))
    return float(np.linalg.norm(point - (start + factor * vector)))


def _line_candidates_near(
    image: np.ndarray,
    click: Point,
    search_radius: int,
    max_candidates: int = 12,
) -> list[LineDetection]:
    patch, x0, y0, local_click = _local_gray(image, click, max(48, int(search_radius)))
    normalized = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(6, 6)).apply(patch)
    blurred = cv2.GaussianBlur(normalized, (3, 3), 0)
    median = float(np.median(blurred))
    lower = int(max(24, 0.55 * median))
    upper = int(min(220, max(lower + 45, 1.35 * median)))
    edges = cv2.Canny(blurred, lower, upper, L2gradient=True)
    min_length = max(24, int(search_radius * 0.32))
    lines = cv2.HoughLinesP(
        edges,
        1.0,
        np.pi / 360.0,
        threshold=max(18, int(search_radius * 0.16)),
        minLineLength=min_length,
        maxLineGap=max(5, int(search_radius * 0.07)),
    )
    if lines is None:
        return []

    candidates: list[tuple[float, LineDetection, float]] = []
    for x1, y1, x2, y2 in lines[:, 0, :]:
        start = np.asarray([float(x1), float(y1)])
        end = np.asarray([float(x2), float(y2)])
        length = float(np.linalg.norm(end - start))
        gap = _point_segment_distance(local_click, start, end)
        if gap > search_radius * 0.72:
            continue
        direction = end - start
        angle = math.atan2(float(direction[1]), float(direction[0])) % math.pi
        proximity = max(0.0, 1.0 - gap / max(search_radius * 0.72, 1.0))
        length_score = min(1.0, length / max(search_radius * 0.9, 1.0))
        confidence = 0.5 + 0.27 * proximity + 0.2 * length_score
        score = 0.62 * proximity + 0.38 * length_score
        detection = LineDetection(
            (float(start[0] + x0), float(start[1] + y0)),
            (float(end[0] + x0), float(end[1] + y0)),
            float(np.clip(confidence, 0.0, 0.98)),
            "局部直线边缘",
        )
        candidates.append((score, detection, angle))
    candidates.sort(key=lambda item: item[0], reverse=True)

    distinct: list[tuple[LineDetection, float]] = []
    for _, candidate, angle in candidates:
        center = np.mean(np.asarray([candidate.start, candidate.end]), axis=0)
        duplicate = False
        for kept, kept_angle in distinct:
            kept_center = np.mean(np.asarray([kept.start, kept.end]), axis=0)
            angle_gap = abs(angle - kept_angle)
            angle_gap = min(angle_gap, math.pi - angle_gap)
            if angle_gap < math.radians(4.0) and np.linalg.norm(center - kept_center) < 12.0:
                duplicate = True
                break
        if not duplicate:
            distinct.append((candidate, angle))
        if len(distinct) >= max_candidates:
            break
    return [item[0] for item in distinct]


def detect_line_near(image: np.ndarray, click: Point, search_radius: int = 150) -> LineDetection:
    candidates = _line_candidates_near(image, click, search_radius)
    if not candidates:
        raise ValueError("附近没有找到足够清晰且连续的直线边缘。")
    return candidates[0]


def _infinite_line_intersection(first: LineDetection, second: LineDetection) -> np.ndarray | None:
    p = np.asarray(first.start, dtype=np.float64)
    r = np.asarray(first.end, dtype=np.float64) - p
    q = np.asarray(second.start, dtype=np.float64)
    s = np.asarray(second.end, dtype=np.float64) - q
    cross = float(r[0] * s[1] - r[1] * s[0])
    if abs(cross) < 1e-7:
        return None
    qmp = q - p
    factor = float((qmp[0] * s[1] - qmp[1] * s[0]) / cross)
    return p + factor * r


def detect_angle_candidates_near(
    image: np.ndarray,
    click: Point,
    search_radius: int = 170,
    max_candidates: int = 4,
) -> list[AngleDetection]:
    """Return plausible two-line angle candidates whose intersection is near a click."""
    lines = _line_candidates_near(image, click, search_radius, max_candidates=16)
    click_array = np.asarray(click, dtype=np.float64)
    ranked: list[tuple[float, AngleDetection]] = []
    for index, first in enumerate(lines):
        first_vector = np.asarray(first.end) - np.asarray(first.start)
        first_angle = math.atan2(float(first_vector[1]), float(first_vector[0]))
        for second in lines[index + 1 :]:
            second_vector = np.asarray(second.end) - np.asarray(second.start)
            second_angle = math.atan2(float(second_vector[1]), float(second_vector[0]))
            acute = abs(math.degrees(first_angle - second_angle)) % 180.0
            acute = min(acute, 180.0 - acute)
            if acute < 12.0:
                continue
            vertex = _infinite_line_intersection(first, second)
            if vertex is None:
                continue
            vertex_gap = float(np.linalg.norm(vertex - click_array))
            if vertex_gap > search_radius * 0.62:
                continue

            def ray_point(line: LineDetection) -> np.ndarray:
                endpoints = [np.asarray(line.start), np.asarray(line.end)]
                return max(endpoints, key=lambda endpoint: float(np.linalg.norm(endpoint - vertex)))

            first_point = ray_point(first)
            second_point = ray_point(second)
            if min(np.linalg.norm(first_point - vertex), np.linalg.norm(second_point - vertex)) < 18.0:
                continue
            proximity = max(0.0, 1.0 - vertex_gap / max(search_radius * 0.62, 1.0))
            confidence = float(np.clip(0.45 * proximity + 0.275 * first.confidence + 0.275 * second.confidence, 0.0, 0.99))
            points = (
                (float(first_point[0]), float(first_point[1])),
                (float(vertex[0]), float(vertex[1])),
                (float(second_point[0]), float(second_point[1])),
            )
            ranked.append((confidence, AngleDetection(points, confidence, "两条直线边缘自动拟合")))
    ranked.sort(key=lambda item: item[0], reverse=True)

    distinct: list[AngleDetection] = []
    for _, candidate in ranked:
        vertex = np.asarray(candidate.points[1])
        value = _angle_value(candidate.points)
        if any(np.linalg.norm(vertex - np.asarray(kept.points[1])) < 10.0 and abs(value - _angle_value(kept.points)) < 3.0 for kept in distinct):
            continue
        distinct.append(candidate)
        if len(distinct) >= max_candidates:
            break
    if not distinct:
        raise ValueError("附近没有找到两条可靠的相交直线，请点击更靠近角点的位置。")
    return distinct


def _angle_value(points: tuple[Point, Point, Point]) -> float:
    first = np.asarray(points[0]) - np.asarray(points[1])
    second = np.asarray(points[2]) - np.asarray(points[1])
    cosine = float(np.clip(np.dot(first, second) / (np.linalg.norm(first) * np.linalg.norm(second) + 1e-9), -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def project_point_to_line(point: Point, line: LineDetection) -> Point:
    source = np.asarray(point, dtype=np.float64)
    start = np.asarray(line.start, dtype=np.float64)
    vector = np.asarray(line.end, dtype=np.float64) - start
    factor = float(np.dot(source - start, vector) / (np.dot(vector, vector) + 1e-9))
    foot = start + factor * vector
    return float(foot[0]), float(foot[1])


def _cluster_positions(values: np.ndarray, max_gap: int) -> list[float]:
    if values.size == 0:
        return []
    groups: list[list[int]] = [[int(values[0])]]
    for value in values[1:]:
        if int(value) - groups[-1][-1] <= max_gap:
            groups[-1].append(int(value))
        else:
            groups.append([int(value)])
    return [float(np.mean(group)) for group in groups]


def _ruler_tick_metrics(binary: np.ndarray, x0: int, x1: int, y: int) -> tuple[int, float, float, float]:
    height, width = binary.shape
    line_length = x1 - x0
    if line_length < 40:
        return 0, 99.0, 0.0, 0.0

    band = max(12, min(int(height * 0.032), int(line_length * 0.16)))
    xa = max(0, x0 - int(line_length * 0.02))
    xb = min(width, x1 + int(line_length * 0.02) + 1)
    ya = max(0, y - band)
    yb = min(height, y + max(5, band // 3))
    patch = binary[ya:yb, xa:xb]
    if patch.size == 0:
        return 0, 99.0, 0.0, 0.0

    center_row = y - ya
    exclusion = max(2, height // 900)
    upper = patch[: max(1, center_row - exclusion)]
    lower = patch[min(patch.shape[0], center_row + exclusion + 1) :]
    projection = np.count_nonzero(upper, axis=0).astype(np.float32)
    if lower.size:
        projection = np.maximum(projection, np.count_nonzero(lower, axis=0).astype(np.float32))

    threshold = max(3.0, float(np.percentile(projection, 70)))
    active = np.flatnonzero(projection >= threshold)
    peaks = _cluster_positions(active, max(1, width // 1300 + 1))
    peaks = [peak for peak in peaks if xa + peak >= x0 - line_length * 0.03 and xa + peak <= x1 + line_length * 0.03]
    if len(peaks) < 8:
        return len(peaks), 99.0, 0.0, 0.0

    gaps = np.diff(peaks)
    median_gap = float(np.median(gaps))
    valid = gaps[(gaps > median_gap * 0.45) & (gaps < median_gap * 1.7)]
    spacing_cv = float(np.std(valid) / np.mean(valid)) if valid.size >= 5 else 99.0
    density = len(peaks) / max(1.0, line_length)

    # On this calibration card the metric ticks rise above their baseline while
    # the neighboring inch-scale ticks hang below theirs. Looking only a few
    # pixels away from the baseline avoids interference from labels and numbers.
    near_depth = max(6, min(16, int(line_length * 0.025)))
    near_upper = binary[max(0, y - near_depth) : max(0, y - 2), xa:xb]
    near_lower = binary[min(height, y + 3) : min(height, y + near_depth + 1), xa:xb]
    upper_columns = 0
    lower_columns = 0
    if near_upper.size:
        upper_columns = int(np.count_nonzero(np.count_nonzero(near_upper, axis=0) >= max(2, near_upper.shape[0] * 0.42)))
    if near_lower.size:
        lower_columns = int(np.count_nonzero(np.count_nonzero(near_lower, axis=0) >= max(2, near_lower.shape[0] * 0.42)))
    upward_ratio = upper_columns / max(1, upper_columns + lower_columns)
    return len(peaks), spacing_cv, density, upward_ratio


def _metric_ruler_candidates(gray: np.ndarray, transpose: bool = False) -> list[tuple[_RulerCandidate, float]]:
    source = gray.T if transpose else gray
    source_height, source_width = source.shape
    scale = min(1.0, 1800.0 / source_width)
    work = (
        cv2.resize(source, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else source
    )
    height, width = work.shape
    normalized = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(12, 8)).apply(work)
    binary = cv2.adaptiveThreshold(
        cv2.GaussianBlur(normalized, (3, 3), 0),
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        35,
        8,
    )
    horizontal = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(24, int(width * 0.025)), 1)),
    )
    horizontal = cv2.morphologyEx(
        horizontal,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, width // 500), 1)),
    )
    contours, _ = cv2.findContours(horizontal, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    common_lengths = (10, 20, 30, 40, 50, 100)
    results: list[tuple[_RulerCandidate, float]] = []
    for contour in contours:
        x, y, line_width, line_height = cv2.boundingRect(contour)
        if not 0.04 <= line_width / width <= 0.92:
            continue
        if line_height > max(14, height * 0.022):
            continue
        center_y = y + line_height // 2
        ticks, spacing_cv, density, side_ratio = _ruler_tick_metrics(
            binary, x, x + line_width - 1, center_y
        )
        if ticks < 8 or spacing_cv > 0.32:
            continue
        divisions = ticks - 1
        reference_mm = min(common_lengths, key=lambda length: abs(length - divisions))
        count_error = abs(reference_mm - divisions)
        if count_error > max(2, reference_mm * 0.08):
            continue
        periodicity_score = max(0.0, 1.0 - spacing_cv / 0.32)
        count_score = max(0.0, 1.0 - count_error / max(2.0, reference_mm * 0.08))
        density_score = 1.0 if 0.018 <= density <= 0.24 else 0.55
        one_side_score = max(side_ratio, 1.0 - side_ratio)
        score = 0.44 * periodicity_score + 0.30 * count_score + 0.10 * density_score + 0.16 * one_side_score
        start_raw = (x / scale, center_y / scale)
        end_raw = ((x + line_width - 1) / scale, center_y / scale)
        start = (start_raw[1], start_raw[0]) if transpose else start_raw
        end = (end_raw[1], end_raw[0]) if transpose else end_raw
        results.append(
            (
                _RulerCandidate(start, end, score, ticks, spacing_cv),
                float(reference_mm),
            )
        )
    return results


def _known_dual_scale_40mm_candidate(gray: np.ndarray) -> _RulerCandidate | None:
    """Recognize the supplied card's 40 mm ruler only when its lower inch scale is also present."""
    height, width = gray.shape
    scale = min(1.0, 1800.0 / width)
    work = (
        cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else gray
    )
    work_height, work_width = work.shape
    normalized = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(12, 8)).apply(work)
    binary = cv2.adaptiveThreshold(
        cv2.GaussianBlur(normalized, (3, 3), 0),
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        35,
        8,
    )
    horizontal = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(35, int(work_width * 0.045)), 1)),
    )
    horizontal = cv2.morphologyEx(
        horizontal,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, work_width // 500), 1)),
    )
    contours, _ = cv2.findContours(horizontal, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    rectangles = [cv2.boundingRect(contour) for contour in contours]
    candidates: list[_RulerCandidate] = []
    for x, y, line_width, line_height in rectangles:
        if not 0.12 <= line_width / work_width <= 0.42:
            continue
        if not 0.42 * work_height <= y <= 0.82 * work_height:
            continue
        if line_height > max(12, work_height * 0.018):
            continue
        companion = any(
            other_width >= line_width * 0.55
            and line_width * 0.015 <= other_y - y <= line_width * 0.12
            for other_x, other_y, other_width, other_height in rectangles
            if (other_x, other_y, other_width, other_height) != (x, y, line_width, line_height)
        )
        if not companion:
            continue
        center_y = y + line_height // 2
        ticks, spacing_cv, density, upward_ratio = _ruler_tick_metrics(
            binary, x, x + line_width - 1, center_y
        )
        if not 35 <= ticks <= 75 or spacing_cv > 0.48 or upward_ratio < 0.72:
            continue
        count_score = max(0.0, 1.0 - abs(ticks - 70) / 60.0)
        periodicity_score = max(0.0, 1.0 - spacing_cv / 0.48)
        density_score = 1.0 if 0.045 <= density <= 0.16 else 0.55
        score = 0.38 * periodicity_score + 0.20 * count_score + 0.10 * density_score + 0.32 * upward_ratio
        candidates.append(
            _RulerCandidate(
                (x / scale, center_y / scale),
                ((x + line_width - 1) / scale, center_y / scale),
                score,
                ticks,
                spacing_cv,
            )
        )
    return max(candidates, key=lambda item: item.score, default=None)


def detect_metric_ruler(image: np.ndarray) -> Calibration:
    """Detect a reliable horizontal or vertical common metric ruler in the full image."""
    gray = _gray(image)
    height, width = gray.shape
    if min(height, width) < 240:
        raise ValueError("图像分辨率过低，无法可靠识别标尺。")
    known_40mm = _known_dual_scale_40mm_candidate(gray)
    if known_40mm is not None and known_40mm.score >= 0.60:
        best = known_40mm
        reference_mm = 40.0
    else:
        candidates = _metric_ruler_candidates(gray) + _metric_ruler_candidates(gray, transpose=True)
        if not candidates:
            raise ValueError("整幅画面未找到可确定实际长度的公制标尺。")
        candidates.sort(key=lambda item: item[0].score, reverse=True)
        best, reference_mm = candidates[0]
        if best.score < 0.60:
            raise ValueError("发现疑似刻度线，但无法可靠确定标尺长度。")
    calibration = Calibration(
        mm_per_pixel=reference_mm / best.length,
        start=best.start,
        end=best.end,
        method=f"自动识别 {reference_mm:g} mm 标尺",
        confidence=float(np.clip(best.score, 0.0, 0.99)),
        reference_mm=reference_mm,
        image_size=(width, height),
    )
    if reference_mm == 40.0:
        micron = detect_micron_circle_scale(image, calibration)
        if micron is not None:
            circle_weight = min(0.22, 0.025 * len(micron.circles))
            calibration.mm_per_pixel = (
                (1.0 - circle_weight) * calibration.mm_per_pixel + circle_weight * micron.mm_per_pixel
            )
            calibration.micron_circle_count = len(micron.circles)
            calibration.micron_scale_mm_per_pixel = micron.mm_per_pixel
            calibration.micron_circles = micron.circles
            calibration.method = "40 mm 标尺 + 微米圆组联合标定"
            calibration.confidence = float(
                np.clip(calibration.confidence + 0.02 * len(micron.circles), 0.0, 0.99)
            )
    return calibration


def detect_40mm_ruler(image: np.ndarray) -> Calibration:
    """Backward-compatible entry point for callers using the original name."""
    calibration = detect_metric_ruler(image)
    if calibration.reference_mm != 40.0:
        raise ValueError(f"识别到 {calibration.reference_mm:g} mm 标尺，不是 40 mm 标尺。")
    return calibration


def _ring_support(magnitude: np.ndarray, cx: float, cy: float, radius: float) -> float:
    angles = np.linspace(0.0, 2.0 * math.pi, 96, endpoint=False)
    xs = np.clip(np.rint(cx + radius * np.cos(angles)).astype(int), 0, magnitude.shape[1] - 1)
    ys = np.clip(np.rint(cy + radius * np.sin(angles)).astype(int), 0, magnitude.shape[0] - 1)
    return float(np.mean(magnitude[ys, xs]))


def detect_micron_circle_scale(
    image: np.ndarray,
    ruler: Calibration,
) -> MicronCircleCalibration | None:
    """Measure usable members of the printed 100-1000 um circle sequence.

    The card layout is normalized to the detected 40 mm ruler. Circles below
    roughly six pixels in diameter are intentionally excluded because their
    printed stroke dominates the nominal geometry at that sampling density.
    """
    gray = _gray(image)
    length = ruler.pixel_length
    if length < 180:
        return None
    x_direction = 1.0 if ruler.end[0] >= ruler.start[0] else -1.0
    expected_spacing = length * 0.039
    first_x = ruler.end[0] + x_direction * length * 0.158
    row_y = (ruler.start[1] + ruler.end[1]) / 2.0 + length * 0.009
    expected = [
        (first_x + x_direction * expected_spacing * index, row_y, (index + 1) * 0.1)
        for index in range(10)
    ]

    detected: list[tuple[Point, float, float]] = []
    for expected_x, expected_y, diameter_mm in expected:
        expected_radius = diameter_mm / (2.0 * ruler.mm_per_pixel)
        if expected_radius * 2.0 < 4.0:
            continue
        half = max(11, int(round(expected_spacing * 0.64)))
        x0 = max(0, int(round(expected_x)) - half)
        y0 = max(0, int(round(expected_y)) - half)
        x1 = min(gray.shape[1], int(round(expected_x)) + half + 1)
        y1 = min(gray.shape[0], int(round(expected_y)) + half + 1)
        patch = gray[y0:y1, x0:x1]
        if min(patch.shape) < 12:
            continue
        patch = cv2.GaussianBlur(patch, (3, 3), 0)
        _, binary = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        local_expected = np.asarray([expected_x - x0, expected_y - y0], dtype=np.float64)
        best: tuple[float, Point, float] | None = None
        for contour in contours:
            if len(contour) < 8:
                continue
            area = abs(cv2.contourArea(contour))
            perimeter = cv2.arcLength(contour, True)
            if area < 5 or perimeter < 8:
                continue
            (cx, cy), radius = cv2.minEnclosingCircle(contour)
            center_gap = float(np.linalg.norm(np.asarray([cx, cy]) - local_expected))
            circularity = float(np.clip(4.0 * math.pi * area / (perimeter * perimeter + 1e-9), 0.0, 1.0))
            if center_gap > max(4.0, expected_spacing * 0.22):
                continue
            if not expected_radius * 0.65 <= radius <= expected_radius * 1.55:
                continue
            if circularity < 0.68:
                continue
            radius_fit = 1.0 - min(1.0, abs(radius - expected_radius) / max(expected_radius, 1.0))
            score = 0.58 * circularity + 0.27 * radius_fit + 0.15 * max(0.0, 1.0 - center_gap / 5.0)
            if best is None or score > best[0]:
                best = (score, (cx + x0, cy + y0), float(radius))
        if best is None:
            continue
        _, center, radius = best
        detected.append((center, radius, diameter_mm))

    if len(detected) < 4:
        return None
    diameters_px = np.asarray([radius * 2.0 for _, radius, _ in detected], dtype=np.float64)
    diameters_mm = np.asarray([diameter for _, _, diameter in detected], dtype=np.float64)
    # A fitted intercept absorbs the approximately constant printed edge width.
    slope, intercept = np.polyfit(diameters_mm, diameters_px, 1)
    if slope <= 0:
        return None
    predicted = slope * diameters_mm + intercept
    residual = float(np.sqrt(np.mean((diameters_px - predicted) ** 2)))
    scale = 1.0 / float(slope)
    relative_difference = abs(scale - ruler.mm_per_pixel) / ruler.mm_per_pixel
    if residual > 1.5 or relative_difference > 0.10:
        return None
    return MicronCircleCalibration(scale, detected, residual)


def _gradient_circle_support(magnitude: np.ndarray, cx: float, cy: float, radius: float) -> float:
    if radius < 3:
        return 0.0
    angles = np.linspace(0.0, 2.0 * math.pi, 120, endpoint=False)
    xs = np.clip(np.rint(cx + radius * np.cos(angles)).astype(int), 0, magnitude.shape[1] - 1)
    ys = np.clip(np.rint(cy + radius * np.sin(angles)).astype(int), 0, magnitude.shape[0] - 1)
    samples = magnitude[ys, xs]
    reference = float(np.percentile(magnitude, 88)) + 1e-6
    return float(np.clip(np.mean(samples) / reference, 0.0, 1.0))


def _circle_edge_quality(
    gray: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    magnitude: np.ndarray,
    cx: float,
    cy: float,
    radius: float,
) -> tuple[float, float, float]:
    """Return edge coverage, radial-gradient consistency, and contrast."""
    if radius < 4:
        return 0.0, 0.0, 0.0
    sample_count = int(np.clip(2.0 * math.pi * radius / 2.5, 96, 360))
    angles = np.linspace(0.0, 2.0 * math.pi, sample_count, endpoint=False)
    cosines = np.cos(angles)
    sines = np.sin(angles)
    xs = np.clip(np.rint(cx + radius * cosines).astype(int), 0, gray.shape[1] - 1)
    ys = np.clip(np.rint(cy + radius * sines).astype(int), 0, gray.shape[0] - 1)
    edge_magnitude = magnitude[ys, xs]
    local_reference = max(12.0, float(np.percentile(magnitude, 82)))
    strong = edge_magnitude >= local_reference
    coverage = float(np.mean(strong))

    radial = np.abs(gx[ys, xs] * cosines + gy[ys, xs] * sines) / (edge_magnitude + 1e-6)
    radial_consistency = float(np.mean(radial[strong])) if np.any(strong) else 0.0

    offset = float(np.clip(radius * 0.025, 2.0, 7.0))
    inner_x = np.clip(np.rint(cx + (radius - offset) * cosines).astype(int), 0, gray.shape[1] - 1)
    inner_y = np.clip(np.rint(cy + (radius - offset) * sines).astype(int), 0, gray.shape[0] - 1)
    outer_x = np.clip(np.rint(cx + (radius + offset) * cosines).astype(int), 0, gray.shape[1] - 1)
    outer_y = np.clip(np.rint(cy + (radius + offset) * sines).astype(int), 0, gray.shape[0] - 1)
    contrast = float(np.mean(np.abs(gray[inner_y, inner_x].astype(np.float32) - gray[outer_y, outer_x].astype(np.float32))))
    return coverage, radial_consistency, contrast


def _radial_concentric_candidates(
    gray: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    magnitude: np.ndarray,
    center: np.ndarray,
    min_radius: int,
    max_radius: int,
) -> list[tuple[float, float, float, float, str]]:
    """Pair signed inner/outer radial edges into close concentric circle centerlines."""
    if max_radius - min_radius < 8:
        return []
    angles = np.linspace(0.0, 2.0 * math.pi, 180, endpoint=False)
    cosines = np.cos(angles)
    sines = np.sin(angles)
    signed_profile: list[float] = []
    absolute_profile: list[float] = []
    radii = np.arange(min_radius, max_radius + 1, dtype=np.float64)
    for radius in radii:
        xs = np.clip(np.rint(center[0] + radius * cosines).astype(int), 0, gray.shape[1] - 1)
        ys = np.clip(np.rint(center[1] + radius * sines).astype(int), 0, gray.shape[0] - 1)
        radial = gx[ys, xs] * cosines + gy[ys, xs] * sines
        signed_profile.append(float(np.median(radial)))
        absolute_profile.append(float(np.mean(np.abs(radial))))
    signed = np.asarray(signed_profile, dtype=np.float64)
    absolute = np.asarray(absolute_profile, dtype=np.float64)
    if float(np.max(absolute)) < 8.0:
        return []
    threshold = max(6.0, float(np.percentile(absolute, 78)) * 0.72)
    extrema: list[tuple[int, float]] = []
    for index in range(1, len(radii) - 1):
        if absolute[index] < threshold:
            continue
        if absolute[index] >= absolute[index - 1] and absolute[index] >= absolute[index + 1]:
            extrema.append((index, signed[index]))

    results: list[tuple[float, float, float, float, str]] = []
    used: set[int] = set()
    for inner_index, inner_signed in extrema:
        if inner_signed >= 0 or inner_index in used:
            continue
        matches = [
            (outer_index, outer_signed)
            for outer_index, outer_signed in extrema
            if outer_signed > 0 and 2 <= outer_index - inner_index <= 20 and outer_index not in used
        ]
        if not matches:
            continue
        outer_index, _ = min(matches, key=lambda item: item[0] - inner_index)
        inner_radius = float(radii[inner_index])
        outer_radius = float(radii[outer_index])
        radius = (inner_radius + outer_radius) * 0.5
        inner_support = _gradient_circle_support(magnitude, float(center[0]), float(center[1]), inner_radius)
        outer_support = _gradient_circle_support(magnitude, float(center[0]), float(center[1]), outer_radius)
        edge_strength = min(1.0, float((absolute[inner_index] + absolute[outer_index]) * 0.5) / 120.0)
        if min(inner_support, outer_support) < 0.24 or edge_strength < 0.12:
            continue
        score = float(np.clip(0.46 * edge_strength + 0.27 * inner_support + 0.27 * outer_support + 0.08, 0.0, 0.99))
        results.append((score, float(center[0]), float(center[1]), radius, "径向双边缘同心圆拟合"))
        used.update({inner_index, outer_index})
    return results


def detect_circle_near(
    image: np.ndarray,
    click: Point,
    search_radius: int | None = None,
    excluded: list[CircleDetection] | None = None,
    target_radius: float | None = None,
) -> CircleDetection:
    """Fit the best new circle near a click, excluding previously detected circles."""
    gray_full = _gray(image)
    height, width = gray_full.shape
    if search_radius is None:
        search_radius = int(np.clip(min(width, height) * 0.11, 80, 420))
    search_radius = max(30, int(search_radius))
    click_x, click_y = click
    x0 = max(0, int(round(click_x)) - search_radius)
    y0 = max(0, int(round(click_y)) - search_radius)
    x1 = min(width, int(round(click_x)) + search_radius + 1)
    y1 = min(height, int(round(click_y)) + search_radius + 1)
    gray = gray_full[y0:y1, x0:x1]
    if min(gray.shape) < 24:
        raise ValueError("点击位置太靠近图像边缘，圆形搜索区域不足。")

    local_click = np.array([click_x - x0, click_y - y0], dtype=np.float64)
    detection_scale = min(1.0, 900.0 / max(gray.shape))
    if detection_scale < 1.0:
        gray = cv2.resize(gray, None, fx=detection_scale, fy=detection_scale, interpolation=cv2.INTER_AREA)
        local_click *= detection_scale
        if target_radius is not None:
            target_radius *= detection_scale
    denoised = cv2.GaussianBlur(gray, (5, 5), 0)
    gradient_x = cv2.Sobel(denoised, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(denoised, cv2.CV_32F, 0, 1, ksize=3)
    gradient_magnitude = cv2.magnitude(gradient_x, gradient_y)
    candidates: list[tuple[float, float, float, float, str]] = []

    edges = cv2.Canny(denoised, 45, 140)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    roi_area = float(gray.shape[0] * gray.shape[1])
    for contour in contours:
        if len(contour) < 18:
            continue
        perimeter = cv2.arcLength(contour, True)
        area = abs(cv2.contourArea(contour))
        if perimeter < 25 or area < 45 or area > roi_area * 0.78:
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        if not 5 <= radius <= min(gray.shape) * 0.48:
            continue
        distance_to_click = float(np.linalg.norm(np.array([cx, cy]) - local_click))
        allowed_center_gap = max(12.0 * detection_scale, radius * (0.22 if target_radius is not None else 0.55))
        if distance_to_click > allowed_center_gap:
            continue
        circularity = float(np.clip(4.0 * math.pi * area / (perimeter * perimeter + 1e-9), 0.0, 1.0))
        fill = float(np.clip(area / (math.pi * radius * radius + 1e-9), 0.0, 1.0))
        coverage, radial_consistency, contrast = _circle_edge_quality(
            denoised, gradient_x, gradient_y, gradient_magnitude, cx, cy, radius
        )
        if coverage < 0.38 or radial_consistency < 0.52 or contrast < 3.0:
            continue
        support = _gradient_circle_support(gradient_magnitude, cx, cy, radius)
        proximity = max(0.0, 1.0 - distance_to_click / max(radius * 1.4, 25.0))
        score = (
            0.20 * circularity
            + 0.12 * fill
            + 0.18 * support
            + 0.14 * proximity
            + 0.22 * coverage
            + 0.14 * radial_consistency
        )
        candidates.append((score, cx, cy, radius, "轮廓圆拟合"))

    if target_radius is None:
        min_radius = max(5, int(min(gray.shape) * 0.025))
        max_radius = max(min_radius + 2, int(min(gray.shape) * 0.46))
    else:
        min_radius = max(5, int(target_radius * 0.72))
        max_radius = max(min_radius + 2, min(int(min(gray.shape) * 0.49), int(target_radius * 1.28)))
    hough = cv2.HoughCircles(
        denoised,
        cv2.HOUGH_GRADIENT,
        dp=1.25,
        minDist=max(16, min_radius * 2),
        param1=120,
        param2=24,
        minRadius=min_radius,
        maxRadius=max_radius,
    )
    if hough is not None:
        for cx, cy, radius in hough[0]:
            distance_to_click = float(np.linalg.norm(np.array([cx, cy]) - local_click))
            allowed_center_gap = max(12.0 * detection_scale, radius * (0.20 if target_radius is not None else 0.50))
            if distance_to_click > allowed_center_gap:
                continue
            coverage, radial_consistency, contrast = _circle_edge_quality(
                denoised,
                gradient_x,
                gradient_y,
                gradient_magnitude,
                float(cx),
                float(cy),
                float(radius),
            )
            if coverage < 0.46 or radial_consistency < 0.58 or contrast < 3.5:
                continue
            support = _gradient_circle_support(gradient_magnitude, float(cx), float(cy), float(radius))
            proximity = max(0.0, 1.0 - distance_to_click / max(float(radius) * 1.35, 25.0))
            # Hough proposals are valuable for incomplete edges, but their center
            # and radius are less precise than a complete closed contour fit.
            score = 0.28 * support + 0.18 * proximity + 0.32 * coverage + 0.22 * radial_consistency
            candidates.append((score, float(cx), float(cy), float(radius), "霍夫圆检测"))

    radial_centers = [local_click]
    radial_centers.extend(np.asarray([candidate[1], candidate[2]], dtype=np.float64) for candidate in candidates[:5])
    distinct_centers: list[np.ndarray] = []
    for center in radial_centers:
        if not any(np.linalg.norm(center - kept) < 4.0 for kept in distinct_centers):
            distinct_centers.append(center)
    for center in distinct_centers:
        candidates.extend(
            _radial_concentric_candidates(
                denoised,
                gradient_x,
                gradient_y,
                gradient_magnitude,
                center,
                min_radius,
                max_radius,
            )
        )

    if not candidates:
        raise ValueError("点击位置附近没有找到圆形边界，请点击零件中心附近并确保边缘清晰。")
    candidates.sort(
        key=lambda item: item[0] + (0.12 if "径向双边缘" in item[4] else 0.0),
        reverse=True,
    )

    # Merge inner/outer edge responses from the same thick printed or machined
    # boundary. Distinct concentric radii remain available as separate results.
    distinct: list[tuple[float, float, float, float, str]] = []
    for candidate in candidates:
        _, cx, cy, radius, method = candidate
        duplicate = False
        for _, kept_x, kept_y, kept_radius, kept_method in distinct:
            center_gap = math.dist((cx, cy), (kept_x, kept_y))
            radius_gap = abs(radius - kept_radius)
            candidate_paired = "径向双边缘" in method
            kept_paired = "径向双边缘" in kept_method
            if candidate_paired and kept_paired:
                radius_tolerance = max(2.5, min(radius, kept_radius) * 0.018)
            elif candidate_paired or kept_paired:
                radius_tolerance = max(8.0, min(radius, kept_radius) * 0.055)
            else:
                radius_tolerance = max(2.5, min(radius, kept_radius) * 0.018)
            if center_gap <= max(4.0, min(radius, kept_radius) * 0.035) and radius_gap <= radius_tolerance:
                duplicate = True
                break
        if not duplicate:
            distinct.append(candidate)

    excluded = excluded or []
    available: list[tuple[float, float, float, float, str]] = []
    for candidate in distinct:
        _, cx, cy, radius, _ = candidate
        global_center = (cx / detection_scale + x0, cy / detection_scale + y0)
        original_radius = radius / detection_scale
        already_measured = any(
            math.dist(global_center, previous.center) <= max(5.0, min(original_radius, previous.radius) * 0.035)
            and abs(original_radius - previous.radius) <= max(2.5, min(original_radius, previous.radius) * 0.018)
            for previous in excluded
        )
        if not already_measured:
            available.append(candidate)

    if not available:
        raise ValueError("该区域内检测到的圆都已测量，请扩大搜索半径或点击其他区域。")

    if target_radius is not None:
        target_radius = max(3.0, float(target_radius))
        ring_candidates = [
            candidate
            for candidate in available
            if abs(candidate[3] - target_radius) <= max(8.0, target_radius * 0.24)
        ]
        if not ring_candidates:
            raise ValueError("红色选择环附近没有找到匹配圆，请用滚轮调整红环大小后重试。")
        available = sorted(
            ring_candidates,
            key=lambda item: abs(item[3] - target_radius) / target_radius - item[0] * 0.12,
        )

    score, cx, cy, radius, method = available[0]
    if score < 0.34:
        raise ValueError("圆形边界置信度不足，请调整光照、对焦或点击位置后重试。")

    return CircleDetection(
        center=(cx / detection_scale + x0, cy / detection_scale + y0),
        radius=radius / detection_scale,
        confidence=float(np.clip(score, 0.0, 0.99)),
        method=method,
    )
