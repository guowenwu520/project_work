#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen-Image-Edit 蓝线缓存策略：100组校准 + 20组独立验证（v4）。

流程：
1. 从同一份固定manifest中划分互不重叠的calibration/validation样本。
2. calibration样本完整运行，记录相邻timestep同层text/image残差变化。
3. 用跨样本分位数生成step×block风险图，再收缩成“缓存前缀/缓存后缀、
   中间连续区间重算”的蓝线边界；Block 1/60始终重算。
4. validation样本各运行一次完整基线和一次固定蓝线schedule，保存逐step误差、
   逐Block执行/缓存、缓存来源step、缓存年龄和最终图像指标。

该脚本不在验证阶段搜索候选窗口，验证的是可以直接部署的静态缓存表。
"""

from __future__ import annotations

import argparse
import copy
import csv
import gc
import gzip
import json
import math
import os
import random
import re
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.distributed as dist
from torch.distributed.elastic.multiprocessing.errors import record
from PIL import Image

from qwen_edit_diagonal_bridge_search import (
    BLUE_LINE_CACHE_STRATEGY_VERSION,
    BlueLineScheduledController,
    FullReferenceController,
    ResidualProfileController,
    generate_image,
    image_metrics,
    infer_forwards_per_step,
    load_pipeline,
    one_based_layer_string,
    replace_transformer_forward,
)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
STRATEGY_VERSION = BLUE_LINE_CACHE_STRATEGY_VERSION
DEFAULT_PROMPT_GROUPS = {
    "FFHQ 主训练",
    "FFHQ 额外评估",
    "早期 10 prompt 评估",
    "生活场景评估",
    "姿态背景评估",
}

def save_input_artifact(
    sample_dir: Path,
    input_image: Image.Image,
    prompt: str,
    args: argparse.Namespace,
) -> Tuple[Path, Path]:
    """保存输入图像和 prompt 到样本目录，返回文件路径。"""
    input_image_path = sample_dir / f"input_image{image_suffix(args)}"
    save_image(input_image, input_image_path, args)
    prompt_path = sample_dir / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    return input_image_path, prompt_path

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用完整轨迹生成蓝线缓存表，并在独立样本上验证。"
    )
    parser.add_argument(
        "--model-path",
        default="/data4/guowenwu/MMDITModelCompression/models/Qwen-Image-Edit-2511",
    )
    parser.add_argument(
        "--dataset-root",
        default="/data4/guowenwu/MMDITModelCompression/dataset/images1024x1024",
    )
    parser.add_argument(
        "--prompt-file",
        default="/data4/guowenwu/MMDITModelCompression/portrait_prompts.md",
    )
    parser.add_argument("--calibration-count", type=int, default=200)
    parser.add_argument("--validation-count", type=int, default=20)
    # default=20260724
    parser.add_argument("--sampling-seed", type=int, default=20260806)
    parser.add_argument("--generation-seed", type=int, default=0)
    parser.add_argument("--num-inference-steps", type=int, default=40)
    parser.add_argument("--true-cfg-scale", type=float, default=1.0)
    parser.add_argument("--guidance-scale", type=float, default=1.0)
    parser.add_argument(
        "--forwards-per-step", type=int, choices=[1, 2], default=None
    )
    # 新增：校准缓存复用
    parser.add_argument(
        "--load-calibration-cache",
        type=str,
        default=None,
        help="从已有输出目录加载校准数据（该目录下需包含 calibration/samples/），跳过校准样本生成。",
    )
    parser.add_argument("--noise-weight", type=float, default=1.0)
    parser.add_argument("--image-token-weight", type=float, default=1.0)
    parser.add_argument("--text-token-weight", type=float, default=0.25)
    parser.add_argument(
        "--profile-quantile",
        type=float,
        default=0.90,
        help="每个step×block使用跨校准样本的该分位数作为风险，默认P90。",
    )
    parser.add_argument(
        "--target-cache-ratio",
        type=float,
        default=0.90,
        help=(
            "生成蓝线前先把风险最低的该比例cell标为可缓存；"
            "连续中间计算区约束会使实际缓存比例不高于该值。"
        ),
    )
    parser.add_argument(
        "--profile-smoothing-radius",
        type=int,
        default=1,
        help="对step×block风险做中值平滑的半径，默认1。",
    )
    parser.add_argument(
        "--max-cache-age",
        type=int,
        default=0,
        help="同一Block最多连续缓存多少step；0表示不强制刷新。",
    )
    parser.add_argument(
        "--force-full-first-steps",
        type=int,
        default=1,
        help="开头强制完整计算的step数，至少为1。",
    )
    parser.add_argument(
        "--force-full-last-steps",
        type=int,
        default=1,
        help="结尾强制完整计算的step数，默认1。",
    )
    parser.add_argument("--dtype", choices=["bf16", "fp16"], default="bf16")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--cpu-offload", action="store_true")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument(
        "--output-dir",
        default=(
            "/data4/guowenwu/MMDITModelCompression/outputs/"
            "blue_line_cache_cal1000_val200"
        ),
    )
    parser.add_argument("--image-format", choices=["png", "jpg", "webp"], default="png")
    parser.add_argument("--image-quality", type=int, default=95)
    parser.add_argument("--include-viton-prompts", action="store_true")
    parser.add_argument("--prompt-language", choices=["english", "chinese"], default="english")
    parser.add_argument(
        "--no-save-calibration-images",
        dest="save_calibration_images",
        action="store_false",
    )
    parser.set_defaults(save_calibration_images=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.set_defaults(resume=True)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--show-progress", action="store_true")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.load_calibration_cache:
        cache_dir = Path(args.load_calibration_cache)
        if not (cache_dir / "calibration" / "samples").is_dir():
            raise ValueError(f"校准缓存路径无效：{cache_dir / 'calibration' / 'samples'}")
        if args.calibration_count > 0:
            print("WARNING: --load-calibration-cache 已指定，将忽略 --calibration-count，校准样本数设为 0。")
        if args.validation_count < 0:
            raise ValueError("--validation-count 不能为负。")
    else:
        if args.calibration_count <= 0:
            raise ValueError("校准样本数必须大于 0，除非使用 --load-calibration-cache。")
        if args.validation_count < 0:
            raise ValueError("--validation-count 不能为负。")
    if args.num_inference_steps <= 1:
        raise ValueError("--num-inference-steps必须大于1。")
    if not 0.0 < args.profile_quantile <= 1.0:
        raise ValueError("--profile-quantile必须在(0,1]。")
    if not 0.0 <= args.target_cache_ratio <= 1.0:
        raise ValueError("--target-cache-ratio必须在[0,1]。")
    if args.profile_smoothing_radius < 0:
        raise ValueError("--profile-smoothing-radius不能小于0。")
    if args.max_cache_age < 0:
        raise ValueError("--max-cache-age不能小于0。")
    if args.force_full_first_steps < 1:
        raise ValueError("--force-full-first-steps至少为1。")
    if args.force_full_last_steps < 0:
        raise ValueError("--force-full-last-steps不能小于0。")
    weights = (args.noise_weight, args.image_token_weight, args.text_token_weight)
    if min(weights) < 0 or sum(weights) <= 0:
        raise ValueError("误差权重不能为负，且至少一个权重大于0。")
    if not Path(args.dataset_root).is_dir():
        raise FileNotFoundError(f"数据集目录不存在：{args.dataset_root}")
    if not Path(args.prompt_file).is_file():
        raise FileNotFoundError(f"提示词文件不存在：{args.prompt_file}")
    if not args.prepare_only and not Path(args.model_path).is_dir():
        raise FileNotFoundError(f"模型目录不存在：{args.model_path}")


def initialize_distributed(args: argparse.Namespace) -> Tuple[int, int, int]:
    world_size = int(os.environ.get("WORLD_SIZE", "1"))
    rank = int(os.environ.get("RANK", "0"))
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    if world_size > 1:
        torch.cuda.set_device(local_rank)
        dist.init_process_group(backend="gloo")
        args.device = f"cuda:{local_rank}"
    return rank, local_rank, world_size


def barrier(world_size: int) -> None:
    if world_size > 1:
        dist.barrier()


def cleanup_distributed(world_size: int) -> None:
    if world_size > 1 and dist.is_initialized():
        dist.destroy_process_group()


def rank_print(rank: int, message: str) -> None:
    print(f"[rank {rank}] {message}", flush=True)


def write_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output_file:
        json.dump(value, output_file, ensure_ascii=False, indent=2)
    temporary.replace(path)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as input_file:
        return json.load(input_file)


def write_json_gz(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as output_file:
        json.dump(value, output_file, ensure_ascii=False)
    temporary.replace(path)


def read_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as input_file:
        return json.load(input_file)


def write_csv(
    rows: Sequence[Dict[str, Any]],
    path: Path,
    fieldnames: Optional[Sequence[str]] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        ordered: List[str] = []
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    ordered.append(key)
        fieldnames = ordered
    temporary = path.with_suffix(path.suffix + ".tmp")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(temporary, "wt", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    temporary.replace(path)


def parse_prompt_markdown(
    prompt_path: Path,
    language: str,
    include_viton: bool,
) -> Tuple[List[Dict[str, str]], str]:
    text = prompt_path.read_text(encoding="utf-8")
    group_pattern = re.compile(
        r"^##\s+(.+?)\s*$([\s\S]*?)(?=^##\s+|\Z)", re.MULTILINE
    )
    entry_pattern = re.compile(
        r"^###\s+(.+?)\s*$([\s\S]*?)(?=^###\s+|\Z)", re.MULTILINE
    )
    label = "英文" if language == "english" else "中文"
    prompts: List[Dict[str, str]] = []
    negative_entries: Dict[str, str] = {}
    for group_match in group_pattern.finditer(text):
        group_name = group_match.group(1).strip()
        for entry_match in entry_pattern.finditer(group_match.group(2)):
            prompt_id = entry_match.group(1).strip()
            value_match = re.search(
                rf"{label}：\s*```text\s*([\s\S]*?)\s*```",
                entry_match.group(2),
            )
            if value_match is None:
                continue
            prompt_text = value_match.group(1).strip()
            if group_name == "负向 prompt":
                negative_entries[prompt_id] = prompt_text
                continue
            if group_name == "试衣" and not include_viton:
                continue
            if not include_viton and group_name not in DEFAULT_PROMPT_GROUPS:
                continue
            prompts.append(
                {"group": group_name, "prompt_id": prompt_id, "prompt": prompt_text}
            )
    if not prompts:
        raise ValueError(f"没有从{prompt_path}解析出可用提示词。")
    negative_parts = [
        negative_entries[key]
        for key in ("ffhq_negative", "ffhq_negative_occlusion")
        if key in negative_entries
    ]
    if include_viton and "viton_negative" in negative_entries:
        negative_parts.append(negative_entries["viton_negative"])
    return prompts, ", ".join(negative_parts) if negative_parts else " "


def scan_images(dataset_root: Path) -> List[Path]:
    paths = [
        path
        for path in dataset_root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(paths, key=lambda path: path.as_posix())


def build_or_load_manifest(
    args: argparse.Namespace,
    prompts: Sequence[Dict[str, str]],
    negative_prompt: str,
    output_dir: Path,
    rank: int,
    world_size: int,
) -> List[Dict[str, Any]]:
    manifest_path = output_dir / "manifest.jsonl"
    metadata_path = output_dir / "manifest_metadata.json"
    total_count = args.calibration_count + args.validation_count
    expected_metadata = {
        "dataset_root": str(Path(args.dataset_root).resolve()),
        "calibration_count": args.calibration_count,
        "validation_count": args.validation_count,
        "sampling_seed": args.sampling_seed,
        "generation_seed": args.generation_seed,
        "prompt_file": str(Path(args.prompt_file).resolve()),
        "prompt_language": args.prompt_language,
        "include_viton_prompts": args.include_viton_prompts,
    }
    if rank == 0 and manifest_path.exists():
        if not metadata_path.exists():
            raise FileNotFoundError("已有manifest但缺少manifest_metadata.json。")
        old_metadata = read_json(metadata_path)
        mismatched = [
            key for key, value in expected_metadata.items() if old_metadata.get(key) != value
        ]
        if mismatched:
            raise ValueError(
                f"现有manifest与本次参数不一致：{mismatched}。请更换输出目录。"
            )
    if rank == 0 and not manifest_path.exists():
        dataset_root = Path(args.dataset_root).resolve()
        images = scan_images(dataset_root)
        if len(images) < total_count:
            raise ValueError(f"只找到{len(images)}张图片，少于需要的{total_count}张。")
        rng = random.Random(args.sampling_seed)
        selected = rng.sample(images, total_count)
        rows: List[Dict[str, Any]] = []
        for sample_index, image_path in enumerate(selected):
            prompt_item = prompts[rng.randrange(len(prompts))]
            split = "calibration" if sample_index < args.calibration_count else "validation"
            split_index = (
                sample_index
                if split == "calibration"
                else sample_index - args.calibration_count
            )
            rows.append(
                {
                    "sample_index": sample_index,
                    "split": split,
                    "split_index": split_index,
                    "image_path": str(image_path),
                    "image_relative_path": str(image_path.relative_to(dataset_root)),
                    "prompt_group": prompt_item["group"],
                    "prompt_id": prompt_item["prompt_id"],
                    "prompt": prompt_item["prompt"],
                    "negative_prompt": negative_prompt,
                    "generation_seed": args.generation_seed + sample_index,
                }
            )
        temporary = manifest_path.with_suffix(".jsonl.tmp")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as output_file:
            for row in rows:
                output_file.write(json.dumps(row, ensure_ascii=False) + "\n")
        temporary.replace(manifest_path)
        write_json(
            {
                **expected_metadata,
                "total_count": total_count,
                "total_discovered_images": len(images),
                "prompt_count": len(prompts),
            },
            metadata_path,
        )
    barrier(world_size)
    rows = []
    with manifest_path.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            if line.strip():
                rows.append(json.loads(line))
    if len(rows) != total_count:
        raise ValueError(f"manifest有{len(rows)}条，预期{total_count}条。")
    return rows


def experiment_signature(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "strategy_version": STRATEGY_VERSION,
        "num_inference_steps": args.num_inference_steps,
        "true_cfg_scale": args.true_cfg_scale,
        "guidance_scale": args.guidance_scale,
        "forwards_per_step": args.forwards_per_step,
        "profile_quantile": args.profile_quantile,
        "target_cache_ratio": args.target_cache_ratio,
        "profile_smoothing_radius": args.profile_smoothing_radius,
        "max_cache_age": args.max_cache_age,
        "force_full_first_steps": args.force_full_first_steps,
        "force_full_last_steps": args.force_full_last_steps,
        "noise_weight": args.noise_weight,
        "image_token_weight": args.image_token_weight,
        "text_token_weight": args.text_token_weight,
        "width": args.width,
        "height": args.height,
        "dtype": args.dtype,
    }


def make_sample_args(args: argparse.Namespace, row: Dict[str, Any]) -> argparse.Namespace:
    sample_args = copy.copy(args)
    sample_args.prompt = str(row["prompt"])
    sample_args.negative_prompt = str(row["negative_prompt"])
    sample_args.seed = int(row["generation_seed"])
    sample_args.sample_index = int(row["sample_index"])
    return sample_args


def load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB").copy()


def image_suffix(args: argparse.Namespace) -> str:
    return ".jpg" if args.image_format == "jpg" else f".{args.image_format}"


def save_image(image: Image.Image, path: Path, args: argparse.Namespace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.image_format == "png":
        image.save(path, format="PNG")
    elif args.image_format == "jpg":
        image.save(path, format="JPEG", quality=args.image_quality, subsampling=0)
    else:
        image.save(path, format="WEBP", quality=args.image_quality)


def append_error(
    output_dir: Path,
    rank: int,
    row: Dict[str, Any],
    error: BaseException,
) -> None:
    path = output_dir / f"errors_rank_{rank:03d}.jsonl"
    payload = {
        "sample_index": row.get("sample_index"),
        "split": row.get("split"),
        "image_path": row.get("image_path"),
        "error_type": type(error).__name__,
        "error": str(error),
        "traceback": traceback.format_exc(),
    }
    with path.open("a", encoding="utf-8") as output_file:
        output_file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def process_calibration_sample(
    pipe,
    row: Dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
    rank: int,
) -> Dict[str, Any]:
    sample_index = int(row["sample_index"])
    sample_dir = output_dir / "calibration" / "samples" / f"{sample_index:05d}"
    complete_path = sample_dir / "complete.json"
    signature = experiment_signature(args)
    if args.resume and complete_path.exists():
        complete = read_json(complete_path)
        if complete.get("experiment_signature") == signature:
            return complete
    sample_dir.mkdir(parents=True, exist_ok=True)

    sample_args = make_sample_args(args, row)
    input_image = load_rgb(Path(row["image_path"]))

    # 保存输入图像和 prompt
    input_image_path, prompt_path = save_input_artifact(
        sample_dir, input_image, sample_args.prompt, args
    )

    transformer = pipe.transformer
    blocks = list(transformer.transformer_blocks)
    forwards_per_step = infer_forwards_per_step(sample_args)
    controller = ResidualProfileController(
        transformer_blocks=blocks,
        original_transformer_forward=transformer.forward,
        args=sample_args,
        forwards_per_step=forwards_per_step,
    )
    rank_print(rank, f"calibration sample {sample_index:05d}: 完整轨迹统计开始")
    torch.cuda.synchronize(torch.device(sample_args.device))
    started = time.perf_counter()
    with replace_transformer_forward(transformer, controller):
        baseline = generate_image(pipe, [input_image], sample_args)
    torch.cuda.synchronize(torch.device(sample_args.device))
    elapsed = time.perf_counter() - started
    controller.validate_complete()
    profile_path = sample_dir / "residual_profile.json.gz"
    write_json_gz(controller.rows, profile_path)

    if args.save_calibration_images:
        baseline_path = sample_dir / f"baseline_full{image_suffix(args)}"
        save_image(baseline, baseline_path, args)
    else:
        baseline_path = None

    complete = {
        **row,
        "strategy_version": STRATEGY_VERSION,
        "experiment_signature": signature,
        "rank": rank,
        "total_layers": len(blocks),
        "forwards_per_step": forwards_per_step,
        "elapsed_seconds": elapsed,
        "profile_row_count": len(controller.rows),
        "profile_file": str(profile_path.resolve()),
        "input_image_file": str(input_image_path.resolve()),
        "prompt_file": str(prompt_path.resolve()),
        "baseline_image": None if baseline_path is None else str(baseline_path.resolve()),
    }
    write_json(complete, complete_path)
    rank_print(rank, f"calibration sample {sample_index:05d}: 完成，耗时{elapsed:.2f}s")
    del controller, baseline, input_image
    return complete


def quantile(values: Sequence[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


def smooth_grid(grid: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return grid.copy()
    result = grid.copy()
    steps, layers = grid.shape
    for step in range(1, steps):
        for layer in range(1, layers - 1):
            s0, s1 = max(1, step - radius), min(steps, step + radius + 1)
            b0, b1 = max(1, layer - radius), min(layers - 1, layer + radius + 1)
            window = grid[s0:s1, b0:b1]
            finite = window[np.isfinite(window)]
            if finite.size:
                result[step, layer] = float(np.median(finite))
    return result


def save_calibration_plots(
    calibration_dir: Path,
    image_grid: np.ndarray,
    text_grid: np.ndarray,
    combined_grid: np.ndarray,
    risk_threshold: float,
    schedule: Sequence[Dict[str, Any]],
) -> List[str]:
    """保存残差风险和最终执行矩阵；matplotlib缺失时不影响主实验。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        print(f"[WARN] matplotlib不可用，跳过PNG图表：{error}", flush=True)
        return []

    calibration_dir.mkdir(parents=True, exist_ok=True)
    finite_image = image_grid[np.isfinite(image_grid)]
    finite_text = text_grid[np.isfinite(text_grid)]
    finite_combined = combined_grid[np.isfinite(combined_grid)]
    panels = [
        (image_grid[1:], "Image residual adjacent-step relative L2 (P quantile)", finite_image),
        (text_grid[1:], "Text residual adjacent-step relative L2 (P quantile)", finite_text),
        (combined_grid[1:], "Combined normalized risk", finite_combined),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(21, 7), constrained_layout=True)
    for axis, (grid, title, finite) in zip(axes, panels):
        vmax = float(np.quantile(finite, 0.98)) if finite.size else 1.0
        image = axis.imshow(grid, aspect="auto", origin="upper", cmap="magma", vmin=0.0, vmax=max(vmax, 1e-12))
        axis.set_title(title)
        axis.set_xlabel("Transformer block (1-based)")
        axis.set_ylabel("Current denoising step (2-based start)")
        axis.set_xticks(np.arange(0, grid.shape[1], 5), np.arange(1, grid.shape[1] + 1, 5))
        fig.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
    risk_path = calibration_dir / "residual_risk_heatmaps.png"
    fig.savefig(risk_path, dpi=180)
    plt.close(fig)

    total_layers = len(schedule[0]["executed_blocks_0based"])
    if total_layers != max(
        max(item["executed_blocks_0based"] + item["skipped_blocks_0based"])
        for item in schedule
    ) + 1:
        total_layers = max(
            max(item["executed_blocks_0based"] + item["skipped_blocks_0based"])
            for item in schedule
        ) + 1
    effective = np.zeros((len(schedule), total_layers), dtype=np.float64)
    base = np.zeros_like(effective)
    for step, item in enumerate(schedule):
        effective[step, item["executed_blocks_0based"]] = 1.0
        base[step, item["base_executed_blocks_0based"]] = 1.0
    fig, axes = plt.subplots(1, 2, figsize=(18, 8), constrained_layout=True)
    for axis, grid, title in (
        (axes[0], base, "Base blue-line mask (before cache-age refresh)"),
        (axes[1], effective, "Effective execution mask (after forced refresh)"),
    ):
        image = axis.imshow(grid, aspect="auto", origin="upper", cmap="viridis", vmin=0.0, vmax=1.0)
        axis.set_title(title)
        axis.set_xlabel("Transformer block (1-based)")
        axis.set_ylabel("Denoising step (1-based)")
        axis.set_xticks(np.arange(0, total_layers, 5), np.arange(1, total_layers + 1, 5))
        fig.colorbar(image, ax=axis, ticks=[0, 1], fraction=0.046, pad=0.04, label="0=cache, 1=execute")
    fig.suptitle(f"Blue-line cache schedule; normalized risk threshold={risk_threshold:.4f}")
    schedule_path = calibration_dir / "blue_line_schedule_heatmaps.png"
    fig.savefig(schedule_path, dpi=180)
    plt.close(fig)
    return [str(risk_path.resolve()), str(schedule_path.resolve())]


def aggregate_calibration_and_build_schedule(
    args: argparse.Namespace,
    output_dir: Path,
    calibration_samples_dir: Optional[Path] = None,   # 新增参数
) -> Dict[str, Any]:
    if calibration_samples_dir is None:
        calibration_samples_dir = output_dir / "calibration" / "samples"
        
    print(f"缓存目录 {calibration_samples_dir}")
    complete_paths = sorted(
        calibration_samples_dir.glob("[0-9][0-9][0-9][0-9][0-9]/complete.json")
    )
    valid = [
        path for path in complete_paths
        if read_json(path).get("strategy_version") == STRATEGY_VERSION
    ]
    expected_count = args.calibration_count
    if expected_count <= 0:   # 加载缓存模式，使用实际找到的样本数
        expected_count = len(valid)
        if expected_count == 0:
            raise RuntimeError("没有找到任何有效的校准样本。")
        print(f"从缓存目录找到 {expected_count} 个有效校准样本。")
    elif len(valid) != expected_count:
        raise RuntimeError(
            f"校准样本完成{len(valid)}/{expected_count}，不能生成蓝线表。"
        )
    all_rows: List[Dict[str, Any]] = []
    total_layers: Optional[int] = None
    for complete_path in valid:
        complete = read_json(complete_path)
        sample_layers = int(complete["total_layers"])
        if total_layers is not None and total_layers != sample_layers:
            raise RuntimeError(
                f"校准样本Block数不一致：{total_layers}和{sample_layers}。"
            )
        total_layers = sample_layers
        sample_rows = read_json_gz(Path(complete["profile_file"]))
        for profile_row in sample_rows:
            all_rows.append({"sample_index": complete["sample_index"], **profile_row})
    assert total_layers is not None
    num_steps = args.num_inference_steps
    fields = [
        "image_relative_l2",
        "image_cosine_similarity",
        "image_difference_rms",
        "image_previous_residual_rms",
        "image_current_residual_rms",
        "text_relative_l2",
        "text_cosine_similarity",
        "text_difference_rms",
        "text_previous_residual_rms",
        "text_current_residual_rms",
    ]
    grouped: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for row in all_rows:
        if int(row["step_index_0based"]) == 0:
            continue
        key = (int(row["step_index_0based"]), int(row["block_index_0based"]))
        grouped.setdefault(key, []).append(row)
    summary_rows: List[Dict[str, Any]] = []
    image_grid = np.full((num_steps, total_layers), np.nan, dtype=np.float64)
    text_grid = np.full_like(image_grid, np.nan)
    for step in range(1, num_steps):
        for layer in range(total_layers):
            rows = grouped.get((step, layer), [])
            if not rows:
                raise RuntimeError(f"缺少step={step + 1}, block={layer + 1}校准数据。")
            output: Dict[str, Any] = {
                "step_index_0based": step,
                "step_number_1based": step + 1,
                "block_index_0based": layer,
                "block_number_1based": layer + 1,
                "observation_count": len(rows),
            }
            for field in fields:
                values = [float(row[field]) for row in rows if row.get(field) is not None]
                output[f"{field}_mean"] = float(np.mean(values))
                output[f"{field}_median"] = float(np.median(values))
                output[f"{field}_std"] = float(np.std(values))
                output[f"{field}_q"] = quantile(values, args.profile_quantile)
            image_grid[step, layer] = output["image_relative_l2_q"]
            text_grid[step, layer] = output["text_relative_l2_q"]
            summary_rows.append(output)

    image_smooth = smooth_grid(image_grid, args.profile_smoothing_radius)
    text_smooth = smooth_grid(text_grid, args.profile_smoothing_radius)
    eligible = (slice(1, num_steps), slice(1, total_layers - 1))
    image_scale = float(np.nanmedian(image_smooth[eligible]))
    text_scale = float(np.nanmedian(text_smooth[eligible]))
    image_scale = max(image_scale, 1e-12)
    text_scale = max(text_scale, 1e-12)
    combined = np.maximum(image_smooth / image_scale, text_smooth / text_scale)
    risk_values = combined[eligible]
    risk_values = risk_values[np.isfinite(risk_values)]
    risk_threshold = quantile(risk_values.tolist(), args.target_cache_ratio)

    summary_lookup = {
        (int(row["step_index_0based"]), int(row["block_index_0based"])): row
        for row in summary_rows
    }
    for step in range(1, num_steps):
        for layer in range(total_layers):
            row = summary_lookup[(step, layer)]
            row["image_relative_l2_q_smoothed"] = float(image_smooth[step, layer])
            row["text_relative_l2_q_smoothed"] = float(text_smooth[step, layer])
            row["image_normalized_risk"] = float(image_smooth[step, layer] / image_scale)
            row["text_normalized_risk"] = float(text_smooth[step, layer] / text_scale)
            row["combined_normalized_risk"] = float(combined[step, layer])
            row["dominant_risk_modality"] = (
                "image"
                if row["image_normalized_risk"] >= row["text_normalized_risk"]
                else "text"
            )
            row["below_global_risk_threshold"] = bool(combined[step, layer] <= risk_threshold)

    ages = [0 for _ in range(total_layers)]
    schedule: List[Dict[str, Any]] = []
    for step in range(num_steps):
        force_full = (
            step < args.force_full_first_steps
            or (
                args.force_full_last_steps > 0
                and step >= num_steps - args.force_full_last_steps
            )
        )
        internal = list(range(1, total_layers - 1))
        if step == 0 or force_full:
            base_executed = set(range(total_layers))
            left_boundary = 1
            right_boundary = total_layers
            boundary_reason = "forced_full_step"
        else:
            unstable = [layer for layer in internal if combined[step, layer] > risk_threshold]
            if unstable:
                left = min(unstable)
                right = max(unstable)
                base_executed = {0, total_layers - 1, *range(left, right + 1)}
                left_boundary = left + 1
                right_boundary = right + 1
                boundary_reason = "continuous_interval_covering_all_high_risk_blocks"
            else:
                base_executed = {0, total_layers - 1}
                left_boundary = None
                right_boundary = None
                boundary_reason = "all_internal_blocks_below_threshold"
        base_skipped = set(range(total_layers)) - base_executed
        effective_executed = set(base_executed)
        forced_refresh: List[int] = []
        if args.max_cache_age > 0:
            for layer in sorted(base_skipped):
                if ages[layer] >= args.max_cache_age:
                    effective_executed.add(layer)
                    forced_refresh.append(layer)
        effective_skipped = set(range(total_layers)) - effective_executed
        for layer in range(total_layers):
            ages[layer] = 0 if layer in effective_executed else ages[layer] + 1
        schedule.append(
            {
                "step_index_0based": step,
                "step_number_1based": step + 1,
                "mode": "full_compute" if len(effective_executed) == total_layers else "blue_line_cache",
                "boundary_reason": boundary_reason,
                "blue_line_left_compute_boundary_1based": left_boundary,
                "blue_line_right_compute_boundary_1based": right_boundary,
                "base_executed_blocks_0based": sorted(base_executed),
                "base_skipped_blocks_0based": sorted(base_skipped),
                "forced_refresh_blocks_0based": forced_refresh,
                "executed_blocks_0based": sorted(effective_executed),
                "skipped_blocks_0based": sorted(effective_skipped),
                "executed_blocks_1based": one_based_layer_string(sorted(effective_executed)),
                "skipped_blocks_1based": one_based_layer_string(sorted(effective_skipped)),
                "forced_refresh_blocks_1based": one_based_layer_string(forced_refresh),
                "executed_block_count": len(effective_executed),
                "skipped_block_count": len(effective_skipped),
                "max_cache_age_after_step": max(ages),
            }
        )

    total_full_blocks = num_steps * total_layers
    executed_blocks = sum(int(item["executed_block_count"]) for item in schedule)
    base_executed_blocks = sum(len(item["base_executed_blocks_0based"]) for item in schedule)
    payload = {
        "strategy_version": STRATEGY_VERSION,
        "experiment_signature": experiment_signature(args),
        "calibration_sample_count": expected_count, 
        "profile_quantile": args.profile_quantile,
        "target_cache_ratio_before_contiguous_constraint": args.target_cache_ratio,
        "profile_smoothing_radius": args.profile_smoothing_radius,
        "mask_rule": "max(image_q/median_image_q, text_q/median_text_q)",
        "image_normalization_scale": image_scale,
        "text_normalization_scale": text_scale,
        "combined_risk_threshold": risk_threshold,
        "max_cache_age": args.max_cache_age,
        "total_layers": total_layers,
        "num_inference_steps": num_steps,
        "total_full_block_forwards": total_full_blocks,
        "base_blue_line_executed_block_forwards": base_executed_blocks,
        "base_blue_line_cache_fraction": 1.0 - base_executed_blocks / total_full_blocks,
        "effective_executed_block_forwards": executed_blocks,
        "effective_skipped_block_forwards": total_full_blocks - executed_blocks,
        "effective_executed_block_fraction": executed_blocks / total_full_blocks,
        "effective_cache_fraction": 1.0 - executed_blocks / total_full_blocks,
        "theoretical_block_speedup": total_full_blocks / executed_blocks,
        "schedule": schedule,
    }
    calibration_dir = output_dir / "calibration"
    payload["plot_files"] = save_calibration_plots(
        calibration_dir=calibration_dir,
        image_grid=image_smooth,
        text_grid=text_smooth,
        combined_grid=combined,
        risk_threshold=risk_threshold,
        schedule=schedule,
    )
    write_csv(summary_rows, calibration_dir / "profile_cell_summary.csv")
    write_json(payload, calibration_dir / "blue_line_schedule.json")

    matrix_rows: List[Dict[str, Any]] = []
    for item in schedule:
        executed = set(item["executed_blocks_0based"])
        base_executed = set(item["base_executed_blocks_0based"])
        row: Dict[str, Any] = {
            "step_number_1based": item["step_number_1based"],
            "left_compute_boundary_1based": item["blue_line_left_compute_boundary_1based"],
            "right_compute_boundary_1based": item["blue_line_right_compute_boundary_1based"],
            "executed_block_count": item["executed_block_count"],
            "skipped_block_count": item["skipped_block_count"],
        }
        for layer in range(total_layers):
            row[f"block_{layer + 1:03d}"] = 1 if layer in executed else 0
            row[f"base_block_{layer + 1:03d}"] = 1 if layer in base_executed else 0
        matrix_rows.append(row)
    write_csv(matrix_rows, calibration_dir / "blue_line_schedule_matrix.csv")
    return payload


def aggregate_branch_steps(
    rows: Sequence[Dict[str, Any]],
    schedule: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["step_index_0based"]), []).append(row)
    result: List[Dict[str, Any]] = []
    metric_fields = [
        "noise_relative_mse",
        "image_token_relative_mse",
        "text_token_relative_mse",
        "score",
    ]
    for step in sorted(grouped):
        branch_rows = grouped[step]
        item = schedule[step]
        output: Dict[str, Any] = {
            "step_index_0based": step,
            "step_number_1based": step + 1,
            "branch_count": len(branch_rows),
            "executed_block_count": item["executed_block_count"],
            "skipped_block_count": item["skipped_block_count"],
            "executed_blocks_1based": item["executed_blocks_1based"],
            "skipped_blocks_1based": item["skipped_blocks_1based"],
            "forced_refresh_blocks_1based": item["forced_refresh_blocks_1based"],
            "left_compute_boundary_1based": item["blue_line_left_compute_boundary_1based"],
            "right_compute_boundary_1based": item["blue_line_right_compute_boundary_1based"],
            "max_cache_age_after_step": item["max_cache_age_after_step"],
        }
        for field in metric_fields:
            output[field] = float(np.mean([float(row[field]) for row in branch_rows]))
        result.append(output)
    return result

def process_validation_sample(
    pipe,
    row: Dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
    schedule_payload: Dict[str, Any],
    rank: int,
) -> Dict[str, Any]:
    sample_index = int(row["sample_index"])
    sample_dir = output_dir / "validation" / "samples" / f"{sample_index:05d}"
    complete_path = sample_dir / "complete.json"
    signature = experiment_signature(args)
    if args.resume and complete_path.exists():
        complete = read_json(complete_path)
        if complete.get("experiment_signature") == signature:
            return complete
    sample_dir.mkdir(parents=True, exist_ok=True)

    sample_args = make_sample_args(args, row)
    input_image = load_rgb(Path(row["image_path"]))

    # 保存输入图像和 prompt
    input_image_path, prompt_path = save_input_artifact(
        sample_dir, input_image, sample_args.prompt, args
    )

    transformer = pipe.transformer
    blocks = list(transformer.transformer_blocks)
    forwards_per_step = infer_forwards_per_step(sample_args)
    schedule = {
        int(item["step_index_0based"]): item for item in schedule_payload["schedule"]
    }

    teacher = FullReferenceController(
        transformer_blocks=blocks,
        original_transformer_forward=transformer.forward,
        args=sample_args,
        forwards_per_step=forwards_per_step,
    )
    rank_print(rank, f"validation sample {sample_index:05d}: 完整基线开始")
    torch.cuda.synchronize(torch.device(sample_args.device))
    start = time.perf_counter()
    with replace_transformer_forward(transformer, teacher):
        baseline = generate_image(pipe, [input_image], sample_args)
    torch.cuda.synchronize(torch.device(sample_args.device))
    baseline_elapsed = time.perf_counter() - start
    teacher.validate_complete()

    cached_controller = BlueLineScheduledController(
        transformer_blocks=blocks,
        original_transformer_forward=transformer.forward,
        schedule=schedule,
        teacher_references=teacher.references,
        args=sample_args,
        forwards_per_step=forwards_per_step,
    )
    rank_print(rank, f"validation sample {sample_index:05d}: 蓝线缓存轨迹开始")
    torch.cuda.synchronize(torch.device(sample_args.device))
    start = time.perf_counter()
    with replace_transformer_forward(transformer, cached_controller):
        cached = generate_image(pipe, [input_image], sample_args)
    torch.cuda.synchronize(torch.device(sample_args.device))
    cached_elapsed = time.perf_counter() - start
    cached_controller.validate_complete()

    baseline_path = sample_dir / f"baseline_full{image_suffix(args)}"
    cached_path = sample_dir / f"blue_line_cached{image_suffix(args)}"
    save_image(baseline, baseline_path, args)
    save_image(cached, cached_path, args)
    final_metrics = image_metrics(baseline, cached)
    step_rows = aggregate_branch_steps(cached_controller.branch_step_rows, schedule)
    write_csv(step_rows, sample_dir / "step_metrics.csv")
    write_csv(cached_controller.block_action_rows, sample_dir / "block_actions.csv.gz")
    write_json(
        {
            "strategy_version": STRATEGY_VERSION,
            "schedule_source": str((output_dir / "calibration" / "blue_line_schedule.json").resolve()),
            "schedule": schedule_payload["schedule"],
        },
        sample_dir / "schedule_used.json",
    )
    measured_speedup = baseline_elapsed / cached_elapsed if cached_elapsed > 0 else None
    metrics_payload = {
        **final_metrics,
        "baseline_elapsed_seconds_instrumented": baseline_elapsed,
        "cached_elapsed_seconds_instrumented": cached_elapsed,
        "measured_instrumented_speedup": measured_speedup,
        "theoretical_block_speedup": schedule_payload["theoretical_block_speedup"],
        "executed_block_fraction": schedule_payload["effective_executed_block_fraction"],
        "note": "实测时间包含逐step/逐block统计开销，理论Block加速用于公平比较。",
    }
    write_json(metrics_payload, sample_dir / "final_image_metrics.json")
    complete = {
        **row,
        "strategy_version": STRATEGY_VERSION,
        "experiment_signature": signature,
        "rank": rank,
        "input_image_file": str(input_image_path.resolve()),
        "prompt_file": str(prompt_path.resolve()),
        "baseline_image": str(baseline_path.resolve()),
        "cached_image": str(cached_path.resolve()),
        "step_metrics_file": str((sample_dir / "step_metrics.csv").resolve()),
        "block_actions_file": str((sample_dir / "block_actions.csv.gz").resolve()),
        "final_image_metrics_file": str((sample_dir / "final_image_metrics.json").resolve()),
        "final_image_metrics": metrics_payload,
    }
    write_json(complete, complete_path)
    rank_print(
        rank,
        f"validation sample {sample_index:05d}: 完成；MSE={final_metrics['mse']:.8f}，"
        f"PSNR={final_metrics['psnr']:.3f}，理论Block加速="
        f"{schedule_payload['theoretical_block_speedup']:.3f}x",
    )
    del teacher, cached_controller, baseline, cached, input_image
    return complete

def merge_validation_summaries(
    args: argparse.Namespace,
    output_dir: Path,
    schedule_payload: Dict[str, Any],
) -> None:
    paths = sorted(
        (output_dir / "validation" / "samples").glob("[0-9][0-9][0-9][0-9][0-9]/complete.json")
    )
    completed: List[Dict[str, Any]] = []
    for path in paths:
        item = read_json(path)
        if item.get("strategy_version") == STRATEGY_VERSION:
            completed.append(item)
    summary_rows: List[Dict[str, Any]] = []
    all_step_rows: List[Dict[str, Any]] = []
    all_action_rows: List[Dict[str, Any]] = []
    for item in completed:
        metrics = item["final_image_metrics"]
        summary_rows.append(
            {
                "sample_index": item["sample_index"],
                "split_index": item["split_index"],
                "image_relative_path": item["image_relative_path"],
                "prompt_id": item["prompt_id"],
                **metrics,
                "baseline_image": item["baseline_image"],
                "cached_image": item["cached_image"],
            }
        )
        with Path(item["step_metrics_file"]).open("r", encoding="utf-8-sig", newline="") as input_file:
            for row in csv.DictReader(input_file):
                all_step_rows.append({"sample_index": item["sample_index"], **row})
        with gzip.open(item["block_actions_file"], "rt", encoding="utf-8-sig", newline="") as input_file:
            for row in csv.DictReader(input_file):
                all_action_rows.append({"sample_index": item["sample_index"], **row})
    validation_dir = output_dir / "validation"
    write_csv(summary_rows, validation_dir / "validation_summary.csv")

    step_groups: Dict[int, List[Dict[str, Any]]] = {}
    for row in all_step_rows:
        step_groups.setdefault(int(row["step_index_0based"]), []).append(row)
    step_summary: List[Dict[str, Any]] = []
    for step in range(args.num_inference_steps):
        rows = step_groups.get(step, [])
        if not rows:
            continue
        output: Dict[str, Any] = {
            "step_index_0based": step,
            "step_number_1based": step + 1,
            "sample_count": len(rows),
            "executed_block_count": int(rows[0]["executed_block_count"]),
            "skipped_block_count": int(rows[0]["skipped_block_count"]),
            "executed_blocks_1based": rows[0]["executed_blocks_1based"],
            "skipped_blocks_1based": rows[0]["skipped_blocks_1based"],
        }
        for field in ("noise_relative_mse", "image_token_relative_mse", "text_token_relative_mse", "score"):
            values = np.asarray([float(row[field]) for row in rows], dtype=np.float64)
            output[f"{field}_mean"] = float(values.mean())
            output[f"{field}_median"] = float(np.median(values))
            output[f"{field}_std"] = float(values.std())
            output[f"{field}_p90"] = float(np.quantile(values, 0.9))
        step_summary.append(output)
    write_csv(step_summary, validation_dir / "step_metrics_summary.csv")

    action_groups: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for row in all_action_rows:
        key = (int(row["step_index_0based"]), int(row["block_index_0based"]))
        action_groups.setdefault(key, []).append(row)
    action_frequency: List[Dict[str, Any]] = []
    for step in range(args.num_inference_steps):
        for layer in range(int(schedule_payload["total_layers"])):
            rows = action_groups.get((step, layer), [])
            if not rows:
                continue
            cache_count = sum(row["action"] == "cache" for row in rows)
            ages = [int(row["cache_age"]) for row in rows]
            action_frequency.append(
                {
                    "step_number_1based": step + 1,
                    "block_number_1based": layer + 1,
                    "observation_count": len(rows),
                    "cache_count": cache_count,
                    "execute_count": len(rows) - cache_count,
                    "cache_frequency": cache_count / len(rows),
                    "mean_cache_age": float(np.mean(ages)),
                    "max_cache_age": max(ages),
                }
            )
    write_csv(action_frequency, validation_dir / "block_action_frequency.csv")

    aggregate_metrics: Dict[str, Any] = {}
    for field in ("mse", "mae", "rmse", "psnr", "changed_ratio", "measured_instrumented_speedup"):
        values = [float(row[field]) for row in summary_rows if row.get(field) is not None]
        if values:
            aggregate_metrics[field] = {
                "mean": float(np.mean(values)),
                "median": float(np.median(values)),
                "std": float(np.std(values)),
                "p90": float(np.quantile(values, 0.9)),
            }
    write_json(
        {
            "strategy_version": STRATEGY_VERSION,
            "expected_validation_samples": args.validation_count,
            "completed_validation_samples": len(completed),
            "theoretical_block_speedup": schedule_payload["theoretical_block_speedup"],
            "effective_executed_block_fraction": schedule_payload["effective_executed_block_fraction"],
            "aggregate_final_image_metrics": aggregate_metrics,
            "validation_summary": str((validation_dir / "validation_summary.csv").resolve()),
            "step_metrics_summary": str((validation_dir / "step_metrics_summary.csv").resolve()),
            "block_action_frequency": str((validation_dir / "block_action_frequency.csv").resolve()),
        },
        output_dir / "progress.json",
    )


@record
def main() -> None:
    args = parse_args()
    validate_args(args)
    rank, local_rank, world_size = initialize_distributed(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts, negative_prompt = parse_prompt_markdown(
        Path(args.prompt_file), args.prompt_language, args.include_viton_prompts
    )
    manifest = build_or_load_manifest(
        args, prompts, negative_prompt, output_dir, rank, world_size
    )
    if rank == 0:
        write_json(
            {
                **vars(args),
                "strategy_version": STRATEGY_VERSION,
                "world_size": world_size,
                "parsed_prompt_count": len(prompts),
                "workflow": [
                    "calibration_full_profile",
                    "build_static_blue_line_schedule",
                    "validation_full_vs_cached",
                ],
            },
            output_dir / "run_config.json",
        )
        rank_print(
            rank,
            f"固定manifest={len(manifest)}条：calibration={args.calibration_count}，"
            f"validation={args.validation_count}。",
        )
    barrier(world_size)
    if args.prepare_only:
        cleanup_distributed(world_size)
        return

    pipe = load_pipeline(args)
    pipe.vae.enable_tiling()
    if args.load_calibration_cache:
        rank_print(rank, f">>> 进入缓存加载模式，缓存路径：{args.load_calibration_cache}")
        # 跳过样本生成，直接从外部缓存构建 schedule
        calibration_samples_dir = Path(args.load_calibration_cache) / "calibration" / "samples"
        barrier(world_size)
        if rank == 0:
            schedule_payload = aggregate_calibration_and_build_schedule(
                args, output_dir, calibration_samples_dir=calibration_samples_dir
            )
        barrier(world_size)
        # 所有 rank 统一读取 schedule（保证多卡同步）
        schedule_payload = read_json(output_dir / "calibration" / "blue_line_schedule.json")
    else:
        calibration_rows = [
            row for row in manifest
            if row["split"] == "calibration" and int(row["split_index"]) % world_size == rank
        ]
        rank_print(rank, f"cuda:{local_rank}分到{len(calibration_rows)}个校准样本。")
        for local_index, row in enumerate(calibration_rows, start=1):
            rank_print(rank, f"校准进度[{local_index}/{len(calibration_rows)}]")
            try:
                process_calibration_sample(pipe, row, args, output_dir, rank)
            except Exception as error:
                append_error(output_dir, rank, row, error)
                rank_print(rank, f"校准sample失败：{type(error).__name__}: {error}")
                if args.fail_fast:
                    raise
            finally:
                gc.collect()
                torch.cuda.empty_cache()
        barrier(world_size)
        if rank == 0:
            rank_print(
                rank,
                f"{args.calibration_count}组校准轨迹完成，开始汇总并生成蓝线schedule。",
            )
            schedule_payload = aggregate_calibration_and_build_schedule(args, output_dir)
            rank_print(
                rank,
                f"蓝线schedule生成完成：执行比例="
                f"{schedule_payload['effective_executed_block_fraction']:.4f}，"
                f"理论Block加速={schedule_payload['theoretical_block_speedup']:.3f}x。",
            )
            for item in schedule_payload["schedule"]:
                rank_print(
                    rank,
                    f"蓝线step={item['step_number_1based']:02d}/{args.num_inference_steps}："
                    f"执行{item['executed_block_count']}层，"
                    f"跳过{item['skipped_block_count']}层；"
                    f"跳过=[{item['skipped_blocks_1based']}]；"
                    f"到期刷新=[{item['forced_refresh_blocks_1based']}]",
                )
        barrier(world_size)
        schedule_payload = read_json(output_dir / "calibration" / "blue_line_schedule.json")

    validation_rows = [
        row for row in manifest
        if row["split"] == "validation" and int(row["split_index"]) % world_size == rank
    ]
    rank_print(rank, f"cuda:{local_rank}分到{len(validation_rows)}个验证样本。")
    for local_index, row in enumerate(validation_rows, start=1):
        rank_print(rank, f"验证进度[{local_index}/{len(validation_rows)}]")
        try:
            process_validation_sample(
                pipe, row, args, output_dir, schedule_payload, rank
            )
        except Exception as error:
            append_error(output_dir, rank, row, error)
            rank_print(rank, f"验证sample失败：{type(error).__name__}: {error}")
            if args.fail_fast:
                raise
        finally:
            gc.collect()
            torch.cuda.empty_cache()
    del pipe
    gc.collect()
    torch.cuda.empty_cache()
    barrier(world_size)
    if rank == 0:
        merge_validation_summaries(args, output_dir, schedule_payload)
        rank_print(rank, f"蓝线缓存实验完成：{output_dir}")
    barrier(world_size)
    cleanup_distributed(world_size)


if __name__ == "__main__":
    main()