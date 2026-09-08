from __future__ import annotations

import platform
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "camera-noise-matplotlib"))

import matplotlib
import numpy as np

from camera_noise.storage import utc_now_iso, write_json_atomic

from .math_utils import (
    PairAccumulator,
    autocorrelation,
    histogram_percentiles,
    json_safe,
    pearson,
)
from .models import AnalysisConfig
from .plots import generate_plots
from .session import LoadedSession, load_session


PERCENTILES = (0.1, 1.0, 5.0, 25.0, 50.0, 75.0, 95.0, 99.0, 99.9)


@dataclass(frozen=True)
class AnalysisResult:
    output_dir: Path
    report_path: Path
    plots: tuple[Path, ...]
    findings: dict[str, Any]


def _grid_points(width: int, height: int, maximum: int) -> list[tuple[int, int]]:
    count = min(maximum, width * height)
    side = max(1, int(np.ceil(np.sqrt(count))))
    xs = np.unique(np.linspace(0, width - 1, side, dtype=int))
    ys = np.unique(np.linspace(0, height - 1, side, dtype=int))
    points = [(int(x), int(y)) for y in ys for x in xs]
    if len(points) <= maximum:
        return points
    indices = np.linspace(0, len(points) - 1, maximum, dtype=int)
    return [points[index] for index in indices]


def _window_bounds(frame_count: int, windows: int) -> list[tuple[int, int]]:
    count = min(frame_count, windows)
    return [(int(chunk[0]), int(chunk[-1]) + 1) for chunk in np.array_split(np.arange(frame_count), count)]


def _window_index(index: int, bounds: list[tuple[int, int]]) -> int:
    for window, (start, stop) in enumerate(bounds):
        if start <= index < stop:
            return window
    return len(bounds) - 1


def _spectrum(series: np.ndarray, times: np.ndarray, time_source: str, peaks: int) -> dict[str, Any]:
    values = np.asarray(series, dtype=np.float64)
    if len(values) < 4:
        return {
            "frequency": np.empty(0),
            "power": np.empty(0),
            "dominant_peaks": [],
            "sample_rate": None,
            "frequency_unit": "unknown",
            "timing_jitter_fraction": None,
            "method": "Insufficient frames for spectral analysis.",
        }
    deltas = np.diff(times)
    median_delta = float(np.median(deltas))
    if median_delta <= 0:
        median_delta = 1.0
    frequency_unit = "cycles/frame" if time_source == "frame_index" else "Hz"
    sample_rate = 1.0 / median_delta
    jitter = float(np.median(np.abs(deltas - median_delta)) / median_delta) if len(deltas) else 0.0
    x = np.arange(len(values), dtype=np.float64)
    coefficients = np.polyfit(x, values, 1)
    detrended = values - np.polyval(coefficients, x)
    window = np.hanning(len(values))
    transformed = np.fft.rfft(detrended * window)
    power = np.abs(transformed) ** 2 / max(float(np.sum(window**2)), 1.0)
    frequency = np.fft.rfftfreq(len(values), d=median_delta)
    candidate = np.arange(1, len(power))
    if len(candidate):
        candidate = candidate[np.argsort(power[candidate])[::-1][:peaks]]
    dominant = [
        {"frequency": float(frequency[index]), "power": float(power[index])}
        for index in candidate
    ]
    return {
        "frequency": frequency,
        "power": power,
        "dominant_peaks": dominant,
        "sample_rate": sample_rate,
        "frequency_unit": frequency_unit,
        "timing_jitter_fraction": jitter,
        "linear_trend_removed": {"slope_per_frame": float(coefficients[0])},
        "method": "Linear detrend, Hann window, one-sided NumPy real FFT; no stochastic model assumed.",
    }


def _histogram_edges(
    session: LoadedSession, observed_min: float, observed_max: float, requested_bins: int
) -> tuple[np.ndarray, str]:
    direct_integer = session.channel in {"b", "g", "r", "intensity"} and np.issubdtype(
        session.source_dtype, np.integer
    )
    observed_levels = int(round(observed_max - observed_min)) + 1
    if direct_integer and observed_levels <= requested_bins:
        return (
            np.arange(observed_min - 0.5, observed_max + 1.5, 1.0),
            "exact_integer_levels",
        )
    if observed_max == observed_min:
        return np.array([observed_min - 0.5, observed_max + 0.5]), "single_value"
    return np.linspace(observed_min, observed_max, requested_bins + 1), "fixed_width_bins"


def _neighbor_accumulators() -> dict[str, PairAccumulator]:
    return {name: PairAccumulator() for name in ("horizontal", "vertical", "diagonal")}


def _update_neighbors(accumulators: dict[str, PairAccumulator], frame: np.ndarray) -> None:
    if frame.shape[1] > 1:
        accumulators["horizontal"].update(frame[:, :-1], frame[:, 1:])
    if frame.shape[0] > 1:
        accumulators["vertical"].update(frame[:-1, :], frame[1:, :])
    if frame.shape[0] > 1 and frame.shape[1] > 1:
        accumulators["diagonal"].update(frame[:-1, :-1], frame[1:, 1:])


def _correlation_values(accumulators: dict[str, PairAccumulator]) -> dict[str, float | None]:
    return {name: accumulator.correlation() for name, accumulator in accumulators.items()}


def _detect_steps(series: np.ndarray, value_range: float | None) -> dict[str, Any]:
    if len(series) < 2:
        return {"indices": [], "threshold": None, "method": "Insufficient frames."}
    changes = np.diff(series)
    center = float(np.median(changes))
    mad = float(np.median(np.abs(changes - center)))
    robust_scale = 1.4826 * mad
    floor = 0.01 * value_range if value_range and value_range > 0 else 0.0
    threshold = max(5.0 * robust_scale, floor, np.finfo(float).eps)
    instantaneous = (np.flatnonzero(np.abs(changes - center) > threshold) + 1).astype(int)

    window = min(10, max(3, len(series) // 10))
    contrasts = np.full(len(series), np.nan, dtype=np.float64)
    for index in range(window, len(series) - window + 1):
        contrasts[index] = float(
            np.median(series[index : index + window])
            - np.median(series[index - window : index])
        )
    finite = contrasts[np.isfinite(contrasts)]
    sustained: list[int] = []
    sustained_threshold: float | None = None
    if len(finite):
        contrast_center = float(np.median(finite))
        contrast_mad = float(np.median(np.abs(finite - contrast_center)))
        sustained_floor = 0.05 * value_range if value_range and value_range > 0 else 0.0
        sustained_threshold = max(
            6.0 * 1.4826 * contrast_mad,
            sustained_floor,
            np.finfo(float).eps,
        )
        candidates = np.flatnonzero(np.abs(contrasts - contrast_center) > sustained_threshold)
        if len(candidates):
            groups = np.split(candidates, np.flatnonzero(np.diff(candidates) > 1) + 1)
            sustained = [
                int(group[np.nanargmax(np.abs(contrasts[group] - contrast_center))])
                for group in groups
            ]
    indices = sorted(set(instantaneous.tolist() + sustained))
    return {
        "indices": indices,
        "instantaneous_indices": instantaneous.tolist(),
        "sustained_indices": sustained,
        "changes": [float(changes[index - 1]) for index in indices],
        "threshold": float(threshold),
        "sustained_threshold": sustained_threshold,
        "sustained_window_frames": window,
        "median_change": center,
        "mad_change": mad,
        "method": (
            "Union of instantaneous frame-median changes beyond max(5 x scaled MAD, 1% source range) "
            "and sustained before/after median contrasts beyond max(6 x scaled MAD, 5% source range). "
            "Adjacent sustained candidates are consolidated. Heuristic only."
        ),
    }


def _block_boundary_ratio(mean_map: np.ndarray, roi_x: int, roi_y: int) -> dict[str, float | None]:
    results: dict[str, float | None] = {}
    for axis, offset, label in ((1, roi_x, "vertical_boundaries"), (0, roi_y, "horizontal_boundaries")):
        differences = np.abs(np.diff(mean_map, axis=axis))
        if differences.size == 0:
            results[label] = None
            continue
        coordinate_count = mean_map.shape[axis] - 1
        boundaries = ((np.arange(coordinate_count) + offset + 1) % 8) == 0
        boundary_values = np.take(differences, np.flatnonzero(boundaries), axis=axis)
        interior_values = np.take(differences, np.flatnonzero(~boundaries), axis=axis)
        boundary_mean = float(boundary_values.mean()) if boundary_values.size else 0.0
        interior_mean = float(interior_values.mean()) if interior_values.size else 0.0
        results[label] = None if interior_mean == 0 else boundary_mean / interior_mean
    return results


def _reported_property_changes(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    snapshots = [
        metadata.get("camera_properties_after_configuration", {}),
        metadata.get("camera_properties_after_warmup", {}),
        metadata.get("camera_properties_at_end", {}),
    ]
    names = (
        "exposure",
        "gain",
        "auto_exposure",
        "auto_white_balance",
        "white_balance_temperature",
        "fps",
    )
    changes: dict[str, dict[str, Any]] = {}
    for name in names:
        values = [snapshot.get(name) for snapshot in snapshots]
        finite = [float(value) for value in values if isinstance(value, (int, float)) and np.isfinite(value)]
        changed = len(finite) >= 2 and not np.allclose(finite, finite[0], rtol=0.0, atol=1e-9)
        changes[name] = {
            "after_configuration": values[0],
            "after_warmup": values[1],
            "at_end": values[2],
            "changed_between_available_snapshots": bool(changed),
        }
    return changes


def _interpret(metrics: dict[str, Any], session: LoadedSession) -> dict[str, Any]:
    descriptive = metrics["descriptive_statistics"]
    artifacts = metrics["artifact_detection"]
    stability = metrics["stability"]
    spatial = metrics["spatial_structure"]
    temporal = metrics["temporal_structure"]
    spectrum = metrics["spectral_analysis"]

    findings: dict[str, Any] = {
        "sensor_electronic_noise": {
            "evidence": [
                f"Median per-pixel temporal standard deviation: {spatial['median_temporal_std']:.6g}.",
                f"Frame-difference RMS median: {metrics['frame_differences']['rms_median']:.6g}.",
            ],
            "interpretation": (
                "Time-varying residuals are compatible with sensor/read/electronic noise, but processed "
                "webcam frames cannot isolate those sources from ISP and driver effects."
            ),
        },
        "fixed_pattern_effects": {
            "evidence": [
                f"Spatial standard deviation of the temporal mean image: {spatial['mean_image_spatial_std']:.6g}.",
                f"Fixed-pattern to temporal-noise ratio: {spatial['fixed_pattern_to_temporal_ratio']!r}.",
            ],
            "interpretation": (
                "A stable mean-image pattern above temporal variation suggests fixed offsets, illumination "
                "leakage, vignetting, or persistent camera processing; it is not fresh noise."
            ),
        },
        "drift": {
            "evidence": [
                f"ROI mean slope: {stability['roi_mean_slope_per_time_unit']:.6g} per {stability['time_unit']}.",
                f"First-to-last window mean change: {stability['first_to_last_mean_change']:.6g}.",
            ],
            "interpretation": (
                "Slow changes may reflect warm-up, dark-current change, automatic controls, light leakage, "
                "or environmental changes."
            ),
        },
        "environmental_contamination": {
            "evidence": [
                f"Detected frame-median step indices: {artifacts['exposure_change_candidates']['indices']}.",
                f"Reported camera property changes: {artifacts['reported_camera_property_changes']}.",
                f"Strongest spectral candidates: {spectrum['dominant_peaks']}.",
            ],
            "interpretation": (
                "Steps or narrow spectral peaks can indicate flicker, power interference, motion of a cover, "
                "thermal cycling, or camera control loops. Frequency peaks are diagnostic, not proof of origin."
            ),
        },
        "camera_processing_artifacts": {
            "evidence": [
                f"Low/high clipping fractions: {artifacts['low_clipping_fraction']:.6g} / "
                f"{artifacts['high_saturation_fraction']:.6g}.",
                f"Exactly duplicated consecutive frames: {artifacts['duplicate_consecutive_frames']}.",
                f"8-pixel boundary ratios: {artifacts['eight_pixel_boundary_discontinuity_ratio']}.",
            ],
            "interpretation": (
                "Clipping destroys amplitude information; duplicate frames, strong adjacent-pixel correlation, "
                "or enhanced 8-pixel boundaries can indicate buffering, denoising, demosaicing, or compression."
            ),
        },
        "temporal_correlation": {
            "evidence": [
                f"Median adjacent-frame spatial correlation: {temporal['adjacent_frame_correlation_median']!r}.",
                f"Lag-1 sampled-pixel correlation: {temporal['sampled_pixel_autocorrelation'][1] if len(temporal['sampled_pixel_autocorrelation']) > 1 else None!r}.",
            ],
            "interpretation": (
                "Nonzero lag correlations mean consecutive measurements retain memory or shared trends. "
                "No claim of independence or randomness is made."
            ),
        },
        "overall_cautions": [
            "The analyzed values are processed webcam output, not confirmed RAW sensor measurements.",
            "Attribution categories overlap; this analysis supplies evidence and warnings rather than source separation.",
            f"Analysis used {descriptive['sample_count']} projected ROI values from {session.valid_frame_count} frames.",
        ],
    }
    return findings


def analyze_session(config: AnalysisConfig) -> AnalysisResult:
    session = load_session(config)
    output_dir = (config.output_dir or session.session_dir / "analysis").resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    report_path = output_dir / "analysis_report.json"
    started_utc = utc_now_iso()
    write_json_atomic(
        report_path,
        {
            "schema_version": 1,
            "status": "analyzing",
            "started_utc": started_utc,
            "source_session": str(session.session_dir),
        },
    )

    n = session.valid_frame_count
    height, width = session.roi.height, session.roi.width
    times, time_source = session.elapsed_seconds()
    time_unit = "frames" if time_source == "frame_index" else "seconds"
    chosen_pixels = list(dict.fromkeys(config.pixels or ((session.roi.x + width // 2, session.roi.y + height // 2),)))
    local_pixels = [(x - session.roi.x, y - session.roi.y) for x, y in chosen_pixels]
    correlation_points = _grid_points(width, height, config.max_correlation_pixels)

    roi_mean = np.empty(n, dtype=np.float64)
    roi_median = np.empty(n, dtype=np.float64)
    frame_spatial_std = np.empty(n, dtype=np.float64)
    roi_min = np.empty(n, dtype=np.float64)
    roi_max = np.empty(n, dtype=np.float64)
    low_fraction = np.zeros(n, dtype=np.float64)
    high_fraction = np.zeros(n, dtype=np.float64)
    near_low_fraction = np.zeros(n, dtype=np.float64)
    near_high_fraction = np.zeros(n, dtype=np.float64)
    pixel_traces = np.empty((n, len(local_pixels)), dtype=np.float64)
    correlation_traces = np.empty((n, len(correlation_points)), dtype=np.float64)
    diff_mean = np.empty(max(0, n - 1), dtype=np.float64)
    diff_mean_abs = np.empty(max(0, n - 1), dtype=np.float64)
    diff_rms = np.empty(max(0, n - 1), dtype=np.float64)
    diff_max_abs = np.empty(max(0, n - 1), dtype=np.float64)
    diff_zero_fraction = np.empty(max(0, n - 1), dtype=np.float64)
    frame_correlation = np.empty(max(0, n - 1), dtype=np.float64)
    mean_map = np.zeros((height, width), dtype=np.float64)
    m2_map = np.zeros((height, width), dtype=np.float64)
    mean_abs_diff_map = np.zeros((height, width), dtype=np.float64)
    global_sum = global_sum_sq = 0.0
    global_count = 0
    observed_min = np.inf
    observed_max = -np.inf
    bounds = _window_bounds(n, config.windows)
    window_accumulators = [
        {"sum": 0.0, "sum_sq": 0.0, "count": 0, "min": np.inf, "max": -np.inf}
        for _ in bounds
    ]
    previous: np.ndarray | None = None
    for index in range(n):
        frame = session.project(index)
        count = frame.size
        frame_sum = float(frame.sum())
        frame_sum_sq = float(np.dot(frame.ravel(), frame.ravel()))
        global_sum += frame_sum
        global_sum_sq += frame_sum_sq
        global_count += count
        current_min = float(frame.min())
        current_max = float(frame.max())
        observed_min = min(observed_min, current_min)
        observed_max = max(observed_max, current_max)
        roi_mean[index] = frame_sum / count
        roi_median[index] = float(np.median(frame))
        frame_spatial_std[index] = float(frame.std())
        roi_min[index] = current_min
        roi_max[index] = current_max
        if session.value_min is not None and session.value_max is not None:
            value_range = session.value_max - session.value_min
            low_fraction[index] = np.mean(frame <= session.value_min)
            high_fraction[index] = np.mean(frame >= session.value_max)
            near_low_fraction[index] = np.mean(frame <= session.value_min + 0.01 * value_range)
            near_high_fraction[index] = np.mean(frame >= session.value_max - 0.01 * value_range)
        delta = frame - mean_map
        mean_map += delta / (index + 1)
        m2_map += delta * (frame - mean_map)
        for pixel_index, (x, y) in enumerate(local_pixels):
            pixel_traces[index, pixel_index] = frame[y, x]
        for pixel_index, (x, y) in enumerate(correlation_points):
            correlation_traces[index, pixel_index] = frame[y, x]
        window = _window_index(index, bounds)
        accumulator = window_accumulators[window]
        accumulator["sum"] += frame_sum
        accumulator["sum_sq"] += frame_sum_sq
        accumulator["count"] += count
        accumulator["min"] = min(accumulator["min"], current_min)
        accumulator["max"] = max(accumulator["max"], current_max)
        if previous is not None:
            difference = frame - previous
            absolute = np.abs(difference)
            diff_mean[index - 1] = float(difference.mean())
            diff_mean_abs[index - 1] = float(absolute.mean())
            diff_rms[index - 1] = float(np.sqrt(np.mean(difference**2)))
            diff_max_abs[index - 1] = float(absolute.max())
            diff_zero_fraction[index - 1] = float(np.mean(difference == 0))
            value = pearson(previous, frame)
            frame_correlation[index - 1] = np.nan if value is None else value
            mean_abs_diff_map += absolute
        previous = frame

    variance_map = m2_map / n
    std_map = np.sqrt(np.maximum(variance_map, 0.0))
    if n > 1:
        mean_abs_diff_map /= n - 1
    global_mean = global_sum / global_count
    global_variance = max(0.0, global_sum_sq / global_count - global_mean**2)
    edges, histogram_mode = _histogram_edges(
        session, float(observed_min), float(observed_max), config.histogram_bins
    )
    histogram = np.zeros(len(edges) - 1, dtype=np.int64)
    window_histograms = np.zeros((len(bounds), len(edges) - 1), dtype=np.int64)
    raw_neighbors = _neighbor_accumulators()
    fixed_removed_neighbors = _neighbor_accumulators()
    local_residual_neighbors = _neighbor_accumulators()
    for index in range(n):
        frame = session.project(index)
        counts, _ = np.histogram(frame, bins=edges)
        histogram += counts
        window_histograms[_window_index(index, bounds)] += counts
        _update_neighbors(raw_neighbors, frame)
        fixed_removed = frame - mean_map
        _update_neighbors(fixed_removed_neighbors, fixed_removed)
        local_residual = fixed_removed - (roi_mean[index] - global_mean)
        _update_neighbors(local_residual_neighbors, local_residual)

    discrete_percentiles = histogram_mode == "exact_integer_levels"
    percentiles = histogram_percentiles(
        histogram,
        edges,
        PERCENTILES,
        discrete_levels=discrete_percentiles,
    )
    max_lag = min(config.max_lag, n - 1)
    roi_mean_acf = autocorrelation(roi_mean, max_lag)
    roi_median_acf = autocorrelation(roi_median, max_lag)
    pixel_acfs = np.array(
        [autocorrelation(correlation_traces[:, index], max_lag) for index in range(len(correlation_points))]
    )
    finite_counts = np.sum(np.isfinite(pixel_acfs), axis=0)
    sampled_pixel_acf = np.divide(
        np.nansum(pixel_acfs, axis=0),
        finite_counts,
        out=np.full(max_lag + 1, np.nan),
        where=finite_counts > 0,
    )
    spectrum = _spectrum(roi_mean, times, time_source, config.spectrum_peaks)

    window_results: list[dict[str, Any]] = []
    for window, ((start, stop), accumulator) in enumerate(zip(bounds, window_accumulators)):
        count = accumulator["count"]
        mean = accumulator["sum"] / count
        variance = max(0.0, accumulator["sum_sq"] / count - mean**2)
        item = {
            "window": window,
            "start_frame": start,
            "stop_frame_exclusive": stop,
            "start_time": float(times[start]),
            "end_time": float(times[stop - 1]),
            "sample_count": count,
            "mean": mean,
            "standard_deviation": float(np.sqrt(variance)),
            "variance": variance,
            "min": accumulator["min"],
            "max": accumulator["max"],
            "roi_frame_mean_mean": float(roi_mean[start:stop].mean()),
            "roi_frame_median_mean": float(roi_median[start:stop].mean()),
            "mean_low_clipping_fraction": float(low_fraction[start:stop].mean()),
            "mean_high_saturation_fraction": float(high_fraction[start:stop].mean()),
        }
        item.update(
            histogram_percentiles(
                window_histograms[window],
                edges,
                (5.0, 50.0, 95.0),
                discrete_levels=discrete_percentiles,
            )
        )
        window_results.append(item)

    if len(times) >= 2 and times[-1] > times[0]:
        slope = float(np.polyfit(times, roi_mean, 1)[0])
    else:
        slope = 0.0
    first_last_change = window_results[-1]["mean"] - window_results[0]["mean"]
    source_range = (
        session.value_max - session.value_min
        if session.value_min is not None and session.value_max is not None
        else float(observed_max - observed_min)
    )
    exposure_steps = _detect_steps(roi_median, source_range)
    fixed_pattern_spatial_std = float(mean_map.std())
    median_temporal_std = float(np.median(std_map))
    fixed_ratio = None if median_temporal_std == 0 else fixed_pattern_spatial_std / median_temporal_std
    row_std = float(mean_map.mean(axis=1).std())
    column_std = float(mean_map.mean(axis=0).std())
    duplicate_indices = (np.flatnonzero(diff_zero_fraction == 1.0) + 1).astype(int)
    finite_frame_corr = frame_correlation[np.isfinite(frame_correlation)]

    metrics: dict[str, Any] = {
        "descriptive_statistics": {
            "sample_count": global_count,
            "mean": global_mean,
            "median": percentiles["p50"],
            "variance": global_variance,
            "standard_deviation": float(np.sqrt(global_variance)),
            "variance_convention": "Population variance over all projected ROI values (ddof=0).",
            "min": float(observed_min),
            "max": float(observed_max),
            "percentiles": percentiles,
            "percentile_method": (
                "Exact nearest-rank percentiles from integer-level counts."
                if histogram_mode == "exact_integer_levels"
                else "Estimated within fixed histogram bins; no distributional fit was used."
            ),
        },
        "histogram": {
            "mode": histogram_mode,
            "bin_count": len(histogram),
            "counts": histogram,
            "edges": edges,
            "note": "Counts include every projected ROI value; no Gaussian model or random subsampling was used.",
        },
        "frame_differences": {
            "mean_absolute_median": float(np.median(diff_mean_abs)) if len(diff_mean_abs) else 0.0,
            "mean_absolute_max": float(np.max(diff_mean_abs)) if len(diff_mean_abs) else 0.0,
            "rms_median": float(np.median(diff_rms)) if len(diff_rms) else 0.0,
            "rms_max": float(np.max(diff_rms)) if len(diff_rms) else 0.0,
            "max_absolute": float(np.max(diff_max_abs)) if len(diff_max_abs) else 0.0,
            "duplicate_pair_indices": duplicate_indices.tolist(),
        },
        "spatial_structure": {
            "mean_image_spatial_std": fixed_pattern_spatial_std,
            "median_temporal_std": median_temporal_std,
            "fixed_pattern_to_temporal_ratio": fixed_ratio,
            "row_mean_std": row_std,
            "column_mean_std": column_std,
            "adjacent_pixel_correlation_raw": _correlation_values(raw_neighbors),
            "adjacent_pixel_correlation_fixed_pattern_removed": _correlation_values(fixed_removed_neighbors),
            "adjacent_pixel_correlation_local_residual": _correlation_values(local_residual_neighbors),
            "definitions": {
                "raw": "All adjacent spatial pairs pooled across frames.",
                "fixed_pattern_removed": "Per-pixel temporal mean image subtracted before pooling pairs.",
                "local_residual": "Per-pixel temporal mean and each frame's global ROI shift subtracted.",
            },
        },
        "temporal_structure": {
            "adjacent_frame_correlation_mean": (
                float(finite_frame_corr.mean()) if len(finite_frame_corr) else None
            ),
            "adjacent_frame_correlation_median": (
                float(np.median(finite_frame_corr)) if len(finite_frame_corr) else None
            ),
            "roi_mean_autocorrelation": roi_mean_acf,
            "roi_median_autocorrelation": roi_median_acf,
            "sampled_pixel_autocorrelation": sampled_pixel_acf,
            "lags": np.arange(max_lag + 1),
            "sampled_pixel_count": len(correlation_points),
            "sampled_pixel_coordinates_roi_local": correlation_points,
            "note": "Sample points form a deterministic grid; correlations are descriptive and do not imply independence elsewhere.",
        },
        "spectral_analysis": {
            key: value for key, value in spectrum.items() if key not in {"frequency", "power"}
        },
        "artifact_detection": {
            "low_clipping_fraction": float(low_fraction.mean()),
            "high_saturation_fraction": float(high_fraction.mean()),
            "near_low_fraction": float(near_low_fraction.mean()),
            "near_high_fraction": float(near_high_fraction.mean()),
            "source_numeric_min": session.value_min,
            "source_numeric_max": session.value_max,
            "duplicate_consecutive_frames": int(len(duplicate_indices)),
            "duplicate_frame_indices": duplicate_indices.tolist(),
            "exposure_change_candidates": exposure_steps,
            "reported_camera_property_changes": _reported_property_changes(session.metadata),
            "eight_pixel_boundary_discontinuity_ratio": _block_boundary_ratio(
                mean_map, session.roi.x, session.roi.y
            ),
            "row_banding_ratio": None if fixed_pattern_spatial_std == 0 else row_std / fixed_pattern_spatial_std,
            "column_banding_ratio": None if fixed_pattern_spatial_std == 0 else column_std / fixed_pattern_spatial_std,
            "notes": [
                "Clipping/saturation use the source dtype limits and may miss internal clipping to a narrower video range.",
                "Exposure changes and 8-pixel boundaries are heuristic candidates, not definitive classifications.",
            ],
        },
        "stability": {
            "time_source": time_source,
            "time_unit": time_unit,
            "roi_mean_slope_per_time_unit": slope,
            "first_to_last_mean_change": first_last_change,
            "windows": window_results,
        },
    }
    findings = _interpret(metrics, session)

    derived_path = output_dir / "derived_metrics.npz"
    np.savez_compressed(
        derived_path,
        elapsed_time=times,
        roi_mean=roi_mean,
        roi_median=roi_median,
        frame_spatial_std=frame_spatial_std,
        roi_min=roi_min,
        roi_max=roi_max,
        low_clipping_fraction=low_fraction,
        high_saturation_fraction=high_fraction,
        pixel_coordinates=np.asarray(chosen_pixels, dtype=np.int64),
        pixel_traces=pixel_traces,
        frame_difference_mean=diff_mean,
        frame_difference_mean_absolute=diff_mean_abs,
        frame_difference_rms=diff_rms,
        frame_difference_max_absolute=diff_max_abs,
        frame_difference_zero_fraction=diff_zero_fraction,
        adjacent_frame_correlation=frame_correlation,
        mean_image=mean_map,
        temporal_variance_image=variance_map,
        temporal_std_image=std_map,
        mean_absolute_difference_image=mean_abs_diff_map,
        histogram_counts=histogram,
        histogram_edges=edges,
        autocorrelation_lags=np.arange(max_lag + 1),
        roi_mean_autocorrelation=roi_mean_acf,
        roi_median_autocorrelation=roi_median_acf,
        sampled_pixel_autocorrelation=sampled_pixel_acf,
        spectrum_frequency=spectrum["frequency"],
        spectrum_power=spectrum["power"],
    )
    arrays = {
        "times": times,
        "roi_mean": roi_mean,
        "roi_median": roi_median,
        "pixel_traces": pixel_traces,
        "chosen_pixels": chosen_pixels,
        "diff_mean_abs": diff_mean_abs,
        "diff_rms": diff_rms,
        "diff_zero_fraction": diff_zero_fraction,
        "frame_correlation": frame_correlation,
        "mean_map": mean_map,
        "std_map": std_map,
        "mean_abs_diff_map": mean_abs_diff_map,
        "histogram": histogram,
        "histogram_edges": edges,
        "lags": np.arange(max_lag + 1),
        "roi_mean_acf": roi_mean_acf,
        "roi_median_acf": roi_median_acf,
        "sampled_pixel_acf": sampled_pixel_acf,
        "spectrum_frequency": spectrum["frequency"],
        "spectrum_power": spectrum["power"],
        "window_results": window_results,
    }
    plots = generate_plots(session, output_dir, arrays, time_unit, spectrum["frequency_unit"])

    report = {
        "schema_version": 1,
        "status": "complete",
        "started_utc": started_utc,
        "completed_utc": utc_now_iso(),
        "source_session": str(session.session_dir),
        "source_data_unchanged": True,
        "source_data_classification": session.metadata.get("data_classification"),
        "capture_context": {
            "dark_frame_protocol": session.metadata.get("dark_frame_protocol"),
            "setting_reports": session.metadata.get("setting_reports", []),
            "capture_warnings": session.metadata.get("warnings", []),
        },
        "analysis_config": config.to_dict(),
        "input": {
            "valid_frame_count": n,
            "stored_frame_shape": session.source_shape,
            "source_dtype": str(session.source_dtype),
            "roi": session.roi.to_dict(),
            "channel_projection": session.channel,
        },
        "preprocessing": [
            {
                "step": "valid-frame selection",
                "detail": "Read only frames [0:valid_frame_count] as declared by capture metadata.",
            },
            {
                "step": "ROI crop",
                "detail": session.roi.to_dict(),
            },
            {
                "step": "channel projection",
                "detail": (
                    "BT.601 luma = 0.114 B + 0.587 G + 0.299 R in float64; no rounding."
                    if session.channel == "luma"
                    else f"Projection '{session.channel}' converted to float64 for arithmetic."
                ),
            },
            {
                "step": "measurement preservation",
                "detail": "No filtering, denoising, normalization, clipping, outlier removal, or frame rejection.",
            },
            {
                "step": "spectral-only detrending",
                "detail": "A linear trend and mean were removed only from a copy of ROI mean before applying a Hann-window FFT.",
            },
        ],
        "metrics": metrics,
        "findings": findings,
        "outputs": {
            "derived_arrays": derived_path.name,
            "plots": [path.name for path in plots],
        },
        "software": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
        },
    }
    write_json_atomic(report_path, json_safe(report))
    return AnalysisResult(output_dir, report_path, tuple(plots), findings)
