#!/usr/bin/env bash
set -Eeuo pipefail

# Qwen-Image-Edit 连续 Block 窗口搜索：同层残差缓存 v2
#
# 常用命令：
#   ./run_qwen_window_sweep.sh start
#   ./run_qwen_window_sweep.sh status
#   ./run_qwen_window_sweep.sh log
#   ./run_qwen_window_sweep.sh stop
#
# 所有实验参数都可以在命令前通过环境变量覆盖，例如：
#   SAMPLE_COUNT=2 NUM_INFERENCE_STEPS=4 \
#     ./run_qwen_window_sweep.sh start

ACTION="${1:-start}"
if [[ $# -gt 0 ]]; then
    shift
fi
EXTRA_ARGS=("$@")

PROJECT_ROOT="${PROJECT_ROOT:-/data4/guowenwu/MMDITModelCompression}"
CONDA_ROOT="${CONDA_ROOT:-/data1/miniconda3}"
CONDA_ENV="${CONDA_ENV:-MMDITModelCompression}"

MODEL_PATH="${MODEL_PATH:-${PROJECT_ROOT}/models/Qwen-Image-Edit-2511}"
DATASET_ROOT="${DATASET_ROOT:-${PROJECT_ROOT}/dataset/images1024x1024}"
PROMPT_FILE="${PROMPT_FILE:-${PROJECT_ROOT}/portrait_prompts.md}"

CUDA_DEVICES="${CUDA_DEVICES:-0,1,2,3}"
NPROC_PER_NODE="${NPROC_PER_NODE:-4}"

SAMPLE_COUNT="${SAMPLE_COUNT:-10}"
WINDOW_SIZE_MIN="${WINDOW_SIZE_MIN:-29}"
WINDOW_SIZE_MAX="${WINDOW_SIZE_MAX:-57}"
WINDOW_STRIDE="${WINDOW_STRIDE:-3}"
NUM_INFERENCE_STEPS="${NUM_INFERENCE_STEPS:-40}"
PROGRESS_EVERY="${PROGRESS_EVERY:-25}"

DTYPE="${DTYPE:-bf16}"
IMAGE_FORMAT="${IMAGE_FORMAT:-png}"
RUN_NAME="${RUN_NAME:-residual_cache_v2_n${SAMPLE_COUNT}_steps${NUM_INFERENCE_STEPS}_w${WINDOW_SIZE_MIN}-${WINDOW_SIZE_MAX}_stride${WINDOW_STRIDE}}"

OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/outputs/${RUN_NAME}}"
LOG_FILE="${LOG_FILE:-${PROJECT_ROOT}/logs/${RUN_NAME}.log}"
PID_FILE="${PID_FILE:-${OUTPUT_DIR}/launcher.pid}"
FOLLOW_LOG="${FOLLOW_LOG:-1}"

BATCH_SCRIPT="${PROJECT_ROOT}/qwen_edit_batch_window_sweep.py"
SEARCH_SCRIPT="${PROJECT_ROOT}/qwen_edit_diagonal_bridge_search.py"

die() {
    echo "[ERROR] $*" >&2
    exit 1
}

read_saved_pid() {
    if [[ -f "$PID_FILE" ]]; then
        tr -d '[:space:]' < "$PID_FILE"
    fi
}

pid_is_running() {
    local pid="${1:-}"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

activate_environment() {
    local conda_sh="${CONDA_ROOT}/etc/profile.d/conda.sh"
    [[ -f "$conda_sh" ]] || die "找不到Conda初始化脚本：${conda_sh}"

    # Conda初始化脚本可能读取未定义变量，因此临时关闭nounset。
    set +u
    # shellcheck disable=SC1090
    source "$conda_sh"
    conda activate "$CONDA_ENV"
    set -u
}

validate_paths() {
    [[ -d "$PROJECT_ROOT" ]] || die "工程目录不存在：${PROJECT_ROOT}"
    [[ -f "$BATCH_SCRIPT" ]] || die "批量脚本不存在：${BATCH_SCRIPT}"
    [[ -f "$SEARCH_SCRIPT" ]] || die "搜索脚本不存在：${SEARCH_SCRIPT}"
    [[ -d "$MODEL_PATH" ]] || die "模型目录不存在：${MODEL_PATH}"
    [[ -d "$DATASET_ROOT" ]] || die "数据集目录不存在：${DATASET_ROOT}"
    [[ -f "$PROMPT_FILE" ]] || die "提示词文件不存在：${PROMPT_FILE}"
    grep -q -- '"--progress-every"' "$BATCH_SCRIPT" || die \
        "批量脚本不是带进度日志的新版：${BATCH_SCRIPT}"
    grep -q -- '"--progress-every"' "$SEARCH_SCRIPT" || die \
        "搜索脚本不是带进度日志的新版：${SEARCH_SCRIPT}"
    grep -q -- 'previous_step_same_block_residual_cache_v2' "$SEARCH_SCRIPT" || die \
        "搜索脚本不是同层残差缓存v2：${SEARCH_SCRIPT}"
    grep -q -- 'from qwen_edit_diagonal_bridge_search import' "$BATCH_SCRIPT" || die \
        "批量脚本没有导入同层残差缓存v2：${BATCH_SCRIPT}"
}

print_configuration() {
    echo "============================================================"
    echo "实验名称       : $RUN_NAME"
    echo "缓存策略       : 当前输入 + 上一步同层Block残差"
    echo "CUDA           : $CUDA_DEVICES"
    echo "进程数         : $NPROC_PER_NODE"
    echo "样本数         : $SAMPLE_COUNT"
    echo "窗口长度       : ${WINDOW_SIZE_MIN}..${WINDOW_SIZE_MAX}"
    echo "窗口滑动步长   : $WINDOW_STRIDE"
    echo "推理step数     : $NUM_INFERENCE_STEPS"
    echo "进度输出间隔   : $PROGRESS_EVERY"
    echo "输出目录       : $OUTPUT_DIR"
    echo "日志文件       : $LOG_FILE"
    echo "============================================================"
}

start_run() {
    validate_paths

    local saved_pid
    saved_pid="$(read_saved_pid || true)"
    if pid_is_running "$saved_pid"; then
        die "任务已经运行，PID=${saved_pid}。请使用status或log查看。"
    fi

    local existing_processes
    existing_processes="$(pgrep -af '[q]wen_edit_batch_window_sweep(_residual_cache)?\\.py' || true)"
    if [[ -n "$existing_processes" ]]; then
        echo "[ERROR] 检测到其他窗口搜索任务，避免两套任务争抢GPU，本次未启动：" >&2
        echo "$existing_processes" >&2
        exit 1
    fi

    mkdir -p "$OUTPUT_DIR" "$(dirname "$LOG_FILE")"
    touch "$LOG_FILE"

    activate_environment
    local torchrun_bin
    torchrun_bin="$(command -v torchrun)"
    [[ -x "$torchrun_bin" ]] || die "当前Conda环境中找不到torchrun。"

    local -a command=(
        "$torchrun_bin"
        --standalone
        "--nproc_per_node=${NPROC_PER_NODE}"
        --max_restarts=0
        "$BATCH_SCRIPT"
        --model-path "$MODEL_PATH"
        --dataset-root "$DATASET_ROOT"
        --prompt-file "$PROMPT_FILE"
        --sample-count "$SAMPLE_COUNT"
        --window-size-min "$WINDOW_SIZE_MIN"
        --window-size-max "$WINDOW_SIZE_MAX"
        --window-stride "$WINDOW_STRIDE"
        --num-inference-steps "$NUM_INFERENCE_STEPS"
        --progress-every "$PROGRESS_EVERY"
        --dtype "$DTYPE"
        --image-format "$IMAGE_FORMAT"
        --output-dir "$OUTPUT_DIR"
    )
    command+=("${EXTRA_ARGS[@]}")

    print_configuration
    {
        echo "[$(date '+%F %T')] 启动任务"
        print_configuration
        printf "执行命令       :"
        printf " %q" "${command[@]}"
        printf "\n"
    } >> "$LOG_FILE"

    nohup env \
        CUDA_VISIBLE_DEVICES="$CUDA_DEVICES" \
        PYTHONUNBUFFERED=1 \
        "${command[@]}" \
        >> "$LOG_FILE" 2>&1 < /dev/null &

    local run_pid=$!
    echo "$run_pid" > "$PID_FILE"

    echo "任务已经启动，PID=${run_pid}"
    echo "查看状态：$0 status"
    echo "查看日志：$0 log"
    echo "停止任务：$0 stop"

    if [[ "$FOLLOW_LOG" == "1" ]]; then
        echo
        echo "开始实时显示日志；按Ctrl+C只退出日志查看，不会停止后台任务。"
        tail -n 50 -f "$LOG_FILE"
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
        if [[ -n "$saved_pid" ]]; then
            echo "PID文件中的进程已经不存在：$saved_pid"
        fi
    fi

    echo
    echo "相关进程："
    pgrep -af '[q]wen_edit_batch_window_sweep(_residual_cache)?\\.py|[t]orchrun' || true

    if command -v nvidia-smi >/dev/null 2>&1; then
        echo
        echo "GPU状态："
        nvidia-smi \
            --query-gpu=index,name,utilization.gpu,memory.used,memory.total \
            --format=csv
    fi
}

follow_log() {
    [[ -f "$LOG_FILE" ]] || die "日志文件不存在：${LOG_FILE}"
    tail -n 100 -f "$LOG_FILE"
}

stop_run() {
    local saved_pid
    saved_pid="$(read_saved_pid || true)"
    pid_is_running "$saved_pid" || die "没有找到正在运行的任务。"

    echo "正在向torchrun主进程发送SIGTERM：PID=${saved_pid}"
    kill -TERM "$saved_pid"

    local count
    for count in $(seq 1 30); do
        if ! pid_is_running "$saved_pid"; then
            rm -f "$PID_FILE"
            echo "任务已经正常停止。"
            return
        fi
        sleep 2
    done

    echo "[WARN] 等待60秒后进程仍未退出。没有自动使用SIGKILL。" >&2
    echo "请先运行：$0 status" >&2
    exit 1
}

case "$ACTION" in
    start)
        start_run
        ;;
    status)
        show_status
        ;;
    log|logs|tail)
        follow_log
        ;;
    stop)
        stop_run
        ;;
    *)
        echo "用法：$0 {start|status|log|stop} [额外传给Python脚本的参数...]" >&2
        exit 2
        ;;
esac
