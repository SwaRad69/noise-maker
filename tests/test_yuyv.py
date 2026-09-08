from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from camera_noise.analysis.yuyv import (
    bit_diagnostics,
    channel_summary,
    extract_channels,
    grid_coordinates,
    held_out_predictor,
    load_packed_yuyv,
    pixel_autocorrelation,
    reshape_packed,
    temporal_differences,
    verify_yuyv_file,
)


def _write_yuyv(tmp_path: Path, frames: int, height: int, width: int, name: str = "sample.yuv") -> Path:
    buffer = np.zeros((frames, height, width * 2), dtype=np.uint8)
    for frame_index in range(frames):
        for row in range(height):
            buffer[frame_index, row, 0::2] = (10 * frame_index + 2 * row + np.arange(width)) % 256
        buffer[frame_index, :, 1::4] = 100 + frame_index
        buffer[frame_index, :, 3::4] = 200 - frame_index
    path = tmp_path / name
    buffer.tofile(path)
    return path


def test_verify_yuyv_file_accepts_exact_size(tmp_path: Path) -> None:
    path = _write_yuyv(tmp_path, frames=3, height=2, width=4)
    result = verify_yuyv_file(path, width=4, height=2, frame_count=3)
    assert result["consistent"] is True
    assert result["actual_bytes"] == 3 * 2 * 4 * 2


def test_verify_yuyv_file_rejects_wrong_size(tmp_path: Path) -> None:
    path = _write_yuyv(tmp_path, frames=3, height=2, width=4)
    with pytest.raises(ValueError, match="does not match"):
        verify_yuyv_file(path, width=4, height=2, frame_count=5)


def test_verify_yuyv_file_rejects_missing_and_odd_width(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        verify_yuyv_file(tmp_path / "missing.yuv", width=4, height=2, frame_count=1)
    with pytest.raises(ValueError, match="even"):
        verify_yuyv_file(tmp_path / "x.yuv", width=5, height=2, frame_count=1)


def test_load_packed_yuyv_shape(tmp_path: Path) -> None:
    path = _write_yuyv(tmp_path, frames=3, height=2, width=4)
    packed = load_packed_yuyv(path, width=4, height=2, frame_count=3)
    assert packed.shape == (3, 2, 8)
    assert packed.dtype == np.uint8


def test_reshape_packed_shape_and_error() -> None:
    packed = np.zeros((2, 3, 8), dtype=np.uint8)
    yuyv = reshape_packed(packed, height=3, width=4)
    assert yuyv.shape == (2, 3, 4, 2)
    with pytest.raises(ValueError, match="Expected packed shape"):
        reshape_packed(np.zeros((2, 3, 9), dtype=np.uint8), height=3, width=4)


def test_extract_channels_y_u_v() -> None:
    yuyv = np.zeros((1, 2, 4, 2), dtype=np.uint8)
    yuyv[..., 0] = np.array([[0, 1, 2, 3], [4, 5, 6, 7]])
    yuyv[0, 0, 0, 1] = 100
    yuyv[0, 0, 1, 1] = 200
    yuyv[0, 0, 2, 1] = 110
    yuyv[0, 0, 3, 1] = 210
    yuyv[0, 1, 0, 1] = 120
    yuyv[0, 1, 1, 1] = 220
    yuyv[0, 1, 2, 1] = 130
    yuyv[0, 1, 3, 1] = 230
    y, u, v = extract_channels(yuyv)
    assert y.shape == (1, 2, 4)
    assert u.shape == (1, 2, 2)
    assert v.shape == (1, 2, 2)
    np.testing.assert_array_equal(y[0], np.array([[0, 1, 2, 3], [4, 5, 6, 7]]))
    np.testing.assert_array_equal(u[0], np.array([[100, 110], [120, 130]]))
    np.testing.assert_array_equal(v[0], np.array([[200, 210], [220, 230]]))


def test_temporal_differences_duplicate_and_change() -> None:
    channel = np.zeros((4, 1, 4), dtype=np.uint8)
    channel[0] = [[0, 10, 20, 30]]
    channel[1] = [[0, 10, 20, 30]]
    channel[2] = [[0, 11, 20, 31]]
    channel[3] = [[0, 5, 20, 31]]
    result = temporal_differences(channel)
    assert result["transitions"] == 3
    assert result["duplicate_frame_count"] == 1
    assert result["duplicate_frame_indices"] == [1]
    assert result["changed_fraction"] == pytest.approx(3 / 12)
    assert result["pm1_fraction_of_all"] == pytest.approx(2 / 12)
    assert result["gt1_fraction_of_all"] == pytest.approx(1 / 12)
    assert result["max_absolute"] == 6
    assert result["rms"] == pytest.approx(np.sqrt((1.0 + 1.0 + 36.0) / 12))


def test_duplicate_detection_all_identical() -> None:
    channel = np.full((5, 2, 3), 16, dtype=np.uint8)
    result = temporal_differences(channel)
    assert result["duplicate_frame_count"] == 4
    assert result["changed_fraction"] == 0.0


def test_channel_summary_stats() -> None:
    channel = np.array([[[0, 255, 0], [128, 0, 255]]], dtype=np.uint8)
    summary = channel_summary(channel)
    assert summary["min"] == 0
    assert summary["max"] == 255
    assert summary["mean"] == pytest.approx((0.0 + 255.0 + 0.0 + 128.0 + 0.0 + 255.0) / 6, abs=1e-9)
    assert summary["zero_fraction"] == pytest.approx(3 / 6)
    assert summary["full_fraction"] == pytest.approx(2 / 6)
    assert summary["unique_value_count"] == 3


def test_grid_coordinates_spread_and_count() -> None:
    points = grid_coordinates(720, 1280, 25)
    assert len(points) == 25
    ys = [p[0] for p in points]
    xs = [p[1] for p in points]
    assert min(xs) == 0 and max(xs) == 1279
    assert min(ys) == 0 and max(ys) == 719


def test_pixel_autocorrelation_lag0_and_lag1(tmp_path: Path) -> None:
    channel = np.zeros((10, 2, 2), dtype=np.uint8)
    for t in range(10):
        channel[t] = 16 + t
    points = grid_coordinates(2, 2, 2)
    result = pixel_autocorrelation(channel, points, max_lag=2)
    acf = result["mean_autocorrelation"]
    assert acf[0] == pytest.approx(1.0)
    assert acf[1] == pytest.approx(1.0, abs=0.05)


def test_held_out_predictor_better_than_constant_for_ramp() -> None:
    channel = np.zeros((20, 1, 1), dtype=np.uint8)
    for t in range(20):
        channel[t, 0, 0] = 16 + t
    result = held_out_predictor(channel, [(0, 0)])
    assert result["lag1_predictor_mean_absolute_error"] < result["constant_predictor_mean_absolute_error"]
    assert result["lag1_predictor_mean_absolute_error"] == pytest.approx(1.0)
    assert result["lag1_predictor_exact_match_fraction"] == 0.0


def test_bit_diagnostics_bias_and_runs() -> None:
    channel = np.zeros((2, 1, 4), dtype=np.uint8)
    channel[0, 0] = [0, 1, 0, 1]
    channel[1, 0] = [0, 1, 0, 1]
    result = bit_diagnostics(channel)
    assert result["fraction_ones"] == pytest.approx(0.5)
    assert result["bias_from_0_5"] == pytest.approx(0.0)
    assert result["runs"] > 1
