#!/usr/bin/env python3
"""Split a canonical-v7 video dataset while preserving structured QA."""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_SCHEMA = "canonical-7.0"
EXPECTED_SCENE_TYPE = "tabletop"
QUESTION_TYPES = {"descriptive", "yes_or_no"}
ANSWER_TYPES = {
    "boolean",
    "color",
    "entity",
    "entity_pair",
    "event",
    "object_state",
    "position",
    "scene_state",
}
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_.]+)\}")
CANONICAL_FILES = {
    "replacement": "QAs_v7_replacement.json",
    "color_change": "QAs_v7_color_change.json",
    "distance_increase": "QAs_v7_distance_increase.json",
    "distance_decrease": "QAs_v7_distance_decrease.json",
    "position_swap": "QAs_v7_position_swap.json",
    "no_change": "QAs_v7_no_change.json",
    "object_adding": "QAs_v7_object_adding.json",
    "object_deleting": "QAs_v7_object_deleting.json",
}


@dataclass(frozen=True)
class DatasetItem:
    record: dict[str, Any]
    source_video: Path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Randomly split canonical-v7 videodata.json and copy the "
            "corresponding MP4 files into two self-contained datasets."
        )
    )
    commands = result.add_subparsers(dest="command", required=True)
    split = commands.add_parser(
        "split",
        help="Randomly select one partition and write the remainder separately.",
    )
    split.add_argument(
        "input_json",
        type=Path,
        help="Canonical input videodata.json, for example outztest/videodata.json.",
    )
    split.add_argument("selected_output", type=Path)
    split.add_argument("remaining_output", type=Path)
    split.add_argument("--count", type=int, default=100)
    split.add_argument("--seed", type=int, default=42)
    split.add_argument("--questions-per-video", type=int, default=8)
    split.add_argument(
        "--canonical-dir",
        type=Path,
        default=PROJECT_ROOT.parent / "canonical",
    )
    split.add_argument("--overwrite", action="store_true")
    split.add_argument("--require-all-videos", action="store_true")
    split.add_argument(
        "--copy-mode",
        choices=("copy", "hardlink"),
        default="copy",
        help="Copy MP4 bytes, or hard-link them when source/output share a filesystem.",
    )
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"File not found: {path}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Invalid JSON {path}:{error.lineno}:{error.colno}: {error.msg}"
        ) from error


def load_definitions(directory: Path) -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for change_type, filename in CANONICAL_FILES.items():
        path = directory / filename
        payload = load_json(path)
        if not isinstance(payload, dict) or payload.get("change_type") != change_type:
            raise RuntimeError(
                f"{path} must define change_type={change_type!r}"
            )
        variables = payload.get("variables")
        answers = payload.get("answer_event")
        groups = payload.get("qa_groups")
        if not isinstance(variables, dict) or not isinstance(answers, list) or not isinstance(groups, list):
            raise RuntimeError(f"Incomplete canonical definition: {path}")
        definitions[change_type] = payload
    return definitions


def render(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [render(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: render(item, variables) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    exact = PLACEHOLDER_RE.fullmatch(value)
    if exact:
        name = exact.group(1)
        if name not in variables:
            raise RuntimeError(f"Missing canonical variable {name!r}")
        return copy.deepcopy(variables[name])

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise RuntimeError(f"Missing canonical variable {name!r}")
        replacement = variables[name]
        if isinstance(replacement, (dict, list)):
            return json.dumps(replacement, ensure_ascii=False)
        return str(replacement)

    return PLACEHOLDER_RE.sub(substitute, value).strip()


def dotted_variable_name(value: Any) -> bool:
    return isinstance(value, str) and (
        value in {"view_a", "view_b"}
        or bool(re.fullmatch(r"view_[ab]\.[A-Za-z0-9_]+", value))
    )


def require_string(value: Any, field: str, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{source}: {field} must be a non-empty string")
    return value.strip()


def validate_variables(
    value: Any,
    definition: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{source}: variables must be an object")
    expected = set(definition["variables"])
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(
            f"{source}: canonical variable keys differ; missing={missing}, extra={extra}"
        )
    invalid = sorted(name for name in actual if not dotted_variable_name(name))
    if invalid:
        raise RuntimeError(
            f"{source}: variables must use dotted names; invalid={invalid}"
        )
    return copy.deepcopy(value)


def answer_lookup(definition: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {item["id"]: item for item in definition["answer_event"]}


def matching_questions(
    definition: dict[str, Any], answer_event_id: int
) -> list[dict[str, Any]]:
    return [
        variant
        for group in definition["qa_groups"]
        if group.get("answer_event.id") == answer_event_id
        for variant in group.get("question_variants", [])
    ]


def validate_question(
    item: Any,
    *,
    variables: dict[str, Any],
    definition: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise RuntimeError(f"{source}: question entry must be an object")
    question = require_string(item.get("question"), "question", source)
    question_type = require_string(item.get("question_type"), "question_type", source)
    if question_type not in QUESTION_TYPES:
        raise RuntimeError(f"{source}: invalid question_type {question_type!r}")
    answer = item.get("answer")
    if not isinstance(answer, dict):
        raise RuntimeError(f"{source}: answer must be a structured JSON object")
    if answer.get("answer_type") not in ANSWER_TYPES:
        raise RuntimeError(f"{source}: invalid answer_type {answer.get('answer_type')!r}")
    event_id = item.get("answer_event_id")
    events = answer_lookup(definition)
    if not isinstance(event_id, int) or event_id not in events:
        raise RuntimeError(f"{source}: unknown answer_event_id {event_id!r}")
    event = events[event_id]
    if item.get("answer_event_name") != event.get("name"):
        raise RuntimeError(f"{source}: answer_event_name does not match id {event_id}")
    expected_answer = render(event["answer_template"], variables)
    if answer != expected_answer:
        raise RuntimeError(f"{source}: answer differs from canonical event {event_id}")
    answer_variables = item.get("answer_variables")
    if answer_variables != event.get("answer_variables"):
        raise RuntimeError(f"{source}: answer_variables differ from canonical event {event_id}")
    variants = matching_questions(definition, event_id)
    matched_variant = next(
        (variant for variant in variants if render(variant["text"], variables) == question),
        None,
    )
    if matched_variant is None:
        raise RuntimeError(f"{source}: question is not a canonical variant for event {event_id}")
    if item.get("question_variables") != matched_variant.get("question_variables"):
        raise RuntimeError(f"{source}: question_variables do not match canonical definition")
    for field in ("question_variables", "answer_variables"):
        names = item.get(field)
        if not isinstance(names, list) or not all(dotted_variable_name(name) for name in names):
            raise RuntimeError(f"{source}: {field} must contain dotted canonical names")
    expected_type = "yes_or_no" if answer["answer_type"] == "boolean" else "descriptive"
    if question_type != expected_type:
        raise RuntimeError(
            f"{source}: question_type must be {expected_type!r} for {answer['answer_type']!r}"
        )
    return copy.deepcopy(item)


def validate_record(
    value: Any,
    *,
    index: int,
    json_path: Path,
    definitions: dict[str, dict[str, Any]],
    questions_per_video: int,
) -> dict[str, Any]:
    source = f"{json_path} record {index}"
    if not isinstance(value, dict):
        raise RuntimeError(f"{source}: record must be an object")
    video = require_string(value.get("video"), "video", source).replace("\\", "/")
    if require_string(value.get("scene_type"), "scene_type", source) != EXPECTED_SCENE_TYPE:
        raise RuntimeError(f"{source}: scene_type must be {EXPECTED_SCENE_TYPE!r}")
    if value.get("qa_schema_version") != EXPECTED_SCHEMA:
        raise RuntimeError(f"{source}: qa_schema_version must be {EXPECTED_SCHEMA!r}")
    scene = value.get("canonical_scene")
    if not isinstance(scene, dict):
        raise RuntimeError(f"{source}: canonical_scene must be an object")
    change_type = scene.get("change_type")
    if change_type not in definitions:
        raise RuntimeError(f"{source}: unsupported canonical change_type {change_type!r}")
    definition = definitions[change_type]
    variables = validate_variables(value.get("variables"), definition, source)
    expected_scene = render(definition["canonical_scene"], variables)
    if scene != expected_scene:
        raise RuntimeError(f"{source}: canonical_scene does not match its definition")
    questions = value.get("questions")
    if not isinstance(questions, list) or len(questions) != questions_per_video:
        raise RuntimeError(
            f"{source}: questions must contain exactly {questions_per_video} entries"
        )
    normalized_questions = [
        validate_question(
            question,
            variables=variables,
            definition=definition,
            source=f"{source} question {question_index}",
        )
        for question_index, question in enumerate(questions, 1)
    ]
    texts = [item["question"] for item in normalized_questions]
    if len(texts) != len(set(texts)):
        raise RuntimeError(f"{source}: duplicate rendered questions")
    result = copy.deepcopy(value)
    result["video"] = video
    result["variables"] = variables
    result["canonical_scene"] = copy.deepcopy(scene)
    result["questions"] = normalized_questions
    return result


def load_dataset(
    path: Path,
    definitions: dict[str, dict[str, Any]],
    questions_per_video: int,
) -> list[dict[str, Any]]:
    payload = load_json(path)
    if not isinstance(payload, list):
        raise RuntimeError(f"{path}: top-level JSON must be a list")
    records = [
        validate_record(
            value,
            index=index,
            json_path=path,
            definitions=definitions,
            questions_per_video=questions_per_video,
        )
        for index, value in enumerate(payload, 1)
    ]
    videos = [record["video"] for record in records]
    if len(videos) != len(set(videos)):
        raise RuntimeError(f"{path}: duplicate video paths")
    return records


def find_video(input_json: Path, video: str) -> Path | None:
    reference = Path(video).expanduser()
    candidates = (
        [reference]
        if reference.is_absolute()
        else [input_json.parent / reference, input_json.parent / "data" / reference.name]
    )
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved.is_file() and resolved.stat().st_size > 0:
            return resolved
    return None


def prepare_output(path: Path, overwrite: bool) -> tuple[Path, Path]:
    output = path.expanduser().resolve()
    protected = {Path(output.anchor).resolve(), Path.home().resolve(), Path.cwd().resolve()}
    if output in protected:
        raise RuntimeError(f"Refusing unsafe output directory: {output}")
    if output.exists() and any(output.iterdir()):
        if not overwrite:
            raise RuntimeError(f"Output directory is not empty: {output}")
        shutil.rmtree(output)
    data = output / "data"
    data.mkdir(parents=True, exist_ok=True)
    return output, data


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_partition(
    *,
    label: str,
    items: list[DatasetItem],
    output_path: Path,
    json_name: str,
    overwrite: bool,
    copy_mode: str,
) -> None:
    output, data = prepare_output(output_path, overwrite)
    records: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for index, item in enumerate(items, 1):
        name = item.source_video.name
        if name in used_names:
            stem, suffix = item.source_video.stem, item.source_video.suffix
            counter = 2
            while f"{stem}_{counter}{suffix}" in used_names:
                counter += 1
            name = f"{stem}_{counter}{suffix}"
        used_names.add(name)
        destination = data / name
        if copy_mode == "hardlink":
            try:
                os.link(item.source_video, destination)
            except OSError as error:
                raise RuntimeError(
                    f"Cannot hard-link {item.source_video} to {destination}: {error}"
                ) from error
        else:
            shutil.copy2(item.source_video, destination)
        record = copy.deepcopy(item.record)
        record["video"] = f"data/{name}"
        records.append(record)
        print(f"[{label}] {index}/{len(items)} {name}", flush=True)
    atomic_json(output / json_name, records)


def main() -> int:
    args = parser().parse_args()
    if args.count <= 0:
        raise RuntimeError("--count must be positive")
    if args.questions_per_video <= 0:
        raise RuntimeError("--questions-per-video must be positive")
    input_json = args.input_json.expanduser().resolve()
    selected_output = args.selected_output.expanduser().resolve()
    remaining_output = args.remaining_output.expanduser().resolve()
    if selected_output == remaining_output:
        raise RuntimeError("Selected and remaining output directories must differ")
    definitions = load_definitions(args.canonical_dir.expanduser().resolve())
    records = load_dataset(input_json, definitions, args.questions_per_video)
    items: list[DatasetItem] = []
    missing: list[str] = []
    for record in records:
        video = find_video(input_json, record["video"])
        if video is None:
            missing.append(record["video"])
        else:
            items.append(DatasetItem(record=record, source_video=video))
    if missing and args.require_all_videos:
        raise RuntimeError(f"Missing {len(missing)} referenced videos: {missing[:10]}")
    if len(items) < args.count:
        raise RuntimeError(
            f"Cannot select {args.count} videos; only {len(items)} valid videos exist"
        )
    shuffled = list(items)
    random.Random(args.seed).shuffle(shuffled)
    selected = shuffled[: args.count]
    selected_paths = {item.record["video"] for item in selected}
    remaining = [item for item in items if item.record["video"] not in selected_paths]
    write_partition(
        label="selected",
        items=selected,
        output_path=selected_output,
        json_name=input_json.name,
        overwrite=args.overwrite,
        copy_mode=args.copy_mode,
    )
    write_partition(
        label="remaining",
        items=remaining,
        output_path=remaining_output,
        json_name=input_json.name,
        overwrite=args.overwrite,
        copy_mode=args.copy_mode,
    )
    # Reload both outputs through the complete canonical validator.
    selected_records = load_dataset(
        selected_output / input_json.name, definitions, args.questions_per_video
    )
    remaining_records = load_dataset(
        remaining_output / input_json.name, definitions, args.questions_per_video
    )
    if len(selected_records) != len(selected) or len(remaining_records) != len(remaining):
        raise RuntimeError("Post-write record count validation failed")
    print("\nCanonical split complete")
    print(f"  input records:     {len(records)}")
    print(f"  selected records:  {len(selected_records)}")
    print(f"  remaining records: {len(remaining_records)}")
    print(f"  missing skipped:   {len(missing)}")
    print(f"  random seed:       {args.seed}")
    print(f"  selected output:   {selected_output}")
    print(f"  remaining output:  {remaining_output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
