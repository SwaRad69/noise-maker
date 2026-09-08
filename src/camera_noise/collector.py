from __future__ import annotations

import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from camera_noise.backends.base import CameraBackend
from camera_noise.backends.opencv import OpenCVBackend
from camera_noise.models import CaptureConfig, SettingReport
from camera_noise.storage import SessionStore, utc_now_iso, write_json_atomic


@dataclass(frozen=True)
class CaptureResult:
    output_dir: Path
    valid_frame_count: int
    requested_frame_count: int
    complete: bool
    warnings: tuple[str, ...]
    setting_reports: tuple[SettingReport, ...]


def _drop_report(timestamps: np.ndarray, fps: float | None, read_failures: int) -> dict[str, Any]:
    report: dict[str, Any] = {
        "read_failures": read_failures,
        "source_frame_position_gap_count": 0,
        "source_frame_position_missing_estimate": 0,
        "host_timing_gap_count": 0,
        "host_timing_missing_estimate": 0,
        "notes": [
            "A failed read means OpenCV did not deliver a frame; it does not reveal how many hardware frames were lost.",
            "Host timing gaps can be caused by camera loss, buffering, USB delay, or operating-system scheduling.",
        ],
    }
    if len(timestamps) < 2:
        return report

    positions = timestamps["source_frame_position"]
    finite_positions = positions[np.isfinite(positions)]
    if len(finite_positions) >= 2 and np.any(np.diff(finite_positions) > 0):
        deltas = np.diff(finite_positions)
        gaps = deltas[deltas > 1.0]
        report["source_frame_position_gap_count"] = int(len(gaps))
        report["source_frame_position_missing_estimate"] = int(
            np.sum(np.maximum(0, np.rint(gaps).astype(np.int64) - 1))
        )
        report["notes"].append(
            "Backend frame-position gaps are the strongest available OpenCV evidence, but driver semantics vary."
        )
    else:
        report["notes"].append(
            "The backend did not expose a usable increasing frame-position counter."
        )

    if fps is not None and np.isfinite(fps) and fps > 0:
        period_ns = 1_000_000_000.0 / fps
        deltas_ns = np.diff(timestamps["capture_end_monotonic_ns"])
        gaps = deltas_ns[deltas_ns > 1.5 * period_ns]
        report["host_timing_gap_count"] = int(len(gaps))
        report["host_timing_missing_estimate"] = int(
            np.sum(np.maximum(0, np.rint(gaps / period_ns).astype(np.int64) - 1))
        )
        report["timing_reference_fps"] = float(fps)
    else:
        report["notes"].append("No usable FPS readback was available for host timing-gap estimates.")
    return report


def collect(config: CaptureConfig, backend: CameraBackend | None = None) -> CaptureResult:
    config.validate()
    backend = backend or OpenCVBackend(config.backend)
    store = SessionStore(config.output_dir, config.frame_count)
    store.create()
    started = utc_now_iso()
    metadata_path = config.output_dir / "metadata.json"
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "status": "initializing",
        "started_utc": started,
        "data_classification": {
            "kind": "processed_webcam_frames",
            "raw_sensor_data": False,
            "description": (
                "Frames returned by OpenCV after the camera/driver processing pipeline, "
                "normally decoded BGR rather than sensor RAW/Bayer samples."
            ),
        },
        "config": config.to_dict(),
    }
    write_json_atomic(metadata_path, metadata)

    read_failures: list[dict[str, int]] = []
    consecutive_failures = 0
    error: str | None = None
    session_warnings: list[str] = []
    session_info: Any = None
    properties_after_warmup: dict[str, Any] = {}
    properties_at_end: dict[str, Any] = {}
    try:
        with backend.open(config) as session:
            session_info = session.info
            for _ in range(config.warmup_frames):
                session.read()
            properties_after_warmup = session.snapshot_properties()

            while store.valid_count < config.frame_count:
                sample = session.read()
                if sample is None:
                    consecutive_failures += 1
                    read_failures.append(
                        {
                            "after_valid_frame_index": store.valid_count - 1,
                            "host_utc_ns": time.time_ns(),
                            "host_monotonic_ns": time.perf_counter_ns(),
                        }
                    )
                    if consecutive_failures >= config.max_consecutive_read_failures:
                        raise RuntimeError(
                            "Camera exceeded the maximum consecutive read failures "
                            f"({config.max_consecutive_read_failures})"
                        )
                    continue
                consecutive_failures = 0
                store.append(sample)
            properties_at_end = session.snapshot_properties()
    except KeyboardInterrupt:
        error = "KeyboardInterrupt: capture interrupted by operator"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        store.flush()

    timestamps = store.timestamp_view()
    properties = session_info.properties if session_info is not None else {}
    latest_properties = properties_at_end or properties_after_warmup or properties
    actual_fps = latest_properties.get("fps")
    drops = _drop_report(timestamps, actual_fps, len(read_failures))
    if session_info is not None:
        session_warnings.extend(session_info.warnings)
    if drops["host_timing_gap_count"]:
        session_warnings.append(
            "Host timing gaps suggest one or more capture intervals may have been missed; "
            "see dropped_frame_detection for caveats."
        )
    if error:
        session_warnings.append(f"Capture ended early: {error}")

    frame_shape = list(store.frame_shape) if store.frame_shape is not None else None
    metadata.update(
        {
            "status": "complete" if error is None else "partial",
            "completed_utc": utc_now_iso(),
            "error": error,
            "valid_frame_count": store.valid_count,
            "requested_frame_count": config.frame_count,
            "frame_array": {
                "path": "frames.npy" if store.frames is not None else None,
                "allocated_shape": [config.frame_count, *frame_shape] if frame_shape else None,
                "valid_shape": [store.valid_count, *frame_shape] if frame_shape else None,
                "dtype": str(store.frame_dtype) if store.frame_dtype is not None else None,
                "color_order": "BGR" if frame_shape and len(frame_shape) == 3 else "single_channel",
                "lossless_storage": True,
                "note": "Only entries before valid_frame_count are measurements.",
            },
            "timestamps": {
                "path": "timestamps.npy" if store.timestamps is not None else None,
                "host_utc_clock": "time.time_ns",
                "host_monotonic_clock": "time.perf_counter_ns",
                "source_timestamp_note": "Backend value; NaN means unavailable or not meaningful.",
            },
            "camera": session_info.device.to_dict() if session_info is not None else None,
            "camera_properties_after_configuration": properties,
            "camera_properties_after_warmup": properties_after_warmup,
            "camera_properties_at_end": properties_at_end,
            "setting_reports": (
                [item.to_dict() for item in session_info.settings]
                if session_info is not None
                else []
            ),
            "warnings": session_warnings,
            "read_failure_events": read_failures,
            "dropped_frame_detection": drops,
            "software": {
                "python": sys.version,
                "platform": platform.platform(),
                "numpy": np.__version__,
                "opencv": cv2.__version__,
            },
            "dark_frame_protocol": {
                "declared": config.dark_frame,
                "physical_cover_verified_by_software": False,
                "note": (
                    "The operator declared the camera covered; software cannot verify opacity or light leakage."
                    if config.dark_frame
                    else "This session was not declared as a covered-camera dark-frame experiment."
                ),
            },
        }
    )
    write_json_atomic(metadata_path, metadata)
    return CaptureResult(
        output_dir=config.output_dir,
        valid_frame_count=store.valid_count,
        requested_frame_count=config.frame_count,
        complete=error is None,
        warnings=tuple(session_warnings),
        setting_reports=tuple(session_info.settings) if session_info is not None else (),
    )
