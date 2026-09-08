from __future__ import annotations

import math
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import scipy
from scipy.stats import binomtest, chi2, chi2_contingency, chisquare, normaltest

from camera_noise.storage import utc_now_iso, write_json_atomic

from .math_utils import json_safe, pearson
from .models import EntropyConfig
from .session import LoadedSession, load_session


@dataclass(frozen=True)
class EntropyResult:
    output_dir: Path
    report_path: Path
    markdown_path: Path
    best_method: str | None
    conclusion: str


def _grid_points(width: int, height: int, maximum: int) -> list[tuple[int, int]]:
    count = min(maximum, width * height)
    side = max(1, int(np.ceil(np.sqrt(count))))
    xs = np.unique(np.linspace(0, width - 1, side, dtype=int))
    ys = np.unique(np.linspace(0, height - 1, side, dtype=int))
    points = [(int(x), int(y)) for y in ys for x in xs]
    if len(points) <= count:
        return points
    indices = np.linspace(0, len(points) - 1, count, dtype=int)
    return [points[index] for index in indices]


def _load_traces(session: LoadedSession, maximum_samples: int) -> tuple[np.ndarray, dict[str, Any]]:
    frame_count = min(session.valid_frame_count, maximum_samples)
    pixel_count = min(
        session.roi.width * session.roi.height,
        max(1, maximum_samples // frame_count),
    )
    points = _grid_points(session.roi.width, session.roi.height, pixel_count)
    traces = np.empty((frame_count, len(points)), dtype=np.float64)
    xs = np.asarray([point[0] for point in points], dtype=np.int64)
    ys = np.asarray([point[1] for point in points], dtype=np.int64)
    for index in range(frame_count):
        frame = session.project(index)
        traces[index] = frame[ys, xs]
    return traces, {
        "frame_selection": "contiguous prefix",
        "selected_frame_count": frame_count,
        "available_frame_count": session.valid_frame_count,
        "pixel_selection": "deterministic approximately uniform ROI grid",
        "selected_pixel_count": len(points),
        "available_roi_pixel_count": session.roi.width * session.roi.height,
        "roi_local_pixel_coordinates": points,
        "measurement_count": int(traces.size),
        "truncated": frame_count < session.valid_frame_count or len(points) < session.roi.width * session.roi.height,
        "limitation": (
            "A deterministic subset controls memory use. Distribution estimates describe that subset; "
            "a different ROI or time interval can have different entropy."
        ),
    }


def _shannon_from_counts(counts: np.ndarray) -> float:
    probabilities = counts[counts > 0] / np.sum(counts)
    return float(-np.sum(probabilities * np.log2(probabilities)))


def _wilson_upper(successes: int, trials: int, confidence: float) -> float:
    if trials <= 0:
        return 1.0
    estimate = successes / trials
    z = NormalDist().inv_cdf(confidence)
    denominator = 1.0 + z * z / trials
    center = estimate + z * z / (2.0 * trials)
    radius = z * math.sqrt(estimate * (1.0 - estimate) / trials + z * z / (4.0 * trials * trials))
    return min(1.0, max(0.0, (center + radius) / denominator))


def _min_entropy(probability: float) -> float:
    return float(-math.log2(max(probability, np.finfo(float).tiny)))


def _symbolize(
    values: np.ndarray,
    bins: int,
    exact_integer: bool,
) -> tuple[np.ndarray, dict[str, Any]]:
    array = np.asarray(values)
    observed_min = float(np.min(array))
    observed_max = float(np.max(array))
    if exact_integer:
        integers = np.rint(array).astype(np.int64)
        minimum = int(integers.min())
        maximum = int(integers.max())
        if maximum - minimum + 1 <= max(4096, bins):
            return integers - minimum, {
                "mode": "exact observed integer levels",
                "alphabet_size": maximum - minimum + 1,
                "observed_min": minimum,
                "observed_max": maximum,
                "bin_width": 1,
            }
    if observed_max == observed_min:
        return np.zeros(array.shape, dtype=np.int64), {
            "mode": "single observed value",
            "alphabet_size": 1,
            "observed_min": observed_min,
            "observed_max": observed_max,
            "bin_width": 0.0,
        }
    edges = np.linspace(observed_min, observed_max, bins + 1)
    symbols = np.searchsorted(edges[1:-1], array, side="right").astype(np.int64)
    return symbols, {
        "mode": "equal-width quantization",
        "alphabet_size": bins,
        "observed_min": observed_min,
        "observed_max": observed_max,
        "bin_width": float(edges[1] - edges[0]),
        "limitation": "Entropy changes with quantizer resolution; this is not differential entropy.",
    }


def _temporal_autocorrelation(values: np.ndarray, maximum_lag: int) -> list[float | None]:
    matrix = np.asarray(values, dtype=np.float64)
    result: list[float | None] = [1.0]
    for lag in range(1, min(maximum_lag, len(matrix) - 1) + 1):
        result.append(pearson(matrix[:-lag], matrix[lag:]))
    return result


def _conditional_symbol_prediction(symbols: np.ndarray, confidence: float) -> dict[str, Any]:
    matrix = np.asarray(symbols, dtype=np.int64)
    if matrix.shape[0] < 6:
        return {"status": "insufficient data"}
    alphabet = int(matrix.max()) + 1
    split = max(3, int(matrix.shape[0] * 0.6))
    transitions = np.zeros((alphabet, alphabet), dtype=np.int64)
    np.add.at(transitions, (matrix[: split - 1].ravel(), matrix[1:split].ravel()), 1)
    marginal = np.bincount(matrix[:split].ravel(), minlength=alphabet)
    default = int(np.argmax(marginal))
    predictions = np.argmax(transitions, axis=1)
    unseen = transitions.sum(axis=1) == 0
    predictions[unseen] = default
    previous = matrix[split - 1 : -1].ravel()
    actual = matrix[split:].ravel()
    correct = int(np.sum(predictions[previous] == actual))
    baseline_correct = int(np.sum(actual == default))
    accuracy = correct / len(actual)
    baseline = baseline_correct / len(actual)

    row_totals = transitions.sum(axis=1)
    total = int(row_totals.sum())
    conditional_entropy = 0.0
    if total:
        for row, row_total in zip(transitions, row_totals):
            if row_total:
                conditional_entropy += row_total / total * _shannon_from_counts(row)
    return {
        "status": "complete",
        "training_frame_fraction": 0.6,
        "test_predictions": int(len(actual)),
        "held_out_accuracy": accuracy,
        "held_out_marginal_baseline_accuracy": baseline,
        "accuracy_excess_over_baseline": accuracy - baseline,
        "upper_confidence_accuracy": _wilson_upper(correct, len(actual), confidence),
        "prediction_min_entropy_lower_bound_bits": _min_entropy(
            _wilson_upper(correct, len(actual), confidence)
        ),
        "empirical_first_order_conditional_shannon_entropy_bits": conditional_entropy,
        "assumptions": (
            "A first-order stationary Markov table trained on the first 60% is evaluated on the remainder. "
            "It detects only this predictor class and does not bound a stronger adversary."
        ),
    }


def _continuous_metrics(
    values: np.ndarray,
    exact_integer: bool,
    bins: int,
    maximum_lag: int,
    confidence: float,
) -> dict[str, Any]:
    matrix = np.asarray(values, dtype=np.float64)
    symbols, quantization = _symbolize(matrix, bins, exact_integer)
    counts = np.bincount(symbols.ravel(), minlength=quantization["alphabet_size"])
    total = int(counts.sum())
    most_common = int(counts.max())
    plugin_probability = most_common / total
    upper_probability = _wilson_upper(most_common, total, confidence)
    temporal_equal = float(np.mean(symbols[:-1] == symbols[1:])) if len(symbols) > 1 else None
    iid_collision_probability = float(np.sum((counts / total) ** 2))

    expected = total / len(counts)
    uniform_p = float(chisquare(counts).pvalue) if expected >= 5 and len(counts) > 1 else None
    shift_p: float | None = None
    if matrix.shape[0] >= 4 and len(counts) > 1:
        split = matrix.shape[0] // 2
        first = np.bincount(symbols[:split].ravel(), minlength=len(counts))
        second = np.bincount(symbols[split:].ravel(), minlength=len(counts))
        used = first + second > 0
        if np.sum(used) > 1:
            shift_p = float(chi2_contingency(np.vstack((first[used], second[used]))).pvalue)

    normality_p: float | None = None
    flat = matrix.ravel()
    if len(flat) >= 20 and float(np.std(flat)) > 0:
        if len(flat) > 100_000:
            indices = np.linspace(0, len(flat) - 1, 100_000, dtype=np.int64)
            flat = flat[indices]
        normality_p = float(normaltest(flat).pvalue)

    per_pixel_bounds = []
    for column in range(matrix.shape[1]):
        column_counts = np.bincount(symbols[:, column], minlength=len(counts))
        per_pixel_bounds.append(
            _min_entropy(_wilson_upper(int(column_counts.max()), len(symbols), confidence))
        )
    return {
        "sample_count": total,
        "quantization": quantization,
        "shannon_entropy_bits_per_symbol_plugin": _shannon_from_counts(counts),
        "min_entropy_bits_per_symbol_plugin": _min_entropy(plugin_probability),
        "most_common_value": {
            "count": most_common,
            "observed_probability": plugin_probability,
            "upper_confidence_probability": upper_probability,
            "confidence": confidence,
            "min_entropy_lower_bound_bits_per_symbol": _min_entropy(upper_probability),
            "method": (
                "Most-common-value estimate with a one-sided Wilson probability bound; inspired by the "
                "SP 800-90B MCV principle, but this report is not a validated EntropyAssessment run."
            ),
        },
        "per_pixel_temporal_mcv_lower_bounds_bits": {
            "minimum": float(np.min(per_pixel_bounds)),
            "p10": float(np.percentile(per_pixel_bounds, 10)),
            "median": float(np.median(per_pixel_bounds)),
            "maximum": float(np.max(per_pixel_bounds)),
            "note": "Computed separately down time for each sampled pixel; small frame counts make the confidence penalty large.",
        },
        "collisions": {
            "iid_probability_two_independent_symbols_match": iid_collision_probability,
            "observed_adjacent_frame_same_pixel_match_fraction": temporal_equal,
            "note": "Excess temporal matches indicate memory or quantization, but matches are not independent trials.",
        },
        "temporal_autocorrelation": _temporal_autocorrelation(matrix, maximum_lag),
        "conditional_predictability": _conditional_symbol_prediction(symbols, confidence),
        "distribution_tests": {
            "uniform_chi_square_p_value": uniform_p,
            "first_half_vs_second_half_chi_square_p_value": shift_p,
            "dagostino_pearson_normality_p_value": normality_p,
            "interpretation": (
                "Uniformity and normality are reference models, not requirements for entropy. The half-vs-half test "
                "checks stationarity. Small p-values identify model mismatch; large p-values do not prove the model."
            ),
        },
        "entropy_estimate_assumptions": {
            "shannon_plugin": (
                "Empirical symbol frequencies are treated as representative of one stationary distribution. The "
                "finite-sample plugin estimate is biased and pooled amplitudes may include fixed spatial structure."
            ),
            "min_entropy_plugin": (
                "Uses the observed most-common frequency with no confidence penalty, so it is descriptive and normally optimistic."
            ),
            "mcv_confidence_lower_bound": (
                "Uses a one-sided Wilson upper bound for the most-common probability. It covers binomial sampling "
                "uncertainty under representative observations but does not correct unknown nonstationarity or dependence."
            ),
            "per_pixel_temporal_mcv": (
                "Treats each pixel's time series as a separate source with the same quantizer. Results summarize pixels "
                "rather than proving that pixels are independent or that their entropy can be added."
            ),
            "conditional_shannon": (
                "Assumes first-order symbol dependence is an informative model. Higher-order, cross-pixel, environmental, "
                "or hidden-state predictors may perform better."
            ),
        },
        "limitations": [
            "Pooled amplitude entropy can include stable pixel-to-pixel offsets and is not automatically fresh entropy.",
            "Plugin Shannon and min-entropy estimates are optimistic at finite sample sizes.",
            "The MCV confidence bound assumes representative, sufficiently stationary observations.",
        ],
    }


def _runs(bits: np.ndarray) -> dict[str, Any]:
    n = len(bits)
    if n < 2:
        return {"run_count": n, "p_value": None, "longest_run": n}
    changes = np.flatnonzero(bits[1:] != bits[:-1]) + 1
    lengths = np.diff(np.concatenate(([0], changes, [n])))
    ones = int(bits.sum())
    zeros = n - ones
    expected = 1.0 + 2.0 * ones * zeros / n
    denominator = n * n * max(n - 1, 1)
    variance = 2.0 * ones * zeros * (2.0 * ones * zeros - n) / denominator
    p_value = None
    if variance > 0:
        z = (len(lengths) - expected) / math.sqrt(variance)
        p_value = math.erfc(abs(z) / math.sqrt(2.0))
    unique, counts = np.unique(lengths, return_counts=True)
    return {
        "run_count": int(len(lengths)),
        "expected_run_count_under_iid_with_observed_bias": expected,
        "p_value": p_value,
        "longest_run": int(lengths.max()),
        "run_length_counts": {str(int(length)): int(count) for length, count in zip(unique, counts)},
        "assumption": "Wald-Wolfowitz normal approximation conditional on the observed zero/one counts.",
    }


def _lag_predictor(bits: np.ndarray, maximum_lag: int, confidence: float) -> dict[str, Any]:
    n = len(bits)
    split = max(maximum_lag + 1, int(n * 0.6))
    if n - split < 20:
        return {"status": "insufficient data"}
    candidates: list[dict[str, Any]] = []
    for lag in range(1, min(maximum_lag, split - 1) + 1):
        train_equal = float(np.mean(bits[lag:split] == bits[: split - lag]))
        copy = train_equal >= 0.5
        actual = bits[split:]
        prior = bits[split - lag : n - lag]
        predicted = prior if copy else 1 - prior
        correct = int(np.sum(predicted == actual))
        candidates.append(
            {
                "lag": lag,
                "rule": "copy" if copy else "invert",
                "test_accuracy": correct / len(actual),
                "correct": correct,
            }
        )
    best = max(candidates, key=lambda item: item["test_accuracy"])
    upper = _wilson_upper(best["correct"], n - split, confidence)
    return {
        "status": "complete",
        "best_lag": best["lag"],
        "best_rule": best["rule"],
        "held_out_accuracy": best["test_accuracy"],
        "upper_confidence_accuracy": upper,
        "min_entropy_lower_bound_bits_per_bit": _min_entropy(upper),
        "tested_lags": len(candidates),
        "assumptions": (
            "Copy/invert lag rules are selected on the first 60% and evaluated on the remainder. Selecting the "
            "best of several lags adds multiple-comparison optimism; this is a diagnostic lower bound, not a proof."
        ),
    }


def _bit_metrics(
    bits: np.ndarray,
    maximum_lag: int,
    confidence: float,
    alpha: float,
) -> dict[str, Any]:
    sequence = np.asarray(bits, dtype=np.uint8).ravel()
    n = len(sequence)
    ones = int(sequence.sum())
    zeros = n - ones
    counts = np.asarray([zeros, ones], dtype=np.int64)
    most_common = int(counts.max())
    upper = _wilson_upper(most_common, n, confidence)
    autocorrelations: list[float | None] = [1.0]
    for lag in range(1, min(maximum_lag, n - 1) + 1):
        autocorrelations.append(pearson(sequence[:-lag], sequence[lag:]))

    pairs = np.zeros((2, 2), dtype=np.int64)
    if n > 1:
        np.add.at(pairs, (sequence[:-1], sequence[1:]), 1)
    serial_p = float(chisquare(pairs.ravel()).pvalue) if n > 20 else None
    monobit_p = float(binomtest(ones, n, 0.5).pvalue)
    run_metrics = _runs(sequence)

    block_size = 128
    blocks = n // block_size
    block_p: float | None = None
    if blocks >= 2:
        proportions = sequence[: blocks * block_size].reshape(blocks, block_size).mean(axis=1)
        statistic = 4.0 * block_size * float(np.sum((proportions - 0.5) ** 2))
        block_p = float(chi2.sf(statistic, blocks))

    lag = _lag_predictor(sequence, maximum_lag, confidence)
    estimates = {"most_common_value": _min_entropy(upper)}
    if lag.get("status") == "complete":
        estimates["held_out_lag_prediction"] = lag["min_entropy_lower_bound_bits_per_bit"]
    conservative = min(estimates.values())
    available_p = [value for value in (monobit_p, run_metrics["p_value"], block_p, serial_p) if value is not None]
    passes = len(available_p) >= 2 and all(value >= alpha for value in available_p)
    finite_acf = [abs(value) for value in autocorrelations[1:] if value is not None]
    max_acf = max(finite_acf, default=0.0)
    tolerance = max(0.02, 3.0 / math.sqrt(n))
    prediction_excess = (
        max(0.0, lag.get("held_out_accuracy", 0.5) - 0.5)
        if lag.get("status") == "complete"
        else 0.0
    )
    looks_random = passes and max_acf <= tolerance and prediction_excess <= tolerance and conservative >= 0.8
    return {
        "bit_count": n,
        "zero_count": zeros,
        "one_count": ones,
        "bias_one_probability_minus_half": ones / n - 0.5,
        "shannon_entropy_bits_per_bit_plugin": _shannon_from_counts(counts),
        "min_entropy_bits_per_bit_plugin": _min_entropy(most_common / n),
        "collision_statistics": {
            "pair_counts_00_01_10_11": pairs.ravel().tolist(),
            "adjacent_equal_fraction": float((pairs[0, 0] + pairs[1, 1]) / max(n - 1, 1)),
            "iid_collision_probability_from_observed_bias": float(np.sum((counts / n) ** 2)),
        },
        "serial_dependence": {
            "lag_1_correlation": autocorrelations[1] if len(autocorrelations) > 1 else None,
            "autocorrelation_by_lag": autocorrelations,
            "overlapping_pair_uniform_chi_square_p_value": serial_p,
            "note": "The pair chi-square uses overlapping pairs, so its p-value is diagnostic rather than exact.",
        },
        "conditional_predictability": lag,
        "runs": run_metrics,
        "distribution_tests": {
            "exact_monobit_p_value": monobit_p,
            "block_frequency_p_value": block_p,
            "block_size_bits": block_size,
            "selected_alpha": alpha,
            "passes_all_available_selected_tests": passes,
        },
        "nist_sp_800_90b_inspired_estimators": {
            "most_common_value_lower_bound_bits_per_bit": estimates["most_common_value"],
            "held_out_lag_prediction_lower_bound_bits_per_bit": estimates.get("held_out_lag_prediction"),
            "conservative_minimum_of_implemented_estimators_bits_per_bit": conservative,
            "confidence": confidence,
            "scope": (
                "Diagnostic subset only. It omits many SP 800-90B non-IID estimators, restart testing, data validation, "
                "and conditioning-component validation. Use NIST EntropyAssessment for a conforming workflow."
            ),
        },
        "entropy_estimate_assumptions": {
            "shannon_plugin": (
                "Uses observed zero/one frequencies as the source distribution. It measures average surprise under a "
                "stationary marginal model and ignores ordering unless paired with the dependence diagnostics."
            ),
            "min_entropy_plugin": (
                "Uses the observed more-common bit frequency without a confidence penalty and is therefore optimistic at finite n."
            ),
            "mcv_confidence_lower_bound": (
                "Uses a one-sided Wilson upper bound for the more-common bit probability. Unknown drift or dependence can "
                "invalidate the representative-sampling interpretation."
            ),
            "lag_prediction_lower_bound": (
                "Bounds only the best tested copy/invert lag predictor on one held-out suffix. It is not a bound against "
                "arbitrary prediction and is optimistic after choosing among several lags."
            ),
            "conservative_minimum": (
                "Takes the smaller implemented bound. It is conservative only relative to this limited estimator set; "
                "an omitted SP 800-90B estimator or external predictor may give a lower value."
            ),
        },
        "claim_levels": {
            "looks_random": looks_random,
            "statistically_passes_selected_tests": passes,
            "contains_measurable_sample_entropy": conservative > 0.0,
            "is_unpredictable": "not established by these observations and predictor classes",
            "is_cryptographically_secure": "not established; no validated entropy-source or conditioning design",
        },
    }


def _von_neumann(bits: np.ndarray) -> np.ndarray:
    pair_count = len(bits) // 2
    pairs = np.asarray(bits[: 2 * pair_count], dtype=np.uint8).reshape(-1, 2)
    unequal = pairs[:, 0] != pairs[:, 1]
    return pairs[unequal, 0]


def _extract_methods(traces: np.ndarray, direct_integer: bool) -> dict[str, dict[str, Any]]:
    methods: dict[str, dict[str, Any]] = {}
    input_measurements = int(traces.size)

    median_training = max(1, traces.shape[0] // 2)
    thresholds = np.median(traces[:median_training], axis=0)
    median_bits = (traces[median_training:] > thresholds).T.ravel().astype(np.uint8)
    methods["per_pixel_median_threshold"] = {
        "bits": median_bits,
        "input_measurements": input_measurements,
        "description": "Threshold each pixel against its first-half temporal median; emit only second-half comparisons.",
        "assumptions": "Threshold training and evaluation are time-separated, but drift can bias the output and ties emit zero.",
    }

    if traces.shape[0] > 1:
        differences = np.diff(traces, axis=0)
        sign_bits = (differences > 0).T.ravel().astype(np.uint8)
        methods["temporal_difference_sign"] = {
            "bits": sign_bits,
            "input_measurements": input_measurements,
            "description": "Emit one when a pixel increased from the prior frame and zero otherwise.",
            "assumptions": "Exact ties emit zero; drift and temporal filtering directly affect bias and dependence.",
        }
        if direct_integer:
            parity_bits = (np.abs(np.rint(differences).astype(np.int64)) & 1).T.ravel().astype(np.uint8)
            methods["temporal_difference_parity"] = {
                "bits": parity_bits,
                "input_measurements": input_measurements,
                "description": "Parity of the absolute integer frame-to-frame change at each pixel.",
                "assumptions": "Quantization and ISP filtering can make even or zero differences dominant.",
            }

    if direct_integer:
        integer_values = np.rint(traces).astype(np.int64)
        lsb_bits = (integer_values & 1).T.ravel().astype(np.uint8)
        methods["direct_value_lsb"] = {
            "bits": lsb_bits,
            "input_measurements": input_measurements,
            "description": "Least significant bit of each observed integer sample.",
            "assumptions": "The LSB is only a candidate; quantization, clipping, compression, and ISP logic may determine it.",
        }

    for source in ("direct_value_lsb", "temporal_difference_sign"):
        if source in methods:
            debiased = _von_neumann(methods[source]["bits"])
            methods[f"{source}_von_neumann"] = {
                "bits": debiased,
                "input_measurements": input_measurements,
                "description": f"Von Neumann 01/10 pair extraction applied to {source}; equal pairs are discarded.",
                "assumptions": (
                    "Von Neumann debiasing requires identically biased independent input pairs. It can reduce simple bias "
                    "but cannot create entropy or remove arbitrary dependence."
                ),
            }
    return methods


def _rank_methods(methods: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for name, item in methods.items():
        metrics = item["metrics"]
        entropy = metrics["nist_sp_800_90b_inspired_estimators"][
            "conservative_minimum_of_implemented_estimators_bits_per_bit"
        ]
        yield_per_measurement = metrics["bit_count"] / item["input_measurements"]
        rows.append(
            {
                "method": name,
                "conservative_entropy_bits_per_output_bit": entropy,
                "output_bits_per_input_measurement": yield_per_measurement,
                "estimated_entropy_bits_per_input_measurement": entropy * yield_per_measurement,
                "absolute_bias": abs(metrics["bias_one_probability_minus_half"]),
                "selected_tests_pass": metrics["claim_levels"]["statistically_passes_selected_tests"],
                "looks_random_under_builtin_diagnostics": metrics["claim_levels"]["looks_random"],
            }
        )
    quality = sorted(
        rows,
        key=lambda row: (
            row["conservative_entropy_bits_per_output_bit"],
            -row["absolute_bias"],
            row["output_bits_per_input_measurement"],
        ),
        reverse=True,
    )
    yield_rank = sorted(rows, key=lambda row: row["estimated_entropy_bits_per_input_measurement"], reverse=True)
    passing = [row for row in yield_rank if row["selected_tests_pass"]]
    recommended = passing[0] if passing else (yield_rank[0] if yield_rank else None)
    return {
        "ranking_by_output_bit_quality": quality,
        "ranking_by_estimated_entropy_yield": yield_rank,
        "best_quality_method": quality[0]["method"] if quality else None,
        "best_estimated_yield_method": yield_rank[0]["method"] if yield_rank else None,
        "recommended_diagnostic_candidate": recommended["method"] if recommended else None,
        "selection_reason": (
            "Recommendation prefers methods passing the selected built-in tests, then ranks by the minimum of the "
            "implemented MCV and lag-prediction bounds multiplied by extraction yield. This is a comparison heuristic, "
            "not a certified entropy rate."
        ),
    }


def _write_bitstreams(output_dir: Path, methods: dict[str, dict[str, Any]]) -> dict[str, Any]:
    directory = output_dir / "bitstreams"
    directory.mkdir()
    exports: dict[str, Any] = {}
    for name, item in methods.items():
        bits = item["bits"]
        byte_aligned_count = len(bits) // 8 * 8
        packed = np.packbits(bits[:byte_aligned_count], bitorder="big")
        packed_path = directory / f"{name}.bin"
        symbol_path = directory / f"{name}.symbols.bin"
        packed_path.write_bytes(packed.tobytes())
        symbol_path.write_bytes(bits.tobytes())
        exports[name] = {
            "packed_binary": str(packed_path.relative_to(output_dir)),
            "one_bit_per_byte_binary": str(symbol_path.relative_to(output_dir)),
            "meaningful_bits": int(len(bits)),
            "packed_bits": int(byte_aligned_count),
            "discarded_for_byte_alignment": int(len(bits) - byte_aligned_count),
            "packed_bit_order": "most-significant bit first within each byte",
        }
    return exports


def _run_external_tools(
    output_dir: Path,
    requested: tuple[str, ...],
    method: str | None,
    exports: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    if not requested:
        return {
            "status": "not requested",
            "available_integrations": ["nist90b", "practrand", "dieharder"],
            "nist_sp_800_22": (
                "Bitstreams are exported for an external NIST STS installation. STS is not invoked automatically "
                "because common distributions are interactive and require experiment-size configuration."
            ),
        }
    if method not in exports:
        raise ValueError(f"External-tool method '{method}' was not produced")
    external_dir = output_dir / "external_tools"
    external_dir.mkdir()
    packed = output_dir / exports[method]["packed_binary"]
    symbols = output_dir / exports[method]["one_bit_per_byte_binary"]
    definitions = {
        "nist90b": ("ea_non_iid", ["-i", str(symbols), "1"], None),
        "practrand": ("RNG_test", ["stdin32"], packed),
        "dieharder": ("dieharder", ["-a", "-g", "201", "-f", str(packed)], None),
    }
    for tool in requested:
        executable_name, arguments, stdin_path = definitions[tool]
        executable = shutil.which(executable_name)
        meaningful_bits = int(exports[method]["meaningful_bits"])
        minimum_guidance = {
            "nist90b": 1_000_000,
            "practrand": 8_000_000,
            "dieharder": 80_000_000,
        }[tool]
        size_warning = (
            None
            if meaningful_bits >= minimum_guidance
            else (
                f"Only {meaningful_bits} meaningful bits are available; this is below the rough "
                f"{minimum_guidance}-bit minimum used here for an informative run. Tool-specific tests may require more."
            )
        )
        if executable is None:
            results[tool] = {
                "status": "not installed",
                "expected_executable": executable_name,
                "sample_size_warning": size_warning,
            }
            continue
        command = [executable, *arguments]
        try:
            if stdin_path is None:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
            else:
                with stdin_path.open("rb") as handle:
                    completed = subprocess.run(
                        command,
                        stdin=handle,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        check=False,
                    )
            output_path = external_dir / f"{tool}.txt"
            output_path.write_text(completed.stdout + "\nSTDERR:\n" + completed.stderr, encoding="utf-8")
            results[tool] = {
                "status": "completed" if completed.returncode == 0 else "nonzero exit",
                "return_code": completed.returncode,
                "command": command,
                "output": str(output_path.relative_to(output_dir)),
                "sample_size_warning": size_warning,
                "interpretation": "External test output is retained verbatim and is not treated as proof of unpredictability.",
            }
        except subprocess.TimeoutExpired:
            results[tool] = {
                "status": "timed out",
                "timeout_seconds": timeout,
                "command": command,
                "sample_size_warning": size_warning,
            }
    return {"method": method, "results": results}


def _conclusion(comparison: dict[str, Any], methods: dict[str, dict[str, Any]]) -> str:
    candidate = comparison["recommended_diagnostic_candidate"]
    if candidate is None:
        return "No usable bit-extraction method was produced from this input."
    metrics = methods[candidate]["metrics"]
    entropy = metrics["nist_sp_800_90b_inspired_estimators"][
        "conservative_minimum_of_implemented_estimators_bits_per_bit"
    ]
    if metrics["claim_levels"]["looks_random"]:
        return (
            f"{candidate} is the strongest candidate in this dataset: it shows about {entropy:.4f} conservative "
            "diagnostic bits per output bit and no defect detected by the selected built-in tests. This is evidence "
            "of measurable sample entropy, not proof of true unpredictability or cryptographic security."
        )
    return (
        f"{candidate} ranks highest among the tested methods, with about {entropy:.4f} conservative diagnostic bits "
        "per output bit, but at least one built-in diagnostic found bias, dependence, prediction, or distribution "
        "concerns. Useful unpredictability is not demonstrated by this dataset."
    )


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    comparison = report["bitstream_comparison"]
    lines = [
        "# Camera Noise Entropy and Randomness Report",
        "",
        "## Answer",
        "",
        report["conclusion"],
        "",
        "## What the labels mean",
        "",
        "- **Looks random:** no obvious defect was found by this report's limited diagnostics.",
        "- **Passes selected tests:** the listed p-values did not cross the configured threshold.",
        "- **Measurable entropy:** observed outcomes are not all identical, under the stated quantization and confidence model.",
        "- **Unpredictable:** not established by statistical testing of one recorded dataset.",
        "- **Cryptographically secure:** not established without a validated entropy source, threat model, health tests, and conditioner.",
        "",
        "## Extraction comparison",
        "",
        "| Method | Hmin/output bit | Output bits/input sample | Estimated entropy/input sample | |bias| | Selected tests |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in comparison["ranking_by_estimated_entropy_yield"]:
        lines.append(
            f"| {row['method']} | {row['conservative_entropy_bits_per_output_bit']:.4f} | "
            f"{row['output_bits_per_input_measurement']:.4f} | "
            f"{row['estimated_entropy_bits_per_input_measurement']:.4f} | "
            f"{row['absolute_bias']:.4f} | {'pass' if row['selected_tests_pass'] else 'concern'} |"
        )
    lines.extend(
        [
            "",
            "The ranking is comparative, not a certification. A debiaser can improve output-bit quality while sharply reducing yield.",
            "",
            "## Continuous measurements",
            "",
            "Pooled amplitudes include stable spatial offsets, so their entropy is not automatically fresh. Temporal differences and per-pixel estimates are more relevant to new information between frames.",
            "",
            "## Main limitations",
            "",
            "- The source is processed webcam output, not confirmed RAW sensor data.",
            "- Entropy depends on ROI, channel, quantization, extraction ordering, sample size, and stationarity.",
            "- The built-in SP 800-90B-inspired estimates are a diagnostic subset, not a conforming assessment.",
            "- Repeated sessions, restart tests, environmental controls, and independent validation are required before stronger claims.",
            "- Passing NIST SP 800-22, PractRand, or Dieharder would still not prove unpredictability or security.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def analyze_entropy(config: EntropyConfig) -> EntropyResult:
    config.validate()
    session = load_session(config.to_analysis_config())
    output_dir = (config.output_dir or session.session_dir / "entropy").resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    report_path = output_dir / "entropy_report.json"
    markdown_path = output_dir / "entropy_report.md"
    started = utc_now_iso()
    write_json_atomic(
        report_path,
        {"schema_version": 1, "status": "analyzing", "started_utc": started, "source_session": str(session.session_dir)},
    )

    traces, selection = _load_traces(session, config.max_samples)
    direct_integer = session.channel in {"b", "g", "r", "intensity"} and np.issubdtype(
        session.source_dtype, np.integer
    )
    continuous = {
        "observed_amplitudes": _continuous_metrics(
            traces,
            direct_integer,
            config.histogram_bins,
            config.max_lag,
            config.confidence,
        )
    }
    if traces.shape[0] > 1:
        continuous["temporal_differences"] = _continuous_metrics(
            np.diff(traces, axis=0),
            direct_integer,
            config.histogram_bins,
            config.max_lag,
            config.confidence,
        )

    extracted = _extract_methods(traces, direct_integer)
    methods: dict[str, dict[str, Any]] = {}
    for name, item in extracted.items():
        bits = item.pop("bits")
        if len(bits) < 2:
            continue
        item["metrics"] = _bit_metrics(
            bits,
            config.max_lag,
            config.confidence,
            config.significance_level,
        )
        item["_bits"] = bits
        methods[name] = item
    comparison = _rank_methods(methods)
    exports = _write_bitstreams(
        output_dir,
        {name: {"bits": item["_bits"]} for name, item in methods.items()},
    )
    selected_external_method = config.external_method or comparison["recommended_diagnostic_candidate"]
    external = _run_external_tools(
        output_dir,
        config.external_tools,
        selected_external_method,
        exports,
        config.external_timeout_seconds,
    )
    for item in methods.values():
        item.pop("_bits", None)
    conclusion = _conclusion(comparison, methods)

    report = {
        "schema_version": 1,
        "status": "complete",
        "started_utc": started,
        "completed_utc": utc_now_iso(),
        "source_session": str(session.session_dir),
        "source_data_unchanged": True,
        "source_data_classification": session.metadata.get("data_classification"),
        "question": "Does the measured camera noise contain useful unpredictability?",
        "conclusion": conclusion,
        "claim_policy": {
            "looks_random": "A limited empirical description, not a physical claim.",
            "statistically_passes_tests": "No rejection by the named tests at the configured alpha and sample size.",
            "contains_measurable_entropy": "A model- and quantization-dependent estimate from observed frequencies.",
            "is_unpredictable": "Requires a defensible source model and adversarial analysis; not established here.",
            "is_cryptographically_secure": "Requires a validated entropy source, health tests, conditioning, and threat model; not established here.",
        },
        "analysis_config": config.to_dict(),
        "input": {
            "stored_frame_shape": session.source_shape,
            "source_dtype": str(session.source_dtype),
            "roi": session.roi.to_dict(),
            "channel_projection": session.channel,
            "direct_integer_projection": direct_integer,
            "selection": selection,
        },
        "continuous_measurements": continuous,
        "bit_extraction_methods": methods,
        "bitstream_comparison": comparison,
        "bitstream_exports": exports,
        "external_randomness_tools": external,
        "global_assumptions_and_limitations": [
            "Observed camera output may include deterministic ISP, compression, clipping, automatic-control, buffering, and driver behavior.",
            "One session cannot establish behavior across restarts, temperatures, settings, devices, or adversarial conditions.",
            "Statistical test p-values are not probabilities that the source is random and are sensitive to sample size and multiple testing.",
            "No extractor or conditioner is claimed to create entropy; deterministic processing can only preserve or discard source entropy.",
            "External statistical suites, when requested, supplement but do not replace entropy-source modeling or SP 800-90B validation.",
        ],
        "software": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
    }
    write_json_atomic(report_path, json_safe(report))
    _write_markdown(markdown_path, report)
    return EntropyResult(
        output_dir=output_dir,
        report_path=report_path,
        markdown_path=markdown_path,
        best_method=comparison["recommended_diagnostic_candidate"],
        conclusion=conclusion,
    )
