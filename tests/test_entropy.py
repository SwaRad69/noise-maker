from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from camera_noise.analysis import EntropyConfig, ROI, analyze_entropy
from camera_noise.analysis.entropy import _bit_metrics, _von_neumann


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _session(path: Path, frames: np.ndarray) -> None:
    path.mkdir()
    np.save(path / "frames.npy", frames)
    metadata = {
        "schema_version": 1,
        "status": "complete",
        "valid_frame_count": len(frames),
        "data_classification": {
            "kind": "processed_webcam_frames",
            "raw_sensor_data": False,
        },
    }
    (path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def test_von_neumann_pair_rule() -> None:
    bits = np.asarray([0, 1, 1, 0, 0, 0, 1, 1, 0], dtype=np.uint8)
    np.testing.assert_array_equal(_von_neumann(bits), np.asarray([0, 1], dtype=np.uint8))


def test_constant_bits_are_not_credited_with_entropy() -> None:
    metrics = _bit_metrics(
        np.zeros(2000, dtype=np.uint8),
        maximum_lag=8,
        confidence=0.99,
        alpha=0.01,
    )

    assert metrics["shannon_entropy_bits_per_bit_plugin"] == 0.0
    assert metrics["nist_sp_800_90b_inspired_estimators"][
        "conservative_minimum_of_implemented_estimators_bits_per_bit"
    ] == 0.0
    assert metrics["claim_levels"]["looks_random"] is False
    assert metrics["claim_levels"]["is_unpredictable"].startswith("not established")


def test_entropy_analysis_compares_integer_extraction_methods(tmp_path: Path) -> None:
    rng = np.random.default_rng(7123)
    frames = rng.integers(0, 256, size=(320, 4, 5), dtype=np.uint8)
    session = tmp_path / "capture"
    _session(session, frames)
    before = _digest(session / "frames.npy")

    result = analyze_entropy(
        EntropyConfig(
            session_dir=session,
            output_dir=tmp_path / "entropy",
            roi=ROI(0, 0, 5, 4),
            channel="intensity",
            max_samples=100_000,
            max_lag=8,
        )
    )

    assert before == _digest(session / "frames.npy")
    assert result.report_path.is_file()
    assert result.markdown_path.is_file()
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    methods = report["bit_extraction_methods"]
    assert {
        "direct_value_lsb",
        "direct_value_lsb_von_neumann",
        "per_pixel_median_threshold",
        "temporal_difference_sign",
        "temporal_difference_parity",
    } <= methods.keys()
    assert report["source_data_unchanged"] is True
    assert report["bitstream_comparison"]["recommended_diagnostic_candidate"] in methods
    assert report["continuous_measurements"]["observed_amplitudes"]["sample_count"] == frames.size
    assert "not proof" in report["conclusion"].lower()
    assert report["external_randomness_tools"]["status"] == "not requested"
    for export in report["bitstream_exports"].values():
        assert (result.output_dir / export["packed_binary"]).is_file()
        assert (result.output_dir / export["one_bit_per_byte_binary"]).is_file()


def test_entropy_analysis_handles_constant_camera_session(tmp_path: Path) -> None:
    frames = np.full((80, 3, 3), 42, dtype=np.uint8)
    session = tmp_path / "capture"
    _session(session, frames)

    result = analyze_entropy(
        EntropyConfig(
            session_dir=session,
            output_dir=tmp_path / "entropy",
            channel="intensity",
            max_samples=10_000,
        )
    )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert "not demonstrated" in report["conclusion"].lower()
    assert report["continuous_measurements"]["observed_amplitudes"][
        "shannon_entropy_bits_per_symbol_plugin"
    ] == 0.0
    assert all(
        not item["metrics"]["claim_levels"]["looks_random"]
        for item in report["bit_extraction_methods"].values()
    )
