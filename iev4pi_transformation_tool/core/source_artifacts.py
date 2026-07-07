"""SourceArtifact construction for v0.8 AIO Object evidence rows."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from iev4pi_transformation_tool.models import (
    EvidenceRef,
    ExtractedFieldResult,
    ExtractedRecord,
    SourceArtifact,
)


_DERIVED_FIELD_PATTERNS = (
    re.compile(r"(^|_)bbox($|_)", re.IGNORECASE),
    re.compile(r"(^|_)component_(role|type)($|_)", re.IGNORECASE),
    re.compile(r"(^|_)object_type($|_)", re.IGNORECASE),
    re.compile(r"(^|_)raw_context($|_)", re.IGNORECASE),
    re.compile(r"(^|_)graph_", re.IGNORECASE),
)
_BBOX_VALUE_RE = re.compile(
    r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$"
)
_CELL_RE = re.compile(r"^[A-Z]{1,3}\d+(?::[A-Z]{1,3}\d+)?$", re.IGNORECASE)


class SourceArtifactIndex:
    """Lookup helper from extraction fields/records to Object_ID evidence rows."""

    def __init__(self) -> None:
        self.by_record: dict[str, list[str]] = {}
        self.by_field: dict[tuple[str, str], list[str]] = {}
        self.by_evidence: dict[tuple[str, str, str, str], str] = {}
        self.by_object_id: dict[str, SourceArtifact] = {}
        self.metadata_object_id = ""

    def add(self, artifact: SourceArtifact, object_id: str) -> None:
        self.by_object_id[object_id] = artifact
        record_key = artifact.record_key or ""
        if record_key:
            self.by_record.setdefault(record_key, []).append(object_id)
            for field_name in artifact.field_names:
                self.by_field.setdefault((record_key, field_name), []).append(object_id)
        for evidence in artifact.evidence_refs:
            self.by_evidence[_evidence_key(evidence)] = object_id

    def object_for_field(self, record: ExtractedRecord, field: ExtractedFieldResult) -> str:
        record_key = getattr(record, "record_key", "") or ""
        for evidence in field.evidence_refs or []:
            obj_id = self.by_evidence.get(_evidence_key(evidence))
            if obj_id:
                return obj_id
        field_hits = self.by_field.get((record_key, field.field_name), [])
        if field_hits:
            return field_hits[0]
        return self.object_for_record(record)

    def object_for_record(self, record: ExtractedRecord) -> str:
        record_hits = self.by_record.get(getattr(record, "record_key", "") or "", [])
        if record_hits:
            return record_hits[0]
        return self.metadata_object_id

    def artifact_for_object(self, object_id: str) -> SourceArtifact | None:
        return self.by_object_id.get(object_id)

    def artifacts(self) -> list[SourceArtifact]:
        return list(self.by_object_id.values())


def build_source_artifact_objects(
    g: Any,
    doc_id: str,
    records: list[ExtractedRecord],
) -> tuple[list[dict[str, Any]], SourceArtifactIndex]:
    """Create v0.8 Object rows from source-verbatim artifacts, not field values."""

    rows: list[dict[str, Any]] = []
    index = SourceArtifactIndex()
    seen: dict[str, str] = {}

    for rec in records:
        artifacts = _record_artifacts(rec)
        for artifact in artifacts:
            dedupe_key = _artifact_dedupe_key(artifact)
            obj_id = seen.get(dedupe_key)
            if not obj_id:
                obj_id = g.next("Object")
                seen[dedupe_key] = obj_id
                rows.append(_artifact_to_object_row(artifact, obj_id, doc_id, len(rows) + 1))
            artifact.artifact_id = obj_id
            index.add(artifact, obj_id)

    return rows, index


def field_source_object_id(
    index: SourceArtifactIndex | None,
    record: ExtractedRecord,
    field: ExtractedFieldResult,
    fallback: str = "",
) -> str:
    if index is None:
        return fallback
    return index.object_for_field(record, field) or fallback


def record_source_object_id(
    index: SourceArtifactIndex | None,
    record: ExtractedRecord,
    fallback: str = "",
) -> str:
    if index is None:
        return fallback
    return index.object_for_record(record) or fallback


def _record_artifacts(rec: ExtractedRecord) -> list[SourceArtifact]:
    if _is_tabular_record(rec):
        return [_vl_row_artifact(rec)]

    artifacts: list[SourceArtifact] = []
    for field in rec.results or []:
        if _is_derived_field(field.field_name):
            continue
        for evidence in field.evidence_refs or []:
            artifact = _artifact_from_evidence(rec, field, evidence)
            if artifact:
                artifacts.append(artifact)

    if artifacts:
        return artifacts
    return [_manual_review_artifact(rec)]


def _artifact_from_evidence(
    rec: ExtractedRecord,
    field: ExtractedFieldResult,
    evidence: EvidenceRef,
) -> SourceArtifact | None:
    snippet = _clean_text(evidence.snippet)
    bbox = _parse_bbox(evidence.cell_range_or_bbox)
    source_path = evidence.source_path or getattr(rec, "source_path", "") or ""
    evidence_type = (evidence.evidence_type or "").lower()

    if _is_source_text_evidence(evidence, source_path) and snippet and not _looks_like_derived_text(snippet):
        return SourceArtifact(
            source_path=source_path,
            page_or_sheet=evidence.page_or_sheet,
            bbox=bbox,
            object_type="Text",
            source_operation=_source_operation(evidence, source_path),
            content_text=snippet[:500],
            confidence=_confidence(field, evidence),
            method=_method_from_evidence(evidence),
            evidence_refs=[evidence],
            source_role="Value",
            record_key=getattr(rec, "record_key", "") or "",
            field_names=[field.field_name],
        )

    if bbox is not None or evidence_type in {"diagram_edge", "diagram_node", "geometry", "graph"}:
        return SourceArtifact(
            source_path=source_path,
            page_or_sheet=evidence.page_or_sheet,
            bbox=bbox,
            object_type="Topology" if evidence_type == "diagram_edge" else "Symbol",
            source_operation="S",
            content_text="",
            confidence=_confidence(field, evidence),
            method=_method_from_evidence(evidence),
            evidence_refs=[evidence],
            source_role="Geometry",
            record_key=getattr(rec, "record_key", "") or "",
            field_names=[field.field_name],
        )

    return None


def _vl_row_artifact(rec: ExtractedRecord) -> SourceArtifact:
    parts: list[str] = []
    field_names: list[str] = []
    confidences: list[float] = []
    evidences: list[EvidenceRef] = []

    for field in rec.results or []:
        value = _clean_text(field.value)
        if not value:
            continue
        if _is_derived_field(field.field_name) or _looks_like_derived_text(value):
            continue
        parts.append(value)
        field_names.append(field.field_name)
        if field.confidence:
            confidences.append(float(field.confidence))
        evidences.extend(field.evidence_refs or [])

    row_text = " | ".join(parts)
    if not row_text:
        row_text = f"Tabular source row requires review: {getattr(rec, 'record_key', '')}"
    first_evidence = evidences[0] if evidences else None
    source_path = (first_evidence.source_path if first_evidence else "") or getattr(rec, "source_path", "") or ""
    page_or_sheet = first_evidence.page_or_sheet if first_evidence else ""
    source_operation = "VL_Row" if "verschaltung" in _family_value(rec).lower() else "Cell"
    confidence = min(confidences) if confidences else 1.0

    return SourceArtifact(
        source_path=source_path,
        page_or_sheet=page_or_sheet,
        object_type="Text",
        source_operation=source_operation,
        content_text=row_text[:500],
        confidence=confidence,
        method="Native_Table_Row",
        evidence_refs=evidences[:12],
        source_role="Value",
        record_key=getattr(rec, "record_key", "") or "",
        field_names=field_names,
        requires_review=not bool(parts),
        abstain_reason="" if parts else "No source-verbatim cell values available for row serialization.",
    )


def _manual_review_artifact(rec: ExtractedRecord) -> SourceArtifact:
    source_path = getattr(rec, "source_path", "") or ""
    label = getattr(rec, "display_name", "") or getattr(rec, "record_key", "") or "record"
    return SourceArtifact(
        source_path=source_path,
        page_or_sheet="",
        object_type="Text",
        source_operation="Manual_Entry",
        content_text=f"No source-verbatim artifact available for {label}; requires review."[:500],
        confidence=0.0,
        method="Manual_Review_Required",
        source_role="Rationale",
        record_key=getattr(rec, "record_key", "") or "",
        field_names=[field.field_name for field in rec.results or []],
        requires_review=True,
        abstain_reason="Evidence linker abstained because no parser/OCR/Excel source artifact was available.",
    )


def _artifact_to_object_row(
    artifact: SourceArtifact,
    object_id: str,
    doc_id: str,
    index: int,
) -> dict[str, Any]:
    bbox = artifact.bbox or (0.0, 0.0, 0.0, 0.0)
    return {
        "Index": index,
        "Object_ID": object_id,
        "Document_ID": doc_id,
        "Page_Number": _page_number(artifact.page_or_sheet),
        "Object_Type": artifact.object_type,
        "Source_Operation": artifact.source_operation,
        "BBox_X1": bbox[0],
        "BBox_Y1": bbox[1],
        "BBox_X2": bbox[2],
        "BBox_Y2": bbox[3],
        "Content_Text": artifact.content_text,
        "Object_Role": artifact.source_role,
        "_SourceArtifact": artifact,
    }


def _is_tabular_record(rec: ExtractedRecord) -> bool:
    source_path = (getattr(rec, "source_path", "") or "").lower()
    family = _family_value(rec).lower()
    if source_path.endswith((".xls", ".xlsx", ".xlsm", ".csv", ".tsv")):
        return True
    return any(token in family for token in ("verschaltung", "klemmenplan", "cabinet_reference"))


def _is_source_text_evidence(evidence: EvidenceRef, source_path: str) -> bool:
    evidence_type = (evidence.evidence_type or "").lower()
    if evidence_type in {"native_text", "ocr_text", "table_cell", "excel_cell", "cell", "vl_row"}:
        return True
    if source_path.lower().endswith((".xls", ".xlsx", ".xlsm", ".csv", ".tsv")):
        return True
    locator = evidence.cell_range_or_bbox or ""
    return bool(_CELL_RE.match(locator.strip()))


def _source_operation(evidence: EvidenceRef, source_path: str) -> str:
    evidence_type = (evidence.evidence_type or "").lower()
    if evidence_type in {"table_cell", "excel_cell", "cell", "vl_row"}:
        return "Cell"
    if source_path.lower().endswith((".xls", ".xlsx", ".xlsm", ".csv", ".tsv")):
        return "Cell"
    return "Tj"


def _method_from_evidence(evidence: EvidenceRef) -> str:
    if evidence.engine:
        return evidence.engine
    evidence_type = (evidence.evidence_type or "").lower()
    if evidence_type == "ocr_text":
        return "OCR"
    if evidence_type in {"table_cell", "excel_cell", "cell", "vl_row"}:
        return "Native_Table_Row"
    if evidence_type == "native_text":
        return "Native_Text"
    return evidence_type or "Source_Artifact"


def _confidence(field: ExtractedFieldResult, evidence: EvidenceRef) -> float:
    if evidence.score:
        return round(float(evidence.score), 4)
    if field.decision_confidence:
        return round(float(field.decision_confidence), 4)
    if field.confidence:
        return round(float(field.confidence), 4)
    return 0.0


def _is_derived_field(field_name: str) -> bool:
    return any(pattern.search(field_name or "") for pattern in _DERIVED_FIELD_PATTERNS)


def _looks_like_derived_text(value: str) -> bool:
    text = _clean_text(value)
    if not text:
        return True
    if _BBOX_VALUE_RE.match(text):
        return True
    if len(text) > 400 and "," in text and text.count(",") > text.count(" "):
        return True
    return False


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _parse_bbox(locator: str) -> tuple[float, float, float, float] | None:
    text = str(locator or "")
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    if len(numbers) != 4:
        return None
    try:
        return tuple(float(n) for n in numbers)  # type: ignore[return-value]
    except ValueError:
        return None


def _page_number(page_or_sheet: str) -> int:
    text = str(page_or_sheet or "")
    match = re.search(r"\d+", text)
    if not match:
        return 1
    try:
        return max(1, int(match.group(0)))
    except ValueError:
        return 1


def _evidence_key(evidence: EvidenceRef) -> tuple[str, str, str, str]:
    return (
        evidence.source_path or "",
        evidence.page_or_sheet or "",
        evidence.cell_range_or_bbox or "",
        hashlib.sha1(_clean_text(evidence.snippet).encode("utf-8")).hexdigest(),
    )


def _artifact_dedupe_key(artifact: SourceArtifact) -> str:
    payload = "|".join(
        [
            artifact.source_path or "",
            artifact.page_or_sheet or "",
            str(artifact.bbox or ""),
            artifact.object_type,
            artifact.source_operation,
            artifact.content_text,
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _family_value(rec: ExtractedRecord) -> str:
    family = getattr(rec, "family", "")
    return str(getattr(family, "value", family))
