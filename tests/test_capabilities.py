from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from camera_noise.capabilities import (
    CapabilityConfig,
    classify_pixel_format,
    discover_capabilities,
)
from camera_noise.cli import _parse_resolutions, build_parser


class FakeCapture:
    def __init__(self, opened: bool = True):
        self.opened = opened
        self.counter = 0
        self.properties: dict[int, float] = {
            cv2.CAP_PROP_FRAME_WIDTH: 640.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 480.0,
            cv2.CAP_PROP_FPS: 30.0,
            cv2.CAP_PROP_FOURCC: float(cv2.VideoWriter_fourcc(*"YUY2")),
            cv2.CAP_PROP_FORMAT: 16.0,
            cv2.CAP_PROP_CONVERT_RGB: 1.0,
            cv2.CAP_PROP_EXPOSURE: -6.0,
            cv2.CAP_PROP_GAIN: 2.0,
        }

    def isOpened(self) -> bool:
        return self.opened

    def getBackendName(self) -> str:
        return "DSHOW"

    def get(self, prop: int) -> float:
        return self.properties.get(prop, 0.0)

    def set(self, prop: int, value: float) -> bool:
        if prop == cv2.CAP_PROP_FOURCC:
            if int(value) != cv2.VideoWriter_fourcc(*"YUY2"):
                return False
        elif prop == cv2.CAP_PROP_FRAME_WIDTH and int(value) != 640:
            return False
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT and int(value) != 480:
            return False
        elif prop == cv2.CAP_PROP_FPS and float(value) != 30.0:
            return False
        self.properties[prop] = float(value)
        return True

    def read(self) -> tuple[bool, np.ndarray]:
        self.counter += 1
        value = self.counter % 256
        if self.properties[cv2.CAP_PROP_CONVERT_RGB] == 0.0:
            frame = np.full((480, 1280), value, dtype=np.uint8)
        else:
            frame = np.full((480, 640, 3), value, dtype=np.uint8)
        return True, frame

    def release(self) -> None:
        self.opened = False


def _hardware(_: CapabilityConfig) -> dict[str, Any]:
    return {
        "source": "fake PnP provider",
        "devices": [{"name": "Fake Integrated Camera", "instance_id": "USB\\FAKE"}],
        "selected": {
            "name": "Fake Integrated Camera",
            "instance_id": "USB\\FAKE",
            "manufacturer": "Fake Corp",
            "status": "Started",
            "driver_version": "1.2.3",
        },
        "limitations": [],
    }


def test_pixel_format_classification_is_conservative() -> None:
    assert classify_pixel_format("MJPG")["compression"] == "compressed"
    assert classify_pixel_format("YUY2")["compression"] == "uncompressed"
    assert classify_pixel_format("YUY2")["raw_or_bayer_name"] is False
    assert classify_pixel_format("BG10")["raw_or_bayer_name"] is True
    assert classify_pixel_format("BG10")["reported_component_bit_depth"] == 10
    assert classify_pixel_format("ZZZZ")["compression"] == "unknown"


def test_capability_discovery_negotiates_validates_and_reports(tmp_path: Path) -> None:
    result = discover_capabilities(
        CapabilityConfig(
            camera_id=0,
            backends=("dshow",),
            output_dir=tmp_path,
            resolutions=((640, 480),),
            frame_rates=(30.0,),
            pixel_formats=("YUY2", "MJPG"),
            validation_frames=3,
            warmup_frames=0,
            max_validation_modes_per_backend=1,
            open_retry_delay_seconds=0.0,
            read_retry_delay_seconds=0.0,
            release_settle_seconds=0.0,
        ),
        capture_factory=lambda _camera, _api: FakeCapture(),
        hardware_provider=_hardware,
    )

    assert result.json_path.is_file()
    assert result.markdown_path.is_file()
    report = json.loads(result.json_path.read_text(encoding="utf-8"))
    backend = report["backends"][0]
    assert backend["opened"] is True
    assert len(backend["discovered_modes"]) == 1
    assert any(test["fallback_detected"] for test in backend["mode_negotiation_tests"])
    validation = backend["validation_results"][0]
    assert validation["consecutive_duplicate_pairs"] == 0
    assert validation["native_transport_preservation_possible"] is True
    assert validation["npy_roundtrip_exact"] is True
    assert Path(validation["sample_file"]).is_file()
    assert report["conclusion"]["category"] == "B"
    assert report["conclusion"]["raw_bayer_exposed"] is False
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "RAW/Bayer access was not exposed through the tested interface." in markdown
    assert "Uncompressed video does NOT automatically mean raw sensor data." in markdown


def test_capability_cli_arguments_follow_existing_subcommand_style() -> None:
    args = build_parser().parse_args(
        [
            "capabilities",
            "--camera",
            "2",
            "--backend",
            "dshow",
            "--resolution",
            "1280x720",
            "--probe-fps",
            "30",
            "--pixel-format",
            "YUY2",
        ]
    )

    assert args.command == "capabilities"
    assert args.camera == 2
    assert args.backend == ["dshow"]
    assert _parse_resolutions(args.resolution) == ((1280, 720),)


def test_capability_discovery_retries_a_transient_open_failure(tmp_path: Path) -> None:
    captures = 0

    def factory(_camera: int, _api: int) -> FakeCapture:
        nonlocal captures
        captures += 1
        return FakeCapture(opened=captures != 1)

    result = discover_capabilities(
        CapabilityConfig(
            camera_id=0,
            backends=("dshow",),
            output_dir=tmp_path,
            resolutions=((640, 480),),
            frame_rates=(30.0,),
            pixel_formats=("YUY2",),
            validation_frames=2,
            warmup_frames=0,
            max_validation_modes_per_backend=1,
            open_attempts=2,
            open_retry_delay_seconds=0.0,
            read_retry_delay_seconds=0.0,
            release_settle_seconds=0.0,
        ),
        capture_factory=factory,
        hardware_provider=_hardware,
    )

    backend = result.report["backends"][0]
    assert backend["status"] == "working"
    assert [item["opened"] for item in backend["open_attempts"]] == [False, True]


def test_hardware_metadata_query_runs_after_camera_probing(tmp_path: Path) -> None:
    events: list[str] = []

    def factory(_camera: int, _api: int) -> FakeCapture:
        events.append("capture")
        return FakeCapture()

    def hardware(config: CapabilityConfig) -> dict[str, Any]:
        events.append("hardware")
        return _hardware(config)

    discover_capabilities(
        CapabilityConfig(
            camera_id=0,
            backends=("dshow",),
            output_dir=tmp_path,
            resolutions=((640, 480),),
            frame_rates=(30.0,),
            pixel_formats=("YUY2",),
            validation_frames=2,
            warmup_frames=0,
            max_validation_modes_per_backend=1,
            open_retry_delay_seconds=0.0,
            read_retry_delay_seconds=0.0,
            release_settle_seconds=0.0,
        ),
        capture_factory=factory,
        hardware_provider=hardware,
    )

    assert events[-1] == "hardware"
    assert "capture" in events[:-1]
