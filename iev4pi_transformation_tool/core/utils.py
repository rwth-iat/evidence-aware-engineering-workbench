from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable

from iev4pi_transformation_tool.models import DocumentFamily


COMPONENT_PATTERNS = [
    # 下面的正则覆盖了在 Stellenplan 等图纸中常见的元器件编号格式
    # 例如 "TU10F17-5"、"HC10-01" 等
    re.compile(r"\bTU\d+[A-Z]?\d*(?:[-_/]\w+)*\b", re.IGNORECASE),
    re.compile(r"\bHC\d{2}[A-Z]?\d*\b", re.IGNORECASE),
    re.compile(r"\bX\d+:\d+(?:/[A-Za-z0-9_+\-.]+)?\b"),
    re.compile(
        r"\b(?:HC|TU|PXC|IO|AI|AO|DI|DO|FI|LS|PE|L1|L2|L3|N)[A-Z0-9:_/\-.]{2,}\b"
    ),
    re.compile(r"\b[A-Z]{1,4}\d{1,4}[A-Z0-9:_/\-.]*\b"),
]

VALUE_TYPE_HINTS = {
    "voltage": "number",
    "strom": "number",
    "leistung": "number",
    "range": "string",
    "messbereich": "string",
    "serial": "string",
    "serien": "string",
    "datum": "date",
    "date": "date",
    "page": "integer",
}


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def json_dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def slugify(text: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    ascii_text = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    return ascii_text or "field"


def normalize_label(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_identifier(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def tokenize(text: str) -> list[str]:
    normalized = normalize_label(text)
    return [token for token in normalized.split(" ") if token]


def cell_coordinate(row: int, column: int) -> str:
    letters: list[str] = []
    while column > 0:
        column, remainder = divmod(column - 1, 26)
        letters.append(chr(65 + remainder))
    return f"{''.join(reversed(letters))}{row}"


def non_empty_cells(row: Iterable[str]) -> list[str]:
    return [cell for cell in row if str(cell).strip()]


def row_non_empty_count(row: Iterable[str]) -> int:
    return len(non_empty_cells(row))


def looks_like_identifier(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    return bool(
        re.match(
            r"^(?:HC|TU|X|PXC|IO|AI|AO|DI|DO)[A-Z0-9:_/\-.]+$", value, re.IGNORECASE
        )
    )


def looks_header_like(row: list[str]) -> bool:
    values = non_empty_cells(row)
    if len(values) < 2:
        return False
    identifier_like = sum(1 for value in values if looks_like_identifier(value))
    numeric_like = sum(
        1 for value in values if re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", value.strip())
    )
    return identifier_like == 0 and numeric_like < max(2, len(values) // 2)


def detect_header_rows(rows: list[list[str]]) -> list[int]:
    search_limit = min(len(rows), 25)
    candidate = None
    for index in range(search_limit):
        row = rows[index]
        if row_non_empty_count(row) >= 3 and looks_header_like(row):
            candidate = index + 1
            break
    if candidate is None:
        return [1] if rows else []
    return [candidate]


def build_header_map(rows: list[list[str]], header_rows: list[int]) -> dict[int, str]:
    if not rows:
        return {}
    header_map: dict[int, list[str]] = {}
    for header_row in header_rows or [1]:
        row_index = header_row - 1
        if row_index >= len(rows):
            continue
        for col_index, value in enumerate(rows[row_index], start=1):
            cleaned = clean_cell(value)
            if cleaned:
                header_map.setdefault(col_index, []).append(cleaned)
    return {
        col_index: " | ".join(parts)
        for col_index, parts in header_map.items()
        if any(part.strip() for part in parts)
    }


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def guess_value_type(label: str) -> str:
    normalized = normalize_label(label)
    for hint, value_type in VALUE_TYPE_HINTS.items():
        if hint in normalized:
            return value_type
    return "string"


def canonical_field_name(label: str) -> str:
    normalized = normalize_label(label)
    normalized = normalized.replace(" ", "_")
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or slugify(label)


def extract_component_tokens(text: str) -> list[str]:
    found: list[str] = []
    for pattern in COMPONENT_PATTERNS:
        for match in pattern.findall(text):
            token = match.strip(".,;:()[]{}")
            if token and token not in found:
                found.append(token)
    return found


def family_title(family: DocumentFamily) -> str:
    return family.value.replace("_", " ").title()


def bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def bbox_union(
    boxes: Iterable[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    items = list(boxes)
    if not items:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        float(min(box[0] for box in items)),
        float(min(box[1] for box in items)),
        float(max(box[2] for box in items)),
        float(max(box[3] for box in items)),
    )


def bbox_contains_point(
    bbox: tuple[float, float, float, float], point: tuple[float, float]
) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def bbox_expand(
    bbox: tuple[float, float, float, float], pad_x: float, pad_y: float | None = None
) -> tuple[float, float, float, float]:
    dy = pad_x if pad_y is None else pad_y
    return (bbox[0] - pad_x, bbox[1] - dy, bbox[2] + pad_x, bbox[3] + dy)


def bbox_intersection_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float((x1 - x0) * (y1 - y0))


def bbox_overlaps(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
    *,
    min_area: float = 1.0,
) -> bool:
    return bbox_intersection_area(left, right) >= min_area


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def summarize_counts(items: Iterable[str]) -> dict[str, int]:
    return dict(Counter(items))
