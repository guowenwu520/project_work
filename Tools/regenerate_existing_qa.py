#!/usr/bin/env python3
"""Regenerate QA for existing videos without running Unity or rerendering."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import os
import posixpath
import re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
QA_XOR = 0x5F3759DF
XLSX_MAIN_NS = (
    "http://schemas.openxmlformats.org/"
    "spreadsheetml/2006/main"
)
XLSX_REL_NS = (
    "http://schemas.openxmlformats.org/"
    "officeDocument/2006/relationships"
)
XLSX_PACKAGE_REL_NS = (
    "http://schemas.openxmlformats.org/"
    "package/2006/relationships"
)

QA_SOURCE_SHEETS = [
    {
        "sheet_name": "01_Replacement",
        "change_type": "one_object_replacement",
        "prefix": "replacement",
        "name_cn": "物体替换",
        "description": (
            "One object is replaced while the other object "
            "remains unchanged."
        ),
    },
    {
        "sheet_name": "02_Color_Change",
        "change_type": "same_object_color_change",
        "prefix": "color_change",
        "name_cn": "颜色变化",
        "description": "One object changes color.",
    },
    {
        "sheet_name": "03_Distance_Increase",
        "change_type": "distance_increase",
        "prefix": "distance_increase",
        "name_cn": "距离增大",
        "description": (
            "One object moves and the distance between the two "
            "objects increases."
        ),
    },
    {
        "sheet_name": "04_Distance_Decrease",
        "change_type": "distance_decrease",
        "prefix": "distance_decrease",
        "name_cn": "距离减小",
        "description": (
            "One object moves and the distance between the two "
            "objects decreases."
        ),
    },
    {
        "sheet_name": "05_Position_Swap",
        "change_type": "swap_positions",
        "prefix": "swap_positions",
        "name_cn": "位置交换",
        "description": "Two objects exchange positions.",
    },
    {
        "sheet_name": "06_No_Change",
        "change_type": "no_change",
        "prefix": "no_change",
        "name_cn": "无变化",
        "description": "The tabletop state remains unchanged.",
    },
    {
        "sheet_name": "07_Object_Adding",
        "change_type": "object_adding",
        "prefix": "object_adding",
        "name_cn": "增加物体",
        "description": (
            "One object is added, changing the count from one to two."
        ),
    },
    {
        "sheet_name": "08_Object_Deleting",
        "change_type": "object_deleting",
        "prefix": "object_deleting",
        "name_cn": "减少物体",
        "description": (
            "One object is removed, changing the count from two to one."
        ),
    },
]

SUPPORTED_QA_VARIABLES = {
    "view_a_object_a",
    "view_b_object_a",
    "view_a_object_b",
    "view_b_object_b",
    "view_a_color_a",
    "view_b_color_a",
    "view_a_count",
    "view_b_count",
    "view_a_position_a",
    "view_b_position_a",
    "view_a_position_b",
    "view_b_position_b",
    "view_a_object_list",
    "view_b_object_list",
}

FIXED_POSITION_VALUES = {
    "view_a_position_a":
        "the left side (1st view) of the table",
    "view_a_position_b":
        "the right side (1st view) of the table",
    "view_b_position_a":
        "the right side (2nd view) of the table",
    "view_b_position_b":
        "the left side (2nd view) of the table",
}


def xlsx_cell_column(reference: str) -> int:
    match = re.match(r"([A-Za-z]+)", reference or "")
    if not match:
        return 0

    index = 0
    for char in match.group(1).upper():
        index = index * 26 + ord(char) - ord("A") + 1
    return max(0, index - 1)


def xlsx_rich_text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return "".join(
        node.text or ""
        for node in element.iter(
            f"{{{XLSX_MAIN_NS}}}t"
        )
    )


def read_xlsx_rows(
    workbook_path: Path,
) -> tuple[list[str], dict[str, list[list[str]]]]:
    """Read the simple text tables used by the QA workbook.

    This uses only Python's standard library so the build server does not
    need openpyxl, LibreOffice, Node.js, or another generated helper script.
    """

    try:
        archive = zipfile.ZipFile(workbook_path)
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError(
            f"Invalid XLSX workbook: {workbook_path}: {error}"
        ) from error

    with archive:
        try:
            workbook_xml = ElementTree.fromstring(
                archive.read("xl/workbook.xml")
            )
            relationships_xml = ElementTree.fromstring(
                archive.read(
                    "xl/_rels/workbook.xml.rels"
                )
            )
        except (KeyError, ElementTree.ParseError) as error:
            raise ValueError(
                f"XLSX workbook structure is invalid: "
                f"{workbook_path}: {error}"
            ) from error

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ElementTree.fromstring(
                archive.read("xl/sharedStrings.xml")
            )
            shared_strings = [
                xlsx_rich_text(item)
                for item in shared_root.findall(
                    f"{{{XLSX_MAIN_NS}}}si"
                )
            ]

        relationship_targets = {
            relation.get("Id", ""):
            relation.get("Target", "")
            for relation in relationships_xml.findall(
                f"{{{XLSX_PACKAGE_REL_NS}}}Relationship"
            )
        }

        sheet_names: list[str] = []
        rows_by_sheet: dict[str, list[list[str]]] = {}
        sheets_node = workbook_xml.find(
            f"{{{XLSX_MAIN_NS}}}sheets"
        )
        if sheets_node is None:
            raise ValueError(
                f"XLSX workbook contains no sheets: {workbook_path}"
            )

        for sheet in sheets_node.findall(
            f"{{{XLSX_MAIN_NS}}}sheet"
        ):
            sheet_name = str(sheet.get("name") or "").strip()
            relationship_id = sheet.get(
                f"{{{XLSX_REL_NS}}}id",
                "",
            )
            target = relationship_targets.get(
                relationship_id,
                "",
            )
            if not sheet_name or not target:
                continue

            if target.startswith("/"):
                sheet_member = target.lstrip("/")
            elif target.startswith("xl/"):
                sheet_member = target
            else:
                sheet_member = posixpath.normpath(
                    posixpath.join("xl", target)
                )

            try:
                sheet_root = ElementTree.fromstring(
                    archive.read(sheet_member)
                )
            except (KeyError, ElementTree.ParseError) as error:
                raise ValueError(
                    f"Cannot read sheet {sheet_name!r} from "
                    f"{workbook_path}: {error}"
                ) from error

            row_values: dict[int, dict[int, str]] = {}
            for row in sheet_root.findall(
                ".//"
                f"{{{XLSX_MAIN_NS}}}sheetData/"
                f"{{{XLSX_MAIN_NS}}}row"
            ):
                row_number = int(row.get("r") or 0)
                if row_number <= 0:
                    continue
                values: dict[int, str] = {}
                for cell in row.findall(
                    f"{{{XLSX_MAIN_NS}}}c"
                ):
                    column = xlsx_cell_column(
                        cell.get("r") or ""
                    )
                    cell_type = cell.get("t") or ""
                    value_node = cell.find(
                        f"{{{XLSX_MAIN_NS}}}v"
                    )
                    raw = (
                        value_node.text
                        if value_node is not None and
                        value_node.text is not None
                        else ""
                    )

                    if cell_type == "s" and raw:
                        try:
                            value = shared_strings[int(raw)]
                        except (ValueError, IndexError) as error:
                            raise ValueError(
                                f"{sheet_name}!{cell.get('r')}: "
                                "invalid shared-string index"
                            ) from error
                    elif cell_type == "inlineStr":
                        value = xlsx_rich_text(
                            cell.find(
                                f"{{{XLSX_MAIN_NS}}}is"
                            )
                        )
                    elif cell_type == "b":
                        value = "true" if raw == "1" else "false"
                    else:
                        value = raw

                    values[column] = value
                row_values[row_number] = values

            max_row = max(row_values, default=0)
            max_column = max(
                (
                    max(values, default=-1)
                    for values in row_values.values()
                ),
                default=-1,
            )
            matrix: list[list[str]] = []
            for row_number in range(1, max_row + 1):
                values = row_values.get(row_number, {})
                matrix.append(
                    [
                        values.get(column, "")
                        for column in range(max_column + 1)
                    ]
                )

            sheet_names.append(sheet_name)
            rows_by_sheet[sheet_name] = matrix

    return sheet_names, rows_by_sheet


def unique_placeholders(
    question: str,
    answer: str,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for text in (question, answer):
        for match in PLACEHOLDER_RE.finditer(text):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                values.append(name)
    return values


def normalize_sequence(value: Any, fallback: int) -> str:
    text = str(value or "").strip()
    if not text:
        return str(fallback)
    if re.fullmatch(r"[0-9]+(?:\.0+)?", text):
        return str(int(float(text)))
    return text


def build_runtime_library_from_workbook(
    workbook_path: Path,
    template_path: Path,
) -> dict[str, Any]:
    sheet_names, rows_by_sheet = read_xlsx_rows(
        workbook_path
    )
    required_sheet_names = [
        source["sheet_name"]
        for source in QA_SOURCE_SHEETS
    ]
    missing_sheets = [
        name
        for name in required_sheet_names + ["Variables"]
        if name not in rows_by_sheet
    ]
    if missing_sheets:
        raise ValueError(
            "The QA workbook is missing required sheets: "
            + ", ".join(missing_sheets)
        )

    variable_definitions: list[dict[str, str]] = []
    variable_rows = rows_by_sheet["Variables"]
    for row_number, row in enumerate(
        variable_rows[3:],
        start=4,
    ):
        values = list(row) + ["", "", "", ""]
        variable = str(values[0] or "").strip()
        if not variable:
            continue
        variable_definitions.append(
            {
                "variable": variable,
                "english_meaning":
                    str(values[1] or "").strip(),
                "typical_english_value":
                    str(values[2] or "").strip(),
                "note": str(values[3] or "").strip(),
                "source_row": row_number,
            }
        )

    typical_values = {
        item["variable"]: item["typical_english_value"]
        for item in variable_definitions
        if item["variable"] in SUPPORTED_QA_VARIABLES
    }

    groups: list[dict[str, Any]] = []
    source_qa_core: list[dict[str, Any]] = []
    used_placeholders: set[str] = set()

    for source in QA_SOURCE_SHEETS:
        sheet_name = source["sheet_name"]
        rows = rows_by_sheet[sheet_name]
        templates: list[dict[str, Any]] = []

        for row_number, row in enumerate(
            rows[1:],
            start=2,
        ):
            values = list(row) + ["", "", "", "", ""]
            question = str(values[3] or "")
            answer = str(values[4] or "")
            if not question.strip() and not answer.strip():
                continue
            if not question.strip() or not answer.strip():
                raise ValueError(
                    f"{sheet_name} row {row_number} must contain "
                    "both an English question and an English answer."
                )

            sequence = normalize_sequence(
                values[1],
                len(templates) + 1,
            )
            template_id = (
                f"{source['prefix']}_{sequence.zfill(2)}"
            )
            answer_style = str(
                values[2] or "descriptive"
            ).strip()
            normalized_style = normalize_question_type(
                answer_style
            )
            if answer_style.strip().lower().replace(
                "-",
                "_",
            ).replace(" ", "_") not in {
                "descriptive",
                "yes_no",
                "yes_or_no",
            }:
                raise ValueError(
                    f"{sheet_name} row {row_number} has unsupported "
                    f"answer type {answer_style!r}."
                )

            required_variables = unique_placeholders(
                question,
                answer,
            )
            used_placeholders.update(required_variables)
            template = {
                "template_id": template_id,
                "question": question,
                "answer": answer,
                "required_variables": required_variables,
                "answer_style": (
                    "yes_no"
                    if normalized_style == "yes_or_no"
                    else "descriptive"
                ),
                "source_sheet": sheet_name,
                "source_row": row_number,
            }
            templates.append(template)
            source_qa_core.append(
                {
                    "change_type": source["change_type"],
                    "template_id": template_id,
                    "question": question,
                    "answer": answer,
                    "answer_style": template["answer_style"],
                    "source_sheet": sheet_name,
                    "source_row": row_number,
                }
            )

        template_ids = [
            template["template_id"]
            for template in templates
        ]
        if len(template_ids) != len(set(template_ids)):
            raise ValueError(
                f"{sheet_name} contains duplicate template IDs."
            )
        if len(templates) < 8:
            raise ValueError(
                f"{sheet_name} contains only {len(templates)} "
                "templates; at least 8 are required."
            )

        groups.append(
            {
                "change_type": source["change_type"],
                "name_cn": source["name_cn"],
                "description": source["description"],
                "source_sheet": sheet_name,
                "templates": templates,
            }
        )

    unsupported_variables = sorted(
        used_placeholders - SUPPORTED_QA_VARIABLES
    )
    if unsupported_variables:
        raise ValueError(
            "The workbook uses new QA variables that the current "
            "scene code does not support: "
            + ", ".join(unsupported_variables)
            + ". Update the code before adding new variables."
        )

    missing_variables = sorted(
        name
        for name in used_placeholders
        if not typical_values.get(name)
    )
    if missing_variables:
        raise ValueError(
            "Variables is missing a typical English value for: "
            + ", ".join(missing_variables)
        )

    for name, expected in FIXED_POSITION_VALUES.items():
        if typical_values.get(name) != expected:
            raise ValueError(
                f"Variables!{name} must remain {expected!r}; "
                f"found {typical_values.get(name)!r}."
            )

    template_counts = {
        group["change_type"]: len(group["templates"])
        for group in groups
    }
    total_templates = sum(template_counts.values())
    source_qa_payload = json.dumps(
        source_qa_core,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    source_qa_sha256 = hashlib.sha256(
        source_qa_payload
    ).hexdigest()
    source_sheets_payload = json.dumps(
        [
            {
                "sheet": source["sheet_name"],
                "rows": template_counts[source["change_type"]],
            }
            for source in QA_SOURCE_SHEETS
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    source_sheets_sha256 = hashlib.sha256(
        source_sheets_payload
    ).hexdigest()
    workbook_sha256 = hashlib.sha256(
        workbook_path.read_bytes()
    ).hexdigest()

    sampling_salt = 20260726
    if template_path.is_file():
        try:
            old_library = json.loads(
                template_path.read_text(encoding="utf-8")
            )
            sampling_salt = int(
                old_library.get(
                    "sampling_salt",
                    sampling_salt,
                )
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    return {
        "schema_version": "3.7",
        "content_revision": (
            "xlsx-auto-sync-sheet01-08-"
            + source_qa_sha256[:16]
        ),
        "source_workbook": workbook_path.name,
        "source_workbook_sha256": workbook_sha256,
        "qa_source_policy": (
            "Only sheets 01-08 are authoritative for QA wording."
        ),
        "qa_source_sheets": required_sheet_names,
        "ignored_qa_sheets": [
            name
            for name in sheet_names
            if name not in required_sheet_names
        ],
        "variable_source_sheet": "Variables",
        "supported_qa_variables": sorted(
            SUPPORTED_QA_VARIABLES
        ),
        "scene_type": "tabletop",
        "questions_per_scene": 8,
        "color_missing_value": "Null",
        "total_templates": total_templates,
        "template_counts": template_counts,
        "source_qa_sha256": source_qa_sha256,
        "source_sheets_sha256": source_sheets_sha256,
        "typical_variable_values": typical_values,
        "sampling_salt": sampling_salt,
        "change_types": groups,
    }


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
        "--workbook",
        default=str(project_root / "QAs_v5_d.xlsx"),
        help=(
            "Authoritative XLSX workbook. Sheets 01-08 supply the "
            "exact English QA wording and Variables supplies typical "
            "placeholder values."
        ),
    )
    parser.add_argument(
        "--sync-templates-only",
        action="store_true",
        help=(
            "Validate the workbook, update the runtime QA JSON, "
            "print its coverage, and exit without reading videos."
        ),
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
        "color_change": "same_object_color_change",
        "same_object_color_change": "same_object_color_change",
        "distance_increase": "distance_increase",
        "distance_decrease": "distance_decrease",
        "position_swap": "swap_positions",
        "swap_position": "swap_positions",
        "swap_positions": "swap_positions",
        "none": "no_change",
        "no_change": "no_change",
        "object_addition": "object_adding",
        "object_adding": "object_adding",
        "object_removal": "object_deleting",
        "object_deleting": "object_deleting",
    }
    return aliases.get(key, key)


def expected_changed_slot(change_type: Any) -> str:
    normalized = normalize_change_type(change_type)
    if normalized in {
        "object_adding",
        "object_deleting",
    }:
        return "right"
    if normalized == "swap_positions":
        return "both"
    if normalized == "no_change":
        return "none"
    if normalized in {
        "one_object_replacement",
        "same_object_color_change",
        "distance_increase",
        "distance_decrease",
    }:
        return "left"
    raise ValueError(
        f"Unsupported change type {change_type!r}."
    )


def comparable_state(state: Any) -> tuple[Any, ...]:
    state = state if isinstance(state, dict) else {}
    return (
        bool(state.get("present", True)) if state else False,
        str(state.get("propClass") or "").strip().casefold(),
        str(state.get("label") or "").strip().casefold(),
        str(state.get("color") or "").strip().casefold(),
        bool(state.get("supportsColor", False)),
    )


def validate_scene_correspondence(
    annotation: dict[str, Any],
    source: Path,
) -> None:
    change_type = normalize_change_type(
        annotation.get("changeType")
    )
    expected_slot = expected_changed_slot(change_type)
    actual_slot = str(
        annotation.get("changedSlot") or ""
    ).strip().lower()
    if actual_slot != expected_slot:
        raise ValueError(
            f"{source}: changeType={change_type!r} must use "
            f"changedSlot={expected_slot!r}, not {actual_slot!r}. "
            "The fixed sheet wording maps physical left to position A "
            "(left in the first view, right in the second view) and "
            "physical right to position B (right in the first view, "
            "left in the second view). QA-only regeneration cannot "
            "repair a video rendered with the wrong changed slot; "
            "rerender this scene."
        )

    left_before = annotation.get("leftBefore")
    right_before = annotation.get("rightBefore")
    left_after = annotation.get("leftAfter")
    right_after = annotation.get("rightAfter")
    lb = comparable_state(left_before)
    rb = comparable_state(right_before)
    la = comparable_state(left_after)
    ra = comparable_state(right_after)

    def present(state: Any) -> bool:
        return isinstance(state, dict) and bool(
            state.get("present", True)
        )

    mismatch = ""
    if change_type == "one_object_replacement":
        if (
            not all(
                present(state)
                for state in (
                    left_before,
                    right_before,
                    left_after,
                    right_after,
                )
            )
            or lb[1] == la[1]
            or rb != ra
        ):
            mismatch = (
                "only the first-view left object A must be replaced"
            )
    elif change_type == "same_object_color_change":
        if (
            not all(
                present(state)
                for state in (
                    left_before,
                    right_before,
                    left_after,
                    right_after,
                )
            )
            or lb[1] != la[1]
            or lb[3] == la[3]
            or rb != ra
        ):
            mismatch = (
                "only object A's color may change"
            )
    elif change_type in {
        "distance_increase",
        "distance_decrease",
    }:
        if (
            not all(
                present(state)
                for state in (
                    left_before,
                    right_before,
                    left_after,
                    right_after,
                )
            )
            or lb != la
            or rb != ra
        ):
            mismatch = (
                "object identities and colors must stay unchanged "
                "while the first-view left object A moves"
            )
    elif change_type == "object_adding":
        if (
            not present(left_before)
            or not present(left_after)
            or lb != la
            or present(right_before)
            or not present(right_after)
        ):
            mismatch = (
                "object A must remain on the first-view left and the "
                "new object B must be added on the first-view right"
            )
    elif change_type == "object_deleting":
        if (
            not present(left_before)
            or not present(left_after)
            or lb != la
            or not present(right_before)
            or present(right_after)
        ):
            mismatch = (
                "object A must remain on the first-view left and "
                "object B must be removed from the first-view right"
            )
    elif change_type == "swap_positions":
        if (
            not all(
                present(state)
                for state in (
                    left_before,
                    right_before,
                    left_after,
                    right_after,
                )
            )
            or lb != ra
            or rb != la
        ):
            mismatch = "the two object states must exchange physical slots"
    elif change_type == "no_change":
        if lb != la or rb != ra:
            mismatch = "both object states must stay unchanged"

    if mismatch:
        raise ValueError(
            f"{source}: scene-state mismatch for "
            f"{change_type!r}: {mismatch}. QA-only regeneration "
            "cannot repair the rendered MP4; rerender this scene."
        )


def normalize_question_type(value: Any) -> str:
    key = (
        str(value or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if key in {"yes_no", "yes_or_no"}:
        return "yes_or_no"
    return "descriptive"


def state_description(state: dict[str, Any] | None) -> str:
    state = state or {}
    if not bool(state.get("present", True)):
        return "no object"
    label = str(state.get("label") or "item").strip()
    color = str(state.get("color") or "").strip()
    supports_color = bool(state.get("supportsColor", False))

    if supports_color and color:
        return f"{color} {label}".strip()
    return label


def state_label(state: dict[str, Any] | None) -> str:
    return str((state or {}).get("label") or "item").strip()


def state_is_present(state: dict[str, Any] | None) -> bool:
    if not isinstance(state, dict) or not state:
        return False
    return bool(state.get("present", True))


def state_object_list(
    state: dict[str, Any] | None,
) -> list[str]:
    if not state_is_present(state):
        return []
    state = state or {}
    label = str(
        state.get("label")
        or state.get("propClass")
        or ""
    ).strip()
    return [label] if label else []


def state_color_list(
    state: dict[str, Any] | None,
) -> list[str]:
    if not state_is_present(state):
        return []
    color = str((state or {}).get("color") or "").strip()
    return [color or "Null"]


def metadata_change_type(value: Any) -> str:
    change_type = normalize_change_type(value)
    return {
        "one_object_replacement": "replacement",
        "same_object_color_change": "color_change",
        "distance_increase": "distance_increase",
        "distance_decrease": "distance_decrease",
        "swap_positions": "position_swap",
        "no_change": "no_change",
        "object_adding": "object_adding",
        "object_deleting": "object_deleting",
    }.get(change_type, change_type or "no_change")


def build_metadata(
    annotation: dict[str, Any],
) -> dict[str, Any]:
    change_type = normalize_change_type(
        annotation.get("changeType")
    )
    changed_slot = str(
        annotation.get("changedSlot") or ""
    ).strip().lower()
    left_before = annotation.get("leftBefore") or {}
    right_before = annotation.get("rightBefore") or {}
    left_after = annotation.get("leftAfter") or {}
    right_after = annotation.get("rightAfter") or {}

    initial_count = annotation.get("initialObjectCount")
    if initial_count is None:
        initial_count = sum(
            state_is_present(state)
            for state in (left_before, right_before)
        )
    final_count = annotation.get("finalObjectCount")
    if final_count is None:
        final_count = sum(
            state_is_present(state)
            for state in (left_after, right_after)
        )

    changed_positions: list[str] = []
    if changed_slot == "left":
        changed_positions.append("position_a")
    elif changed_slot == "right":
        changed_positions.append("position_b")
    elif changed_slot == "both":
        changed_positions.extend(
            ["position_a", "position_b"]
        )

    distance_changed = change_type in {
        "distance_increase",
        "distance_decrease",
    }
    return {
        "change_type": metadata_change_type(change_type),
        "change_exists": change_type != "no_change",
        "view_a_object_count": int(initial_count),
        "view_b_object_count": int(final_count),
        "view_a_position_a": state_object_list(left_before),
        "view_a_position_b": state_object_list(right_before),
        "view_b_position_a": state_object_list(left_after),
        "view_b_position_b": state_object_list(right_after),
        "view_a_color_a": state_color_list(left_before),
        "view_a_color_b": state_color_list(right_before),
        "view_b_color_a": state_color_list(left_after),
        "view_b_color_b": state_color_list(right_after),
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

    def view_a_position_a() -> str:
        return "the left side (1st view) of the table"

    def view_a_position_b() -> str:
        return "the right side (1st view) of the table"

    def view_b_position_a() -> str:
        return "the right side (2nd view) of the table"

    def view_b_position_b() -> str:
        return "the left side (2nd view) of the table"

    def color_value(state: dict[str, Any]) -> str:
        return str(state.get("color") or "").strip() or "Null"

    left_before = annotation.get("leftBefore") or {}
    right_before = annotation.get("rightBefore") or {}
    left_after = annotation.get("leftAfter") or {}
    right_after = annotation.get("rightAfter") or {}

    initial_count = annotation.get("initialObjectCount")
    if initial_count is None:
        initial_count = sum(
            state_is_present(state)
            for state in (left_before, right_before)
        )
    final_count = annotation.get("finalObjectCount")
    if final_count is None:
        final_count = sum(
            state_is_present(state)
            for state in (left_after, right_after)
        )

    put("view_a_count", initial_count)
    put("view_b_count", final_count)

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
        unchanged_before = (
            right_before if changed_left else left_before
        )
        unchanged_after = (
            right_after if changed_left else left_after
        )

        put("view_a_object_a", state_description(before))
        put("view_b_object_a", state_description(after))
        put("view_a_object_b", state_description(unchanged_before))
        put("view_b_object_b", state_description(unchanged_after))
        put("view_a_position_a", view_a_position_a())
        put("view_b_position_a", view_b_position_a())

    elif change_type == "same_object_color_change":
        changed_left = is_left(changed_slot)
        before = left_before if changed_left else right_before
        after = left_after if changed_left else right_after
        unchanged_before = (
            right_before if changed_left else left_before
        )
        unchanged_after = (
            right_after if changed_left else left_after
        )

        put("view_a_object_a", state_label(before))
        put("view_b_object_a", state_label(after))
        put("view_a_object_b", state_description(unchanged_before))
        put("view_b_object_b", state_description(unchanged_after))
        put("view_a_color_a", color_value(before))
        put("view_b_color_a", color_value(after))
        put("view_a_position_a", view_a_position_a())
        put("view_b_position_a", view_b_position_a())

    elif change_type in {
        "distance_increase",
        "distance_decrease",
    }:
        moved_left = is_left(changed_slot)
        moving_before = left_before if moved_left else right_before
        moving_after = left_after if moved_left else right_after
        stationary_before = (
            right_before if moved_left else left_before
        )
        stationary_after = (
            right_after if moved_left else left_after
        )

        put("view_a_object_a", state_description(moving_before))
        put("view_a_object_b", state_description(stationary_before))
        put("view_b_object_a", state_description(moving_after))
        put("view_b_object_b", state_description(stationary_after))
        put("view_a_position_a", view_a_position_a())
        put("view_a_position_b", view_a_position_b())
        put("view_b_position_a", view_b_position_a())
        put("view_b_position_b", view_b_position_b())

    elif change_type == "swap_positions":
        put("view_a_object_a", state_description(left_before))
        put("view_a_object_b", state_description(right_before))
        put("view_b_object_a", state_description(right_after))
        put("view_b_object_b", state_description(left_after))
        put("view_a_position_a", view_a_position_a())
        put("view_a_position_b", view_a_position_b())
        put("view_b_position_a", view_b_position_a())
        put("view_b_position_b", view_b_position_b())

    elif change_type == "object_adding":
        added_left = is_left(changed_slot)
        original = right_after if added_left else left_after
        added = left_after if added_left else right_after

        put("view_a_object_a", state_description(original))
        put("view_b_object_a", state_description(original))
        put("view_b_object_b", state_description(added))
        put("view_a_position_a", view_a_position_a())
        put("view_b_position_a", view_b_position_a())
        put("view_b_position_b", view_b_position_b())

    elif change_type == "object_deleting":
        deleted_left = is_left(changed_slot)
        removed = left_before if deleted_left else right_before
        remaining = right_after if deleted_left else left_after

        put("view_a_object_a", state_description(remaining))
        put("view_a_object_b", state_description(removed))
        put("view_a_position_a", view_a_position_a())
        put("view_a_position_b", view_a_position_b())
        put("view_b_object_a", state_description(remaining))
        put("view_b_position_a", view_b_position_a())

    elif change_type == "no_change":
        put("view_a_object_a", state_description(left_before))
        put("view_a_object_b", state_description(right_before))
        put("view_b_object_a", state_description(left_after))
        put("view_b_object_b", state_description(right_after))
        put("view_a_position_a", view_a_position_a())
        put("view_a_position_b", view_a_position_b())
        put("view_b_position_a", view_b_position_a())
        put("view_b_position_b", view_b_position_b())
        put("view_a_color_a", color_value(left_before))
        put("view_b_color_a", color_value(left_after))

        put(
            "view_b_object_list",
            "The "
            + state_description(left_after)
            + " and the "
            + state_description(right_after),
        )

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

    for template in templates:
        question = render(template.get("question", ""), context)
        answer = render(template.get("answer", ""), context)

        if question is None or answer is None:
            continue

        template_id = str(
            template.get("template_id") or ""
        ).strip()
        if not template_id:
            raise ValueError(
                f"{change_type} contains a template without template_id"
            )

        rendered.append(
            {
                "template_id": template_id,
                "question": question,
                "answer": answer,
                "question_type": normalize_question_type(
                    template.get("answer_style")
                ),
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

    A 30-template pool follows the same rule and completes in 4 videos
    (30 unseen selections plus 2 repeated selections). Each change type
    owns an independent scheduler.
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
        seen_ids: set[str] = set()

        for entry in rendered_templates:
            template_id = entry["template_id"]
            question = entry["question"]

            if template_id in seen_ids:
                continue

            seen_ids.add(template_id)
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
        unseen_question_counts = Counter(
            entry["question"]
            for entry in unseen_candidates
        )
        # Drain repeated source wordings across different scenes early.
        # The prior shuffle randomizes ties, while this stable sort gives
        # templates that share a rendered question enough separate scenes
        # to complete the full cycle without duplicating a question inside
        # any single scene.
        unseen_candidates.sort(
            key=lambda entry: unseen_question_counts[entry["question"]],
            reverse=True,
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
                "question_type": entry["question_type"],
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
        lines.append(
            f"Q{index} [{pair['question_type']}]: "
            f"{pair['question']}"
        )
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
    workbook_path = Path(args.workbook).expanduser().resolve()

    if not workbook_path.is_file():
        print(
            f"QA workbook does not exist: {workbook_path}",
            file=sys.stderr,
        )
        return 2

    try:
        library = build_runtime_library_from_workbook(
            workbook_path,
            template_path,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(
            f"QA workbook validation failed: {error}",
            file=sys.stderr,
        )
        return 2

    template_payload = (
        json.dumps(
            library,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    current_template_payload = (
        template_path.read_text(encoding="utf-8")
        if template_path.is_file()
        else ""
    )
    templates_changed = (
        current_template_payload != template_payload
    )

    if args.sync_templates_only:
        if args.dry_run:
            print(
                "Workbook validation complete. "
                "No file was changed."
            )
        else:
            atomic_text_write(
                template_path,
                template_payload,
            )
            print(
                "Runtime QA JSON synchronized from the workbook."
            )
        print(f"Workbook: {workbook_path}")
        print(f"Runtime JSON: {template_path}")
        print(
            f"Templates: {library['total_templates']}"
        )
        print(
            "Exact minimum scenes: "
            + str(
                sum(
                    (
                        count
                        + int(
                            library.get(
                                "questions_per_scene",
                                8,
                            )
                        )
                        - 1
                    )
                    // int(
                        library.get(
                            "questions_per_scene",
                            8,
                        )
                    )
                    for count in (
                        library.get(
                            "template_counts",
                            {},
                        )
                    ).values()
                )
            )
        )
        print(
            "Source QA SHA-256: "
            + str(library["source_qa_sha256"])
        )
        return 0

    if not output_root.is_dir():
        print(
            f"Output directory does not exist: {output_root}",
            file=sys.stderr,
        )
        return 2

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
    correspondence_errors: list[str] = []

    for annotation_path in annotation_paths:
        batch_dir = annotation_path.parent
        annotation = json.loads(
            annotation_path.read_text(encoding="utf-8")
        )
        try:
            validate_scene_correspondence(
                annotation,
                annotation_path,
            )
        except ValueError as error:
            correspondence_errors.append(str(error))
            continue

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

    if correspondence_errors:
        print(
            "Canonical A/B scene correspondence validation failed "
            f"for {len(correspondence_errors)} video(s):",
            file=sys.stderr,
        )
        for message in correspondence_errors:
            print(f"  - {message}", file=sys.stderr)
        print(
            "No file was changed. Rerender the listed videos before "
            "regenerating their QA.",
            file=sys.stderr,
        )
        return 2

    if templates_changed and not args.dry_run:
        atomic_text_write(
            template_path,
            template_payload,
        )
        print(
            "Runtime QA JSON synchronized from the workbook."
        )

    scene_items.sort(
        key=lambda item: int(item[1].get("batchId", 0))
    )

    # The cycle target for each change type is the union of templates that
    # can actually be rendered by at least one completed scene in this
    # dataset. The configured pool size comes directly from sheets 01-08.
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
    # independent cycle sized from its authoritative source sheet.
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
            "video": video_path,
            "video_path": video_path,
            "scene_type": str(
                library.get("scene_type") or "tabletop"
            ),
            "metadata": build_metadata(annotation),
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
        annotation["metadata"] = record["metadata"]
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
