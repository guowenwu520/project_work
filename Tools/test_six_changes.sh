#!/usr/bin/env bash
set -Eeuo pipefail

# Balanced QA cycles are applied by run_dataset.sh.
# Each change type owns an independent template cycle.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$PROJECT_DIR/Tools/run_dataset.sh"

OUTPUT="${OUTPUT:-$PROJECT_DIR/Output/SixChangeTest_$(date +%Y%m%d_%H%M%S)}"
FPS="${FPS:-10}"
WORKERS="${WORKERS:-1}"
START_INDEX="${START_INDEX:-$((100000 + $(date +%s) % 800000))}"
EXPECTED_SCHEMA="${EXPECTED_SCHEMA:-six-change-tabletop-8qa-60pool-v7}"

change_types=(
  "one_object_replacement"
  "two_objects_replacement"
  "same_object_color_change"
  "distance_increase"
  "swap_positions"
  "no_change"
)

if [[ ! -x "$RUNNER" ]]; then
  echo "Missing executable formal runner: $RUNNER" >&2
  exit 2
fi

rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"

echo "Six-change test started"
echo "  output     : $OUTPUT"
echo "  start index: $START_INDEX"
echo "  fps        : $FPS"
echo

for offset in "${!change_types[@]}"; do
  index=$((START_INDEX + offset))
  change_type="${change_types[$offset]}"

  echo "============================================================"
  echo "[$((offset + 1))/6] $change_type"
  echo "============================================================"

  OUTPUT="$OUTPUT" \
  START_INDEX="$index" \
  COUNT=1 \
  WORKERS="$WORKERS" \
  FPS="$FPS" \
  RANDOM_START=0 \
  CLEAN_OUTPUT=0 \
  RESUME=0 \
  FORCE_CHANGE_TYPE="$change_type" \
  EXPECTED_SCHEMA="$EXPECTED_SCHEMA" \
  "$RUNNER"
done

python3 - "$OUTPUT" "$EXPECTED_SCHEMA" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
expected_schema = sys.argv[2]
expected_types = [
    "one_object_replacement",
    "two_objects_replacement",
    "same_object_color_change",
    "distance_increase",
    "swap_positions",
    "no_change",
]


def fail(message: str) -> None:
    raise SystemExit(f"[six-change validation failed] {message}")


def prop_class(state: object, location: str) -> str:
    if not isinstance(state, dict):
        fail(f"{location} is missing an object state")
    value = str(state.get("propClass", "")).strip()
    if not value:
        fail(f"{location} has no propClass")
    return value.casefold()


def require_unique(values: list[str], location: str) -> None:
    if len(values) != len(set(values)):
        fail(f"{location} contains a repeated object class: {values}")


annotations = sorted(root.glob("Batch_*/annotation.json"))
if len(annotations) != 6:
    fail(f"expected 6 annotations, found {len(annotations)}")

found = {}
for path in annotations:
    annotation = json.loads(path.read_text(encoding="utf-8"))
    change_type = annotation.get("changeType")

    if annotation.get("schemaVersion") != expected_schema:
        fail(f"{path.parent.name} has an unexpected schema")
    if change_type not in expected_types:
        fail(f"unexpected change type {change_type!r}")
    if change_type in found:
        fail(f"duplicate change type {change_type!r}")

    left_before = prop_class(annotation.get("leftBefore"), f"{path.parent.name}.leftBefore")
    right_before = prop_class(annotation.get("rightBefore"), f"{path.parent.name}.rightBefore")
    require_unique([left_before, right_before], path.parent.name)

    if change_type == "one_object_replacement":
        changed_slot = annotation.get("changedSlot")
        changed_after_key = "leftAfter" if changed_slot == "left" else "rightAfter"
        changed_after = prop_class(
            annotation.get(changed_after_key),
            f"{path.parent.name}.{changed_after_key}",
        )
        require_unique(
            [left_before, right_before, changed_after],
            path.parent.name,
        )
    elif change_type == "two_objects_replacement":
        left_after = prop_class(annotation.get("leftAfter"), f"{path.parent.name}.leftAfter")
        right_after = prop_class(annotation.get("rightAfter"), f"{path.parent.name}.rightAfter")
        require_unique(
            [left_before, right_before, left_after, right_after],
            path.parent.name,
        )

    qa = annotation.get("qa")
    if not isinstance(qa, list) or len(qa) != 8:
        fail(f"{path.parent.name} does not contain exactly 8 QA pairs")

    questions = [
        item.get("question", "").strip()
        for item in qa
        if isinstance(item, dict)
    ]
    if len(questions) != 8 or len(set(questions)) != 8:
        fail(f"{path.parent.name} contains invalid or duplicate questions")

    video_path = annotation.get("videoPath")
    if not isinstance(video_path, str) or not (root / video_path).is_file():
        fail(f"{path.parent.name} has no completed video")

    found[change_type] = path.parent.name

missing = [item for item in expected_types if item not in found]
if missing:
    fail("missing change types: " + ", ".join(missing))

videodata_path = root / "videodata.json"
if not videodata_path.is_file():
    fail("videodata.json was not generated")

videodata = json.loads(videodata_path.read_text(encoding="utf-8"))
if not isinstance(videodata, list) or len(videodata) != 6:
    fail("videodata.json must contain exactly 6 video records")

for record in videodata:
    if record.get("scene_type") != "tabletop":
        fail("scene_type must be tabletop")
    questions = record.get("questions")
    if not isinstance(questions, list) or len(questions) != 8:
        fail("each video record must contain exactly 8 questions")

print()
print("[six-change validation passed]")
for change_type in expected_types:
    print(f"  {change_type}: {found[change_type]}")
print(f"  output: {root}")
PY

echo
echo "Six-change test complete: $OUTPUT"
