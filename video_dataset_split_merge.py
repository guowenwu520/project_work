#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
新格式视频数据集拆分与合并工具。

仅支持当前按视频聚合的 JSON 格式：

[
  {
    "video_id": "scene_000123",
    "video_path": "data/video_123.mp4",
    "scene_type": "tabletop",
    "questions": [
      {
        "question": "What changed during the video?",
        "answer": "The apple was replaced by the cup."
      }
    ]
  }
]

 python3 video_dataset_split_merge.py split    ./Output/videodata.json         datav5/test       datav5/train         --count 100         --seed 42
拆分：

    python3 video_dataset_split_merge.py split \
        input/videodata.json \
        output/test100 \
        output/remain400 \
        --count 100 \
        --seed 42

合并：

    python3 video_dataset_split_merge.py merge \
        output/test100/videodata.json \
        output/remain400/videodata.json \
        --output-dir output/merged

输出目录：

    output_dir/
    ├── videodata.json
    └── data/
        └── *.mp4

规则：

1. JSON 顶层必须是列表。
2. 每条记录必须包含 video_id、video_path、scene_type、questions。
3. scene_type 必须为 tabletop。
4. 每个视频必须包含 8 组不重复问答。
5. 一条记录只对应一个视频。
6. 不兼容旧的 video 或 video + conversations 格式。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXPECTED_SCENE_TYPE = "tabletop"
EXPECTED_QUESTIONS = 8


@dataclass(frozen=True)
class DatasetItem:
    """一条已完成校验、并且能够找到视频文件的数据。"""

    record: dict[str, Any]
    source_video: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="拆分或合并新格式的桌面变化视频数据集"
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    split_parser = subparsers.add_parser(
        "split",
        help="随机抽出一份数据，并把剩余数据生成另一份",
    )
    split_parser.add_argument(
        "input_json",
        type=Path,
        help="输入 videodata.json",
    )
    split_parser.add_argument(
        "selected_output",
        type=Path,
        help="抽中部分的输出目录",
    )
    split_parser.add_argument(
        "remaining_output",
        type=Path,
        help="剩余部分的输出目录",
    )
    split_parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="随机抽取的视频数量，默认100",
    )
    split_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子，默认42",
    )
    split_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖非空输出目录",
    )

    merge_parser = subparsers.add_parser(
        "merge",
        help="把两份或多份新格式数据集合并为一份",
    )
    merge_parser.add_argument(
        "input_jsons",
        type=Path,
        nargs="+",
        help="输入 videodata.json，至少两份",
    )
    merge_parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="合并后的输出目录",
    )
    merge_parser.add_argument(
        "--json-name",
        default="videodata.json",
        help="合并后的 JSON 文件名，默认 videodata.json",
    )
    merge_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖非空输出目录",
    )

    return parser


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到输入 JSON：{path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"JSON 格式错误：{path}\n"
            f"第 {exc.lineno} 行，第 {exc.colno} 列：{exc.msg}"
        ) from exc


def non_empty_string(
    value: Any,
    *,
    field: str,
    record_index: int,
    json_path: Path,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 "
            f"{field} 必须是非空字符串。"
        )
    return value.strip()


def validate_questions(
    value: Any,
    *,
    record_index: int,
    json_path: Path,
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 "
            "questions 必须是列表。"
        )

    if len(value) != EXPECTED_QUESTIONS:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录包含 "
            f"{len(value)} 组问答，要求恰好为 {EXPECTED_QUESTIONS} 组。"
        )

    result: list[dict[str, str]] = []
    question_texts: set[str] = set()

    for question_index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"{json_path} 第 {record_index} 条记录的 "
                f"第 {question_index} 组问答不是 JSON 对象。"
            )

        question = non_empty_string(
            item.get("question"),
            field=f"questions[{question_index}].question",
            record_index=record_index,
            json_path=json_path,
        )
        answer = non_empty_string(
            item.get("answer"),
            field=f"questions[{question_index}].answer",
            record_index=record_index,
            json_path=json_path,
        )

        if question in question_texts:
            raise RuntimeError(
                f"{json_path} 第 {record_index} 条记录中存在重复问题："
                f"{question}"
            )
        question_texts.add(question)

        # 输出时只保留新格式规定的 question 和 answer。
        result.append(
            {
                "question": question,
                "answer": answer,
            }
        )

    return result


def validate_record(
    record: Any,
    *,
    record_index: int,
    json_path: Path,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录不是 JSON 对象。"
        )

    if "video" in record or "conversations" in record:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录仍是旧格式。"
            "本脚本只支持 video_id + video_path + scene_type + questions。"
        )

    video_id = non_empty_string(
        record.get("video_id"),
        field="video_id",
        record_index=record_index,
        json_path=json_path,
    )
    video_path = non_empty_string(
        record.get("video_path"),
        field="video_path",
        record_index=record_index,
        json_path=json_path,
    ).replace("\\", "/")

    scene_type = non_empty_string(
        record.get("scene_type"),
        field="scene_type",
        record_index=record_index,
        json_path=json_path,
    )
    if scene_type != EXPECTED_SCENE_TYPE:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 scene_type "
            f"为 {scene_type!r}，要求为 {EXPECTED_SCENE_TYPE!r}。"
        )

    questions = validate_questions(
        record.get("questions"),
        record_index=record_index,
        json_path=json_path,
    )

    # 保留未来可能增加的额外字段，但强制覆盖核心字段为规范值。
    normalized = copy.deepcopy(record)
    normalized["video_id"] = video_id
    normalized["video_path"] = video_path
    normalized["scene_type"] = EXPECTED_SCENE_TYPE
    normalized["questions"] = questions
    normalized.pop("video", None)
    normalized.pop("conversations", None)
    return normalized


def load_dataset(path: Path) -> list[dict[str, Any]]:
    root = load_json(path)

    if not isinstance(root, list):
        raise RuntimeError(
            f"{path} 的 JSON 顶层必须是视频记录列表，"
            "不再支持 data/samples/records/items 包装结构。"
        )

    records = [
        validate_record(
            record,
            record_index=index,
            json_path=path,
        )
        for index, record in enumerate(root, start=1)
    ]

    seen_video_ids: dict[str, int] = {}
    seen_video_paths: dict[str, int] = {}

    for index, record in enumerate(records, start=1):
        video_id = record["video_id"]
        video_path = record["video_path"]

        if video_id in seen_video_ids:
            raise RuntimeError(
                f"{path} 中 video_id 重复：{video_id}\n"
                f"第 {seen_video_ids[video_id]} 条和第 {index} 条。"
            )
        seen_video_ids[video_id] = index

        if video_path in seen_video_paths:
            raise RuntimeError(
                f"{path} 中 video_path 重复：{video_path}\n"
                f"第 {seen_video_paths[video_path]} 条和第 {index} 条。"
            )
        seen_video_paths[video_path] = index

    return records


def find_video(input_json: Path, video_path: str) -> Path | None:
    ref = Path(video_path).expanduser()

    if ref.is_absolute():
        candidates = [ref]
    else:
        candidates = [
            input_json.parent / ref,
            input_json.parent / "data" / ref.name,
            Path.cwd() / ref,
        ]

    seen: set[Path] = set()
    for candidate in candidates:
        normalized = candidate.resolve(strict=False)
        if normalized in seen:
            continue
        seen.add(normalized)

        if normalized.is_file():
            return normalized

    return None


def collect_existing_items(
    json_path: Path,
    records: list[dict[str, Any]],
    *,
    label: str,
) -> tuple[list[DatasetItem], list[str]]:
    items: list[DatasetItem] = []
    missing: list[str] = []

    print(
        f"[{label}] JSON 中共有 {len(records)} 条视频记录",
        flush=True,
    )

    for index, record in enumerate(records, start=1):
        video_path = record["video_path"]
        source = find_video(json_path, video_path)

        if source is None:
            missing.append(video_path)
            print(
                f"[{label}] {index}/{len(records)} | "
                f"视频缺失并排除：{video_path}",
                flush=True,
            )
            continue

        items.append(
            DatasetItem(
                record=record,
                source_video=source,
            )
        )

    return items, missing


def rewrite_video_path(
    record: dict[str, Any],
    new_path: str,
) -> dict[str, Any]:
    result = copy.deepcopy(record)
    result["video_path"] = new_path.replace("\\", "/")
    return result


def prepare_output_dir(
    output_dir: Path,
    overwrite: bool,
) -> Path:
    output_dir = output_dir.expanduser().resolve()

    if output_dir.exists():
        if not output_dir.is_dir():
            raise RuntimeError(f"输出路径不是目录：{output_dir}")

        if any(output_dir.iterdir()):
            if not overwrite:
                raise RuntimeError(
                    f"输出目录不是空目录：{output_dir}\n"
                    "请换一个目录，或增加 --overwrite。"
                )
            shutil.rmtree(output_dir)

    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def write_json(path: Path, value: Any) -> None:
    temp_path = path.with_name(path.name + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(
            value,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    temp_path.replace(path)


def print_progress(
    label: str,
    current: int,
    total: int,
    name: str,
) -> None:
    percent = 100.0 if total == 0 else current * 100.0 / total
    print(
        f"[{label}] {current}/{total} "
        f"({percent:6.2f}%) | {name}",
        flush=True,
    )


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "video"


def allocate_output_name(
    *,
    source: Path,
    video_id: str,
    used_names: set[str],
) -> str:
    candidate = source.name

    if candidate not in used_names:
        used_names.add(candidate)
        return candidate

    stem = safe_component(source.stem)
    suffix = source.suffix
    video_component = safe_component(video_id)

    candidate = f"{stem}__{video_component}{suffix}"
    counter = 2

    while candidate in used_names:
        candidate = (
            f"{stem}__{video_component}_{counter}{suffix}"
        )
        counter += 1

    used_names.add(candidate)
    return candidate


def build_partition(
    *,
    label: str,
    items: list[DatasetItem],
    output_dir: Path,
    json_name: str,
    overwrite: bool,
) -> tuple[int, int]:
    data_dir = prepare_output_dir(
        output_dir,
        overwrite,
    )

    output_records: list[dict[str, Any]] = []
    used_names: set[str] = set()

    for index, item in enumerate(items, start=1):
        output_name = allocate_output_name(
            source=item.source_video,
            video_id=item.record["video_id"],
            used_names=used_names,
        )
        destination = data_dir / output_name
        shutil.copy2(
            item.source_video,
            destination,
        )

        output_records.append(
            rewrite_video_path(
                item.record,
                f"data/{output_name}",
            )
        )
        print_progress(
            label,
            index,
            len(items),
            output_name,
        )

    output_json = output_dir.resolve() / json_name
    write_json(
        output_json,
        output_records,
    )

    actual_videos = len(
        [
            path
            for path in data_dir.iterdir()
            if path.is_file()
        ]
    )
    if actual_videos != len(items):
        raise RuntimeError(
            f"{label} 输出校验失败：应有 {len(items)} 个视频，"
            f"实际为 {actual_videos} 个。"
        )

    reloaded = load_dataset(output_json)
    if len(reloaded) != len(output_records):
        raise RuntimeError(
            f"{label} JSON 校验失败：应有 {len(output_records)} 条记录，"
            f"实际为 {len(reloaded)} 条。"
        )

    return actual_videos, len(output_records)


def split_dataset(args: argparse.Namespace) -> int:
    input_json = args.input_json.expanduser().resolve()
    selected_output = args.selected_output.expanduser().resolve()
    remaining_output = args.remaining_output.expanduser().resolve()

    if args.count <= 0:
        raise RuntimeError("--count 必须大于 0。")

    if selected_output == remaining_output:
        raise RuntimeError(
            "抽中输出目录和剩余输出目录不能相同。"
        )

    records = load_dataset(input_json)
    valid_items, missing_paths = collect_existing_items(
        input_json,
        records,
        label="检查",
    )

    if len(valid_items) < args.count:
        raise RuntimeError(
            f"有效视频不足：需要抽取 {args.count} 个，"
            f"实际只有 {len(valid_items)} 个。"
        )

    shuffled = valid_items[:]
    random.Random(args.seed).shuffle(shuffled)

    selected_items = shuffled[: args.count]
    selected_ids = {
        item.record["video_id"]
        for item in selected_items
    }
    remaining_items = [
        item
        for item in valid_items
        if item.record["video_id"] not in selected_ids
    ]

    selected_video_count, selected_record_count = build_partition(
        label="抽中部分",
        items=selected_items,
        output_dir=selected_output,
        json_name=input_json.name,
        overwrite=args.overwrite,
    )

    remaining_video_count, remaining_record_count = build_partition(
        label="剩余部分",
        items=remaining_items,
        output_dir=remaining_output,
        json_name=input_json.name,
        overwrite=args.overwrite,
    )

    if selected_ids & {
        item.record["video_id"]
        for item in remaining_items
    }:
        raise RuntimeError(
            "拆分校验失败：两份数据中出现重复 video_id。"
        )

    print("\n拆分完成")
    print(f"  输入JSON记录：   {len(records)}")
    print(f"  有效视频：       {len(valid_items)}")
    print(f"  缺失并排除：     {len(missing_paths)}")
    print(
        f"  抽中部分：       {selected_video_count} 个视频，"
        f"{selected_record_count} 条记录"
    )
    print(
        f"  剩余部分：       {remaining_video_count} 个视频，"
        f"{remaining_record_count} 条记录"
    )
    print(f"  随机种子：       {args.seed}")
    print(f"  抽中输出：       {selected_output}")
    print(f"  剩余输出：       {remaining_output}")
    return 0


def file_signature(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(4 * 1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return path.stat().st_size, digest.hexdigest()


def canonical_record(record: dict[str, Any]) -> str:
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def merge_datasets(args: argparse.Namespace) -> int:
    if len(args.input_jsons) < 2:
        raise RuntimeError(
            "merge 至少需要两份输入 JSON。"
        )

    if "/" in args.json_name or "\\" in args.json_name:
        raise RuntimeError(
            "--json-name 只能是文件名，不能包含目录。"
        )

    input_jsons = [
        path.expanduser().resolve()
        for path in args.input_jsons
    ]
    output_dir = args.output_dir.expanduser().resolve()
    data_dir = prepare_output_dir(
        output_dir,
        args.overwrite,
    )

    merged_records: list[dict[str, Any]] = []
    record_by_video_id: dict[str, dict[str, Any]] = {}

    signature_to_name: dict[tuple[int, str], str] = {}
    used_output_names: set[str] = set()

    copied_count = 0
    reused_video_count = 0
    duplicate_record_count = 0
    missing_count = 0

    for json_path in input_jsons:
        records = load_dataset(json_path)
        items, missing_paths = collect_existing_items(
            json_path,
            records,
            label="合并检查",
        )
        missing_count += len(missing_paths)

        for item in items:
            signature = file_signature(
                item.source_video
            )

            if signature in signature_to_name:
                output_name = signature_to_name[signature]
                reused_video_count += 1
            else:
                output_name = allocate_output_name(
                    source=item.source_video,
                    video_id=item.record["video_id"],
                    used_names=used_output_names,
                )
                shutil.copy2(
                    item.source_video,
                    data_dir / output_name,
                )
                signature_to_name[signature] = output_name
                copied_count += 1
                print(
                    f"[合并复制] {copied_count} | "
                    f"{item.source_video.name} -> {output_name}",
                    flush=True,
                )

            rewritten = rewrite_video_path(
                item.record,
                f"data/{output_name}",
            )
            video_id = rewritten["video_id"]

            previous = record_by_video_id.get(video_id)
            if previous is None:
                record_by_video_id[video_id] = rewritten
                merged_records.append(rewritten)
                continue

            if canonical_record(previous) == canonical_record(rewritten):
                duplicate_record_count += 1
                continue

            raise RuntimeError(
                f"合并时发现冲突的 video_id：{video_id}\n"
                "同一个 video_id 对应了不同的视频、问题或答案。"
            )

    output_json = output_dir / args.json_name
    write_json(
        output_json,
        merged_records,
    )

    actual_videos = len(
        [
            path
            for path in data_dir.iterdir()
            if path.is_file()
        ]
    )
    if actual_videos != copied_count:
        raise RuntimeError(
            f"合并校验失败：应复制 {copied_count} 个唯一视频，"
            f"实际为 {actual_videos} 个。"
        )

    reloaded = load_dataset(output_json)
    if len(reloaded) != len(merged_records):
        raise RuntimeError(
            f"合并JSON校验失败：应有 {len(merged_records)} 条记录，"
            f"实际为 {len(reloaded)} 条。"
        )

    print("\n合并完成")
    print(f"  输入数据集：     {len(input_jsons)} 份")
    print(f"  输出唯一视频：   {actual_videos}")
    print(f"  复用相同视频：   {reused_video_count}")
    print(f"  跳过重复记录：   {duplicate_record_count}")
    print(f"  缺失并排除：     {missing_count}")
    print(f"  输出JSON记录：   {len(merged_records)}")
    print(f"  输出JSON：       {output_json}")
    print(f"  视频目录：       {data_dir}")
    return 0


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "split":
        return split_dataset(args)

    if args.command == "merge":
        return merge_datasets(args)

    raise RuntimeError(
        f"未知命令：{args.command}"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
