#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen-Image-Edit-2511：验证不同去噪 step、不同 Transformer Block 的特征变化。

本脚本始终执行完整模型，不跳过、不缓存替代任何 Block。它仅在 Block.forward
外层采集输出，比较同一 CFG 分支中相邻去噪 step 的双流 hidden：

    image_hidden[t, block] vs image_hidden[t-1, block]
    text_hidden[t, block]  vs text_hidden[t-1, block]

主要输出：
    baseline_full.png
    block_change_metrics.csv
    block_change_matrices.npz
    image_relative_l2_heatmap.png
    text_relative_l2_heatmap.png
    combined_relative_l2_heatmap.png
    image_cosine_similarity_heatmap.png
    step_change_curve.png
    summary.json

说明：
1. 热力图横轴是 Block 1..60，纵轴是当前去噪 step 2..T。
2. relative_l2 越大表示相邻 step 的特征变化越快。
3. cosine_similarity 越接近 1 表示方向越相似、越适合跨 step 复用。
4. 只保留每个 CFG 分支的上一个 step 特征，不保存全部 step 的大 Tensor。

CUDA_VISIBLE_DEVICES=0,1,2,3 torchrun \
  --standalone \
  --nproc_per_node=2 \
  qwen_edit_step_block_heatmap.py \
  --sample-count 100 \
  --num-inference-steps 40 \
  --sampling-seed 20260724 \
  --generation-seed 0 \
  --true-cfg-scale 1.0 \
  --guidance-scale 1.0 \
  --output-dir outputs/qwen_step_block_heatmap_n100
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import time
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.distributed as dist
from PIL import Image

try:
    from diffusers import QwenImageEditPlusPipeline
except ImportError as import_error:
    raise ImportError(
        "当前环境没有 QwenImageEditPlusPipeline。请在已经配置好的 "
        "MMDITModelCompression Conda 环境中运行本脚本。"
    ) from import_error


TensorPair = Tuple[torch.Tensor, torch.Tensor]  # (text hidden, image hidden)
BlockCache = Dict[int, TensorPair]
METRIC_NAMES = ("relative_mse", "relative_l2", "cosine_similarity")
STREAM_NAMES = ("image", "text")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DEFAULT_PROMPT_GROUPS = {
    "早期 10 prompt 评估",
    "生活场景评估",
    "姿态背景评估",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "完整运行Qwen-Image-Edit-2511，统计相邻去噪step在每个Block上的"
            "text/image hidden变化并绘制热力图。"
        )
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default="/data4/guowenwu/MMDITModelCompression/models/Qwen-Image-Edit-2511",
        help="本地 Diffusers 模型目录。",
    )
    parser.add_argument(
        "--input-image",
        type=str,
        nargs="+",
        default=None,
        help="手动单组模式的一张或多张参考图；不填写时启用数据集随机抽样。",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="手动单组模式的编辑指令；数据集模式从prompt文件随机抽取。",
    )
    parser.add_argument(
        "--negative-prompt",
        type=str,
        default=" ",
        help="负面提示词；默认是一个空格。",
    )
    parser.add_argument(
        "--num-inference-steps",
        type=int,
        default=40,
        help="去噪步数；建议与之前窗口搜索实验保持一致，默认40。",
    )
    parser.add_argument(
        "--true-cfg-scale",
        type=float,
        default=1.0,
        help="Qwen true CFG强度；蒸馏模型默认1.0。",
    )
    parser.add_argument(
        "--guidance-scale",
        type=float,
        default=1.0,
        help="传给Transformer guidance embedding的强度。",
    )
    parser.add_argument(
        "--forwards-per-step",
        type=int,
        choices=[1, 2],
        default=None,
        help=(
            "每个step调用Transformer的次数。不填写时根据true CFG推断："
            "true_cfg_scale>1且negative_prompt非空时为2，否则为1。"
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="随机种子。")
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
        help="之前使用的Markdown提示词集合。",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=10,
        help="数据集模式随机测试多少组图像+文本；只需修改此参数。",
    )
    parser.add_argument(
        "--sampling-seed",
        type=int,
        default=20260724,
        help="控制图片抽样和提示词分配；同一输出目录可复现。",
    )
    parser.add_argument(
        "--generation-seed",
        type=int,
        default=0,
        help="第i组使用generation_seed+i生成。",
    )
    parser.add_argument(
        "--prompt-language",
        choices=["english", "chinese"],
        default="english",
    )
    parser.add_argument(
        "--include-viton-prompts",
        action="store_true",
        help="默认排除需要额外服装参考图的试衣提示词。",
    )
    parser.add_argument("--width", type=int, default=None, help="可选输出宽度。")
    parser.add_argument("--height", type=int, default=None, help="可选输出高度。")
    parser.add_argument(
        "--dtype",
        choices=["bf16", "fp16"],
        default="bf16",
        help="模型推理精度；H20推荐bf16。",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cuda:0",
        help="运行设备，例如cuda:0。",
    )
    parser.add_argument(
        "--cpu-offload",
        action="store_true",
        help="启用Diffusers model CPU offload；采集会明显变慢。",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="/data4/guowenwu/MMDITModelCompression/outputs/qwen_step_block_heatmap",
        help="结果目录。",
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="显示Diffusers进度条。",
    )
    parser.add_argument(
        "--heatmap-percentile",
        type=float,
        default=99.0,
        help="热力图颜色上限使用的百分位，避免极端值淹没主体，默认99。",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="忽略已经完成的样本并重新运行；默认支持断点续跑。",
    )
    parser.set_defaults(resume=True)
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="某组失败时立即退出；默认记录错误后继续其他组。",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只生成固定随机manifest，不加载模型。",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.num_inference_steps < 2:
        raise ValueError("--num-inference-steps至少为2，才能比较相邻step。")
    if not 0.0 < args.heatmap_percentile <= 100.0:
        raise ValueError("--heatmap-percentile必须在(0, 100]范围内。")
    if args.width is not None and args.width <= 0:
        raise ValueError("--width必须为正数。")
    if args.height is not None and args.height <= 0:
        raise ValueError("--height必须为正数。")
    manual_mode = args.input_image is not None or args.prompt is not None
    if manual_mode:
        if not args.input_image or not args.prompt:
            raise ValueError("手动模式必须同时填写--input-image和--prompt。")
        for image_path in args.input_image:
            if not Path(image_path).is_file():
                raise FileNotFoundError(f"输入图片不存在：{image_path}")
    else:
        if args.sample_count <= 0:
            raise ValueError("--sample-count必须大于0。")
        if not Path(args.dataset_root).is_dir():
            raise FileNotFoundError(f"数据集目录不存在：{args.dataset_root}")
        if not Path(args.prompt_file).is_file():
            raise FileNotFoundError(f"提示词文件不存在：{args.prompt_file}")
    if not args.prepare_only and not Path(args.model_path).is_dir():
        raise FileNotFoundError(f"模型目录不存在：{args.model_path}")


def infer_forwards_per_step(args: argparse.Namespace) -> int:
    if args.forwards_per_step is not None:
        return int(args.forwards_per_step)
    has_negative_prompt = bool(str(args.negative_prompt).strip())
    return 2 if args.true_cfg_scale > 1.0 and has_negative_prompt else 1


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
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def build_or_load_manifest(
    args: argparse.Namespace,
    output_dir: Path,
    rank: int,
    world_size: int,
) -> List[Dict[str, Any]]:
    manifest_path = output_dir / "manifest.jsonl"
    metadata_path = output_dir / "manifest_metadata.json"

    if args.input_image is not None:
        return [
            {
                "sample_index": 0,
                "image_paths": [str(Path(path).resolve()) for path in args.input_image],
                "image_relative_path": ",".join(args.input_image),
                "prompt_group": "manual",
                "prompt_id": "manual",
                "prompt": args.prompt,
                "negative_prompt": args.negative_prompt,
                "generation_seed": args.seed,
            }
        ]

    prompts, negative_prompt = parse_prompt_markdown(
        Path(args.prompt_file),
        args.prompt_language,
        args.include_viton_prompts,
    )

    if rank == 0 and manifest_path.exists():
        if not metadata_path.exists():
            raise FileNotFoundError(
                f"已有manifest但缺少{metadata_path}，无法验证断点续跑参数。"
            )
        with metadata_path.open("r", encoding="utf-8") as handle:
            old_metadata = json.load(handle)
        expected = {
            "dataset_root": str(Path(args.dataset_root).resolve()),
            "prompt_file": str(Path(args.prompt_file).resolve()),
            "sample_count": args.sample_count,
            "sampling_seed": args.sampling_seed,
            "generation_seed": args.generation_seed,
            "prompt_language": args.prompt_language,
            "include_viton_prompts": args.include_viton_prompts,
        }
        mismatched = [
            key for key, value in expected.items() if old_metadata.get(key) != value
        ]
        if mismatched:
            raise ValueError(
                f"已有manifest与本次参数不一致：{mismatched}。"
                "请恢复原参数或更换--output-dir。"
            )

    if rank == 0 and not manifest_path.exists():
        dataset_root = Path(args.dataset_root).resolve()
        image_paths = scan_images(dataset_root)
        if len(image_paths) < args.sample_count:
            raise ValueError(
                f"数据集只找到{len(image_paths)}张图片，少于--sample-count="
                f"{args.sample_count}。"
            )
        rng = random.Random(args.sampling_seed)
        selected_paths = rng.sample(image_paths, args.sample_count)
        rows: List[Dict[str, Any]] = []
        for sample_index, image_path in enumerate(selected_paths):
            prompt_item = prompts[rng.randrange(len(prompts))]
            rows.append(
                {
                    "sample_index": sample_index,
                    "image_paths": [str(image_path)],
                    "image_relative_path": str(image_path.relative_to(dataset_root)),
                    "prompt_group": prompt_item["group"],
                    "prompt_id": prompt_item["prompt_id"],
                    "prompt": prompt_item["prompt"],
                    "negative_prompt": negative_prompt,
                    "generation_seed": args.generation_seed + sample_index,
                }
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        temporary.replace(manifest_path)
        write_json(
            {
                "dataset_root": str(dataset_root),
                "total_discovered_images": len(image_paths),
                "prompt_file": str(Path(args.prompt_file).resolve()),
                "parsed_prompt_count": len(prompts),
                "sample_count": args.sample_count,
                "sampling_seed": args.sampling_seed,
                "generation_seed": args.generation_seed,
                "prompt_language": args.prompt_language,
                "include_viton_prompts": args.include_viton_prompts,
            },
            metadata_path,
        )

    barrier(world_size)
    rows = []
    with manifest_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    if len(rows) != args.sample_count:
        raise ValueError(
            f"manifest包含{len(rows)}组，但--sample-count={args.sample_count}。"
        )
    return rows


def load_input_images(image_paths: Sequence[str]) -> List[Image.Image]:
    images: List[Image.Image] = []
    for image_path in image_paths:
        with Image.open(image_path) as image:
            images.append(image.convert("RGB"))
    return images


def make_generator(seed: int) -> torch.Generator:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    return generator


def split_block_output(output: Any) -> TensorPair:
    """
    QwenImageTransformerBlock返回：
    (encoder_hidden_states, hidden_states)，即(text, image)。
    """
    if not isinstance(output, (tuple, list)) or len(output) < 2:
        raise RuntimeError(
            "Qwen Block.forward返回值不是预期的双流tuple/list；"
            "请检查当前Diffusers版本。"
        )
    text_output, image_output = output[0], output[1]
    if not isinstance(text_output, torch.Tensor):
        raise RuntimeError("Block返回的text输出不是Tensor。")
    if not isinstance(image_output, torch.Tensor):
        raise RuntimeError("Block返回的image输出不是Tensor。")
    return text_output, image_output


def tensor_metrics(previous: torch.Tensor, current: torch.Tensor) -> Dict[str, float]:
    if previous.shape != current.shape:
        raise RuntimeError(
            "相邻step的hidden形状不一致："
            f"previous={tuple(previous.shape)}，current={tuple(current.shape)}"
        )
    if previous.device != current.device:
        previous = previous.to(current.device)

    previous_float = previous.float()
    current_float = current.float()
    difference = current_float - previous_float

    difference_energy = difference.square().mean()
    previous_energy = previous_float.square().mean().clamp_min(1e-12)
    relative_mse = difference_energy / previous_energy

    previous_flat = previous_float.reshape(-1)
    current_flat = current_float.reshape(-1)
    cosine_denominator = (
        torch.linalg.vector_norm(previous_flat)
        * torch.linalg.vector_norm(current_flat)
    ).clamp_min(1e-12)
    cosine_similarity = torch.dot(previous_flat, current_flat) / cosine_denominator

    return {
        "relative_mse": float(relative_mse.item()),
        "relative_l2": float(torch.sqrt(relative_mse).item()),
        "cosine_similarity": float(cosine_similarity.item()),
    }


class FeatureChangeController:
    """包装完整Transformer，在Block输出处采集相邻step变化，不改变模型输出。"""

    def __init__(
        self,
        transformer_blocks: Sequence[torch.nn.Module],
        original_transformer_forward: Callable[..., Any],
        num_inference_steps: int,
        forwards_per_step: int,
    ) -> None:
        self.blocks = list(transformer_blocks)
        self.original_transformer_forward = original_transformer_forward
        self.original_block_forwards = [block.forward for block in self.blocks]
        self.num_inference_steps = num_inference_steps
        self.forwards_per_step = forwards_per_step
        self.total_layers = len(self.blocks)
        self.expected_calls = num_inference_steps * forwards_per_step
        self.call_index = 0
        self.previous_cache_by_branch: Dict[int, BlockCache] = {}
        self.rows: List[Dict[str, Any]] = []

    def _install_capture(
        self,
        step_index: int,
        branch_index: int,
        current_cache: BlockCache,
    ):
        previous_cache = self.previous_cache_by_branch.get(branch_index)

        def make_forward(layer_index: int):
            original_forward = self.original_block_forwards[layer_index]

            def capture_forward(_block_self, *args, **kwargs):
                output = original_forward(*args, **kwargs)
                text_output, image_output = split_block_output(output)

                # detach不会改变模型计算；只保留到同一分支的下一个step。
                current_cache[layer_index] = (
                    text_output.detach(),
                    image_output.detach(),
                )

                if previous_cache is not None:
                    if layer_index not in previous_cache:
                        raise RuntimeError(
                            f"上一step缓存缺少Block {layer_index + 1}。"
                        )
                    previous_text, previous_image = previous_cache[layer_index]
                    for stream_name, previous_tensor, current_tensor in (
                        ("text", previous_text, text_output),
                        ("image", previous_image, image_output),
                    ):
                        values = tensor_metrics(previous_tensor, current_tensor)
                        self.rows.append(
                            {
                                "previous_step_index_0based": step_index - 1,
                                "current_step_index_0based": step_index,
                                "previous_step_number_1based": step_index,
                                "current_step_number_1based": step_index + 1,
                                "branch_index_0based": branch_index,
                                "branch_number_1based": branch_index + 1,
                                "block_index_0based": layer_index,
                                "block_number_1based": layer_index + 1,
                                "stream": stream_name,
                                **values,
                            }
                        )
                return output

            return capture_forward

        @contextmanager
        def context():
            try:
                for layer_index, block in enumerate(self.blocks):
                    block.forward = types.MethodType(
                        make_forward(layer_index),
                        block,
                    )
                yield
            finally:
                for block, original_forward in zip(
                    self.blocks,
                    self.original_block_forwards,
                ):
                    block.forward = original_forward

        return context()

    def __call__(self, *args, **kwargs):
        if self.call_index >= self.expected_calls:
            raise RuntimeError(
                "Transformer forward次数超过预期。请显式设置正确的"
                "--forwards-per-step。"
            )
        step_index = self.call_index // self.forwards_per_step
        branch_index = self.call_index % self.forwards_per_step
        print(
            f"[采集] step={step_index + 1}/{self.num_inference_steps}，"
            f"branch={branch_index + 1}/{self.forwards_per_step}，"
            f"完整执行{self.total_layers}个Block",
            flush=True,
        )

        current_cache: BlockCache = {}
        with self._install_capture(step_index, branch_index, current_cache):
            output = self.original_transformer_forward(*args, **kwargs)

        if len(current_cache) != self.total_layers:
            missing = [
                index + 1
                for index in range(self.total_layers)
                if index not in current_cache
            ]
            raise RuntimeError(f"当前step缓存不完整，缺少Block：{missing}")

        # 当前step替换上一step；更早的Tensor引用随即释放。
        self.previous_cache_by_branch[branch_index] = current_cache
        self.call_index += 1
        return output

    def validate_complete(self) -> None:
        if self.call_index != self.expected_calls:
            raise RuntimeError(
                f"pipeline实际调用Transformer {self.call_index}次，"
                f"预期{self.expected_calls}次。请检查--forwards-per-step。"
            )
        expected_rows = (
            (self.num_inference_steps - 1)
            * self.forwards_per_step
            * self.total_layers
            * len(STREAM_NAMES)
        )
        if len(self.rows) != expected_rows:
            raise RuntimeError(
                f"采集到{len(self.rows)}行指标，预期{expected_rows}行。"
            )


@contextmanager
def replace_transformer_forward(
    transformer: torch.nn.Module,
    controller: Callable[..., Any],
):
    original_forward = transformer.forward

    def controlled_forward(_transformer_self, *args, **kwargs):
        return controller(*args, **kwargs)

    transformer.forward = types.MethodType(controlled_forward, transformer)
    try:
        yield
    finally:
        transformer.forward = original_forward


def load_pipeline(args: argparse.Namespace) -> QwenImageEditPlusPipeline:
    torch_dtype = torch.bfloat16 if args.dtype == "bf16" else torch.float16
    print(f"正在加载模型：{args.model_path}", flush=True)
    pipe = QwenImageEditPlusPipeline.from_pretrained(
        args.model_path,
        torch_dtype=torch_dtype,
        local_files_only=True,
    )
    if args.cpu_offload:
        device = torch.device(args.device)
        gpu_id = 0 if device.index is None else int(device.index)
        pipe.enable_model_cpu_offload(gpu_id=gpu_id)
    else:
        pipe.to(args.device)
    pipe.set_progress_bar_config(disable=not args.show_progress)
    return pipe


def build_pipeline_inputs(
    input_images: Sequence[Image.Image],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "image": list(input_images) if len(input_images) > 1 else input_images[0],
        "prompt": args.prompt,
        "negative_prompt": args.negative_prompt,
        "true_cfg_scale": args.true_cfg_scale,
        "guidance_scale": args.guidance_scale,
        "num_inference_steps": args.num_inference_steps,
        "num_images_per_prompt": 1,
        "generator": make_generator(args.seed),
        "max_sequence_length": 512,
    }
    if args.width is not None:
        values["width"] = args.width
    if args.height is not None:
        values["height"] = args.height
    return values


def aggregate_matrices(
    rows: Sequence[Dict[str, Any]],
    num_inference_steps: int,
    total_layers: int,
) -> Dict[str, np.ndarray]:
    shape = (num_inference_steps - 1, total_layers)
    sums = {
        f"{stream}_{metric}": np.zeros(shape, dtype=np.float64)
        for stream in STREAM_NAMES
        for metric in METRIC_NAMES
    }
    counts = {
        stream: np.zeros(shape, dtype=np.int64)
        for stream in STREAM_NAMES
    }

    for row in rows:
        step = int(row["current_step_index_0based"]) - 1
        block = int(row["block_index_0based"])
        stream = str(row["stream"])
        for metric in METRIC_NAMES:
            sums[f"{stream}_{metric}"][step, block] += float(row[metric])
        counts[stream][step, block] += 1

    matrices: Dict[str, np.ndarray] = {}
    for stream in STREAM_NAMES:
        if np.any(counts[stream] == 0):
            raise RuntimeError(f"{stream}矩阵中存在未采集位置。")
        for metric in METRIC_NAMES:
            key = f"{stream}_{metric}"
            matrices[key] = sums[key] / counts[stream]

    matrices["combined_relative_l2"] = 0.5 * (
        matrices["image_relative_l2"] + matrices["text_relative_l2"]
    )
    matrices["combined_relative_mse"] = 0.5 * (
        matrices["image_relative_mse"] + matrices["text_relative_mse"]
    )
    matrices["combined_cosine_similarity"] = 0.5 * (
        matrices["image_cosine_similarity"]
        + matrices["text_cosine_similarity"]
    )
    return matrices


def write_metrics_csv(rows: Sequence[Dict[str, Any]], output_path: Path) -> None:
    fieldnames = [
        "previous_step_index_0based",
        "current_step_index_0based",
        "previous_step_number_1based",
        "current_step_number_1based",
        "branch_index_0based",
        "branch_number_1based",
        "block_index_0based",
        "block_number_1based",
        "stream",
        "relative_mse",
        "relative_l2",
        "cosine_similarity",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def heatmap(
    matrix: np.ndarray,
    output_path: Path,
    title: str,
    colorbar_label: str,
    percentile: float,
    cmap: str = "magma",
    fixed_range: Optional[Tuple[float, float]] = None,
) -> None:
    width = max(12.0, matrix.shape[1] * 0.22)
    height = max(6.5, matrix.shape[0] * 0.20)
    fig, axis = plt.subplots(figsize=(width, height), constrained_layout=True)

    if fixed_range is None:
        finite = matrix[np.isfinite(matrix)]
        vmin = 0.0
        vmax = float(np.percentile(finite, percentile))
        if not math.isfinite(vmax) or vmax <= vmin:
            vmax = float(np.max(finite)) if finite.size else 1.0
        if vmax <= vmin:
            vmax = 1.0
    else:
        vmin, vmax = fixed_range

    image = axis.imshow(
        matrix,
        aspect="auto",
        origin="upper",
        interpolation="nearest",
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
    )
    axis.set_title(title)
    axis.set_xlabel("Transformer Block (1-based)")
    axis.set_ylabel("Current denoising step (1-based)")

    block_ticks = np.arange(0, matrix.shape[1], 5)
    axis.set_xticks(block_ticks)
    axis.set_xticklabels((block_ticks + 1).astype(str))
    step_tick_stride = max(1, matrix.shape[0] // 15)
    step_ticks = np.arange(0, matrix.shape[0], step_tick_stride)
    axis.set_yticks(step_ticks)
    axis.set_yticklabels((step_ticks + 2).astype(str))

    colorbar = fig.colorbar(image, ax=axis, shrink=0.92)
    colorbar.set_label(colorbar_label)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def plot_step_curve(
    matrices: Dict[str, np.ndarray],
    output_path: Path,
) -> None:
    steps = np.arange(2, matrices["image_relative_l2"].shape[0] + 2)
    fig, axis = plt.subplots(figsize=(12, 6.5), constrained_layout=True)

    for stream, color in (("image", "#0072B2"), ("text", "#D55E00")):
        matrix = matrices[f"{stream}_relative_l2"]
        mean_values = np.mean(matrix, axis=1)
        median_values = np.median(matrix, axis=1)
        axis.plot(
            steps,
            mean_values,
            color=color,
            linewidth=2.0,
            label=f"{stream}: block mean",
        )
        axis.plot(
            steps,
            median_values,
            color=color,
            linewidth=1.4,
            linestyle="--",
            alpha=0.8,
            label=f"{stream}: block median",
        )

    axis.set_title("Adjacent-step feature change across the denoising trajectory")
    axis.set_xlabel("Current denoising step (1-based)")
    axis.set_ylabel("Relative L2 change")
    axis.grid(alpha=0.25)
    axis.legend()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def build_summary(
    args: argparse.Namespace,
    matrices: Dict[str, np.ndarray],
    total_layers: int,
    forwards_per_step: int,
    elapsed_seconds: float,
) -> Dict[str, Any]:
    combined = matrices["combined_relative_l2"]
    step_mean = np.mean(combined, axis=1)
    block_mean = np.mean(combined, axis=0)
    fastest_step_order = np.argsort(step_mean)[::-1]
    fastest_block_order = np.argsort(block_mean)[::-1]

    return {
        "model_path": args.model_path,
        "num_inference_steps": args.num_inference_steps,
        "total_blocks": total_layers,
        "forwards_per_step": forwards_per_step,
        "seed": args.seed,
        "elapsed_seconds": elapsed_seconds,
        "metric_definition": {
            "relative_mse": "mean((current-previous)^2) / mean(previous^2)",
            "relative_l2": "sqrt(relative_mse)",
            "cosine_similarity": "cosine(previous.flatten, current.flatten)",
            "aggregation": "mean across CFG branches",
            "combined": "equal mean of image stream and text stream",
        },
        "highest_change_steps": [
            {
                "current_step_number_1based": int(index + 2),
                "combined_block_mean_relative_l2": float(step_mean[index]),
            }
            for index in fastest_step_order[: min(10, len(fastest_step_order))]
        ],
        "highest_change_blocks": [
            {
                "block_number_1based": int(index + 1),
                "combined_step_mean_relative_l2": float(block_mean[index]),
            }
            for index in fastest_block_order[: min(10, len(fastest_block_order))]
        ],
    }


def save_matrix_plots(
    matrices: Dict[str, np.ndarray],
    output_dir: Path,
    percentile: float,
    title_prefix: str = "",
) -> None:
    prefix = f"{title_prefix} - " if title_prefix else ""
    heatmap(
        matrices["image_relative_l2"],
        output_dir / "image_relative_l2_heatmap.png",
        prefix + "Image hidden: adjacent-step relative L2 change",
        "Relative L2 change (larger = faster change)",
        percentile,
    )
    heatmap(
        matrices["text_relative_l2"],
        output_dir / "text_relative_l2_heatmap.png",
        prefix + "Text hidden: adjacent-step relative L2 change",
        "Relative L2 change (larger = faster change)",
        percentile,
    )
    heatmap(
        matrices["combined_relative_l2"],
        output_dir / "combined_relative_l2_heatmap.png",
        prefix + "Combined text/image hidden: adjacent-step change",
        "Mean relative L2 change (larger = faster change)",
        percentile,
    )
    heatmap(
        matrices["image_cosine_similarity"],
        output_dir / "image_cosine_similarity_heatmap.png",
        prefix + "Image hidden: adjacent-step cosine similarity",
        "Cosine similarity (closer to 1 = more stable)",
        percentile,
        cmap="viridis",
        fixed_range=(0.90, 1.0),
    )
    plot_step_curve(matrices, output_dir / "step_change_curve.png")


def run_one_sample(
    pipe: QwenImageEditPlusPipeline,
    args: argparse.Namespace,
    manifest_row: Dict[str, Any],
    sample_dir: Path,
    rank: int,
) -> Dict[str, Any]:
    sample_args = argparse.Namespace(**vars(args))
    sample_args.prompt = str(manifest_row["prompt"])
    sample_args.negative_prompt = str(manifest_row["negative_prompt"])
    sample_args.seed = int(manifest_row["generation_seed"])
    input_images = load_input_images(manifest_row["image_paths"])
    forwards_per_step = infer_forwards_per_step(sample_args)

    transformer = pipe.transformer
    blocks = list(transformer.transformer_blocks)
    rank_print(
        rank,
        f"sample={int(manifest_row['sample_index']):05d}；"
        f"检测到{len(blocks)}个Block；steps={args.num_inference_steps}；"
        f"forwards_per_step={forwards_per_step}。",
    )

    original_transformer_forward = transformer.forward
    controller = FeatureChangeController(
        transformer_blocks=blocks,
        original_transformer_forward=original_transformer_forward,
        num_inference_steps=args.num_inference_steps,
        forwards_per_step=forwards_per_step,
    )

    start_time = time.time()
    with torch.inference_mode():
        with replace_transformer_forward(transformer, controller):
            # 必须使用当前样本的参数。全局 args 在数据集模式下没有 prompt；
            # 随机抽取的 prompt、negative_prompt 和 generation seed 都保存在
            # sample_args 中。
            output = pipe(**build_pipeline_inputs(input_images, sample_args))
    elapsed_seconds = time.time() - start_time
    controller.validate_complete()

    sample_dir.mkdir(parents=True, exist_ok=True)
    output.images[0].convert("RGB").save(sample_dir / "baseline_full.png")
    write_metrics_csv(
        controller.rows,
        sample_dir / "block_change_metrics.csv",
    )
    matrices = aggregate_matrices(
        rows=controller.rows,
        num_inference_steps=args.num_inference_steps,
        total_layers=len(blocks),
    )
    np.savez_compressed(sample_dir / "block_change_matrices.npz", **matrices)
    save_matrix_plots(
        matrices,
        sample_dir,
        args.heatmap_percentile,
        title_prefix=f"Sample {int(manifest_row['sample_index']):05d}",
    )

    summary = build_summary(
        args=sample_args,
        matrices=matrices,
        total_layers=len(blocks),
        forwards_per_step=forwards_per_step,
        elapsed_seconds=elapsed_seconds,
    )
    summary.update(
        {
            "status": "completed",
            "sample_index": int(manifest_row["sample_index"]),
            "image_paths": list(manifest_row["image_paths"]),
            "image_relative_path": manifest_row["image_relative_path"],
            "prompt_group": manifest_row["prompt_group"],
            "prompt_id": manifest_row["prompt_id"],
            "prompt": manifest_row["prompt"],
            "negative_prompt": manifest_row["negative_prompt"],
        }
    )
    write_json(summary, sample_dir / "summary.json")

    del controller, output, input_images
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return summary


def merge_all_samples(
    output_dir: Path,
    manifest: Sequence[Dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    matrix_sets: List[Dict[str, np.ndarray]] = []
    sample_summaries: List[Dict[str, Any]] = []
    for row in manifest:
        sample_index = int(row["sample_index"])
        sample_dir = output_dir / "samples" / f"{sample_index:05d}"
        matrix_path = sample_dir / "block_change_matrices.npz"
        summary_path = sample_dir / "summary.json"
        if not matrix_path.is_file() or not summary_path.is_file():
            continue
        with np.load(matrix_path) as archive:
            matrix_sets.append({key: archive[key].copy() for key in archive.files})
        with summary_path.open("r", encoding="utf-8") as handle:
            sample_summaries.append(json.load(handle))

    if not matrix_sets:
        raise RuntimeError("没有成功样本，无法生成总体热力图。")

    keys = matrix_sets[0].keys()
    aggregate_matrices_dict = {
        key: np.mean(
            np.stack([matrices[key] for matrices in matrix_sets], axis=0),
            axis=0,
        )
        for key in keys
    }
    np.savez_compressed(
        output_dir / "aggregate_block_change_matrices.npz",
        **aggregate_matrices_dict,
    )
    save_matrix_plots(
        aggregate_matrices_dict,
        output_dir,
        args.heatmap_percentile,
        title_prefix=f"Mean of {len(matrix_sets)} samples",
    )

    combined = aggregate_matrices_dict["combined_relative_l2"]
    step_mean = np.mean(combined, axis=1)
    block_mean = np.mean(combined, axis=0)
    write_json(
        {
            "requested_sample_count": len(manifest),
            "completed_sample_count": len(matrix_sets),
            "failed_sample_count": len(manifest) - len(matrix_sets),
            "aggregation": "arithmetic mean of per-sample, per-branch matrices",
            "highest_change_steps": [
                {
                    "current_step_number_1based": int(index + 2),
                    "combined_block_mean_relative_l2": float(step_mean[index]),
                }
                for index in np.argsort(step_mean)[::-1][:10]
            ],
            "highest_change_blocks": [
                {
                    "block_number_1based": int(index + 1),
                    "combined_step_mean_relative_l2": float(block_mean[index]),
                }
                for index in np.argsort(block_mean)[::-1][:10]
            ],
            "samples": sample_summaries,
        },
        output_dir / "aggregate_summary.json",
    )


def main() -> None:
    args = parse_args()
    validate_args(args)
    rank, local_rank, world_size = initialize_distributed(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        manifest = build_or_load_manifest(
            args=args,
            output_dir=output_dir,
            rank=rank,
            world_size=world_size,
        )
        rank_print(
            rank,
            f"随机测试组数={len(manifest)}；world_size={world_size}；"
            f"本rank设备={args.device}。",
        )
        if args.prepare_only:
            if rank == 0:
                print(f"manifest已经生成：{output_dir / 'manifest.jsonl'}")
            return

        pipe = load_pipeline(args)
        local_rows = [
            row
            for row in manifest
            if int(row["sample_index"]) % world_size == rank
        ]
        for local_position, row in enumerate(local_rows, start=1):
            sample_index = int(row["sample_index"])
            sample_dir = output_dir / "samples" / f"{sample_index:05d}"
            summary_path = sample_dir / "summary.json"
            matrix_path = sample_dir / "block_change_matrices.npz"
            if args.resume and summary_path.is_file() and matrix_path.is_file():
                rank_print(
                    rank,
                    f"跳过已完成sample={sample_index:05d} "
                    f"({local_position}/{len(local_rows)})",
                )
                continue
            try:
                rank_print(
                    rank,
                    f"开始sample={sample_index:05d} "
                    f"({local_position}/{len(local_rows)})；"
                    f"prompt_id={row['prompt_id']}",
                )
                run_one_sample(pipe, args, row, sample_dir, rank)
                rank_print(rank, f"完成sample={sample_index:05d}")
            except Exception as error:
                sample_dir.mkdir(parents=True, exist_ok=True)
                write_json(
                    {
                        "status": "failed",
                        "sample_index": sample_index,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                    sample_dir / "error.json",
                )
                rank_print(
                    rank,
                    f"sample={sample_index:05d}失败："
                    f"{type(error).__name__}: {error}",
                )
                if args.fail_fast:
                    raise

        barrier(world_size)
        if rank == 0:
            merge_all_samples(output_dir, manifest, args)
            print(f"全部完成，总体结果目录：{output_dir.resolve()}", flush=True)
    finally:
        cleanup_distributed(world_size)


if __name__ == "__main__":
    main()
