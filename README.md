# Qwen-Image-Edit 连续窗口：同层残差缓存 v2

## 策略

- 第一个 timestep 完整执行 60 个 Block。
- 后续 timestep 强制执行 Block 1、Block 60 和指定长度的连续中间窗口。
- 执行层正常前向，并更新该层双流层内残差：

  ```text
  residual[t, i] = output[t, i] - input[t, i]
  ```

- 跳过层不读取上一 timestep 的绝对输出，而是：

  ```text
  output[t, i] = input[t, i] + residual[t-1, i]
  ```

- text stream 与 image stream 分别维护残差。
- 不使用前后端点残差平均，不做跨层线性插值。

策略版本：

```text
previous_step_same_block_residual_cache_v2
```

## 文件

- `qwen_edit_diagonal_bridge_search.py`：单样本、单窗口长度搜索。
- `qwen_edit_batch_window_sweep.py`：数据集批量窗口扫描。
- `run_qwen_window_sweep.sh`：双 GPU 启动、日志、状态和停止脚本。
- `analyze_qwen_window_sweep_10.py`：多样本联合分析。

## 安装

把文件解压到：

```text
/data4/guowenwu/MMDITModelCompression
```

然后：

```bash
cd /data4/guowenwu/MMDITModelCompression

chmod +x \
  qwen_edit_diagonal_bridge_search.py \
  qwen_edit_batch_window_sweep.py \
  run_qwen_window_sweep.sh
```

不要把新实验输出到旧版绝对缓存实验目录。启动脚本默认使用：

```text
outputs/residual_cache_v2_...
```

## 建议先跑小测试

```bash
cd /data4/guowenwu/MMDITModelCompression

SAMPLE_COUNT=2 \
NUM_INFERENCE_STEPS=4 \
WINDOW_SIZE_MIN=3 \
WINDOW_SIZE_MAX=10 \
WINDOW_STRIDE=3 \
RUN_NAME=residual_cache_v2_smoke \
./run_qwen_window_sweep.sh start
```

查看：

```bash
./run_qwen_window_sweep.sh status
./run_qwen_window_sweep.sh log
```

停止：

```bash
./run_qwen_window_sweep.sh stop
```

## 10 样本、40 step 正式验证

```bash
cd /data4/guowenwu/MMDITModelCompression

SAMPLE_COUNT=10 \
NUM_INFERENCE_STEPS=40 \
WINDOW_SIZE_MIN=3 \
WINDOW_SIZE_MAX=57 \
WINDOW_STRIDE=3 \
PROGRESS_EVERY=25 \
RUN_NAME=residual_cache_v2_n10_steps40_w3-57_stride3 \
./run_qwen_window_sweep.sh start
```

日志默认位于：

```text
logs/residual_cache_v2_n10_steps40_w3-57_stride3.log
```

输出默认位于：

```text
outputs/residual_cache_v2_n10_steps40_w3-57_stride3
```

## 验证新语义是否生效

运行完成后检查：

```bash
grep -R \
  'previous_step_same_block_residual_cache_v2' \
  outputs/residual_cache_v2_n10_steps40_w3-57_stride3 \
  | head
```

候选结果中，同一 step、相同窗口长度、不同起点的 score 不应再只剩
“非末端窗口完全相同、末端窗口另一种值”这两个结果。如果仍然完全相同，
应先停止大规模运行并检查实际执行的 Python 文件是否为本 v2 版本。

## 联合分析

```bash
python analyze_qwen_window_sweep_10.py \
  --input-dir \
  /data4/guowenwu/MMDITModelCompression/outputs/residual_cache_v2_n10_steps40_w3-57_stride3 \
  --expected-samples 10
```
