from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from camera_noise.models import CameraDevice, CapturedFrame, CaptureConfig, SessionInfo


class CameraSession(ABC):
    """An opened camera. Other acquisition backends can implement this API."""

    info: SessionInfo

    @abstractmethod
    def read(self) -> CapturedFrame | None:
        """Return one frame, or None when the backend reports a read failure."""

    @abstractmethod
    def close(self) -> None:
        pass

    def snapshot_properties(self) -> dict[str, Any]:
        """Return currently readable camera properties."""
        return dict(self.info.properties)

    def __enter__(self) -> "CameraSession":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class CameraBackend(ABC):
    @abstractmethod
    def discover(self, max_devices: int = 10) -> list[CameraDevice]:
        pass

    @abstractmethod
    def open(self, config: CaptureConfig) -> CameraSession:
        pass
