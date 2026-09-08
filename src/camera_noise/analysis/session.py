from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .models import AnalysisConfig, ROI


@dataclass(frozen=True)
class LoadedSession:
    session_dir: Path
    metadata: dict[str, Any]
    frames: np.ndarray
    timestamps: np.ndarray | None
    valid_frame_count: int
    roi: ROI
    channel: str
    source_dtype: np.dtype[Any]
    source_shape: tuple[int, ...]
    value_min: float | None
    value_max: float | None

    def project(self, frame_index: int) -> np.ndarray:
        frame = self.frames[frame_index]
        roi = frame[
            self.roi.y : self.roi.y + self.roi.height,
            self.roi.x : self.roi.x + self.roi.width,
        ]
        if roi.ndim == 2:
            return np.asarray(roi, dtype=np.float64)
        if roi.ndim != 3 or roi.shape[2] < 3:
            raise ValueError(f"Unsupported stored frame layout: {frame.shape}")
        if self.channel == "b":
            return np.asarray(roi[..., 0], dtype=np.float64)
        if self.channel == "g":
            return np.asarray(roi[..., 1], dtype=np.float64)
        if self.channel == "r":
            return np.asarray(roi[..., 2], dtype=np.float64)
        values = np.asarray(roi[..., :3], dtype=np.float64)
        if self.channel == "mean":
            return values.mean(axis=2)
        return 0.114 * values[..., 0] + 0.587 * values[..., 1] + 0.299 * values[..., 2]

    def elapsed_seconds(self) -> tuple[np.ndarray, str]:
        if self.timestamps is not None and len(self.timestamps) >= self.valid_frame_count:
            names = self.timestamps.dtype.names or ()
            if "capture_end_monotonic_ns" in names:
                ns = np.asarray(
                    self.timestamps["capture_end_monotonic_ns"][: self.valid_frame_count],
                    dtype=np.float64,
                )
                if len(ns) and np.all(np.isfinite(ns)) and np.all(np.diff(ns) > 0):
                    return (ns - ns[0]) / 1_000_000_000.0, "host_monotonic"
        fps = _metadata_fps(self.metadata)
        if fps is not None and fps > 0:
            return np.arange(self.valid_frame_count, dtype=np.float64) / fps, "fps_readback"
        return np.arange(self.valid_frame_count, dtype=np.float64), "frame_index"


def _metadata_fps(metadata: dict[str, Any]) -> float | None:
    for key in (
        "camera_properties_at_end",
        "camera_properties_after_warmup",
        "camera_properties_after_configuration",
    ):
        value = metadata.get(key, {}).get("fps")
        if isinstance(value, (int, float)) and np.isfinite(value) and value > 0:
            return float(value)
    return None


def load_session(config: AnalysisConfig) -> LoadedSession:
    config.validate()
    session_dir = config.session_dir.resolve()
    metadata_path = session_dir / "metadata.json"
    frames_path = session_dir / "frames.npy"
    if not metadata_path.is_file() or not frames_path.is_file():
        raise ValueError("Session must contain metadata.json and frames.npy")

    with metadata_path.open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    frames = np.load(frames_path, mmap_mode="r", allow_pickle=False)
    valid_count = int(metadata.get("valid_frame_count", len(frames)))
    if valid_count < 1 or valid_count > len(frames):
        raise ValueError(
            f"Invalid valid_frame_count {valid_count} for stored array length {len(frames)}"
        )
    if frames.ndim not in (3, 4):
        raise ValueError(f"Expected frames shaped (time, height, width[, channels]), got {frames.shape}")
    if frames.ndim == 4 and config.channel == "intensity":
        raise ValueError("Channel 'intensity' is only valid for stored single-channel frames")
    height, width = int(frames.shape[1]), int(frames.shape[2])
    roi = config.roi or ROI(0, 0, width, height)
    roi.validate(width, height)
    for x, y in config.pixels:
        if not (roi.x <= x < roi.x + roi.width and roi.y <= y < roi.y + roi.height):
            raise ValueError(f"Pixel ({x}, {y}) is outside the selected ROI")

    timestamps_path = session_dir / "timestamps.npy"
    timestamps = (
        np.load(timestamps_path, mmap_mode="r", allow_pickle=False)
        if timestamps_path.is_file()
        else None
    )
    dtype = frames.dtype
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        value_min, value_max = float(info.min), float(info.max)
    elif np.issubdtype(dtype, np.bool_):
        value_min, value_max = 0.0, 1.0
    else:
        value_min = value_max = None
    channel = "intensity" if frames.ndim == 3 else config.channel
    return LoadedSession(
        session_dir=session_dir,
        metadata=metadata,
        frames=frames,
        timestamps=timestamps,
        valid_frame_count=valid_count,
        roi=roi,
        channel=channel,
        source_dtype=dtype,
        source_shape=tuple(int(item) for item in frames.shape),
        value_min=value_min,
        value_max=value_max,
    )
