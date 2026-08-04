#!/usr/bin/env python3
"""Validate and split a world-state-1.0 tabletop video dataset."""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import shutil
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "world-state-1.0"
SCENE_TYPE = "tabletop"
QUESTION = (
    "Reconstruct the physical tabletop state before and after the viewpoint "
    "change, and identify the world-state event."
)
SLOTS = {"world_slot_a", "world_slot_b"}


def atomic_json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_answer(answer: Any, source: str) -> None:
    if not isinstance(answer, dict) or set(answer) != {
        "objects", "before", "after", "event", "change"
    }:
        raise RuntimeError(f"{source}: invalid answer fields")
    objects = answer["objects"]
    if not isinstance(objects, dict) or not objects:
        raise RuntimeError(f"{source}: objects must be non-empty")
    object_ids = set(objects)
    for object_id, descriptor in objects.items():
        if not object_id.startswith("obj_") or not isinstance(descriptor, dict):
            raise RuntimeError(f"{source}: invalid object {object_id!r}")
        if set(descriptor) != {"category", "color"}:
            raise RuntimeError(f"{source}: invalid descriptor for {object_id}")
        if any(value is not None and not isinstance(value, str) for value in descriptor.values()):
            raise RuntimeError(f"{source}: attributes must be strings or null")
    for state_name in ("before", "after"):
        state = answer[state_name]
        if not isinstance(state, dict) or set(state) != SLOTS:
            raise RuntimeError(f"{source}: invalid {state_name} state")
        if any(value is not None and value not in object_ids for value in state.values()):
            raise RuntimeError(f"{source}: unknown object in {state_name}")
    event = answer["event"]
    if not isinstance(event, dict) or set(event) != {"type", "participants"}:
        raise RuntimeError(f"{source}: invalid event")
    if not isinstance(event["type"], str) or not isinstance(event["participants"], list):
        raise RuntimeError(f"{source}: invalid event values")
    if any(value not in object_ids for value in event["participants"]):
        raise RuntimeError(f"{source}: unknown event participant")
    if type(answer["change"]) is not bool:
        raise RuntimeError(f"{source}: change must be boolean")
    if answer["change"] != (event["type"] != "none"):
        raise RuntimeError(f"{source}: inconsistent change and event")


def validate_record(record: Any, index: int, input_json: Path) -> dict[str, Any]:
    source = f"{input_json} record {index}"
    if not isinstance(record, dict):
        raise RuntimeError(f"{source}: record must be an object")
    if record.get("qa_schema_version") != SCHEMA_VERSION:
        raise RuntimeError(f"{source}: expected schema {SCHEMA_VERSION!r}")
    if record.get("scene_type") != SCENE_TYPE:
        raise RuntimeError(f"{source}: expected scene_type {SCENE_TYPE!r}")
    video = record.get("video")
    if not isinstance(video, str) or not video.strip():
        raise RuntimeError(f"{source}: video must be non-empty")
    qas = record.get("QAs")
    if not isinstance(qas, list) or len(qas) != 1:
        raise RuntimeError(f"{source}: exactly one question is required")
    question = qas[0]
    if not isinstance(question, dict) or question.get("question") != QUESTION:
        raise RuntimeError(f"{source}: unexpected fixed question")
    if "question_type" in question and question["question_type"] != "descriptive":
        raise RuntimeError(f"{source}: question_type must be descriptive when present")
    validate_answer(question.get("answer"), f"{source} answer")
    if "canonical_scene" in record:
        raise RuntimeError(f"{source}: canonical_scene must not duplicate the answer")
    return copy.deepcopy(record)


def resolve_video(input_json: Path, video: str) -> Path:
    path = Path(video)
    candidates = [path] if path.is_absolute() else [input_json.parent / path, input_json.parent / "data" / path.name]
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.resolve()
    raise RuntimeError(f"Cannot resolve video {video!r} from {input_json}")


def prepare_output(path: Path, overwrite: bool, json_only: bool) -> None:
    if json_only:
        if not path.is_dir():
            raise RuntimeError(
                f"JSON-only output directory does not exist: {path}"
            )
        return
    if path.exists() and any(path.iterdir()):
        if not overwrite:
            raise RuntimeError(f"Output is not empty: {path}; pass --overwrite")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "data").mkdir(parents=True, exist_ok=True)


def next_videodata_path(output: Path) -> Path:
    candidate = output / "videodata.json"
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = output / f"videodata_{suffix}.json"
        if not candidate.exists():
            return candidate
        suffix += 1


def existing_output_video(output: Path, video: str) -> tuple[Path, str]:
    input_path = Path(video)
    candidates: list[Path] = []
    if not input_path.is_absolute():
        candidates.append(output / input_path)
    candidates.extend(
        (output / "data" / input_path.name, output / input_path.name)
    )
    checked: set[Path] = set()
    for candidate in candidates:
        if candidate in checked:
            continue
        checked.add(candidate)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate, candidate.relative_to(output).as_posix()
    locations = ", ".join(str(path) for path in candidates)
    raise RuntimeError(
        f"Video {video!r} is not present in JSON-only output {output}; "
        f"checked: {locations}"
    )


def write_partition(
    records: list[dict[str, Any]],
    sources: dict[str, Path],
    output: Path,
    copy_mode: str,
    json_only: bool,
) -> Path:
    normalized = []
    for record in records:
        if json_only:
            _, relative_video = existing_output_video(output, record["video"])
        else:
            source = sources[record["video"]]
            destination = output / "data" / source.name
            if copy_mode == "hardlink":
                os.link(source, destination)
            else:
                shutil.copy2(source, destination)
            relative_video = f"data/{source.name}"
        item = copy.deepcopy(record)
        item["video"] = relative_video
        normalized.append(item)
    json_path = next_videodata_path(output)
    atomic_json_write(json_path, normalized)
    return json_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split a validated world-state-1.0 dataset."
    )
    parser.add_argument("input_json", type=Path)
    parser.add_argument("selected_output", type=Path)
    parser.add_argument("remaining_output", type=Path)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--copy-mode", choices=("copy", "hardlink"), default="copy")
    parser.add_argument(
        "--json-only",
        action="store_true",
        help=(
            "Do not copy videos. Verify that each video already exists in its "
            "output directory and write only the split JSON files."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_json = args.input_json.expanduser().resolve()
    try:
        payload = json.loads(input_json.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError(f"{input_json}: root must be a list")
        records = [
            validate_record(record, index, input_json)
            for index, record in enumerate(payload, 1)
        ]
        if args.count <= 0 or args.count >= len(records):
            raise RuntimeError(
                f"--count must be between 1 and {len(records) - 1}"
            )
        videos = [record["video"] for record in records]
        if len(videos) != len(set(videos)):
            raise RuntimeError("Duplicate video paths in input dataset")
        if args.json_only and args.overwrite:
            raise RuntimeError("--json-only and --overwrite cannot be combined")
        sources = (
            {}
            if args.json_only
            else {video: resolve_video(input_json, video) for video in videos}
        )
        order = list(range(len(records)))
        random.Random(args.seed).shuffle(order)
        selected_indices = set(order[: args.count])
        selected = [record for index, record in enumerate(records) if index in selected_indices]
        remaining = [record for index, record in enumerate(records) if index not in selected_indices]

        selected_output = args.selected_output.expanduser().resolve()
        remaining_output = args.remaining_output.expanduser().resolve()
        if selected_output == remaining_output:
            raise RuntimeError("Selected and remaining outputs must differ")
        prepare_output(selected_output, args.overwrite, args.json_only)
        prepare_output(remaining_output, args.overwrite, args.json_only)
        selected_json = write_partition(
            selected, sources, selected_output, args.copy_mode, args.json_only
        )
        remaining_json = write_partition(
            remaining, sources, remaining_output, args.copy_mode, args.json_only
        )
    except (OSError, json.JSONDecodeError, RuntimeError) as error:
        print(f"Split failed: {error}", file=sys.stderr)
        return 2

    print(f"Selected: {len(selected)} -> {selected_json}")
    print(f"Remaining: {len(remaining)} -> {remaining_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
