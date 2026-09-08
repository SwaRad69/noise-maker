from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from camera_noise.backends.opencv import OpenCVBackend
from camera_noise.collector import collect
from camera_noise.models import CameraDevice, CaptureConfig


def _device_line(device: CameraDevice) -> str:
    resolution = f"{device.width}x{device.height}" if device.width and device.height else "unknown"
    fps = f"{device.fps:g} fps" if device.fps else "fps unknown"
    fourcc = device.fourcc or "format unknown"
    return f"[{device.device_id}] {device.name}: {resolution}, {fps}, {fourcc}, backend={device.backend}"


def _discover(backend: str, max_devices: int) -> list[CameraDevice]:
    print(f"Probing camera indices 0 through {max_devices - 1} using backend '{backend}'...")
    return OpenCVBackend(backend).discover(max_devices)


def _select_camera(devices: list[CameraDevice]) -> int:
    if not devices:
        raise RuntimeError("No cameras produced a readable frame")
    for device in devices:
        print(_device_line(device))
    while True:
        answer = input("Select camera index: ").strip()
        try:
            selected = int(answer)
        except ValueError:
            print("Enter one of the listed numeric camera indices.", file=sys.stderr)
            continue
        if any(item.device_id == selected for item in devices):
            return selected
        print("That camera index is not in the discovered list.", file=sys.stderr)


def _default_output(camera_id: int, dark: bool) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    kind = "dark" if dark else "camera"
    return Path("experiments") / f"{stamp}_{kind}{camera_id}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="camera-noise")
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser("list", help="detect cameras that can return a frame")
    listing.add_argument("--backend", choices=["any", "dshow", "msmf", "v4l2"], default="any")
    listing.add_argument("--max-devices", type=int, default=10)

    capabilities = subparsers.add_parser(
        "capabilities",
        help="measure camera backends, negotiated modes, controls, and least-processed access",
    )
    capabilities.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    capabilities.add_argument(
        "--backend",
        action="append",
        choices=["any", "dshow", "msmf", "v4l2"],
        help="backend to test; repeatable (default on Windows: dshow and msmf)",
    )
    capabilities.add_argument("--device-name", help="optional Windows device name used for PnP matching")
    capabilities.add_argument(
        "--device-id",
        help="optional Windows PnP identifier substring used for hardware/driver matching",
    )
    capabilities.add_argument(
        "--resolution",
        action="append",
        default=[],
        metavar="WIDTHxHEIGHT",
        help="mode resolution to test; repeatable (uses a standard webcam grid by default)",
    )
    capabilities.add_argument(
        "--probe-fps",
        action="append",
        type=float,
        default=[],
        help="frame rate to test; repeatable (defaults to 15 and 30)",
    )
    capabilities.add_argument(
        "--pixel-format",
        action="append",
        default=[],
        metavar="FOURCC",
        help="FOURCC to request; repeatable (defaults to MJPG, YUY2, and NV12)",
    )
    capabilities.add_argument("--validation-frames", type=int, default=5)
    capabilities.add_argument("--warmup-frames", type=int, default=2)
    capabilities.add_argument("--max-validation-modes", type=int, default=6)
    capabilities.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="directory for camera_capabilities.md/json and tiny .npy samples",
    )

    capture = subparsers.add_parser("capture", help="capture a camera-noise data session")
    capture.add_argument("--camera", type=int, help="camera index; omit for interactive selection")
    capture.add_argument("--frames", type=int, default=300)
    capture.add_argument("--width", type=int)
    capture.add_argument("--height", type=int)
    capture.add_argument("--fps", type=float)
    capture.add_argument("--exposure", type=float)
    capture.add_argument("--gain", type=float)
    capture.add_argument("--white-balance", type=float)
    capture.add_argument("--keep-auto-exposure", action="store_true")
    capture.add_argument("--keep-auto-white-balance", action="store_true")
    capture.add_argument("--warmup-frames", type=int, default=5)
    capture.add_argument("--max-read-failures", type=int, default=10)
    capture.add_argument("--dark", action="store_true", help="declare a covered-camera dark-frame session")
    capture.add_argument("--notes", default="")
    capture.add_argument("--output", type=Path)
    capture.add_argument("--backend", choices=["any", "dshow", "msmf", "v4l2"], default="any")
    capture.add_argument("--max-devices", type=int, default=10)
    capture.add_argument("--yes", action="store_true", help="skip the dark-cover confirmation")

    analysis = subparsers.add_parser("analyze", help="analyze a captured camera-noise session")
    analysis.add_argument("session", type=Path, help="capture session containing frames.npy and metadata.json")
    analysis.add_argument("--output", type=Path, help="output directory; defaults to SESSION/analysis")
    analysis.add_argument(
        "--roi",
        type=int,
        nargs=4,
        metavar=("X", "Y", "WIDTH", "HEIGHT"),
        help="rectangular ROI in full-frame pixel coordinates; defaults to the full frame",
    )
    analysis.add_argument(
        "--channel",
        choices=["luma", "mean", "b", "g", "r", "intensity"],
        default="luma",
        help="projection for color frames; intensity is for single-channel captures",
    )
    analysis.add_argument(
        "--pixel",
        action="append",
        default=[],
        metavar="X,Y",
        help="pixel time series to include; repeatable and constrained to the ROI",
    )
    analysis.add_argument("--max-lag", type=int, default=30)
    analysis.add_argument("--windows", type=int, default=5)
    analysis.add_argument("--histogram-bins", type=int, default=256)
    analysis.add_argument("--max-correlation-pixels", type=int, default=256)
    analysis.add_argument("--spectrum-peaks", type=int, default=5)

    entropy = subparsers.add_parser(
        "entropy",
        help="estimate entropy and compare bit extraction methods for a captured session",
    )
    entropy.add_argument("session", type=Path, help="capture session containing frames.npy and metadata.json")
    entropy.add_argument("--output", type=Path, help="output directory; defaults to SESSION/entropy")
    entropy.add_argument(
        "--roi",
        type=int,
        nargs=4,
        metavar=("X", "Y", "WIDTH", "HEIGHT"),
        help="rectangular ROI in full-frame pixel coordinates; defaults to the full frame",
    )
    entropy.add_argument(
        "--channel",
        choices=["luma", "mean", "b", "g", "r", "intensity"],
        default="luma",
        help="projection for color frames; direct integer channels enable LSB/parity methods",
    )
    entropy.add_argument("--max-samples", type=int, default=1_000_000)
    entropy.add_argument("--max-lag", type=int, default=16)
    entropy.add_argument("--histogram-bins", type=int, default=256)
    entropy.add_argument("--confidence", type=float, default=0.99)
    entropy.add_argument("--alpha", type=float, default=0.01, help="selected-test significance level")
    entropy.add_argument(
        "--external-tool",
        action="append",
        choices=["nist90b", "practrand", "dieharder"],
        default=[],
        help="run an installed external suite on the selected bitstream; repeatable",
    )
    entropy.add_argument(
        "--external-method",
        help="extraction method for external tools; defaults to the report's recommended candidate",
    )
    entropy.add_argument("--external-timeout", type=int, default=120)

    quantum = subparsers.add_parser(
        "quantum",
        help="generate quantum circuits driven by camera-noise bitstreams",
    )
    quantum.add_argument("entropy_dir", type=Path, help="entropy output directory containing entropy_report.json and bitstreams")
    quantum.add_argument("--output", type=Path, help="output directory; defaults to ENTROPY_DIR/quantum")
    quantum.add_argument(
        "--method",
        help="extraction method whose bitstream to use; defaults to the report's recommended candidate",
    )
    quantum.add_argument("--qubits", type=int, default=4, help="number of qubits per circuit")
    quantum.add_argument("--depth", type=int, default=8, help="circuit depth (gate layers)")
    quantum.add_argument("--circuits", type=int, default=5, help="number of circuits to generate")
    quantum.add_argument(
        "--strategy",
        choices=["random_angles", "random_structure", "random_walk"],
        default="random_angles",
        help="circuit generation strategy",
    )
    quantum.add_argument("--angle-bits", type=int, default=10, help="noise bits per rotation angle")
    quantum.add_argument("--simulate", action="store_true", help="run circuits on Qiskit Aer simulator")
    quantum.add_argument("--shots", type=int, default=1024, help="simulator shots per circuit")
    return parser


def _parse_pixels(values: list[str]) -> tuple[tuple[int, int], ...]:
    pixels: list[tuple[int, int]] = []
    for value in values:
        parts = value.split(",")
        if len(parts) != 2:
            raise ValueError(f"Invalid pixel '{value}'; expected X,Y")
        try:
            pixels.append((int(parts[0]), int(parts[1])))
        except ValueError as exc:
            raise ValueError(f"Invalid pixel '{value}'; expected integer X,Y") from exc
    return tuple(pixels)


def _parse_resolutions(values: list[str]) -> tuple[tuple[int, int], ...]:
    resolutions: list[tuple[int, int]] = []
    for value in values:
        parts = value.lower().split("x")
        if len(parts) != 2:
            raise ValueError(f"Invalid resolution '{value}'; expected WIDTHxHEIGHT")
        try:
            width, height = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError(f"Invalid resolution '{value}'; expected integer WIDTHxHEIGHT") from exc
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid resolution '{value}'; dimensions must be positive")
        resolutions.append((width, height))
    return tuple(resolutions)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "list":
        devices = _discover(args.backend, args.max_devices)
        if not devices:
            print("No readable cameras detected.")
            return
        for device in devices:
            print(_device_line(device))
        return
    if args.command == "capabilities":
        from camera_noise.capabilities import (
            DEFAULT_FRAME_RATES,
            DEFAULT_PIXEL_FORMATS,
            DEFAULT_RESOLUTIONS,
            CapabilityConfig,
            discover_capabilities,
        )

        try:
            result = discover_capabilities(
                CapabilityConfig(
                    camera_id=args.camera,
                    backends=tuple(dict.fromkeys(args.backend or ["dshow", "msmf"])),
                    output_dir=args.output_dir,
                    device_name=args.device_name,
                    device_identifier=args.device_id,
                    resolutions=_parse_resolutions(args.resolution) or DEFAULT_RESOLUTIONS,
                    frame_rates=tuple(args.probe_fps) or DEFAULT_FRAME_RATES,
                    pixel_formats=tuple(item.upper() for item in args.pixel_format)
                    or DEFAULT_PIXEL_FORMATS,
                    validation_frames=args.validation_frames,
                    warmup_frames=args.warmup_frames,
                    max_validation_modes_per_backend=args.max_validation_modes,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Capability discovery failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        report = result.report
        print(
            json.dumps(
                {
                    "camera_detected_by_windows": bool(
                        report["device"]["hardware"].get("selected")
                        or report["device"]["hardware"].get("devices")
                    ),
                    "working_backends": [
                        item["backend"] for item in report["backends"] if item["opened"]
                    ],
                    "discovered_mode_count": sum(
                        len(item["discovered_modes"]) for item in report["backends"]
                    ),
                    "lowest_level_classification": report["conclusion"]["category"],
                    "answer": report["conclusion"]["answer"],
                    "recommended_mode": report["conclusion"]["recommended_mode"],
                    "markdown_report": str(result.markdown_path),
                    "json_report": str(result.json_path),
                },
                indent=2,
            )
        )
        return
    if args.command == "analyze":
        from camera_noise.analysis import AnalysisConfig, ROI, analyze_session

        try:
            roi = ROI(*args.roi) if args.roi else None
            analysis_config = AnalysisConfig(
                session_dir=args.session,
                output_dir=args.output,
                roi=roi,
                channel=args.channel,
                pixels=_parse_pixels(args.pixel),
                max_lag=args.max_lag,
                windows=args.windows,
                histogram_bins=args.histogram_bins,
                max_correlation_pixels=args.max_correlation_pixels,
                spectrum_peaks=args.spectrum_peaks,
            )
            result = analyze_session(analysis_config)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Analysis failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(
            json.dumps(
                {
                    "output_dir": str(result.output_dir),
                    "report": str(result.report_path),
                    "plots": [str(path) for path in result.plots],
                    "findings": result.findings,
                },
                indent=2,
            )
        )
        return
    if args.command == "entropy":
        from camera_noise.analysis import EntropyConfig, ROI, analyze_entropy

        try:
            result = analyze_entropy(
                EntropyConfig(
                    session_dir=args.session,
                    output_dir=args.output,
                    roi=ROI(*args.roi) if args.roi else None,
                    channel=args.channel,
                    max_samples=args.max_samples,
                    max_lag=args.max_lag,
                    histogram_bins=args.histogram_bins,
                    confidence=args.confidence,
                    significance_level=args.alpha,
                    external_tools=tuple(dict.fromkeys(args.external_tool)),
                    external_method=args.external_method,
                    external_timeout_seconds=args.external_timeout,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Entropy analysis failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(
            json.dumps(
                {
                    "output_dir": str(result.output_dir),
                    "report": str(result.report_path),
                    "markdown_report": str(result.markdown_path),
                    "best_method": result.best_method,
                    "conclusion": result.conclusion,
                },
                indent=2,
            )
        )
        return
    if args.command == "quantum":
        from camera_noise.quantum import run_quantum_pipeline

        try:
            result = run_quantum_pipeline(
                entropy_dir=args.entropy_dir,
                output_dir=args.output,
                method=args.method,
                n_qubits=args.qubits,
                depth=args.depth,
                n_circuits=args.circuits,
                strategy=args.strategy,
                angle_bits=args.angle_bits,
                simulate=args.simulate,
                shots=args.shots,
            )
        except (OSError, RuntimeError, ValueError, ImportError) as exc:
            print(f"Quantum circuit generation failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        summary = {
            "output_dir": str(result.output_dir),
            "report": str(result.report_path),
            "circuits_generated": len(result.circuits),
            "strategy": args.strategy,
            "simulated": result.simulated,
        }
        if result.measurement_counts:
            summary["measurement_samples"] = [
                {"circuit": index, "unique_outcomes": len(counts)}
                for index, counts in enumerate(result.measurement_counts)
            ]
        print(json.dumps(summary, indent=2))
        return

    camera_id = args.camera
    if camera_id is None:
        camera_id = _select_camera(_discover(args.backend, args.max_devices))
    if args.dark and not args.yes:
        input(
            "Cover the selected camera with an opaque cover, minimize light leakage, "
            "then press Enter to begin..."
        )

    output = args.output or _default_output(camera_id, args.dark)
    config = CaptureConfig(
        camera_id=camera_id,
        output_dir=output,
        frame_count=args.frames,
        width=args.width,
        height=args.height,
        fps=args.fps,
        exposure=args.exposure,
        gain=args.gain,
        white_balance=args.white_balance,
        disable_auto_exposure=not args.keep_auto_exposure,
        disable_auto_white_balance=not args.keep_auto_white_balance,
        warmup_frames=args.warmup_frames,
        max_consecutive_read_failures=args.max_read_failures,
        dark_frame=args.dark,
        notes=args.notes,
        backend=args.backend,
    )
    try:
        result = collect(config)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Capture could not start: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "valid_frames": result.valid_frame_count,
                "requested_frames": result.requested_frame_count,
                "complete": result.complete,
                "settings": [
                    {
                        "name": item.name,
                        "requested": item.requested,
                        "readback": item.after,
                        "set_returned": item.set_returned,
                        "status": item.status,
                        "detail": item.detail,
                    }
                    for item in result.setting_reports
                ],
                "warnings": list(result.warnings),
            },
            indent=2,
        )
    )
    if not result.complete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
