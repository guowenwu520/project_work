#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="${RUNNER:-$PROJECT_DIR/Tools/run_dataset.sh}"

TEST_FEATURE="${TEST_FEATURE:-changes}"
OUTPUT="${OUTPUT:-}"
FPS="${FPS:-10}"
WORKERS="${WORKERS:-1}"
UNITY_JOB_WORKERS="${UNITY_JOB_WORKERS:-2}"
START_INDEX="${START_INDEX:-$((100000 + $(date +%s) % 700000))}"
EXPECTED_SCHEMA="${EXPECTED_SCHEMA:-eight-change-tabletop-xlsx-autosync-physical-ab-compact-json-v15}"

case "$TEST_FEATURE" in
  changes|camera_route|all)
    ;;
  *)
    echo "TEST_FEATURE must be changes, camera_route, or all: $TEST_FEATURE" >&2
    exit 2
    ;;
esac

[[ -x "$RUNNER" ]] || {
  echo "Missing executable runner: $RUNNER" >&2
  exit 2
}

safe_output_path() {
  local path="$1"
  local resolved
  resolved="$(realpath -m "$path")"
  if [[ "$resolved" == "/" || "$resolved" == "$PROJECT_DIR" ]]; then
    echo "Refusing unsafe output path: $resolved" >&2
    exit 2
  fi
  printf '%s\n' "$resolved"
}

run_case() {
  local output_root="$1"
  local batch_index="$2"
  local change_type="$3"
  local changed_slot="$4"
  local timing_profile="$5"
  local end_angle="$6"
  local route_variant="$7"
  local clean_output="$8"

  OUTPUT="$output_root" \
  START_INDEX="$batch_index" \
  COUNT=1 \
  WORKERS="$WORKERS" \
  UNITY_JOB_WORKERS="$UNITY_JOB_WORKERS" \
  FPS="$FPS" \
  RANDOM_START=0 \
  RANDOM_RESOLUTION=0 \
  WIDTH=336 \
  HEIGHT=336 \
  PRESET="${PRESET:-ultrafast}" \
  CRF="${CRF:-28}" \
  CLEAN_OUTPUT="$clean_output" \
  RESUME=0 \
  DELETE_FRAMES=1 \
  FORCE_CHANGE_TYPE="$change_type" \
  FORCE_CHANGED_SLOT="$changed_slot" \
  FORCE_TIMING_PROFILE="$timing_profile" \
  FORCE_CAMERA_END_ANGLE="$end_angle" \
  FORCE_CAMERA_ROUTE_VARIANT="$route_variant" \
  EXPECTED_SCHEMA="$EXPECTED_SCHEMA" \
  "$RUNNER"
}

validate_common_output() {
  local output_root="$1"
  local expected_count="$2"

  python3 - "$output_root" "$expected_count" <<'PY'
import json
import math
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
expected_count = int(sys.argv[2])
annotations = sorted(root.glob("Batch_*/annotation.json"))
if len(annotations) != expected_count:
    raise SystemExit(
        f"expected {expected_count} Batch annotations, found {len(annotations)}"
    )

if list(root.glob("videodata*.json")):
    raise SystemExit("our test runner must not generate videodata.json")

required = {
    "schemaVersion",
    "batchId",
    "seed",
    "videoPath",
    "changeType",
    "changedSlot",
    "initialObjectCount",
    "finalObjectCount",
    "leftBefore",
    "rightBefore",
    "leftAfter",
    "rightAfter",
    "metadata",
    "timeline",
    "cameraRoute",
    "conversations",
}
forbidden = {"qa", "qaTemplateIds", "qaSchemaVersion", "variables", "canonicalScene"}

for path in annotations:
    annotation = json.loads(path.read_text(encoding="utf-8"))
    missing = required - set(annotation)
    if missing:
        raise SystemExit(f"{path.parent.name}: missing fields {sorted(missing)}")
    extra_qa = forbidden & set(annotation)
    if extra_qa:
        raise SystemExit(f"{path.parent.name}: unexpected QA fields {sorted(extra_qa)}")

    if annotation["conversations"] != []:
        raise SystemExit(f"{path.parent.name}: conversations must be empty")

    for name in ("qa_entries.json", "qa.txt"):
        if (path.parent / name).exists():
            raise SystemExit(f"{path.parent.name}: unexpected {name}")

    video = root / str(annotation["videoPath"])
    if not video.is_file() or video.stat().st_size <= 0:
        raise SystemExit(f"{path.parent.name}: completed video is missing")

    manifest_path = path.parent / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"{path.parent.name}: manifest.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("completed") is not True:
        raise SystemExit(f"{path.parent.name}: manifest is not completed")
    if int(manifest.get("frameCount") or 0) <= 0:
        raise SystemExit(f"{path.parent.name}: invalid frameCount")

    timeline = annotation["timeline"]
    duration = sum(
        float(timeline[key])
        for key in (
            "initialHold",
            "moveAway",
            "hiddenChange",
            "returnDuration",
            "finalHold",
        )
    )
    if not math.isclose(duration, float(timeline["totalDuration"]), abs_tol=1e-3):
        raise SystemExit(f"{path.parent.name}: annotation timeline total mismatch")
    if not math.isclose(duration, float(manifest["durationSeconds"]), abs_tol=2e-3):
        raise SystemExit(f"{path.parent.name}: manifest duration mismatch")

    annotation_route = annotation["cameraRoute"]
    manifest_route = manifest["cameraRoute"]
    for key in ("endAngleDegrees", "routeVariant"):
        if annotation_route[key] != manifest_route[key]:
            raise SystemExit(f"{path.parent.name}: route {key} mismatch")
    for key in ("sweepDegrees", "pathLengthMeters"):
        if not math.isclose(
            float(annotation_route[key]),
            float(manifest_route[key]),
            abs_tol=2e-3,
        ):
            raise SystemExit(f"{path.parent.name}: route {key} mismatch")

    if annotation_route["endAngleDegrees"] not in {45, 90, 135, 180}:
        raise SystemExit(f"{path.parent.name}: unsupported end angle")
    if annotation_route["routeVariant"] not in {1, 2}:
        raise SystemExit(f"{path.parent.name}: unsupported route variant")
    if float(annotation_route["pathLengthMeters"]) <= 0:
        raise SystemExit(f"{path.parent.name}: path length must be positive")
    if len(annotation_route.get("waypoints") or []) < 3:
        raise SystemExit(f"{path.parent.name}: at least three waypoints are required")

print(
    f"Validated {expected_count} scene-only Batches; "
    "videos, dynamic timelines and camera routes are complete."
)
PY
}

validate_change_semantics() {
  local output_root="$1"

  python3 - "$output_root" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected_slots = {
    "one_object_replacement": "left",
    "same_object_color_change": "left",
    "distance_increase": "left",
    "distance_decrease": "left",
    "swap_positions": "both",
    "no_change": "none",
    "object_adding": "right",
    "object_deleting": "right",
}
seen = {}

def present(state):
    return isinstance(state, dict) and bool(state.get("present", True))

for path in sorted(root.glob("Batch_*/annotation.json")):
    annotation = json.loads(path.read_text(encoding="utf-8"))
    change_type = annotation["changeType"]
    if change_type in seen:
        raise SystemExit(f"duplicate change type: {change_type}")
    seen[change_type] = path.parent.name
    if annotation["changedSlot"] != expected_slots.get(change_type):
        raise SystemExit(f"{path.parent.name}: wrong changedSlot")

    lb = annotation["leftBefore"]
    rb = annotation["rightBefore"]
    la = annotation["leftAfter"]
    ra = annotation["rightAfter"]
    if change_type == "object_adding" and (present(rb) or not present(ra)):
        raise SystemExit(f"{path.parent.name}: object addition state mismatch")
    if change_type == "object_deleting" and (not present(rb) or present(ra)):
        raise SystemExit(f"{path.parent.name}: object deletion state mismatch")
    if change_type == "same_object_color_change":
        if lb.get("propClass") != la.get("propClass") or lb.get("color") == la.get("color"):
            raise SystemExit(f"{path.parent.name}: color-change state mismatch")

if set(seen) != set(expected_slots):
    raise SystemExit(
        f"change coverage mismatch: expected {sorted(expected_slots)}, "
        f"found {sorted(seen)}"
    )

print("Eight-change scene coverage passed.")
PY
}

run_changes_test() {
  local output_root="$1"
  local start_index="$2"
  local -a change_types=(
    one_object_replacement
    same_object_color_change
    distance_increase
    distance_decrease
    swap_positions
    no_change
    object_adding
    object_deleting
  )
  local change_type changed_slot clean_output index

  echo "Eight-change annotation/video test"
  echo "  output: $output_root"
  echo

  for index in "${!change_types[@]}"; do
    change_type="${change_types[$index]}"
    case "$change_type" in
      one_object_replacement|same_object_color_change|distance_increase|distance_decrease)
        changed_slot=left
        ;;
      swap_positions)
        changed_slot=both
        ;;
      no_change)
        changed_slot=none
        ;;
      object_adding|object_deleting)
        changed_slot=right
        ;;
    esac
    clean_output=$([[ "$index" -eq 0 ]] && echo 1 || echo 0)
    echo "[$((index + 1))/8] $change_type"
    run_case \
      "$output_root" \
      "$((start_index + index))" \
      "$change_type" \
      "$changed_slot" \
      random \
      random \
      random \
      "$clean_output"
  done

  validate_common_output "$output_root" 8
  validate_change_semantics "$output_root"
  echo "Eight-change test complete: $output_root"
}

validate_camera_route_output() {
  local output_root="$1"
  local start_index="$2"

  python3 - "$output_root" "$start_index" <<'PY'
import json
import math
import sys
from pathlib import Path

root = Path(sys.argv[1])
start = int(sys.argv[2])
expected_routes = [
    (45, 1),
    (45, 2),
    (90, 1),
    (90, 2),
    (135, 1),
    (135, 2),
    (180, 1),
    (180, 2),
]
observed = []
manifests = {}

for offset in range(10):
    batch_id = start + offset
    matches = list(root.glob(f"Batch_{batch_id:06d}_*/manifest.json"))
    if len(matches) != 1:
        raise SystemExit(
            f"batch {batch_id}: expected one manifest, found {len(matches)}"
        )
    manifest = json.loads(matches[0].read_text(encoding="utf-8"))
    manifests[offset] = manifest
    route = manifest["cameraRoute"]
    if offset < 8:
        observed.append(
            (int(route["endAngleDegrees"]), int(route["routeVariant"]))
        )

if observed != expected_routes:
    raise SystemExit(
        f"route coverage mismatch: expected {expected_routes}, found {observed}"
    )

longest_fastest = manifests[8]
shortest_slowest = manifests[9]
long_route = longest_fastest["cameraRoute"]
short_route = shortest_slowest["cameraRoute"]

if (long_route["endAngleDegrees"], long_route["routeVariant"]) != (45, 2):
    raise SystemExit("longest-route case must use 45 degrees, variant 2")
if (short_route["endAngleDegrees"], short_route["routeVariant"]) != (45, 1):
    raise SystemExit("shortest-route case must use 45 degrees, variant 1")
if not float(long_route["pathLengthMeters"]) > float(short_route["pathLengthMeters"]):
    raise SystemExit("the longest route must be longer than the shortest route")

fast = longest_fastest["timeline"]
slow = shortest_slowest["timeline"]
expected_fast = {
    "initialHold": 2,
    "moveAway": 2,
    "awayHold": 2,
    "return": 2,
    "finalHold": 2,
    "swapAt": 5,
}
expected_slow = {
    "initialHold": 10,
    "moveAway": 7,
    "awayHold": 7,
    "return": 7,
    "finalHold": 10,
    "swapAt": 20.5,
}
if fast != expected_fast or not math.isclose(
    float(longest_fastest["durationSeconds"]), 10, abs_tol=1e-3
):
    raise SystemExit(f"longest-route/fastest-time mismatch: {fast}")
if slow != expected_slow or not math.isclose(
    float(shortest_slowest["durationSeconds"]), 41, abs_tol=1e-3
):
    raise SystemExit(f"shortest-route/slowest-time mismatch: {slow}")

print("Camera route test passed:")
print("  4 end angles x 2 routes = 8 route cases")
print("  longest route + shortest time = 1 case")
print("  shortest route + longest time = 1 case")
print("  total = 10 cases")
PY
}

run_camera_route_test() {
  local output_root="$1"
  local start_index="$2"
  local -a angles=(45 45 90 90 135 135 180 180)
  local -a variants=(1 2 1 2 1 2 1 2)
  local index clean_output

  echo "Camera route and timing feature test"
  echo "  route cases : 4 angles x 2 variants = 8"
  echo "  extremes    : longest/fastest + shortest/slowest = 2"
  echo "  total       : 10"
  echo "  output      : $output_root"
  echo

  for index in {0..7}; do
    clean_output=$([[ "$index" -eq 0 ]] && echo 1 || echo 0)
    echo "[$((index + 1))/10] angle=${angles[$index]}, route=${variants[$index]}"
    run_case \
      "$output_root" \
      "$((start_index + index))" \
      no_change \
      none \
      random \
      "${angles[$index]}" \
      "${variants[$index]}" \
      "$clean_output"
  done

  echo "[9/10] longest route + shortest time"
  run_case \
    "$output_root" \
    "$((start_index + 8))" \
    no_change \
    none \
    fastest \
    45 \
    2 \
    0

  echo "[10/10] shortest route + longest time"
  run_case \
    "$output_root" \
    "$((start_index + 9))" \
    no_change \
    none \
    slowest \
    45 \
    1 \
    0

  validate_common_output "$output_root" 10
  validate_camera_route_output "$output_root" "$start_index"
  echo "Camera route test complete: $output_root"
}

if [[ -z "$OUTPUT" ]]; then
  OUTPUT="$PROJECT_DIR/Output/Feature_Test_${TEST_FEATURE}_$(date +%Y%m%d_%H%M%S)"
fi
OUTPUT="$(safe_output_path "$OUTPUT")"

case "$TEST_FEATURE" in
  changes)
    run_changes_test "$OUTPUT" "$START_INDEX"
    ;;
  camera_route)
    run_camera_route_test "$OUTPUT" "$START_INDEX"
    ;;
  all)
    run_changes_test "$OUTPUT/changes" "$START_INDEX"
    run_camera_route_test "$OUTPUT/camera_route" "$((START_INDEX + 100))"
    ;;
esac

echo
echo "Selected feature test complete: $TEST_FEATURE"
echo "No QA or final videodata.json was generated."
