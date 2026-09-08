from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ROI:
    x: int
    y: int
    width: int
    height: int

    def validate(self, frame_width: int, frame_height: int) -> None:
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("ROI coordinates must be non-negative and dimensions positive")
        if self.x + self.width > frame_width or self.y + self.height > frame_height:
            raise ValueError(
                f"ROI {self} exceeds frame bounds {frame_width}x{frame_height}"
            )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class AnalysisConfig:
    session_dir: Path
    output_dir: Path | None = None
    roi: ROI | None = None
    channel: str = "luma"
    pixels: tuple[tuple[int, int], ...] = ()
    max_lag: int = 30
    windows: int = 5
    histogram_bins: int = 256
    max_correlation_pixels: int = 256
    spectrum_peaks: int = 5

    def validate(self) -> None:
        if self.channel not in {"luma", "mean", "b", "g", "r", "intensity"}:
            raise ValueError(f"Unsupported channel projection: {self.channel}")
        if self.max_lag < 1:
            raise ValueError("max_lag must be positive")
        if self.windows < 1:
            raise ValueError("windows must be positive")
        if self.histogram_bins < 2:
            raise ValueError("histogram_bins must be at least 2")
        if self.max_correlation_pixels < 1:
            raise ValueError("max_correlation_pixels must be positive")
        if self.spectrum_peaks < 1:
            raise ValueError("spectrum_peaks must be positive")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["session_dir"] = str(self.session_dir)
        data["output_dir"] = str(self.output_dir) if self.output_dir else None
        return data


@dataclass
class EntropyConfig:
    session_dir: Path
    output_dir: Path | None = None
    roi: ROI | None = None
    channel: str = "luma"
    max_samples: int = 1_000_000
    max_lag: int = 16
    histogram_bins: int = 256
    confidence: float = 0.99
    significance_level: float = 0.01
    external_tools: tuple[str, ...] = ()
    external_method: str | None = None
    external_timeout_seconds: int = 120

    def validate(self) -> None:
        if self.channel not in {"luma", "mean", "b", "g", "r", "intensity"}:
            raise ValueError(f"Unsupported channel projection: {self.channel}")
        if self.max_samples < 100:
            raise ValueError("max_samples must be at least 100")
        if self.max_lag < 1:
            raise ValueError("max_lag must be positive")
        if self.histogram_bins < 2:
            raise ValueError("histogram_bins must be at least 2")
        if not 0.5 < self.confidence < 1.0:
            raise ValueError("confidence must be between 0.5 and 1")
        if not 0.0 < self.significance_level < 0.5:
            raise ValueError("significance_level must be between 0 and 0.5")
        allowed = {"nist90b", "practrand", "dieharder"}
        unknown = set(self.external_tools) - allowed
        if unknown:
            raise ValueError(f"Unsupported external randomness tools: {sorted(unknown)}")
        if self.external_timeout_seconds < 1:
            raise ValueError("external_timeout_seconds must be positive")

    def to_analysis_config(self) -> AnalysisConfig:
        return AnalysisConfig(
            session_dir=self.session_dir,
            output_dir=self.output_dir,
            roi=self.roi,
            channel=self.channel,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["session_dir"] = str(self.session_dir)
        data["output_dir"] = str(self.output_dir) if self.output_dir else None
        return data
