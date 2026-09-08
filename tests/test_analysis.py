from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from camera_noise.analysis import AnalysisConfig, ROI, analyze_session
from camera_noise.storage import TIMESTAMP_DTYPE


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _synthetic_session(path: Path) -> np.ndarray:
    path.mkdir()
    frame_count, height, width = 80, 8, 10
    yy, xx = np.mgrid[:height, :width]
    fixed_pattern = 2 * xx + 3 * yy
    frames = np.empty((frame_count, height, width, 3), dtype=np.uint8)
    rng = np.random.default_rng(42)
    for index in range(frame_count):
        periodic = 5.0 * np.sin(2 * np.pi * index / 10)
        drift = 0.08 * index
        step = 18.0 if index >= 50 else 0.0
        noise = rng.integers(-2, 3, size=(height, width))
        image = np.clip(35 + fixed_pattern + periodic + drift + step + noise, 0, 255)
        frames[index, ..., 0] = image.astype(np.uint8)
        frames[index, ..., 1] = np.clip(image + 2, 0, 255).astype(np.uint8)
        frames[index, ..., 2] = np.clip(image + 4, 0, 255).astype(np.uint8)
    frames[5, 2, 3] = 0
    frames[6, 2, 3] = 255
    frames[31] = frames[30]
    np.save(path / "frames.npy", frames)

    timestamps = np.zeros(frame_count, dtype=TIMESTAMP_DTYPE)
    timestamps["frame_index"] = np.arange(frame_count)
    timestamps["capture_end_monotonic_ns"] = np.arange(frame_count) * 50_000_000
    timestamps["capture_end_utc_ns"] = 1_800_000_000_000_000_000 + np.arange(frame_count) * 50_000_000
    timestamps["source_timestamp_ms"] = np.arange(frame_count) * 50.0
    timestamps["source_frame_position"] = np.arange(1, frame_count + 1)
    np.save(path / "timestamps.npy", timestamps)
    metadata = {
        "schema_version": 1,
        "status": "complete",
        "valid_frame_count": frame_count,
        "data_classification": {
            "kind": "processed_webcam_frames",
            "raw_sensor_data": False,
        },
        "camera_properties_at_end": {"fps": 20.0},
    }
    (path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return frames


def test_analysis_end_to_end_preserves_source_and_finds_known_features(tmp_path: Path) -> None:
    session_dir = tmp_path / "capture"
    frames = _synthetic_session(session_dir)
    before = _digest(session_dir / "frames.npy")
    output = tmp_path / "results"

    result = analyze_session(
        AnalysisConfig(
            session_dir=session_dir,
            output_dir=output,
            roi=ROI(2, 1, 6, 5),
            channel="b",
            pixels=((3, 2), (6, 4)),
            max_lag=20,
            windows=4,
            max_correlation_pixels=20,
        )
    )

    assert before == _digest(session_dir / "frames.npy")
    assert result.report_path.is_file()
    assert (output / "derived_metrics.npz").is_file()
    assert len(result.plots) == 8
    assert all(path.stat().st_size > 0 for path in result.plots)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert report["source_data_unchanged"] is True
    assert report["input"]["roi"] == {"x": 2, "y": 1, "width": 6, "height": 5}
    assert report["metrics"]["descriptive_statistics"]["sample_count"] == 80 * 6 * 5
    assert 31 in report["metrics"]["artifact_detection"]["duplicate_frame_indices"]
    assert 50 in report["metrics"]["artifact_detection"]["exposure_change_candidates"]["indices"]
    peaks = report["metrics"]["spectral_analysis"]["dominant_peaks"]
    assert any(abs(item["frequency"] - 2.0) < 0.3 for item in peaks)
    assert report["metrics"]["stability"]["first_to_last_mean_change"] > 10

    derived = np.load(output / "derived_metrics.npz")
    assert derived["pixel_traces"].shape == (80, 2)
    assert derived["mean_image"].shape == (5, 6)
    assert np.array_equal(frames, np.load(session_dir / "frames.npy"))


def test_analysis_rejects_roi_outside_frame(tmp_path: Path) -> None:
    session_dir = tmp_path / "capture"
    _synthetic_session(session_dir)

    with pytest.raises(ValueError, match="exceeds frame bounds"):
        analyze_session(
            AnalysisConfig(
                session_dir=session_dir,
                output_dir=tmp_path / "results",
                roi=ROI(8, 7, 4, 4),
            )
        )
