from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from camera_noise.backends.base import CameraBackend, CameraSession
from camera_noise.collector import _drop_report, collect
from camera_noise.models import (
    CameraDevice,
    CapturedFrame,
    CaptureConfig,
    SessionInfo,
    SettingReport,
)
from camera_noise.storage import TIMESTAMP_DTYPE
from camera_noise.backends.opencv import _finite


class FakeSession(CameraSession):
    def __init__(self, frames: list[np.ndarray | None]):
        self.frames = iter(frames)
        self.counter = 0
        self.info = SessionInfo(
            device=CameraDevice(2, "Fake camera", "FAKE"),
            settings=[SettingReport("auto_exposure", "manual/off", 1.0, 0.25, True, "accepted")],
            warnings=["OpenCV has no portable auto-gain switch."],
            properties={"fps": 10.0, "frame_width": 3, "frame_height": 2},
        )

    def read(self) -> CapturedFrame | None:
        image = next(self.frames)
        if image is None:
            return None
        index = self.counter
        self.counter += 1
        return CapturedFrame(
            image=image,
            capture_start_monotonic_ns=index * 100_000_000,
            capture_end_monotonic_ns=index * 100_000_000 + 1_000,
            capture_end_utc_ns=1_800_000_000_000_000_000 + index,
            source_timestamp_ms=index * 100.0,
            source_frame_position=float(index + 1),
        )

    def close(self) -> None:
        pass

    def snapshot_properties(self) -> dict[str, object]:
        return dict(self.info.properties)


class FakeBackend(CameraBackend):
    def __init__(self, frames: list[np.ndarray | None]):
        self.frames = frames

    def discover(self, max_devices: int = 10) -> list[CameraDevice]:
        return [CameraDevice(2, "Fake camera", "FAKE")]

    def open(self, config: CaptureConfig) -> CameraSession:
        return FakeSession(self.frames)


def test_collect_preserves_frames_and_metadata(tmp_path: Path) -> None:
    frames = [
        np.arange(18, dtype=np.uint8).reshape(2, 3, 3),
        None,
        np.full((2, 3, 3), 251, dtype=np.uint8),
    ]
    output = tmp_path / "session"
    config = CaptureConfig(
        camera_id=2,
        output_dir=output,
        frame_count=2,
        warmup_frames=0,
        dark_frame=True,
    )

    result = collect(config, FakeBackend(frames))

    assert result.complete
    assert result.setting_reports[0].status == "accepted"
    saved = np.load(output / "frames.npy", mmap_mode="r")
    np.testing.assert_array_equal(saved[0], frames[0])
    np.testing.assert_array_equal(saved[1], frames[2])
    assert saved.dtype == np.uint8

    timestamps = np.load(output / "timestamps.npy", mmap_mode="r")
    assert timestamps.dtype == TIMESTAMP_DTYPE
    assert timestamps[1]["frame_index"] == 1

    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "complete"
    assert metadata["data_classification"]["raw_sensor_data"] is False
    assert metadata["data_classification"]["kind"] == "processed_webcam_frames"
    assert metadata["valid_frame_count"] == 2
    assert metadata["dark_frame_protocol"]["declared"] is True
    assert len(metadata["read_failure_events"]) == 1
    assert metadata["setting_reports"][0]["status"] == "accepted"
    assert metadata["camera_properties_at_end"]["fps"] == 10.0


def test_drop_report_marks_source_and_timing_gaps() -> None:
    values = np.zeros(3, dtype=TIMESTAMP_DTYPE)
    values["source_frame_position"] = [1.0, 2.0, 5.0]
    values["capture_end_monotonic_ns"] = [0, 100_000_000, 400_000_000]

    report = _drop_report(values, fps=10.0, read_failures=1)

    assert report["read_failures"] == 1
    assert report["source_frame_position_gap_count"] == 1
    assert report["source_frame_position_missing_estimate"] == 2
    assert report["host_timing_gap_count"] == 1
    assert report["host_timing_missing_estimate"] == 2


def test_partial_capture_is_retained(tmp_path: Path) -> None:
    output = tmp_path / "partial"
    config = CaptureConfig(
        camera_id=2,
        output_dir=output,
        frame_count=3,
        warmup_frames=0,
        max_consecutive_read_failures=2,
    )
    frame = np.ones((2, 3), dtype=np.uint16)

    result = collect(config, FakeBackend([frame, None, None]))

    assert not result.complete
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "partial"
    assert metadata["valid_frame_count"] == 1
    assert metadata["frame_array"]["dtype"] == "uint16"
    np.testing.assert_array_equal(np.load(output / "frames.npy", mmap_mode="r")[0], frame)


def test_negative_camera_property_is_a_valid_readback() -> None:
    assert _finite(-6.0) == -6.0
