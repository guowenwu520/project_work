#!/usr/bin/env python3
"""Regenerate QA for existing videos without running Unity or rerendering."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
QA_XOR = 0x5F3759DF


def to_int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


class DotNetRandom:
    """Classic System.Random implementation used by Unity/Mono."""

    MBIG = 2147483647
    MSEED = 161803398

    def __init__(self, seed: int) -> None:
        seed = to_int32(seed)
        subtraction = self.MBIG if seed == -2147483648 else abs(seed)
        mj = self.MSEED - subtraction
        if mj < 0:
            mj += self.MBIG

        self.seed_array = [0] * 56
        self.seed_array[55] = mj
        mk = 1

        for index in range(1, 55):
            mapped = (21 * index) % 55
            self.seed_array[mapped] = mk
            mk = mj - mk
            if mk < 0:
                mk += self.MBIG
            mj = self.seed_array[mapped]

        for _ in range(4):
            for index in range(1, 56):
                self.seed_array[index] -= self.seed_array[
                    1 + (index + 30) % 55
                ]
                if self.seed_array[index] < 0:
                    self.seed_array[index] += self.MBIG

        self.inext = 0
        self.inextp = 21

    def _internal_sample(self) -> int:
        next_index = self.inext + 1
        if next_index >= 56:
            next_index = 1

        next_index_p = self.inextp + 1
        if next_index_p >= 56:
            next_index_p = 1

        value = (
            self.seed_array[next_index]
            - self.seed_array[next_index_p]
        )
        if value == self.MBIG:
            value -= 1
        if value < 0:
            value += self.MBIG

        self.seed_array[next_index] = value
        self.inext = next_index
        self.inextp = next_index_p
        return value

    def next(self, max_value: int) -> int:
        if max_value <= 0:
            if max_value == 0:
                return 0
            raise ValueError("max_value must be non-negative")

        sample = self._internal_sample()
        return int((sample * (1.0 / self.MBIG)) * max_value)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(
        description=(
            "Read existing Batch_*/annotation.json files and regenerate "
            "only their QA metadata. MP4 and PNG files are never modified."
        )
    )
    parser.add_argument(
        "output_root",
        nargs="?",
        default=str(project_root / "Output"),
        help="Existing output directory containing Batch_* folders.",
    )
    parser.add_argument(
        "--templates",
        default=str(
            project_root
            / "Assets"
            / "StreamingAssets"
            / "tabletop_qa_templates.json"
        ),
        help="Reviewed English QA library.",
    )
    parser.add_argument(
        "--sampling-salt",
        type=int,
        default=None,
        help=(
            "Override sampling_salt from the JSON file. "
            "Use a different value to create another stable sequence of "
            "balanced random cycles."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and sample all QA without writing files.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not back up old QA metadata before overwriting.",
    )
    parser.add_argument(
        "--require-all-videos",
        action="store_true",
        help="Fail instead of skipping annotations whose MP4 is missing.",
    )
    return parser.parse_args()


def normalize_change_type(value: Any) -> str:
    key = (
        str(value or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    aliases = {
        "single_object_replacement": "one_object_replacement",
        "one_object_replacement": "one_object_replacement",
        "double_object_replacement": "two_objects_replacement",
        "two_object_replacement": "two_objects_replacement",
        "two_objects_replacement": "two_objects_replacement",
        "color_change": "same_object_color_change",
        "same_object_color_change": "same_object_color_change",
        "distance_increase": "distance_increase",
        "position_swap": "swap_positions",
        "swap_position": "swap_positions",
        "swap_positions": "swap_positions",
        "none": "no_change",
        "no_change": "no_change",
    }
    return aliases.get(key, key)


def state_description(state: dict[str, Any] | None) -> str:
    state = state or {}
    label = str(state.get("label") or "item").strip()
    color = str(state.get("color") or "").strip()
    supports_color = bool(state.get("supportsColor", False))

    if supports_color and color:
        return f"{color} {label}".strip()
    return label


def state_label(state: dict[str, Any] | None) -> str:
    return str((state or {}).get("label") or "item").strip()


def put(
    context: dict[str, str],
    key: str,
    value: Any,
) -> None:
    text = str(value or "").strip()
    if key and text:
        context[key] = text


def is_left(slot: Any) -> bool:
    return str(slot or "").strip().lower() == "left"


def build_context(
    annotation: dict[str, Any],
    random: DotNetRandom,
) -> dict[str, str]:
    context: dict[str, str] = {}

    def put(key: str, value: Any) -> None:
        text = str(value or "").strip()
        if key and text:
            context[key] = text

    def left_first() -> str:
        return "the left side of the table in the first view"

    def right_first() -> str:
        return "the right side of the table in the first view"

    def left_second() -> str:
        return "the left side of the table in the second view"

    def right_second() -> str:
        return "the right side of the table in the second view"

    put("initial_count", "2")
    put("final_count", "2")

    left_before = annotation.get("leftBefore") or {}
    right_before = annotation.get("rightBefore") or {}
    left_after = annotation.get("leftAfter") or {}
    right_after = annotation.get("rightAfter") or {}

    put("object_a", state_description(left_before))
    put("object_b", state_description(right_before))
    put(
        "final_object_list",
        "The "
        + state_description(right_after)
        + " and the "
        + state_description(left_after),
    )

    change_type = normalize_change_type(
        annotation.get("changeType")
    )
    changed_slot = str(
        annotation.get("changedSlot") or ""
    )

    if change_type == "one_object_replacement":
        changed_left = is_left(changed_slot)
        before = left_before if changed_left else right_before
        after = left_after if changed_left else right_after

        put("old_object", state_description(before))
        put("new_object", state_description(after))
        put(
            "initial_position",
            left_first() if changed_left else right_first(),
        )
        put(
            "final_position",
            right_second() if changed_left else left_second(),
        )

    elif change_type == "two_objects_replacement":
        put("old_object_1", state_description(left_before))
        put("new_object_1", state_description(left_after))
        put("old_object_2", state_description(right_before))
        put("new_object_2", state_description(right_after))

        put("initial_position_1", left_first())
        put("final_position_1", right_second())
        put("initial_position_2", right_first())
        put("final_position_2", left_second())

    elif change_type == "same_object_color_change":
        changed_left = is_left(changed_slot)
        before = left_before if changed_left else right_before
        after = left_after if changed_left else right_after

        put("object", state_label(before))
        put("original_color", before.get("color"))
        put("new_color", after.get("color"))
        put(
            "initial_position",
            left_first() if changed_left else right_first(),
        )
        put(
            "final_position",
            right_second() if changed_left else left_second(),
        )

    elif change_type == "distance_increase":
        put("initial_position_a", left_first())
        put("initial_position_b", right_first())
        put("final_position_a", right_second())
        put("final_position_b", left_second())

    elif change_type == "swap_positions":
        put("object_a_initial_position", left_first())
        put("object_a_final_position", left_second())
        put("object_b_initial_position", right_first())
        put("object_b_final_position", right_second())

    elif change_type == "no_change":
        select_left = random.next(2) == 0
        selected = left_before if select_left else right_before

        put("selected_object", state_description(selected))
        put(
            "initial_selected_position",
            left_first() if select_left else right_first(),
        )
        put(
            "final_selected_position",
            right_second() if select_left else left_second(),
        )

        if bool(selected.get("supportsColor", False)):
            put("selected_color", selected.get("color"))

    return context


def render(
    template: str,
    context: dict[str, str],
) -> str | None:
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        key = match.group(1)
        value = context.get(key)
        if not value:
            missing = True
            return match.group(0)
        return value.strip()

    rendered = PLACEHOLDER_RE.sub(
        replace,
        str(template or ""),
    ).strip()

    if missing or not rendered:
        return None
    return rendered


def shuffle(
    values: list[dict[str, Any]],
    random: DotNetRandom,
) -> None:
    for index in range(len(values) - 1, 0, -1):
        other = random.next(index + 1)
        values[index], values[other] = values[other], values[index]


def stable_cycle_seed(
    change_type: str,
    sampling_salt: int,
) -> int:
    """Create a stable random seed without using Python's salted hash()."""

    payload = (
        f"balanced-cycle-v1|{change_type}|{sampling_salt}"
    ).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def render_template_pool(
    annotation: dict[str, Any],
    templates_by_type: dict[str, list[dict[str, Any]]],
    sampling_salt: int,
) -> list[dict[str, str]]:
    """
    Render every valid template for one scene.

    The result keeps template_id internally so the global scheduler can
    track which source templates have already appeared in the current cycle.
    """

    seed = int(annotation.get("seed", 1))
    context_seed = to_int32(seed ^ QA_XOR ^ sampling_salt)
    context_random = DotNetRandom(context_seed)
    context = build_context(annotation, context_random)

    change_type = normalize_change_type(
        annotation.get("changeType")
    )
    templates = templates_by_type.get(change_type, [])
    if not templates:
        raise ValueError(
            f"No QA templates found for change type: {change_type}"
        )

    rendered: list[dict[str, str]] = []
    seen_questions: set[str] = set()

    for template in templates:
        question = render(template.get("question", ""), context)
        answer = render(template.get("answer", ""), context)

        if question is None or answer is None:
            continue
        if question in seen_questions:
            continue

        template_id = str(
            template.get("template_id") or ""
        ).strip()
        if not template_id:
            raise ValueError(
                f"{change_type} contains a template without template_id"
            )

        seen_questions.add(question)
        rendered.append(
            {
                "template_id": template_id,
                "question": question,
                "answer": answer,
            }
        )

    return rendered


class BalancedCycleScheduler:
    """
    Select templates in balanced random cycles.

    Example for a 60-template pool with 8 questions per video:

    - videos 1-7 consume 56 previously unseen templates;
    - video 8 consumes the remaining 4 unseen templates, then randomly
      fills the other 4 positions from templates already seen this cycle;
    - after video 8, the cycle is reset;
    - video 9 starts a newly shuffled cycle.

    Each change type owns an independent scheduler.
    """

    def __init__(
        self,
        *,
        change_type: str,
        target_template_ids: set[str],
        sampling_salt: int,
    ) -> None:
        if not target_template_ids:
            raise ValueError(
                f"{change_type} has no renderable QA templates"
            )

        self.change_type = change_type
        self.target_template_ids = set(target_template_ids)
        self.random = random.Random(
            stable_cycle_seed(
                change_type,
                sampling_salt,
            )
        )
        self.seen_in_cycle: set[str] = set()
        self.completed_cycles = 0
        self.total_appearances: Counter[str] = Counter()

    def _shuffle(
        self,
        values: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        result = list(values)
        self.random.shuffle(result)
        return result

    def select(
        self,
        rendered_templates: list[dict[str, str]],
        questions_per_scene: int,
    ) -> tuple[
        list[dict[str, str]],
        list[str],
        bool,
    ]:
        if questions_per_scene <= 0:
            raise ValueError(
                "questions_per_scene must be positive"
            )

        # One scene must never contain the same rendered question twice.
        unique_entries: list[dict[str, str]] = []
        seen_questions: set[str] = set()
        seen_ids: set[str] = set()

        for entry in rendered_templates:
            template_id = entry["template_id"]
            question = entry["question"]

            if template_id in seen_ids:
                continue
            if question in seen_questions:
                continue

            seen_ids.add(template_id)
            seen_questions.add(question)
            unique_entries.append(entry)

        if len(unique_entries) < questions_per_scene:
            raise ValueError(
                f"{self.change_type} has only "
                f"{len(unique_entries)} valid unique questions for this "
                f"scene; {questions_per_scene} are required."
            )

        selected: list[dict[str, str]] = []
        selected_ids: set[str] = set()
        selected_questions: set[str] = set()

        # First priority: templates that have not appeared in this cycle.
        unseen_candidates = self._shuffle(
            [
                entry
                for entry in unique_entries
                if entry["template_id"]
                not in self.seen_in_cycle
            ]
        )

        for entry in unseen_candidates:
            if len(selected) >= questions_per_scene:
                break
            if entry["question"] in selected_questions:
                continue

            selected.append(entry)
            selected_ids.add(entry["template_id"])
            selected_questions.add(entry["question"])

        # At the end of a non-divisible cycle, fill the remaining slots
        # from templates already used during this cycle.
        if len(selected) < questions_per_scene:
            filler_candidates = self._shuffle(
                [
                    entry
                    for entry in unique_entries
                    if entry["template_id"]
                    in self.seen_in_cycle
                    and entry["template_id"]
                    not in selected_ids
                ]
            )

            for entry in filler_candidates:
                if len(selected) >= questions_per_scene:
                    break
                if entry["question"] in selected_questions:
                    continue

                selected.append(entry)
                selected_ids.add(entry["template_id"])
                selected_questions.add(entry["question"])

        if len(selected) != questions_per_scene:
            raise ValueError(
                f"{self.change_type} could select only "
                f"{len(selected)} unique questions; "
                f"{questions_per_scene} are required."
            )

        for template_id in selected_ids:
            self.seen_in_cycle.add(template_id)
            self.total_appearances[template_id] += 1

        completed_cycle = (
            self.target_template_ids
            <= self.seen_in_cycle
        )
        if completed_cycle:
            self.completed_cycles += 1
            self.seen_in_cycle.clear()

        qa = [
            {
                "question": entry["question"],
                "answer": entry["answer"],
            }
            for entry in selected
        ]
        selected_template_ids = [
            entry["template_id"]
            for entry in selected
        ]

        return qa, selected_template_ids, completed_cycle


def atomic_json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def build_qa_text(
    annotation: dict[str, Any],
    video_id: str,
    qa: list[dict[str, str]],
) -> str:
    lines = [
        f"Video ID: {video_id}",
        "Scene type: tabletop",
        (
            "Change type: "
            + normalize_change_type(annotation.get("changeType"))
        ),
        f"Changed slot: {annotation.get('changedSlot', '')}",
        (
            "Before: left="
            + state_description(annotation.get("leftBefore"))
            + ", right="
            + state_description(annotation.get("rightBefore"))
        ),
        (
            "After:  left="
            + state_description(annotation.get("leftAfter"))
            + ", right="
            + state_description(annotation.get("rightAfter"))
        ),
        "",
    ]

    for index, pair in enumerate(qa, start=1):
        lines.append(f"Q{index}: {pair['question']}")
        lines.append(f"A{index}: {pair['answer']}")
        lines.append("")

    return "\n".join(lines)


def back_up_metadata(
    output_root: Path,
    batch_dirs: list[Path],
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = (
        output_root
        / f"qa_backup_before_regenerate_{timestamp}"
    )
    backup_root.mkdir(parents=True, exist_ok=False)

    for batch_dir in batch_dirs:
        destination = backup_root / batch_dir.name
        destination.mkdir(parents=True, exist_ok=True)

        for name in (
            "annotation.json",
            "qa_entries.json",
            "qa.txt",
        ):
            source = batch_dir / name
            if source.is_file():
                shutil.copy2(source, destination / name)

    final_json = output_root / "videodata.json"
    if final_json.is_file():
        shutil.copy2(
            final_json,
            backup_root / "videodata.json",
        )

    return backup_root


def main() -> int:
    args = parse_args()

    output_root = Path(args.output_root).expanduser().resolve()
    template_path = Path(args.templates).expanduser().resolve()

    if not output_root.is_dir():
        print(
            f"Output directory does not exist: {output_root}",
            file=sys.stderr,
        )
        return 2

    if not template_path.is_file():
        print(
            f"QA template file does not exist: {template_path}",
            file=sys.stderr,
        )
        return 2

    library = json.loads(
        template_path.read_text(encoding="utf-8")
    )
    questions_per_scene = int(
        library.get("questions_per_scene", 8)
    )
    library_salt = int(library.get("sampling_salt", 0))
    sampling_salt = (
        library_salt
        if args.sampling_salt is None
        else args.sampling_salt
    )

    templates_by_type = {
        normalize_change_type(group.get("change_type")):
        list(group.get("templates") or [])
        for group in library.get("change_types") or []
    }

    annotation_paths = sorted(
        output_root.glob("Batch_*/annotation.json")
    )
    if not annotation_paths:
        print(
            f"No Batch_*/annotation.json found under {output_root}",
            file=sys.stderr,
        )
        return 2

    # Stage 1: validate every completed video and render every valid
    # template for that scene. No QA is selected yet.
    scene_items: list[
        tuple[
            Path,
            dict[str, Any],
            str,
            str,
            list[dict[str, str]],
        ]
    ] = []
    skipped_missing_video = 0
    counts: Counter[str] = Counter()

    for annotation_path in annotation_paths:
        batch_dir = annotation_path.parent
        annotation = json.loads(
            annotation_path.read_text(encoding="utf-8")
        )

        video_path = (
            str(annotation.get("videoPath") or "")
            .replace("\\", "/")
            .lstrip("/")
        )
        if not video_path:
            raise ValueError(
                f"{annotation_path} does not contain videoPath."
            )

        video_file = output_root / video_path
        if not video_file.is_file() or video_file.stat().st_size == 0:
            message = (
                f"Skipping {batch_dir.name}: "
                f"missing video {video_file}"
            )
            if args.require_all_videos:
                raise FileNotFoundError(message)
            print(message, file=sys.stderr)
            skipped_missing_video += 1
            continue

        change_type = normalize_change_type(
            annotation.get("changeType")
        )
        rendered_templates = render_template_pool(
            annotation,
            templates_by_type,
            sampling_salt,
        )

        scene_items.append(
            (
                batch_dir,
                annotation,
                video_path,
                change_type,
                rendered_templates,
            )
        )
        counts[change_type] += 1

    scene_items.sort(
        key=lambda item: int(item[1].get("batchId", 0))
    )

    # The cycle target for each change type is the union of templates that
    # can actually be rendered by at least one completed scene in this
    # dataset. Normally this is all 60 templates.
    target_ids_by_type: dict[str, set[str]] = defaultdict(set)
    for _, _, _, change_type, rendered_templates in scene_items:
        target_ids_by_type[change_type].update(
            entry["template_id"]
            for entry in rendered_templates
        )

    schedulers = {
        change_type: BalancedCycleScheduler(
            change_type=change_type,
            target_template_ids=template_ids,
            sampling_salt=sampling_salt,
        )
        for change_type, template_ids
        in target_ids_by_type.items()
    }

    # Stage 2: process scenes in batch-id order. Every change type owns an
    # independent 60-template cycle.
    prepared: list[
        tuple[
            Path,
            dict[str, Any],
            list[dict[str, str]],
            dict[str, Any],
            list[str],
        ]
    ] = []

    for (
        batch_dir,
        annotation,
        video_path,
        change_type,
        rendered_templates,
    ) in scene_items:
        scheduler = schedulers[change_type]
        qa, selected_template_ids, _ = scheduler.select(
            rendered_templates,
            questions_per_scene,
        )

        batch_id = int(annotation.get("batchId", 0))
        video_id = f"scene_{batch_id:06d}"
        record = {
            "video_id": video_id,
            "video_path": video_path,
            "scene_type": str(
                library.get("scene_type") or "tabletop"
            ),
            "questions": qa,
        }

        prepared.append(
            (
                batch_dir,
                annotation,
                qa,
                record,
                selected_template_ids,
            )
        )

    print(
        f"Validated {len(prepared)} completed videos."
    )
    print(
        f"Each video will receive "
        f"{questions_per_scene} QA pairs."
    )
    print(
        "Sampling strategy: balanced random cycles "
        "(unseen templates first)."
    )
    print(f"Sampling salt: {sampling_salt}")

    configured_pool_sizes = {
        normalize_change_type(group.get("change_type")):
        len(group.get("templates") or [])
        for group in library.get("change_types") or []
    }

    for change_type, count in sorted(counts.items()):
        scheduler = schedulers[change_type]
        renderable = len(scheduler.target_template_ids)
        configured = configured_pool_sizes.get(
            change_type,
            renderable,
        )
        current_coverage = len(scheduler.seen_in_cycle)

        print(
            f"  {change_type}: {count} videos | "
            f"renderable pool {renderable}/{configured} | "
            f"completed cycles {scheduler.completed_cycles} | "
            f"current cycle coverage {current_coverage}/{renderable}"
        )

        if renderable < configured:
            unavailable = sorted(
                {
                    str(template.get("template_id") or "")
                    for template in templates_by_type.get(
                        change_type,
                        [],
                    )
                }
                - scheduler.target_template_ids
            )
            print(
                f"    warning: {len(unavailable)} templates were not "
                "renderable by any completed scene in this dataset.",
                file=sys.stderr,
            )
            if unavailable:
                print(
                    "    unavailable: "
                    + ", ".join(unavailable),
                    file=sys.stderr,
                )

    if skipped_missing_video:
        print(
            f"Skipped missing videos: {skipped_missing_video}"
        )

    if args.dry_run:
        print("Dry run complete. No file was changed.")
        return 0

    if not prepared:
        print(
            "No completed videos were available for QA regeneration.",
            file=sys.stderr,
        )
        return 2

    backup_root: Path | None = None
    if not args.no_backup:
        backup_root = back_up_metadata(
            output_root,
            [item[0] for item in prepared],
        )

    final_records: list[dict[str, Any]] = []

    for (
        batch_dir,
        annotation,
        qa,
        record,
        selected_template_ids,
    ) in prepared:
        annotation["changeType"] = normalize_change_type(annotation.get("changeType"))
        annotation["qa"] = qa
        annotation["qaTemplateIds"] = selected_template_ids

        atomic_json_write(
            batch_dir / "annotation.json",
            annotation,
        )
        atomic_json_write(
            batch_dir / "qa_entries.json",
            record,
        )
        atomic_text_write(
            batch_dir / "qa.txt",
            build_qa_text(
                annotation,
                record["video_id"],
                qa,
            ),
        )
        final_records.append(record)

    atomic_json_write(
        output_root / "videodata.json",
        final_records,
    )

    print()
    print("QA regeneration complete.")
    print(f"Updated videos: {len(final_records)}")
    print(
        f"Updated QA pairs: "
        f"{len(final_records) * questions_per_scene}"
    )
    print(
        f"Final JSON: {output_root / 'videodata.json'}"
    )
    if backup_root is not None:
        print(f"Old QA backup: {backup_root}")
    print("MP4 videos and PNG frames were not modified.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
