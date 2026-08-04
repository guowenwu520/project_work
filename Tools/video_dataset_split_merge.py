#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
v15 桌面变化视频数据集拆分与合并工具。

支持当前按视频聚合的 JSON 格式：

[
  {
    "video": "data/video_000123.mp4",
    "scene_type": "tabletop",
    "metadata": {
      "object_replaced": true,
      "no_change": false,
      "...": "完整 v15 metadata"
    },
    "questions": [
      {
        "question": "What changed during the video?",
        "answer": "The apple was replaced by the cup.",
        "question_type": "descriptive"
      }
    ]
  }
]

拆分：

    python3 video_dataset_split_merge.py split \
        Output/videodata.json \
        Output/test100 \
        Output/remain \
        --count 100 \
        --seed 42

合并：

    python3 video_dataset_split_merge.py merge \
        Output/test100/videodata.json \
        Output/remain/videodata.json \
        --output-dir Output/merged

规则：

1. JSON 顶层必须是列表。
2. 每条记录必须包含 video、scene_type、metadata、questions。
3. video 必须指向对应 MP4；不再输出 video_id 或 video_path。
4. scene_type 必须为 tabletop。
5. metadata 必须包含当前 v15 的完整变化字段，不再输出
   change_type 或 change_exists，并使用 no_change 表示无变化。
6. 每个视频必须包含 8 组不重复问答。
7. 每组问答必须保留 question、answer、question_type。
8. question_type 只能为 descriptive 或 yes_or_no。
9. 拆分、合并只改输出视频路径，不改 metadata、问答描述或其他字段。
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
QUESTION_TYPES = {"descriptive", "yes_or_no"}
DISTANCE_CHANGES = {"none", "increased", "decreased"}
POSITION_NAMES = {"position_a", "position_b"}

METADATA_LIST_FIELDS = {
    "view_a_position_a",
    "view_a_position_b",
    "view_b_position_a",
    "view_b_position_b",
    "view_a_color_a",
    "view_a_color_b",
    "view_b_color_a",
    "view_b_color_b",
    "changed_positions",
}
METADATA_BOOL_FIELDS = {
    "object_replaced",
    "object_added",
    "object_removed",
    "color_changed",
    "position_changed",
    "distance_changed",
    "no_change",
}
METADATA_COUNT_FIELDS = {
    "view_a_object_count",
    "view_b_object_count",
}
REQUIRED_METADATA_FIELDS = (
    {"distance_change"}
    | METADATA_LIST_FIELDS
    | METADATA_BOOL_FIELDS
    | METADATA_COUNT_FIELDS
)


@dataclass(frozen=True)
class DatasetItem:
    """一条已完成校验、并且能够找到视频文件的数据。"""

    record: dict[str, Any]
    source_video: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="拆分或合并 v15 桌面变化视频数据集"
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
        help="随机抽取的视频数量，默认 100",
    )
    split_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="随机种子，默认 42",
    )
    split_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖非空输出目录",
    )
    split_parser.add_argument(
        "--require-all-videos",
        action="store_true",
        help="只要有一个 JSON 对应的 MP4 缺失就立即失败",
    )

    merge_parser = subparsers.add_parser(
        "merge",
        help="把两份或多份 v15 数据集合并为一份",
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
    merge_parser.add_argument(
        "--require-all-videos",
        action="store_true",
        help="只要有一个 JSON 对应的 MP4 缺失就立即失败",
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


def validate_string_list(
    value: Any,
    *,
    field: str,
    record_index: int,
    json_path: Path,
) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 "
            f"metadata.{field} 必须是列表。"
        )

    result: list[str] = []
    for value_index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(
                f"{json_path} 第 {record_index} 条记录的 "
                f"metadata.{field}[{value_index}] 必须是非空字符串。"
            )
        result.append(item)
    return result


def validate_metadata(
    value: Any,
    *,
    record_index: int,
    json_path: Path,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 metadata "
            "必须是 JSON 对象。"
        )

    removed = sorted(
        {"change_type", "change_exists"} & value.keys()
    )
    if removed:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 metadata "
            "仍包含 v15 已删除的冗余字段："
            f"{', '.join(removed)}"
        )

    missing = sorted(REQUIRED_METADATA_FIELDS - value.keys())
    if missing:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 metadata "
            f"缺少 v15 字段：{', '.join(missing)}"
        )

    distance_change = non_empty_string(
        value.get("distance_change"),
        field="metadata.distance_change",
        record_index=record_index,
        json_path=json_path,
    )
    if distance_change not in DISTANCE_CHANGES:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 "
            f"metadata.distance_change 为 {distance_change!r}，"
            f"允许值为：{', '.join(sorted(DISTANCE_CHANGES))}。"
        )

    for field in sorted(METADATA_BOOL_FIELDS):
        if not isinstance(value.get(field), bool):
            raise RuntimeError(
                f"{json_path} 第 {record_index} 条记录的 "
                f"metadata.{field} 必须是 true 或 false。"
            )

    for field in sorted(METADATA_COUNT_FIELDS):
        count = value.get(field)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RuntimeError(
                f"{json_path} 第 {record_index} 条记录的 "
                f"metadata.{field} 必须是非负整数。"
            )

    normalized = copy.deepcopy(value)
    normalized["distance_change"] = distance_change

    for field in sorted(METADATA_LIST_FIELDS):
        items = validate_string_list(
            value.get(field),
            field=field,
            record_index=record_index,
            json_path=json_path,
        )
        if field == "changed_positions":
            invalid = sorted(set(items) - POSITION_NAMES)
            if invalid:
                raise RuntimeError(
                    f"{json_path} 第 {record_index} 条记录的 "
                    "metadata.changed_positions 包含未知位置："
                    f"{', '.join(invalid)}"
                )
        normalized[field] = items

    distance_changed = normalized["distance_changed"]
    expected_distance_change = distance_change != "none"
    if distance_changed != expected_distance_change:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 "
            "metadata.distance_changed 与 distance_change 不一致。"
        )

    primary_flags = [
        normalized["object_replaced"],
        normalized["object_added"],
        normalized["object_removed"],
        normalized["color_changed"],
        normalized["no_change"],
        normalized["distance_changed"],
        normalized["position_changed"]
        and not normalized["distance_changed"],
    ]
    if sum(bool(flag) for flag in primary_flags) != 1:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 metadata "
            "变化标志不能唯一确定一种变化情况。"
        )

    if normalized["no_change"] and normalized["changed_positions"]:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录为 no_change，"
            "changed_positions 必须为空。"
        )

    return normalized


def validate_questions(
    value: Any,
    *,
    record_index: int,
    json_path: Path,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录的 "
            "questions 必须是列表。"
        )

    if len(value) != EXPECTED_QUESTIONS:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录包含 "
            f"{len(value)} 组问答，要求恰好为 "
            f"{EXPECTED_QUESTIONS} 组。"
        )

    result: list[dict[str, Any]] = []
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
        question_type = non_empty_string(
            item.get("question_type"),
            field=f"questions[{question_index}].question_type",
            record_index=record_index,
            json_path=json_path,
        )
        if question_type not in QUESTION_TYPES:
            raise RuntimeError(
                f"{json_path} 第 {record_index} 条记录的 "
                f"questions[{question_index}].question_type "
                f"为 {question_type!r}，只能使用 descriptive "
                "或 yes_or_no。"
            )

        if question in question_texts:
            raise RuntimeError(
                f"{json_path} 第 {record_index} 条记录中存在重复问题："
                f"{question}"
            )
        question_texts.add(question)

        # 保留当前和未来增加的问答字段，不改变文档中的问题和答案。
        normalized = copy.deepcopy(item)
        normalized["question"] = question
        normalized["answer"] = answer
        normalized["question_type"] = question_type
        result.append(normalized)

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

    if "conversations" in record:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录仍含 conversations。"
            "本脚本只支持当前 v15 的 questions 格式。"
        )

    removed = sorted(
        {"video_id", "video_path"} & record.keys()
    )
    if removed:
        raise RuntimeError(
            f"{json_path} 第 {record_index} 条记录仍包含 v15 "
            f"已删除的冗余字段：{', '.join(removed)}"
        )

    video = non_empty_string(
        record.get("video"),
        field="video",
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

    metadata = validate_metadata(
        record.get("metadata"),
        record_index=record_index,
        json_path=json_path,
    )
    questions = validate_questions(
        record.get("questions"),
        record_index=record_index,
        json_path=json_path,
    )

    return {
        "video": video,
        "scene_type": EXPECTED_SCENE_TYPE,
        "metadata": metadata,
        "questions": questions,
    }


def load_dataset(path: Path) -> list[dict[str, Any]]:
    root = load_json(path)

    if not isinstance(root, list):
        raise RuntimeError(
            f"{path} 的 JSON 顶层必须是视频记录列表，"
            "不支持 data/samples/records/items 包装结构。"
        )

    records = [
        validate_record(
            record,
            record_index=index,
            json_path=path,
        )
        for index, record in enumerate(root, start=1)
    ]

    seen_videos: dict[str, int] = {}

    for index, record in enumerate(records, start=1):
        video = record["video"]
        if video in seen_videos:
            raise RuntimeError(
                f"{path} 中 video 重复：{video}\n"
                f"第 {seen_videos[video]} 条和第 {index} 条。"
            )
        seen_videos[video] = index

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
    require_all_videos: bool,
) -> tuple[list[DatasetItem], list[str]]:
    items: list[DatasetItem] = []
    missing: list[str] = []

    print(
        f"[{label}] JSON 中共有 {len(records)} 条视频记录",
        flush=True,
    )

    for index, record in enumerate(records, start=1):
        video_path = record["video"]
        source = find_video(json_path, video_path)

        if source is None:
            missing.append(video_path)
            print(
                f"[{label}] {index}/{len(records)} | "
                f"视频缺失：{video_path}",
                flush=True,
            )
            continue

        items.append(
            DatasetItem(
                record=record,
                source_video=source,
            )
        )

    if require_all_videos and missing:
        raise RuntimeError(
            f"{json_path} 有 {len(missing)} 个视频缺失；"
            "已启用 --require-all-videos，因此停止处理。"
        )

    return items, missing


def rewrite_video_path(
    record: dict[str, Any],
    new_path: str,
) -> dict[str, Any]:
    result = copy.deepcopy(record)
    normalized = new_path.replace("\\", "/")
    result["video"] = normalized
    return result


def prepare_output_dir(
    output_dir: Path,
    overwrite: bool,
) -> Path:
    output_dir = output_dir.expanduser().resolve()

    protected = {
        Path(output_dir.anchor).resolve(),
        Path.home().resolve(),
        Path.cwd().resolve(),
    }
    if output_dir in protected:
        raise RuntimeError(
            f"拒绝把过大的目录作为输出目录：{output_dir}"
        )

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
    record_key: str,
    used_names: set[str],
) -> str:
    candidate = source.name

    if candidate not in used_names:
        used_names.add(candidate)
        return candidate

    stem = safe_component(source.stem)
    suffix = source.suffix
    video_component = safe_component(record_key)

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
    output_dir = output_dir.expanduser().resolve()
    data_dir = prepare_output_dir(
        output_dir,
        overwrite,
    )

    output_records: list[dict[str, Any]] = []
    used_names: set[str] = set()

    for index, item in enumerate(items, start=1):
        output_name = allocate_output_name(
            source=item.source_video,
            record_key=record_identity(item.record),
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

    output_json = output_dir / json_name
    write_json(
        output_json,
        output_records,
    )

    actual_videos = sum(
        1
        for path in data_dir.iterdir()
        if path.is_file()
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
        require_all_videos=args.require_all_videos,
    )

    if len(valid_items) < args.count:
        raise RuntimeError(
            f"有效视频不足：需要抽取 {args.count} 个，"
            f"实际只有 {len(valid_items)} 个。"
        )

    shuffled = valid_items[:]
    random.Random(args.seed).shuffle(shuffled)

    selected_items = shuffled[: args.count]
    selected_videos = {
        item.record["video"]
        for item in selected_items
    }
    remaining_items = [
        item
        for item in valid_items
        if item.record["video"] not in selected_videos
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

    if selected_videos & {
        item.record["video"]
        for item in remaining_items
    }:
        raise RuntimeError(
            "拆分校验失败：两份数据中出现重复 video。"
        )

    print("\n拆分完成")
    print(f"  输入 JSON 记录： {len(records)}")
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


def canonical_record_without_video(
    record: dict[str, Any],
) -> str:
    normalized = copy.deepcopy(record)
    normalized.pop("video", None)
    return canonical_record(normalized)


def record_identity(record: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        canonical_record_without_video(record).encode("utf-8")
    ).hexdigest()[:12]
    stem = safe_component(Path(record["video"]).stem)
    return f"{stem}_{digest}"


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
    seen_records: set[tuple[str, tuple[int, str]]] = set()

    used_output_names: set[str] = set()

    copied_count = 0
    duplicate_record_count = 0
    missing_count = 0

    for json_path in input_jsons:
        records = load_dataset(json_path)
        items, missing_paths = collect_existing_items(
            json_path,
            records,
            label="合并检查",
            require_all_videos=args.require_all_videos,
        )
        missing_count += len(missing_paths)

        for item in items:
            signature = file_signature(
                item.source_video
            )
            canonical = canonical_record_without_video(item.record)
            identity = (canonical, signature)
            if identity in seen_records:
                duplicate_record_count += 1
                continue

            # v15 不再输出 video_id。只有视频字节以及 metadata/问答都
            # 相同的记录才视为重复；其他记录即使文件名相同也分别保留。
            record_key = record_identity(item.record)
            output_name = allocate_output_name(
                source=item.source_video,
                record_key=record_key,
                used_names=used_output_names,
            )
            shutil.copy2(
                item.source_video,
                data_dir / output_name,
            )
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
            seen_records.add(identity)
            merged_records.append(rewritten)

    output_json = output_dir / args.json_name
    write_json(
        output_json,
        merged_records,
    )

    actual_videos = sum(
        1
        for path in data_dir.iterdir()
        if path.is_file()
    )
    if actual_videos != copied_count:
        raise RuntimeError(
            f"合并校验失败：应复制 {copied_count} 个唯一视频，"
            f"实际为 {actual_videos} 个。"
        )

    reloaded = load_dataset(output_json)
    if len(reloaded) != len(merged_records):
        raise RuntimeError(
            f"合并 JSON 校验失败：应有 {len(merged_records)} 条记录，"
            f"实际为 {len(reloaded)} 条。"
        )

    print("\n合并完成")
    print(f"  输入数据集：     {len(input_jsons)} 份")
    print(f"  输出唯一视频：   {actual_videos}")
    print(f"  跳过重复记录：   {duplicate_record_count}")
    print(f"  缺失并排除：     {missing_count}")
    print(f"  输出 JSON 记录： {len(merged_records)}")
    print(f"  输出 JSON：      {output_json}")
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
