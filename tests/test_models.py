import math

from planevision.models import angle_degrees, arc_geometry, circumcircle, distance, polygon_area, polyline_length


def test_distance() -> None:
    assert distance((0, 0), (3, 4)) == 5


def test_angle() -> None:
    assert math.isclose(angle_degrees((1, 0), (0, 0), (0, 1)), 90.0)
    assert math.isclose(angle_degrees((-1, 0), (0, 0), (1, 0)), 180.0)


def test_circumcircle() -> None:
    center, radius = circumcircle((10, 0), (0, 10), (-10, 0))
    assert math.isclose(center[0], 0.0, abs_tol=1e-9)
    assert math.isclose(center[1], 0.0, abs_tol=1e-9)
    assert math.isclose(radius, 10.0, abs_tol=1e-9)


def test_polyline_length_and_polygon_area() -> None:
    points = [(0, 0), (3, 0), (3, 4)]
    assert polyline_length(points) == 7
    assert polygon_area([(0, 0), (4, 0), (4, 3), (0, 3)]) == 12


def test_semicircle_geometry() -> None:
    center, radius, sweep, arc_length = arc_geometry((-10, 0), (0, -10), (10, 0))
    assert math.isclose(center[0], 0.0, abs_tol=1e-9)
    assert math.isclose(center[1], 0.0, abs_tol=1e-9)
    assert math.isclose(radius, 10.0, abs_tol=1e-9)
    assert math.isclose(abs(sweep), 180.0, abs_tol=1e-9)
    assert math.isclose(arc_length, math.pi * 10.0, abs_tol=1e-9)
