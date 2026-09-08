from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

from .session import LoadedSession


def _save(fig: Any, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def _display_frame(frame: np.ndarray) -> tuple[np.ndarray, str | None]:
    if frame.ndim == 2:
        return frame, "gray"
    if frame.shape[2] >= 3:
        return frame[..., [2, 1, 0]], None
    return frame[..., 0], "gray"


def generate_plots(
    session: LoadedSession,
    output_dir: Path,
    arrays: dict[str, Any],
    time_unit: str,
    frequency_unit: str,
) -> list[Path]:
    plots: list[Path] = []
    times = arrays["times"]
    x_label = "Elapsed time (s)" if time_unit == "seconds" else "Frame index"

    fig, ax = plt.subplots(figsize=(8, 5))
    display, cmap = _display_frame(np.asarray(session.frames[0]))
    ax.imshow(display, cmap=cmap)
    roi = session.roi
    ax.add_patch(Rectangle((roi.x, roi.y), roi.width, roi.height, fill=False, edgecolor="red", linewidth=1.5))
    ax.set(title="Selected ROI on first captured frame", xlabel="x (pixels)", ylabel="y (pixels)")
    plots.append(_save(fig, output_dir / "roi_selection.png"))

    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    axes[0].plot(times, arrays["roi_mean"], label="ROI mean", linewidth=1)
    axes[0].plot(times, arrays["roi_median"], label="ROI median", linewidth=1)
    axes[0].set(ylabel="Projected value", title="ROI location statistics over time")
    axes[0].legend()
    for index, coordinates in enumerate(arrays["chosen_pixels"]):
        axes[1].plot(times, arrays["pixel_traces"][:, index], linewidth=0.9, label=str(coordinates))
    axes[1].set(xlabel=x_label, ylabel="Projected value", title="Individual-pixel time series")
    axes[1].legend(title="Pixel (x, y)", ncol=2, fontsize="small")
    plots.append(_save(fig, output_dir / "time_series.png"))

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    diff_times = times[1:]
    axes[0].plot(diff_times, arrays["diff_mean_abs"], linewidth=0.9)
    axes[0].set(ylabel="Mean absolute diff", title="Frame-to-frame changes")
    axes[1].plot(diff_times, arrays["diff_rms"], linewidth=0.9)
    axes[1].set(ylabel="RMS diff")
    axes[2].plot(diff_times, arrays["diff_zero_fraction"], linewidth=0.9)
    axes[2].set(xlabel=x_label, ylabel="Unchanged fraction", ylim=(-0.02, 1.02))
    plots.append(_save(fig, output_dir / "frame_differences.png"))

    fig, ax = plt.subplots(figsize=(8, 5))
    centers = (arrays["histogram_edges"][:-1] + arrays["histogram_edges"][1:]) / 2
    widths = np.diff(arrays["histogram_edges"])
    ax.bar(centers, arrays["histogram"], width=widths, align="center", color="#3f6f8f")
    ax.set(title="Distribution of all projected ROI values", xlabel="Projected value", ylabel="Count")
    plots.append(_save(fig, output_dir / "distribution.png"))

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    for ax, data, title in zip(
        axes,
        (arrays["mean_map"], arrays["std_map"], arrays["mean_abs_diff_map"]),
        ("Temporal mean image", "Temporal standard deviation", "Mean absolute frame difference"),
    ):
        image = ax.imshow(data, cmap="viridis")
        ax.set(title=title, xlabel="ROI x", ylabel="ROI y")
        fig.colorbar(image, ax=ax, shrink=0.8)
    plots.append(_save(fig, output_dir / "spatial_maps.png"))

    fig, axes = plt.subplots(2, 1, figsize=(10, 7))
    axes[0].plot(times[1:], arrays["frame_correlation"], linewidth=0.9)
    axes[0].set(title="Adjacent-frame spatial correlation", xlabel=x_label, ylabel="Pearson correlation")
    axes[1].plot(arrays["lags"], arrays["roi_mean_acf"], marker="o", ms=3, label="ROI mean")
    axes[1].plot(arrays["lags"], arrays["roi_median_acf"], marker="o", ms=3, label="ROI median")
    axes[1].plot(arrays["lags"], arrays["sampled_pixel_acf"], marker="o", ms=3, label="Sampled pixels")
    axes[1].axhline(0, color="black", linewidth=0.6)
    axes[1].set(title="Autocorrelation by lag", xlabel="Lag (frames)", ylabel="Correlation")
    axes[1].legend()
    plots.append(_save(fig, output_dir / "correlations.png"))

    fig, ax = plt.subplots(figsize=(9, 5))
    frequency = arrays["spectrum_frequency"]
    power = arrays["spectrum_power"]
    if len(frequency) > 1:
        ax.semilogy(frequency[1:], np.maximum(power[1:], np.finfo(float).tiny))
    ax.set(title="ROI mean spectrum after linear detrending", xlabel=f"Frequency ({frequency_unit})", ylabel="Power")
    plots.append(_save(fig, output_dir / "spectrum.png"))

    windows = arrays["window_results"]
    centers = np.array([(item["start_time"] + item["end_time"]) / 2 for item in windows])
    means = np.array([item["mean"] for item in windows])
    deviations = np.array([item["standard_deviation"] for item in windows])
    p5 = np.array([item["p5"] for item in windows])
    p95 = np.array([item["p95"] for item in windows])
    fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    axes[0].plot(centers, means, marker="o", label="Window mean")
    axes[0].fill_between(centers, p5, p95, alpha=0.25, label="Window p5-p95")
    axes[0].set(ylabel="Projected value", title="Windowed stability")
    axes[0].legend()
    axes[1].plot(centers, deviations, marker="o", color="#a64b2a")
    axes[1].set(xlabel=x_label, ylabel="Standard deviation")
    plots.append(_save(fig, output_dir / "stability.png"))
    return plots

