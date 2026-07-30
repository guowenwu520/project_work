#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen-Image-Edit-2511：逐 timestep 搜索连续 Block 执行窗口。

严格执行规则
------------
假设模型共有 60 个 Block：

1. 第一个 timestep 强制完整执行 Block 1-60，并保存每个 Block 的双流输出。
2. 后续 timestep 的 Block 1 和 Block 60 必须执行，候选连续窗口只在
   Block 2-59 中滑动。
3. 如果当前候选窗口为 4-8（下文均用 1-based），则当前 timestep：

       执行：1, 4, 5, 6, 7, 8, 60
       缓存：2, 3, 9, 10, ..., 59

4. 被跳过的 Block 不再复制当前 timestep 的输入，也不使用残差插值，而是
   读取“上一个 timestep 同编号 Block”的双流输出：

       text_out[t, block_i]  = text_out[t-1, block_i]
       image_out[t, block_i] = image_out[t-1, block_i]

每个 CFG 分支维护独立缓存。某层如果连续多个 timestep 都未执行，其缓存会
保持为该层最近一次实际执行时的输出。

搜索过程
--------
1. 第一个 timestep 只完整执行，不搜索候选窗口，并建立所有 Block 缓存。
2. 后续每个 timestep 先执行一次完整教师 Transformer。
3. 在相同 timestep、相同 Transformer 输入上，逐个测试固定长度的连续窗口；
   候选跳过层读取上一个教师 timestep 同编号 Block 的缓存。
4. 候选与完整教师比较：
   - 最后一个 Block 的生成图 image token 相对 MSE；
   - 最后一个 Block 的 text token 相对 MSE；
   - Transformer 噪声预测相对 MSE。
5. 从第二个 timestep 开始选择综合误差最小的窗口。
6. 最后按所有 timestep 的最优窗口重新运行一次完整 pipeline，输出最终图片
   以及相对完整基线的图像误差。

主要输出
--------
- baseline_full.png：完整模型生成结果。
- candidate_scores.csv：每步每个候选的执行/跳过列表和聚合误差。
- candidate_layer_matrix.csv：每个候选 × 每个 Block 的 0/1 执行矩阵。
- candidate_branch_details.json：每个 CFG 分支的原始误差。
- best_schedule.json：逐 timestep 最优窗口、执行/跳过列表和误差。
- best_schedule_layer_matrix.csv：最优路径的 timestep × Block 执行矩阵。
- diagonal_bridge_best.png：按最优路径生成的最终图片。
- final_image_metrics.json：最终图片相对 baseline 的 MAE/MSE/PSNR 等。
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import time
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import torch
from PIL import Image

try:
    from diffusers import QwenImageEditPlusPipeline
except ImportError as import_error:
    raise ImportError(
        "当前环境没有 QwenImageEditPlusPipeline。请在已经配置好的 "
        "MMDITModelCompression Conda 环境中运行本脚本。"
    ) from import_error


TensorPair = Tuple[torch.Tensor, torch.Tensor]  # (text token, image token)
BlockCache = Dict[int, TensorPair]
Window = Tuple[int, int]  # 0-based 闭区间
CACHE_STRATEGY_VERSION = "previous_step_same_block_cache_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "第一个timestep完整计算；后续逐timestep穷举连续中间Block窗口；"
            "首尾Block必算，跳过层读取上一timestep同编号Block缓存。"
        )
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default="/data4/guowenwu/MMDITModelCompression/models/Qwen-Image-Edit-2511",
        help="本地 Qwen-Image-Edit-2511 Diffusers 模型目录。",
    )
    parser.add_argument(
        "--input-image",
        type=str,
        nargs="+",
        required=True,
        help="一张或多张编辑参考图。",
    )
    parser.add_argument("--prompt", type=str, required=True, help="编辑指令。")
    parser.add_argument(
        "--negative-prompt",
        type=str,
        default=" ",
        help="负面提示词；默认是一个空格。",
    )
    parser.add_argument(
        "--num-inference-steps",
        type=int,
        default=4,
        help="去噪步数；当前压缩测试默认 4 步。",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=5,
        help=(
            "每个 timestep 正常执行多少个连续中间 Block。"
            "例如设置5，可以测试4-8这种5层窗口；首尾 Block 不计入这5层。"
        ),
    )
    parser.add_argument(
        "--window-stride",
        type=int,
        default=1,
        help="候选窗口滑动步长；1表示从Block 2开始逐段穷举。",
    )
    parser.add_argument(
        "--true-cfg-scale",
        type=float,
        default=1.0,
        help="Qwen true CFG强度；压缩快速实验默认1.0。",
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
            "每个 timestep 调用 Transformer 的次数。不填写时根据 true CFG 推断；"
            "true_cfg_scale>1 且有 negative_prompt 时推断为2，否则为1。"
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="固定随机种子。")
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
        help="启用Diffusers model CPU offload；穷举搜索会明显变慢。",
    )
    parser.add_argument(
        "--noise-weight",
        type=float,
        default=1.0,
        help="Transformer噪声预测相对MSE的评分权重。",
    )
    parser.add_argument(
        "--image-token-weight",
        type=float,
        default=1.0,
        help="末层生成图image token相对MSE的评分权重。",
    )
    parser.add_argument(
        "--text-token-weight",
        type=float,
        default=0.25,
        help="末层text token相对MSE的评分权重。",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./qwen_edit_diagonal_bridge_outputs",
        help="结果保存目录。",
    )
    parser.add_argument(
        "--skip-final-run",
        action="store_true",
        help="只搜索每步最优窗口，不运行最终组合路径。",
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="显示Diffusers去噪进度条。",
    )
    parser.add_argument(
        "--verbose-candidates",
        action="store_true",
        help="在终端打印每个候选窗口误差；默认只打印每步最优结果。",
    )
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
    model_path = Path(args.model_path)
    if not model_path.is_dir():
        raise FileNotFoundError(f"模型目录不存在：{model_path}")
    for image_path_text in args.input_image:
        image_path = Path(image_path_text)
        if not image_path.is_file():
            raise FileNotFoundError(f"输入图片不存在：{image_path}")
    if args.num_inference_steps <= 0:
        raise ValueError("--num-inference-steps必须大于0。")
    if args.window_size <= 0:
        raise ValueError("--window-size必须大于0。")
    if args.window_stride <= 0:
        raise ValueError("--window-stride必须大于0。")
    if args.progress_every < 0:
        raise ValueError("--progress-every不能小于0。")
    weights = (
        args.noise_weight,
        args.image_token_weight,
        args.text_token_weight,
    )
    if min(weights) < 0:
        raise ValueError("误差权重不能为负数。")
    if sum(weights) <= 0:
        raise ValueError("至少需要一个大于0的误差权重。")
    device = torch.device(args.device)
    if device.type != "cuda":
        raise ValueError("Qwen-Image-Edit-2511测试必须使用CUDA设备。")
    if not torch.cuda.is_available():
        raise RuntimeError("当前Python环境没有检测到CUDA。")


def load_input_images(image_paths: Sequence[str]) -> List[Image.Image]:
    images: List[Image.Image] = []
    for image_path in image_paths:
        with Image.open(image_path) as opened_image:
            images.append(opened_image.convert("RGB").copy())
    return images


def make_generator(seed: int) -> torch.Generator:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    return generator


def infer_forwards_per_step(args: argparse.Namespace) -> int:
    if args.forwards_per_step is not None:
        return int(args.forwards_per_step)
    true_cfg_enabled = args.true_cfg_scale > 1.0 and args.negative_prompt is not None
    return 2 if true_cfg_enabled else 1


def build_candidate_windows(
    total_layers: int,
    window_size: int,
    stride: int,
) -> List[Window]:
    """
    Block 0和Block N-1是强制层，候选窗口只在1..N-2中滑动。
    """
    internal_layers = total_layers - 2
    if internal_layers <= 0:
        raise ValueError(f"模型只有{total_layers}层，无法进行中间层窗口测试。")
    if window_size > internal_layers:
        raise ValueError(
            f"--window-size={window_size}超过中间层总数{internal_layers}。"
        )
    first_start = 1
    last_start = total_layers - 1 - window_size
    starts = list(range(first_start, last_start + 1, stride))
    if not starts:
        raise RuntimeError("没有构造出任何候选窗口。")
    if starts[-1] != last_start:
        starts.append(last_start)
    return [(start, start + window_size - 1) for start in starts]


def executed_layers_for_window(total_layers: int, window: Window) -> Set[int]:
    start_layer, end_layer = window
    return {0, total_layers - 1, *range(start_layer, end_layer + 1)}


def skipped_layers_for_window(total_layers: int, window: Window) -> List[int]:
    executed = executed_layers_for_window(total_layers, window)
    return [layer for layer in range(total_layers) if layer not in executed]


def one_based_layer_string(layers: Iterable[int]) -> str:
    return ",".join(str(layer + 1) for layer in layers)


def read_block_inputs(
    positional_args: Tuple[Any, ...],
    keyword_args: Dict[str, Any],
) -> Tuple[torch.Tensor, torch.Tensor]:
    """返回(image hidden, text hidden)。"""
    hidden_states = keyword_args.get("hidden_states")
    encoder_hidden_states = keyword_args.get("encoder_hidden_states")
    if hidden_states is None and len(positional_args) >= 1:
        hidden_states = positional_args[0]
    if encoder_hidden_states is None and len(positional_args) >= 2:
        encoder_hidden_states = positional_args[1]
    if not isinstance(hidden_states, torch.Tensor):
        raise RuntimeError("无法从Block.forward读取image hidden_states。")
    if not isinstance(encoder_hidden_states, torch.Tensor):
        raise RuntimeError("无法从Block.forward读取text encoder_hidden_states。")
    return hidden_states, encoder_hidden_states


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


@contextmanager
def install_block_policy(
    transformer_blocks: Sequence[torch.nn.Module],
    original_block_forwards: Sequence[Callable[..., Any]],
    executed_layers: Set[int],
    previous_step_cache: Optional[BlockCache] = None,
    capture_all_layers: bool = False,
):
    """
    executed_layers正常计算；其他层读取上一timestep同编号Block的输出。

    capture_all_layers=True时记录本次forward的每层双流输出，用于建立下一
    timestep缓存；否则只记录最后一个Block，供候选误差计算。
    """
    total_layers = len(transformer_blocks)
    last_layer = total_layers - 1
    captured_last: Dict[str, Optional[TensorPair]] = {"tokens": None}
    captured_layers: BlockCache = {}

    if previous_step_cache is not None:
        missing_cached_layers = [
            layer_index
            for layer_index in range(total_layers)
            if (
                layer_index not in executed_layers
                and layer_index not in previous_step_cache
            )
        ]
        if missing_cached_layers:
            raise RuntimeError(
                "上一timestep缓存不完整，缺少Block："
                f"{one_based_layer_string(missing_cached_layers)}。"
                "第一个timestep必须完整执行。"
            )

    def make_policy_forward(layer_index: int):
        original_forward = original_block_forwards[layer_index]

        def policy_forward(_block_self, *positional_args, **keyword_args):
            if layer_index in executed_layers:
                output = original_forward(*positional_args, **keyword_args)
                text_output, image_output = split_block_output(output)
            else:
                if previous_step_cache is None:
                    raise RuntimeError(
                        f"Block {layer_index + 1}需要上一timestep缓存，"
                        "但缓存尚未建立。"
                    )
                text_output, image_output = previous_step_cache[layer_index]
                output = (text_output, image_output)

            detached_tokens = (
                text_output.detach(),
                image_output.detach(),
            )
            if capture_all_layers:
                captured_layers[layer_index] = detached_tokens
            if layer_index == last_layer:
                captured_last["tokens"] = detached_tokens
            return output

        return policy_forward

    try:
        for layer_index, block in enumerate(transformer_blocks):
            block.forward = types.MethodType(make_policy_forward(layer_index), block)
        yield {
            "last_tokens": captured_last,
            "layer_cache": captured_layers,
        }
    finally:
        for block, original_forward in zip(
            transformer_blocks,
            original_block_forwards,
        ):
            block.forward = original_forward


def extract_transformer_sample(output: Any) -> torch.Tensor:
    if hasattr(output, "sample") and isinstance(output.sample, torch.Tensor):
        return output.sample
    if isinstance(output, (tuple, list)) and output:
        if isinstance(output[0], torch.Tensor):
            return output[0]
    if isinstance(output, torch.Tensor):
        return output
    raise RuntimeError("无法从Transformer返回值读取噪声预测sample。")


def relative_mse(reference: torch.Tensor, candidate: torch.Tensor) -> float:
    if reference.shape != candidate.shape:
        raise RuntimeError(
            f"误差Tensor形状不一致：reference={tuple(reference.shape)}，"
            f"candidate={tuple(candidate.shape)}。"
        )
    if reference.device != candidate.device:
        reference = reference.to(candidate.device)
    difference = candidate.float() - reference.float()
    numerator = difference.square().mean()
    denominator = reference.float().square().mean().clamp_min(1e-12)
    return float((numerator / denominator).item())


def score_candidate(
    teacher_output: Any,
    candidate_output: Any,
    teacher_last_tokens: TensorPair,
    candidate_last_tokens: TensorPair,
    args: argparse.Namespace,
) -> Dict[str, float]:
    teacher_text, teacher_image = teacher_last_tokens
    candidate_text, candidate_image = candidate_last_tokens
    teacher_sample = extract_transformer_sample(teacher_output)
    candidate_sample = extract_transformer_sample(candidate_output)

    noise_error = relative_mse(teacher_sample, candidate_sample)

    # 最后一层image stream一般包含“生成图token+参考图token”，而sample通常
    # 只保留生成图部分。形状允许时只比较生成图token，防止参考图token稀释误差。
    if (
        teacher_sample.ndim == 3
        and teacher_image.ndim == 3
        and candidate_image.ndim == 3
        and teacher_sample.shape[1] <= teacher_image.shape[1]
        and teacher_sample.shape[1] <= candidate_image.shape[1]
    ):
        generated_tokens = int(teacher_sample.shape[1])
        teacher_image_for_score = teacher_image[:, :generated_tokens]
        candidate_image_for_score = candidate_image[:, :generated_tokens]
    else:
        teacher_image_for_score = teacher_image
        candidate_image_for_score = candidate_image

    image_token_error = relative_mse(
        teacher_image_for_score,
        candidate_image_for_score,
    )
    text_token_error = relative_mse(teacher_text, candidate_text)
    total_score = (
        args.noise_weight * noise_error
        + args.image_token_weight * image_token_error
        + args.text_token_weight * text_token_error
    )
    return {
        "noise_relative_mse": noise_error,
        "image_token_relative_mse": image_token_error,
        "text_token_relative_mse": text_token_error,
        "score": total_score,
    }


class SearchController:
    """
    在完整教师pipeline内部逐step穷举窗口，但返回教师输出维持完整基线轨迹。

    第一个timestep完整计算并按CFG分支建立全部Block缓存；后续候选的跳过层
    只读取上一教师timestep同分支、同编号Block的缓存。
    """

    def __init__(
        self,
        transformer_blocks: Sequence[torch.nn.Module],
        original_transformer_forward: Callable[..., Any],
        candidates: Sequence[Window],
        args: argparse.Namespace,
        forwards_per_step: int,
    ) -> None:
        self.blocks = list(transformer_blocks)
        self.original_transformer_forward = original_transformer_forward
        self.original_block_forwards = [block.forward for block in self.blocks]
        self.candidates = list(candidates)
        self.args = args
        self.forwards_per_step = forwards_per_step
        self.total_layers = len(self.blocks)
        self.expected_calls = args.num_inference_steps * forwards_per_step
        self.call_index = 0
        self.branch_rows: List[Dict[str, Any]] = []
        self.aggregate_rows: List[Dict[str, Any]] = []
        self.schedule: Dict[int, Dict[str, Any]] = {}
        self.previous_teacher_caches: Dict[int, BlockCache] = {}
        self.progress_every = max(
            0,
            int(getattr(args, "progress_every", 25)),
        )
        self.search_started_at = time.perf_counter()
        self.total_candidate_evaluations = (
            max(0, args.num_inference_steps - 1)
            * forwards_per_step
            * len(self.candidates)
        )

    def _progress_prefix(self) -> str:
        parts = ["search"]
        device = getattr(self.args, "device", None)
        if device:
            parts.append(str(device))
        sample_index = getattr(self.args, "sample_index", None)
        if sample_index is not None:
            parts.append(f"sample {int(sample_index):05d}")
        return "".join(f"[{part}]" for part in parts)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = max(0, int(round(seconds)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _print_candidate_progress(
        self,
        step_index: int,
        branch_index: int,
        candidate_index: int,
    ) -> None:
        if self.progress_every <= 0:
            return
        candidate_count = len(self.candidates)
        if (
            candidate_index != 1
            and candidate_index != candidate_count
            and candidate_index % self.progress_every != 0
        ):
            return

        completed = (
            (
                (step_index - 1) * self.forwards_per_step
                + branch_index
            )
            * candidate_count
            + candidate_index
        )
        total = self.total_candidate_evaluations
        elapsed = time.perf_counter() - self.search_started_at
        percent = 100.0 * completed / total if total else 100.0
        if completed > 0 and total > completed:
            eta = elapsed * (total - completed) / completed
            eta_text = self._format_duration(eta)
        else:
            eta_text = "00:00"

        print(
            f"{self._progress_prefix()} "
            f"step={step_index + 1}/{self.args.num_inference_steps}，"
            f"branch={branch_index + 1}/{self.forwards_per_step}，"
            f"candidate={candidate_index}/{candidate_count}；"
            f"候选总体={completed}/{total} ({percent:.2f}%)；"
            f"已用={self._format_duration(elapsed)}，ETA={eta_text}",
            flush=True,
        )

    def _run_with_policy(
        self,
        positional_args: Tuple[Any, ...],
        keyword_args: Dict[str, Any],
        executed_layers: Set[int],
        previous_step_cache: Optional[BlockCache],
        capture_all_layers: bool,
    ) -> Tuple[Any, TensorPair, BlockCache]:
        with install_block_policy(
            transformer_blocks=self.blocks,
            original_block_forwards=self.original_block_forwards,
            executed_layers=executed_layers,
            previous_step_cache=previous_step_cache,
            capture_all_layers=capture_all_layers,
        ) as captured:
            output = self.original_transformer_forward(
                *positional_args,
                **keyword_args,
            )
        last_tokens = captured["last_tokens"]["tokens"]
        if last_tokens is None:
            raise RuntimeError("没有记录到最后一个Block的token。")
        layer_cache = captured["layer_cache"]
        if capture_all_layers and len(layer_cache) != self.total_layers:
            missing = [
                layer
                for layer in range(self.total_layers)
                if layer not in layer_cache
            ]
            raise RuntimeError(
                "没有记录完整的Block缓存，缺少："
                f"{one_based_layer_string(missing)}"
            )
        return output, last_tokens, layer_cache

    def _full_step_item(self) -> Dict[str, Any]:
        executed = list(range(self.total_layers))
        return {
            "step_index_0based": 0,
            "step_number_1based": 1,
            "window_start_0based": None,
            "window_end_0based": None,
            "window_start_1based": None,
            "window_end_1based": None,
            "window_size": self.total_layers,
            "executed_block_count": self.total_layers,
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

    def _finish_step(self, step_index: int) -> None:
        step_rows = [
            row
            for row in self.branch_rows
            if int(row["step_index_0based"]) == step_index
        ]
        if not step_rows:
            raise RuntimeError(f"step={step_index}没有候选结果。")

        rows_by_window: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
        for row in step_rows:
            key = (
                int(row["window_start_0based"]),
                int(row["window_end_0based"]),
            )
            rows_by_window.setdefault(key, []).append(row)

        aggregates: List[Dict[str, Any]] = []
        for start_layer, end_layer in self.candidates:
            branch_rows = rows_by_window.get((start_layer, end_layer), [])
            if len(branch_rows) != self.forwards_per_step:
                raise RuntimeError(
                    f"step={step_index}、窗口={start_layer}-{end_layer}只有"
                    f"{len(branch_rows)}个分支结果，预期{self.forwards_per_step}个。"
                )

            def mean(field: str) -> float:
                return float(
                    sum(float(row[field]) for row in branch_rows) / len(branch_rows)
                )

            window = (start_layer, end_layer)
            executed = sorted(executed_layers_for_window(self.total_layers, window))
            skipped = skipped_layers_for_window(self.total_layers, window)
            aggregate = {
                "step_index_0based": step_index,
                "step_number_1based": step_index + 1,
                "window_start_0based": start_layer,
                "window_end_0based": end_layer,
                "window_start_1based": start_layer + 1,
                "window_end_1based": end_layer + 1,
                "window_size": end_layer - start_layer + 1,
                "executed_block_count": len(executed),
                "skipped_block_count": len(skipped),
                "executed_blocks_1based": one_based_layer_string(executed),
                "skipped_blocks_1based": one_based_layer_string(skipped),
                "noise_relative_mse": mean("noise_relative_mse"),
                "image_token_relative_mse": mean(
                    "image_token_relative_mse"
                ),
                "text_token_relative_mse": mean(
                    "text_token_relative_mse"
                ),
                "score": mean("score"),
                "selected": False,
                "mode": "previous_timestep_same_block_cache",
                "search_cache_source": "previous_teacher_timestep_same_block",
                "cache_source": "previous_teacher_timestep_same_block",
            }
            aggregates.append(aggregate)

        best = min(
            aggregates,
            key=lambda row: (
                float(row["score"]),
                int(row["window_start_0based"]),
            ),
        )
        best["selected"] = True
        self.aggregate_rows.extend(aggregates)
        self.schedule[step_index] = {
            **best,
            "mode": "previous_timestep_same_block_cache",
            "search_cache_source": "previous_teacher_timestep_same_block",
            "cache_source": "previous_scheduled_timestep_same_block",
        }
        print(
            f"{self._progress_prefix()} "
            f"step={step_index + 1}/{self.args.num_inference_steps}完成；"
            f"最优窗口=Block "
            f"{best['window_start_1based']}-{best['window_end_1based']}；"
            f"执行={best['executed_block_count']}层，"
            f"跳过={best['skipped_block_count']}层；"
            f"score={float(best['score']):.8e}",
            flush=True,
        )

    def __call__(self, *positional_args, **keyword_args):
        if self.call_index >= self.expected_calls:
            raise RuntimeError(
                "Transformer forward次数超过预期。"
                "请显式设置正确的--forwards-per-step。"
            )
        step_index = self.call_index // self.forwards_per_step
        branch_index = self.call_index % self.forwards_per_step
        print(
            f"{self._progress_prefix()} "
            f"开始step={step_index + 1}/{self.args.num_inference_steps}，"
            f"branch={branch_index + 1}/{self.forwards_per_step}；"
            + (
                "完整执行全部Block并建立缓存"
                if step_index == 0
                else f"准备测试{len(self.candidates)}个候选窗口"
            ),
            flush=True,
        )

        teacher_output, teacher_last_tokens, current_teacher_cache = (
            self._run_with_policy(
                positional_args,
                keyword_args,
                executed_layers=set(range(self.total_layers)),
                previous_step_cache=None,
                capture_all_layers=True,
            )
        )

        if step_index == 0:
            self.previous_teacher_caches[branch_index] = current_teacher_cache
            full_item = self._full_step_item()
            self.branch_rows.append(
                {
                    **full_item,
                    "branch_index_0based": branch_index,
                    "branch_number_1based": branch_index + 1,
                }
            )
            self.call_index += 1
            if branch_index == self.forwards_per_step - 1:
                self.schedule[0] = full_item
                print(
                    f"{self._progress_prefix()} "
                    "step=1完整执行结束，逐层缓存已经建立。",
                    flush=True,
                )
            return teacher_output

        previous_step_cache = self.previous_teacher_caches.get(branch_index)
        if previous_step_cache is None:
            raise RuntimeError(
                f"step={step_index + 1}、branch={branch_index + 1}"
                "没有找到上一timestep逐层缓存。"
            )

        for candidate_index, window in enumerate(self.candidates, start=1):
            start_layer, end_layer = window
            executed = executed_layers_for_window(self.total_layers, window)
            skipped = skipped_layers_for_window(self.total_layers, window)
            candidate_output, candidate_last_tokens, _ = self._run_with_policy(
                positional_args,
                keyword_args,
                executed_layers=executed,
                previous_step_cache=previous_step_cache,
                capture_all_layers=False,
            )
            metrics = score_candidate(
                teacher_output=teacher_output,
                candidate_output=candidate_output,
                teacher_last_tokens=teacher_last_tokens,
                candidate_last_tokens=candidate_last_tokens,
                args=self.args,
            )
            row = {
                "step_index_0based": step_index,
                "step_number_1based": step_index + 1,
                "branch_index_0based": branch_index,
                "branch_number_1based": branch_index + 1,
                "window_start_0based": start_layer,
                "window_end_0based": end_layer,
                "window_start_1based": start_layer + 1,
                "window_end_1based": end_layer + 1,
                "window_size": end_layer - start_layer + 1,
                "executed_block_count": len(executed),
                "skipped_block_count": len(skipped),
                "executed_blocks_1based": one_based_layer_string(sorted(executed)),
                "skipped_blocks_1based": one_based_layer_string(skipped),
                "mode": "previous_timestep_same_block_cache",
                "search_cache_source": "previous_teacher_timestep_same_block",
                "cache_source": "previous_teacher_timestep_same_block",
                **metrics,
            }
            self.branch_rows.append(row)
            if self.args.verbose_candidates:
                print(
                    f"  candidate {candidate_index:03d}/{len(self.candidates):03d}: "
                    f"Block {start_layer + 1}-{end_layer + 1}，"
                    f"执行{len(executed)}层，缓存{len(skipped)}层，"
                    f"noise={metrics['noise_relative_mse']:.6e}，"
                    f"image_token={metrics['image_token_relative_mse']:.6e}，"
                    f"text_token={metrics['text_token_relative_mse']:.6e}，"
                    f"score={metrics['score']:.6e}",
                    flush=True,
                )
            else:
                self._print_candidate_progress(
                    step_index=step_index,
                    branch_index=branch_index,
                    candidate_index=candidate_index,
                )
            del candidate_output, candidate_last_tokens

        # 搜索阶段始终沿完整教师轨迹前进。下一timestep候选读取当前教师
        # timestep的逐层缓存，而不是读取任一候选的缓存。
        self.previous_teacher_caches[branch_index] = current_teacher_cache
        self.call_index += 1
        if branch_index == self.forwards_per_step - 1:
            self._finish_step(step_index)

        return teacher_output

    def validate_complete(self) -> None:
        if self.call_index != self.expected_calls:
            raise RuntimeError(
                f"pipeline实际调用Transformer {self.call_index}次，"
                f"预期{self.expected_calls}次。请检查--forwards-per-step。"
            )
        missing_steps = [
            step_index
            for step_index in range(self.args.num_inference_steps)
            if step_index not in self.schedule
        ]
        if missing_steps:
            raise RuntimeError(f"这些timestep没有选出执行策略：{missing_steps}")


class ScheduledController:
    """
    按每个timestep选出的最优窗口执行最终组合路径。

    与搜索不同，最终运行维护的是组合路径自身的逐层缓存：执行层写入新输出，
    跳过层沿用上一timestep同编号Block输出。
    """

    def __init__(
        self,
        transformer_blocks: Sequence[torch.nn.Module],
        original_transformer_forward: Callable[..., Any],
        schedule: Dict[int, Dict[str, Any]],
        args: argparse.Namespace,
        forwards_per_step: int,
    ) -> None:
        self.blocks = list(transformer_blocks)
        self.original_transformer_forward = original_transformer_forward
        self.original_block_forwards = [block.forward for block in self.blocks]
        self.schedule = schedule
        self.args = args
        self.forwards_per_step = forwards_per_step
        self.total_layers = len(self.blocks)
        self.expected_calls = args.num_inference_steps * forwards_per_step
        self.call_index = 0
        self.previous_step_caches: Dict[int, BlockCache] = {}

    def __call__(self, *positional_args, **keyword_args):
        if self.call_index >= self.expected_calls:
            raise RuntimeError("最终路径Transformer forward次数超过预期。")
        step_index = self.call_index // self.forwards_per_step
        branch_index = self.call_index % self.forwards_per_step
        schedule_item = self.schedule[step_index]
        mode = str(schedule_item.get("mode", ""))

        if step_index == 0 or mode == "full_compute":
            executed = set(range(self.total_layers))
            previous_step_cache = None
        else:
            window = (
                int(schedule_item["window_start_0based"]),
                int(schedule_item["window_end_0based"]),
            )
            executed = executed_layers_for_window(self.total_layers, window)
            previous_step_cache = self.previous_step_caches.get(branch_index)
            if previous_step_cache is None:
                raise RuntimeError(
                    f"最终路径step={step_index + 1}、branch={branch_index + 1}"
                    "没有上一timestep缓存。"
                )

        with install_block_policy(
            transformer_blocks=self.blocks,
            original_block_forwards=self.original_block_forwards,
            executed_layers=executed,
            previous_step_cache=previous_step_cache,
            capture_all_layers=True,
        ) as captured:
            output = self.original_transformer_forward(
                *positional_args,
                **keyword_args,
            )

        current_cache = captured["layer_cache"]
        if len(current_cache) != self.total_layers:
            missing = [
                layer
                for layer in range(self.total_layers)
                if layer not in current_cache
            ]
            raise RuntimeError(
                "最终路径没有记录完整缓存，缺少Block："
                f"{one_based_layer_string(missing)}"
            )
        self.previous_step_caches[branch_index] = current_cache
        self.call_index += 1
        return output

    def validate_complete(self) -> None:
        if self.call_index != self.expected_calls:
            raise RuntimeError(
                f"最终路径实际调用Transformer {self.call_index}次，"
                f"预期{self.expected_calls}次。"
            )


@contextmanager
def replace_transformer_forward(
    transformer: torch.nn.Module,
    controller: Callable[..., Any],
):
    original_forward = transformer.forward

    def controlled_forward(_transformer_self, *positional_args, **keyword_args):
        return controller(*positional_args, **keyword_args)

    transformer.forward = types.MethodType(controlled_forward, transformer)
    try:
        yield
    finally:
        transformer.forward = original_forward


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


def generate_image(
    pipe: QwenImageEditPlusPipeline,
    input_images: Sequence[Image.Image],
    args: argparse.Namespace,
) -> Image.Image:
    with torch.inference_mode():
        output = pipe(**build_pipeline_inputs(input_images, args))
    return output.images[0].convert("RGB")


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


def image_metrics(reference: Image.Image, candidate: Image.Image) -> Dict[str, float]:
    reference_array = np.asarray(reference.convert("RGB"), dtype=np.float32) / 255.0
    candidate_rgb = candidate.convert("RGB")
    if candidate_rgb.size != reference.size:
        candidate_rgb = candidate_rgb.resize(reference.size, Image.Resampling.LANCZOS)
    candidate_array = np.asarray(candidate_rgb, dtype=np.float32) / 255.0
    difference = candidate_array - reference_array
    absolute = np.abs(difference)
    mse = float(np.mean(np.square(difference)))
    mae = float(np.mean(absolute))
    rmse = math.sqrt(mse)
    psnr = float("inf") if mse == 0.0 else float(10.0 * math.log10(1.0 / mse))
    changed_ratio = float(np.mean(np.max(absolute, axis=2) > (1.0 / 255.0)))
    return {
        "mae": mae,
        "mse": mse,
        "rmse": rmse,
        "psnr": psnr,
        "changed_ratio": changed_ratio,
    }


def save_json(value: Any, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as json_file:
        json.dump(value, json_file, ensure_ascii=False, indent=2)


def write_candidate_scores(
    rows: Sequence[Dict[str, Any]],
    output_path: Path,
) -> None:
    fieldnames = [
        "mode",
        "search_cache_source",
        "cache_source",
        "step_index_0based",
        "step_number_1based",
        "window_start_0based",
        "window_end_0based",
        "window_start_1based",
        "window_end_1based",
        "window_size",
        "executed_block_count",
        "skipped_block_count",
        "executed_blocks_1based",
        "skipped_blocks_1based",
        "noise_relative_mse",
        "image_token_relative_mse",
        "text_token_relative_mse",
        "score",
        "selected",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def write_candidate_layer_matrix(
    rows: Sequence[Dict[str, Any]],
    total_layers: int,
    output_path: Path,
) -> None:
    """
    每个step的每个候选一行；Block_001..Block_060中1=执行、0=跳过。
    """
    block_fields = [f"block_{layer + 1:03d}" for layer in range(total_layers)]
    fieldnames = [
        "mode",
        "search_cache_source",
        "cache_source",
        "step_number_1based",
        "window_start_1based",
        "window_end_1based",
        "score",
        "selected",
        *block_fields,
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            window = (
                int(row["window_start_0based"]),
                int(row["window_end_0based"]),
            )
            executed = executed_layers_for_window(total_layers, window)
            output_row: Dict[str, Any] = {
                "mode": row.get("mode"),
                "search_cache_source": row.get("cache_source"),
                "cache_source": row.get("cache_source"),
                "step_number_1based": row["step_number_1based"],
                "window_start_1based": row["window_start_1based"],
                "window_end_1based": row["window_end_1based"],
                "score": row["score"],
                "selected": row["selected"],
            }
            for layer in range(total_layers):
                output_row[f"block_{layer + 1:03d}"] = 1 if layer in executed else 0
            writer.writerow(output_row)


def write_best_schedule_matrix(
    schedule: Sequence[Dict[str, Any]],
    total_layers: int,
    output_path: Path,
) -> None:
    block_fields = [f"block_{layer + 1:03d}" for layer in range(total_layers)]
    fieldnames = [
        "mode",
        "search_cache_source",
        "cache_source",
        "step_number_1based",
        "window_start_1based",
        "window_end_1based",
        "noise_relative_mse",
        "image_token_relative_mse",
        "text_token_relative_mse",
        "score",
        *block_fields,
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for item in schedule:
            if item.get("mode") == "full_compute":
                executed = set(range(total_layers))
            else:
                window = (
                    int(item["window_start_0based"]),
                    int(item["window_end_0based"]),
                )
                executed = executed_layers_for_window(total_layers, window)
            row: Dict[str, Any] = {
                "mode": item.get("mode"),
                "search_cache_source": item.get("search_cache_source"),
                "cache_source": item.get("cache_source"),
                "step_number_1based": item["step_number_1based"],
                "window_start_1based": item["window_start_1based"],
                "window_end_1based": item["window_end_1based"],
                "noise_relative_mse": item["noise_relative_mse"],
                "image_token_relative_mse": item[
                    "image_token_relative_mse"
                ],
                "text_token_relative_mse": item[
                    "text_token_relative_mse"
                ],
                "score": item["score"],
            }
            for layer in range(total_layers):
                row[f"block_{layer + 1:03d}"] = 1 if layer in executed else 0
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    validate_args(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    save_json(
        {
            **vars(args),
            "strategy_version": CACHE_STRATEGY_VERSION,
            "first_timestep": "full_compute_all_blocks",
            "skipped_block_policy": "previous_timestep_same_block_cache",
        },
        output_dir / "run_config.json",
    )

    input_images = load_input_images(args.input_image)
    pipe = load_pipeline(args)
    transformer = pipe.transformer
    transformer_blocks = list(transformer.transformer_blocks)
    total_layers = len(transformer_blocks)
    candidates = build_candidate_windows(
        total_layers=total_layers,
        window_size=args.window_size,
        stride=args.window_stride,
    )
    forwards_per_step = infer_forwards_per_step(args)
    normal_executed_count = args.window_size + 2

    print(
        f"模型共{total_layers}个Block；中间候选窗口{len(candidates)}个；"
        "第一个timestep完整执行全部Block；"
        f"每个候选连续执行{args.window_size}个中间Block；"
        f"从第二个timestep起加上首尾后每步共执行{normal_executed_count}层、"
        f"跳过{total_layers - normal_executed_count}层；"
        "跳过层读取上一timestep同编号Block缓存；"
        f"forwards_per_step={forwards_per_step}。",
        flush=True,
    )

    original_transformer_forward = transformer.forward
    search_controller = SearchController(
        transformer_blocks=transformer_blocks,
        original_transformer_forward=original_transformer_forward,
        candidates=candidates,
        args=args,
        forwards_per_step=forwards_per_step,
    )

    print(
        "开始完整教师轨迹：step 1建立全层缓存，step 2起搜索候选……",
        flush=True,
    )
    torch.cuda.synchronize(torch.device(args.device))
    search_start = time.perf_counter()
    with replace_transformer_forward(transformer, search_controller):
        baseline_image = generate_image(pipe, input_images, args)
    torch.cuda.synchronize(torch.device(args.device))
    search_elapsed = time.perf_counter() - search_start
    search_controller.validate_complete()

    baseline_path = output_dir / "baseline_full.png"
    baseline_image.save(baseline_path)

    candidate_scores_path = output_dir / "candidate_scores.csv"
    candidate_matrix_path = output_dir / "candidate_layer_matrix.csv"
    branch_details_path = output_dir / "candidate_branch_details.json"
    write_candidate_scores(
        search_controller.aggregate_rows,
        candidate_scores_path,
    )
    write_candidate_layer_matrix(
        search_controller.aggregate_rows,
        total_layers,
        candidate_matrix_path,
    )
    save_json(search_controller.branch_rows, branch_details_path)

    ordered_schedule = [
        search_controller.schedule[step_index]
        for step_index in range(args.num_inference_steps)
    ]
    best_schedule_path = output_dir / "best_schedule.json"
    best_matrix_path = output_dir / "best_schedule_layer_matrix.csv"
    schedule_payload = {
        "method": (
            "第一个timestep完整执行全部Block；后续Block 1和最后一个Block"
            "始终执行，并执行固定长度的连续中间窗口；其他Block读取上一"
            "timestep同编号Block的text/image双流缓存。"
        ),
        "strategy_version": CACHE_STRATEGY_VERSION,
        "first_timestep": "full_compute_all_blocks",
        "skipped_block_policy": "previous_timestep_same_block_cache",
        "score_formula": (
            f"{args.noise_weight} * noise_relative_mse + "
            f"{args.image_token_weight} * image_token_relative_mse + "
            f"{args.text_token_weight} * text_token_relative_mse"
        ),
        "total_layers": total_layers,
        "num_inference_steps": args.num_inference_steps,
        "window_size": args.window_size,
        "window_stride": args.window_stride,
        "candidate_count_first_timestep": 0,
        "candidate_count_later_timestep": len(candidates),
        "executed_block_count_first_timestep": total_layers,
        "executed_block_count_later_timestep": normal_executed_count,
        "skipped_block_count_first_timestep": 0,
        "skipped_block_count_later_timestep": (
            total_layers - normal_executed_count
        ),
        "forwards_per_step": forwards_per_step,
        "search_elapsed_seconds": search_elapsed,
        "steps": ordered_schedule,
    }
    save_json(schedule_payload, best_schedule_path)
    write_best_schedule_matrix(
        ordered_schedule,
        total_layers,
        best_matrix_path,
    )

    final_path: Optional[Path] = None
    final_metrics: Optional[Dict[str, float]] = None
    final_elapsed: Optional[float] = None
    if not args.skip_final_run:
        print("开始按逐step最优窗口执行最终组合路径……", flush=True)
        scheduled_controller = ScheduledController(
            transformer_blocks=transformer_blocks,
            original_transformer_forward=original_transformer_forward,
            schedule=search_controller.schedule,
            args=args,
            forwards_per_step=forwards_per_step,
        )
        torch.cuda.synchronize(torch.device(args.device))
        final_start = time.perf_counter()
        with replace_transformer_forward(transformer, scheduled_controller):
            final_image = generate_image(pipe, input_images, args)
        torch.cuda.synchronize(torch.device(args.device))
        final_elapsed = time.perf_counter() - final_start
        scheduled_controller.validate_complete()
        final_path = output_dir / "diagonal_bridge_best.png"
        final_image.save(final_path)
        final_metrics = image_metrics(baseline_image, final_image)
        save_json(
            {
                "baseline_image": str(baseline_path.resolve()),
                "candidate_image": str(final_path.resolve()),
                "elapsed_seconds": final_elapsed,
                **final_metrics,
            },
            output_dir / "final_image_metrics.json",
        )

    summary = {
        "baseline_image": str(baseline_path.resolve()),
        "candidate_scores": str(candidate_scores_path.resolve()),
        "candidate_layer_matrix": str(candidate_matrix_path.resolve()),
        "candidate_branch_details": str(branch_details_path.resolve()),
        "best_schedule": str(best_schedule_path.resolve()),
        "best_schedule_layer_matrix": str(best_matrix_path.resolve()),
        "search_elapsed_seconds": search_elapsed,
        "final_image": None if final_path is None else str(final_path.resolve()),
        "final_elapsed_seconds": final_elapsed,
        "final_image_metrics": final_metrics,
    }
    save_json(summary, output_dir / "summary.json")

    print("逐timestep最优结果：", flush=True)
    for item in ordered_schedule:
        if item.get("mode") == "full_compute":
            print(
                f"  step {item['step_number_1based']}: 完整执行全部"
                f"{item['executed_block_count']}个Block并建立缓存。",
                flush=True,
            )
            continue
        print(
            f"  step {item['step_number_1based']}: "
            f"窗口Block {item['window_start_1based']}-"
            f"{item['window_end_1based']}；"
            f"执行={item['executed_blocks_1based']}；"
            f"跳过={item['skipped_blocks_1based']}；"
            f"noise={item['noise_relative_mse']:.6e}；"
            f"image_token={item['image_token_relative_mse']:.6e}；"
            f"text_token={item['text_token_relative_mse']:.6e}；"
            f"score={item['score']:.6e}",
            flush=True,
        )
    if final_metrics is not None:
        print(
            f"最终组合图相对baseline：MAE={final_metrics['mae']:.8f}，"
            f"MSE={final_metrics['mse']:.8f}，"
            f"PSNR={final_metrics['psnr']:.4f} dB。",
            flush=True,
        )
    print(f"全部完成，结果目录：{output_dir.resolve()}", flush=True)

    del pipe
    gc.collect()
    torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
