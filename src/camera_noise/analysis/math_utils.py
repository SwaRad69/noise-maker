from __future__ import annotations

from typing import Any

import numpy as np


def pearson(x: np.ndarray, y: np.ndarray) -> float | None:
    a = np.asarray(x, dtype=np.float64).ravel()
    b = np.asarray(y, dtype=np.float64).ravel()
    if len(a) != len(b) or len(a) < 2:
        return None
    a = a - a.mean()
    b = b - b.mean()
    denominator = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    if denominator == 0:
        return None
    return float(np.dot(a, b) / denominator)


def autocorrelation(series: np.ndarray, max_lag: int) -> np.ndarray:
    values = np.asarray(series, dtype=np.float64)
    result = np.full(max_lag + 1, np.nan, dtype=np.float64)
    result[0] = 1.0
    centered = values - values.mean()
    for lag in range(1, min(max_lag, len(values) - 1) + 1):
        value = pearson(centered[:-lag], centered[lag:])
        result[lag] = np.nan if value is None else value
    return result


def histogram_percentiles(
    counts: np.ndarray,
    edges: np.ndarray,
    percentiles: tuple[float, ...],
    *,
    discrete_levels: bool = False,
) -> dict[str, float | None]:
    total = int(np.sum(counts))
    if total == 0:
        return {f"p{value:g}": None for value in percentiles}
    cumulative = np.cumsum(counts)
    result: dict[str, float | None] = {}
    for percentile in percentiles:
        if discrete_levels:
            target_count = max(1, int(np.ceil(percentile / 100.0 * total)))
            index = int(np.searchsorted(cumulative, target_count, side="left"))
            index = min(index, len(counts) - 1)
            result[f"p{percentile:g}"] = float((edges[index] + edges[index + 1]) / 2)
            continue
        target = percentile / 100.0 * max(total - 1, 0)
        index = int(np.searchsorted(cumulative, target + 1, side="left"))
        index = min(index, len(counts) - 1)
        previous = int(cumulative[index - 1]) if index else 0
        count = int(counts[index])
        fraction = 0.5 if count == 0 else min(1.0, max(0.0, (target - previous) / count))
        result[f"p{percentile:g}"] = float(
            edges[index] + fraction * (edges[index + 1] - edges[index])
        )
    return result


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


class PairAccumulator:
    def __init__(self) -> None:
        self.n = 0
        self.sum_x = 0.0
        self.sum_y = 0.0
        self.sum_x2 = 0.0
        self.sum_y2 = 0.0
        self.sum_xy = 0.0

    def update(self, x: np.ndarray, y: np.ndarray) -> None:
        a = np.asarray(x, dtype=np.float64).ravel()
        b = np.asarray(y, dtype=np.float64).ravel()
        self.n += len(a)
        self.sum_x += float(a.sum())
        self.sum_y += float(b.sum())
        self.sum_x2 += float(np.dot(a, a))
        self.sum_y2 += float(np.dot(b, b))
        self.sum_xy += float(np.dot(a, b))

    def correlation(self) -> float | None:
        if self.n < 2:
            return None
        covariance = self.sum_xy - self.sum_x * self.sum_y / self.n
        variance_x = self.sum_x2 - self.sum_x * self.sum_x / self.n
        variance_y = self.sum_y2 - self.sum_y * self.sum_y / self.n
        denominator = float(np.sqrt(max(0.0, variance_x) * max(0.0, variance_y)))
        return None if denominator == 0 else float(covariance / denominator)
