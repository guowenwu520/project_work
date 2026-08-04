#!/usr/bin/env python3
"""Regenerate QA for existing videos from the canonical v7 JSON files."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_.]+)\}")
CANONICAL_CHANGE_TYPES = {
    "one_object_replacement": "replacement",
    "same_object_color_change": "color_change",
    "distance_increase": "distance_increase",
    "distance_decrease": "distance_decrease",
    "swap_positions": "position_swap",
    "no_change": "no_change",
    "object_adding": "object_adding",
    "object_deleting": "object_deleting",
}


def normalize_change_type(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "single_object_replacement": "one_object_replacement",
        "color_change": "same_object_color_change",
        "position_swap": "swap_positions",
        "swap_position": "swap_positions",
        "none": "no_change",
        "object_addition": "object_adding",
        "object_removal": "object_deleting",
    }
    return aliases.get(key, key)


def state_is_present(state: Any) -> bool:
    return isinstance(state, dict) and bool(state) and bool(state.get("present", True))


def comparable_state(state: Any) -> tuple[Any, ...]:
    state = state if isinstance(state, dict) else {}
    return (
        state_is_present(state),
        str(state.get("propClass") or "").strip().casefold(),
        str(state.get("label") or "").strip().casefold(),
        str(state.get("color") or "").strip().casefold(),
        bool(state.get("supportsColor", False)),
    )


def validate_scene_correspondence(annotation: dict[str, Any], source: Path) -> None:
    change_type = normalize_change_type(annotation.get("changeType"))
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
    if change_type not in expected_slots:
        raise ValueError(f"{source}: unsupported change type {change_type!r}")
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
        valid = all(map(present, (lb_state, rb_state, la_state, ra_state))) and lb[1] != la[1] and rb == ra
    elif change_type == "same_object_color_change":
        valid = all(map(present, (lb_state, rb_state, la_state, ra_state))) and lb[1] == la[1] and lb[3] != la[3] and rb == ra
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
            f"{source}: scene state does not match canonical change "
            f"type {change_type!r}"
        )


def stable_cycle_seed(change_type: str, sampling_salt: int) -> int:
    payload = f"balanced-cycle-v1|{change_type}|{sampling_salt}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


class BalancedCycleScheduler:
    def __init__(self, *, change_type: str, target_template_ids: set[str], sampling_salt: int) -> None:
        if not target_template_ids:
            raise ValueError(f"{change_type} has no renderable canonical questions")
        self.change_type = change_type
        self.target_template_ids = set(target_template_ids)
        self.random = random.Random(stable_cycle_seed(change_type, sampling_salt))
        self.seen_in_cycle: set[str] = set()

    def select(
        self, pool: list[dict[str, Any]], count: int
    ) -> tuple[list[dict[str, Any]], list[str], bool]:
        unique = {item["template_id"]: item for item in pool}
        if len(unique) < count:
            raise ValueError(
                f"{self.change_type} has {len(unique)} canonical questions; "
                f"{count} required"
            )
        unseen = [item for key, item in unique.items() if key not in self.seen_in_cycle]
        self.random.shuffle(unseen)
        selected = unseen[:count]
        if len(selected) < count:
            fillers = [
                item for key, item in unique.items()
                if key in self.seen_in_cycle
                and key not in {entry["template_id"] for entry in selected}
            ]
            self.random.shuffle(fillers)
            selected.extend(fillers[: count - len(selected)])
        ids = [item["template_id"] for item in selected]
        self.seen_in_cycle.update(ids)
        completed = self.target_template_ids <= self.seen_in_cycle
        if completed:
            self.seen_in_cycle.clear()
        qa = [
            {
                "question": item["question"],
                "answer": copy.deepcopy(item["answer"]),
                "question_type": item["question_type"],
            }
            for item in selected
        ]
        return qa, ids, completed


def atomic_json_write(path: Path, data: Any) -> None:
    atomic_text_write(
        path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )


def atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def back_up_metadata(output_root: Path, batch_dirs: list[Path]) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = output_root / f"qa_backup_before_canonical_{timestamp}"
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
        description=(
            "Read existing Batch_*/annotation.json files and regenerate "
            "their QA with the structured canonical JSON definitions."
        )
    )
    parser.add_argument(
        "output_root",
        nargs="?",
        default=str(project_root / "Output"),
        help="Existing output directory containing Batch_* folders.",
    )
    parser.add_argument(
        "--canonical-dir",
        default=str(project_root.parent / "canonical"),
        help="Directory containing one QAs_v7_<change>.json per change type.",
    )
    parser.add_argument(
        "--questions-per-scene",
        type=int,
        default=8,
        help="Number of canonical QA pairs to select for each video.",
    )
    parser.add_argument(
        "--sampling-salt",
        type=int,
        default=20260726,
        help="Salt for deterministic balanced-cycle sampling.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate all canonical definition files and exit.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--require-all-videos", action="store_true")
    return parser.parse_args()


def placeholders(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(PLACEHOLDER_RE.findall(value))
    elif isinstance(value, list):
        for item in value:
            found.update(placeholders(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.update(placeholders(item))
    return found


def load_canonical_libraries(canonical_dir: Path) -> dict[str, dict[str, Any]]:
    libraries: dict[str, dict[str, Any]] = {}
    for runtime_type, canonical_type in CANONICAL_CHANGE_TYPES.items():
        path = canonical_dir / f"QAs_v7_{canonical_type}.json"
        if not path.is_file():
            raise ValueError(f"Missing canonical QA file: {path}")
        try:
            library = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Cannot read {path}: {error}") from error

        if library.get("change_type") != canonical_type:
            raise ValueError(
                f"{path}: change_type must be {canonical_type!r}"
            )
        declared = set((library.get("variables") or {}).keys())
        answers = library.get("answer_event") or []
        answer_by_id = {item.get("id"): item for item in answers}
        if len(answer_by_id) != len(answers):
            raise ValueError(f"{path}: duplicate or missing answer_event id")

        seen_questions: set[str] = set()
        for group_index, group in enumerate(library.get("qa_groups") or [], 1):
            answer_id = group.get("answer_event.id")
            if answer_id not in answer_by_id:
                raise ValueError(
                    f"{path}: qa_group {group_index} references unknown "
                    f"answer_event.id {answer_id!r}"
                )
            answer = answer_by_id[answer_id]
            answer_vars = set(answer.get("answer_variables") or [])
            actual_answer_vars = placeholders(answer.get("answer_template"))
            if answer_vars != actual_answer_vars:
                raise ValueError(
                    f"{path}: answer_event {answer_id} answer_variables "
                    "do not match its template placeholders"
                )
            for question_index, question in enumerate(
                group.get("question_variants") or [], 1
            ):
                text = str(question.get("text") or "").strip()
                question_vars = set(question.get("question_variables") or [])
                if not text or question_vars != placeholders(text):
                    raise ValueError(
                        f"{path}: group {group_index} question "
                        f"{question_index} has invalid text or variables"
                    )
                if text in seen_questions:
                    raise ValueError(f"{path}: duplicate question {text!r}")
                seen_questions.add(text)
                unknown = (question_vars | answer_vars) - declared
                if unknown:
                    raise ValueError(
                        f"{path}: undeclared variables: {', '.join(sorted(unknown))}"
                    )
        if not seen_questions:
            raise ValueError(f"{path}: no question variants")
        libraries[runtime_type] = library
    return libraries


def build_context(annotation: dict[str, Any]) -> dict[str, Any]:
    left_before = annotation.get("leftBefore") or {}
    right_before = annotation.get("rightBefore") or {}
    left_after = annotation.get("leftAfter") or {}
    right_after = annotation.get("rightAfter") or {}

    def label(state: dict[str, Any]) -> str:
        return str(state.get("label") or state.get("propClass") or "item").strip()

    def color(state: dict[str, Any]) -> str:
        return str(state.get("color") or "Null").strip()

    def object_list(*states: dict[str, Any]) -> list[str]:
        return [label(state) for state in states if state_is_present(state)]

    initial_count = annotation.get("initialObjectCount")
    final_count = annotation.get("finalObjectCount")
    if initial_count is None:
        initial_count = len(object_list(left_before, right_before))
    if final_count is None:
        final_count = len(object_list(left_after, right_after))

    return {
        "view_a": "the first view",
        "view_b": "the second view",
        "view_a.object_a": label(left_before),
        "view_b.object_a": label(left_after),
        "view_a.object_b": label(right_before),
        "view_b.object_b": label(right_after),
        "view_a.color_a": color(left_before),
        "view_b.color_a": color(left_after),
        "view_a.count": int(initial_count),
        "view_b.count": int(final_count),
        "view_a.position_a": "the left side (1st view) of the table",
        "view_a.position_b": "the right side (1st view) of the table",
        "view_b.position_a": "the right side (2nd view) of the table",
        "view_b.position_b": "the left side (2nd view) of the table",
        "view_a.object_list": object_list(left_before, right_before),
        "view_b.object_list": object_list(left_after, right_after),
    }


def render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [render_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render_value(item, context) for key, item in value.items()}
    if not isinstance(value, str):
        return value

    full_match = PLACEHOLDER_RE.fullmatch(value)
    if full_match:
        key = full_match.group(1)
        if key not in context:
            raise ValueError(f"No annotation value for {{{key}}}")
        return copy.deepcopy(context[key])

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise ValueError(f"No annotation value for {{{key}}}")
        replacement = context[key]
        if isinstance(replacement, (dict, list)):
            return json.dumps(replacement, ensure_ascii=False)
        return str(replacement)

    return PLACEHOLDER_RE.sub(replace, value).strip()


def render_pool(
    annotation: dict[str, Any], library: dict[str, Any]
) -> list[dict[str, Any]]:
    context = build_context(annotation)
    answer_by_id = {
        item["id"]: item for item in library["answer_event"]
    }
    rendered: list[dict[str, Any]] = []
    prefix = str(library["change_type"])
    for group_index, group in enumerate(library["qa_groups"], 1):
        answer_event = answer_by_id[group["answer_event.id"]]
        answer = render_value(answer_event["answer_template"], context)
        question_type = (
            "yes_or_no"
            if answer.get("answer_type") == "boolean"
            else "descriptive"
        )
        for variant_index, variant in enumerate(group["question_variants"], 1):
            rendered.append(
                {
                    "template_id": f"{prefix}_g{group_index:02d}_q{variant_index:02d}",
                    "question": render_value(variant["text"], context),
                    "answer": copy.deepcopy(answer),
                    "question_type": question_type,
                    "answer_event_id": answer_event["id"],
                    "answer_event_name": answer_event["name"],
                    "question_variables": list(
                        variant.get("question_variables") or []
                    ),
                    "answer_variables": list(
                        answer_event.get("answer_variables") or []
                    ),
                }
            )
    return rendered


def canonical_scene_data(
    annotation: dict[str, Any],
    library: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return dotted canonical variables and the resolved canonical scene."""

    context = build_context(annotation)
    declared_variables = list((library.get("variables") or {}).keys())
    variables = {
        name: copy.deepcopy(context[name])
        for name in declared_variables
        if name in context
    }
    missing = sorted(set(declared_variables) - set(variables))
    if missing:
        raise ValueError(
            "Annotation cannot provide canonical variables: "
            + ", ".join(missing)
        )
    scene = render_value(library.get("canonical_scene") or {}, context)
    return variables, scene


def qa_text(annotation: dict[str, Any], video_id: str, qa: list[dict[str, Any]]) -> str:
    lines = [
        f"Video: {video_id}",
        f"Change type: {normalize_change_type(annotation.get('changeType'))}",
        "Answer format: canonical structured JSON",
        "",
    ]
    for index, pair in enumerate(qa, 1):
        lines.append(f"Q{index} [{pair['question_type']}]: {pair['question']}")
        lines.append(
            f"A{index}: "
            + json.dumps(pair["answer"], ensure_ascii=False, separators=(",", ":"))
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_one_canonical_batch(
    *,
    output_root: Path,
    batch_dir: Path,
    libraries: dict[str, dict[str, Any]],
    questions_per_scene: int = 8,
    sampling_salt: int = 20260726,
) -> dict[str, Any]:
    """Immediately replace one Unity-written legacy QA record.

    Dataset-wide balanced scheduling still happens in ``main`` after every
    worker finishes. This per-batch selection prevents a completed batch from
    ever being left with the Player's legacy string-answer QA meanwhile.
    """

    annotation_path = batch_dir / "annotation.json"
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    validate_scene_correspondence(annotation, annotation_path)
    change_type = normalize_change_type(annotation.get("changeType"))
    if change_type not in libraries:
        raise ValueError(
            f"{annotation_path}: unsupported change type {change_type!r}"
        )
    pool = render_pool(annotation, libraries[change_type])
    if len(pool) < questions_per_scene:
        raise ValueError(
            f"{annotation_path}: only {len(pool)} canonical questions; "
            f"{questions_per_scene} requested"
        )

    seed_payload = (
        f"canonical-batch-v7|{annotation.get('seed', 1)}|"
        f"{change_type}|{sampling_salt}"
    ).encode("utf-8")
    selection_seed = int.from_bytes(
        hashlib.sha256(seed_payload).digest()[:8], "big"
    )
    shuffled = list(pool)
    random.Random(selection_seed).shuffle(shuffled)
    selected = shuffled[:questions_per_scene]
    qa = [
        {
            "question": item["question"],
            "question_variables": item["question_variables"],
            "answer": item["answer"],
            "answer_variables": item["answer_variables"],
            "question_type": item["question_type"],
            "answer_event_id": item["answer_event_id"],
            "answer_event_name": item["answer_event_name"],
        }
        for item in selected
    ]
    selected_ids = [item["template_id"] for item in selected]
    video_path = str(annotation.get("videoPath") or "").replace("\\", "/").lstrip("/")
    video_file = output_root / video_path
    if not video_file.is_file() or video_file.stat().st_size == 0:
        raise ValueError(f"Completed MP4 is missing: {video_file}")
    variables, canonical_scene = canonical_scene_data(
        annotation, libraries[change_type]
    )
    record = {
        "video": video_path,
        "scene_type": "tabletop",
        "qa_schema_version": "canonical-7.0",
        "variables": variables,
        "canonical_scene": canonical_scene,
        "questions": qa,
    }
    annotation["changeType"] = change_type
    annotation.pop("metadata", None)
    annotation["variables"] = variables
    annotation["canonicalScene"] = canonical_scene
    annotation["qaSchemaVersion"] = "canonical-7.0"
    annotation["qa"] = qa
    annotation["qaTemplateIds"] = selected_ids
    atomic_json_write(annotation_path, annotation)
    atomic_json_write(batch_dir / "qa_entries.json", record)
    atomic_text_write(
        batch_dir / "qa.txt",
        qa_text(
            annotation,
            f"scene_{int(annotation.get('batchId', 0)):06d}",
            qa,
        ),
    )
    return record


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).expanduser().resolve()
    canonical_dir = Path(args.canonical_dir).expanduser().resolve()
    if args.questions_per_scene <= 0:
        print("--questions-per-scene must be positive", file=sys.stderr)
        return 2
    try:
        libraries = load_canonical_libraries(canonical_dir)
    except ValueError as error:
        print(f"Canonical QA validation failed: {error}", file=sys.stderr)
        return 2
    if args.validate_only:
        total_questions = sum(
            len(group.get("question_variants") or [])
            for library in libraries.values()
            for group in library.get("qa_groups") or []
        )
        print(
            f"Validated {len(libraries)} canonical QA files "
            f"with {total_questions} question variants."
        )
        return 0
    if not output_root.is_dir():
        print(f"Output directory does not exist: {output_root}", file=sys.stderr)
        return 2

    scene_items: list[tuple[Path, dict[str, Any], str, str, list[dict[str, Any]]]] = []
    errors: list[str] = []
    skipped = 0
    for annotation_path in sorted(output_root.glob("Batch_*/annotation.json")):
        try:
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            validate_scene_correspondence(annotation, annotation_path)
            change_type = normalize_change_type(annotation.get("changeType"))
            if change_type not in libraries:
                raise ValueError(f"{annotation_path}: unsupported change type {change_type!r}")
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
            pool = render_pool(annotation, libraries[change_type])
            if len(pool) < args.questions_per_scene:
                raise ValueError(
                    f"{annotation_path}: only {len(pool)} canonical questions; "
                    f"{args.questions_per_scene} requested"
                )
            scene_items.append((annotation_path.parent, annotation, video_path, change_type, pool))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            errors.append(str(error))

    if errors:
        print(f"Validation failed for {len(errors)} annotation(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print("No file was changed.", file=sys.stderr)
        return 2
    if not scene_items:
        print("No completed videos were available.", file=sys.stderr)
        return 2

    scene_items.sort(key=lambda item: int(item[1].get("batchId", 0)))
    target_ids: dict[str, set[str]] = defaultdict(set)
    for _, _, _, change_type, pool in scene_items:
        target_ids[change_type].update(item["template_id"] for item in pool)
    schedulers = {
        change_type: BalancedCycleScheduler(
            change_type=change_type,
            target_template_ids=ids,
            sampling_salt=args.sampling_salt,
        )
        for change_type, ids in target_ids.items()
    }

    prepared: list[tuple[Path, dict[str, Any], list[dict[str, Any]], dict[str, Any], list[str]]] = []
    counts: Counter[str] = Counter()
    for batch_dir, annotation, video_path, change_type, pool in scene_items:
        qa, selected_ids, _ = schedulers[change_type].select(
            pool, args.questions_per_scene
        )
        # BalancedCycleScheduler intentionally emits the core three fields.
        # Restore canonical answer-event provenance from the selected entries.
        by_id = {item["template_id"]: item for item in pool}
        qa = [
            {
                **pair,
                "answer_event_id": by_id[template_id]["answer_event_id"],
                "answer_event_name": by_id[template_id]["answer_event_name"],
                "question_variables": by_id[template_id]["question_variables"],
                "answer_variables": by_id[template_id]["answer_variables"],
            }
            for pair, template_id in zip(qa, selected_ids)
        ]
        variables, canonical_scene = canonical_scene_data(
            annotation, libraries[change_type]
        )
        record = {
            "video": video_path,
            "scene_type": "tabletop",
            "qa_schema_version": "canonical-7.0",
            "variables": variables,
            "canonical_scene": canonical_scene,
            "questions": qa,
        }
        prepared.append((batch_dir, annotation, qa, record, selected_ids))
        counts[change_type] += 1

    print(f"Validated {len(prepared)} completed videos ({skipped} skipped).")
    print(f"Each video will receive {args.questions_per_scene} structured QA pairs.")
    for change_type, count in sorted(counts.items()):
        print(f"  {change_type}: {count} videos | canonical pool {len(target_ids[change_type])}")
    if args.dry_run:
        print("Dry run complete. No file was changed.")
        return 0

    backup_root = None
    if not args.no_backup:
        backup_root = back_up_metadata(
            output_root, [item[0] for item in prepared]
        )
    records: list[dict[str, Any]] = []
    for batch_dir, annotation, qa, record, selected_ids in prepared:
        annotation["changeType"] = normalize_change_type(annotation.get("changeType"))
        annotation.pop("metadata", None)
        annotation["variables"] = record["variables"]
        annotation["canonicalScene"] = record["canonical_scene"]
        annotation["qaSchemaVersion"] = "canonical-7.0"
        annotation["qa"] = qa
        annotation["qaTemplateIds"] = selected_ids
        atomic_json_write(batch_dir / "annotation.json", annotation)
        atomic_json_write(batch_dir / "qa_entries.json", record)
        atomic_text_write(
            batch_dir / "qa.txt",
            qa_text(annotation, f"scene_{int(annotation.get('batchId', 0)):06d}", qa),
        )
        records.append(record)
    atomic_json_write(output_root / "videodata.json", records)
    print(f"QA regeneration complete: {len(records)} videos, {len(records) * args.questions_per_scene} pairs.")
    print(f"Final JSON: {output_root / 'videodata.json'}")
    if backup_root is not None:
        print(f"Old QA backup: {backup_root}")
    print("MP4 videos and PNG frames were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
