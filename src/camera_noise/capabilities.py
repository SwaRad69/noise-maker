from __future__ import annotations

import json
import math
import os
import platform
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import cv2
import numpy as np

from camera_noise.backends.opencv import backend_code
from camera_noise.storage import write_json_atomic


DEFAULT_RESOLUTIONS = (
    (320, 240),
    (640, 480),
    (800, 600),
    (1280, 720),
    (1280, 960),
    (1920, 1080),
)
DEFAULT_FRAME_RATES = (15.0, 30.0)
DEFAULT_PIXEL_FORMATS = ("MJPG", "YUY2", "NV12")


@dataclass(frozen=True)
class CapabilityConfig:
    camera_id: int = 0
    backends: tuple[str, ...] = ("dshow", "msmf")
    output_dir: Path = Path(".")
    device_name: str | None = None
    device_identifier: str | None = None
    resolutions: tuple[tuple[int, int], ...] = DEFAULT_RESOLUTIONS
    frame_rates: tuple[float, ...] = DEFAULT_FRAME_RATES
    pixel_formats: tuple[str, ...] = DEFAULT_PIXEL_FORMATS
    validation_frames: int = 5
    warmup_frames: int = 2
    max_validation_modes_per_backend: int = 6
    open_attempts: int = 8
    open_retry_delay_seconds: float = 1.0
    read_attempts: int = 5
    read_retry_delay_seconds: float = 0.1
    release_settle_seconds: float = 0.15

    def validate(self) -> None:
        if self.camera_id < 0:
            raise ValueError("camera_id cannot be negative")
        if not self.backends:
            raise ValueError("at least one backend is required")
        if not self.resolutions:
            raise ValueError("at least one resolution is required")
        if any(width <= 0 or height <= 0 for width, height in self.resolutions):
            raise ValueError("resolutions must be positive")
        if not self.frame_rates or any(fps <= 0 for fps in self.frame_rates):
            raise ValueError("frame rates must be positive")
        if self.validation_frames < 2:
            raise ValueError("validation_frames must be at least 2")
        if self.warmup_frames < 0:
            raise ValueError("warmup_frames cannot be negative")
        if self.max_validation_modes_per_backend <= 0:
            raise ValueError("max_validation_modes_per_backend must be positive")
        if self.open_attempts <= 0 or self.read_attempts <= 0:
            raise ValueError("open_attempts and read_attempts must be positive")
        if min(
            self.open_retry_delay_seconds,
            self.read_retry_delay_seconds,
            self.release_settle_seconds,
        ) < 0:
            raise ValueError("retry and release delays cannot be negative")


@dataclass(frozen=True)
class CapabilityResult:
    json_path: Path
    markdown_path: Path
    report: dict[str, Any]


CaptureFactory = Callable[[int, int], Any]
HardwareProvider = Callable[[CapabilityConfig], dict[str, Any]]


_COMPRESSED_FORMATS = {
    "MJPG": "Motion JPEG",
    "JPEG": "JPEG",
    "H264": "H.264/AVC",
    "HEVC": "H.265/HEVC",
    "H265": "H.265/HEVC",
    "MP4V": "MPEG-4 Part 2",
}
_UNCOMPRESSED_FORMATS = {
    "YUY2": "packed 4:2:2 YUV",
    "YUYV": "packed 4:2:2 YUV",
    "UYVY": "packed 4:2:2 YUV",
    "NV12": "semi-planar 4:2:0 YUV",
    "I420": "planar 4:2:0 YUV",
    "YV12": "planar 4:2:0 YUV",
    "RGB3": "packed RGB24",
    "BGR3": "packed BGR24",
    "RGB4": "packed RGB32",
    "BGR4": "packed BGR32",
    "GREY": "8-bit monochrome",
    "GRAY": "8-bit monochrome",
    "Y800": "8-bit monochrome",
    "Y8  ": "8-bit monochrome",
    "Y16 ": "16-bit monochrome container",
    "P010": "10-bit YUV in 16-bit containers",
    "P012": "12-bit YUV in 16-bit containers",
}
_RAW_NAMES = {
    "BA81",
    "BGGR",
    "GBRG",
    "GRBG",
    "RGGB",
    "BY8 ",
    "BG10",
    "GB10",
    "BA10",
    "RG10",
    "BG12",
    "GB12",
    "BA12",
    "RG12",
    "BG16",
    "GB16",
    "BA16",
    "RG16",
}


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _fourcc(value: Any) -> str | None:
    number = _finite(value)
    if number is None or number <= 0:
        return None
    code = int(number)
    text = "".join(chr((code >> (8 * index)) & 0xFF) for index in range(4))
    return text if text.isprintable() else None


def _fourcc_code(text: str) -> int:
    normalized = text.upper().ljust(4)[:4]
    return cv2.VideoWriter_fourcc(*normalized)


def classify_pixel_format(fourcc: str | None) -> dict[str, Any]:
    normalized = fourcc.upper().ljust(4)[:4] if fourcc else None
    if normalized in _COMPRESSED_FORMATS:
        compression = "compressed"
        description = _COMPRESSED_FORMATS[normalized]
    elif normalized in _UNCOMPRESSED_FORMATS or normalized in _RAW_NAMES:
        compression = "uncompressed"
        description = (_UNCOMPRESSED_FORMATS | {name: "Bayer/raw-like format name" for name in _RAW_NAMES})[
            normalized
        ]
    else:
        compression = "unknown"
        description = "OpenCV/driver format name was absent or not in the conservative format table"

    raw_like = normalized in _RAW_NAMES if normalized else False
    component_bits: int | None = None
    if normalized:
        if normalized in {"P010", "BG10", "GB10", "BA10", "RG10"}:
            component_bits = 10
        elif normalized in {"P012", "BG12", "GB12", "BA12", "RG12"}:
            component_bits = 12
        elif normalized in {"Y16 ", "BG16", "GB16", "BA16", "RG16"}:
            component_bits = 16
        elif normalized in _COMPRESSED_FORMATS or normalized in _UNCOMPRESSED_FORMATS or raw_like:
            component_bits = 8
    return {
        "fourcc": fourcc,
        "normalized_fourcc": normalized.rstrip() if normalized else None,
        "description": description,
        "compression": compression,
        "reported_component_bit_depth": component_bits,
        "raw_or_bayer_name": raw_like,
        "interpretation": (
            "A RAW/Bayer-like name is evidence only if the requested format is honored and the delivered "
            "representation is consistent with it. Uncompressed transport alone does not establish ISP bypass."
        ),
    }


def _safe_get(capture: Any, prop: int) -> float | None:
    try:
        return _finite(capture.get(prop))
    except (cv2.error, TypeError, ValueError):
        return None


def _safe_set(capture: Any, prop: int, value: float) -> bool:
    try:
        return bool(capture.set(prop, value))
    except (cv2.error, TypeError, ValueError):
        return False


def _backend_name(capture: Any, fallback: str) -> str:
    try:
        return str(capture.getBackendName())
    except (cv2.error, AttributeError, TypeError):
        return fallback


def _release_capture(capture: Any, settle_seconds: float) -> None:
    try:
        capture.release()
    finally:
        if settle_seconds > 0:
            time.sleep(settle_seconds)


def _open_capture(
    capture_factory: CaptureFactory,
    config: CapabilityConfig,
    backend: str,
) -> tuple[Any | None, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, config.open_attempts + 1):
        started = time.perf_counter()
        capture: Any | None = None
        exception: str | None = None
        try:
            capture = capture_factory(config.camera_id, backend_code(backend))
            opened = bool(capture.isOpened())
        except (cv2.error, OSError, RuntimeError, TypeError, ValueError) as exc:
            opened = False
            exception = f"{type(exc).__name__}: {exc}"
        attempts.append(
            {
                "attempt": attempt,
                "opened": opened,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "exception": exception,
            }
        )
        if opened and capture is not None:
            return capture, attempts
        if capture is not None:
            _release_capture(capture, config.release_settle_seconds)
        if attempt < config.open_attempts and config.open_retry_delay_seconds > 0:
            time.sleep(config.open_retry_delay_seconds)
    return None, attempts


def _read_frame(
    capture: Any,
    attempts: int,
    retry_delay_seconds: float,
) -> tuple[bool, np.ndarray | None, list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        exception: str | None = None
        try:
            ok, frame = capture.read()
        except (cv2.error, OSError, RuntimeError, TypeError, ValueError) as exc:
            ok, frame = False, None
            exception = f"{type(exc).__name__}: {exc}"
        succeeded = bool(ok and frame is not None)
        diagnostics.append(
            {
                "attempt": attempt,
                "succeeded": succeeded,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "exception": exception,
            }
        )
        if succeeded:
            return True, frame, diagnostics
        if attempt < attempts and retry_delay_seconds > 0:
            time.sleep(retry_delay_seconds)
    return False, None, diagnostics


def _capture_properties(capture: Any) -> dict[str, Any]:
    width = _safe_get(capture, cv2.CAP_PROP_FRAME_WIDTH)
    height = _safe_get(capture, cv2.CAP_PROP_FRAME_HEIGHT)
    codec_pixel_format = _fourcc(
        _safe_get(capture, getattr(cv2, "CAP_PROP_CODEC_PIXEL_FORMAT", cv2.CAP_PROP_FOURCC))
    )
    return {
        "width": int(round(width)) if width is not None else None,
        "height": int(round(height)) if height is not None else None,
        "fps": _safe_get(capture, cv2.CAP_PROP_FPS),
        "fourcc": _fourcc(_safe_get(capture, cv2.CAP_PROP_FOURCC)),
        "codec_pixel_format": codec_pixel_format,
        "format_mat_type": _safe_get(capture, cv2.CAP_PROP_FORMAT),
        "convert_rgb": _safe_get(capture, cv2.CAP_PROP_CONVERT_RGB),
        "backend_name": _backend_name(capture, "unknown"),
    }


def _nearly_equal(actual: float | None, requested: float, relative: float = 0.03) -> bool:
    if actual is None:
        return False
    return abs(actual - requested) <= max(0.05, abs(requested) * relative)


def _numeric_request_status(
    actual: float | None,
    requested: float,
    *,
    relative: float,
    nonpositive_is_unverifiable: bool = False,
) -> str:
    if actual is None or (nonpositive_is_unverifiable and actual <= 0):
        return "unverifiable"
    return "honored" if _nearly_equal(actual, requested, relative=relative) else "mismatch"


def _format_request_status(actual: str | None, requested: str | None) -> str:
    if requested is None:
        return "not_requested"
    if actual is None:
        return "unverifiable"
    return "honored" if actual == requested else "mismatch"


def _mode_key(mode: dict[str, Any]) -> tuple[Any, ...]:
    actual = mode["actual"]
    return (actual.get("width"), actual.get("height"), actual.get("fps"), actual.get("fourcc"))


def _request_mode(
    capture_factory: CaptureFactory,
    config: CapabilityConfig,
    backend: str,
    width: int,
    height: int,
    fps: float,
    requested_fourcc: str | None,
) -> dict[str, Any]:
    requested = {"width": width, "height": height, "fps": fps, "fourcc": requested_fourcc}
    capture, open_attempts = _open_capture(capture_factory, config, backend)
    if capture is None:
        return {
            "backend": backend,
            "requested": requested,
            "opened": False,
            "open_attempts": open_attempts,
            "setter_results": {},
            "read_succeeded": False,
            "read_attempts": [],
            "actual": {},
            "honored": False,
            "usable": False,
            "verification_status": "open_failed",
            "fallback_detected": False,
            "failure": (
                f"VideoCapture.isOpened() remained false after {len(open_attempts)} isolated attempts"
            ),
        }
    try:
        setters: dict[str, bool] = {}
        if requested_fourcc is not None:
            setters["fourcc"] = _safe_set(capture, cv2.CAP_PROP_FOURCC, _fourcc_code(requested_fourcc))
        setters["width"] = _safe_set(capture, cv2.CAP_PROP_FRAME_WIDTH, width)
        setters["height"] = _safe_set(capture, cv2.CAP_PROP_FRAME_HEIGHT, height)
        setters["fps"] = _safe_set(capture, cv2.CAP_PROP_FPS, fps)
        ok, frame, read_attempts = _read_frame(
            capture,
            config.read_attempts,
            config.read_retry_delay_seconds,
        )
        actual = _capture_properties(capture)
        width_status = _numeric_request_status(actual["width"], width, relative=0.0)
        height_status = _numeric_request_status(actual["height"], height, relative=0.0)
        fps_status = _numeric_request_status(
            actual["fps"],
            fps,
            relative=0.05,
            nonpositive_is_unverifiable=True,
        )
        format_status = _format_request_status(actual["fourcc"], requested_fourcc)
        statuses = (width_status, height_status, fps_status, format_status)
        known_fallback = "mismatch" in statuses
        fully_verified = all(status in {"honored", "not_requested"} for status in statuses)
        usable = bool(ok and frame is not None and not known_fallback)
        honored = bool(ok and frame is not None and fully_verified)
        actual["pixel_format_classification"] = classify_pixel_format(actual["fourcc"])
        return {
            "backend": backend,
            "requested": requested,
            "opened": True,
            "open_attempts": open_attempts,
            "setter_results": setters,
            "read_succeeded": bool(ok and frame is not None),
            "read_attempts": read_attempts,
            "actual": actual,
            "honored": honored,
            "usable": usable,
            "verification_status": (
                "fully_verified"
                if honored
                else "usable_with_unverifiable_readback"
                if usable
                else "known_fallback"
                if ok and frame is not None and known_fallback
                else "no_frame"
            ),
            "fallback_detected": bool(ok and frame is not None and known_fallback),
            "checks": {
                "width": width_status,
                "height": height_status,
                "fps": fps_status,
                "pixel_format": format_status,
            },
            "delivered_frame": _frame_description(frame) if ok and frame is not None else None,
        }
    finally:
        _release_capture(capture, config.release_settle_seconds)


def _frame_description(frame: np.ndarray) -> dict[str, Any]:
    channels = 1 if frame.ndim == 2 else int(frame.shape[2]) if frame.ndim == 3 else None
    return {
        "shape": list(frame.shape),
        "dtype": str(frame.dtype),
        "dtype_bits": int(frame.dtype.itemsize * 8),
        "channels": channels,
        "contiguous": bool(frame.flags.c_contiguous),
        "minimum": float(np.min(frame)),
        "maximum": float(np.max(frame)),
    }


_CONTROL_SPECS = (
    ("exposure", "CAP_PROP_EXPOSURE", (-1.0, 1.0)),
    ("auto_exposure", "CAP_PROP_AUTO_EXPOSURE", (0.0, 0.25, 0.75, 1.0)),
    ("gain", "CAP_PROP_GAIN", (1.0, 8.0)),
    ("auto_gain", None, ()),
    ("white_balance_temperature", "CAP_PROP_WB_TEMPERATURE", (100.0, 500.0)),
    ("auto_white_balance", "CAP_PROP_AUTO_WB", (0.0, 1.0)),
    ("brightness", "CAP_PROP_BRIGHTNESS", (1.0, 8.0)),
    ("contrast", "CAP_PROP_CONTRAST", (1.0, 8.0)),
    ("saturation", "CAP_PROP_SATURATION", (1.0, 8.0)),
    ("gamma", "CAP_PROP_GAMMA", (1.0, 10.0)),
    ("sharpness", "CAP_PROP_SHARPNESS", (1.0, 8.0)),
    ("focus", "CAP_PROP_FOCUS", (1.0, 5.0)),
    ("auto_focus", "CAP_PROP_AUTOFOCUS", (0.0, 1.0)),
    ("backlight_compensation", "CAP_PROP_BACKLIGHT", (0.0, 1.0)),
    ("frame_rate", "CAP_PROP_FPS", (1.0, 5.0)),
)


def _candidate_values(current: float, offsets: Iterable[float]) -> list[float]:
    candidates: list[float] = []
    for offset in offsets:
        if offset in (0.0, 0.25, 0.75, 1.0) and current in (0.0, 0.25, 0.75, 1.0):
            candidate = offset
        else:
            candidate = current + offset
        if not _nearly_equal(candidate, current, relative=0.0001):
            candidates.append(candidate)
    return candidates


def _probe_controls(capture: Any, backend: str) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    for name, constant_name, offsets in _CONTROL_SPECS:
        prop = getattr(cv2, constant_name, None) if constant_name else None
        if prop is None:
            controls.append(
                {
                    "name": name,
                    "backend": backend,
                    "opencv_property": constant_name,
                    "current": None,
                    "minimum": None,
                    "maximum": None,
                    "step": None,
                    "default": None,
                    "readable": False,
                    "change_verified": False,
                    "setter_returned": None,
                    "readback_after_test": None,
                    "range_source": (
                        "OpenCV has no portable auto-gain property"
                        if name == "auto_gain"
                        else "not present in this OpenCV build"
                    ),
                }
            )
            continue
        current = _safe_get(capture, prop)
        setter_current = _safe_set(capture, prop, current) if current is not None else False
        change_verified = False
        changed_to: float | None = None
        observed_increment: float | None = None
        any_setter = setter_current
        last_readback = _safe_get(capture, prop)
        if current is not None:
            for candidate in _candidate_values(current, offsets):
                setter = _safe_set(capture, prop, candidate)
                any_setter = any_setter or setter
                readback = _safe_get(capture, prop)
                last_readback = readback
                if readback is not None and not _nearly_equal(readback, current, relative=0.0001):
                    change_verified = True
                    changed_to = readback
                    observed_increment = abs(readback - current)
                    break
            _safe_set(capture, prop, current)
        restored = _safe_get(capture, prop)
        controls.append(
            {
                "name": name,
                "backend": backend,
                "opencv_property": constant_name,
                "current": current,
                "minimum": None,
                "maximum": None,
                "step": None,
                "default": None,
                "readable": current is not None,
                "change_verified": change_verified,
                "setter_returned": any_setter,
                "readback_after_test": last_readback,
                "changed_readback": changed_to,
                "observed_change_increment": observed_increment,
                "restore_readback": restored,
                "restored": current is not None and _nearly_equal(restored, current, relative=0.0001),
                "range_source": (
                    "OpenCV exposes value get/set but not native min/max/step/default; null values are not inferred"
                ),
                "interpretation": (
                    "Setter success plus readback change shows that the interface accepted a changed value; "
                    "it does not prove the hardware or all automatic ISP logic applied it exactly."
                ),
            }
        )
    return controls


def _validate_mode(
    capture_factory: CaptureFactory,
    config: CapabilityConfig,
    backend: str,
    mode: dict[str, Any],
    samples_dir: Path,
    sample_number: int,
) -> dict[str, Any]:
    actual_mode = mode["actual"]
    capture, open_attempts = _open_capture(capture_factory, config, backend)
    requested_fourcc = actual_mode.get("fourcc")
    if capture is None:
        return {
            "status": "failed",
            "failure": f"camera could not be reopened after {len(open_attempts)} attempts",
            "backend": backend,
            "open_attempts": open_attempts,
        }
    try:
        if requested_fourcc:
            _safe_set(capture, cv2.CAP_PROP_FOURCC, _fourcc_code(requested_fourcc))
        if actual_mode.get("width"):
            _safe_set(capture, cv2.CAP_PROP_FRAME_WIDTH, actual_mode["width"])
        if actual_mode.get("height"):
            _safe_set(capture, cv2.CAP_PROP_FRAME_HEIGHT, actual_mode["height"])
        if actual_mode.get("fps"):
            _safe_set(capture, cv2.CAP_PROP_FPS, actual_mode["fps"])

        convert_setter = _safe_set(capture, cv2.CAP_PROP_CONVERT_RGB, 0.0)
        format_setter = _safe_set(capture, cv2.CAP_PROP_FORMAT, -1.0)
        for _ in range(config.warmup_frames):
            capture.read()
        frames: list[np.ndarray] = []
        read_failures = 0
        for _ in range(config.validation_frames):
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(frame.copy())
            else:
                read_failures += 1
        properties = _capture_properties(capture)
        if not frames:
            return {
                "status": "failed",
                "failure": "no validation frames were delivered",
                "backend": backend,
                "properties": properties,
            }
        same_shape = all(frame.shape == frames[0].shape and frame.dtype == frames[0].dtype for frame in frames)
        stack = np.stack(frames) if same_shape else None
        duplicate_pairs = 0
        changed_pairs = 0
        if stack is not None and len(stack) > 1:
            for first, second in zip(stack[:-1], stack[1:]):
                if np.array_equal(first, second):
                    duplicate_pairs += 1
                else:
                    changed_pairs += 1
        samples_dir.mkdir(parents=True, exist_ok=True)
        safe_format = (requested_fourcc or "unknown").strip().replace(" ", "_")
        sample_path = samples_dir / (
            f"{backend}_{actual_mode.get('width')}x{actual_mode.get('height')}_"
            f"{safe_format}_{sample_number}.npy"
        )
        save_status = "saved"
        save_error: str | None = None
        try:
            if stack is None:
                raise ValueError("frames did not have a consistent shape/dtype")
            np.save(sample_path, stack, allow_pickle=False)
            restored = np.load(sample_path, allow_pickle=False)
            roundtrip_exact = bool(np.array_equal(restored, stack) and restored.dtype == stack.dtype)
        except (OSError, ValueError) as exc:
            save_status = "failed"
            save_error = str(exc)
            roundtrip_exact = False
        delivered = _frame_description(frames[0])
        validation_checks = {
            "width": _numeric_request_status(
                properties.get("width"), actual_mode.get("width"), relative=0.0
            ),
            "height": _numeric_request_status(
                properties.get("height"), actual_mode.get("height"), relative=0.0
            ),
            "fps": (
                "not_requested"
                if actual_mode.get("fps") is None
                else _numeric_request_status(
                    properties.get("fps"),
                    actual_mode["fps"],
                    relative=0.05,
                    nonpositive_is_unverifiable=True,
                )
            ),
            "pixel_format": _format_request_status(properties.get("fourcc"), requested_fourcc),
        }
        known_validation_fallback = "mismatch" in validation_checks.values()
        mode_still_usable = not known_validation_fallback
        mode_still_honored = all(
            status in {"honored", "not_requested"} for status in validation_checks.values()
        )
        native_like_delivery = bool(
            delivered["channels"] == 1
            and (properties.get("convert_rgb") == 0.0 or properties.get("format_mat_type") == -1.0)
        )
        return {
            "status": "complete",
            "backend": backend,
            "open_attempts": open_attempts,
            "mode": actual_mode,
            "frame_count_requested": config.validation_frames,
            "frame_count_received": len(frames),
            "read_failures": read_failures,
            "consistent_shape_and_dtype": same_shape,
            "frame": delivered,
            "consecutive_duplicate_pairs": duplicate_pairs,
            "consecutive_changed_pairs": changed_pairs,
            "duplicate_pair_fraction": duplicate_pairs / max(1, len(frames) - 1),
            "convert_rgb_disable_setter_returned": convert_setter,
            "raw_format_request_setter_returned": format_setter,
            "native_transport_preservation_possible": native_like_delivery,
            "native_transport_interpretation": (
                "A single-channel/packed delivery with conversion disabled may preserve the backend stream "
                "more closely, but still does not establish sensor RAW or ISP bypass."
                if native_like_delivery
                else "OpenCV delivered an application image representation; native transport bytes were not demonstrated."
            ),
            "properties_after_read": properties,
            "mode_checks": validation_checks,
            "mode_still_honored": mode_still_honored,
            "mode_still_usable": mode_still_usable,
            "silent_fallback_detected": known_validation_fallback,
            "sample_file": str(sample_path),
            "sample_save_status": save_status,
            "sample_save_error": save_error,
            "npy_roundtrip_exact": roundtrip_exact,
        }
    finally:
        _release_capture(capture, config.release_settle_seconds)


def _windows_hardware_info(config: CapabilityConfig) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": "Windows registry camera enumeration (read-only)",
        "devices": [],
        "selected": None,
        "camera_privacy_consent": _windows_camera_consent(),
        "limitations": [],
    }
    if platform.system() != "Windows":
        result["limitations"].append("Windows registry camera inspection is only available on Windows.")
        return result
    try:
        import winreg
    except ImportError:
        result["limitations"].append("Python winreg support is unavailable.")
        return result

    devices: list[dict[str, Any]] = []
    usb_path = r"SYSTEM\CurrentControlSet\Enum\USB"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, usb_path) as usb_key:
            vendor_count = winreg.QueryInfoKey(usb_key)[0]
            for vendor_index in range(vendor_count):
                vendor_name = winreg.EnumKey(usb_key, vendor_index)
                if not vendor_name.upper().startswith("VID_"):
                    continue
                with winreg.OpenKey(usb_key, vendor_name) as vendor_key:
                    instance_count = winreg.QueryInfoKey(vendor_key)[0]
                    for instance_index in range(instance_count):
                        instance_name = winreg.EnumKey(vendor_key, instance_index)
                        with winreg.OpenKey(vendor_key, instance_name) as instance_key:
                            device_class = _registry_value(instance_key, "Class")
                            class_guid = str(_registry_value(instance_key, "ClassGUID") or "").lower()
                            if device_class != "Camera" and class_guid != "{ca3e7ab9-b4c3-4ae6-8251-579ef933890f}":
                                continue
                            driver_reference = _registry_value(instance_key, "Driver")
                            item: dict[str, Any] = {
                                "instance_id": f"USB\\{vendor_name}\\{instance_name}",
                                "name": _registry_display_value(
                                    _registry_value(instance_key, "FriendlyName")
                                    or _registry_value(instance_key, "DeviceDesc")
                                ),
                                "manufacturer": _registry_display_value(
                                    _registry_value(instance_key, "Mfg")
                                ),
                                "driver_registry_reference": driver_reference,
                                "config_flags": _registry_value(instance_key, "ConfigFlags"),
                                "present": True,
                            }
                            if driver_reference:
                                driver_path = rf"SYSTEM\CurrentControlSet\Control\Class\{driver_reference}"
                                try:
                                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, driver_path) as driver_key:
                                        item.update(
                                            {
                                                "driver_inf": _registry_value(driver_key, "InfPath"),
                                                "driver_provider": _registry_value(driver_key, "ProviderName"),
                                                "driver_version": _registry_value(driver_key, "DriverVersion"),
                                                "driver_date": _registry_value(driver_key, "DriverDate"),
                                                "driver_description": _registry_value(driver_key, "DriverDesc"),
                                            }
                                        )
                                except OSError:
                                    pass
                            devices.append(item)
    except OSError as exc:
        result["limitations"].append(f"Windows camera registry query failed: {exc}")
    result["devices"] = devices
    selected: dict[str, Any] | None = None
    if config.device_identifier:
        selected = next(
            (item for item in devices if config.device_identifier.lower() in item.get("instance_id", "").lower()),
            None,
        )
    if selected is None and config.device_name:
        selected = next(
            (item for item in devices if config.device_name.lower() in item.get("name", "").lower()),
            None,
        )
    if selected is None and len(devices) == 1:
        selected = devices[0]
    result["selected"] = selected
    if selected is None:
        result["limitations"].append(
            "The OpenCV numeric index could not be authoritatively mapped to a Windows PnP device."
        )
    return result


def _registry_value(key: Any, name: str) -> Any:
    try:
        return key.QueryValueEx(name)[0]
    except AttributeError:
        try:
            import winreg

            return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None
    except OSError:
        return None


def _registry_display_value(value: Any) -> Any:
    if not isinstance(value, str) or ";" not in value:
        return value
    return value.rsplit(";", 1)[-1]


def _windows_camera_consent() -> dict[str, Any]:
    values: dict[str, Any] = {"current_user": None, "local_machine": None}
    if platform.system() != "Windows":
        return values
    try:
        import winreg
    except ImportError:
        return values
    path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
    for label, root in (("current_user", winreg.HKEY_CURRENT_USER), ("local_machine", winreg.HKEY_LOCAL_MACHINE)):
        try:
            with winreg.OpenKey(root, path) as key:
                values[label] = winreg.QueryValueEx(key, "Value")[0]
        except OSError:
            values[label] = None
    return values


def _control_by_name(controls: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in controls if item["name"] == name), None)


def _mode_score(mode: dict[str, Any], controls: list[dict[str, Any]], validation: dict[str, Any] | None) -> tuple[int, list[str]]:
    actual = mode["actual"]
    classification = actual.get("pixel_format_classification", {})
    score = 0
    reasons: list[str] = []
    if classification.get("raw_or_bayer_name"):
        score += 100
        reasons.append("RAW/Bayer-like stream name was actually negotiated")
    if classification.get("compression") == "uncompressed":
        score += 40
        reasons.append("uncompressed transport format")
    elif classification.get("compression") == "compressed":
        score -= 20
        reasons.append("compressed transport format")
    bit_depth = classification.get("reported_component_bit_depth")
    if bit_depth:
        score += bit_depth * 2
        reasons.append(f"reported/format-derived component depth {bit_depth} bits")
    exposure = _control_by_name(controls, "exposure")
    gain = _control_by_name(controls, "gain")
    if exposure and exposure.get("change_verified"):
        score += 12
        reasons.append("exposure change and readback verified")
    if gain and gain.get("change_verified"):
        score += 10
        reasons.append("gain change and readback verified")
    if validation and validation.get("status") == "complete":
        score += 8
        reasons.append("tiny validation capture completed")
        if validation.get("duplicate_pair_fraction") == 0:
            score += 8
            reasons.append("no consecutive duplicates in the tiny validation capture")
        if validation.get("native_transport_preservation_possible"):
            score += 12
            reasons.append("OpenCV delivered a packed/single-channel representation with conversion disabled")
    return score, reasons


def _rank_modes(backends: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for backend in backends:
        validations = backend.get("validation_results", [])
        for mode in backend.get("discovered_modes", []):
            validation = next(
                (item for item in validations if item.get("mode") and _mode_key({"actual": item["mode"]}) == _mode_key(mode)),
                None,
            )
            score, reasons = _mode_score(mode, backend.get("controls", []), validation)
            ranked.append(
                {
                    "backend": backend["backend"],
                    "mode": mode["actual"],
                    "score": score,
                    "reasons": reasons,
                    "validation": validation,
                }
            )
    ranked.sort(
        key=lambda item: (
            item["score"],
            item["mode"].get("pixel_format_classification", {}).get(
                "reported_component_bit_depth"
            )
            or 0,
        ),
        reverse=True,
    )
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked


def _discover_backend(
    config: CapabilityConfig,
    backend: str,
    capture_factory: CaptureFactory,
    samples_dir: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "backend": backend,
        "api_preference": backend_code(backend),
        "opencv_backend_compiled": backend_code(backend) != cv2.CAP_ANY or backend == "any",
        "opened": False,
        "actual_backend_name": None,
        "default_mode": None,
        "controls": [],
        "mode_negotiation_tests": [],
        "discovered_modes": [],
        "validation_results": [],
        "limitations": [],
    }
    capture, open_attempts = _open_capture(capture_factory, config, backend)
    result["open_attempts"] = open_attempts
    if capture is None:
        result["status"] = "open_failed"
        result["failure_stage"] = "open"
        result["failure"] = (
            f"VideoCapture.isOpened() remained false after {len(open_attempts)} isolated attempts"
        )
        result["limitations"].append(
            f"Camera {config.camera_id} could not be opened through {backend}; no stream/control claims were inferred."
        )
        return result
    try:
        result["opened"] = True
        result["actual_backend_name"] = _backend_name(capture, backend)
        ok, default_frame, read_attempts = _read_frame(
            capture,
            config.read_attempts,
            config.read_retry_delay_seconds,
        )
        result["default_read_attempts"] = read_attempts
        result["default_read_succeeded"] = bool(ok and default_frame is not None)
        result["status"] = "working" if result["default_read_succeeded"] else "opened_no_frame"
        result["failure_stage"] = None if result["default_read_succeeded"] else "first_frame"
        result["failure"] = (
            None
            if result["default_read_succeeded"]
            else f"camera opened but no frame arrived after {len(read_attempts)} read attempts"
        )
        result["default_mode"] = _capture_properties(capture)
        result["default_mode"]["pixel_format_classification"] = classify_pixel_format(
            result["default_mode"].get("fourcc")
        )
        if default_frame is not None:
            result["default_delivered_frame"] = _frame_description(default_frame)
        result["controls"] = _probe_controls(capture, backend)
    finally:
        _release_capture(capture, config.release_settle_seconds)

    tests: list[dict[str, Any]] = []
    requested_formats: tuple[str | None, ...] = (None, *config.pixel_formats)
    for width, height in config.resolutions:
        for fps in config.frame_rates:
            for requested_format in requested_formats:
                tests.append(
                    _request_mode(
                        capture_factory,
                        config,
                        backend,
                        width,
                        height,
                        fps,
                        requested_format,
                    )
                )
    result["mode_negotiation_tests"] = tests
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for test in tests:
        if test.get("read_succeeded") and test.get("usable"):
            unique.setdefault(_mode_key(test), test)
    result["discovered_modes"] = list(unique.values())

    candidates = sorted(
        result["discovered_modes"],
        key=lambda item: (
            classify_pixel_format(item["actual"].get("fourcc"))["raw_or_bayer_name"],
            classify_pixel_format(item["actual"].get("fourcc"))["compression"] == "uncompressed",
            classify_pixel_format(item["actual"].get("fourcc"))["reported_component_bit_depth"] or 0,
            -((item["actual"].get("width") or 0) * (item["actual"].get("height") or 0)),
        ),
        reverse=True,
    )[: config.max_validation_modes_per_backend]
    for index, candidate in enumerate(candidates, start=1):
        result["validation_results"].append(
            _validate_mode(capture_factory, config, backend, candidate, samples_dir, index)
        )
    result["capability_scope"] = (
        "Modes listed as discovered delivered frames with no known requested-versus-actual mismatch. Fields whose "
        "backend readback is unavailable remain explicitly unverifiable. This is not a native DirectShow/Media "
        "Foundation advertisement dump, so vendor-specific modes outside the grid remain unknown."
    )
    return result


def _conclusion(backends: list[dict[str, Any]], ranked: list[dict[str, Any]]) -> dict[str, Any]:
    modes = [item for backend in backends for item in backend.get("discovered_modes", [])]
    raw = [
        item
        for item in modes
        if item.get("actual", {}).get("pixel_format_classification", {}).get("raw_or_bayer_name")
    ]
    uncompressed = [
        item
        for item in modes
        if item.get("actual", {}).get("pixel_format_classification", {}).get("compression") == "uncompressed"
    ]
    compressed = [
        item
        for item in modes
        if item.get("actual", {}).get("pixel_format_classification", {}).get("compression") == "compressed"
    ]
    if raw:
        category = "A"
        answer = "RAW/Bayer appears accessible"
        explanation = (
            "At least one RAW/Bayer-like FOURCC was honored. The delivered representation and vendor documentation "
            "must still be checked before claiming complete ISP bypass."
        )
    elif uncompressed:
        category = "B"
        answer = "Uncompressed but still processed video is accessible"
        explanation = (
            "At least one uncompressed mode was actually negotiated, but no RAW/Bayer mode was exposed. "
            "The camera may still follow sensor -> ADC -> ISP/firmware -> YUV -> USB before software receives it."
        )
    elif compressed:
        category = "C"
        answer = "Only heavily processed/compressed video is accessible"
        explanation = "Only compressed modes were actually negotiated through the tested interfaces."
    else:
        category = "D"
        answer = "The available interfaces do not provide enough information to determine the processing level"
        explanation = (
            "No tested backend produced an honored mode, so this run cannot characterize the accessible stream."
        )
    return {
        "category": category,
        "answer": answer,
        "explanation": explanation,
        "raw_bayer_exposed": bool(raw),
        "raw_bayer_statement": (
            "RAW/Bayer-like access was observed through a tested interface."
            if raw
            else "RAW/Bayer access was not exposed through the tested interface."
        ),
        "hardware_raw_support_statement": (
            "This does not establish whether the camera hardware, proprietary software, firmware, or an undocumented "
            "interface supports RAW."
        ),
        "recommended_mode": ranked[0] if ranked else None,
    }


def discover_capabilities(
    config: CapabilityConfig,
    *,
    capture_factory: CaptureFactory | None = None,
    hardware_provider: HardwareProvider | None = None,
) -> CapabilityResult:
    config.validate()
    capture_factory = capture_factory or (lambda camera_id, api: cv2.VideoCapture(camera_id, api))
    hardware_provider = hardware_provider or _windows_hardware_info
    config.output_dir.mkdir(parents=True, exist_ok=True)
    samples_dir = config.output_dir / "camera_capability_samples"
    # Some Windows camera drivers briefly reject stream opens after a PnP query.
    # Finish all OpenCV access before collecting device/driver metadata.
    backend_reports = [
        _discover_backend(config, backend, capture_factory, samples_dir) for backend in config.backends
    ]
    hardware = hardware_provider(config)
    historical_evidence = _historical_project_evidence()
    ranked = _rank_modes(backend_reports)
    conclusion = _conclusion(backend_reports, ranked)
    highest_depth = max(
        (
            item["mode"].get("pixel_format_classification", {}).get("reported_component_bit_depth") or 0
            for item in ranked
        ),
        default=0,
    )
    unknowns = [
        "OpenCV does not expose native DirectShow/Media Foundation control min/max/step/default metadata.",
        "The active mode probe covers the configured resolution/FPS/FOURCC grid, not every vendor-specific media type.",
        "An observed YUV/RGB stream does not reveal which ISP, denoising, temporal filtering, tone mapping, or firmware stages ran.",
        "Internal ADC/sensor bit depth cannot be inferred from the application stream bit depth.",
        "Proprietary manufacturer software, undocumented extension controls, firmware interfaces, and hardware-specific methods were not tested.",
        "A tiny validation capture can reveal obvious repetition but cannot establish long-term buffering or temporal-filter behavior.",
        "When Windows detects a started camera but OpenCV cannot open it, this layer cannot distinguish a physical/e-shutter, exclusive use, or a driver/runtime failure.",
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_utc": datetime.now().astimezone().isoformat(),
        "purpose": "Camera capability and format discovery; no entropy analysis was performed.",
        "device": {
            "camera_index": config.camera_id,
            "requested_name": config.device_name,
            "requested_identifier": config.device_identifier,
            "hardware": hardware,
        },
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "opencv": cv2.__version__,
            "opencv_videoio_backends": [
                cv2.videoio_registry.getBackendName(code)
                for code in cv2.videoio_registry.getBackends()
            ],
        },
        "probe_configuration": {
            **asdict(config),
            "output_dir": str(config.output_dir),
        },
        "backends": backend_reports,
        "historical_project_evidence": historical_evidence,
        "candidate_ranking": ranked,
        "highest_accessible_reported_component_bit_depth": highest_depth or None,
        "conclusion": conclusion,
        "unknowns_and_limitations": unknowns,
        "scientific_interpretation": {
            "directly_observed": (
                "Only backend opens, property readbacks, requested-versus-actual modes, delivered arrays, and tiny-frame comparisons."
            ),
            "meaning": (
                "An honored uncompressed format means software can receive an uncompressed representation through that interface."
            ),
            "does_not_mean": (
                "It does not prove raw sensor measurements, ISP bypass, sensor-level access, true randomness, quantum randomness, or cryptographic security."
            ),
            "conceptual_pipeline": [
                "sensor",
                "analog electronics",
                "ADC",
                "ISP",
                "firmware",
                "USB",
                "Windows driver/backend",
                "application",
            ],
        },
        "next_experiments": _next_experiments(conclusion, historical_evidence),
    }
    json_path = config.output_dir / "camera_capabilities.json"
    markdown_path = config.output_dir / "camera_capabilities.md"
    write_json_atomic(json_path, report)
    _write_text_atomic(markdown_path, render_markdown(report))
    return CapabilityResult(json_path=json_path, markdown_path=markdown_path, report=report)


def _next_experiments(
    conclusion: dict[str, Any], historical_evidence: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    recommended = conclusion.get("recommended_mode")
    if recommended:
        mode = recommended["mode"]
        primary = (
            f"Capture a controlled exposure/gain sweep using {recommended['backend']} at "
            f"{mode.get('width')}x{mode.get('height')} {mode.get('fps')} fps {mode.get('fourcc')}, "
            "with automatic controls disabled where verified, preserving the least-converted OpenCV representation."
        )
    else:
        latest = historical_evidence[-1] if historical_evidence else None
        if latest and latest.get("fourcc"):
            primary = (
                "Restore Camera 0 access, then verify the previously successful DirectShow configuration "
                f"{latest.get('width')}x{latest.get('height')} {latest.get('fourcc')} at exposure "
                f"{latest.get('exposure')}. Rerun capability discovery before starting a new noise experiment."
            )
        else:
            primary = (
                "Restore Camera 0 access (privacy shutter/OS permission/exclusive-use application), then rerun this "
                "capability command before selecting a sensor-noise experiment."
            )
    return {
        "primary": primary,
        "secondary": [
            "Compare the best uncompressed mode with the best compressed mode at matched exposure, gain, resolution, and FPS.",
            "Investigate manufacturer-specific tools or documented extension controls only if they explicitly expose RAW/Bayer or higher-bit-depth streams.",
        ],
    }


def _historical_project_evidence() -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    experiments_root = Path("experiments")
    if not experiments_root.is_dir():
        return evidence
    for metadata_path in sorted(experiments_root.glob("*/metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        camera = metadata.get("camera") or {}
        props = metadata.get("camera_properties_after_configuration") or {}
        if str(camera.get("backend", "")).upper() != "DSHOW":
            continue
        if not props.get("fourcc") and not props.get("frame_width"):
            continue
        evidence.append(
            {
                "session": str(metadata_path.parent),
                "backend": camera.get("backend"),
                "camera_id": camera.get("device_id"),
                "width": props.get("frame_width"),
                "height": props.get("frame_height"),
                "fps": props.get("fps"),
                "fourcc": props.get("fourcc"),
                "exposure": props.get("exposure"),
                "gain": props.get("gain"),
                "valid_frame_count": metadata.get("valid_frame_count"),
                "status": metadata.get("status"),
            }
        )
    return evidence


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _display(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: tuple[str, ...], rows: Iterable[Iterable[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_display(value) for value in row) + " |")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    device = report["device"]
    hardware = device["hardware"]
    selected = hardware.get("selected") or {}
    backends = report["backends"]
    modes = [
        (backend, mode)
        for backend in backends
        for mode in backend.get("discovered_modes", [])
    ]
    controls = [
        control
        for backend in backends
        for control in backend.get("controls", [])
    ]
    negotiation = [
        test
        for backend in backends
        for test in backend.get("mode_negotiation_tests", [])
    ]
    conclusion = report["conclusion"]
    lines = [
        "# Camera Capability Report",
        "",
        "This report covers capability discovery only. No entropy, randomness, bit extraction, or Qiskit analysis was performed.",
        "",
        "## 1. Device",
        "",
        f"- OpenCV camera index: `{device['camera_index']}`",
        f"- Device name: `{_display(selected.get('name') or device.get('requested_name'))}`",
        f"- Device identifier: `{_display(selected.get('instance_id') or device.get('requested_identifier'))}`",
        "",
        "## 2. Hardware/Driver Information",
        "",
        *_table(
            ("Field", "Measured value", "Source"),
            (
                ("Manufacturer", selected.get("manufacturer"), hardware.get("source")),
                (
                    "Device presence",
                    selected.get("status")
                    or ("present" if selected.get("present") else None),
                    hardware.get("source"),
                ),
                ("Driver INF", selected.get("driver_inf"), hardware.get("source")),
                ("Driver original name", selected.get("driver_original_name"), hardware.get("source")),
                ("Driver provider", selected.get("driver_provider"), hardware.get("source")),
                ("Driver version", selected.get("driver_version"), hardware.get("source")),
                (
                    "Current-user camera consent",
                    hardware.get("camera_privacy_consent", {}).get("current_user"),
                    "Windows CapabilityAccessManager registry",
                ),
                (
                    "Machine camera consent",
                    hardware.get("camera_privacy_consent", {}).get("local_machine"),
                    "Windows CapabilityAccessManager registry",
                ),
            ),
        ),
        "",
        "## 3. Backends Tested",
        "",
        *_table(
            (
                "Backend",
                "Status",
                "Opened Camera 0",
                "Open attempts",
                "Actual backend",
                "Default mode",
                "Default read",
                "Failure",
            ),
            (
                (
                    item["backend"],
                    item.get("status"),
                    item["opened"],
                    _attempt_text(item.get("open_attempts", [])),
                    item.get("actual_backend_name"),
                    _mode_text(item.get("default_mode")),
                    item.get("default_read_succeeded"),
                    item.get("failure"),
                )
                for item in backends
            ),
        ),
        "",
        "## 4. Supported Video Modes",
        "",
        "These are modes actually honored from the configured probe grid, not an exhaustive native driver advertisement dump.",
        "",
        *_table(
            (
                "Backend",
                "Width",
                "Height",
                "FPS",
                "FOURCC",
                "Opened",
                "Usable",
                "Fully verified",
                "Verification",
                "Delivered array",
            ),
            (
                (
                    backend["backend"],
                    mode["actual"].get("width"),
                    mode["actual"].get("height"),
                    mode["actual"].get("fps"),
                    mode["actual"].get("fourcc"),
                    mode.get("opened"),
                    mode.get("usable"),
                    mode.get("honored"),
                    mode.get("verification_status"),
                    json.dumps(mode.get("delivered_frame"), sort_keys=True),
                )
                for backend, mode in modes
            ),
        ),
        "",
        "### Historical project evidence (not a fresh capability result)",
        "",
        "The current run's open failures are kept separate from earlier completed captures. These records demonstrate prior access, but do not prove that the same modes are available at this moment.",
        "",
        *_table(
            ("Session", "Backend", "Mode", "FOURCC", "Exposure", "Gain", "Frames", "Status"),
            (
                (
                    item.get("session"),
                    item.get("backend"),
                    f"{item.get('width')}x{item.get('height')} @ {item.get('fps')} fps",
                    item.get("fourcc"),
                    item.get("exposure"),
                    item.get("gain"),
                    item.get("valid_frame_count"),
                    item.get("status"),
                )
                for item in report.get("historical_project_evidence", [])
            ),
        ),
        "",
        "## 5. Pixel Formats",
        "",
        *_table(
            ("Backend", "FOURCC", "Description", "RAW/Bayer-like name", "Observed meaning"),
            (
                (
                    backend["backend"],
                    mode["actual"].get("fourcc"),
                    mode["actual"]["pixel_format_classification"].get("description"),
                    mode["actual"]["pixel_format_classification"].get("raw_or_bayer_name"),
                    "Negotiated stream format only; sensor/ISP path remains unknown",
                )
                for backend, mode in modes
            ),
        ),
        "",
        "## 6. Compression",
        "",
        "Uncompressed video does NOT automatically mean raw sensor data. A valid path may still be `sensor -> ISP -> YUV -> USB`.",
        "",
        *_table(
            ("Backend", "Mode", "Classification", "Evidence"),
            (
                (
                    backend["backend"],
                    _mode_text(mode["actual"]),
                    mode["actual"]["pixel_format_classification"].get("compression"),
                    mode["actual"]["pixel_format_classification"].get("description"),
                )
                for backend, mode in modes
            ),
        ),
        "",
        "## 7. Bit Depth",
        "",
        f"Highest accessible reported/format-derived component bit depth: `{_display(report.get('highest_accessible_reported_component_bit_depth'))}` bits.",
        "",
        "This is the exposed stream/component depth, not an inference about the internal ADC or sensor precision.",
        "",
        *_table(
            ("Backend", "Mode", "Component bits", "Application dtype bits"),
            (
                (
                    backend["backend"],
                    _mode_text(mode["actual"]),
                    mode["actual"]["pixel_format_classification"].get("reported_component_bit_depth"),
                    (mode.get("delivered_frame") or {}).get("dtype_bits"),
                )
                for backend, mode in modes
            ),
        ),
        "",
        "## 8. Camera Controls",
        "",
        "Native min/max/step/default values are `unknown` because OpenCV does not expose them. Change verification used setter calls, readback, and restoration.",
        "",
        *_table(
            ("Backend", "Control", "Current", "Min", "Max", "Step", "Default", "Setter", "Change verified", "Readback", "Restored"),
            (
                (
                    item["backend"],
                    item["name"],
                    item.get("current"),
                    item.get("minimum"),
                    item.get("maximum"),
                    item.get("step"),
                    item.get("default"),
                    item.get("setter_returned"),
                    item.get("change_verified"),
                    item.get("readback_after_test"),
                    item.get("restored"),
                )
                for item in controls
            ),
        ),
        "",
        "A successful setter/readback is evidence of interface acceptance, not proof that the hardware applied the value exactly or that all automatic processing stopped.",
        "",
        "## 9. RAW/Bayer Availability",
        "",
        f"**{conclusion['raw_bayer_statement']}**",
        "",
        conclusion["hardware_raw_support_statement"],
        "",
        "## 10. Mode Negotiation",
        "",
        *_table(
            (
                "Backend",
                "Requested",
                "Actual",
                "Read",
                "Usable",
                "Fully verified",
                "Verification",
                "Silent fallback",
            ),
            (
                (
                    item["backend"],
                    _mode_text(item.get("requested")),
                    _mode_text(item.get("actual")),
                    item.get("read_succeeded"),
                    item.get("usable"),
                    item.get("honored"),
                    item.get("verification_status"),
                    item.get("fallback_detected"),
                )
                for item in negotiation
            ),
        ),
        "",
        "## 11. Backend Comparison",
        "",
        *_table(
            ("Backend", "Works", "Honored modes", "Validated modes", "Verified exposure", "Verified gain"),
            (
                (
                    item["backend"],
                    item["opened"],
                    len(item.get("discovered_modes", [])),
                    sum(result.get("status") == "complete" for result in item.get("validation_results", [])),
                    bool((_control_by_name(item.get("controls", []), "exposure") or {}).get("change_verified")),
                    bool((_control_by_name(item.get("controls", []), "gain") or {}).get("change_verified")),
                )
                for item in backends
            ),
        ),
        "",
        "## 12. Processing/ISP Considerations",
        "",
        "Directly observed: backend open/read results, FOURCC readbacks, dimensions/FPS, OpenCV arrays, control readbacks, and tiny-frame duplication checks.",
        "",
        "Likely for ordinary YUV/RGB webcam modes: sensor and analog stages feed an ADC, followed by some camera ISP/firmware processing before USB and Windows delivery.",
        "",
        "Unknown: demosaicing location, denoising, temporal filtering, black-level correction, tone mapping, sharpening, compression details, internal precision, and whether proprietary paths bypass any stage.",
        "",
        "Observed `YUY2` would mean software can receive uncompressed YUV through that interface. It would not prove raw sensor measurements.",
        "",
        "## 13. Candidate Least-Processed Modes",
        "",
        *_table(
            ("Rank", "Backend", "Mode", "Score", "Evidence-based reasons"),
            (
                (
                    item["rank"],
                    item["backend"],
                    _mode_text(item["mode"]),
                    item["score"],
                    "; ".join(item["reasons"]),
                )
                for item in report["candidate_ranking"]
            ),
        ),
        "",
        "## 14. Recommended Configuration",
        "",
        _recommended_text(conclusion.get("recommended_mode")),
        "",
        f"Lowest-level classification: **{conclusion['category']} - {conclusion['answer']}**.",
        "",
        conclusion["explanation"],
        "",
        "Primary next experiment: " + report["next_experiments"]["primary"],
        "",
        "Secondary experiments:",
        "",
        *[f"- {item}" for item in report["next_experiments"]["secondary"]],
        "",
        "## 15. Unknowns and Limitations",
        "",
        *[f"- {item}" for item in report["unknowns_and_limitations"]],
        "",
        "### Confirmed",
        "",
        "Only values in the tables that came from successful opens, readbacks, honored requests, or delivered validation frames.",
        "",
        "### Unknown",
        "",
        "Anything available only through proprietary software, undocumented controls, firmware, or hardware-specific interfaces, plus all unobserved internal sensor/ISP details.",
        "",
        "No claims are made here about entropy, true randomness, quantum randomness, or cryptographic security.",
        "",
    ]
    return "\n".join(lines)


def _mode_text(mode: dict[str, Any] | None) -> str:
    if not mode:
        return "unknown"
    return f"{mode.get('width')}x{mode.get('height')} @ {mode.get('fps')} fps {mode.get('fourcc')}"


def _attempt_text(attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return "none"
    return "; ".join(
        f"{item.get('attempt')}:{'open' if item.get('opened') else 'failed'} "
        f"({item.get('elapsed_seconds')} s)"
        for item in attempts
    )


def _recommended_text(recommended: dict[str, Any] | None) -> str:
    if not recommended:
        return "No camera mode can be recommended from this run because no tested mode was successfully honored."
    mode = recommended["mode"]
    return (
        f"Use `{recommended['backend']}` with `{mode.get('width')}x{mode.get('height')}` at "
        f"`{mode.get('fps')}` fps and FOURCC `{mode.get('fourcc')}`. "
        "Disable automatic exposure/white balance and fix gain only where the control table shows verified readback."
    )
