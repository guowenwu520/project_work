#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在约 7 万张人像中固定抽取 1 万张，批量测试 Qwen-Image-Edit-2511
连续中间 Block 窗口长度 5..55。

依赖同目录下：
    qwen_edit_diagonal_bridge_search.py

每张图片的流程：
1. 从 Markdown 提示词集合中随机选择一条正向英文人像编辑提示词。
2. 运行一次完整模型，保存 baseline。
3. 第一个 timestep 完整执行全部 Block，并保存每一层输出缓存。
4. 从第二个 timestep 开始测试窗口长度 5..55 的所有合法连续位置；未执行
   Block 读取上一个 timestep 同编号 Block 的 text/image 输出。
5. 保存每个候选的执行层、缓存层、token误差、噪声误差和综合误差。
6. 对每个窗口长度分别选择逐 timestep 最优连续位置。
7. 按每种窗口长度的最优序列各生成一张最终图片并保存。
8. 额外运行一张允许不同 timestep 选择不同窗口长度的全局最优组合结果。

支持：
- 固定 manifest，保证恢复运行时样本、提示词、seed 不变；
- 单 GPU 或 torchrun 多 GPU 数据并行；
- 每张样本、每种窗口长度单独断点续跑；
- gzip 压缩候选 CSV/JSON，降低 1 万样本统计文件体积。
"""

from __future__ import annotations

import argparse
import copy
import csv
import gc
import gzip
import json
import os
import random
import re
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import torch
import torch.distributed as dist
from torch.distributed.elastic.multiprocessing.errors import record
from PIL import Image

from qwen_edit_diagonal_bridge_search import (
    CACHE_STRATEGY_VERSION,
    ScheduledController,
    SearchController,
    build_candidate_windows,
    executed_layers_for_window,
    generate_image,
    image_metrics,
    infer_forwards_per_step,
    load_pipeline,
    one_based_layer_string,
    replace_transformer_forward,
    skipped_layers_for_window,
)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
STRATEGY_VERSION = CACHE_STRATEGY_VERSION
DEFAULT_PROMPT_GROUPS = {
    "FFHQ 主训练",
    "FFHQ 额外评估",
    "早期 10 prompt 评估",
    "生活场景评估",
    "姿态背景评估",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抽取1万人像，测试连续Block窗口长度5到55。"
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default="/data4/guowenwu/MMDITModelCompression/models/Qwen-Image-Edit-2511",
    )
    parser.add_argument(
        "--dataset-root",
        type=str,
        default="/data4/guowenwu/MMDITModelCompression/dataset/images1024x1024",
        help="包含00000、01000等子目录的人像数据集根目录。",
    )
    parser.add_argument(
        "--prompt-file",
        type=str,
        default="/data4/guowenwu/MMDITModelCompression/portrait_prompts.md",
        help="用户提供的Markdown提示词集合。",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=10000,
        help="固定随机抽取的图片数量。",
    )
    parser.add_argument(
        "--sampling-seed",
        type=int,
        default=20260724,
        help="控制数据抽样和提示词分配；manifest生成后不再变化。",
    )
    parser.add_argument(
        "--generation-seed",
        type=int,
        default=0,
        help="第i个样本使用generation_seed+i。",
    )
    parser.add_argument("--window-size-min", type=int, default=5)
    parser.add_argument("--window-size-max", type=int, default=55)
    parser.add_argument(
        "--window-stride",
        type=int,
        default=1,
        help="每种窗口长度在Block 2-59中的滑动步长。",
    )
    parser.add_argument("--num-inference-steps", type=int, default=4)
    parser.add_argument("--true-cfg-scale", type=float, default=1.0)
    parser.add_argument("--guidance-scale", type=float, default=1.0)
    parser.add_argument(
        "--forwards-per-step",
        type=int,
        choices=[1, 2],
        default=None,
    )
    parser.add_argument("--noise-weight", type=float, default=1.0)
    parser.add_argument("--image-token-weight", type=float, default=1.0)
    parser.add_argument("--text-token-weight", type=float, default=0.25)
    parser.add_argument("--dtype", choices=["bf16", "fp16"], default="bf16")
    parser.add_argument(
        "--device",
        type=str,
        default="cuda:0",
        help="普通python运行时使用；torchrun会自动改为当前LOCAL_RANK。",
    )
    parser.add_argument("--cpu-offload", action="store_true")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument(
        "--output-dir",
        type=str,
        default="/data4/guowenwu/MMDITModelCompression/outputs/window_sweep_10k",
    )
    parser.add_argument(
        "--image-format",
        choices=["png", "jpg", "webp"],
        default="png",
        help="png无损但1万×52张结果会占用大量磁盘。",
    )
    parser.add_argument(
        "--image-quality",
        type=int,
        default=95,
        help="仅jpg/webp使用。",
    )
    parser.add_argument(
        "--include-viton-prompts",
        action="store_true",
        help="默认排除需要服装参考图的试衣提示词。",
    )
    parser.add_argument(
        "--prompt-language",
        choices=["english", "chinese"],
        default="english",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="忽略已有单样本结果并重新测试。",
    )
    parser.set_defaults(resume=True)
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="任意样本失败时立即停止；默认记录错误后继续。",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只扫描数据并生成固定manifest，不加载模型。",
    )
    parser.add_argument(
        "--limit-local-samples",
        type=int,
        default=None,
        help="每个rank最多处理多少张；建议先设1或10做冒烟测试。",
    )
    parser.add_argument("--show-progress", action="store_true")
    parser.add_argument("--verbose-candidates", action="store_true")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help=(
            "搜索时每完成多少个候选打印一次简洁进度、耗时和ETA；"
            "默认25，设为0只保留step开始/完成日志。"
        ),
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.sample_count <= 0:
        raise ValueError("--sample-count必须大于0。")
    if args.window_size_min <= 0:
        raise ValueError("--window-size-min必须大于0。")
    if args.window_size_min > args.window_size_max:
        raise ValueError("--window-size-min不能大于--window-size-max。")
    if args.window_stride <= 0:
        raise ValueError("--window-stride必须大于0。")
    if args.progress_every < 0:
        raise ValueError("--progress-every不能小于0。")
    if args.num_inference_steps <= 0:
        raise ValueError("--num-inference-steps必须大于0。")
    if not (1 <= args.image_quality <= 100):
        raise ValueError("--image-quality必须位于1到100。")
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
        dist.init_process_group(backend="nccl")
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


def parse_prompt_markdown(
    prompt_path: Path,
    language: str,
    include_viton: bool,
) -> Tuple[List[Dict[str, str]], str]:
    text = prompt_path.read_text(encoding="utf-8")
    group_pattern = re.compile(
        r"^##\s+(.+?)\s*$([\s\S]*?)(?=^##\s+|\Z)",
        flags=re.MULTILINE,
    )
    entry_pattern = re.compile(
        r"^###\s+(.+?)\s*$([\s\S]*?)(?=^###\s+|\Z)",
        flags=re.MULTILINE,
    )
    label = "英文" if language == "english" else "中文"
    prompts: List[Dict[str, str]] = []
    negative_entries: Dict[str, str] = {}

    for group_match in group_pattern.finditer(text):
        group_name = group_match.group(1).strip()
        group_body = group_match.group(2)
        for entry_match in entry_pattern.finditer(group_body):
            prompt_id = entry_match.group(1).strip()
            entry_body = entry_match.group(2)
            value_match = re.search(
                rf"{label}：\s*```text\s*([\s\S]*?)\s*```",
                entry_body,
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
                {
                    "group": group_name,
                    "prompt_id": prompt_id,
                    "prompt": prompt_text,
                }
            )

    if not prompts:
        raise ValueError(f"没有从{prompt_path}解析出可用正向提示词。")

    negative_parts = [
        negative_entries[key]
        for key in ("ffhq_negative", "ffhq_negative_occlusion")
        if key in negative_entries
    ]
    if include_viton and "viton_negative" in negative_entries:
        negative_parts.append(negative_entries["viton_negative"])
    negative_prompt = ", ".join(part for part in negative_parts if part)
    if not negative_prompt:
        negative_prompt = " "
    return prompts, negative_prompt


def scan_images(dataset_root: Path) -> List[Path]:
    image_paths = [
        path
        for path in dataset_root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    image_paths.sort(key=lambda path: path.as_posix())
    return image_paths


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

    if rank == 0 and manifest_path.exists():
        if not metadata_path.exists():
            raise FileNotFoundError(
                f"已有manifest但缺少{metadata_path}，无法验证是否可安全续跑。"
            )
        old_metadata = read_json(metadata_path)
        expected = {
            "dataset_root": str(Path(args.dataset_root).resolve()),
            "sample_count": args.sample_count,
            "sampling_seed": args.sampling_seed,
            "generation_seed": args.generation_seed,
            "prompt_file": str(Path(args.prompt_file).resolve()),
            "prompt_language": args.prompt_language,
            "include_viton_prompts": args.include_viton_prompts,
        }
        mismatched = [
            key for key, value in expected.items() if old_metadata.get(key) != value
        ]
        if mismatched:
            raise ValueError(
                f"现有manifest与本次参数不一致：{mismatched}。"
                "请恢复原参数，或更换--output-dir生成新manifest。"
            )

    if rank == 0 and not manifest_path.exists():
        dataset_root = Path(args.dataset_root).resolve()
        image_paths = scan_images(dataset_root)
        if len(image_paths) < args.sample_count:
            raise ValueError(
                f"数据集只找到{len(image_paths)}张图片，少于要求的"
                f"{args.sample_count}张。"
            )
        rng = random.Random(args.sampling_seed)
        selected_paths = rng.sample(image_paths, args.sample_count)
        manifest_rows: List[Dict[str, Any]] = []
        for sample_index, image_path in enumerate(selected_paths):
            prompt_item = prompts[rng.randrange(len(prompts))]
            manifest_rows.append(
                {
                    "sample_index": sample_index,
                    "image_path": str(image_path),
                    "image_relative_path": str(image_path.relative_to(dataset_root)),
                    "prompt_group": prompt_item["group"],
                    "prompt_id": prompt_item["prompt_id"],
                    "prompt": prompt_item["prompt"],
                    "negative_prompt": negative_prompt,
                    "generation_seed": args.generation_seed + sample_index,
                }
            )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8") as manifest_file:
            for row in manifest_rows:
                manifest_file.write(json.dumps(row, ensure_ascii=False) + "\n")
        temporary.replace(manifest_path)
        write_json(
            {
                "dataset_root": str(dataset_root),
                "total_discovered_images": len(image_paths),
                "sample_count": args.sample_count,
                "sampling_seed": args.sampling_seed,
                "generation_seed": args.generation_seed,
                "prompt_file": str(Path(args.prompt_file).resolve()),
                "prompt_count": len(prompts),
                "prompt_language": args.prompt_language,
                "include_viton_prompts": args.include_viton_prompts,
            },
            metadata_path,
        )

    barrier(world_size)
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest没有成功创建：{manifest_path}")
    rows: List[Dict[str, Any]] = []
    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        for line in manifest_file:
            if line.strip():
                rows.append(json.loads(line))
    if len(rows) != args.sample_count:
        raise ValueError(
            f"现有manifest包含{len(rows)}条，但本次--sample-count="
            f"{args.sample_count}。请使用匹配参数或换一个output-dir。"
        )
    return rows


def build_all_candidates(
    total_layers: int,
    window_sizes: Sequence[int],
    stride: int,
) -> List[Tuple[int, int]]:
    candidates: List[Tuple[int, int]] = []
    for window_size in window_sizes:
        candidates.extend(
            build_candidate_windows(
                total_layers=total_layers,
                window_size=window_size,
                stride=stride,
            )
        )
    return candidates


def derive_schedules(
    aggregate_rows: Sequence[Dict[str, Any]],
    window_sizes: Sequence[int],
    num_steps: int,
    total_layers: int,
) -> Tuple[Dict[int, Dict[int, Dict[str, Any]]], Dict[int, Dict[str, Any]]]:
    """
    返回：
    - schedules_by_size[window_size][step]；
    - global_schedule[step]，允许每个step选择不同窗口长度。
    """
    rows_by_size_step: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    rows_by_step: Dict[int, List[Dict[str, Any]]] = {}
    for row in aggregate_rows:
        window_size = int(row["window_size"])
        step_index = int(row["step_index_0based"])
        if step_index == 0:
            raise RuntimeError(
                "新策略的第一个timestep不应包含窗口候选；"
                "请勿复用旧策略的search_state。"
            )
        rows_by_size_step.setdefault((window_size, step_index), []).append(row)
        rows_by_step.setdefault(step_index, []).append(row)

    def full_first_step(requested_window_size: Optional[int]) -> Dict[str, Any]:
        executed = list(range(total_layers))
        return {
            "step_index_0based": 0,
            "step_number_1based": 1,
            "window_start_0based": None,
            "window_end_0based": None,
            "window_start_1based": None,
            "window_end_1based": None,
            "window_size": total_layers,
            "requested_window_size": requested_window_size,
            "executed_block_count": total_layers,
            "skipped_block_count": 0,
            "executed_blocks_1based": one_based_layer_string(executed),
            "skipped_blocks_1based": "",
            "noise_relative_mse": 0.0,
            "image_token_relative_mse": 0.0,
            "text_token_relative_mse": 0.0,
            "score": 0.0,
            "selected": True,
            "mode": "full_compute",
            "cache_source": "none_first_timestep",
        }

    schedules_by_size: Dict[int, Dict[int, Dict[str, Any]]] = {}
    for window_size in window_sizes:
        step_schedule: Dict[int, Dict[str, Any]] = {
            0: {
                **full_first_step(window_size),
                "selected_for_window_size": True,
            }
        }
        for step_index in range(1, num_steps):
            candidates = rows_by_size_step.get((window_size, step_index), [])
            if not candidates:
                raise RuntimeError(
                    f"window_size={window_size}, step={step_index}没有候选结果。"
                )
            best = min(
                candidates,
                key=lambda row: (
                    float(row["score"]),
                    int(row["window_start_0based"]),
                ),
            )
            step_schedule[step_index] = {
                **copy.deepcopy(best),
                "selected_for_window_size": True,
                "mode": "previous_timestep_same_block_cache",
                "search_cache_source": "previous_teacher_timestep_same_block",
                "cache_source": "previous_scheduled_timestep_same_block",
            }
        schedules_by_size[window_size] = step_schedule

    global_schedule: Dict[int, Dict[str, Any]] = {
        0: {
            **full_first_step(None),
            "selected_global": True,
        }
    }
    for step_index in range(1, num_steps):
        candidates = rows_by_step.get(step_index, [])
        if not candidates:
            raise RuntimeError(f"step={step_index}没有全局候选结果。")
        best = min(
            candidates,
            key=lambda row: (
                float(row["score"]),
                -int(row["window_size"]),
                int(row["window_start_0based"]),
            ),
        )
        global_schedule[step_index] = {
            **copy.deepcopy(best),
            "selected_global": True,
            "mode": "previous_timestep_same_block_cache",
            "search_cache_source": "previous_teacher_timestep_same_block",
            "cache_source": "previous_scheduled_timestep_same_block",
        }
    return schedules_by_size, global_schedule


def annotate_candidate_rows(
    aggregate_rows: Sequence[Dict[str, Any]],
    schedules_by_size: Dict[int, Dict[int, Dict[str, Any]]],
    global_schedule: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    selected_by_size = {
        (
            window_size,
            step_index,
            int(item["window_start_0based"]),
            int(item["window_end_0based"]),
        )
        for window_size, schedule in schedules_by_size.items()
        for step_index, item in schedule.items()
        if item.get("mode") != "full_compute"
    }
    selected_global = {
        (
            step_index,
            int(item["window_start_0based"]),
            int(item["window_end_0based"]),
        )
        for step_index, item in global_schedule.items()
        if item.get("mode") != "full_compute"
    }
    annotated: List[Dict[str, Any]] = []
    for source in aggregate_rows:
        row = copy.deepcopy(source)
        window_size = int(row["window_size"])
        step_index = int(row["step_index_0based"])
        start = int(row["window_start_0based"])
        end = int(row["window_end_0based"])
        row["selected_for_window_size"] = (
            window_size,
            step_index,
            start,
            end,
        ) in selected_by_size
        row["selected_global"] = (step_index, start, end) in selected_global
        annotated.append(row)
    return annotated


def write_candidate_scores_gz(
    rows: Sequence[Dict[str, Any]],
    output_path: Path,
) -> None:
    fieldnames = [
        "mode",
        "cache_source",
        "step_number_1based",
        "window_size",
        "window_start_1based",
        "window_end_1based",
        "executed_block_count",
        "skipped_block_count",
        "executed_blocks_1based",
        "skipped_blocks_1based",
        "noise_relative_mse",
        "image_token_relative_mse",
        "text_token_relative_mse",
        "score",
        "selected_for_window_size",
        "selected_global",
    ]
    with gzip.open(output_path, "wt", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def write_candidate_matrix_gz(
    rows: Sequence[Dict[str, Any]],
    total_layers: int,
    output_path: Path,
) -> None:
    block_fields = [f"block_{layer + 1:03d}" for layer in range(total_layers)]
    fieldnames = [
        "mode",
        "cache_source",
        "step_number_1based",
        "window_size",
        "window_start_1based",
        "window_end_1based",
        "score",
        "selected_for_window_size",
        "selected_global",
        *block_fields,
    ]
    with gzip.open(output_path, "wt", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for source in rows:
            window = (
                int(source["window_start_0based"]),
                int(source["window_end_0based"]),
            )
            executed = executed_layers_for_window(total_layers, window)
            row: Dict[str, Any] = {
                field: source.get(field)
                for field in fieldnames
                if not field.startswith("block_")
            }
            for layer in range(total_layers):
                row[f"block_{layer + 1:03d}"] = 1 if layer in executed else 0
            writer.writerow(row)


def write_best_schedule_matrix_gz(
    schedules_by_size: Dict[int, Dict[int, Dict[str, Any]]],
    global_schedule: Dict[int, Dict[str, Any]],
    total_layers: int,
    num_steps: int,
    output_path: Path,
) -> None:
    """
    每种窗口长度及全局混合路径各输出一组逐step执行矩阵。

    Block列中：1=当前timestep实际执行，0=读取上一timestep同编号Block缓存。
    第一个timestep全部为1。
    """
    block_fields = [f"block_{layer + 1:03d}" for layer in range(total_layers)]
    fieldnames = [
        "schedule_name",
        "requested_window_size",
        "step_number_1based",
        "mode",
        "search_cache_source",
        "cache_source",
        "window_start_1based",
        "window_end_1based",
        "executed_block_count",
        "skipped_block_count",
        "executed_blocks_1based",
        "skipped_blocks_1based",
        "noise_relative_mse",
        "image_token_relative_mse",
        "text_token_relative_mse",
        "score",
        *block_fields,
    ]

    schedule_groups: List[
        Tuple[str, Optional[int], Dict[int, Dict[str, Any]]]
    ] = [
        (f"window_size_{window_size:02d}", window_size, schedule)
        for window_size, schedule in sorted(schedules_by_size.items())
    ]
    schedule_groups.append(("global_mixed", None, global_schedule))

    with gzip.open(output_path, "wt", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for schedule_name, requested_window_size, schedule in schedule_groups:
            for step_index in range(num_steps):
                item = schedule[step_index]
                if item.get("mode") == "full_compute":
                    executed = set(range(total_layers))
                else:
                    window = (
                        int(item["window_start_0based"]),
                        int(item["window_end_0based"]),
                    )
                    executed = executed_layers_for_window(total_layers, window)
                row: Dict[str, Any] = {
                    "schedule_name": schedule_name,
                    "requested_window_size": requested_window_size,
                    "step_number_1based": step_index + 1,
                    "mode": item.get("mode"),
                    "search_cache_source": item.get("search_cache_source"),
                    "cache_source": item.get("cache_source"),
                    "window_start_1based": item.get("window_start_1based"),
                    "window_end_1based": item.get("window_end_1based"),
                    "executed_block_count": len(executed),
                    "skipped_block_count": total_layers - len(executed),
                    "executed_blocks_1based": one_based_layer_string(
                        sorted(executed)
                    ),
                    "skipped_blocks_1based": one_based_layer_string(
                        layer
                        for layer in range(total_layers)
                        if layer not in executed
                    ),
                    "noise_relative_mse": item.get("noise_relative_mse"),
                    "image_token_relative_mse": item.get(
                        "image_token_relative_mse"
                    ),
                    "text_token_relative_mse": item.get(
                        "text_token_relative_mse"
                    ),
                    "score": item.get("score"),
                }
                for layer in range(total_layers):
                    row[f"block_{layer + 1:03d}"] = (
                        1 if layer in executed else 0
                    )
                writer.writerow(row)


def save_image(image: Image.Image, path: Path, args: argparse.Namespace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".tmp" + path.suffix)
    if args.image_format == "png":
        image.save(temporary, format="PNG", compress_level=6)
    elif args.image_format == "jpg":
        image.save(
            temporary,
            format="JPEG",
            quality=args.image_quality,
            subsampling=0,
        )
    else:
        image.save(
            temporary,
            format="WEBP",
            quality=args.image_quality,
            method=6,
        )
    temporary.replace(path)


def image_suffix(args: argparse.Namespace) -> str:
    return ".jpg" if args.image_format == "jpg" else f".{args.image_format}"


def load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as image_file:
        return image_file.convert("RGB").copy()


def make_sample_args(
    args: argparse.Namespace,
    manifest_row: Dict[str, Any],
) -> argparse.Namespace:
    sample_args = argparse.Namespace(**vars(args))
    sample_args.prompt = str(manifest_row["prompt"])
    sample_args.negative_prompt = str(manifest_row["negative_prompt"])
    sample_args.seed = int(manifest_row["generation_seed"])
    sample_args.sample_index = int(manifest_row["sample_index"])
    sample_args.quiet = not args.verbose_candidates
    return sample_args


def run_schedule(
    pipe,
    transformer_blocks,
    original_transformer_forward,
    schedule: Dict[int, Dict[str, Any]],
    input_image: Image.Image,
    sample_args: argparse.Namespace,
    forwards_per_step: int,
) -> Tuple[Image.Image, float]:
    controller = ScheduledController(
        transformer_blocks=transformer_blocks,
        original_transformer_forward=original_transformer_forward,
        schedule=schedule,
        args=sample_args,
        forwards_per_step=forwards_per_step,
    )
    torch.cuda.synchronize(torch.device(sample_args.device))
    start_time = time.perf_counter()
    with replace_transformer_forward(pipe.transformer, controller):
        result_image = generate_image(pipe, [input_image], sample_args)
    torch.cuda.synchronize(torch.device(sample_args.device))
    elapsed = time.perf_counter() - start_time
    controller.validate_complete()
    return result_image, elapsed


def process_sample(
    pipe,
    manifest_row: Dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
    window_sizes: Sequence[int],
    rank: int,
) -> Dict[str, Any]:
    sample_index = int(manifest_row["sample_index"])
    sample_dir = output_dir / "samples" / f"{sample_index:05d}"
    sample_dir.mkdir(parents=True, exist_ok=True)
    complete_path = sample_dir / "complete.json"
    metadata_path = sample_dir / "metadata.json"
    previous_metadata: Dict[str, Any] = {}
    if metadata_path.exists():
        previous_metadata = read_json(metadata_path)
    resume_strategy_compatible = (
        args.resume
        and previous_metadata.get("strategy_version") == STRATEGY_VERSION
    )
    if resume_strategy_compatible and complete_path.exists():
        complete_payload = read_json(complete_path)
        if complete_payload.get("strategy_version") == STRATEGY_VERSION:
            return complete_payload

    sample_args = make_sample_args(args, manifest_row)
    image_path = Path(str(manifest_row["image_path"]))
    input_image = load_rgb(image_path)
    transformer = pipe.transformer
    transformer_blocks = list(transformer.transformer_blocks)
    total_layers = len(transformer_blocks)
    candidates = build_all_candidates(
        total_layers=total_layers,
        window_sizes=window_sizes,
        stride=args.window_stride,
    )
    forwards_per_step = infer_forwards_per_step(sample_args)
    original_transformer_forward = transformer.forward
    suffix = image_suffix(args)
    baseline_path = sample_dir / f"baseline_full{suffix}"
    search_state_path = sample_dir / "search_state.json.gz"

    write_json(
        {
            **manifest_row,
            "rank": rank,
            "window_size_min": min(window_sizes),
            "window_size_max": max(window_sizes),
            "window_stride": args.window_stride,
            "num_inference_steps": args.num_inference_steps,
            "strategy_version": STRATEGY_VERSION,
            "first_timestep": "full_compute_all_blocks",
            "skipped_block_policy": "previous_timestep_same_block_cache",
            "candidate_count_first_timestep": 0,
            "candidate_count_later_timestep": len(candidates),
        },
        metadata_path,
    )

    search_state_reused = False
    if (
        resume_strategy_compatible
        and baseline_path.exists()
        and search_state_path.exists()
    ):
        baseline_image = load_rgb(baseline_path)
        search_state = read_json_gz(search_state_path)
        if search_state.get("strategy_version") == STRATEGY_VERSION:
            aggregate_rows = search_state["aggregate_rows"]
            search_elapsed = float(search_state["search_elapsed_seconds"])
            search_state_reused = True

    if not search_state_reused:
        total_candidate_evaluations = (
            max(0, args.num_inference_steps - 1)
            * forwards_per_step
            * len(candidates)
        )
        rank_print(
            rank,
            f"sample {sample_index:05d}: 开始候选搜索；"
            f"step数={args.num_inference_steps}，"
            f"每个非首step候选={len(candidates)}，"
            f"候选评估总数={total_candidate_evaluations}，"
            f"每{args.progress_every}个候选输出一次进度。",
        )
        search_controller = SearchController(
            transformer_blocks=transformer_blocks,
            original_transformer_forward=original_transformer_forward,
            candidates=candidates,
            args=sample_args,
            forwards_per_step=forwards_per_step,
        )
        torch.cuda.synchronize(torch.device(sample_args.device))
        search_start = time.perf_counter()
        with replace_transformer_forward(transformer, search_controller):
            baseline_image = generate_image(pipe, [input_image], sample_args)
        torch.cuda.synchronize(torch.device(sample_args.device))
        search_elapsed = time.perf_counter() - search_start
        search_controller.validate_complete()
        aggregate_rows = search_controller.aggregate_rows
        save_image(baseline_image, baseline_path, args)
        write_json_gz(
            {
                "strategy_version": STRATEGY_VERSION,
                "first_timestep": "full_compute_all_blocks",
                "skipped_block_policy": "previous_timestep_same_block_cache",
                "search_elapsed_seconds": search_elapsed,
                "aggregate_rows": aggregate_rows,
            },
            search_state_path,
        )
        write_json_gz(
            search_controller.branch_rows,
            sample_dir / "candidate_branch_details.json.gz",
        )
        del search_controller
        rank_print(
            rank,
            f"sample {sample_index:05d}: 候选搜索完成；"
            f"耗时={search_elapsed:.2f}秒，开始生成各窗口结果。",
        )
    else:
        rank_print(
            rank,
            f"sample {sample_index:05d}: 已复用完整候选搜索缓存；"
            f"原搜索耗时={search_elapsed:.2f}秒。",
        )

    resume_outputs = resume_strategy_compatible and search_state_reused
    schedules_by_size, global_schedule = derive_schedules(
        aggregate_rows=aggregate_rows,
        window_sizes=window_sizes,
        num_steps=args.num_inference_steps,
        total_layers=total_layers,
    )
    annotated_rows = annotate_candidate_rows(
        aggregate_rows,
        schedules_by_size,
        global_schedule,
    )
    candidate_scores_path = sample_dir / "candidate_scores.csv.gz"
    candidate_matrix_path = sample_dir / "candidate_layer_matrix.csv.gz"
    if not (resume_outputs and candidate_scores_path.exists()):
        write_candidate_scores_gz(annotated_rows, candidate_scores_path)
    if not (resume_outputs and candidate_matrix_path.exists()):
        write_candidate_matrix_gz(
            annotated_rows,
            total_layers,
            candidate_matrix_path,
        )

    schedules_payload = {
        "strategy_version": STRATEGY_VERSION,
        "first_timestep": "full_compute_all_blocks",
        "skipped_block_policy": "previous_timestep_same_block_cache",
        "matrix_legend": {
            "1": "execute_current_timestep",
            "0": "reuse_previous_timestep_same_block_cache",
        },
        "window_sizes": {
            str(window_size): [
                schedule[step_index]
                for step_index in range(args.num_inference_steps)
            ]
            for window_size, schedule in schedules_by_size.items()
        },
        "global_mixed": [
            global_schedule[step_index]
            for step_index in range(args.num_inference_steps)
        ],
    }
    write_json(
        schedules_payload,
        sample_dir / "best_sequences.json",
    )
    best_schedule_matrix_path = sample_dir / "best_schedule_layer_matrix.csv.gz"
    write_best_schedule_matrix_gz(
        schedules_by_size=schedules_by_size,
        global_schedule=global_schedule,
        total_layers=total_layers,
        num_steps=args.num_inference_steps,
        output_path=best_schedule_matrix_path,
    )

    window_results_path = sample_dir / "window_results.json"
    if resume_outputs and window_results_path.exists():
        window_results: Dict[str, Dict[str, Any]] = read_json(window_results_path)
    else:
        window_results = {}

    for window_size in window_sizes:
        key = str(window_size)
        result_path = sample_dir / f"window_size_{window_size:02d}{suffix}"
        if resume_outputs and key in window_results and result_path.exists():
            continue
        rank_print(
            rank,
            f"sample {sample_index:05d}: 生成window_size={window_size}",
        )
        result_image, elapsed = run_schedule(
            pipe=pipe,
            transformer_blocks=transformer_blocks,
            original_transformer_forward=original_transformer_forward,
            schedule=schedules_by_size[window_size],
            input_image=input_image,
            sample_args=sample_args,
            forwards_per_step=forwards_per_step,
        )
        save_image(result_image, result_path, args)
        metrics = image_metrics(baseline_image, result_image)
        window_results[key] = {
            "strategy_version": STRATEGY_VERSION,
            "window_size": window_size,
            "result_image": str(result_path.resolve()),
            "elapsed_seconds": elapsed,
            **metrics,
            "sequence": [
                schedules_by_size[window_size][step_index]
                for step_index in range(args.num_inference_steps)
            ],
        }
        write_json(window_results, window_results_path)
        del result_image

    mixed_path = sample_dir / f"global_best_mixed{suffix}"
    mixed_metrics_path = sample_dir / "global_best_mixed_metrics.json"
    if resume_outputs and mixed_path.exists() and mixed_metrics_path.exists():
        mixed_result = read_json(mixed_metrics_path)
    else:
        rank_print(rank, f"sample {sample_index:05d}: 生成全局混合最优序列")
        mixed_image, mixed_elapsed = run_schedule(
            pipe=pipe,
            transformer_blocks=transformer_blocks,
            original_transformer_forward=original_transformer_forward,
            schedule=global_schedule,
            input_image=input_image,
            sample_args=sample_args,
            forwards_per_step=forwards_per_step,
        )
        save_image(mixed_image, mixed_path, args)
        mixed_result = {
            "strategy_version": STRATEGY_VERSION,
            "result_image": str(mixed_path.resolve()),
            "elapsed_seconds": mixed_elapsed,
            **image_metrics(baseline_image, mixed_image),
            "sequence": [
                global_schedule[step_index]
                for step_index in range(args.num_inference_steps)
            ],
        }
        write_json(mixed_result, mixed_metrics_path)
        del mixed_image

    best_window_size = min(
        window_results,
        key=lambda key: (
            float(window_results[key]["mse"]),
            -int(key),
        ),
    )
    sample_summary = {
        **manifest_row,
        "strategy_version": STRATEGY_VERSION,
        "rank": rank,
        "sample_dir": str(sample_dir.resolve()),
        "baseline_image": str(baseline_path.resolve()),
        "search_elapsed_seconds": search_elapsed,
        "candidate_count_first_timestep": 0,
        "candidate_count_later_timestep": len(candidates),
        "best_window_size_by_final_mse": int(best_window_size),
        "best_window_result": window_results[best_window_size],
        "global_mixed_result": mixed_result,
        "window_results_file": str(window_results_path.resolve()),
        "best_sequences_file": str(
            (sample_dir / "best_sequences.json").resolve()
        ),
        "best_schedule_layer_matrix_file": str(
            best_schedule_matrix_path.resolve()
        ),
    }
    write_json(sample_summary, complete_path)
    return sample_summary


def append_error(
    output_dir: Path,
    rank: int,
    manifest_row: Dict[str, Any],
    error: BaseException,
) -> None:
    error_path = output_dir / f"errors_rank_{rank:03d}.jsonl"
    payload = {
        "sample_index": manifest_row.get("sample_index"),
        "image_path": manifest_row.get("image_path"),
        "error_type": type(error).__name__,
        "error": str(error),
        "traceback": traceback.format_exc(),
    }
    with error_path.open("a", encoding="utf-8") as error_file:
        error_file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def merge_summaries(
    output_dir: Path,
    expected_count: int,
) -> None:
    sample_dirs = sorted((output_dir / "samples").glob("[0-9][0-9][0-9][0-9][0-9]"))
    completed: List[Path] = []
    for sample_dir in sample_dirs:
        complete_path = sample_dir / "complete.json"
        if not complete_path.exists():
            continue
        complete_payload = read_json(complete_path)
        if complete_payload.get("strategy_version") == STRATEGY_VERSION:
            completed.append(complete_path)
    sample_summary_path = output_dir / "samples_summary.csv"
    with sample_summary_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as summary_file:
        fieldnames = [
            "sample_index",
            "image_relative_path",
            "prompt_group",
            "prompt_id",
            "generation_seed",
            "best_window_size_by_final_mse",
            "best_window_mse",
            "best_window_psnr",
            "global_mixed_mse",
            "global_mixed_psnr",
            "sample_dir",
        ]
        writer = csv.DictWriter(summary_file, fieldnames=fieldnames)
        writer.writeheader()
        for complete_path in completed:
            item = read_json(complete_path)
            writer.writerow(
                {
                    "sample_index": item["sample_index"],
                    "image_relative_path": item["image_relative_path"],
                    "prompt_group": item["prompt_group"],
                    "prompt_id": item["prompt_id"],
                    "generation_seed": item["generation_seed"],
                    "best_window_size_by_final_mse": item[
                        "best_window_size_by_final_mse"
                    ],
                    "best_window_mse": item["best_window_result"]["mse"],
                    "best_window_psnr": item["best_window_result"]["psnr"],
                    "global_mixed_mse": item["global_mixed_result"]["mse"],
                    "global_mixed_psnr": item["global_mixed_result"]["psnr"],
                    "sample_dir": item["sample_dir"],
                }
            )

    window_summary_path = output_dir / "window_results_summary.csv"
    with window_summary_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as window_file:
        fieldnames = [
            "sample_index",
            "window_size",
            "mae",
            "mse",
            "rmse",
            "psnr",
            "changed_ratio",
            "elapsed_seconds",
            "result_image",
        ]
        writer = csv.DictWriter(window_file, fieldnames=fieldnames)
        writer.writeheader()
        for complete_path in completed:
            item = read_json(complete_path)
            window_results = read_json(Path(item["window_results_file"]))
            for window_size in sorted(window_results, key=int):
                result = window_results[window_size]
                writer.writerow(
                    {
                        "sample_index": item["sample_index"],
                        "window_size": result["window_size"],
                        "mae": result["mae"],
                        "mse": result["mse"],
                        "rmse": result["rmse"],
                        "psnr": result["psnr"],
                        "changed_ratio": result["changed_ratio"],
                        "elapsed_seconds": result["elapsed_seconds"],
                        "result_image": result["result_image"],
                    }
                )
    write_json(
        {
            "strategy_version": STRATEGY_VERSION,
            "expected_samples": expected_count,
            "completed_samples": len(completed),
            "samples_summary": str(sample_summary_path.resolve()),
            "window_results_summary": str(window_summary_path.resolve()),
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
        prompt_path=Path(args.prompt_file),
        language=args.prompt_language,
        include_viton=args.include_viton_prompts,
    )
    manifest = build_or_load_manifest(
        args=args,
        prompts=prompts,
        negative_prompt=negative_prompt,
        output_dir=output_dir,
        rank=rank,
        world_size=world_size,
    )
    if rank == 0:
        write_json(
            {
                **vars(args),
                "strategy_version": STRATEGY_VERSION,
                "first_timestep": "full_compute_all_blocks",
                "skipped_block_policy": "previous_timestep_same_block_cache",
                "world_size": world_size,
                "parsed_prompt_count": len(prompts),
                "negative_prompt": negative_prompt,
            },
            output_dir / "run_config.json",
        )
        rank_print(
            rank,
            f"manifest共{len(manifest)}条；解析到{len(prompts)}条正向提示词。",
        )
    barrier(world_size)

    if args.prepare_only:
        if rank == 0:
            rank_print(rank, f"manifest已生成：{output_dir / 'manifest.jsonl'}")
        cleanup_distributed(world_size)
        return

    window_sizes = list(
        range(args.window_size_min, args.window_size_max + 1)
    )
    local_rows = [
        row
        for row in manifest
        if int(row["sample_index"]) % world_size == rank
    ]
    if args.limit_local_samples is not None:
        local_rows = local_rows[: args.limit_local_samples]

    rank_print(
        rank,
        f"cuda:{local_rank}分配到{len(local_rows)}张；"
        f"窗口长度={args.window_size_min}..{args.window_size_max}；"
        "step 1全层计算，step 2起跳过层读取上一step同层缓存。",
    )
    pipe = load_pipeline(args)

    for local_index, manifest_row in enumerate(local_rows, start=1):
        sample_index = int(manifest_row["sample_index"])
        rank_print(
            rank,
            f"[{local_index}/{len(local_rows)}] 开始sample {sample_index:05d}",
        )
        try:
            process_sample(
                pipe=pipe,
                manifest_row=manifest_row,
                args=args,
                output_dir=output_dir,
                window_sizes=window_sizes,
                rank=rank,
            )
        except Exception as error:
            append_error(output_dir, rank, manifest_row, error)
            rank_print(
                rank,
                f"sample {sample_index:05d}失败："
                f"{type(error).__name__}: {error}",
            )
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
        merge_summaries(output_dir, args.sample_count)
        rank_print(rank, f"本轮完成，汇总目录：{output_dir}")
    barrier(world_size)
    cleanup_distributed(world_size)


if __name__ == "__main__":
    main()
