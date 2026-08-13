import cv2
import numpy as np
import pytest

from planevision.vision import (
    CircleDetection,
    detect_40mm_ruler,
    detect_metric_ruler,
    detect_angle_candidates_near,
    detect_circle_near,
    detect_line_near,
    project_point_to_line,
    snap_point_near,
)


def _synthetic_ruler() -> np.ndarray:
    image = np.full((900, 1600, 3), 210, dtype=np.uint8)
    cv2.rectangle(image, (230, 180), (1370, 750), (40, 40, 40), 4)
    start_x, end_x, baseline_y = 500, 1100, 560
    cv2.line(image, (start_x, baseline_y), (end_x, baseline_y), (20, 20, 20), 3)
    for index in range(41):
        x = round(start_x + (end_x - start_x) * index / 40)
        tick = 34 if index % 10 == 0 else (25 if index % 5 == 0 else 16)
        cv2.line(image, (x, baseline_y), (x, baseline_y - tick), (20, 20, 20), 2)
    return image


def _synthetic_metric_ruler(length_mm: int, vertical: bool = False) -> np.ndarray:
    image = np.full((900, 1600, 3), 210, dtype=np.uint8)
    start_x, end_x, baseline_y = 300, 1300, 470
    cv2.line(image, (start_x, baseline_y), (end_x, baseline_y), (20, 20, 20), 3)
    for index in range(length_mm + 1):
        x = round(start_x + (end_x - start_x) * index / length_mm)
        tick = 42 if index % 10 == 0 else (30 if index % 5 == 0 else 20)
        cv2.line(image, (x, baseline_y), (x, baseline_y - tick), (20, 20, 20), 2)
    return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE) if vertical else image


def test_detect_40mm_ruler() -> None:
    calibration = detect_40mm_ruler(_synthetic_ruler())
    assert abs(calibration.pixel_length - 600) < 8
    assert abs(calibration.mm_per_pixel - 40 / 600) < 0.001


@pytest.mark.parametrize(
    ("length_mm", "vertical"),
    [(10, False), (20, True), (30, False), (40, False)],
)
def test_detect_common_metric_ruler(length_mm: int, vertical: bool) -> None:
    calibration = detect_metric_ruler(_synthetic_metric_ruler(length_mm, vertical))
    assert calibration.reference_mm == float(length_mm)
    assert abs(calibration.pixel_length - 1000) < 12
    assert abs(calibration.mm_per_pixel - length_mm / 1000) < 0.001


def test_detect_clicked_circle() -> None:
    image = np.full((700, 1000, 3), 190, dtype=np.uint8)
    cv2.circle(image, (270, 330), 85, (25, 25, 25), 5)
    cv2.circle(image, (730, 330), 125, (25, 25, 25), 5)
    found = detect_circle_near(image, (720, 340), 220)
    assert abs(found.center[0] - 730) < 5
    assert abs(found.center[1] - 330) < 5
    assert abs(found.radius - 125) < 6


def test_detect_next_concentric_circle_without_repeating() -> None:
    image = np.full((700, 700, 3), 190, dtype=np.uint8)
    cv2.circle(image, (350, 350), 75, (20, 20, 20), 5)
    cv2.circle(image, (350, 350), 145, (20, 20, 20), 5)
    first = detect_circle_near(image, (350, 350), 220)
    second = detect_circle_near(image, (350, 350), 220, excluded=[first])
    radii = sorted([first.radius, second.radius])
    assert abs(radii[0] - 75) < 8
    assert abs(radii[1] - 145) < 8
    assert abs(first.radius - second.radius) > 40


def test_target_radius_selects_outer_circle() -> None:
    image = np.full((800, 800, 3), 190, dtype=np.uint8)
    cv2.circle(image, (400, 400), 70, (20, 20, 20), 5)
    cv2.circle(image, (400, 400), 235, (20, 20, 20), 5)
    found = detect_circle_near(image, (400, 400), 340, target_radius=235)
    assert abs(found.center[0] - 400) < 7
    assert abs(found.center[1] - 400) < 7
    assert abs(found.radius - 235) < 10


def test_detect_close_concentric_circles_without_excluding_second() -> None:
    image = np.full((700, 700, 3), 190, dtype=np.uint8)
    cv2.circle(image, (350, 350), 140, (20, 20, 20), 3)
    cv2.circle(image, (350, 350), 148, (20, 20, 20), 3)
    first = detect_circle_near(image, (350, 350), 240, target_radius=140)
    second = detect_circle_near(image, (350, 350), 240, excluded=[first], target_radius=148)
    assert abs(first.radius - second.radius) > 3.0
    assert max(first.radius, second.radius) > 144.0


@pytest.mark.parametrize("thickness", [2, 3, 5, 8, 12])
def test_single_thick_circle_does_not_repeat_as_inner_or_outer_edge(thickness: int) -> None:
    image = np.full((700, 700, 3), 190, dtype=np.uint8)
    cv2.circle(image, (350, 350), 145, (20, 20, 20), thickness)
    first = detect_circle_near(image, (350, 350), 240, target_radius=145)
    with pytest.raises(ValueError):
        detect_circle_near(image, (350, 350), 240, excluded=[first], target_radius=145)


def test_textured_blank_does_not_produce_circle() -> None:
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 12, (700, 700)).astype(np.float32)
    texture = cv2.GaussianBlur(noise, (0, 0), 2.2)
    image = np.clip(155 + texture, 0, 255).astype(np.uint8)
    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    with pytest.raises(ValueError):
        detect_circle_near(image, (350, 350), 300, target_radius=210)


def test_snap_point_prefers_nearby_corner() -> None:
    image = np.full((500, 700, 3), 220, dtype=np.uint8)
    cv2.rectangle(image, (180, 140), (520, 390), (25, 25, 25), 5)
    found = snap_point_near(image, (190, 150), 45)
    assert np.linalg.norm(np.asarray(found.point) - np.asarray((180, 140))) < 8


def test_detect_nearest_line_and_projection() -> None:
    image = np.full((500, 700, 3), 220, dtype=np.uint8)
    cv2.line(image, (90, 310), (610, 310), (20, 20, 20), 5)
    line = detect_line_near(image, (360, 300), 130)
    assert abs(line.start[1] - 310) < 8
    foot = project_point_to_line((350, 150), line)
    assert abs(foot[1] - 310) < 8


def test_detect_angle_from_nearest_intersecting_edges() -> None:
    image = np.full((600, 800, 3), 220, dtype=np.uint8)
    cv2.line(image, (170, 300), (630, 300), (20, 20, 20), 5)
    cv2.line(image, (400, 90), (400, 510), (20, 20, 20), 5)
    candidates = detect_angle_candidates_near(image, (405, 305), 180)
    assert candidates
    first = candidates[0]
    assert np.linalg.norm(np.asarray(first.points[1]) - np.asarray((400, 300))) < 12
