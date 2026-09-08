"""Reliable collection of processed webcam frames and provenance."""

from .collector import CaptureResult, collect
from .models import CaptureConfig

__all__ = [
    "CaptureConfig",
    "CaptureResult",
    "collect",
]
__version__ = "0.2.0"
