#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$PROJECT_DIR/Tools/run_dataset.sh"
REGENERATOR="$PROJECT_DIR/Tools/regenerate_existing_qa.py"
QA_LIBRARY="$PROJECT_DIR/Assets/StreamingAssets/tabletop_qa_templates.json"
SOURCE_WORKBOOK="$PROJECT_DIR/QAs_v5_d.xlsx"
OUTPUT="${OUTPUT:-}"
FPS="${FPS:-10}"
WORKERS="${WORKERS:-2}"
UNITY_JOB_WORKERS="${UNITY_JOB_WORKERS:-2}"
START_INDEX="${START_INDEX:-$((100000 + $(date +%s) % 700000))}"
EXPECTED_SCHEMA="${EXPECTED_SCHEMA:-eight-change-tabletop-xlsx-autosync-canonical-slots-metadata-v13}"
QA_ONLY="${QA_ONLY:-0}"

[[ -f "$SOURCE_WORKBOOK" ]] || {
  echo "Missing authoritative workbook: $SOURCE_WORKBOOK" >&2
  exit 2
}
[[ -x "$REGENERATOR" ]] || {
  echo "Missing executable QA regenerator: $REGENERATOR" >&2
  exit 2
}

python3 "$REGENERATOR" \
  --workbook "$SOURCE_WORKBOOK" \
  --templates "$QA_LIBRARY" \
  --sync-templates-only

[[ -f "$QA_LIBRARY" ]] || {
  echo "Missing generated QA library: $QA_LIBRARY" >&2
  exit 2
}

mapfile -t plan_rows < <(
  python3 - "$QA_LIBRARY" <<'PYPLAN'
import json
import sys
from pathlib import Path

library = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
per_scene = int(library.get("questions_per_scene", 8))
for group in library.get("change_types") or []:
    change_type = str(group.get("change_type") or "")
    count = len(group.get("templates") or [])
    scenes = (count + per_scene - 1) // per_scene
    print(f"{change_type}\t{scenes}")
PYPLAN
)

change_types=()
scene_counts=()
total_scenes=0
for row in "${plan_rows[@]}"; do
  IFS=$'\t' read -r change_type scene_count <<<"$row"
  change_types+=("$change_type")
  scene_counts+=("$scene_count")
  total_scenes=$((total_scenes + scene_count))
done

[[ "${#change_types[@]}" -eq 8 ]] || {
  echo "Expected eight change types, found ${#change_types[@]}" >&2
  exit 2
}
if [[ -z "$OUTPUT" ]]; then
  OUTPUT="$PROJECT_DIR/Output/QA_Exact_Coverage${total_scenes}_$(date +%Y%m%d_%H%M%S)"
fi

total_templates="$(
  python3 - "$QA_LIBRARY" <<'PYTOTAL'
import json
import sys
from pathlib import Path

library = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(int(library.get("total_templates", 0)))
PYTOTAL
)"

resolved_output="$(realpath -m "$OUTPUT")"
if [[ "$resolved_output" == "/" || "$resolved_output" == "$PROJECT_DIR" ]]; then
  echo "Refusing unsafe output path: $resolved_output" >&2
  exit 2
fi

rm -rf -- "$resolved_output"
mkdir -p "$resolved_output/data"
OUTPUT="$resolved_output"

echo "All-cases exact $total_scenes-scene test"
echo "  QA source sheets: 01-08 only"
echo "  templates: $total_templates"
echo "  output: $OUTPUT"
echo "  mode: $([[ "$QA_ONLY" == "1" ]] && echo 'QA-only fast test' || echo \"render $total_scenes videos\")"
echo

if [[ "$QA_ONLY" == "1" ]]; then
  python3 - "$OUTPUT" "$START_INDEX" "$EXPECTED_SCHEMA" "$QA_LIBRARY" <<'PYFAST'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
start = int(sys.argv[2])
schema = sys.argv[3]
library = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
per_scene = int(library.get("questions_per_scene", 8))
plans = [
    (
        group["change_type"],
        (len(group.get("templates") or []) + per_scene - 1)
        // per_scene,
    )
    for group in library.get("change_types") or []
]


def state(
    label: str,
    color: str,
    slot: str,
    *,
    supports_color: bool = True,
) -> dict:
    return {
        "slot": slot,
        "propClass": label.replace(" ", "_"),
        "label": label,
        "color": color,
        "supportsColor": supports_color,
        "present": True,
    }


def absent(slot: str) -> dict:
    return {
        "slot": slot,
        "propClass": "",
        "label": "",
        "color": "",
        "supportsColor": False,
        "present": False,
    }


offset = 0
canonical_slots = {
    "one_object_replacement": "left",
    "same_object_color_change": "left",
    "distance_increase": "left",
    "distance_decrease": "left",
    "swap_positions": "both",
    "no_change": "none",
    "object_adding": "right",
    "object_deleting": "right",
}
for change_type, scene_count in plans:
    for scene_index in range(scene_count):
        batch_id = start + offset
        offset += 1
        batch = root / f"Batch_{batch_id:06d}_{change_type}"
        batch.mkdir(parents=True)
        video_path = f"data/video_{batch_id:06d}.mp4"
        (root / video_path).write_bytes(b"qa-test")

        left_before = state("cup", "red", "left")
        if scene_index == 0:
            right_before = state(
                "camera",
                "",
                "right",
                supports_color=False,
            )
        else:
            right_before = state("bowl", "green", "right")
        left_after = dict(left_before)
        right_after = dict(right_before)
        changed_slot = canonical_slots[change_type]

        if change_type == "one_object_replacement":
            if changed_slot == "left":
                left_after = state("bottle", "blue", "left")
            else:
                right_after = state("box", "yellow", "right")
        elif change_type == "same_object_color_change":
            if changed_slot == "left":
                left_after = state("cup", "blue", "left")
            else:
                right_after = state("bowl", "yellow", "right")
        elif change_type == "swap_positions":
            left_after = dict(right_before, slot="left")
            right_after = dict(left_before, slot="right")
            changed_slot = "both"
        elif change_type == "object_adding":
            if changed_slot == "left":
                left_before = absent("left")
                left_after = state("box", "yellow", "left")
            else:
                right_before = absent("right")
                right_after = state("box", "yellow", "right")
        elif change_type == "object_deleting":
            if changed_slot == "left":
                left_after = absent("left")
            else:
                right_after = absent("right")
        elif change_type == "no_change":
            changed_slot = "none"

        initial_count = sum(
            bool(item.get("present", True))
            for item in (left_before, right_before)
        )
        final_count = sum(
            bool(item.get("present", True))
            for item in (left_after, right_after)
        )
        annotation = {
            "schemaVersion": schema,
            "batchId": batch_id,
            "seed": batch_id * 17 + 3,
            "changeType": change_type,
            "changedSlot": changed_slot,
            "initialObjectCount": initial_count,
            "finalObjectCount": final_count,
            "leftBefore": left_before,
            "rightBefore": right_before,
            "leftAfter": left_after,
            "rightAfter": right_after,
            "videoPath": video_path,
            "qa": [],
        }
        (batch / "annotation.json").write_text(
            json.dumps(annotation, indent=2),
            encoding="utf-8",
        )

expected_offset = sum(scene_count for _, scene_count in plans)
if offset != expected_offset:
    raise SystemExit(
        f"internal plan error: generated {offset} scenes, "
        f"expected {expected_offset}"
    )
PYFAST
else
  [[ -x "$RUNNER" ]] || {
    echo "Missing executable runner: $RUNNER" >&2
    exit 2
  }

  render_batch() {
    local change_type="$1"
    local changed_slot="$2"
    local type_start="$3"
    local scene_count="$4"
    OUTPUT="$OUTPUT" \
    START_INDEX="$type_start" \
    COUNT="$scene_count" \
    WORKERS="$WORKERS" \
    UNITY_JOB_WORKERS="$UNITY_JOB_WORKERS" \
    FPS="$FPS" \
    RANDOM_START=0 \
    RANDOM_RESOLUTION=0 \
    WIDTH=336 \
    HEIGHT=336 \
    PRESET="${PRESET:-ultrafast}" \
    CRF="${CRF:-28}" \
    CLEAN_OUTPUT=0 \
    RESUME=0 \
    DELETE_FRAMES=1 \
    FORCE_CHANGE_TYPE="$change_type" \
    FORCE_CHANGED_SLOT="$changed_slot" \
    EXPECTED_SCHEMA="$EXPECTED_SCHEMA" \
    "$RUNNER"
  }

  offset=0
  for type_index in "${!change_types[@]}"; do
    change_type="${change_types[$type_index]}"
    scene_count="${scene_counts[$type_index]}"
    echo "[$((type_index + 1))/8] Rendering $scene_count scenes: $change_type"
    case "$change_type" in
      one_object_replacement|same_object_color_change|distance_increase|distance_decrease)
        changed_slot="left"
        ;;
      object_adding|object_deleting)
        changed_slot="right"
        ;;
      swap_positions)
        changed_slot="both"
        ;;
      no_change)
        changed_slot="none"
        ;;
      *)
        echo "Unsupported change type: $change_type" >&2
        exit 2
        ;;
    esac
    render_batch \
      "$change_type" \
      "$changed_slot" \
      "$((START_INDEX + offset))" \
      "$scene_count"
    offset=$((offset + scene_count))
  done
fi

python3 "$REGENERATOR" \
  "$OUTPUT" \
  --templates "$QA_LIBRARY" \
  --no-backup \
  --require-all-videos

python3 - \
  "$OUTPUT" \
  "$QA_LIBRARY" \
  "$SOURCE_WORKBOOK" \
  "$EXPECTED_SCHEMA" <<'PYVALIDATE'
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

root = Path(sys.argv[1])
library_path = Path(sys.argv[2])
workbook_path = Path(sys.argv[3])
expected_schema = sys.argv[4]
library = json.loads(library_path.read_text(encoding="utf-8"))
questions_per_scene = int(
    library.get("questions_per_scene", 8)
)
source_sheets = [
    "01_Replacement",
    "02_Color_Change",
    "03_Distance_Increase",
    "04_Distance_Decrease",
    "05_Position_Swap",
    "06_No_Change",
    "07_Object_Adding",
    "08_Object_Deleting",
]
types = [
    "one_object_replacement",
    "same_object_color_change",
    "distance_increase",
    "distance_decrease",
    "swap_positions",
    "no_change",
    "object_adding",
    "object_deleting",
]
expected_counts = {
    change_type: int(
        (library.get("template_counts") or {}).get(
            change_type,
            0,
        )
    )
    for change_type in types
}
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
fixed_values = {
    "view_a_position_a": "the left side (1st view) of the table",
    "view_a_position_b": "the right side (1st view) of the table",
    "view_b_position_a": "the right side (2nd view) of the table",
    "view_b_position_b": "the left side (2nd view) of the table",
}
placeholder_re = re.compile(r"\{([A-Za-z0-9_]+)\}")


def fail(message: str) -> None:
    raise SystemExit(
        "[dynamic sheets-01-08 QA coverage failed] " + message
    )


def state_present(state: object) -> bool:
    return isinstance(state, dict) and bool(
        state.get("present", True)
    )


def state_signature(state: object) -> tuple[object, ...]:
    if not isinstance(state, dict) or not state:
        return (False, "", "", "", False)
    return (
        bool(state.get("present", True)),
        str(state.get("propClass") or "").strip().casefold(),
        str(state.get("label") or "").strip().casefold(),
        str(state.get("color") or "").strip().casefold(),
        bool(state.get("supportsColor", False)),
    )


def state_objects(state: object) -> list[str]:
    if not state_present(state):
        return []
    assert isinstance(state, dict)
    label = str(
        state.get("label")
        or state.get("propClass")
        or ""
    ).strip()
    return [label] if label else []


def state_colors(state: object) -> list[str]:
    if not state_present(state):
        return []
    assert isinstance(state, dict)
    color = str(state.get("color") or "").strip()
    return [color or "Null"]


def expected_metadata(annotation: dict) -> dict:
    change_type = annotation["changeType"]
    changed_slot = annotation["changedSlot"]
    left_before = annotation.get("leftBefore")
    right_before = annotation.get("rightBefore")
    left_after = annotation.get("leftAfter")
    right_after = annotation.get("rightAfter")
    changed_positions = {
        "left": ["position_a"],
        "right": ["position_b"],
        "both": ["position_a", "position_b"],
    }.get(changed_slot, [])
    names = {
        "one_object_replacement": "replacement",
        "same_object_color_change": "color_change",
        "distance_increase": "distance_increase",
        "distance_decrease": "distance_decrease",
        "swap_positions": "position_swap",
        "no_change": "no_change",
        "object_adding": "object_adding",
        "object_deleting": "object_deleting",
    }
    distance_changed = change_type in {
        "distance_increase",
        "distance_decrease",
    }
    return {
        "change_type": names[change_type],
        "change_exists": change_type != "no_change",
        "view_a_object_count": annotation["initialObjectCount"],
        "view_b_object_count": annotation["finalObjectCount"],
        "view_a_position_a": state_objects(left_before),
        "view_a_position_b": state_objects(right_before),
        "view_b_position_a": state_objects(left_after),
        "view_b_position_b": state_objects(right_after),
        "view_a_color_a": state_colors(left_before),
        "view_a_color_b": state_colors(right_before),
        "view_b_color_a": state_colors(left_after),
        "view_b_color_b": state_colors(right_after),
        "changed_positions": changed_positions,
        "object_replaced":
            change_type == "one_object_replacement",
        "object_added": change_type == "object_adding",
        "object_removed": change_type == "object_deleting",
        "color_changed":
            change_type == "same_object_color_change",
        "position_changed": change_type in {
            "distance_increase",
            "distance_decrease",
            "swap_positions",
        },
        "distance_changed": distance_changed,
        "distance_change":
            "increased"
            if change_type == "distance_increase"
            else "decreased"
            if change_type == "distance_decrease"
            else "none",
    }


workbook_sha256 = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
if workbook_sha256 != library.get("source_workbook_sha256"):
    fail(
        "runtime JSON was not generated from the current "
        "QAs_v5_d.xlsx"
    )

if library.get("qa_source_sheets") != source_sheets:
    fail("runtime QA does not declare sheets 01-08 in exact order")
if library.get("variable_source_sheet") != "Variables":
    fail("Variables is not declared as the substitution source")
if library.get("color_missing_value") != "Null":
    fail("missing color value is not the literal Null")

groups = {
    group["change_type"]: group
    for group in library.get("change_types") or []
}
if list(groups) != types:
    fail(f"runtime change-type order is {list(groups)}, expected {types}")

actual_counts = {
    change_type: len(groups[change_type].get("templates") or [])
    for change_type in types
}
if actual_counts != expected_counts:
    fail(f"unexpected sheets-01-08 template counts: {actual_counts}")
if library.get("template_counts") != expected_counts:
    fail("top-level template_counts do not match the eight source sheets")
expected_total_templates = sum(expected_counts.values())
if library.get("total_templates") != expected_total_templates:
    fail(
        "total_templates does not equal the current Sheet 01-08 total"
    )

core = []
expected_templates = {}
template_lookup = {}
typical = library.get("typical_variable_values") or {}
for name, expected in fixed_values.items():
    if typical.get(name) != expected:
        fail(f"{name} typical value is {typical.get(name)!r}")

for change_type in types:
    templates = groups[change_type].get("templates") or []
    expected_templates[change_type] = {
        item["template_id"] for item in templates
    }
    for item in templates:
        if item.get("source_sheet") not in source_sheets:
            fail(f"{item.get('template_id')}: source is not sheet 01-08")
        template_lookup[item["template_id"]] = item
        core.append(
            {
                "change_type": change_type,
                "template_id": item["template_id"],
                "question": item["question"],
                "answer": item["answer"],
                "answer_style": item["answer_style"],
                "source_sheet": item["source_sheet"],
                "source_row": item["source_row"],
            }
        )
        raw = f"{item['question']} {item['answer']}"
        placeholders = list(
            dict.fromkeys(placeholder_re.findall(raw))
        )
        if item.get("required_variables") != placeholders:
            fail(
                f"{item['template_id']}: required_variables do not "
                "match the unchanged sheet wording"
            )
        missing = [name for name in placeholders if not typical.get(name)]
        if missing:
            fail(
                f"{item['template_id']}: Variables lacks {missing}"
            )
        rendered_question = placeholder_re.sub(
            lambda match: typical[match.group(1)],
            item["question"],
        )
        rendered_answer = placeholder_re.sub(
            lambda match: typical[match.group(1)],
            item["answer"],
        )
        if placeholder_re.search(rendered_question + rendered_answer):
            fail(f"{item['template_id']}: typical substitution failed")

qa_payload = json.dumps(
    core,
    ensure_ascii=False,
    separators=(",", ":"),
).encode("utf-8")
qa_sha256 = hashlib.sha256(qa_payload).hexdigest()
if library.get("source_qa_sha256") != qa_sha256:
    fail("source_qa_sha256 metadata does not match the source content")

expected_scene_counts = {
    change_type:
        (template_count + questions_per_scene - 1)
        // questions_per_scene
    for change_type, template_count in expected_counts.items()
}
expected_total_scenes = sum(expected_scene_counts.values())

annotations = sorted(root.glob("Batch_*/annotation.json"))
if len(annotations) != expected_total_scenes:
    fail(
        f"expected exactly {expected_total_scenes} annotations, "
        f"found {len(annotations)}"
    )

scenes = Counter()
slots = defaultdict(set)
appearances = defaultdict(Counter)
annotations_by_video = {}
null_color_seen = False
for path in annotations:
    annotation = json.loads(path.read_text(encoding="utf-8"))
    change_type = annotation.get("changeType")
    if change_type not in types:
        fail(f"{path.parent.name}: invalid change type {change_type!r}")
    scenes[change_type] += 1
    slots[change_type].add(annotation.get("changedSlot"))

    if annotation.get("schemaVersion") != expected_schema:
        fail(f"{path.parent.name}: schema mismatch")

    actual_slot = annotation.get("changedSlot")
    if actual_slot != expected_slots[change_type]:
        fail(
            f"{path.parent.name}: {change_type} must use "
            f"changedSlot={expected_slots[change_type]!r}, got "
            f"{actual_slot!r}"
        )

    left_before = annotation.get("leftBefore")
    right_before = annotation.get("rightBefore")
    left_after = annotation.get("leftAfter")
    right_after = annotation.get("rightAfter")
    lb = state_signature(left_before)
    rb = state_signature(right_before)
    la = state_signature(left_after)
    ra = state_signature(right_after)

    before = [
        item
        for item in (
            left_before,
            right_before,
        )
        if state_present(item)
    ]
    after = [
        item
        for item in (
            left_after,
            right_after,
        )
        if state_present(item)
    ]
    expected_object_counts = {
        "object_adding": (1, 2),
        "object_deleting": (2, 1),
    }.get(change_type, (2, 2))
    actual_object_counts = (len(before), len(after))
    if actual_object_counts != expected_object_counts:
        fail(
            f"{path.parent.name}: object counts "
            f"{actual_object_counts}, expected {expected_object_counts}"
        )

    for view_name, states in (("first", before), ("second", after)):
        classes = [
            str(state.get("propClass", "")).casefold()
            for state in states
        ]
        if any(not value for value in classes):
            fail(
                f"{path.parent.name}: {view_name} view has "
                "an empty propClass"
            )
        if len(classes) != len(set(classes)):
            fail(
                f"{path.parent.name}: {view_name} view repeats "
                "an object class"
            )

    if change_type == "one_object_replacement":
        if lb[1] == la[1] or rb != ra:
            fail(
                f"{path.parent.name}: only first-view left "
                "object A may be replaced"
            )
    elif change_type == "same_object_color_change":
        if lb[1] != la[1] or lb[3] == la[3] or rb != ra:
            fail(
                f"{path.parent.name}: only first-view left "
                "object A's color may change"
            )
    elif change_type in {"distance_increase", "distance_decrease"}:
        if lb != la or rb != ra:
            fail(
                f"{path.parent.name}: distance change must move "
                "first-view left object A without changing either "
                "object state"
            )
    elif change_type == "object_adding":
        if (
            not state_present(left_before)
            or not state_present(left_after)
            or lb != la
            or state_present(right_before)
            or not state_present(right_after)
        ):
            fail(
                f"{path.parent.name}: object B must be added on the "
                "first-view right / second-view left"
            )
    elif change_type == "object_deleting":
        if (
            not state_present(left_before)
            or not state_present(left_after)
            or lb != la
            or not state_present(right_before)
            or state_present(right_after)
        ):
            fail(
                f"{path.parent.name}: object B must be removed from "
                "the first-view right / second-view left"
            )
    elif change_type == "swap_positions":
        if lb != ra or rb != la:
            fail(
                f"{path.parent.name}: the two objects did not "
                "exchange physical slots"
            )
    elif change_type == "no_change":
        if lb != la or rb != ra:
            fail(
                f"{path.parent.name}: no-change altered an object state"
            )

    qa = annotation.get("qa")
    template_ids = annotation.get("qaTemplateIds")
    if (
        not isinstance(qa, list)
        or len(qa) != questions_per_scene
    ):
        fail(
            f"{path.parent.name}: QA count is not "
            f"{questions_per_scene}"
        )
    if (
        not isinstance(template_ids, list)
        or len(template_ids) != questions_per_scene
        or len(set(template_ids)) != questions_per_scene
    ):
        fail(f"{path.parent.name}: template-id count is invalid")

    questions = [
        item.get("question", "").strip()
        for item in qa
        if isinstance(item, dict)
    ]
    if (
        len(questions) != questions_per_scene
        or len(set(questions)) != questions_per_scene
    ):
        fail(f"{path.parent.name}: questions are not unique")

    for pair, template_id in zip(qa, template_ids):
        if template_id not in expected_templates[change_type]:
            fail(f"{path.parent.name}: unknown id {template_id}")
        if pair.get("question_type") not in {
            "descriptive",
            "yes_or_no",
        }:
            fail(
                f"{path.parent.name}: invalid question_type "
                f"{pair.get('question_type')!r}"
            )
        combined = (
            pair.get("question", "").strip()
            + " "
            + pair.get("answer", "").strip()
        )
        if placeholder_re.search(combined):
            fail(f"{path.parent.name}: unresolved placeholder")
        source_style = template_lookup[template_id]["answer_style"]
        expected_question_type = (
            "yes_or_no"
            if source_style in {"yes_no", "yes_or_no"}
            else "descriptive"
        )
        if pair["question_type"] != expected_question_type:
            fail(
                f"{path.parent.name}: {template_id} question_type "
                "does not match the source sheet"
            )
        appearances[change_type][template_id] += 1

    metadata = annotation.get("metadata")
    expected_scene_metadata = expected_metadata(annotation)
    if metadata != expected_scene_metadata:
        fail(
            f"{path.parent.name}: metadata does not describe the "
            "complete two-view scene state"
        )
    if any(
        "Null" in metadata[key]
        for key in (
            "view_a_color_a",
            "view_a_color_b",
            "view_b_color_a",
            "view_b_color_b",
        )
    ):
        null_color_seen = True

    video_path = annotation.get("videoPath")
    annotations_by_video[video_path] = annotation

if not null_color_seen:
    fail("the test did not cover a present object with color=Null")

for change_type in types:
    expected_scene_count = expected_scene_counts[change_type]
    if scenes[change_type] != expected_scene_count:
        fail(
            f"{change_type}: expected {expected_scene_count} scenes, "
            f"got {scenes[change_type]}"
        )
    missing = (
        expected_templates[change_type]
        - set(appearances[change_type])
    )
    if missing:
        fail(f"{change_type}: missing {sorted(missing)}")

    template_count = expected_counts[change_type]
    repeated = (
        expected_scene_count * questions_per_scene
        - template_count
    )
    expected_distribution = Counter(
        {1: template_count - repeated}
    )
    if repeated:
        expected_distribution[2] = repeated
    actual_distribution = Counter(
        appearances[change_type].values()
    )
    if actual_distribution != expected_distribution:
        fail(
            f"{change_type}: occurrence distribution "
            f"{dict(actual_distribution)}, expected "
            f"{dict(expected_distribution)}"
        )

for change_type in types:
    expected_slot_set = {expected_slots[change_type]}
    if slots[change_type] != expected_slot_set:
        fail(
            f"{change_type}: changedSlot coverage is "
            f"{sorted(slots[change_type])}, expected "
            f"{sorted(expected_slot_set)}"
        )

records = json.loads(
    (root / "videodata.json").read_text(encoding="utf-8")
)
if len(records) != expected_total_scenes:
    fail(
        "videodata.json must contain exactly "
        f"{expected_total_scenes} records"
    )

for record in records:
    video_path = record.get("video_path")
    if record.get("video") != video_path:
        fail("video and video_path must contain the same relative path")
    if record.get("scene_type") != "tabletop":
        fail("scene_type must be tabletop")
    annotation = annotations_by_video.get(video_path)
    if annotation is None:
        fail(f"no annotation matches video {video_path!r}")
    if record.get("metadata") != annotation.get("metadata"):
        fail(f"{video_path}: final-record metadata mismatch")
    if record.get("questions") != annotation.get("qa"):
        fail(f"{video_path}: final-record questions mismatch")

print()
print(
    f"[{expected_total_scenes}-scene sheets-01-08 "
    "QA coverage passed]"
)
for change_type in types:
    count = expected_counts[change_type]
    scene_count = expected_scene_counts[change_type]
    repeated = (
        scene_count * questions_per_scene - count
    )
    print(
        f"  {change_type}: {count}/{count} templates in "
        f"{scene_count} scenes; {repeated} repeated slots"
    )
print("  QA wording came only from sheets 01-08")
print("  all Variables typical values resolve every placeholder")
print("  position variables use the four fixed English phrases")
print("  every change uses its fixed A/B physical slot")
print("  colorless present objects use the literal Null")
print("  every view contains unique object classes")
print("  every question contains descriptive or yes_or_no")
print("  output: " + str(root))
PYVALIDATE

echo
echo "All-cases test complete: $OUTPUT"
