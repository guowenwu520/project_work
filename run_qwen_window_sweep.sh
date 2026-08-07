#!/usr/bin/env bash
set -Eeuo pipefail

# 蓝线缓存策略 v4：100组校准生成schedule，20组独立验证。
#
#   ./run_qwen_window_sweep.sh start
#   ./run_qwen_window_sweep.sh status
#   ./run_qwen_window_sweep.sh log
#   ./run_qwen_window_sweep.sh stop

ACTION="${1:-start}"
if [[ $# -gt 0 ]]; then shift; fi
EXTRA_ARGS=("$@")

PROJECT_ROOT="${PROJECT_ROOT:-/data4/guowenwu/MMDITModelCompression}"
CONDA_ROOT="${CONDA_ROOT:-/data1/miniconda3}"
CONDA_ENV="${CONDA_ENV:-MMDITModelCompression}"
MODEL_PATH="${MODEL_PATH:-${PROJECT_ROOT}/models/Qwen-Image-Edit-2511}"
DATASET_ROOT="${DATASET_ROOT:-${PROJECT_ROOT}/dataset/images1024x1024}"
PROMPT_FILE="${PROMPT_FILE:-${PROJECT_ROOT}/portrait_prompts.md}"

CUDA_DEVICES="${CUDA_DEVICES:-4,5,6,7}"
IFS=',' read -r -a CUDA_DEVICE_LIST <<< "$CUDA_DEVICES"
NPROC_PER_NODE="${NPROC_PER_NODE:-${#CUDA_DEVICE_LIST[@]}}"

CALIBRATION_COUNT="${CALIBRATION_COUNT:-200}"
VALIDATION_COUNT="${VALIDATION_COUNT:-20}"
NUM_INFERENCE_STEPS="${NUM_INFERENCE_STEPS:-40}"
PROFILE_QUANTILE="${PROFILE_QUANTILE:-0.90}"
TARGET_CACHE_RATIO="${TARGET_CACHE_RATIO:-0.70}"
PROFILE_SMOOTHING_RADIUS="${PROFILE_SMOOTHING_RADIUS:-1}"
MAX_CACHE_AGE="${MAX_CACHE_AGE:-5}"
FORCE_FULL_FIRST_STEPS="${FORCE_FULL_FIRST_STEPS:-1}"
FORCE_FULL_LAST_STEPS="${FORCE_FULL_LAST_STEPS:-1}"
DTYPE="${DTYPE:-bf16}"
IMAGE_FORMAT="${IMAGE_FORMAT:-png}"
SAVE_CALIBRATION_IMAGES="${SAVE_CALIBRATION_IMAGES:-1}"

RUN_NAME="${RUN_NAME:-blue_line_cache_cal${CALIBRATION_COUNT}_val${VALIDATION_COUNT}_steps${NUM_INFERENCE_STEPS}_q${PROFILE_QUANTILE}_target${TARGET_CACHE_RATIO}_age${MAX_CACHE_AGE}}"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/outputs/${RUN_NAME}}"
LOG_FILE="${LOG_FILE:-${PROJECT_ROOT}/logs/${RUN_NAME}.log}"
PID_FILE="${PID_FILE:-${OUTPUT_DIR}/launcher.pid}"
FOLLOW_LOG="${FOLLOW_LOG:-1}"

BATCH_SCRIPT="${PROJECT_ROOT}/qwen_edit_batch_window_sweep.py"
SEARCH_SCRIPT="${PROJECT_ROOT}/qwen_edit_diagonal_bridge_search.py"

die() { echo "[ERROR] $*" >&2; exit 1; }

read_saved_pid() {
    if [[ -f "$PID_FILE" ]]; then tr -d '[:space:]' < "$PID_FILE"; fi
}

pid_is_running() {
    local pid="${1:-}"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

sweep_age() {
    # 校准缓存目录（建议放在输出目录同级，方便复用）
    CALIB_CACHE_DIR="${CALIB_CACHE_DIR:-${PROJECT_ROOT}/outputs/calibration_cache}"

    validate_paths
    mkdir -p "$CALIB_CACHE_DIR" "$(dirname "$LOG_FILE")"

    activate_environment
    local torchrun_bin
    torchrun_bin="$(command -v torchrun)"
    [[ -x "$torchrun_bin" ]] || die "找不到torchrun"

    # ---------- 第一步：生成校准缓存（仅一次） ----------
    echo "========== 开始校准阶段（生成缓存到 $CALIB_CACHE_DIR）=========="
    local -a calib_cmd=(
        "$torchrun_bin"
        --standalone
        "--nproc_per_node=${NPROC_PER_NODE}"
        --max_restarts=0
        "$BATCH_SCRIPT"
        --model-path "$MODEL_PATH"
        --dataset-root "$DATASET_ROOT"
        --prompt-file "$PROMPT_FILE"
        --calibration-count "$CALIBRATION_COUNT"
        --validation-count 0                    # 只校准，不验证
        --num-inference-steps "$NUM_INFERENCE_STEPS"
        --profile-quantile "$PROFILE_QUANTILE"
        --target-cache-ratio "$TARGET_CACHE_RATIO"
        --profile-smoothing-radius "$PROFILE_SMOOTHING_RADIUS"
        --max-cache-age 1                       # 校准时用 1 即可
        --force-full-first-steps "$FORCE_FULL_FIRST_STEPS"
        --force-full-last-steps "$FORCE_FULL_LAST_STEPS"
        --dtype "$DTYPE"
        --image-format "$IMAGE_FORMAT"
        --output-dir "$CALIB_CACHE_DIR"
    )
    if [[ "$SAVE_CALIBRATION_IMAGES" == "0" ]]; then
        calib_cmd+=(--no-save-calibration-images)
    fi
    env CUDA_VISIBLE_DEVICES="$CUDA_DEVICES" PYTHONUNBUFFERED=1 \
        "${calib_cmd[@]}" || die "校准阶段失败"

    echo "校准完成，缓存保存在 $CALIB_CACHE_DIR"

    # ---------- 第二步：循环验证不同 MAX_CACHE_AGE ----------
    for age in $(seq 1 25); do
        echo "========== 验证 MAX_CACHE_AGE = $age =========="
        local run_dir="${PROJECT_ROOT}/outputs/age_${age}"
        mkdir -p "$run_dir"

        local -a val_cmd=(
            "$torchrun_bin"
            --standalone
            "--nproc_per_node=${NPROC_PER_NODE}"
            --max_restarts=0
            "$BATCH_SCRIPT"
            --model-path "$MODEL_PATH"
            --dataset-root "$DATASET_ROOT"
            --prompt-file "$PROMPT_FILE"
            --calibration-count "$CALIBRATION_COUNT"
            --load-calibration-cache "$CALIB_CACHE_DIR"   # 复用缓存
            --validation-count 20                         # 每组 20 个样本
            --num-inference-steps "$NUM_INFERENCE_STEPS"
            --profile-quantile "$PROFILE_QUANTILE"
            --target-cache-ratio "$TARGET_CACHE_RATIO"
            --profile-smoothing-radius "$PROFILE_SMOOTHING_RADIUS"
            --max-cache-age "$age"
            --force-full-first-steps "$FORCE_FULL_FIRST_STEPS"
            --force-full-last-steps "$FORCE_FULL_LAST_STEPS"
            --dtype "$DTYPE"
            --image-format "$IMAGE_FORMAT"
            --output-dir "$run_dir"
        )
        if [[ "$SAVE_CALIBRATION_IMAGES" == "0" ]]; then
            val_cmd+=(--no-save-calibration-images)
        fi
        env CUDA_VISIBLE_DEVICES="$CUDA_DEVICES" PYTHONUNBUFFERED=1 \
            "${val_cmd[@]}" || echo "[WARNING] MAX_CACHE_AGE=$age 验证失败，继续下一个"
    done

    echo "全部 age 扫描完成，结果分别在 outputs/age_{1..25} 中"
}

activate_environment() {
    local conda_sh="${CONDA_ROOT}/etc/profile.d/conda.sh"
    [[ -f "$conda_sh" ]] || die "找不到Conda初始化脚本：${conda_sh}"
    set +u
    # shellcheck disable=SC1090
    source "$conda_sh"
    conda activate "$CONDA_ENV"
    set -u
}

validate_paths() {
    [[ -d "$PROJECT_ROOT" ]] || die "工程目录不存在：${PROJECT_ROOT}"
    [[ -f "$BATCH_SCRIPT" ]] || die "批量脚本不存在：${BATCH_SCRIPT}"
    [[ -f "$SEARCH_SCRIPT" ]] || die "Block策略脚本不存在：${SEARCH_SCRIPT}"
    [[ -d "$MODEL_PATH" ]] || die "模型目录不存在：${MODEL_PATH}"
    [[ -d "$DATASET_ROOT" ]] || die "数据集目录不存在：${DATASET_ROOT}"
    [[ -f "$PROMPT_FILE" ]] || die "提示词文件不存在：${PROMPT_FILE}"
    grep -q -- '"--calibration-count"' "$BATCH_SCRIPT" || die "批量脚本不是蓝线v4。"
    grep -q -- 'BlueLineScheduledController' "$BATCH_SCRIPT" || die "批量脚本缺少蓝线控制器。"
    grep -q -- 'blue_line_profiled_previous_step_same_block_residual_cache_v4' "$SEARCH_SCRIPT" || die "Block策略脚本不是蓝线v4。"
    [[ "$NPROC_PER_NODE" -eq "${#CUDA_DEVICE_LIST[@]}" ]] || die \
        "NPROC_PER_NODE=${NPROC_PER_NODE}，但CUDA_DEVICES有${#CUDA_DEVICE_LIST[@]}张卡。"
    [[ "$SAVE_CALIBRATION_IMAGES" == "0" || "$SAVE_CALIBRATION_IMAGES" == "1" ]] || die \
        "SAVE_CALIBRATION_IMAGES只能为0或1。"
}

print_configuration() {
    echo "============================================================"
    echo "实验名称       : $RUN_NAME"
    echo "缓存策略       : 100组残差变化画像 -> 静态蓝线 -> 20组验证"
    echo "CUDA           : $CUDA_DEVICES"
    echo "进程数         : $NPROC_PER_NODE"
    echo "校准样本       : $CALIBRATION_COUNT"
    echo "验证样本       : $VALIDATION_COUNT"
    echo "推理step       : $NUM_INFERENCE_STEPS"
    echo "跨样本分位数   : $PROFILE_QUANTILE"
    echo "目标缓存比例   : $TARGET_CACHE_RATIO"
    echo "平滑半径       : $PROFILE_SMOOTHING_RADIUS"
    echo "最大缓存年龄   : $MAX_CACHE_AGE"
    echo "首部完整step   : $FORCE_FULL_FIRST_STEPS"
    echo "尾部完整step   : $FORCE_FULL_LAST_STEPS"
    echo "保存校准图像   : $SAVE_CALIBRATION_IMAGES"
    echo "输出目录       : $OUTPUT_DIR"
    echo "日志文件       : $LOG_FILE"
    echo "============================================================"
}

start_run() {
    validate_paths
    local saved_pid existing_processes
    saved_pid="$(read_saved_pid || true)"
    if pid_is_running "$saved_pid"; then
        die "任务已经运行，PID=${saved_pid}。"
    fi
    existing_processes="$(pgrep -af '[q]wen_edit_batch_window_sweep\.py' || true)"
    if [[ -n "$existing_processes" ]]; then
        echo "[ERROR] 检测到其他同名实验，未启动：" >&2
        echo "$existing_processes" >&2
        exit 1
    fi
    mkdir -p "$OUTPUT_DIR" "$(dirname "$LOG_FILE")" "$OUTPUT_DIR/.matplotlib"
    touch "$LOG_FILE"
    activate_environment
    local torchrun_bin
    torchrun_bin="$(command -v torchrun)"
    [[ -x "$torchrun_bin" ]] || die "环境中找不到torchrun。"
    local -a command=(
        "$torchrun_bin"
        --standalone
        "--nproc_per_node=${NPROC_PER_NODE}"
        --max_restarts=0
        "$BATCH_SCRIPT"
        --model-path "$MODEL_PATH"
        --dataset-root "$DATASET_ROOT"
        --prompt-file "$PROMPT_FILE"
        --calibration-count "$CALIBRATION_COUNT"
        --validation-count "$VALIDATION_COUNT"
        --num-inference-steps "$NUM_INFERENCE_STEPS"
        --profile-quantile "$PROFILE_QUANTILE"
        --target-cache-ratio "$TARGET_CACHE_RATIO"
        --profile-smoothing-radius "$PROFILE_SMOOTHING_RADIUS"
        --max-cache-age "$MAX_CACHE_AGE"
        --force-full-first-steps "$FORCE_FULL_FIRST_STEPS"
        --force-full-last-steps "$FORCE_FULL_LAST_STEPS"
        --dtype "$DTYPE"
        --image-format "$IMAGE_FORMAT"
        --output-dir "$OUTPUT_DIR"
    )
    if [[ "$SAVE_CALIBRATION_IMAGES" == "0" ]]; then
        command+=(--no-save-calibration-images)
    fi
    command+=("${EXTRA_ARGS[@]}")
    print_configuration
    {
        echo "[$(date '+%F %T')] 启动任务"
        print_configuration
        printf "执行命令       :"
        printf " %q" "${command[@]}"
        printf "\n"
    } >> "$LOG_FILE"
    nohup env CUDA_VISIBLE_DEVICES="$CUDA_DEVICES" PYTHONUNBUFFERED=1 \
        MPLCONFIGDIR="$OUTPUT_DIR/.matplotlib" \
        "${command[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
    local run_pid=$!
    echo "$run_pid" > "$PID_FILE"
    echo "任务已启动，PID=${run_pid}"
    echo "查看：$0 status"
    echo "日志：$0 log"
    echo "停止：$0 stop"
    if [[ "$FOLLOW_LOG" == "1" ]]; then
        echo "开始实时显示日志；Ctrl+C只退出tail，不会停止任务。"
        tail -n 80 -f "$LOG_FILE"
    fi
}

show_status() {
    local saved_pid
    saved_pid="$(read_saved_pid || true)"
    print_configuration
    if pid_is_running "$saved_pid"; then
        echo "运行状态       : 正在运行"
        ps -p "$saved_pid" -o pid,ppid,stat,etime,time,%cpu,%mem,cmd
    else
        echo "运行状态       : 未运行"
    fi
    echo
    echo "相关进程："
    pgrep -af '[q]wen_edit_batch_window_sweep\.py|[t]orchrun' || true
    if command -v nvidia-smi >/dev/null 2>&1; then
        echo
        nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total --format=csv
    fi
    if [[ -f "$OUTPUT_DIR/progress.json" ]]; then
        echo
        echo "当前汇总：$OUTPUT_DIR/progress.json"
        sed -n '1,120p' "$OUTPUT_DIR/progress.json"
    fi
}

follow_log() {
    [[ -f "$LOG_FILE" ]] || die "日志不存在：${LOG_FILE}"
    tail -n 120 -f "$LOG_FILE"
}

stop_run() {
    local saved_pid count
    saved_pid="$(read_saved_pid || true)"
    pid_is_running "$saved_pid" || die "没有找到正在运行的任务。"
    echo "向torchrun发送SIGTERM：PID=${saved_pid}"
    kill -TERM "$saved_pid"
    for count in $(seq 1 30); do
        if ! pid_is_running "$saved_pid"; then
            rm -f "$PID_FILE"
            echo "任务已停止，已有校准/验证样本可以断点续跑。"
            return
        fi
        sleep 2
    done
    die "等待60秒后仍未退出；未自动使用SIGKILL，请先查看status。"
}

case "$ACTION" in
    start) start_run ;;
    status) show_status ;;
    log|logs|tail) follow_log ;;
    stop) stop_run ;;
    sweep-age) sweep_age ;;          # 新增这一行
    *) echo "用法：$0 {start|status|log|stop} [额外Python参数...]" >&2; exit 2 ;;
esac