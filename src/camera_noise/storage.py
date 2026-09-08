from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from camera_noise.models import CapturedFrame


TIMESTAMP_DTYPE = np.dtype(
    [
        ("frame_index", "<i8"),
        ("capture_start_monotonic_ns", "<i8"),
        ("capture_end_monotonic_ns", "<i8"),
        ("capture_end_utc_ns", "<i8"),
        ("source_timestamp_ms", "<f8"),
        ("source_frame_position", "<f8"),
    ]
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


class SessionStore:
    def __init__(self, output_dir: Path, frame_count: int):
        self.output_dir = output_dir
        self.frame_count = frame_count
        self.frames: np.memmap | None = None
        self.timestamps: np.memmap | None = None
        self.frame_shape: tuple[int, ...] | None = None
        self.frame_dtype: np.dtype[Any] | None = None
        self.valid_count = 0

    def create(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=False)

    def initialize_arrays(self, first: CapturedFrame) -> None:
        image = np.asarray(first.image)
        if image.ndim not in (2, 3):
            raise ValueError(f"Unexpected frame dimensions: {image.shape}")
        self.frame_shape = image.shape
        self.frame_dtype = image.dtype
        self.frames = np.lib.format.open_memmap(
            self.output_dir / "frames.npy",
            mode="w+",
            dtype=image.dtype,
            shape=(self.frame_count, *image.shape),
        )
        self.timestamps = np.lib.format.open_memmap(
            self.output_dir / "timestamps.npy",
            mode="w+",
            dtype=TIMESTAMP_DTYPE,
            shape=(self.frame_count,),
        )

    def append(self, frame: CapturedFrame) -> None:
        if self.frames is None or self.timestamps is None:
            self.initialize_arrays(frame)
        image = np.asarray(frame.image)
        if image.shape != self.frame_shape or image.dtype != self.frame_dtype:
            raise ValueError(
                "Camera changed frame layout during capture: "
                f"expected {self.frame_shape}/{self.frame_dtype}, got {image.shape}/{image.dtype}"
            )
        index = self.valid_count
        if index >= self.frame_count:
            raise IndexError("SessionStore is already full")
        self.frames[index] = image
        self.timestamps[index] = (
            index,
            frame.capture_start_monotonic_ns,
            frame.capture_end_monotonic_ns,
            frame.capture_end_utc_ns,
            np.nan if frame.source_timestamp_ms is None else frame.source_timestamp_ms,
            np.nan if frame.source_frame_position is None else frame.source_frame_position,
        )
        self.valid_count += 1

    def timestamp_view(self) -> np.ndarray:
        if self.timestamps is None:
            return np.empty(0, dtype=TIMESTAMP_DTYPE)
        return np.asarray(self.timestamps[: self.valid_count])

    def flush(self) -> None:
        if self.frames is not None:
            self.frames.flush()
        if self.timestamps is not None:
            self.timestamps.flush()

