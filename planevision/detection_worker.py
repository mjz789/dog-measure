from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from .models import Point
from .vision import CircleDetection, detect_circle_near


class DetectionSignals(QObject):
    completed = Signal(object, object)
    failed = Signal(str, object)


@dataclass(slots=True)
class CircleDetectionRequest:
    image: np.ndarray
    click: Point
    search_radius: int
    target_radius: float
    excluded: list[CircleDetection]


class CircleDetectionTask(QRunnable):
    def __init__(self, request: CircleDetectionRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = DetectionSignals()

    @Slot()
    def run(self) -> None:
        try:
            found = detect_circle_near(
                self.request.image,
                self.request.click,
                self.request.search_radius,
                excluded=self.request.excluded,
                target_radius=self.request.target_radius,
            )
        except ValueError as error:
            self.signals.failed.emit(str(error), self.request.click)
        except Exception as error:  # pragma: no cover - defensive worker boundary
            self.signals.failed.emit(f"圆形检测失败：{error}", self.request.click)
        else:
            self.signals.completed.emit(found, self.request.click)
