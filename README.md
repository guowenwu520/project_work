# Qwen-Image-Edit 蓝线缓存策略 v4

这一版保留原来的三个文件名，但不再逐step穷举窗口。

## 实验流程

1. 固定随机抽取100组校准样本和20组独立验证样本，二者不重叠。
2. 100组校准样本全部运行完整模型，统计每个 `step × block`：
   - image层内残差相邻step relative L2、cosine、RMS；
   - text层内残差相邻step relative L2、cosine、RMS。
3. 每个cell默认使用100组中的P90变化值，防止均值掩盖困难样本。
4. image/text分别归一化，实际风险取二者最大值：任一模态不稳定就重算。
5. 每个step把所有高风险Block包含在一个连续计算区间内；区间前后低风险Block使用缓存。这两个边界就是蓝线。
6. Block 1、Block 60必算；step 1完整计算；最后一个step默认也完整计算。
7. 默认同一Block最多连续缓存3步，到期强制重算并刷新残差。
8. 20组验证样本分别生成完整基线和蓝线缓存结果，并保存全部逐step/逐Block细节。

跳过Block仍然采用：

```text
output[t,b] = input[t,b] + residual[last_real_step,b]
```

其中 `last_real_step` 是该Block最近一次真实执行的step。输出文件会明确记录它，不会把连续跳过误写成“上一step真实残差”。

策略版本：

```text
blue_line_profiled_previous_step_same_block_residual_cache_v4
```

## 覆盖文件

把压缩包内三个同名文件覆盖到：

```text
/data4/guowenwu/MMDITModelCompression
```

然后：

```bash
cd /data4/guowenwu/MMDITModelCompression
chmod +x qwen_edit_batch_window_sweep.py \
  qwen_edit_diagonal_bridge_search.py \
  run_qwen_window_sweep.sh
```

## 先做冒烟测试

建议先用4组校准、2组验证、4个step确认环境和输出：

```bash
CUDA_DEVICES=0,1 \
CALIBRATION_COUNT=4 \
VALIDATION_COUNT=2 \
NUM_INFERENCE_STEPS=4 \
RUN_NAME=blue_line_v4_smoke \
./run_qwen_window_sweep.sh start
```

## 正式100+20运行

```bash
CUDA_DEVICES=0,1 \
CALIBRATION_COUNT=100 \
VALIDATION_COUNT=20 \
NUM_INFERENCE_STEPS=40 \
PROFILE_QUANTILE=0.90 \
TARGET_CACHE_RATIO=0.50 \
PROFILE_SMOOTHING_RADIUS=1 \
MAX_CACHE_AGE=3 \
FORCE_FULL_FIRST_STEPS=1 \
FORCE_FULL_LAST_STEPS=1 \
RUN_NAME=blue_line_cache_cal100_val20 \
./run_qwen_window_sweep.sh start
```

`TARGET_CACHE_RATIO=0.50` 表示在生成连续蓝线之前，把风险最低的50% cell视为候选缓存区。由于需要用一个连续计算区覆盖所有高风险Block，并且存在首尾必算和缓存到期刷新，最终实际缓存比例通常低于50%。

如果想测试更保守或更激进的蓝线，必须使用不同的 `RUN_NAME`：

```bash
# 保守
TARGET_CACHE_RATIO=0.35 RUN_NAME=blue_line_target035 ./run_qwen_window_sweep.sh start

# 激进
TARGET_CACHE_RATIO=0.65 RUN_NAME=blue_line_target065 ./run_qwen_window_sweep.sh start
```

## 日志和管理

```bash
./run_qwen_window_sweep.sh status
./run_qwen_window_sweep.sh log
./run_qwen_window_sweep.sh stop
```

停止后可以使用同样参数和同一 `RUN_NAME` 断点续跑。已完成的校准/验证样本不会重跑。

## 输出说明

根目录：

- `manifest.jsonl`：固定的100+20样本、提示词和seed，带calibration/validation划分。
- `run_config.json`：完整运行参数。
- `progress.json`：最终汇总和主要指标。
- `errors_rank_*.jsonl`：失败样本及完整traceback。

`calibration/`：

- `samples/xxxxx/residual_profile.json.gz`：该样本每步每层双流残差变化。
- `samples/xxxxx/baseline_full.png`：校准样本完整模型图像；可用 `SAVE_CALIBRATION_IMAGES=0` 关闭。
- `profile_cell_summary.csv`：100组汇总后的每个step×block均值、中位数、标准差和P90。
- `residual_risk_heatmaps.png`：image、text和归一化联合残差风险热力图。
- `blue_line_schedule.json`：最终蓝线边界、每步执行/跳过层、强制刷新层、计算比例和理论Block加速。
- `blue_line_schedule_matrix.csv`：每行一个step；`block_001...060` 中1=执行、0=缓存；`base_block_*` 是不考虑缓存年龄时的原始蓝线。
- `blue_line_schedule_heatmaps.png`：原始蓝线与加入缓存到期强制刷新后的实际执行矩阵。

`validation/samples/xxxxx/`：

- `baseline_full.png`：完整模型结果。
- `blue_line_cached.png`：蓝线缓存结果。
- `schedule_used.json`：该样本使用的完整静态schedule。
- `step_metrics.csv`：每一步执行/跳过层，以及noise/image token/text token误差。
- `block_actions.csv.gz`：每一步每一层的动作、缓存来源step、缓存年龄、是否因到期强制刷新。
- `final_image_metrics.json`：最终MSE、PSNR、耗时和理论Block加速。
- `complete.json`：该验证样本全部结果入口。

`validation/` 汇总：

- `validation_summary.csv`：20组最终图像指标。
- `step_metrics_summary.csv`：20组逐step误差均值、中位数、标准差和P90。
- `block_action_frequency.csv`：每个step×block的真实执行/缓存频率及缓存年龄。

实测耗时包含详细统计hook，因此主要用于发现异常；公平计算量以 `theoretical_block_speedup` 和 `effective_executed_block_fraction` 为准。
