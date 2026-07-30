#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
联合分析 qwen_edit_batch_window_sweep.py 生成的多个样本结果。

默认面向10样本实验，主要输出：

1. combined_sample_summary.csv
   每个样本的最佳固定窗口、最终图像误差及全局混合结果。
2. window_size_summary.csv
   每种窗口长度跨样本的平均/标准差/中位数图像指标、理论Block加速比。
3. candidate_aggregate.csv.gz
   每个step、窗口长度和窗口位置跨样本聚合后的token/noise误差。
4. consensus_schedule_by_window_size.csv
   每种固定窗口长度在所有样本上的联合最优逐step路径。
5. global_consensus_schedule.csv
   允许每个step选择不同窗口长度时的联合最优路径。
6. block_execution_frequency.csv.gz
   逐step、逐Block在各样本最优路径中的执行频率。
7. analysis_summary.json 和 analysis_report.md
   机器可读与人类可读的联合结论。
8. 多张PNG曲线、热力图和10样本结果总览拼图。

这个脚本只分析已有文件，不加载Qwen模型，不使用GPU。
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


METRIC_FIELDS = (
    "mae",
    "mse",
    "rmse",
    "psnr",
    "changed_ratio",
    "elapsed_seconds",
)
CANDIDATE_METRICS = (
    "noise_relative_mse",
    "image_token_relative_mse",
    "text_token_relative_mse",
    "score",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="联合分析多个Qwen连续Block窗口搜索样本。"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(
            "/data4/guowenwu/MMDITModelCompression/"
            "outputs/test_n10_steps40_w3-57_stride3"
        ),
        help="qwen_edit_batch_window_sweep.py的输出目录。",
    )
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=None,
        help="分析结果目录；默认是<输入目录>/combined_analysis_10。",
    )
    parser.add_argument(
        "--expected-samples",
        type=int,
        default=10,
        help="预期完整样本数量，默认10。",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="完整样本不足expected-samples时仍继续分析。",
    )
    parser.add_argument(
        "--total-layers",
        type=int,
        default=60,
        help="Transformer Block总数，Qwen-Image-Edit-2511默认60。",
    )
    parser.add_argument(
        "--thumbnail-size",
        type=int,
        default=256,
        help="10样本结果总览中每张图的尺寸。",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="只输出CSV/JSON/Markdown，不生成图表。",
    )
    parser.add_argument(
        "--no-contact-sheet",
        action="store_true",
        help="不生成输入、baseline、推荐窗口和global mixed拼图。",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def to_float(value: Any) -> float:
    if value is None or value == "":
        return math.nan
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def finite(values: Iterable[float]) -> List[float]:
    return [value for value in values if math.isfinite(value)]


def mean(values: Iterable[float]) -> float:
    items = finite(values)
    return statistics.fmean(items) if items else math.nan


def median(values: Iterable[float]) -> float:
    items = finite(values)
    return statistics.median(items) if items else math.nan


def stdev(values: Iterable[float]) -> float:
    items = finite(values)
    return statistics.stdev(items) if len(items) >= 2 else 0.0 if items else math.nan


def minimum(values: Iterable[float]) -> float:
    items = finite(values)
    return min(items) if items else math.nan


def maximum(values: Iterable[float]) -> float:
    items = finite(values)
    return max(items) if items else math.nan


def fmt(value: Any, digits: int = 6) -> str:
    number = to_float(value)
    if not math.isfinite(number):
        return "N/A"
    if number == 0:
        return "0"
    if abs(number) < 1e-4 or abs(number) >= 1e4:
        return f"{number:.4e}"
    return f"{number:.{digits}f}"


def write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fieldnames: Sequence[str],
    gzip_output: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gzip_output:
        handle_context = gzip.open(
            path,
            mode="wt",
            encoding="utf-8-sig",
            newline="",
        )
    else:
        handle_context = path.open(
            mode="w",
            encoding="utf-8-sig",
            newline="",
        )
    with handle_context as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fieldnames),
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def discover_samples(
    input_dir: Path,
    expected_samples: int,
    allow_incomplete: bool,
) -> List[Dict[str, Any]]:
    samples_root = input_dir / "samples"
    if not samples_root.is_dir():
        raise FileNotFoundError(f"没有找到样本目录：{samples_root}")

    sample_records: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for sample_dir in sorted(samples_root.iterdir()):
        if not sample_dir.is_dir() or not sample_dir.name.isdigit():
            continue
        required = {
            "complete": sample_dir / "complete.json",
            "metadata": sample_dir / "metadata.json",
            "window_results": sample_dir / "window_results.json",
            "best_sequences": sample_dir / "best_sequences.json",
            "candidate_scores": sample_dir / "candidate_scores.csv.gz",
        }
        missing = [name for name, path in required.items() if not path.is_file()]
        if missing:
            skipped.append(f"{sample_dir.name}: 缺少{','.join(missing)}")
            continue

        complete = read_json(required["complete"])
        metadata = read_json(required["metadata"])
        window_results = read_json(required["window_results"])
        best_sequences = read_json(required["best_sequences"])
        sample_records.append(
            {
                "sample_index": int(complete.get("sample_index", sample_dir.name)),
                "sample_dir": sample_dir,
                "complete": complete,
                "metadata": metadata,
                "window_results": window_results,
                "best_sequences": best_sequences,
                "candidate_scores_path": required["candidate_scores"],
            }
        )

    sample_records.sort(key=lambda item: item["sample_index"])
    if len(sample_records) < expected_samples and not allow_incomplete:
        details = "\n".join(skipped[:20])
        raise RuntimeError(
            f"只找到{len(sample_records)}个完整样本，预期{expected_samples}个。"
            "如果确定要分析现有结果，请加--allow-incomplete。"
            + (f"\n不完整样本：\n{details}" if details else "")
        )
    if not sample_records:
        raise RuntimeError("没有找到任何可以分析的完整样本。")
    if len(sample_records) > expected_samples:
        sample_records = sample_records[:expected_samples]
    return sample_records


def validate_consistency(
    samples: Sequence[Dict[str, Any]],
) -> Tuple[List[int], int, str]:
    first = samples[0]
    window_sizes = sorted(int(key) for key in first["window_results"])
    num_steps = int(first["metadata"]["num_inference_steps"])
    strategy_version = str(first["metadata"].get("strategy_version", "unknown"))
    expected_windows = set(window_sizes)

    for sample in samples[1:]:
        current_windows = {int(key) for key in sample["window_results"]}
        if current_windows != expected_windows:
            raise RuntimeError(
                f"sample {sample['sample_index']:05d}的窗口长度集合不一致。"
            )
        current_steps = int(sample["metadata"]["num_inference_steps"])
        if current_steps != num_steps:
            raise RuntimeError(
                f"sample {sample['sample_index']:05d}的step数={current_steps}，"
                f"与首个样本的{num_steps}不一致。"
            )
        current_strategy = str(sample["metadata"].get("strategy_version", "unknown"))
        if current_strategy != strategy_version:
            raise RuntimeError("样本之间strategy_version不一致，不能直接联合分析。")
    return window_sizes, num_steps, strategy_version


def summarize_samples(samples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sample in samples:
        complete = sample["complete"]
        best = complete["best_window_result"]
        mixed = complete["global_mixed_result"]
        rows.append(
            {
                "sample_index": sample["sample_index"],
                "image_relative_path": complete.get("image_relative_path"),
                "image_path": complete.get("image_path"),
                "prompt_group": complete.get("prompt_group"),
                "prompt_id": complete.get("prompt_id"),
                "prompt": complete.get("prompt"),
                "generation_seed": complete.get("generation_seed"),
                "best_window_size_by_final_mse": complete.get(
                    "best_window_size_by_final_mse"
                ),
                "best_window_mse": best.get("mse"),
                "best_window_psnr": best.get("psnr"),
                "best_window_elapsed_seconds": best.get("elapsed_seconds"),
                "global_mixed_mse": mixed.get("mse"),
                "global_mixed_psnr": mixed.get("psnr"),
                "global_mixed_elapsed_seconds": mixed.get("elapsed_seconds"),
                "search_elapsed_seconds": complete.get("search_elapsed_seconds"),
                "sample_dir": str(sample["sample_dir"].resolve()),
            }
        )
    return rows


def summarize_window_sizes(
    samples: Sequence[Dict[str, Any]],
    window_sizes: Sequence[int],
    num_steps: int,
    total_layers: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for window_size in window_sizes:
        results = [
            sample["window_results"][str(window_size)]
            for sample in samples
        ]
        executed_blocks = total_layers + (num_steps - 1) * (window_size + 2)
        full_blocks = num_steps * total_layers
        row: Dict[str, Any] = {
            "window_size": window_size,
            "sample_count": len(results),
            "executed_blocks_per_generation": executed_blocks,
            "full_blocks_per_generation": full_blocks,
            "executed_block_fraction": executed_blocks / full_blocks,
            "theoretical_block_speedup": full_blocks / executed_blocks,
        }
        for metric in METRIC_FIELDS:
            values = [to_float(result.get(metric)) for result in results]
            row[f"mean_{metric}"] = mean(values)
            row[f"std_{metric}"] = stdev(values)
            row[f"median_{metric}"] = median(values)
            row[f"min_{metric}"] = minimum(values)
            row[f"max_{metric}"] = maximum(values)
        rows.append(row)

    mse_values = [to_float(row["mean_mse"]) for row in rows]
    finite_mse = finite(mse_values)
    mse_min = min(finite_mse)
    mse_max = max(finite_mse)
    min_window = min(window_sizes)
    max_window = max(window_sizes)
    for row in rows:
        window_size = int(row["window_size"])
        compute_norm = (
            (window_size - min_window) / (max_window - min_window)
            if max_window != min_window
            else 0.0
        )
        current_mse = to_float(row["mean_mse"])
        error_norm = (
            (current_mse - mse_min) / (mse_max - mse_min)
            if mse_max != mse_min
            else 0.0
        )
        row["normalized_compute"] = compute_norm
        row["normalized_error"] = error_norm
        row["knee_distance_to_ideal"] = math.hypot(
            compute_norm,
            error_norm,
        )
    return rows


def aggregate_candidates(
    samples: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    groups: Dict[
        Tuple[int, int, int, int],
        Dict[str, Any],
    ] = {}
    for sample in samples:
        sample_index = int(sample["sample_index"])
        with gzip.open(
            sample["candidate_scores_path"],
            "rt",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            for row in csv.DictReader(handle):
                step = int(row["step_number_1based"])
                window_size = int(row["window_size"])
                start = int(row["window_start_1based"])
                end = int(row["window_end_1based"])
                key = (step, window_size, start, end)
                group = groups.setdefault(
                    key,
                    {
                        "sample_indices": set(),
                        "executed_block_count": int(row["executed_block_count"]),
                        "skipped_block_count": int(row["skipped_block_count"]),
                        "executed_blocks_1based": row["executed_blocks_1based"],
                        "skipped_blocks_1based": row["skipped_blocks_1based"],
                        **{metric: [] for metric in CANDIDATE_METRICS},
                    },
                )
                group["sample_indices"].add(sample_index)
                for metric in CANDIDATE_METRICS:
                    group[metric].append(to_float(row.get(metric)))

    output: List[Dict[str, Any]] = []
    for (step, window_size, start, end), group in sorted(groups.items()):
        record: Dict[str, Any] = {
            "step_number_1based": step,
            "window_size": window_size,
            "window_start_1based": start,
            "window_end_1based": end,
            "sample_count": len(group["sample_indices"]),
            "coverage_ratio": len(group["sample_indices"]) / len(samples),
            "executed_block_count": group["executed_block_count"],
            "skipped_block_count": group["skipped_block_count"],
            "executed_blocks_1based": group["executed_blocks_1based"],
            "skipped_blocks_1based": group["skipped_blocks_1based"],
        }
        for metric in CANDIDATE_METRICS:
            values = group[metric]
            record[f"mean_{metric}"] = mean(values)
            record[f"std_{metric}"] = stdev(values)
            record[f"median_{metric}"] = median(values)
            record[f"min_{metric}"] = minimum(values)
            record[f"max_{metric}"] = maximum(values)
        output.append(record)
    return output


def full_step_row(
    schedule_name: str,
    window_size: Optional[int],
    total_layers: int,
    sample_count: int,
) -> Dict[str, Any]:
    blocks = ",".join(str(block) for block in range(1, total_layers + 1))
    return {
        "schedule_name": schedule_name,
        "requested_window_size": window_size,
        "step_number_1based": 1,
        "window_size": total_layers,
        "window_start_1based": None,
        "window_end_1based": None,
        "sample_count": sample_count,
        "coverage_ratio": 1.0,
        "executed_block_count": total_layers,
        "skipped_block_count": 0,
        "executed_blocks_1based": blocks,
        "skipped_blocks_1based": "",
        **{f"mean_{metric}": 0.0 for metric in CANDIDATE_METRICS},
        "mode": "full_compute",
        "cache_source": "none_first_timestep",
    }


def derive_consensus_schedules(
    candidate_rows: Sequence[Dict[str, Any]],
    window_sizes: Sequence[int],
    num_steps: int,
    total_layers: int,
    sample_count: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    by_window_step: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    by_step: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        step = int(row["step_number_1based"])
        window_size = int(row["window_size"])
        by_window_step[(window_size, step)].append(row)
        by_step[step].append(row)

    fixed_rows: List[Dict[str, Any]] = []
    for window_size in window_sizes:
        schedule_name = f"window_size_{window_size:02d}"
        fixed_rows.append(
            full_step_row(
                schedule_name,
                window_size,
                total_layers,
                sample_count,
            )
        )
        for step in range(2, num_steps + 1):
            candidates = by_window_step.get((window_size, step), [])
            if not candidates:
                raise RuntimeError(
                    f"window_size={window_size}, step={step}没有联合候选数据。"
                )
            best = min(
                candidates,
                key=lambda row: (
                    to_float(row["mean_score"]),
                    to_float(row["median_score"]),
                    int(row["window_start_1based"]),
                ),
            )
            fixed_rows.append(
                {
                    **best,
                    "schedule_name": schedule_name,
                    "requested_window_size": window_size,
                    "mode": "previous_timestep_same_block_cache",
                    "cache_source": "previous_scheduled_timestep_same_block",
                }
            )

    global_rows = [
        full_step_row(
            "global_mixed",
            None,
            total_layers,
            sample_count,
        )
    ]
    for step in range(2, num_steps + 1):
        candidates = by_step.get(step, [])
        if not candidates:
            raise RuntimeError(f"step={step}没有联合候选数据。")
        best = min(
            candidates,
            key=lambda row: (
                to_float(row["mean_score"]),
                to_float(row["median_score"]),
                -int(row["window_size"]),
                int(row["window_start_1based"]),
            ),
        )
        global_rows.append(
            {
                **best,
                "schedule_name": "global_mixed",
                "requested_window_size": None,
                "mode": "previous_timestep_same_block_cache",
                "cache_source": "previous_scheduled_timestep_same_block",
            }
        )
    return fixed_rows, global_rows


def parse_blocks(text: Any) -> List[int]:
    if text is None:
        return []
    return [
        int(part)
        for part in str(text).split(",")
        if part.strip()
    ]


def blocks_from_schedule_item(
    item: Mapping[str, Any],
    total_layers: int,
) -> List[int]:
    if item.get("mode") == "full_compute":
        return list(range(1, total_layers + 1))
    explicit = parse_blocks(item.get("executed_blocks_1based"))
    if explicit:
        return explicit
    start = int(item["window_start_1based"])
    end = int(item["window_end_1based"])
    return sorted({1, total_layers, *range(start, end + 1)})


def block_execution_frequencies(
    samples: Sequence[Dict[str, Any]],
    window_sizes: Sequence[int],
    num_steps: int,
    total_layers: int,
) -> List[Dict[str, Any]]:
    counts: Counter[Tuple[int, int, int]] = Counter()
    for sample in samples:
        schedules = sample["best_sequences"]["window_sizes"]
        for window_size in window_sizes:
            sequence = schedules[str(window_size)]
            if len(sequence) != num_steps:
                raise RuntimeError(
                    f"sample {sample['sample_index']:05d}, "
                    f"window={window_size}的序列长度不等于{num_steps}。"
                )
            for step, item in enumerate(sequence, start=1):
                for block in blocks_from_schedule_item(item, total_layers):
                    counts[(window_size, step, block)] += 1

    rows: List[Dict[str, Any]] = []
    for window_size in window_sizes:
        for step in range(1, num_steps + 1):
            for block in range(1, total_layers + 1):
                count = counts[(window_size, step, block)]
                rows.append(
                    {
                        "window_size": window_size,
                        "step_number_1based": step,
                        "block_number_1based": block,
                        "execution_count": count,
                        "sample_count": len(samples),
                        "execution_frequency": count / len(samples),
                    }
                )
    return rows


def choose_recommendations(
    sample_rows: Sequence[Dict[str, Any]],
    window_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    best_error = min(
        window_rows,
        key=lambda row: (
            to_float(row["mean_mse"]),
            -int(row["window_size"]),
        ),
    )
    knee = min(
        window_rows,
        key=lambda row: (
            to_float(row["knee_distance_to_ideal"]),
            to_float(row["mean_mse"]),
            int(row["window_size"]),
        ),
    )
    frequency = Counter(
        int(row["best_window_size_by_final_mse"])
        for row in sample_rows
    )
    mode_window, mode_count = min(
        frequency.items(),
        key=lambda item: (-item[1], -item[0]),
    )
    mode_row = next(
        row for row in window_rows
        if int(row["window_size"]) == mode_window
    )
    return {
        "lowest_mean_mse": dict(best_error),
        "compute_error_knee": dict(knee),
        "most_frequent_sample_best": {
            **dict(mode_row),
            "frequency_count": mode_count,
            "frequency_ratio": mode_count / len(sample_rows),
        },
        "sample_best_window_histogram": {
            str(window): count
            for window, count in sorted(frequency.items())
        },
    }


def aggregate_mixed_metrics(
    samples: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    results = [sample["complete"]["global_mixed_result"] for sample in samples]
    payload: Dict[str, Any] = {"sample_count": len(results)}
    for metric in METRIC_FIELDS:
        values = [to_float(result.get(metric)) for result in results]
        payload[f"mean_{metric}"] = mean(values)
        payload[f"std_{metric}"] = stdev(values)
        payload[f"median_{metric}"] = median(values)
        payload[f"min_{metric}"] = minimum(values)
        payload[f"max_{metric}"] = maximum(values)
    return payload


def consensus_matrix(
    schedule_rows: Sequence[Mapping[str, Any]],
    total_layers: int,
) -> List[List[int]]:
    matrix: List[List[int]] = []
    for row in sorted(
        schedule_rows,
        key=lambda item: int(item["step_number_1based"]),
    ):
        blocks = set(parse_blocks(row.get("executed_blocks_1based")))
        matrix.append(
            [1 if block in blocks else 0 for block in range(1, total_layers + 1)]
        )
    return matrix


def write_plots(
    analysis_dir: Path,
    sample_rows: Sequence[Dict[str, Any]],
    window_rows: Sequence[Dict[str, Any]],
    candidate_rows: Sequence[Dict[str, Any]],
    fixed_schedule_rows: Sequence[Dict[str, Any]],
    frequency_rows: Sequence[Dict[str, Any]],
    recommended_window: int,
    num_steps: int,
    total_layers: int,
) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as error:
        raise RuntimeError(
            "生成图表需要matplotlib和numpy；可以安装依赖或使用--no-plots。"
        ) from error

    sizes = [int(row["window_size"]) for row in window_rows]
    mean_mse = [to_float(row["mean_mse"]) for row in window_rows]
    std_mse = [to_float(row["std_mse"]) for row in window_rows]
    mean_psnr = [to_float(row["mean_psnr"]) for row in window_rows]
    std_psnr = [to_float(row["std_psnr"]) for row in window_rows]
    speedup = [to_float(row["theoretical_block_speedup"]) for row in window_rows]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), constrained_layout=True)
    axes[0].errorbar(sizes, mean_mse, yerr=std_mse, marker="o", markersize=3)
    axes[0].axvline(recommended_window, color="tab:red", linestyle="--")
    axes[0].set_title("Final image MSE across samples")
    axes[0].set_xlabel("Continuous middle-block window size")
    axes[0].set_ylabel("MSE vs full baseline (mean ± std)")
    axes[0].grid(alpha=0.25)

    axes[1].errorbar(sizes, mean_psnr, yerr=std_psnr, marker="o", markersize=3)
    axes[1].axvline(recommended_window, color="tab:red", linestyle="--")
    axes[1].set_title("Final image PSNR across samples")
    axes[1].set_xlabel("Continuous middle-block window size")
    axes[1].set_ylabel("PSNR vs full baseline (mean ± std)")
    axes[1].grid(alpha=0.25)
    fig.savefig(analysis_dir / "window_size_quality_curves.png", dpi=180)
    plt.close(fig)

    fig, ax1 = plt.subplots(figsize=(9, 5), constrained_layout=True)
    ax1.plot(sizes, mean_mse, color="tab:blue", marker="o", markersize=3)
    ax1.set_xlabel("Continuous middle-block window size")
    ax1.set_ylabel("Mean final-image MSE", color="tab:blue")
    ax1.tick_params(axis="y", labelcolor="tab:blue")
    ax1.grid(alpha=0.25)
    ax2 = ax1.twinx()
    ax2.plot(sizes, speedup, color="tab:orange", marker="s", markersize=3)
    ax2.set_ylabel("Theoretical Block speedup", color="tab:orange")
    ax2.tick_params(axis="y", labelcolor="tab:orange")
    ax1.axvline(recommended_window, color="tab:red", linestyle="--")
    ax1.set_title("Quality-compute trade-off")
    fig.savefig(analysis_dir / "quality_compute_tradeoff.png", dpi=180)
    plt.close(fig)

    histogram = Counter(
        int(row["best_window_size_by_final_mse"])
        for row in sample_rows
    )
    fig, ax = plt.subplots(figsize=(9, 4), constrained_layout=True)
    ax.bar(sorted(histogram), [histogram[key] for key in sorted(histogram)])
    ax.set_xlabel("Per-sample best fixed window size")
    ax.set_ylabel("Number of samples")
    ax.set_title("Best window-size histogram")
    ax.grid(axis="y", alpha=0.25)
    fig.savefig(analysis_dir / "best_window_histogram.png", dpi=180)
    plt.close(fig)

    frequency_matrix = np.zeros((num_steps, total_layers), dtype=float)
    for row in frequency_rows:
        if int(row["window_size"]) != recommended_window:
            continue
        step = int(row["step_number_1based"]) - 1
        block = int(row["block_number_1based"]) - 1
        frequency_matrix[step, block] = float(row["execution_frequency"])
    fig, ax = plt.subplots(figsize=(16, 7), constrained_layout=True)
    image = ax.imshow(
        frequency_matrix,
        aspect="auto",
        interpolation="nearest",
        vmin=0,
        vmax=1,
        cmap="viridis",
    )
    ax.set_title(
        f"Block execution frequency across samples (window={recommended_window})"
    )
    ax.set_xlabel("Block number (1-based)")
    ax.set_ylabel("Denoising step (1-based)")
    ax.set_xticks(range(0, total_layers, 5))
    ax.set_xticklabels(range(1, total_layers + 1, 5))
    fig.colorbar(image, ax=ax, label="Execution frequency")
    fig.savefig(
        analysis_dir / f"block_frequency_window_{recommended_window:02d}.png",
        dpi=180,
    )
    plt.close(fig)

    selected_schedule = [
        row
        for row in fixed_schedule_rows
        if int(row["requested_window_size"]) == recommended_window
    ]
    binary_matrix = np.asarray(
        consensus_matrix(selected_schedule, total_layers),
        dtype=float,
    )
    fig, ax = plt.subplots(figsize=(16, 7), constrained_layout=True)
    image = ax.imshow(
        binary_matrix,
        aspect="auto",
        interpolation="nearest",
        vmin=0,
        vmax=1,
        cmap="Greys",
    )
    ax.set_title(
        f"Dataset-level consensus schedule (window={recommended_window})"
    )
    ax.set_xlabel("Block number (1-based)")
    ax.set_ylabel("Denoising step (1-based)")
    ax.set_xticks(range(0, total_layers, 5))
    ax.set_xticklabels(range(1, total_layers + 1, 5))
    fig.colorbar(image, ax=ax, ticks=[0, 1], label="0=cache, 1=execute")
    fig.savefig(
        analysis_dir / f"consensus_matrix_window_{recommended_window:02d}.png",
        dpi=180,
    )
    plt.close(fig)

    selected_candidates = [
        row
        for row in candidate_rows
        if int(row["window_size"]) == recommended_window
    ]
    starts = sorted(
        {int(row["window_start_1based"]) for row in selected_candidates}
    )
    start_to_col = {start: index for index, start in enumerate(starts)}
    score_matrix = np.full((num_steps - 1, len(starts)), np.nan, dtype=float)
    for row in selected_candidates:
        step = int(row["step_number_1based"]) - 2
        column = start_to_col[int(row["window_start_1based"])]
        score_matrix[step, column] = to_float(row["mean_score"])
    fig, ax = plt.subplots(figsize=(14, 7), constrained_layout=True)
    image = ax.imshow(
        score_matrix,
        aspect="auto",
        interpolation="nearest",
        cmap="magma",
    )
    ax.set_title(
        f"Mean candidate score by step/start (window={recommended_window})"
    )
    ax.set_xlabel("Window start Block (1-based)")
    ax.set_ylabel("Denoising step (2..N)")
    tick_positions = list(range(0, len(starts), max(1, len(starts) // 12)))
    ax.set_xticks(tick_positions)
    ax.set_xticklabels([starts[index] for index in tick_positions])
    fig.colorbar(image, ax=ax, label="Mean score across samples")
    fig.savefig(
        analysis_dir / f"candidate_score_window_{recommended_window:02d}.png",
        dpi=180,
    )
    plt.close(fig)


def make_contact_sheet(
    samples: Sequence[Dict[str, Any]],
    recommended_window: int,
    output_path: Path,
    thumbnail_size: int,
) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError as error:
        raise RuntimeError(
            "生成总览拼图需要Pillow；可以安装Pillow或使用--no-contact-sheet。"
        ) from error

    columns = [
        ("Input", "input"),
        ("Full baseline", "baseline"),
        (f"Window {recommended_window}", "recommended"),
        ("Global mixed", "mixed"),
    ]
    label_width = 110
    header_height = 42
    row_height = thumbnail_size + 34
    canvas = Image.new(
        "RGB",
        (
            label_width + thumbnail_size * len(columns),
            header_height + row_height * len(samples),
        ),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    for column_index, (title, _) in enumerate(columns):
        x = label_width + column_index * thumbnail_size + 8
        draw.text((x, 12), title, fill="black", font=font)

    def paste_image(path_text: Any, x: int, y: int) -> None:
        if not path_text:
            draw.rectangle(
                (x, y, x + thumbnail_size - 1, y + thumbnail_size - 1),
                outline="red",
            )
            draw.text((x + 8, y + 8), "missing path", fill="red", font=font)
            return
        path = Path(str(path_text))
        if not path.is_file():
            draw.rectangle(
                (x, y, x + thumbnail_size - 1, y + thumbnail_size - 1),
                outline="red",
            )
            draw.text((x + 8, y + 8), "file missing", fill="red", font=font)
            return
        with Image.open(path) as opened:
            image = opened.convert("RGB")
            image = ImageOps.fit(
                image,
                (thumbnail_size, thumbnail_size),
                method=Image.Resampling.LANCZOS,
            )
            canvas.paste(image, (x, y))

    for row_index, sample in enumerate(samples):
        y = header_height + row_index * row_height
        complete = sample["complete"]
        window_result = sample["window_results"][str(recommended_window)]
        paths = {
            "input": complete.get("image_path"),
            "baseline": complete.get("baseline_image"),
            "recommended": window_result.get("result_image"),
            "mixed": complete["global_mixed_result"].get("result_image"),
        }
        draw.text(
            (8, y + 8),
            f"sample\n{sample['sample_index']:05d}",
            fill="black",
            font=font,
        )
        for column_index, (_, key) in enumerate(columns):
            x = label_width + column_index * thumbnail_size
            paste_image(paths[key], x, y)
        draw.text(
            (label_width + 4, y + thumbnail_size + 7),
            f"prompt: {str(complete.get('prompt', ''))[:120]}",
            fill="black",
            font=font,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", compress_level=6)


def markdown_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def write_report(
    path: Path,
    input_dir: Path,
    sample_count: int,
    num_steps: int,
    total_layers: int,
    strategy_version: str,
    window_rows: Sequence[Dict[str, Any]],
    recommendations: Dict[str, Any],
    mixed_summary: Dict[str, Any],
    fixed_schedule_rows: Sequence[Dict[str, Any]],
) -> None:
    lowest = recommendations["lowest_mean_mse"]
    knee = recommendations["compute_error_knee"]
    frequent = recommendations["most_frequent_sample_best"]
    knee_window = int(knee["window_size"])

    quality_top = sorted(
        window_rows,
        key=lambda row: (
            to_float(row["mean_mse"]),
            -int(row["window_size"]),
        ),
    )[:10]
    knee_schedule = [
        row
        for row in fixed_schedule_rows
        if int(row["requested_window_size"]) == knee_window
    ]

    lines = [
        "# Qwen连续Block窗口：多样本联合分析",
        "",
        f"- 输入目录：`{input_dir.resolve()}`",
        f"- 完整样本数：{sample_count}",
        f"- 推理step数：{num_steps}",
        f"- Block总数：{total_layers}",
        f"- 策略版本：`{strategy_version}`",
        "",
        "## 核心结论",
        "",
        (
            f"- 最低平均最终图像MSE：窗口长度 **{int(lowest['window_size'])}**，"
            f"mean MSE={fmt(lowest['mean_mse'])}，"
            f"mean PSNR={fmt(lowest['mean_psnr'], 3)}，"
            f"理论Block加速比={fmt(lowest['theoretical_block_speedup'], 3)}×。"
        ),
        (
            f"- 计算量—误差折中拐点：窗口长度 **{knee_window}**，"
            f"mean MSE={fmt(knee['mean_mse'])}，"
            f"mean PSNR={fmt(knee['mean_psnr'], 3)}，"
            f"理论Block加速比={fmt(knee['theoretical_block_speedup'], 3)}×。"
        ),
        (
            f"- 单样本最佳窗口中出现最频繁的是 **{int(frequent['window_size'])}**，"
            f"在{int(frequent['frequency_count'])}/{sample_count}个样本中胜出。"
        ),
        (
            f"- 每个样本各自使用全局混合路径时：mean MSE="
            f"{fmt(mixed_summary['mean_mse'])}，mean PSNR="
            f"{fmt(mixed_summary['mean_psnr'], 3)}。"
        ),
        "",
        "> 理论Block加速比只按实际执行Block数量估算，未包含VAE、文本编码器、"
        "调度器、缓存读写和框架开销，不等同于真实端到端加速比。",
        "",
        "## 最低平均MSE的前10种窗口",
        "",
        markdown_table(
            ["window", "mean MSE", "std MSE", "mean PSNR", "Block speedup"],
            [
                [
                    int(row["window_size"]),
                    fmt(row["mean_mse"]),
                    fmt(row["std_mse"]),
                    fmt(row["mean_psnr"], 3),
                    f"{fmt(row['theoretical_block_speedup'], 3)}×",
                ]
                for row in quality_top
            ],
        ),
        "",
        f"## 折中推荐窗口 {knee_window} 的联合逐step序列",
        "",
        markdown_table(
            ["step", "window", "execute blocks", "mean score"],
            [
                [
                    int(row["step_number_1based"]),
                    (
                        "full"
                        if row.get("mode") == "full_compute"
                        else (
                            f"{int(row['window_start_1based'])}-"
                            f"{int(row['window_end_1based'])}"
                        )
                    ),
                    row["executed_blocks_1based"],
                    fmt(row.get("mean_score")),
                ]
                for row in sorted(
                    knee_schedule,
                    key=lambda item: int(item["step_number_1based"]),
                )
            ],
        ),
        "",
        "## 输出文件说明",
        "",
        "- `combined_sample_summary.csv`：逐样本结果。",
        "- `window_size_summary.csv`：窗口长度级联合统计。",
        "- `candidate_aggregate.csv.gz`：所有候选位置的跨样本统计。",
        "- `consensus_schedule_by_window_size.csv`：每种固定窗口的联合路径。",
        "- `global_consensus_schedule.csv`：允许窗口长度变化的联合路径。",
        "- `block_execution_frequency.csv.gz`：逐层执行频率。",
        "- `contact_sheet_10_samples.png`：10个样本的直观对比。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    analysis_dir = (
        args.analysis_dir.resolve()
        if args.analysis_dir is not None
        else input_dir / "combined_analysis_10"
    )
    analysis_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/8] 扫描完整样本：{input_dir}", flush=True)
    samples = discover_samples(
        input_dir=input_dir,
        expected_samples=args.expected_samples,
        allow_incomplete=args.allow_incomplete,
    )
    window_sizes, num_steps, strategy_version = validate_consistency(samples)
    print(
        f"      找到{len(samples)}个完整样本；"
        f"窗口={window_sizes[0]}..{window_sizes[-1]}；step={num_steps}",
        flush=True,
    )

    print("[2/8] 汇总逐样本与逐窗口最终图像指标", flush=True)
    sample_rows = summarize_samples(samples)
    window_rows = summarize_window_sizes(
        samples=samples,
        window_sizes=window_sizes,
        num_steps=num_steps,
        total_layers=args.total_layers,
    )
    recommendations = choose_recommendations(sample_rows, window_rows)
    mixed_summary = aggregate_mixed_metrics(samples)

    sample_fields = list(sample_rows[0])
    write_csv(
        analysis_dir / "combined_sample_summary.csv",
        sample_rows,
        sample_fields,
    )
    window_fields = list(window_rows[0])
    write_csv(
        analysis_dir / "window_size_summary.csv",
        window_rows,
        window_fields,
    )

    print("[3/8] 聚合所有样本的逐step候选误差", flush=True)
    candidate_rows = aggregate_candidates(samples)
    candidate_fields = list(candidate_rows[0])
    write_csv(
        analysis_dir / "candidate_aggregate.csv.gz",
        candidate_rows,
        candidate_fields,
        gzip_output=True,
    )

    print("[4/8] 计算数据集级联合最优路径", flush=True)
    fixed_schedule_rows, global_schedule_rows = derive_consensus_schedules(
        candidate_rows=candidate_rows,
        window_sizes=window_sizes,
        num_steps=num_steps,
        total_layers=args.total_layers,
        sample_count=len(samples),
    )
    schedule_fields = list(fixed_schedule_rows[0])
    for row in fixed_schedule_rows[1:]:
        for key in row:
            if key not in schedule_fields:
                schedule_fields.append(key)
    write_csv(
        analysis_dir / "consensus_schedule_by_window_size.csv",
        fixed_schedule_rows,
        schedule_fields,
    )
    global_fields = list(global_schedule_rows[0])
    for row in global_schedule_rows[1:]:
        for key in row:
            if key not in global_fields:
                global_fields.append(key)
    write_csv(
        analysis_dir / "global_consensus_schedule.csv",
        global_schedule_rows,
        global_fields,
    )

    print("[5/8] 统计逐step、逐Block执行频率", flush=True)
    frequency_rows = block_execution_frequencies(
        samples=samples,
        window_sizes=window_sizes,
        num_steps=num_steps,
        total_layers=args.total_layers,
    )
    write_csv(
        analysis_dir / "block_execution_frequency.csv.gz",
        frequency_rows,
        list(frequency_rows[0]),
        gzip_output=True,
    )

    knee_window = int(
        recommendations["compute_error_knee"]["window_size"]
    )
    summary_payload = {
        "input_dir": str(input_dir),
        "analysis_dir": str(analysis_dir.resolve()),
        "sample_count": len(samples),
        "sample_indices": [sample["sample_index"] for sample in samples],
        "strategy_version": strategy_version,
        "num_inference_steps": num_steps,
        "total_layers": args.total_layers,
        "window_sizes": window_sizes,
        "recommendations": recommendations,
        "global_mixed_summary": mixed_summary,
        "dataset_consensus_for_knee_window": [
            row
            for row in fixed_schedule_rows
            if int(row["requested_window_size"]) == knee_window
        ],
        "global_consensus_schedule": global_schedule_rows,
    }
    write_json(summary_payload, analysis_dir / "analysis_summary.json")

    print("[6/8] 生成Markdown报告", flush=True)
    write_report(
        path=analysis_dir / "analysis_report.md",
        input_dir=input_dir,
        sample_count=len(samples),
        num_steps=num_steps,
        total_layers=args.total_layers,
        strategy_version=strategy_version,
        window_rows=window_rows,
        recommendations=recommendations,
        mixed_summary=mixed_summary,
        fixed_schedule_rows=fixed_schedule_rows,
    )

    if not args.no_plots:
        print("[7/8] 生成联合曲线和热力图", flush=True)
        write_plots(
            analysis_dir=analysis_dir,
            sample_rows=sample_rows,
            window_rows=window_rows,
            candidate_rows=candidate_rows,
            fixed_schedule_rows=fixed_schedule_rows,
            frequency_rows=frequency_rows,
            recommended_window=knee_window,
            num_steps=num_steps,
            total_layers=args.total_layers,
        )
    else:
        print("[7/8] 已按参数跳过图表", flush=True)

    if not args.no_contact_sheet:
        print("[8/8] 生成10样本结果总览拼图", flush=True)
        make_contact_sheet(
            samples=samples,
            recommended_window=knee_window,
            output_path=analysis_dir / "contact_sheet_10_samples.png",
            thumbnail_size=args.thumbnail_size,
        )
    else:
        print("[8/8] 已按参数跳过总览拼图", flush=True)

    print("", flush=True)
    print("联合分析完成。", flush=True)
    print(f"结果目录：{analysis_dir.resolve()}", flush=True)
    print(
        f"折中推荐窗口：{knee_window}；"
        f"mean MSE={fmt(recommendations['compute_error_knee']['mean_mse'])}；"
        f"理论Block加速比="
        f"{fmt(recommendations['compute_error_knee']['theoretical_block_speedup'], 3)}×",
        flush=True,
    )
    print(
        f"详细报告：{(analysis_dir / 'analysis_report.md').resolve()}",
        flush=True,
    )


if __name__ == "__main__":
    main()
