#!/usr/bin/env python3
"""Regenerate one fixed world-state QA for each existing tabletop video.

The script reads Unity's per-video ``Batch_*/annotation.json`` metadata and
does not inspect or modify video bytes. It intentionally does not reuse the
canonical-v7 question libraries: this is a small, fixed-schema experiment for
viewpoint-invariant before/after state memory.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "world-state-1.0"
SCENE_TYPE = "tabletop"
QUESTION = (
    "Reconstruct the physical tabletop state before and after the viewpoint "
    "change, and identify the world-state event."
)
SLOTS = ("world_slot_a", "world_slot_b")

CHANGE_TYPE_ALIASES = {
    "single_object_replacement": "one_object_replacement",
    "color_change": "same_object_color_change",
    "position_swap": "swap_positions",
    "swap_position": "swap_positions",
    "none": "no_change",
    "object_addition": "object_adding",
    "object_removal": "object_deleting",
}
SUPPORTED_CHANGE_TYPES = {
    "one_object_replacement",
    "same_object_color_change",
    "distance_increase",
    "distance_decrease",
    "swap_positions",
    "no_change",
    "object_adding",
    "object_deleting",
}
EVENT_NAMES = {
    "one_object_replacement": "replacement",
    "same_object_color_change": "color_change",
    "distance_increase": "distance_increase",
    "distance_decrease": "distance_decrease",
    "swap_positions": "swap",
    "no_change": "none",
    "object_adding": "object_adding",
    "object_deleting": "object_deleting",
}


def normalize_change_type(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return CHANGE_TYPE_ALIASES.get(key, key)


def state_is_present(state: Any) -> bool:
    return isinstance(state, dict) and bool(state) and bool(state.get("present", True))


def normalized_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def state_category(state: dict[str, Any]) -> str | None:
    if not state_is_present(state):
        return None
    return normalized_text(state.get("label") or state.get("propClass"))


def state_color(state: dict[str, Any]) -> str | None:
    if not state_is_present(state):
        return None
    return normalized_text(state.get("color"))


def comparable_state(state: Any) -> tuple[Any, ...]:
    state = state if isinstance(state, dict) else {}
    return (
        state_is_present(state),
        str(state.get("propClass") or "").strip().casefold(),
        str(state.get("label") or "").strip().casefold(),
        str(state.get("color") or "").strip().casefold(),
        bool(state.get("supportsColor", False)),
    )


def validate_scene_correspondence(annotation: dict[str, Any], source: Path) -> str:
    """Validate the eight Unity change types before deriving stable identities."""
    change_type = normalize_change_type(annotation.get("changeType"))
    if change_type not in SUPPORTED_CHANGE_TYPES:
        raise ValueError(f"{source}: unsupported change type {change_type!r}")
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
    actual_slot = str(annotation.get("changedSlot") or "").strip().lower()
    if actual_slot != expected_slots[change_type]:
        raise ValueError(
            f"{source}: {change_type!r} requires changedSlot="
            f"{expected_slots[change_type]!r}, found {actual_slot!r}"
        )

    lb_state = annotation.get("leftBefore")
    rb_state = annotation.get("rightBefore")
    la_state = annotation.get("leftAfter")
    ra_state = annotation.get("rightAfter")
    lb, rb = comparable_state(lb_state), comparable_state(rb_state)
    la, ra = comparable_state(la_state), comparable_state(ra_state)
    present = state_is_present
    valid = True
    if change_type == "one_object_replacement":
        valid = (
            all(map(present, (lb_state, rb_state, la_state, ra_state)))
            and lb[1] != la[1]
            and rb == ra
        )
    elif change_type == "same_object_color_change":
        valid = (
            all(map(present, (lb_state, rb_state, la_state, ra_state)))
            and lb[1] == la[1]
            and lb[3] != la[3]
            and rb == ra
        )
    elif change_type in {"distance_increase", "distance_decrease"}:
        valid = all(map(present, (lb_state, rb_state, la_state, ra_state))) and lb == la and rb == ra
    elif change_type == "swap_positions":
        valid = all(map(present, (lb_state, rb_state, la_state, ra_state))) and lb == ra and rb == la
    elif change_type == "no_change":
        valid = lb == la and rb == ra
    elif change_type == "object_adding":
        valid = present(lb_state) and present(la_state) and lb == la and not present(rb_state) and present(ra_state)
    elif change_type == "object_deleting":
        valid = present(lb_state) and present(la_state) and lb == la and present(rb_state) and not present(ra_state)
    if not valid:
        raise ValueError(
            f"{source}: scene state does not match change type {change_type!r}"
        )
    return change_type


def identity_layout(
    annotation: dict[str, Any],
    change_type: str,
) -> tuple[dict[str, str | None], dict[str, str | None], dict[str, list[dict[str, Any]]]]:
    """Assign stable obj_N identities using the known Unity event semantics."""
    lb = annotation.get("leftBefore") or {}
    rb = annotation.get("rightBefore") or {}
    la = annotation.get("leftAfter") or {}
    ra = annotation.get("rightAfter") or {}

    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    before = {"world_slot_a": None, "world_slot_b": None}
    after = {"world_slot_a": None, "world_slot_b": None}
    next_id = 1

    def create(state: dict[str, Any]) -> str | None:
        nonlocal next_id
        if not state_is_present(state):
            return None
        object_id = f"obj_{next_id}"
        next_id += 1
        observations[object_id].append(state)
        return object_id

    before_a = create(lb)
    before_b = create(rb)
    before["world_slot_a"] = before_a
    before["world_slot_b"] = before_b

    if change_type == "one_object_replacement":
        after_a = create(la)
        after_b = before_b
    elif change_type == "swap_positions":
        after_a = before_b
        after_b = before_a
    elif change_type == "object_adding":
        after_a = before_a
        after_b = create(ra)
    elif change_type == "object_deleting":
        after_a = before_a
        after_b = None
    else:
        after_a = before_a
        after_b = before_b

    after["world_slot_a"] = after_a
    after["world_slot_b"] = after_b
    if after_a is not None and after_a in {before_a, before_b}:
        observations[after_a].append(la)
    if after_b is not None and after_b in {before_a, before_b}:
        observations[after_b].append(ra)
    return before, after, observations


def object_registry(
    observations: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, str | None]]:
    result: dict[str, dict[str, str | None]] = {}
    for object_id in sorted(observations, key=lambda value: int(value.split("_")[1])):
        states = observations[object_id]
        categories = [state_category(state) for state in states]
        categories = [value for value in categories if value is not None]
        colors = [state_color(state) for state in states]
        colors = [value for value in colors if value is not None]
        unique_categories = list(dict.fromkeys(categories))
        unique_colors = list(dict.fromkeys(colors))
        if len(unique_categories) > 1:
            raise ValueError(
                f"{object_id} has inconsistent categories: {unique_categories}"
            )
        # A single static color field must not misrepresent a color transition.
        # Use null when the color is absent or changes between the two views.
        result[object_id] = {
            "category": unique_categories[0] if unique_categories else None,
            "color": unique_colors[0] if len(unique_colors) == 1 else None,
        }
    return result


def event_participants(
    change_type: str,
    before: dict[str, str | None],
    after: dict[str, str | None],
) -> list[str]:
    before_a, before_b = before["world_slot_a"], before["world_slot_b"]
    after_a, after_b = after["world_slot_a"], after["world_slot_b"]
    if change_type == "one_object_replacement":
        values = [before_a, after_a]
    elif change_type == "same_object_color_change":
        values = [before_a]
    elif change_type in {"distance_increase", "distance_decrease", "swap_positions"}:
        values = [before_a, before_b]
    elif change_type == "object_adding":
        values = [after_b]
    elif change_type == "object_deleting":
        values = [before_b]
    else:
        values = []
    return [value for value in values if value is not None]


def build_answer(annotation: dict[str, Any], source: Path) -> dict[str, Any]:
    change_type = validate_scene_correspondence(annotation, source)
    before, after, observations = identity_layout(annotation, change_type)
    answer = {
        "objects": object_registry(observations),
        "before": before,
        "after": after,
        "event": {
            "type": EVENT_NAMES[change_type],
            "participants": event_participants(change_type, before, after),
        },
        "change": change_type != "no_change",
    }
    validate_answer(answer, source)
    return answer


def validate_answer(answer: Any, source: Path | str) -> None:
    if not isinstance(answer, dict) or set(answer) != {
        "objects", "before", "after", "event", "change"
    }:
        raise ValueError(f"{source}: invalid world-state answer fields")
    objects = answer["objects"]
    if not isinstance(objects, dict) or not objects:
        raise ValueError(f"{source}: objects must be a non-empty object")
    object_ids = set(objects)
    for object_id, descriptor in objects.items():
        if not object_id.startswith("obj_") or not isinstance(descriptor, dict):
            raise ValueError(f"{source}: invalid object entry {object_id!r}")
        if set(descriptor) != {"category", "color"}:
            raise ValueError(f"{source}: {object_id} must contain category and color")
        if any(value is not None and not isinstance(value, str) for value in descriptor.values()):
            raise ValueError(f"{source}: object attributes must be strings or null")
    for state_name in ("before", "after"):
        state = answer[state_name]
        if not isinstance(state, dict) or tuple(state) != SLOTS:
            raise ValueError(f"{source}: {state_name} must contain the two world slots")
        if any(value is not None and value not in object_ids for value in state.values()):
            raise ValueError(f"{source}: {state_name} refers to an unknown object")
    event = answer["event"]
    if not isinstance(event, dict) or set(event) != {"type", "participants"}:
        raise ValueError(f"{source}: event must contain type and participants")
    if not isinstance(event["type"], str) or not isinstance(event["participants"], list):
        raise ValueError(f"{source}: invalid event values")
    if any(value not in object_ids for value in event["participants"]):
        raise ValueError(f"{source}: event refers to an unknown participant")
    if type(answer["change"]) is not bool:
        raise ValueError(f"{source}: change must be boolean")
    if answer["change"] != (event["type"] != "none"):
        raise ValueError(f"{source}: change and event.type are inconsistent")


def atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json_write(path: Path, data: Any) -> None:
    atomic_text_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def qa_text(video_id: str, answer: dict[str, Any]) -> str:
    return (
        f"Video: {video_id}\n"
        f"Schema: {SCHEMA_VERSION}\n\n"
        f"Q1: {QUESTION}\n"
        f"A1: {json.dumps(answer, ensure_ascii=False, separators=(',', ':'))}\n"
    )


def build_record(annotation: dict[str, Any], annotation_path: Path, video_path: str) -> dict[str, Any]:
    answer = build_answer(annotation, annotation_path)
    return {
        "video": video_path,
        "scene_type": SCENE_TYPE,
        "qa_schema_version": SCHEMA_VERSION,
        "QAs": [
            {
                "question": QUESTION,
                "answer": copy.deepcopy(answer),
            }
        ],
    }


def back_up_metadata(output_root: Path, batch_dirs: list[Path]) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = output_root / f"qa_backup_before_world_state_{timestamp}"
    for batch_dir in batch_dirs:
        destination = backup_root / batch_dir.name
        for name in ("annotation.json", "qa_entries.json", "qa.txt"):
            source = batch_dir / name
            if source.is_file():
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination / name)
    video_data = output_root / "videodata.json"
    if video_data.is_file():
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_data, backup_root / video_data.name)
    return backup_root


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Regenerate one fixed world-state QA for every existing Unity video."
    )
    parser.add_argument(
        "output_root",
        nargs="?",
        default=str(project_root / "Output"),
        help="Existing output directory containing Batch_* folders.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--require-all-videos", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).expanduser().resolve()
    if not output_root.is_dir():
        print(f"Output directory does not exist: {output_root}", file=sys.stderr)
        return 2

    prepared: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    counts: Counter[str] = Counter()
    errors: list[str] = []
    skipped = 0
    for annotation_path in sorted(output_root.glob("Batch_*/annotation.json")):
        try:
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            change_type = validate_scene_correspondence(annotation, annotation_path)
            video_path = str(annotation.get("videoPath") or "").replace("\\", "/").lstrip("/")
            if not video_path:
                raise ValueError(f"{annotation_path}: missing videoPath")
            video_file = output_root / video_path
            if not video_file.is_file() or video_file.stat().st_size == 0:
                message = f"{annotation_path.parent.name}: missing video {video_file}"
                if args.require_all_videos:
                    raise ValueError(message)
                print(f"Skipping {message}", file=sys.stderr)
                skipped += 1
                continue
            record = build_record(annotation, annotation_path, video_path)
            prepared.append((annotation_path.parent, annotation, record))
            counts[change_type] += 1
        except (OSError, json.JSONDecodeError, ValueError) as error:
            errors.append(str(error))

    if errors:
        print(f"Validation failed for {len(errors)} annotation(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print("No file was changed.", file=sys.stderr)
        return 2
    if not prepared:
        print("No completed videos were available.", file=sys.stderr)
        return 2

    print(f"Validated {len(prepared)} videos ({skipped} skipped).")
    for change_type, count in sorted(counts.items()):
        print(f"  {change_type}: {count}")
    if args.dry_run:
        print("Dry run complete. No file was changed.")
        return 0

    backup_root = None
    if not args.no_backup:
        backup_root = back_up_metadata(output_root, [item[0] for item in prepared])

    records = []
    for batch_dir, annotation, record in prepared:
        answer = record["QAs"][0]["answer"]
        annotation["qaSchemaVersion"] = SCHEMA_VERSION
        annotation["canonicalScene"] = copy.deepcopy(answer)
        annotation["qa"] = copy.deepcopy(record["QAs"])
        annotation["qaTemplateIds"] = ["world_state_v1_fixed"]
        atomic_json_write(batch_dir / "annotation.json", annotation)
        atomic_json_write(batch_dir / "qa_entries.json", record)
        atomic_text_write(
            batch_dir / "qa.txt",
            qa_text(f"scene_{int(annotation.get('batchId', 0)):06d}", answer),
        )
        records.append(record)

    atomic_json_write(output_root / "videodata.json", records)
    print(f"World-state QA regeneration complete: {len(records)} videos.")
    print(f"Final JSON: {output_root / 'videodata.json'}")
    if backup_root is not None:
        print(f"Old metadata backup: {backup_root}")
    print("MP4 videos and PNG frames were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
