"""Diagnostic analysis of packed YUYV 4:2:2 webcam streams.

This is a characterization layer only. It decodes the packed YUYV byte stream
directly (no BGR conversion) and reports descriptive statistics, temporal
variation, spatial dependence, common-mode behaviour, and LSB diagnostics.

It deliberately does NOT perform entropy extraction, cryptographic
conditioning, or hashing, and it makes no claim that the observed stream is
random or cryptographically secure. YUYV from a DirectShow device is an
uncompressed video representation, not raw sensor data: an image signal
processor, demosaicing, and firmware stages may all sit upstream of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from camera_noise.analysis.math_utils import PairAccumulator, pearson


def verify_yuyv_file(path: Path, width: int, height: int, frame_count: int) -> dict[str, Any]:
    """Verify byte size and consistency with W x H x 2 x frame_count packing."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if width % 2 != 0:
        raise ValueError("YUYV 4:2:2 requires an even number of pixels per row")
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")
    if not Path(path).is_file():
        raise FileNotFoundError(f"YUYV file not found: {path}")
    actual = Path(path).stat().st_size
    expected = width * height * 2 * frame_count
    result = {
        "path": str(path),
        "width": width,
        "height": height,
        "frame_count": frame_count,
        "bytes_per_frame": width * height * 2,
        "expected_bytes": expected,
        "actual_bytes": actual,
        "consistent": actual == expected,
    }
    if actual != expected:
        result["mismatch"] = (
            f"File size {actual} does not match {width}x{height}x2x{frame_count} "
            f"= {expected} for YUYV 4:2:2. Do not reinterpret the file."
        )
        raise ValueError(result["mismatch"])
    return result


def load_packed_yuyv(
    path: Path, width: int, height: int, frame_count: int
) -> np.ndarray:
    """Load the packed stream as uint8 with shape (frames, height, width * 2)."""
    verify_yuyv_file(path, width, height, frame_count)
    data = np.fromfile(path, dtype=np.uint8)
    return data.reshape(frame_count, height, width * 2)


def reshape_packed(packed: np.ndarray, height: int, width: int) -> np.ndarray:
    """Reshape (frames, height, width*2) into (frames, height, width, 2)."""
    packed = np.asarray(packed, dtype=np.uint8)
    if packed.ndim != 3 or packed.shape[1] != height or packed.shape[2] != width * 2:
        raise ValueError(
            f"Expected packed shape (frames, {height}, {width * 2}), got {packed.shape}"
        )
    return packed.reshape(packed.shape[0], height, width, 2)


def extract_channels(yuyv: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split YUYV into separate Y, U, V arrays.

    Packing per 4 bytes: Y0 U0 Y1 V0, then Y2 U1 Y3 V1, ... The chroma byte at
    each even horizontal position is U; each odd horizontal position is V.
    Returns (Y, U, V) with shapes (F,H,W), (F,H,W/2), (F,H,W/2).
    """
    yuyv = np.asarray(yuyv)
    if yuyv.ndim != 4 or yuyv.shape[3] != 2:
        raise ValueError("Expected yuyv array of shape (frames, height, width, 2)")
    y = yuyv[..., 0]
    chroma = yuyv[..., 1]
    u = chroma[..., 0::2]
    v = chroma[..., 1::2]
    return y, u, v


def channel_summary(channel: np.ndarray, histogram_bins: int = 256) -> dict[str, Any]:
    values = np.asarray(channel)
    flat = values.ravel()
    counts = np.bincount(flat.astype(np.int64), minlength=histogram_bins)[:histogram_bins]
    levels = np.flatnonzero(counts)
    top = levels[np.argsort(counts[levels])[::-1]]
    top_count = min(12, len(top))
    nonzero = counts[levels]
    total = int(flat.size)
    return {
        "shape": list(values.shape),
        "dtype": str(values.dtype),
        "min": int(values.min()),
        "max": int(values.max()),
        "mean": float(values.mean()),
        "standard_deviation": float(values.std()),
        "variance": float(values.var()),
        "unique_value_count": int(len(levels)),
        "zero_fraction": float(np.mean(flat == 0)),
        "full_fraction": float(np.mean(flat == 255)),
        "median": float(np.median(flat)),
        "p25": float(np.percentile(flat, 25)),
        "p75": float(np.percentile(flat, 75)),
        "histogram_top_levels": [
            {"level": int(level), "count": int(counts[level]), "fraction": float(counts[level] / total)}
            for level in top[:top_count]
        ],
        "nonzero_levels": int(len(levels)),
        "histogram_mass_top_level": (
            float(counts[top[0]] / total) if len(top) else 0.0
        ),
        "sample_count": total,
    }


def frame_statistics(channel: np.ndarray) -> dict[str, Any]:
    frame_mean = np.asarray(channel, dtype=np.float64).mean(axis=(1, 2))
    frame_var = np.asarray(channel, dtype=np.float64).var(axis=(1, 2))
    frame_min = np.asarray(channel).min(axis=(1, 2))
    frame_max = np.asarray(channel).max(axis=(1, 2))


    def _change(series: np.ndarray) -> dict[str, float]:
        if len(series) < 2:
            return {"mean_abs_change": 0.0, "max_abs_change": 0.0, "changed_frames": 0}
        delta = np.abs(np.diff(series))
        return {
            "mean_abs_change": float(delta.mean()),
            "max_abs_change": float(delta.max()),
            "changed_frames": int(np.count_nonzero(delta > 0)),
        }

    return {
        "frame_mean": frame_mean.tolist(),
        "frame_variance": frame_var.tolist(),
        "frame_min": frame_min.tolist(),
        "frame_max": frame_max.tolist(),
        "frame_mean_change": _change(frame_mean),
        "frame_variance_change": _change(frame_var),
        "frame_min_change": _change(frame_min.astype(np.float64)),
        "frame_max_change": _change(frame_max.astype(np.float64)),
        "frame_mean_overall": float(frame_mean.mean()),
        "frame_mean_std": float(frame_mean.std()),
        "frame_variance_overall": float(frame_var.mean()),
        "frame_variance_std": float(frame_var.std()),
    }


def temporal_differences(channel: np.ndarray) -> dict[str, Any]:
    values = np.asarray(channel)
    if len(values) < 2:
        return {"transitions": 0, "note": "Fewer than two frames."}
    diffs = values[1:].astype(np.int16) - values[:-1].astype(np.int16)
    absolute = np.abs(diffs)
    total = absolute.size
    changed = absolute > 0
    pm1 = absolute == 1
    gt1 = absolute > 1
    duplicates = np.all(diffs == 0, axis=(1, 2))
    duplicate_indices = (np.flatnonzero(duplicates) + 1).astype(int)
    return {
        "transitions": int(len(values) - 1),
        "mean_absolute": float(absolute.mean()),
        "rms": float(np.sqrt(np.mean(diffs.astype(np.float64) ** 2))),
        "standard_deviation": float(diffs.astype(np.float64).std()),
        "mean": float(diffs.astype(np.float64).mean()),
        "changed_fraction": float(changed.mean()),
        "pm1_fraction_of_all": float(pm1.mean()),
        "gt1_fraction_of_all": float(gt1.mean()),
        "pm1_fraction_of_changed": float(pm1[changed].mean()) if changed.any() else 0.0,
        "gt1_fraction_of_changed": float(gt1[changed].mean()) if changed.any() else 0.0,
        "duplicate_frame_count": int(len(duplicate_indices)),
        "duplicate_frame_indices": duplicate_indices.tolist(),
        "max_absolute": int(absolute.max()),
        "mean_absolute_normalized": float(absolute.mean() / 255.0),
    }


def grid_coordinates(height: int, width: int, count: int) -> list[tuple[int, int]]:
    if count <= 0:
        return []
    side = int(np.ceil(np.sqrt(count)))
    ys = np.unique(np.linspace(0, height - 1, side, dtype=int))
    xs = np.unique(np.linspace(0, width - 1, side, dtype=int))
    points = [(int(y), int(x)) for y in ys for x in xs]
    if len(points) <= count:
        return points
    indices = np.linspace(0, len(points) - 1, count, dtype=int)
    return [points[index] for index in indices]


def pixel_traces(channel: np.ndarray, coordinates: list[tuple[int, int]]) -> np.ndarray:
    values = np.asarray(channel)
    trace = np.empty((len(values), len(coordinates)), dtype=np.float64)
    for index, (y, x) in enumerate(coordinates):
        trace[:, index] = values[:, y, x]
    return trace


def pixel_autocorrelation(channel: np.ndarray, coordinates: list[tuple[int, int]], max_lag: int) -> dict[str, Any]:
    from camera_noise.analysis.math_utils import autocorrelation

    traces = pixel_traces(channel, coordinates)
    finite = np.zeros(max_lag + 1, dtype=np.float64)
    counts = np.zeros(max_lag + 1, dtype=np.int64)
    per_point: list[dict[str, Any]] = []
    for index, (y, x) in enumerate(coordinates):
        acf = autocorrelation(traces[:, index], max_lag)
        per_point.append(
            {
                "coordinate": [y, x],
                "autocorrelation": [None if not np.isfinite(value) else float(value) for value in acf],
                "series": traces[:, index].tolist(),
            }
        )
        good = np.isfinite(acf)
        finite[good] += acf[good]
        counts[good] += 1
    mean_acf = np.divide(
        finite,
        counts,
        out=np.full(max_lag + 1, np.nan),
        where=counts > 0,
    )
    return {
        "coordinate_count": len(coordinates),
        "sample_count": int(traces.shape[0]),
        "max_lag": max_lag,
        "mean_autocorrelation": [None if not np.isfinite(value) else float(value) for value in mean_acf],
        "per_point": per_point,
        "note": (
            f"Autocorrelation uses only {int(traces.shape[0])} temporal samples per point; "
            "that is too few for strong statistical conclusions."
        ),
    }


def held_out_predictor(channel: np.ndarray, coordinates: list[tuple[int, int]]) -> dict[str, Any]:
    traces = pixel_traces(channel, coordinates)
    n = int(traces.shape[0])
    if n < 6:
        return {"note": "Fewer than 6 temporal samples."}
    split = n // 2
    constant_mae: list[float] = []
    lag1_mae: list[float] = []
    lag1_exact: list[float] = []
    for index in range(traces.shape[1]):
        series = traces[:, index]
        train = series[:split]
        test = series[split:]
        constant_mae.append(float(np.abs(test - train.mean()).mean()))
        previous = np.roll(test, 1)
        previous[0] = train[-1]
        lag1_mae.append(float(np.abs(test - previous).mean()))
        lag1_exact.append(float(np.mean(test == previous)))
    all_mae = []
    for index in range(traces.shape[1]):
        series = traces[:, index]
        all_mae.append(float(np.abs(np.diff(series)).mean()))
    return {
        "train_frames": int(split),
        "test_frames": int(n - split),
        "points": int(traces.shape[1]),
        "constant_predictor_mean_absolute_error": float(np.mean(constant_mae)),
        "lag1_predictor_mean_absolute_error": float(np.mean(lag1_mae)),
        "lag1_predictor_exact_match_fraction": float(np.mean(lag1_exact)),
        "overall_mean_abs_diff": float(np.mean(all_mae)),
        "note": "Held-out constant and lag-1 predictors; diagnostic only, not an entropy estimate.",
    }


def _subsample(offset: int, frame: np.ndarray, row_stride: int, col_stride: int) -> tuple[np.ndarray, np.ndarray]:
    rows = slice(None, None, row_stride)
    cols = slice(None, None, col_stride)
    if offset > 0:
        a = frame[rows, cols]
        b = frame[rows, offset::col_stride]
        length = min(a.shape[1], b.shape[1])
        return a[:, :length], b[:, :length]
    return frame[rows, cols], frame[rows, cols]


def pooled_spatial_correlations(
    channel: np.ndarray,
    horizontal_offsets: list[int],
    vertical_offsets: list[int],
    row_stride: int = 2,
    col_stride: int = 4,
) -> dict[str, Any]:
    values = np.asarray(channel, dtype=np.float64)
    frames, height, width = values.shape
    accumulators: dict[str, PairAccumulator] = {}
    for offset in horizontal_offsets:
        if 1 <= offset < width:
            accumulators[("h", offset)] = PairAccumulator()
    for offset in vertical_offsets:
        if 1 <= offset < height:
            accumulators[("v", offset)] = PairAccumulator()
    for frame_index in range(frames):
        frame = values[frame_index]
        for key, accumulator in accumulators.items():
            direction, offset = key
            if direction == "h":
                a, b = _subsample(offset, frame, row_stride, col_stride)
            else:
                a, b = _subsample(offset, frame.T, col_stride, row_stride)
            accumulator.update(a, b)
    results: dict[str, dict[str, Any]] = {}
    for key, accumulator in accumulators.items():
        direction, offset = key
        label = f"{direction}_{offset}"
        results[label] = {
            "direction": direction,
            "offset_pixels": offset,
            "correlation": accumulator.correlation(),
            "sample_count": accumulator.n,
        }
    return results


def adjacent_correlations(channel: np.ndarray) -> dict[str, Any]:
    values = np.asarray(channel, dtype=np.float64)
    frames, height, width = values.shape
    accumulators = {
        "horizontal": PairAccumulator(),
        "vertical": PairAccumulator(),
        "diagonal": PairAccumulator(),
    }
    for frame_index in range(frames):
        frame = values[frame_index]
        accumulators["horizontal"].update(frame[:, :-1], frame[:, 1:])
        if height > 1:
            accumulators["vertical"].update(frame[:-1, :], frame[1:, :])
        if height > 1:
            accumulators["diagonal"].update(frame[:-1, :-1], frame[1:, 1:])
    return {name: acc.correlation() for name, acc in accumulators.items()}


def _sign_agreement(reference: np.ndarray, traces: np.ndarray) -> float:
    ref_change = np.sign(np.diff(reference))
    trace_change = np.sign(np.diff(traces, axis=0))
    active = ref_change != 0
    if not active.any():
        return 0.0
    agreement = np.mean(np.sign(trace_change[active]) == ref_change[active][:, None], axis=0)
    return float(agreement.mean())


def common_mode_analysis(channel: np.ndarray, point_count: int = 25) -> dict[str, Any]:
    values = np.asarray(channel, dtype=np.float64)
    frames, height, width = values.shape
    frame_mean = values.mean(axis=(1, 2))
    frame_var = values.var(axis=(1, 2))
    coordinates = grid_coordinates(height, width, point_count)
    traces = pixel_traces(channel, coordinates)
    correlation_matrix = np.full((len(coordinates), len(coordinates)), np.nan)
    for i in range(len(coordinates)):
        for j in range(len(coordinates)):
            correlation_matrix[i, j] = pearson(traces[:, i], traces[:, j])
    off_diagonal = correlation_matrix[~np.eye(len(coordinates), dtype=bool)]
    finite_off = off_diagonal[np.isfinite(off_diagonal)]
    pixel_to_frame_mean = []
    for index in range(len(coordinates)):
        value = pearson(traces[:, index], frame_mean)
        if value is not None:
            pixel_to_frame_mean.append(value)
    return {
        "frame_mean": frame_mean.tolist(),
        "frame_variance": frame_var.tolist(),
        "frame_mean_std": float(frame_mean.std()),
        "frame_mean_min": float(frame_mean.min()),
        "frame_mean_max": float(frame_mean.max()),
        "distant_point_count": len(coordinates),
        "distant_point_coordinates": [[y, x] for y, x in coordinates],
        "distant_correlation_matrix": correlation_matrix.tolist(),
        "distant_off_diagonal_mean_abs_correlation": (
            float(np.abs(finite_off).mean()) if len(finite_off) else None
        ),
        "distant_off_diagonal_max_abs_correlation": (
            float(np.abs(finite_off).max()) if len(finite_off) else None
        ),
        "distant_off_diagonal_min_abs_correlation": (
            float(np.abs(finite_off).min()) if len(finite_off) else None
        ),
        "mean_pixel_frame_mean_correlation": (
            float(np.mean(pixel_to_frame_mean)) if pixel_to_frame_mean else None
        ),
        "mean_pixel_frame_mean_abs_correlation": (
            float(np.mean(np.abs(pixel_to_frame_mean))) if pixel_to_frame_mean else None
        ),
        "frame_mean_sign_agreement_fraction": _sign_agreement(frame_mean, traces),
        "interpretation": (
            "If distant points covary strongly with the frame mean, most apparent temporal "
            "variation is frame-wide/common-mode rather than independent per-pixel noise."
        ),
    }


def bit_diagnostics(channel: np.ndarray) -> dict[str, Any]:
    values = np.asarray(channel)
    bits = (values & 1).astype(np.int8)
    flat = bits.ravel()
    n = int(flat.size)
    ones = int(flat.sum())
    fraction_ones = float(ones / n)
    bias = fraction_ones - 0.5
    runs = int(np.count_nonzero(flat[1:] != flat[:-1]) + 1)
    expected_runs = 1.0 + 2.0 * ones * (n - ones) / n
    variance_runs = (
        2.0
        * ones
        * (n - ones)
        * (2.0 * ones * (n - ones) - n)
        / (n * n * (n - 1))
    )
    runs_z = (runs - expected_runs) / np.sqrt(variance_runs) if variance_runs > 0 else None
    temporal_flat = bits[1:] != bits[:-1]
    lag1_corr = pearson(bits[:-1], bits[1:])
    spatial_flat = None
    if values.shape[-1] > 1:
        spatial_flat = float(
            np.mean((bits[..., 0:-1] == bits[..., 1:]).ravel())
        )
    return {
        "sample_count": n,
        "fraction_ones": fraction_ones,
        "bias_from_0_5": bias,
        "runs": runs,
        "expected_runs": expected_runs,
        "runs_z_score": None if runs_z is None else float(runs_z),
        "lag1_bit_autocorrelation": lag1_corr,
        "adjacent_lsb_pair_same_fraction": spatial_flat,
        "note": (
            "Diagnostic only. The flattened spatiotemporal LSB stream violates the IID assumption "
            "of the runs statistic because of spatial/temporal dependence, so z-scores are descriptive."
        ),
    }


def analyze_yuyv_file(
    path: Path,
    width: int,
    height: int,
    frame_count: int,
    pixel_point_count: int = 25,
    spatial_horizontal_offsets: list[int] | None = None,
    spatial_vertical_offsets: list[int] | None = None,
    max_lag: int = 8,
) -> dict[str, Any]:
    """Run the full diagnostic pipeline over a packed YUYV file."""
    verification = verify_yuyv_file(path, width, height, frame_count)
    packed = load_packed_yuyv(path, width, height, frame_count)
    yuyv = reshape_packed(packed, height, width)
    y, u, v = extract_channels(yuyv)
    horizontal_offsets = spatial_horizontal_offsets or [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
    vertical_offsets = spatial_vertical_offsets or [1, 2, 4, 8, 16, 32, 64, 128, 256, 512]

    packed_duplicates = np.all(packed[1:] == packed[:-1], axis=(1, 2))
    packed_duplicate_indices = (np.flatnonzero(packed_duplicates) + 1).astype(int)

    channels: dict[str, Any] = {}
    temporal: dict[str, Any] = {}
    pixel_series: dict[str, Any] = {}
    spatial: dict[str, Any] = {}
    common: dict[str, Any] = {}
    bits: dict[str, Any] = {}
    for name, channel in (("y", y), ("u", u), ("v", v)):
        coordinates = grid_coordinates(channel.shape[1], channel.shape[2], pixel_point_count)
        channels[name] = channel_summary(channel)
        temporal[name] = temporal_differences(channel)
        pixel_series[name] = {
            "autocorrelation": pixel_autocorrelation(channel, coordinates, max_lag),
            "held_out_predictor": held_out_predictor(channel, coordinates),
        }
        spatial[name] = {
            "adjacent": adjacent_correlations(channel),
            "by_distance": pooled_spatial_correlations(
                channel, horizontal_offsets, vertical_offsets
            ),
        }
        common[name] = common_mode_analysis(channel, point_count=pixel_point_count)
        bits[name] = bit_diagnostics(channel)

    return {
        "schema_version": 1,
        "status": "complete",
        "source_file": str(path),
        "verification": verification,
        "decoding": {
            "packing": "YUYV 4:2:2 (packed YUY2)",
            "layout": "Y0 U0 Y1 V0 Y2 U1 Y3 V1 ...",
            "packed_shape": list(packed.shape),
            "y_shape": list(y.shape),
            "u_shape": list(u.shape),
            "v_shape": list(v.shape),
            "sampling": {
                "chroma_subsampling": "4:2:2 (chroma sampled every second horizontal position)",
                "u_sites": "every even horizontal x within each row",
                "v_sites": "every odd horizontal x within each row",
                "note": "Y and chroma share the same vertical grid; U and V are not independent per-pixel samples.",
            },
            "packed_duplicate_frame_count": int(len(packed_duplicate_indices)),
            "packed_duplicate_frame_indices": packed_duplicate_indices.tolist(),
        },
        "frame_statistics": {name: frame_statistics(ch) for name, ch in (("y", y), ("u", u), ("v", v))},
        "channels": channels,
        "temporal": temporal,
        "pixel_series": pixel_series,
        "spatial": spatial,
        "common_mode": common,
        "bit_diagnostics": bits,
        "scientific_cautions": [
            "physical variation != entropy != independent entropy != cryptographic security",
            "YUYV is an uncompressed video representation, not raw sensor data.",
            "Uncompressed does not mean unprocessed: the camera ISP may run upstream of YUYV output.",
            "30 temporal samples are insufficient for strong statistical conclusions.",
        ],
    }


def write_yuyv_results(results: dict[str, Any], output_dir: Path) -> Path:
    import json

    from camera_noise.analysis.math_utils import json_safe

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "yuyv_analysis.json"
    path.write_text(json.dumps(json_safe(results), indent=2), encoding="utf-8")
    return path
