from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import time

import cv2
import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot


def camera_device_names() -> list[str]:
    """Return present Windows camera names in SetupAPI enumeration order."""
    if sys.platform != "win32":
        return []

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("ClassGuid", GUID),
            ("DevInst", wintypes.DWORD),
            ("Reserved", ctypes.c_void_p),
        ]

    camera_guid = GUID(
        0xCA3E7AB9,
        0xB4C3,
        0x4AE6,
        (ctypes.c_ubyte * 8)(0x82, 0x51, 0x57, 0x9E, 0xF9, 0x33, 0x89, 0x0F),
    )
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(SP_DEVINFO_DATA),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_ubyte),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    device_set = setupapi.SetupDiGetClassDevsW(ctypes.byref(camera_guid), None, None, 0x00000002)
    invalid_handle = ctypes.c_void_p(-1).value
    if device_set in {None, invalid_handle}:
        return []
    names: list[str] = []
    try:
        index = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(device_set, index, ctypes.byref(info)):
                break
            index += 1
            buffer = ctypes.create_unicode_buffer(512)
            property_type = wintypes.DWORD()
            required_size = wintypes.DWORD()
            ok = setupapi.SetupDiGetDeviceRegistryPropertyW(
                device_set,
                ctypes.byref(info),
                12,  # SPDRP_FRIENDLYNAME
                ctypes.byref(property_type),
                ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                ctypes.sizeof(buffer),
                ctypes.byref(required_size),
            )
            if not ok:
                ok = setupapi.SetupDiGetDeviceRegistryPropertyW(
                    device_set,
                    ctypes.byref(info),
                    0,  # SPDRP_DEVICEDESC
                    ctypes.byref(property_type),
                    ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                    ctypes.sizeof(buffer),
                    ctypes.byref(required_size),
                )
            names.append(buffer.value.strip() if ok and buffer.value.strip() else f"摄像头 {len(names)}")
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(device_set)
    return names


class CameraWorker(QObject):
    frameReady = Signal(object)
    opened = Signal(int, int, float)
    error = Signal(str)
    finished = Signal()

    def __init__(self, index: int, width: int = 3840, height: int = 2160, fps: int = 10) -> None:
        super().__init__()
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self._running = False
        self._capture: cv2.VideoCapture | None = None

    @Slot()
    def run(self) -> None:
        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        capture = cv2.VideoCapture()
        try:
            opened = capture.open(
                self.index,
                backend,
                [
                    cv2.CAP_PROP_FOURCC,
                    fourcc,
                    cv2.CAP_PROP_FRAME_WIDTH,
                    self.width,
                    cv2.CAP_PROP_FRAME_HEIGHT,
                    self.height,
                    cv2.CAP_PROP_FPS,
                    self.fps,
                ],
            )
        except cv2.error:
            opened = False
        if not opened:
            capture.release()
            capture = cv2.VideoCapture(self.index, backend)
            capture.set(cv2.CAP_PROP_FOURCC, fourcc)
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            capture.set(cv2.CAP_PROP_FPS, self.fps)
        self._capture = capture
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not capture.isOpened():
            self.error.emit(f"无法打开摄像头 {self.index}，请检查占用状态或更换设备编号。")
            self.finished.emit()
            return
        self._running = True
        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = float(capture.get(cv2.CAP_PROP_FPS))
        self.opened.emit(actual_width, actual_height, actual_fps)
        failures = 0
        last_emit = 0.0
        emit_interval = 1.0 / max(1, self.fps)
        while self._running:
            ok, frame = capture.read()
            if not ok or frame is None:
                failures += 1
                if failures > 20:
                    self.error.emit("摄像头连续读取失败，连接已停止。")
                    break
                QThread.msleep(20)
                continue
            failures = 0
            now = time.perf_counter()
            if now - last_emit >= emit_interval:
                preview = frame
                if frame.shape[1] > 1440:
                    preview_scale = 1440.0 / frame.shape[1]
                    preview = cv2.resize(
                        frame,
                        None,
                        fx=preview_scale,
                        fy=preview_scale,
                        interpolation=cv2.INTER_AREA,
                    )
                self.frameReady.emit((np.ascontiguousarray(frame), np.ascontiguousarray(preview)))
                last_emit = now
        capture.release()
        self._capture = None
        self.finished.emit()

    @Slot()
    def stop(self) -> None:
        self._running = False


class CameraController(QObject):
    frameReady = Signal(object)
    opened = Signal(int, int, float)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.thread: QThread | None = None
        self.worker: CameraWorker | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.isRunning()

    def start(self, index: int, width: int = 3840, height: int = 2160, fps: int = 10) -> None:
        self.stop()
        self.thread = QThread(self)
        self.worker = CameraWorker(index, width, height, fps)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.frameReady.connect(self.frameReady)
        self.worker.opened.connect(self.opened)
        self.worker.error.connect(self.error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._on_thread_finished)
        self.thread.start()

    def stop(self) -> None:
        if self.worker is not None:
            self.worker.stop()
        if self.thread is not None and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(1800)
        self.worker = None
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None

    def _on_thread_finished(self) -> None:
        self.worker = None
        self.thread = None
        self.stopped.emit()
