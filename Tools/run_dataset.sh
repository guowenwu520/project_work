#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLAYER="${PLAYER:-$PROJECT_DIR/Build/Linux/ChangeBlindnessRoom.x86_64}"
OUTPUT="${OUTPUT:-$PROJECT_DIR/Output}"
MODEL_BUNDLE_DIR="${MODEL_BUNDLE_DIR:-$PROJECT_DIR/ModelBundles}"
UNITY_CONFIG_DIR="${UNITY_CONFIG_DIR:-$PROJECT_DIR/.unity_config}"

START_INDEX_WAS_SET=0
if [[ -v START_INDEX ]]; then
  START_INDEX_WAS_SET=1
fi

START_INDEX="${START_INDEX:-0}"
COUNT="${COUNT:-24}"
FPS="${FPS:-30}"
# Timeline: 8 + 3 + 9 + 3 + 8 = 31 seconds.
# Fixed-rate sampling produces duration * FPS frames, without +1.
CAPTURE_DURATION_SECONDS="${CAPTURE_DURATION_SECONDS:-31}"
WIDTH="${WIDTH:-384}"
HEIGHT="${HEIGHT:-384}"
RANDOM_RESOLUTION="${RANDOM_RESOLUTION:-1}"
RESOLUTION_SEED="${RESOLUTION_SEED:-20260718}"
SEED="${SEED:-}"
USE_XVFB="${USE_XVFB:-1}"
DISPLAY_WIDTH="${DISPLAY_WIDTH:-960}"
DISPLAY_HEIGHT="${DISPLAY_HEIGHT:-540}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-10}"
RANDOM_START="${RANDOM_START:-0}"
FORCE_CHANGE_TYPE="${FORCE_CHANGE_TYPE:-}"
FORCE_CHANGED_SLOT="${FORCE_CHANGED_SLOT:-}"
CLEAN_OUTPUT="${CLEAN_OUTPUT:-0}"
DELETE_FRAMES="${DELETE_FRAMES:-1}"
CRF="${CRF:-16}"
PRESET="${PRESET:-medium}"
FFMPEG_LOGLEVEL="${FFMPEG_LOGLEVEL:-warning}"
FFMPEG_THREADS="${FFMPEG_THREADS:-2}"
RESUME="${RESUME:-0}"
WORKERS="${WORKERS:-2}"
UNITY_JOB_WORKERS="${UNITY_JOB_WORKERS:-2}"
CLEAN_ITEM_CONFIG="${CLEAN_ITEM_CONFIG:-1}"
SHOW_OVERALL_PROGRESS="${SHOW_OVERALL_PROGRESS:-1}"

EXPECTED_SCHEMA="${EXPECTED_SCHEMA:-eight-change-tabletop-xlsx-autosync-canonical-slots-metadata-v13}"
BUILD_SCHEMA_FILE="$PROJECT_DIR/Build/Linux/dataset_schema_version.txt"
QA_WORKBOOK="$PROJECT_DIR/QAs_v5_d.xlsx"
QA_LIBRARY="$PROJECT_DIR/Assets/StreamingAssets/tabletop_qa_templates.json"
QA_REGENERATOR="$PROJECT_DIR/Tools/regenerate_existing_qa.py"
PLAYER_QA_LIBRARY="$PROJECT_DIR/Build/Linux/ChangeBlindnessRoom_Data/StreamingAssets/tabletop_qa_templates.json"

resolution_names=("CLIP" "DFN" "SigLIP")
resolution_sizes=(336 378 384)


format_duration() {
  local total_seconds="${1:-0}"
  local hours minutes seconds
  (( total_seconds < 0 )) && total_seconds=0
  hours=$((total_seconds / 3600))
  minutes=$(((total_seconds % 3600) / 60))
  seconds=$((total_seconds % 60))
  printf '%02d:%02d:%02d' "$hours" "$minutes" "$seconds"
}

print_overall_progress() {
  [[ "$SHOW_OVERALL_PROGRESS" == "1" ]] || return 0

  local now elapsed percent_x10 percent_int percent_dec
  local eta_seconds=0 elapsed_text eta_text rate_text

  now="$(date +%s)"
  elapsed=$((now - RUN_START_EPOCH))
  percent_x10=$((finished_items * 1000 / COUNT))
  percent_int=$((percent_x10 / 10))
  percent_dec=$((percent_x10 % 10))

  if (( finished_items > 0 )); then
    eta_seconds=$((elapsed * (COUNT - finished_items) / finished_items))
    rate_text="$(awk -v done="$finished_items" -v seconds="$elapsed" 'BEGIN {
      if (seconds <= 0) {
        printf "calculating"
      } else {
        printf "%.2f videos/min", done * 60.0 / seconds
      }
    }')"
  else
    rate_text="calculating"
  fi

  elapsed_text="$(format_duration "$elapsed")"
  eta_text="$(format_duration "$eta_seconds")"

  printf '[overall] %d/%d (%d.%d%%) | success %d | failed %d | active %d/%d | elapsed %s | rate %s | ETA %s\n'     "$finished_items" "$COUNT" "$percent_int" "$percent_dec"     "$successful_items" "$failed_items"     "$active_workers" "$WORKERS"     "$elapsed_text" "$rate_text" "$eta_text"
}

is_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

pick_resolution_index() {
  local item_index="$1"
  local mixed
  mixed=$(( ((item_index ^ RESOLUTION_SEED) * 1103515245 + 12345) & 0x7fffffff ))
  mixed=$(( (mixed ^ (mixed >> 16)) & 0x7fffffff ))
  echo $((mixed % 3))
}

random_start_index() {
  local now
  now="$(date +%s)"
  echo $((100000 + (now + RANDOM * 32768 + RANDOM) % 800000))
}

require_file() {
  local path="$1"
  local message="$2"
  if [[ ! -f "$path" ]]; then
    echo "$message"
    exit 1
  fi
}

validate_environment() {
  if ! is_positive_integer "$COUNT"; then
    echo "COUNT must be a positive integer: $COUNT"
    exit 2
  fi
  if ! is_positive_integer "$WORKERS"; then
    echo "WORKERS must be a positive integer: $WORKERS"
    exit 2
  fi
  if ! is_positive_integer "$FPS"; then
    echo "FPS must be a positive integer: $FPS"
    exit 2
  fi
  if ! is_positive_integer "$CAPTURE_DURATION_SECONDS"; then
    echo "CAPTURE_DURATION_SECONDS must be a positive integer: $CAPTURE_DURATION_SECONDS"
    exit 2
  fi
  if ! is_positive_integer "$FFMPEG_THREADS"; then
    echo "FFMPEG_THREADS must be a positive integer: $FFMPEG_THREADS"
    exit 2
  fi
  if ! is_positive_integer "$UNITY_JOB_WORKERS"; then
    echo "UNITY_JOB_WORKERS must be a positive integer: $UNITY_JOB_WORKERS"
    exit 2
  fi

  require_file "$BUILD_SCHEMA_FILE" \
    "The Linux Player has no dataset schema marker: $BUILD_SCHEMA_FILE"
  require_file "$QA_LIBRARY" \
    "Missing QA library: $QA_LIBRARY"
  require_file "$QA_REGENERATOR" \
    "Missing balanced QA regenerator: $QA_REGENERATOR"
  require_file "$QA_WORKBOOK" \
    "Missing authoritative QA workbook: $QA_WORKBOOK"

  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to synchronize QAs_v5_d.xlsx."
    exit 1
  fi

  python3 "$QA_REGENERATOR" \
    --workbook "$QA_WORKBOOK" \
    --templates "$QA_LIBRARY" \
    --sync-templates-only

  local build_schema
  build_schema="$(tr -d '\r\n' < "$BUILD_SCHEMA_FILE")"
  if [[ "$EXPECTED_SCHEMA" != "$build_schema" ]]; then
    echo "Player schema mismatch."
    echo "  expected: $EXPECTED_SCHEMA"
    echo "  player  : $build_schema"
    echo "Rebuild the Linux Player from the current Unity project."
    exit 1
  fi

  if [[ ! -x "$PLAYER" ]]; then
    echo "Player not found or not executable: $PLAYER"
    echo "Build the Linux Player from Unity first."
    exit 1
  fi

  require_file "$MODEL_BUNDLE_DIR/prop_manifest.json" \
    "Model bundle manifest not found: $MODEL_BUNDLE_DIR/prop_manifest.json"

  mkdir -p "$(dirname "$PLAYER_QA_LIBRARY")"
  cp -f "$QA_LIBRARY" "$PLAYER_QA_LIBRARY"

  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg is required. Install it with: sudo apt install ffmpeg"
    exit 1
  fi

  if [[ "$USE_XVFB" == "1" ]] && ! command -v xvfb-run >/dev/null 2>&1; then
    echo "xvfb-run is required. Install it with: sudo apt install xvfb"
    exit 1
  fi

}

batch_prefix() {
  printf 'Batch_%06d_' "$1"
}

find_batch_dir() {
  local index="$1"
  local prefix
  prefix="$(batch_prefix "$index")"
  find "$OUTPUT" -mindepth 1 -maxdepth 1 -type d -name "${prefix}*" \
    -printf '%T@\t%p\n' 2>/dev/null \
    | sort -nr \
    | head -n 1 \
    | cut -f2-
}

count_batch_frames() {
  local index="$1"
  local prefix
  prefix="$(batch_prefix "$index")"
  find "$OUTPUT" -type f -path "*/${prefix}*/frames/frame_*.png" -printf '.' 2>/dev/null | wc -c
}

video_path_for_index() {
  printf '%s/data/video_%06d.mp4' "$OUTPUT" "$1"
}

# This variable belongs to one background worker process. Its signal handler uses
# it to stop either Unity/Xvfb or ffmpeg cleanly.
job_child_pid=""

stop_job_child() {
  if [[ -n "$job_child_pid" ]] && kill -0 "$job_child_pid" 2>/dev/null; then
    kill -TERM -- "-$job_child_pid" 2>/dev/null || kill -TERM "$job_child_pid" 2>/dev/null || true
    wait "$job_child_pid" 2>/dev/null || true
  fi
  job_child_pid=""
}

encode_and_cleanup() {
  local index="$1"
  local batch_dir="$2"
  local frames_dir="$batch_dir/frames"
  local output_video temp_video frame_count status

  output_video="$(video_path_for_index "$index")"
  temp_video="${output_video}.tmp.${BASHPID:-$$}.mp4"

  if [[ ! -f "$frames_dir/frame_000000.png" ]]; then
    echo "[item $index] No PNG sequence found: $frames_dir"
    return 1
  fi

  frame_count="$(find "$frames_dir" -maxdepth 1 -type f -name 'frame_*.png' -printf '.' | wc -c)"
  echo "[item $index] Encoding immediately: $frame_count PNG frames -> $output_video"

  rm -f "$temp_video"
  setsid ffmpeg -nostdin -hide_banner -loglevel "$FFMPEG_LOGLEVEL" -y \
    -framerate "$FPS" \
    -i "$frames_dir/frame_%06d.png" \
    -c:v libx264 \
    -threads "$FFMPEG_THREADS" \
    -preset "$PRESET" \
    -crf "$CRF" \
    -pix_fmt yuv420p \
    -movflags +faststart \
    "$temp_video" &
  job_child_pid=$!

  set +e
  wait "$job_child_pid"
  status=$?
  set -e
  job_child_pid=""

  if [[ $status -ne 0 || ! -s "$temp_video" ]]; then
    echo "[item $index] Encoding failed. Frames were kept: $frames_dir"
    rm -f "$temp_video"
    return 1
  fi

  if command -v ffprobe >/dev/null 2>&1; then
    if ! ffprobe -v error -select_streams v:0 \
      -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 \
      "$temp_video" | grep -q .; then
      echo "[item $index] ffprobe verification failed. Frames were kept: $frames_dir"
      rm -f "$temp_video"
      return 1
    fi
  fi

  mv -f "$temp_video" "$output_video"
  echo "[item $index] Video ready: $output_video ($(du -h "$output_video" | awk '{print $1}'))"

  if [[ "$DELETE_FRAMES" == "1" ]]; then
    rm -rf "$frames_dir"
    echo "[item $index] Deleted PNG frames after successful encoding."
  else
    echo "[item $index] PNG frames kept because DELETE_FRAMES=$DELETE_FRAMES"
  fi
}


merge_annotations() {
  local root="$1"

  python3 - "$root" <<'PY'
from __future__ import annotations

import json
import os
import sys
from collections import OrderedDict
from pathlib import Path

EXPECTED_QUESTIONS = 8
root = Path(sys.argv[1]).resolve()
records = OrderedDict()
invalid = 0
missing_video = 0

for path in sorted(root.glob("Batch_*/qa_entries.json")):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        invalid += 1
        print(f"Skipping invalid JSON {path}: {exc}", file=sys.stderr)
        continue

    if not isinstance(payload, dict):
        invalid += 1
        print(f"Skipping non-grouped annotation: {path}", file=sys.stderr)
        continue

    video_id = payload.get("video_id")
    video = payload.get("video")
    video_path = payload.get("video_path")
    scene_type = payload.get("scene_type")
    metadata = payload.get("metadata")
    questions = payload.get("questions")

    if (
        not isinstance(video_id, str)
        or not video_id.strip()
        or not isinstance(video, str)
        or not video.strip()
        or not isinstance(video_path, str)
        or not video_path.strip()
        or scene_type != "tabletop"
        or not isinstance(metadata, dict)
        or not isinstance(questions, list)
        or len(questions) != EXPECTED_QUESTIONS
    ):
        invalid += 1
        print(f"Skipping invalid QA record: {path}", file=sys.stderr)
        continue

    normalized_questions = []
    for item in questions:
        if not isinstance(item, dict):
            normalized_questions = []
            break
        question = item.get("question")
        answer = item.get("answer")
        question_type = item.get("question_type")
        if (
            not isinstance(question, str)
            or not question.strip()
            or not isinstance(answer, str)
            or not answer.strip()
            or question_type not in {
                "descriptive",
                "yes_or_no",
            }
        ):
            normalized_questions = []
            break
        normalized_questions.append(
            {
                "question": question.strip(),
                "answer": answer.strip(),
                "question_type": question_type,
            }
        )

    if (
        len(normalized_questions) != EXPECTED_QUESTIONS
        or len({item["question"] for item in normalized_questions})
        != EXPECTED_QUESTIONS
    ):
        invalid += 1
        print(f"Skipping invalid or duplicate QA set: {path}", file=sys.stderr)
        continue

    normalized_video_path = video_path.replace("\\", "/").lstrip("/")
    normalized_video = video.replace("\\", "/").lstrip("/")
    if normalized_video != normalized_video_path:
        invalid += 1
        print(
            f"Skipping record with inconsistent video paths: {path}",
            file=sys.stderr,
        )
        continue

    required_metadata = {
        "change_type": str,
        "change_exists": bool,
        "view_a_object_count": int,
        "view_b_object_count": int,
        "view_a_position_a": list,
        "view_a_position_b": list,
        "view_b_position_a": list,
        "view_b_position_b": list,
        "view_a_color_a": list,
        "view_a_color_b": list,
        "view_b_color_a": list,
        "view_b_color_b": list,
        "changed_positions": list,
        "object_replaced": bool,
        "object_added": bool,
        "object_removed": bool,
        "color_changed": bool,
        "position_changed": bool,
        "distance_changed": bool,
        "distance_change": str,
    }
    metadata_valid = all(
        key in metadata
        and isinstance(metadata[key], expected_type)
        for key, expected_type in required_metadata.items()
    )
    list_fields = [
        key
        for key, expected_type in required_metadata.items()
        if expected_type is list
    ]
    metadata_valid = metadata_valid and all(
        all(
            isinstance(value, str)
            and value.strip()
            for value in metadata[key]
        )
        for key in list_fields
    )
    if not metadata_valid:
        invalid += 1
        print(f"Skipping invalid metadata: {path}", file=sys.stderr)
        continue

    video_file = root / normalized_video_path
    if not video_file.is_file() or video_file.stat().st_size == 0:
        missing_video += 1
        continue

    records[normalized_video_path] = {
        "video_id": video_id.strip(),
        "video": normalized_video_path,
        "video_path": normalized_video_path,
        "scene_type": "tabletop",
        "metadata": metadata,
        "questions": normalized_questions,
    }

output = root / "videodata.json"
output.parent.mkdir(parents=True, exist_ok=True)
temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
entries = list(records.values())
temporary.write_text(
    json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
os.replace(temporary, output)

print(
    f"Merged {len(entries)} videos with "
    f"{len(entries) * EXPECTED_QUESTIONS} QA pairs -> {output}"
)
if invalid:
    print(f"Skipped invalid QA records: {invalid}", file=sys.stderr)
if missing_video:
    print(f"Skipped records without completed MP4: {missing_video}")
PY
}

run_one_item() (
  set -Eeuo pipefail

  local i="$1"
  local ordinal="$2"
  local current_change_type="$3"
  local current_changed_slot="$4"
  local log_file expected_frames output_video
  local item_width item_height resolution_profile resolution_index
  local item_config player_status batch_dir final_frames batch_frames

  job_child_pid=""
  trap 'stop_job_child; exit 130' INT TERM

  log_file="$OUTPUT/logs/batch_$(printf '%06d' "$i").log"
  expected_frames=$((CAPTURE_DURATION_SECONDS * FPS))
  output_video="$(video_path_for_index "$i")"
  item_config="$UNITY_CONFIG_DIR/jobs/item_$(printf '%06d' "$i")"

  if [[ "$RESUME" == "1" && -s "$output_video" ]]; then
    echo "[item $i][$ordinal/$COUNT] Skipped; video already exists."
    exit 0
  fi

  item_width="$WIDTH"
  item_height="$HEIGHT"
  resolution_profile="custom"
  if [[ "$RANDOM_RESOLUTION" == "1" ]]; then
    resolution_index="$(pick_resolution_index "$i")"
    resolution_profile="${resolution_names[$resolution_index]}"
    item_width="${resolution_sizes[$resolution_index]}"
    item_height="$item_width"
  fi

  rm -rf "$item_config"
  mkdir -p "$item_config/unity3d/ChangeBlindness/ChangeBlindnessRoom"

  echo "[item $i][$ordinal/$COUNT] Starting ${item_width}x${item_height} (${resolution_profile})"
  if [[ -n "$current_change_type" ]]; then
    echo "[item $i] Forced change type: $current_change_type"
  fi
  if [[ -n "$current_changed_slot" ]]; then
    echo "[item $i] Forced changed slot: $current_changed_slot"
  fi
  echo "[item $i] Unity log: $log_file"

  local -a player_args=(
    -batchmode
    -job-worker-count "$UNITY_JOB_WORKERS"
    -screen-fullscreen 0
    -screen-width "$DISPLAY_WIDTH"
    -screen-height "$DISPLAY_HEIGHT"
    -logFile "$log_file"
    --batch-index "$i"
    --capture
    --auto-quit
    --fps "$FPS"
    --width "$item_width"
    --height "$item_height"
    --output "$OUTPUT"
    --model-bundle-dir "$MODEL_BUNDLE_DIR"
  )

  if [[ -n "$SEED" ]]; then
    player_args+=(--seed "$SEED")
  fi
  if [[ -n "$current_change_type" ]]; then
    player_args+=(--change-type "$current_change_type")
  fi
  if [[ -n "$current_changed_slot" ]]; then
    player_args+=(--changed-slot "$current_changed_slot")
  fi

  if [[ "$USE_XVFB" == "1" ]]; then
    setsid env XDG_CONFIG_HOME="$item_config" \
      xvfb-run -a -s "-screen 0 ${DISPLAY_WIDTH}x${DISPLAY_HEIGHT}x24" \
      "$PLAYER" "${player_args[@]}" &
  else
    setsid env XDG_CONFIG_HOME="$item_config" \
      "$PLAYER" "${player_args[@]}" &
  fi
  job_child_pid=$!

  while kill -0 "$job_child_pid" 2>/dev/null; do
    sleep "$PROGRESS_INTERVAL"
    if kill -0 "$job_child_pid" 2>/dev/null; then
      batch_frames="$(count_batch_frames "$i")"
      echo "[item $i] Progress: ${batch_frames}/${expected_frames} PNG frames"
    fi
  done

  set +e
  wait "$job_child_pid"
  player_status=$?
  set -e
  job_child_pid=""

  if [[ $player_status -ne 0 ]]; then
    echo "[item $i] Unity failed with exit code $player_status"
    tail -n 100 "$log_file" 2>/dev/null || true
    exit "$player_status"
  fi

  batch_dir="$(find_batch_dir "$i")"
  if [[ -z "$batch_dir" || ! -d "$batch_dir" ]]; then
    echo "[item $i] Unity exited successfully, but no Batch directory was found."
    tail -n 100 "$log_file" 2>/dev/null || true
    exit 2
  fi

  final_frames="$(find "$batch_dir/frames" -maxdepth 1 -type f -name 'frame_*.png' -printf '.' 2>/dev/null | wc -c)"
  echo "[item $i] Rendered: $final_frames PNG frames"

  if [[ "$final_frames" -lt "$expected_frames" ]]; then
    echo "[item $i] Incomplete render: expected $expected_frames frames, found $final_frames."
    exit 3
  fi

  encode_and_cleanup "$i" "$batch_dir"

  if [[ "$CLEAN_ITEM_CONFIG" == "1" ]]; then
    rm -rf "$item_config"
  fi

  trap - INT TERM
  echo "[item $i][$ordinal/$COUNT] Complete"
)

cleanup_all_workers() {
  local pid
  trap - INT TERM
  echo
  echo "Stopping active dataset workers..."
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    kill -TERM "$pid" 2>/dev/null || true
  done < <(jobs -pr)
  wait 2>/dev/null || true
}

last_worker_status=0
wait_for_one_worker() {
  set +e
  wait -n
  last_worker_status=$?
  set -e

  active_workers=$((active_workers - 1))
  finished_items=$((finished_items + 1))

  if [[ "$last_worker_status" -eq 0 ]]; then
    successful_items=$((successful_items + 1))
  else
    failed_items=$((failed_items + 1))
  fi

  print_overall_progress
}

trap 'cleanup_all_workers; exit 130' INT TERM

validate_environment

if [[ "$RANDOM_START" == "1" && "$START_INDEX_WAS_SET" == "0" ]]; then
  START_INDEX="$(random_start_index)"
fi

if [[ "$WORKERS" -gt "$COUNT" ]]; then
  WORKERS="$COUNT"
fi

if [[ "$CLEAN_OUTPUT" == "1" ]]; then
  rm -rf "$OUTPUT"
fi

mkdir -p \
  "$OUTPUT/data" \
  "$OUTPUT/logs" \
  "$UNITY_CONFIG_DIR/jobs"

SOURCE_SCHEMA="$EXPECTED_SCHEMA"
END_INDEX=$((START_INDEX + COUNT - 1))

cat <<INFO
Dataset run started
  output        : $OUTPUT
  item range    : $START_INDEX .. $END_INDEX
  parallel jobs : $WORKERS
  resolution    : $([[ "$RANDOM_RESOLUTION" == "1" ]] && echo "random: CLIP 336x336 / DFN 378x378 / SigLIP 384x384" || echo "fixed: ${WIDTH}x${HEIGHT}")
  fps           : $FPS
  capture time  : ${CAPTURE_DURATION_SECONDS}s
  expected PNGs : $((CAPTURE_DURATION_SECONDS * FPS))
  encode        : $PRESET / CRF $CRF / ffmpeg threads $FFMPEG_THREADS
  Unity jobs    : $UNITY_JOB_WORKERS per Player
  delete frames : $DELETE_FRAMES
  schema        : $SOURCE_SCHEMA
  changed slot  : ${FORCE_CHANGED_SLOT:-canonical by change type}
  model bundles : $MODEL_BUNDLE_DIR
INFO

RUN_START_EPOCH="$(date +%s)"
active_workers=0
finished_items=0
successful_items=0
failed_items=0

print_overall_progress

for ((i = START_INDEX; i <= END_INDEX; i++)); do
  ordinal=$((i - START_INDEX + 1))
  current_change_type="$FORCE_CHANGE_TYPE"

  run_one_item \
    "$i" \
    "$ordinal" \
    "$current_change_type" \
    "$FORCE_CHANGED_SLOT" &
  active_workers=$((active_workers + 1))

  if [[ "$active_workers" -ge "$WORKERS" ]]; then
    wait_for_one_worker
    if [[ "$last_worker_status" -ne 0 ]]; then
      echo "A dataset worker failed with exit code $last_worker_status. Stopping the remaining workers."
      cleanup_all_workers
      exit "$last_worker_status"
    fi
  fi
done

while [[ "$active_workers" -gt 0 ]]; do
  wait_for_one_worker
  if [[ "$last_worker_status" -ne 0 ]]; then
    echo "A dataset worker failed with exit code $last_worker_status. Stopping the remaining workers."
    cleanup_all_workers
    exit "$last_worker_status"
  fi
done

# Re-select QA across the complete output using an independent balanced
# template cycle for every change type. MP4 and PNG files are untouched.
python3 "$QA_REGENERATOR" \
  "$OUTPUT" \
  --templates "$QA_LIBRARY" \
  --no-backup \
  --require-all-videos

# Final strict validation and deterministic merge.
merge_annotations "$OUTPUT"


trap - INT TERM

RUN_END_EPOCH="$(date +%s)"
RUN_TOTAL_SECONDS=$((RUN_END_EPOCH - RUN_START_EPOCH))

echo
echo "Dataset run complete: $OUTPUT"
echo "Videos: $OUTPUT/data"
echo "QA JSON: $OUTPUT/videodata.json"
echo "Start index used: $START_INDEX"
echo "Parallel jobs used: $WORKERS"
echo "Completed items: $successful_items/$COUNT"
echo "Failed items: $failed_items"
echo "Total elapsed: $(format_duration "$RUN_TOTAL_SECONDS")"
