from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Callable

try:
    import fitz
except ImportError:  # pragma: no cover - dependency guard
    try:  # pragma: no cover - compatibility alias
        import pymupdf as fitz
    except ImportError:
        fitz = None

from iev4pi_transformation_tool.core.document_classifier import DocumentClassifier
from iev4pi_transformation_tool.core.document_reader import DocumentReader
from iev4pi_transformation_tool.core.utils import clean_cell, ensure_dir, normalize_label
from iev4pi_transformation_tool.models import DocumentDescriptor, EvidenceRef, ParsedDocument, PageData, SheetData


Rect = tuple[float, float, float, float]


class SourcePreviewLoader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        scan_root: Path,
        documents: list[DocumentDescriptor],
        classifier: DocumentClassifier,
        reader: DocumentReader,
        cache_dir: Path,
        parsed_cache: dict[str, ParsedDocument],
        resolve_source_path: Callable[[str], Path | None],
    ) -> None:
        self.workspace_root = workspace_root
        self.scan_root = scan_root
        self.documents = documents
        self.classifier = classifier
        self.reader = reader
        self.cache_dir = ensure_dir(cache_dir)
        self.parsed_cache = parsed_cache
        self.resolve_source_path = resolve_source_path

    def load(
        self,
        *,
        source_path: str,
        evidences_payload: list[dict[str, object]] | list[EvidenceRef],
        evidence_index: int = 0,
        record_display_name: str = "",
        field_name: str = "",
        target_value: str = "",
    ) -> dict[str, object]:
        evidences = self._coerce_evidences(evidences_payload)
        base = self._base_payload(
            source_path=source_path,
            evidences=evidences,
            evidence_index=evidence_index,
            record_display_name=record_display_name,
            field_name=field_name,
            target_value=target_value,
        )
        if not evidences:
            base["message"] = "No source evidence is available for this value."
            return base
        index = max(0, min(int(evidence_index), len(evidences) - 1))
        evidence = evidences[index]
        chosen_source_path = evidence.source_path or source_path
        base["display_source_path"] = chosen_source_path
        base["evidence_index"] = index
        base["locator_text"] = evidence.cell_range_or_bbox
        base["page_label"] = evidence.page_or_sheet
        base["snippet"] = evidence.snippet
        resolved_path = self.resolve_source_path(chosen_source_path)
        if resolved_path is None:
            base["message"] = f"File not found: {chosen_source_path}"
            return base
        base["resolved_source_path"] = str(resolved_path)
        document = self._resolve_document(chosen_source_path, resolved_path)
        if document.extension == ".pdf":
            return self._load_pdf_preview(base, document, resolved_path, evidences, index, evidence)
        if document.extension in {".xls", ".xlsx"}:
            return self._load_spreadsheet_preview(base, document, evidences, index, evidence)
        if document.extension in {".xml", ".xsd", ".ifc", ".txt", ".json", ".csv"}:
            return self._load_text_preview(base, document, resolved_path, evidences, index, evidence)
        base["message"] = f"Unsupported source type: {document.extension}"
        return base

    def _base_payload(
        self,
        *,
        source_path: str,
        evidences: list[EvidenceRef],
        evidence_index: int,
        record_display_name: str,
        field_name: str,
        target_value: str,
    ) -> dict[str, object]:
        return {
            "source_type": "unsupported",
            "display_source_path": source_path,
            "resolved_source_path": "",
            "record_display_name": record_display_name,
            "field_name": field_name,
            "target_value": target_value,
            "evidence_index": max(0, int(evidence_index)),
            "evidence_count": len(evidences),
            "evidence_options": self._evidence_options(evidences),
            "page_number": 0,
            "page_label": "",
            "sheet_name": "",
            "locator_text": "",
            "snippet": "",
            "highlight_text": "",
            "highlight_kind": "none",
            "highlight_geometry": {},
            "viewport_hint": {},
            "rendered_image_path": "",
            "sheet_rows": [],
            "sheet_highlight": {},
            "text_lines": [],
            "text_line_offset": 0,
            "text_highlight": {},
            "message": "",
        }

    def _coerce_evidences(
        self, evidences_payload: list[dict[str, object]] | list[EvidenceRef]
    ) -> list[EvidenceRef]:
        evidences: list[EvidenceRef] = []
        for item in evidences_payload:
            if isinstance(item, EvidenceRef):
                evidences.append(item)
            else:
                evidences.append(EvidenceRef.model_validate(item))
        return evidences

    def _evidence_options(self, evidences: list[EvidenceRef]) -> list[dict[str, object]]:
        options: list[dict[str, object]] = []
        for index, evidence in enumerate(evidences, start=1):
            location_bits = [bit for bit in [evidence.page_or_sheet, evidence.cell_range_or_bbox] if bit]
            location = " / ".join(location_bits) if location_bits else "Unknown location"
            label = f"{index}. {location}"
            options.append(
                {
                    "index": index - 1,
                    "label": label,
                    "page_or_sheet": evidence.page_or_sheet,
                    "locator_text": evidence.cell_range_or_bbox,
                    "snippet": evidence.snippet,
                }
            )
        return options

    def _resolve_document(self, source_path: str, resolved_path: Path) -> DocumentDescriptor:
        for document in self.documents:
            if document.relative_path == source_path or document.path.resolve() == resolved_path.resolve():
                return document
        relative_to = self.scan_root if resolved_path.is_relative_to(self.scan_root) else self.workspace_root
        document = self.classifier.classify(resolved_path, relative_to=relative_to)
        if source_path and not Path(source_path).is_absolute():
            source_root = source_path.split("/", 1)[0] if "/" in source_path else source_path
            document = document.model_copy(update={"relative_path": source_path, "source_root": source_root})
        return document

    def _parse_document(self, document: DocumentDescriptor) -> ParsedDocument:
        cache_keys = [document.relative_path, str(document.path)]
        for key in cache_keys:
            cached = self.parsed_cache.get(key)
            if cached is not None:
                return cached
        parsed = self.reader.read(document)
        self.parsed_cache[document.relative_path] = parsed
        self.parsed_cache[str(document.path)] = parsed
        return parsed

    def _load_pdf_preview(
        self,
        base: dict[str, object],
        document: DocumentDescriptor,
        resolved_path: Path,
        evidences: list[EvidenceRef],
        index: int,
        evidence: EvidenceRef,
    ) -> dict[str, object]:
        if fitz is None:
            base["message"] = "PyMuPDF is not available."
            return base
        parsed = self._parse_document(document)
        page_number = self._pdf_page_number(evidence, parsed)
        page_data = self._page_by_number(parsed, page_number)
        if page_data is None and parsed.pages:
            page_data = parsed.pages[0]
            page_number = page_data.page_number
        if page_data is None:
            base["message"] = "No PDF pages are available for preview."
            return base
        render_dpi = int(page_data.rendered_dpi or 72)
        image_path = self._render_pdf_page_image(resolved_path, page_number, render_dpi)
        highlight_kind, highlight_geometry, viewport_rect = self._pdf_geometry_from_evidence(
            parsed,
            page_data,
            page_number,
            evidence,
        )
        refined = self._refine_pdf_geometry(
            page_data,
            str(base.get("target_value", "")),
            highlight_kind,
            viewport_rect,
        )
        if refined is not None:
            highlight_kind, highlight_geometry, viewport_rect = refined
        payload = dict(base)
        payload["highlight_text"] = self._pdf_highlight_text(
            page_data,
            highlight_kind,
            highlight_geometry,
            str(base.get("target_value", "")),
            str(base.get("snippet", "")),
        )
        payload.update(
            {
                "source_type": "pdf",
                "page_number": page_number,
                "page_label": f"Page {page_number}",
                "evidence_index": index,
                "evidence_count": len(evidences),
                "rendered_image_path": str(image_path),
                "highlight_kind": highlight_kind,
                "highlight_geometry": highlight_geometry,
                "viewport_hint": {"rect": list(viewport_rect)} if viewport_rect else {},
            }
        )
        if highlight_kind == "none":
            payload["message"] = payload["message"] or "Precise source geometry was not available for this evidence."
        return payload

    def _load_spreadsheet_preview(
        self,
        base: dict[str, object],
        document: DocumentDescriptor,
        evidences: list[EvidenceRef],
        index: int,
        evidence: EvidenceRef,
    ) -> dict[str, object]:
        parsed = self._parse_document(document)
        sheet = self._sheet_for_evidence(parsed, evidence)
        if sheet is None:
            base["message"] = "No worksheet could be resolved for this evidence."
            return base
        trimmed_rows = self._trim_sheet_rows(sheet.rows)
        highlight = self._sheet_highlight_from_evidence(sheet, evidence)
        highlight = self._refine_sheet_highlight(
            sheet,
            str(base.get("target_value", "")),
            highlight,
        ) or highlight
        payload = dict(base)
        payload["highlight_text"] = self._sheet_highlight_text(
            sheet,
            highlight,
            str(base.get("target_value", "")),
            str(base.get("snippet", "")),
        )
        payload.update(
            {
                "source_type": "spreadsheet",
                "sheet_name": sheet.name,
                "page_label": sheet.name,
                "evidence_index": index,
                "evidence_count": len(evidences),
                "sheet_rows": trimmed_rows,
                "sheet_highlight": highlight,
                "viewport_hint": highlight,
            }
        )
        if not highlight:
            payload["message"] = payload["message"] or "Precise cell position was not available for this evidence."
        return payload

    def _load_text_preview(
        self,
        base: dict[str, object],
        document: DocumentDescriptor,
        resolved_path: Path,
        evidences: list[EvidenceRef],
        index: int,
        evidence: EvidenceRef,
    ) -> dict[str, object]:
        lines = self._read_text_lines(resolved_path)
        if not lines:
            base["message"] = "No text lines are available for preview."
            return base
        highlight = self._text_highlight_from_evidence(
            lines,
            evidence=evidence,
            target_value=str(base.get("target_value", "")),
        )
        visible_lines, offset, relative_highlight = self._text_preview_window(lines, highlight)
        payload = dict(base)
        if highlight:
            absolute_top = int(highlight.get("top", 1))
            absolute_bottom = int(highlight.get("bottom", absolute_top))
            default_locator = f"L{absolute_top}" if absolute_top == absolute_bottom else f"L{absolute_top}-L{absolute_bottom}"
            existing_locator = str(payload.get("locator_text", "")).strip()
            payload["locator_text"] = f"{existing_locator} / {default_locator}" if existing_locator else default_locator
        payload["highlight_text"] = (
            str(highlight.get("match_text", "")).strip()
            if highlight
            else str(base.get("snippet", "")).strip()
        )
        payload.update(
            {
                "source_type": "text",
                "page_label": evidence.page_or_sheet or document.extension.lstrip(".").upper(),
                "evidence_index": index,
                "evidence_count": len(evidences),
                "text_lines": visible_lines,
                "text_line_offset": offset,
                "text_highlight": relative_highlight,
            }
        )
        if not relative_highlight:
            payload["message"] = payload["message"] or "Precise text line position was not available for this evidence."
        return payload

    def _page_by_number(self, parsed: ParsedDocument, page_number: int) -> PageData | None:
        return next((page for page in parsed.pages if page.page_number == page_number), None)

    def _sheet_for_evidence(self, parsed: ParsedDocument, evidence: EvidenceRef) -> SheetData | None:
        if not parsed.sheets:
            return None
        sheet_name = (evidence.page_or_sheet or "").strip()
        if sheet_name:
            for sheet in parsed.sheets:
                if sheet.name == sheet_name:
                    return sheet
            lowered = sheet_name.casefold()
            for sheet in parsed.sheets:
                if sheet.name.casefold() == lowered:
                    return sheet
        return parsed.sheets[0]

    def _pdf_page_number(self, evidence: EvidenceRef, parsed: ParsedDocument) -> int:
        locator = (evidence.cell_range_or_bbox or "").strip()
        match = re.match(r"p(\d+)@", locator)
        if match:
            return max(1, int(match.group(1)))
        page_match = re.search(r"(\d+)", evidence.page_or_sheet or "")
        if page_match:
            return max(1, int(page_match.group(1)))
        return parsed.pages[0].page_number if parsed.pages else 1

    def _read_text_lines(self, resolved_path: Path) -> list[str]:
        text = ""
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = resolved_path.read_text(encoding=encoding)
                break
            except Exception:
                continue
        if not text:
            try:
                text = resolved_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return []
        return text.splitlines() or [text]

    def _text_highlight_from_evidence(
        self,
        lines: list[str],
        *,
        evidence: EvidenceRef,
        target_value: str,
    ) -> dict[str, object]:
        locator = clean_cell(evidence.cell_range_or_bbox)
        explicit_range = self._explicit_line_range(locator)
        if explicit_range is not None:
            top, bottom = explicit_range
            top = max(1, min(len(lines), top))
            bottom = max(top, min(len(lines), bottom))
            return {
                "top": top,
                "bottom": bottom,
                "match_text": clean_cell(evidence.snippet) or clean_cell(target_value),
            }

        candidates: list[str] = []
        for candidate in (
            clean_cell(evidence.snippet),
            clean_cell(target_value),
            clean_cell(locator),
            clean_cell(evidence.page_or_sheet),
        ):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        if "#" in locator:
            locator_tail = clean_cell(locator.split("#", 1)[1])
            if locator_tail and locator_tail not in candidates:
                candidates.append(locator_tail)
        for part in re.split(r"::|->|#|@", locator):
            cleaned = clean_cell(part)
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)

        best_index = -1
        best_text = ""
        for candidate in candidates:
            lowered = candidate.casefold()
            for index, line in enumerate(lines, start=1):
                if lowered and lowered in line.casefold():
                    best_index = index
                    best_text = candidate
                    break
            if best_index > 0:
                break

        if best_index <= 0:
            best_score = 0
            for index, line in enumerate(lines, start=1):
                lowered_line = line.casefold()
                score = 0
                for candidate in candidates:
                    for token in self._text_match_tokens(candidate):
                        if token in lowered_line:
                            score += max(1, min(4, len(token) // 4 + 1))
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index > 0:
                best_text = clean_cell(evidence.snippet) or clean_cell(target_value) or locator

        if best_index <= 0:
            return {}
        return {
            "top": best_index,
            "bottom": best_index,
            "match_text": best_text,
        }

    def _explicit_line_range(self, locator: str) -> tuple[int, int] | None:
        if not locator:
            return None
        patterns = [
            re.compile(r"^l(?:ine)?\s*(\d+)(?:\s*-\s*l?(?:ine)?\s*(\d+))?$", re.IGNORECASE),
            re.compile(r"^(\d+)(?:\s*-\s*(\d+))?$"),
        ]
        for pattern in patterns:
            match = pattern.match(locator.strip())
            if match is None:
                continue
            top = int(match.group(1))
            bottom = int(match.group(2) or top)
            return min(top, bottom), max(top, bottom)
        return None

    def _text_match_tokens(self, text: str) -> list[str]:
        tokens: list[str] = []
        for part in re.split(r"[^A-Za-z0-9_.:-]+", text.casefold()):
            cleaned = clean_cell(part)
            if not cleaned:
                continue
            if len(cleaned) >= 3 or any(char.isdigit() for char in cleaned):
                if cleaned not in tokens:
                    tokens.append(cleaned)
        return tokens

    def _text_preview_window(
        self,
        lines: list[str],
        highlight: dict[str, object],
    ) -> tuple[list[str], int, dict[str, int]]:
        total = len(lines)
        if not lines:
            return [], 0, {}
        if highlight:
            top = max(1, int(highlight.get("top", 1)))
            bottom = max(top, int(highlight.get("bottom", top)))
            start = max(0, top - 21)
            end = min(total, bottom + 20)
            relative = {
                "top": top - start,
                "bottom": bottom - start,
            }
            return lines[start:end], start, relative
        end = min(total, 120)
        return lines[:end], 0, {}

    def _render_pdf_page_image(self, resolved_path: Path, page_number: int, render_dpi: int) -> Path:
        preview_dir = ensure_dir(self.cache_dir / "preview")
        stat = resolved_path.stat()
        digest = hashlib.sha1(
            json.dumps(
                {
                    "path": str(resolved_path),
                    "mtime": stat.st_mtime,
                    "size": stat.st_size,
                    "page_number": page_number,
                    "render_dpi": render_dpi,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        image_path = preview_dir / f"{digest}.png"
        if image_path.exists():
            return image_path
        pdf = fitz.open(resolved_path)
        try:
            page = pdf.load_page(max(0, page_number - 1))
            scale = max(float(render_dpi) / 72.0, 1.0)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            pixmap.save(image_path)
        finally:
            pdf.close()
        return image_path

    def _pdf_geometry_from_evidence(
        self,
        parsed: ParsedDocument,
        page_data: PageData,
        page_number: int,
        evidence: EvidenceRef,
    ) -> tuple[str, dict[str, object], Rect | None]:
        locator = (evidence.cell_range_or_bbox or "").strip()
        normalized_locator = locator
        page_match = re.match(r"p(\d+)@(.+)", locator)
        if page_match:
            page_number = max(1, int(page_match.group(1)))
            page_data = self._page_by_number(parsed, page_number) or page_data
            normalized_locator = page_match.group(2).strip()

        literal_geometry = self._literal_geometry(normalized_locator or locator)
        if literal_geometry is not None:
            return literal_geometry

        named_geometry = self._named_pdf_geometry(page_data, normalized_locator or locator)
        if named_geometry is not None:
            return named_geometry

        snippet_geometry = self._snippet_geometry(page_data, evidence.snippet)
        if snippet_geometry is not None:
            return snippet_geometry

        return "none", {}, None

    def _literal_geometry(self, text: str) -> tuple[str, dict[str, object], Rect | None] | None:
        parsed = self._safe_literal_eval(text)
        if parsed is None:
            return None
        if self._is_rect_sequence(parsed):
            rect = self._as_rect(parsed)
            return "rect", {"rect": list(rect)}, rect
        polyline = self._as_polyline(parsed)
        if polyline:
            rect = self._polyline_bounds(polyline)
            return "polyline", {"polyline": [list(point) for point in polyline]}, rect
        return None

    def _named_pdf_geometry(
        self,
        page_data: PageData,
        locator: str,
    ) -> tuple[str, dict[str, object], Rect | None] | None:
        locator = locator.strip()
        if not locator:
            return None

        if locator.startswith("kv"):
            index_text = locator[2:]
            if index_text.isdigit():
                pair_index = int(index_text)
                if 0 <= pair_index < len(page_data.kv_pairs):
                    pair = page_data.kv_pairs[pair_index]
                    rect = self._bbox_union([bbox for bbox in [pair.key_bbox, pair.value_bbox] if bbox is not None])
                    if rect is not None:
                        return "rect", {"rect": list(rect)}, rect

        cell_match = re.match(r"(.+):r(\d+)c(\d+)$", locator)
        if cell_match:
            table_id, row_text, col_text = cell_match.groups()
            row_id = int(row_text)
            col_id = int(col_text)
            for table in page_data.tables:
                if table.table_id != table_id:
                    continue
                for cell in table.cells:
                    if cell.row_id == row_id and cell.col_id == col_id:
                        rect = self._as_rect(cell.bbox)
                        return "rect", {"rect": list(rect)}, rect

        row_match = re.match(r"(.+):row(\d+)$", locator)
        if row_match:
            table_id, row_text = row_match.groups()
            row_id = int(row_text)
            for table in page_data.tables:
                if table.table_id != table_id:
                    continue
                rect = self._bbox_union([cell.bbox for cell in table.cells if cell.row_id == row_id])
                if rect is not None:
                    return "rect", {"rect": list(rect)}, rect

        if locator.startswith("group:") and page_data.structured_diagram is not None:
            group_id = locator.split(":", 1)[1]
            group = next((item for item in page_data.structured_diagram.groups if item.id == group_id), None)
            if group is not None:
                rect = self._as_rect(group.bbox)
                return "rect", {"rect": list(rect)}, rect

        if locator.startswith("part:") and page_data.structured_diagram is not None:
            part_id = locator.split(":", 1)[1]
            part = next((item for item in page_data.structured_diagram.parts if item.id == part_id), None)
            if part is not None:
                rect = self._as_rect(part.content_bbox or part.bbox)
                return "rect", {"rect": list(rect)}, rect

        if locator.startswith("trace:") and page_data.structured_diagram is not None:
            trace_id = locator.split(":", 1)[1]
            trace = next((item for item in page_data.structured_diagram.traces if item.id == trace_id), None)
            if trace is not None and trace.trace_path:
                polyline = [(float(x), float(y)) for x, y in trace.trace_path]
                rect = self._polyline_bounds(polyline)
                return "polyline", {"polyline": [list(point) for point in polyline]}, rect

        if locator.startswith("node:") and page_data.diagram_graph is not None:
            node_id = locator.split(":", 1)[1]
            node = next((item for item in page_data.diagram_graph.nodes if item.id == node_id), None)
            if node is not None:
                rect = self._as_rect(node.bbox)
                return "rect", {"rect": list(rect)}, rect

        if locator.startswith("edge:") and page_data.diagram_graph is not None:
            edge_id = locator.split(":", 1)[1]
            edge = next((item for item in page_data.diagram_graph.edges if item.id == edge_id), None)
            if edge is not None and edge.polyline:
                polyline = [(float(x), float(y)) for x, y in edge.polyline]
                rect = self._polyline_bounds(polyline)
                return "polyline", {"polyline": [list(point) for point in polyline]}, rect

        if locator.startswith("(") or locator.startswith("["):
            return self._literal_geometry(locator)
        return None

    def _snippet_geometry(
        self,
        page_data: PageData,
        snippet: str,
    ) -> tuple[str, dict[str, object], Rect | None] | None:
        normalized_snippet = (snippet or "").strip()
        if not normalized_snippet:
            return None
        best_block = None
        best_score = -1
        for block in page_data.blocks:
            block_text = (block.text or "").strip()
            if not block_text:
                continue
            score = 0
            if block_text == normalized_snippet:
                score = 4
            elif normalized_snippet in block_text or block_text in normalized_snippet:
                score = 3
            else:
                snippet_folded = normalized_snippet.casefold()
                block_folded = block_text.casefold()
                if snippet_folded in block_folded or block_folded in snippet_folded:
                    score = 2
            if score > best_score:
                best_score = score
                best_block = block
        if best_block is None or best_score <= 0:
            return None
        rect = self._as_rect(best_block.bbox)
        return "rect", {"rect": list(rect)}, rect

    def _sheet_highlight_from_evidence(self, sheet: SheetData, evidence: EvidenceRef) -> dict[str, int]:
        locator = (evidence.cell_range_or_bbox or "").strip()
        if not locator:
            return {}
        range_match = re.match(r"([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)$", locator)
        if range_match:
            col1, row1, col2, row2 = range_match.groups()
            left = self._excel_column_to_index(col1)
            right = self._excel_column_to_index(col2)
            return self._ordered_sheet_range(int(row1), left, int(row2), right)
        cell_match = re.match(r"([A-Za-z]+)(\d+)$", locator)
        if cell_match:
            col, row = cell_match.groups()
            column = self._excel_column_to_index(col)
            return self._ordered_sheet_range(int(row), column, int(row), column)
        numeric_match = re.match(r"(\d+):(\d+)$", locator)
        if numeric_match:
            column, row = numeric_match.groups()
            return self._ordered_sheet_range(int(row), int(column), int(row), int(column))
        return {}

    def _refine_sheet_highlight(
        self,
        sheet: SheetData,
        target_value: str,
        current_highlight: dict[str, int],
    ) -> dict[str, int]:
        normalized_target = normalize_label(target_value)
        if not normalized_target:
            return {}
        preferred_rows = self._row_candidates_from_highlight(current_highlight, len(sheet.rows))
        match = self._find_sheet_value(sheet.rows, normalized_target, preferred_rows=preferred_rows)
        if match is None:
            match = self._find_sheet_value(sheet.rows, normalized_target, preferred_rows=None)
        if match is None:
            return {}
        row_index, col_index = match
        return self._ordered_sheet_range(row_index + 1, col_index + 1, row_index + 1, col_index + 1)

    def _ordered_sheet_range(self, row1: int, col1: int, row2: int, col2: int) -> dict[str, int]:
        top = max(1, min(row1, row2))
        bottom = max(1, max(row1, row2))
        left = max(1, min(col1, col2))
        right = max(1, max(col1, col2))
        return {
            "top": top,
            "left": left,
            "bottom": bottom,
            "right": right,
        }

    def _trim_sheet_rows(self, rows: list[list[str]]) -> list[list[str]]:
        max_width = 1
        for row in rows:
            last_non_empty = 0
            for index, value in enumerate(row, start=1):
                if str(value).strip():
                    last_non_empty = index
            max_width = max(max_width, last_non_empty)
        return [list(row[:max_width]) for row in rows]

    def _find_sheet_value(
        self,
        rows: list[list[str]],
        normalized_target: str,
        *,
        preferred_rows: set[int] | None,
    ) -> tuple[int, int] | None:
        best: tuple[int, int, int] | None = None
        best_position: tuple[int, int] | None = None
        for row_index, row in enumerate(rows):
            if preferred_rows is not None and row_index not in preferred_rows:
                continue
            for col_index, value in enumerate(row):
                score = self._text_match_score(str(value), normalized_target)
                if score <= 0:
                    continue
                candidate = (score, -row_index, -col_index)
                if best is None or candidate > best:
                    best = candidate
                    best_position = (row_index, col_index)
        if best is None or best_position is None:
            return None
        return best_position

    def _row_candidates_from_highlight(
        self,
        current_highlight: dict[str, int],
        row_count: int,
    ) -> set[int]:
        if not current_highlight:
            return set()
        top = max(1, int(current_highlight.get("top", 1))) - 1
        bottom = max(top, int(current_highlight.get("bottom", top + 1)) - 1)
        rows = set(range(top, min(row_count, bottom + 1)))
        if rows:
            rows.update(range(max(0, top - 1), min(row_count, bottom + 2)))
        return rows

    def _refine_pdf_geometry(
        self,
        page_data: PageData,
        target_value: str,
        current_kind: str,
        current_rect: Rect | None,
    ) -> tuple[str, dict[str, object], Rect | None] | None:
        target_tokens = self._target_tokens(target_value)
        if not target_tokens:
            return None
        anchor_rect = current_rect
        exact_candidate = self._best_pdf_text_candidate(page_data, target_tokens, anchor_rect, exact_only=True)
        if exact_candidate is not None:
            return exact_candidate
        broad_candidate = self._best_pdf_text_candidate(page_data, target_tokens, anchor_rect, exact_only=False)
        if broad_candidate is not None:
            return broad_candidate
        if current_kind == "rect" and current_rect is not None:
            return current_kind, {"rect": list(current_rect)}, current_rect
        return None

    def _best_pdf_text_candidate(
        self,
        page_data: PageData,
        target_tokens: list[str],
        anchor_rect: Rect | None,
        *,
        exact_only: bool,
    ) -> tuple[str, dict[str, object], Rect | None] | None:
        if len(target_tokens) > 1:
            token_matches = [
                self._best_single_pdf_match(page_data, token, anchor_rect, exact_only=exact_only)
                for token in target_tokens[:4]
            ]
            token_matches = [match for match in token_matches if match is not None]
            if len(token_matches) >= 2:
                rect = self._bbox_union([match for match in token_matches])
                if rect is not None:
                    return "rect", {"rect": list(rect)}, rect
        single_match = self._best_single_pdf_match(
            page_data,
            " ".join(target_tokens),
            anchor_rect,
            exact_only=exact_only,
        )
        if single_match is not None:
            return "rect", {"rect": list(single_match)}, single_match
        for token in target_tokens:
            token_match = self._best_single_pdf_match(page_data, token, anchor_rect, exact_only=exact_only)
            if token_match is not None:
                return "rect", {"rect": list(token_match)}, token_match
        return None

    def _best_single_pdf_match(
        self,
        page_data: PageData,
        normalized_target: str,
        anchor_rect: Rect | None,
        *,
        exact_only: bool,
    ) -> Rect | None:
        best_rect: Rect | None = None
        best_key: tuple[float, float, float] | None = None
        for rect, text in self._iter_pdf_text_rects(page_data):
            score = self._text_match_score(text, normalized_target)
            if exact_only and score < 3:
                continue
            if score <= 0:
                continue
            distance = self._rect_distance(rect, anchor_rect)
            area = max(1.0, (rect[2] - rect[0]) * (rect[3] - rect[1]))
            key = (float(score), -distance, -area)
            if best_key is None or key > best_key:
                best_key = key
                best_rect = rect
        return best_rect

    def _iter_pdf_text_rects(self, page_data: PageData):
        for pair in page_data.kv_pairs:
            if pair.value:
                rect = self._bbox_union([pair.value_bbox, pair.key_bbox])
                if rect is not None:
                    yield rect, str(pair.value)
        for table in page_data.tables:
            for cell in table.cells:
                if cell.text:
                    yield self._as_rect(cell.bbox), str(cell.text)
        for block in page_data.blocks:
            if block.text:
                yield self._as_rect(block.bbox), str(block.text)

    def _pdf_highlight_text(
        self,
        page_data: PageData,
        highlight_kind: str,
        highlight_geometry: dict[str, object],
        target_value: str,
        fallback_text: str,
    ) -> str:
        if highlight_kind == "rect":
            rect_data = highlight_geometry.get("rect")
            if isinstance(rect_data, list) and len(rect_data) == 4:
                best_text = self._best_rect_text(page_data, self._as_rect(rect_data), target_value)
                if best_text:
                    return best_text
        if highlight_kind == "polyline":
            polyline_data = highlight_geometry.get("polyline")
            if isinstance(polyline_data, list):
                polyline = self._as_polyline(polyline_data)
                if polyline:
                    bounds = self._polyline_bounds(polyline)
                    if bounds is not None:
                        best_text = self._best_rect_text(page_data, bounds, target_value)
                        if best_text:
                            return best_text
        return fallback_text

    def _best_rect_text(
        self,
        page_data: PageData,
        target_rect: Rect,
        target_value: str,
    ) -> str:
        normalized_target = normalize_label(target_value)
        best_text = ""
        best_key: tuple[float, float, float, float] | None = None
        for rect, text in self._iter_pdf_text_rects(page_data):
            overlap_area = self._rect_intersection_area(rect, target_rect)
            if overlap_area <= 0:
                continue
            area = max(1.0, (rect[2] - rect[0]) * (rect[3] - rect[1]))
            score = self._text_match_score(text, normalized_target) if normalized_target else 0
            distance = self._rect_distance(rect, target_rect)
            key = (float(score), overlap_area / area, -distance, -area)
            if best_key is None or key > best_key:
                best_key = key
                best_text = str(text).strip()
        return best_text

    def _sheet_highlight_text(
        self,
        sheet: SheetData,
        highlight: dict[str, int],
        target_value: str,
        fallback_text: str,
    ) -> str:
        values = self._sheet_cells_in_highlight(sheet.rows, highlight)
        if not values:
            return fallback_text
        normalized_target = normalize_label(target_value)
        if normalized_target:
            for value in values:
                if self._text_match_score(value, normalized_target) > 0:
                    return value
        return " | ".join(value for value in values if value.strip()) or fallback_text

    def _sheet_cells_in_highlight(
        self,
        rows: list[list[str]],
        highlight: dict[str, int],
    ) -> list[str]:
        if not highlight:
            return []
        top = max(1, int(highlight.get("top", 1))) - 1
        left = max(1, int(highlight.get("left", 1))) - 1
        bottom = max(top, int(highlight.get("bottom", top + 1)) - 1)
        right = max(left, int(highlight.get("right", left + 1)) - 1)
        values: list[str] = []
        for row_index in range(top, min(len(rows), bottom + 1)):
            row = rows[row_index]
            for col_index in range(left, min(len(row), right + 1)):
                values.append(str(row[col_index]))
        return values

    def _target_tokens(self, target_value: str) -> list[str]:
        cleaned = clean_cell(target_value)
        if not cleaned:
            return []
        tokens = [normalize_label(cleaned)]
        for piece in re.split(r"\s*\|\s*|\s*;\s*|\s*,\s*", cleaned):
            normalized = normalize_label(piece)
            if normalized and normalized not in tokens:
                tokens.append(normalized)
        return [token for token in tokens if token]

    def _text_match_score(self, text: str, normalized_target: str) -> int:
        normalized_text = normalize_label(text)
        if not normalized_target or not normalized_text:
            return 0
        if normalized_text == normalized_target:
            return 4
        if f" {normalized_target} " in f" {normalized_text} ":
            return 3
        if normalized_target in normalized_text or normalized_text in normalized_target:
            return 2
        return 0

    def _rect_distance(self, rect: Rect, anchor_rect: Rect | None) -> float:
        if anchor_rect is None:
            return 0.0
        rect_cx = (rect[0] + rect[2]) / 2.0
        rect_cy = (rect[1] + rect[3]) / 2.0
        anchor_cx = (anchor_rect[0] + anchor_rect[2]) / 2.0
        anchor_cy = (anchor_rect[1] + anchor_rect[3]) / 2.0
        return abs(rect_cx - anchor_cx) + abs(rect_cy - anchor_cy)

    def _rect_intersection_area(self, left: Rect, right: Rect) -> float:
        overlap_left = max(left[0], right[0])
        overlap_top = max(left[1], right[1])
        overlap_right = min(left[2], right[2])
        overlap_bottom = min(left[3], right[3])
        if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
            return 0.0
        return float((overlap_right - overlap_left) * (overlap_bottom - overlap_top))

    def _safe_literal_eval(self, text: str):
        if not text:
            return None
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return None

    def _is_rect_sequence(self, value) -> bool:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return False
        return all(isinstance(item, (int, float)) for item in value)

    def _as_rect(self, value) -> Rect:
        x0, y0, x1, y1 = value
        left = float(min(x0, x1))
        top = float(min(y0, y1))
        right = float(max(x0, x1))
        bottom = float(max(y0, y1))
        return left, top, right, bottom

    def _as_polyline(self, value) -> list[tuple[float, float]]:
        if not isinstance(value, (list, tuple)):
            return []
        points: list[tuple[float, float]] = []
        for item in value:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                return []
            if not all(isinstance(coord, (int, float)) for coord in item):
                return []
            points.append((float(item[0]), float(item[1])))
        return points

    def _polyline_bounds(self, polyline: list[tuple[float, float]]) -> Rect | None:
        if not polyline:
            return None
        xs = [point[0] for point in polyline]
        ys = [point[1] for point in polyline]
        return float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))

    def _bbox_union(self, boxes: list[Rect | tuple[float, float, float, float] | None]) -> Rect | None:
        cleaned = [self._as_rect(box) for box in boxes if box is not None]
        if not cleaned:
            return None
        return (
            min(box[0] for box in cleaned),
            min(box[1] for box in cleaned),
            max(box[2] for box in cleaned),
            max(box[3] for box in cleaned),
        )

    def _excel_column_to_index(self, label: str) -> int:
        value = 0
        for char in label.upper():
            if not ("A" <= char <= "Z"):
                continue
            value = value * 26 + (ord(char) - ord("A") + 1)
        return max(1, value)
