from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CameraDevice:
    device_id: int
    name: str
    backend: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    fourcc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SettingReport:
    name: str
    requested: Any
    before: Any
    after: Any
    set_returned: bool | None
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapturedFrame:
    image: Any
    capture_start_monotonic_ns: int
    capture_end_monotonic_ns: int
    capture_end_utc_ns: int
    source_timestamp_ms: float | None
    source_frame_position: float | None


@dataclass
class CaptureConfig:
    camera_id: int
    output_dir: Path
    frame_count: int = 300
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    exposure: float | None = None
    gain: float | None = None
    white_balance: float | None = None
    disable_auto_exposure: bool = True
    disable_auto_white_balance: bool = True
    warmup_frames: int = 5
    max_consecutive_read_failures: int = 10
    dark_frame: bool = False
    notes: str = ""
    backend: str = "any"

    def validate(self) -> None:
        if self.frame_count <= 0:
            raise ValueError("frame_count must be positive")
        if (self.width is None) != (self.height is None):
            raise ValueError("width and height must be provided together")
        if self.width is not None and (self.width <= 0 or self.height <= 0):
            raise ValueError("width and height must be positive")
        if self.fps is not None and self.fps <= 0:
            raise ValueError("fps must be positive")
        if self.warmup_frames < 0:
            raise ValueError("warmup_frames cannot be negative")
        if self.max_consecutive_read_failures <= 0:
            raise ValueError("max_consecutive_read_failures must be positive")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output_dir"] = str(self.output_dir)
        return data


@dataclass
class SessionInfo:
    device: CameraDevice
    settings: list[SettingReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

