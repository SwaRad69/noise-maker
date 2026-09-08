from __future__ import annotations

import math
import time
from typing import Any

import cv2

from camera_noise.backends.base import CameraBackend, CameraSession
from camera_noise.models import (
    CameraDevice,
    CapturedFrame,
    CaptureConfig,
    SessionInfo,
    SettingReport,
)


_BACKENDS = {
    "any": cv2.CAP_ANY,
    "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
    "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY),
    "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
}


def backend_code(name: str) -> int:
    try:
        return _BACKENDS[name.lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported OpenCV backend: {name}") from exc


def _finite(value: float) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None


def _close(actual: float | None, requested: float, tolerance: float = 0.02) -> bool:
    if actual is None:
        return False
    return abs(actual - requested) <= max(tolerance, abs(requested) * tolerance)


def _fourcc(value: float) -> str | None:
    if not math.isfinite(value) or value <= 0:
        return None
    code = int(value)
    text = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
    return text if text.isprintable() else None


class OpenCVSession(CameraSession):
    def __init__(self, capture: Any, config: CaptureConfig, device: CameraDevice):
        self._capture = capture
        self._config = config
        self.info = SessionInfo(device=device)
        self._configure()

    def _read_property(self, prop: int) -> float | None:
        try:
            return _finite(self._capture.get(prop))
        except (cv2.error, TypeError, ValueError):
            return None

    def _set_numeric(
        self,
        name: str,
        prop: int,
        requested: float,
        *,
        detail: str = "",
        tolerance: float = 0.02,
    ) -> SettingReport:
        before = self._read_property(prop)
        try:
            set_returned = bool(self._capture.set(prop, requested))
        except (cv2.error, TypeError, ValueError):
            set_returned = False
        after = self._read_property(prop)
        if set_returned and _close(after, requested, tolerance):
            status = "accepted"
        elif _close(after, requested, tolerance):
            status = "unverifiable"
        elif after is None:
            status = "unverifiable" if set_returned else "unsupported"
        else:
            status = "rejected"
        return SettingReport(name, requested, before, after, set_returned, status, detail)

    def _disable_auto_exposure(self) -> SettingReport:
        prop = cv2.CAP_PROP_AUTO_EXPOSURE
        before = self._read_property(prop)
        actual_backend = self.info.device.backend.lower()
        if "dshow" in actual_backend:
            backend = "dshow"
        elif "v4l" in actual_backend:
            backend = "v4l2"
        elif "msmf" in actual_backend:
            backend = "msmf"
        else:
            backend = self._config.backend.lower()
        candidates = {
            "dshow": [0.25],
            "v4l2": [1.0],
            "msmf": [0.0, 0.25, 1.0],
            "any": [0.25, 1.0, 0.0],
        }[backend]
        attempts: list[dict[str, Any]] = []
        accepted = False
        after: float | None = before
        any_set = False
        for value in candidates:
            try:
                result = bool(self._capture.set(prop, value))
            except (cv2.error, TypeError, ValueError):
                result = False
            any_set = any_set or result
            after = self._read_property(prop)
            matched = _close(after, value)
            attempts.append({"value": value, "set_returned": result, "readback": after})
            if result and matched:
                accepted = True
                break
        matching_readback = any(
            item["readback"] is not None and _close(item["readback"], item["value"])
            for item in attempts
        )
        if accepted:
            status = "accepted"
        elif matching_readback:
            status = "unverifiable"
        elif after is None:
            status = "unverifiable" if any_set else "unsupported"
        else:
            status = "rejected"
        detail = (
            "OpenCV auto-exposure values are backend-specific. Attempts: "
            f"{attempts}. A matching readback is evidence of driver acceptance, "
            "not proof that all internal exposure control stopped."
        )
        return SettingReport(
            "auto_exposure", "manual/off", before, after, any_set, status, detail
        )

    def _configure(self) -> None:
        cfg = self._config
        reports = self.info.settings

        if cfg.width is not None:
            reports.append(self._set_numeric("frame_width", cv2.CAP_PROP_FRAME_WIDTH, cfg.width))
            reports.append(self._set_numeric("frame_height", cv2.CAP_PROP_FRAME_HEIGHT, cfg.height))
        if cfg.fps is not None:
            reports.append(self._set_numeric("fps", cv2.CAP_PROP_FPS, cfg.fps, tolerance=0.05))

        if cfg.disable_auto_exposure:
            reports.append(self._disable_auto_exposure())
        if cfg.exposure is not None:
            reports.append(
                self._set_numeric(
                    "exposure",
                    cv2.CAP_PROP_EXPOSURE,
                    cfg.exposure,
                    detail="Exposure units and sign are backend/driver-specific.",
                    tolerance=0.01,
                )
            )

        if cfg.disable_auto_white_balance:
            reports.append(
                self._set_numeric(
                    "auto_white_balance",
                    cv2.CAP_PROP_AUTO_WB,
                    0.0,
                    detail="A zero readback indicates that the driver accepted the request.",
                    tolerance=0.01,
                )
            )
        if cfg.white_balance is not None:
            reports.append(
                self._set_numeric(
                    "white_balance_temperature",
                    cv2.CAP_PROP_WB_TEMPERATURE,
                    cfg.white_balance,
                    detail="Units are normally kelvin but remain driver-specific.",
                )
            )
        if cfg.gain is not None:
            reports.append(
                self._set_numeric(
                    "gain",
                    cv2.CAP_PROP_GAIN,
                    cfg.gain,
                    detail="Gain units are backend/driver-specific.",
                )
            )
        else:
            self.info.warnings.append(
                "OpenCV has no portable auto-gain switch. Gain may still be automatic; "
                "provide --gain to request and verify a fixed value where supported."
            )

        self.info.properties = self._properties()
        for report in reports:
            if report.status != "accepted":
                self.info.warnings.append(
                    f"Camera setting '{report.name}' was {report.status}; requested="
                    f"{report.requested!r}, readback={report.after!r}."
                )

    def _properties(self) -> dict[str, Any]:
        width = self._read_property(cv2.CAP_PROP_FRAME_WIDTH)
        height = self._read_property(cv2.CAP_PROP_FRAME_HEIGHT)
        fps = self._read_property(cv2.CAP_PROP_FPS)
        return {
            "frame_width": int(width) if width is not None else None,
            "frame_height": int(height) if height is not None else None,
            "fps": fps,
            "exposure": self._read_property(cv2.CAP_PROP_EXPOSURE),
            "gain": self._read_property(cv2.CAP_PROP_GAIN),
            "auto_exposure": self._read_property(cv2.CAP_PROP_AUTO_EXPOSURE),
            "auto_white_balance": self._read_property(cv2.CAP_PROP_AUTO_WB),
            "white_balance_temperature": self._read_property(cv2.CAP_PROP_WB_TEMPERATURE),
            "fourcc": _fourcc(self._capture.get(cv2.CAP_PROP_FOURCC)),
            "backend_name": self._capture.getBackendName(),
        }

    def snapshot_properties(self) -> dict[str, Any]:
        return self._properties()

    def read(self) -> CapturedFrame | None:
        start_ns = time.perf_counter_ns()
        ok, image = self._capture.read()
        end_ns = time.perf_counter_ns()
        utc_ns = time.time_ns()
        if not ok or image is None:
            return None
        return CapturedFrame(
            image=image,
            capture_start_monotonic_ns=start_ns,
            capture_end_monotonic_ns=end_ns,
            capture_end_utc_ns=utc_ns,
            source_timestamp_ms=self._read_property(cv2.CAP_PROP_POS_MSEC),
            source_frame_position=self._read_property(cv2.CAP_PROP_POS_FRAMES),
        )

    def close(self) -> None:
        self._capture.release()


class OpenCVBackend(CameraBackend):
    def __init__(self, backend: str = "any"):
        self.backend = backend.lower()
        self.api_preference = backend_code(self.backend)

    def _open_capture(self, device_id: int) -> Any:
        return cv2.VideoCapture(device_id, self.api_preference)

    def discover(self, max_devices: int = 10) -> list[CameraDevice]:
        devices: list[CameraDevice] = []
        for device_id in range(max_devices):
            capture = self._open_capture(device_id)
            try:
                if not capture.isOpened():
                    continue
                ok, frame = capture.read()
                if not ok or frame is None:
                    continue
                height, width = frame.shape[:2]
                fps = _finite(capture.get(cv2.CAP_PROP_FPS))
                devices.append(
                    CameraDevice(
                        device_id=device_id,
                        name=f"Camera {device_id}",
                        backend=capture.getBackendName(),
                        width=width,
                        height=height,
                        fps=fps,
                        fourcc=_fourcc(capture.get(cv2.CAP_PROP_FOURCC)),
                    )
                )
            finally:
                capture.release()
        return devices

    def open(self, config: CaptureConfig) -> OpenCVSession:
        capture = self._open_capture(config.camera_id)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(
                f"Could not open camera {config.camera_id} with backend {self.backend!r}"
            )
        backend_name = capture.getBackendName()
        device = CameraDevice(config.camera_id, f"Camera {config.camera_id}", backend_name)
        return OpenCVSession(capture, config, device)
