from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import threading
from pathlib import Path
import re
from collections import defaultdict
from typing import Any, Callable

from iev4pi_transformation_tool.core.evidence_resolver import EvidenceResolver
from iev4pi_transformation_tool.core.llm_client import OpenAICompatibleLLMClient
from iev4pi_transformation_tool.core.relations import RelationResolver
from iev4pi_transformation_tool.core.retriever import Retriever
from iev4pi_transformation_tool.core.utils import (
    build_header_map,
    clean_cell,
    ensure_dir,
    extract_component_tokens,
    looks_like_identifier,
    normalize_label,
)
from iev4pi_transformation_tool.models import (
    DocumentFamily,
    EvidenceRef,
    ExtractedFieldResult,
    ExtractedRecord,
    ExtractionStatus,
    ParsedDocument,
    SchemaFamily,
    SchemaField,
)

TU_IDENTIFIER_PREFIX_PATTERN = re.compile(
    r"^\s*\.?(?P<identifier>[A-Za-z]{1,4}\d+(?:\.[A-Za-z]\d+)+)\s+(?P<label>.+?)\s*$"
)

ProgressCallback = Callable[[int, str], None]
LLM_NORMALIZATION_PROMPT_VERSION = "20260401_v1"


# ---------------------------------------------------------------------------
# Centralised extraction quality configuration
# ---------------------------------------------------------------------------

# Confidence scores keyed by extraction path.  Tune these in one place
# instead of hunting through 30+ scattered literals.
_EXTRACTION_CONFIDENCE = {
    # Tabular / structured extraction
    "tabular.filled": 1.0,
    "tabular.blank": 0.0,
    "tabular.from_header": 0.95,
    # TU field extraction
    "tu_field.tag_from_filename": 0.95,
    "tu_field.rag_threshold": 0.76,
    "tu_field.llm_threshold": 0.76,
    "tu_field.default": 0.0,
    # Component extraction
    "components.structured.controller_module": 0.90,
    "components.structured.default": 0.84,
    "components.graph.with_reference": 0.70,
    "components.graph.without_reference": 0.62,
    "components.text.with_reference": 0.65,
    "components.text.without_reference": 0.55,
    # Connection trace
    "connection.trace_filled_threshold": 0.75,
    "connection.evidence_score_min": 0.40,
    "connection.evidence_score_max": 0.85,
    # LLM
    "llm.normalization_threshold": 0.40,
    "llm.normalization_cap": 1.0,
    # R&I node fields
    "ri_node.key_field": 1.0,
    "ri_node.general": 0.92,
    "ri_node.evidence_summary": 0.88,
    "ri_node.source_locator": 1.0,
    # R&I instrument fields
    "ri_instrument.key_field": 1.0,
    "ri_instrument.default": 0.95,
    "ri_instrument.evidence_summary": 0.88,
    "ri_instrument.blank": 0.0,
    # R&I connection fields
    "ri_connection.key_field": 1.0,
    "ri_connection.default": 0.90,
    "ri_connection.evidence_summary": 0.85,
    # IFC node fields
    "ifc_node.key_field": 1.0,
    "ifc_node.default": 0.95,
    "ifc_node.blank": 0.0,
    # IFC edge fields
    "ifc_edge.key_field": 1.0,
    "ifc_edge.default": 0.95,
    "ifc_edge.blank": 0.0,
    # PDF graph fallback
    "pdf_graph.edge_cap": 0.75,
}

# Maximum number of evidence references to attach per extracted field.
# Reducing this lowers output verbosity; raising it preserves more traceability.
_MAX_EVIDENCE_REFS = 3

# Maximum number of evidence bundles to collect for PDF-based extraction.
_MAX_PDF_EVIDENCE_BUNDLES = 2



class Extractor:
    def __init__(
        self,
        retriever: Retriever | None = None,
        llm_client: OpenAICompatibleLLMClient | None = None,
        evidence_resolver: EvidenceResolver | None = None,
        *,
        cache_dir: Path | None = None,
        logger: Callable[..., Any] | None = None,
    ) -> None:
        self.retriever = retriever or Retriever()
        self.relation_resolver = RelationResolver()
        self.llm_client = llm_client
        self.evidence_resolver = evidence_resolver
        self.cache_dir = ensure_dir(cache_dir) if cache_dir is not None else None
        self._logger = logger
        self._extractor_lock = threading.RLock()
        self._evidence_bundle_local = threading.local()

    def _log_debug(
        self,
        *,
        action: str,
        message: str,
        level: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._logger is None:
            return
        self._logger(
            source="llm",
            action=action,
            message=message,
            level=level,
            details=details,
        )

    def _parallel_workers(self) -> int:
        from iev4pi_transformation_tool.core.qos_helpers import io_worker_count

        config = getattr(self.llm_client, "config", None)
        raw_value = getattr(config, "parallel_workers", 0) if config is not None else 0
        try:
            base_workers = max(1, int(raw_value or 0))
            if base_workers > 1:
                return max(1, min(8, base_workers))
            return io_worker_count(cap=8)
        except (TypeError, ValueError):
            return io_worker_count(cap=8)

    def extract(
        self,
        parsed: ParsedDocument,
        schemas: dict[DocumentFamily, SchemaFamily],
        *,
        retrieval_top_k: int = 5,
        reference_tokens: set[str] | None = None,
        progress: ProgressCallback | None = None,
    ) -> list[ExtractedRecord]:
        reference_tokens = reference_tokens or set()
        records: list[ExtractedRecord] = []
        for family in parsed.document.output_families:
            schema = schemas.get(family)
            if schema is None:
                continue
            if family in {
                DocumentFamily.STELLEN_OVERVIEW_RECORD,
                DocumentFamily.KLEMMENPLAN_ROW,
                DocumentFamily.VERSCHALTUNGSLISTE_ROW,
                DocumentFamily.CABINET_REFERENCE_ROW,
            }:
                records.extend(self._extract_tabular(parsed, schema))
            elif family == DocumentFamily.STELLEN_TU_DATASHEET:
                records.extend(self._extract_tu_pdf(parsed, schema, retrieval_top_k, progress))
            elif family == DocumentFamily.STROMLAUF_COMPONENT_GROUP:
                records.extend(self._extract_component_groups(parsed, schema))
            elif family == DocumentFamily.STROMLAUF_COMPONENT:
                records.extend(self._extract_components(parsed, schema, reference_tokens))
            elif family == DocumentFamily.STROMLAUF_CONNECTION:
                records.extend(
                    self._extract_structured_connections(parsed, schema)
                    or self.relation_resolver.resolve(parsed, schema, reference_tokens)
                )
            elif family in {
                DocumentFamily.RI_EQUIPMENT_ROW,
                DocumentFamily.RI_PIPING_COMPONENT_ROW,
            }:
                records.extend(self._extract_ri_nodes(parsed, schema))
            elif family == DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW:
                records.extend(self._extract_ri_instrument_instances(parsed, schema))
            elif family == DocumentFamily.RI_CONNECTION_ROW:
                records.extend(self._extract_ri_connections(parsed, schema))
            elif family == DocumentFamily.IFC_PIPING_ITEM_ROW:
                records.extend(self._extract_ifc_nodes(parsed, schema))
            elif family == DocumentFamily.IFC_CONNECTION_ROW:
                records.extend(self._extract_ifc_edges(parsed, schema))
            elif family == DocumentFamily.IFC_3D_ASSEMBLY_STEP:
                records.extend(self._extract_3d_assembly_sheet(parsed, schema, "Assembly_Steps"))
            elif family == DocumentFamily.IFC_3D_ASSEMBLY_CONNECTION:
                records.extend(self._extract_3d_assembly_sheet(parsed, schema, "Connection_Topology"))
            elif family == DocumentFamily.IFC_3D_POSITION:
                records.extend(self._extract_3d_assembly_sheet(parsed, schema, "Position_Data"))
            elif family == DocumentFamily.IFC_3D_PART_LIBRARY:
                records.extend(self._extract_3d_assembly_sheet(parsed, schema, "Part_Library"))
        return records

    def _extract_3d_assembly_sheet(self, parsed, schema, sheet_name: str) -> list:
        """Extract records from a named sheet in an Assembly 3D workbook."""
        records = []
        source_path = getattr(parsed.document, "relative_path", "") or getattr(parsed.document, "path", "")
        for sheet in parsed.sheets:
            if sheet.name != sheet_name:
                continue
            header_map = build_header_map(sheet.rows, sheet.header_rows)
            data_start = max(sheet.header_rows, default=1) + 1
            for row_number, row in enumerate(sheet.rows, start=1):
                if row_number < data_start or not any(cell.strip() for cell in row):
                    continue
                results = []
                display_name = ""
                for field in schema.fields:
                    value = ""
                    for col_idx, col_name in sorted(header_map.items()):
                        if field.name.lower() == col_name.lower():
                            value = row[col_idx].strip() if col_idx < len(row) else ""
                            break
                    display_name = display_name or value
                    status = ExtractionStatus.FILLED if value else ExtractionStatus.BLANK_NO_EVIDENCE
                    results.append(ExtractedFieldResult(
                        field_name=field.name,
                        value=value,
                        normalized_value=value.strip().lower(),
                        confidence=1.0 if value else 0.0,
                        status=status,
                    ))
                if any(r.status == ExtractionStatus.FILLED for r in results):
                    records.append(ExtractedRecord(
                        family=schema.family,
                        source_path=source_path,
                        record_key=f"{source_path}::{sheet_name}::row{row_number}",
                        display_name=display_name or f"{sheet_name}_{row_number}",
                        results=results,
                    ))
        return records

    def _extract_tabular(self, parsed: ParsedDocument, schema: SchemaFamily) -> list[ExtractedRecord]:
        records: list[ExtractedRecord] = []
        for sheet in parsed.sheets:
            header_map = build_header_map(sheet.rows, sheet.header_rows)
            field_matches = self._match_fields_to_columns(schema.fields, header_map)
            data_start = max(sheet.header_rows, default=1) + 1
            for row_number, row in enumerate(sheet.rows, start=1):
                if row_number < data_start or not any(cell.strip() for cell in row):
                    continue
                results: list[ExtractedFieldResult] = []
                display_name = ""
                for field in schema.fields:
                    matched_columns = field_matches.get(field.name, [])
                    values = []
                    evidences = []
                    for column in matched_columns:
                        if column - 1 >= len(row):
                            continue
                        value = clean_cell(row[column - 1])
                        if not value:
                            continue
                        values.append(value)
                        evidences.append(
                            EvidenceRef(
                                source_path=parsed.document.relative_path,
                                page_or_sheet=sheet.name,
                                cell_range_or_bbox=f"{column}:{row_number}",
                                snippet=f"{header_map.get(column, field.name)}: {value}",
                                score=1.0,
                                evidence_type="table_cell",
                                engine="calamine",
                            )
                        )
                    if values:
                        combined = " | ".join(values) if field.repeatable else values[0]
                        if not display_name and (field.name.endswith("id") or "tag" in field.name or "klemme" in field.name):
                            display_name = combined
                        results.append(
                            ExtractedFieldResult(
                                field_name=field.name,
                                value=combined,
                                normalized_value=combined,
                                confidence=1.0,
                                status=ExtractionStatus.FILLED,
                                evidence_refs=evidences,
                            )
                        )
                    else:
                        results.append(
                            ExtractedFieldResult(
                                field_name=field.name,
                                value="",
                                normalized_value="",
                                confidence=0.0,
                                status=ExtractionStatus.BLANK_NO_EVIDENCE,
                                notes="No matching non-empty cell found for this row.",
                            )
                        )
                if not any(result.value.strip() for result in results):
                    continue
                display_name = display_name or f"{sheet.name} row {row_number}"
                records.append(
                    ExtractedRecord(
                        family=schema.family,
                        source_path=parsed.document.relative_path,
                        record_key=f"{parsed.document.relative_path}::{sheet.name}::row{row_number}",
                        display_name=display_name,
                        results=results,
                    )
                )
        return records

    def _match_fields_to_columns(self, fields: list[SchemaField], header_map: dict[int, str]) -> dict[str, list[int]]:
        matches: dict[str, list[int]] = defaultdict(list)
        normalized_headers = {column: normalize_label(header) for column, header in header_map.items()}
        for field in fields:
            target_forms = {normalize_label(field.name)}
            target_forms.update(normalize_label(alias) for alias in field.aliases)
            for column, header in normalized_headers.items():
                if header in target_forms or any(alias in header or header in alias for alias in target_forms):
                    matches[field.name].append(column)
        return matches

    def _extract_tu_pdf(
        self,
        parsed: ParsedDocument,
        schema: SchemaFamily,
        retrieval_top_k: int,
        progress: ProgressCallback | None = None,
    ) -> list[ExtractedRecord]:
        # Clear per-document RAG cache to bound memory usage
        self._evidence_bundle_local.cache = {}

        # Fast path for Gerätedatenblätter (datasheet PDFs): these are long
        # operating-instruction / technical-manual documents (up to 9 MB,
        # hundreds of pages).  Full RAG retrieval + LLM verification over the
        # entire document is prohibitively slow.  Instead, extract only basic
        # identification fields directly — the detailed parameter extraction
        # is handled by the datasheet parser in _export_datasheet.
        _is_datasheet = "Gerätedatenblätter" in parsed.document.relative_path
        if _is_datasheet:
            results = self._extract_tu_datasheet_fast(parsed, schema)
            return [
                ExtractedRecord(
                    family=DocumentFamily.STELLEN_TU_DATASHEET,
                    source_path=parsed.document.relative_path,
                    record_key=parsed.document.relative_path,
                    display_name=parsed.document.path.stem,
                    results=results,
                )
            ]

        # Batch LLM extraction: for small TU PDFs (1-5 pages), send the entire
        # document text + all field names to the LLM in ONE call.  Eliminates
        # per-field RAG retrieval and per-field LLM verification/normalization.
        # Falls back to key-values-only (no RAG, no LLM) if batch fails, since
        # per-field RAG+LLM is prohibitively slow for production use.
        if self.llm_client and self.llm_client.available():
            if progress:
                progress(-1, f"[BATCH LLM] {parsed.document.path.name}: 1 call for all fields")
            _batch_results = self._extract_tu_batch_llm(parsed, schema)
            if _batch_results is not None:
                filename_tag = re.sub(r"\.pdf$", "", parsed.document.path.name, flags=re.IGNORECASE)
                return [
                    ExtractedRecord(
                        family=DocumentFamily.STELLEN_TU_DATASHEET,
                        source_path=parsed.document.relative_path,
                        record_key=parsed.document.relative_path,
                        display_name=filename_tag,
                        results=_batch_results,
                    )
                ]
            # Batch LLM failed — fall through to key-values-only fast path.
            # Skip per-field RAG+LLM entirely: too slow for 20+ fields × 30 docs.

        # Fast fallback: key_values + filename only (no RAG, no LLM).
        # Detailed extraction happens later in _export_datasheet / _export_stellenplan.
        filename_tag = re.sub(r"\.pdf$", "", parsed.document.path.name, flags=re.IGNORECASE)
        _fast_results = self._extract_tu_key_values_only(parsed, schema, filename_tag)
        return [
            ExtractedRecord(
                family=DocumentFamily.STELLEN_TU_DATASHEET,
                source_path=parsed.document.relative_path,
                record_key=parsed.document.relative_path,
                display_name=filename_tag,
                results=_fast_results,
            )
        ]

    def _extract_tu_key_values_only(
        self, parsed: ParsedDocument, schema: SchemaFamily, filename_tag: str
    ) -> list[ExtractedFieldResult]:
        """Fast extraction using only key_values and filename, no RAG, no LLM.

        Filters out OCR-merged label text by detecting values that consist
        entirely of title-block label fragments (short tokens with colons /
        periods, typical of OCR label merging like "Gepr.: Norm:").
        """
        key_values = self._collect_key_values(parsed)
        results: list[ExtractedFieldResult] = []
        for field in schema.fields:
            value = ""
            confidence = 0.0
            if field.name == "tag":
                value = filename_tag
                confidence = 0.95
            else:
                for alias in self._candidate_aliases(field):
                    entry = key_values.get(alias)
                    if entry:
                        val, _, conf, _ = entry
                        if not val or not val.strip():
                            continue
                        # Skip entries where value equals the alias itself
                        if val.strip().lower() == alias.strip().lower():
                            continue
                        # Skip OCR-merged label text: values that are short
                        # and composed entirely of colon/dot-suffixed tokens
                        # (e.g. "Gepr.: Norm:") — these are label fragments,
                        # not real field values.
                        _tokens = val.strip().split()
                        if len(_tokens) <= 4 and all(
                            len(t.rstrip(":.")) <= 10 and (
                                t.endswith(":") or t.endswith(".")
                            )
                            for t in _tokens
                        ):
                            continue
                        value, confidence = val, conf
                        break
            status = ExtractionStatus.FILLED if value else ExtractionStatus.BLANK_NO_EVIDENCE
            results.append(ExtractedFieldResult(
                field_name=field.name, value=value, normalized_value=value,
                confidence=confidence, status=status,
                evidence_refs=[], notes="key-values fast path",
            ))
        return results

    # Cache for batch LLM TU extraction — avoids re-running for the same
    # PDF during extraction + fill_standardized_templates passes.
    _batch_llm_cache: dict[str, list[ExtractedFieldResult] | None] = {}

    def _extract_tu_batch_llm(
        self, parsed: ParsedDocument, schema: SchemaFamily
    ) -> list[ExtractedFieldResult] | None:
        """Extract all TU fields in a single LLM call — no RAG, no per-field API.

        TU PDFs are small (1-5 pages), so the full document text fits in the
        LLM context.  Sending all fields at once eliminates the N×RAG + N×LLM
        overhead of the per-field approach.  Results cached by PDF path.
        """
        cache_key = str(parsed.document.relative_path)  # already relative
        with self._extractor_lock:
            if cache_key in self._batch_llm_cache:
                return self._batch_llm_cache[cache_key]

        # Collect all text from the document
        text_parts: list[str] = []
        for page in parsed.pages:
            for block in page.blocks:
                t = (block.text or "").strip()
                if t:
                    text_parts.append(t)
            for table in page.tables:
                for cell in table.cells:
                    t = (cell.text or "").strip() if hasattr(cell, "text") else ""
                    if t:
                        text_parts.append(t)
        full_text = "\n".join(text_parts)
        if len(full_text) < 50:
            return None  # too little text, fall back to per-field

        # Build field list for the LLM
        field_names = [f.name for f in schema.fields]
        field_list = "\n".join(f"- {n}" for n in field_names)

        prompt = (
            "You are extracting structured information from an engineering "
            "Stellenplan (instrument list) PDF.\n\n"
            f"Available fields:\n{field_list}\n\n"
            "CRITICAL: The OCR text may have merged adjacent labels (e.g. "
            "\"Gepr.: Norm:\" where \"Gepr.:\" and \"Norm:\" are separate field "
            "labels, NOT values). Text like \"Erstellt:\", \"Bearb.:\", "
            "\"Gepr.:\", \"Norm:\", \"Datum:\", \"Name:\", \"Rev.\" are LABELS "
            "— never return these as field values.\n\n"
            "For each field, extract the ACTUAL value from the document text. "
            "If a field cannot be found, leave its value empty. "
            "If the only text found is a label (e.g. \"Gepr.:\" without a real "
            "name after it), set value to empty and confidence to 0.\n\n"
            "Return JSON: {\"fields\": [{\"name\": \"field_name\", \"value\": \"...\", "
            "\"confidence\": 0.95}, ...]}\n\n"
            f"Document text:\n{full_text[:8000]}"
        )

        try:
            response = self.llm_client.chat_json(prompt)
        except Exception:
            with self._extractor_lock:
                self._batch_llm_cache[cache_key] = None
            return None

        if not isinstance(response, dict):
            with self._extractor_lock:
                self._batch_llm_cache[cache_key] = None
            return None

        fields_data = response.get("fields", [])
        if not isinstance(fields_data, list) or not fields_data:
            with self._extractor_lock:
                self._batch_llm_cache[cache_key] = None
            return None

        # Map LLM results to ExtractedFieldResult objects
        llm_values: dict[str, tuple[str, float]] = {}
        for fd in fields_data:
            if isinstance(fd, dict):
                name = str(fd.get("name", ""))
                val = str(fd.get("value", "")) if fd.get("value") else ""
                conf = float(fd.get("confidence", 0.7))
                # Filter: skip entries where the LLM returned the field name
                # as the value, OCR label text, or conf < 0.5
                if not name or not val or conf < 0.5:
                    continue
                if val.lower().strip() == name.lower().strip():
                    continue
                # Skip values that are OCR-merged label fragments:
                # short tokens ending in colon/dot (e.g. "Gepr.: Norm:")
                _tokens = val.strip().split()
                if len(_tokens) <= 4 and all(
                    len(t.rstrip(":.")) <= 10 and (
                        t.endswith(":") or t.endswith(".")
                    )
                    for t in _tokens
                ):
                    continue
                llm_values[name] = (val, conf)

        results: list[ExtractedFieldResult] = []
        for field in schema.fields:
            if field.name == "tag":
                tag_val = parsed.document.path.stem
                results.append(ExtractedFieldResult(
                    field_name=field.name, value=tag_val, normalized_value=tag_val,
                    confidence=0.95, status=ExtractionStatus.FILLED,
                    evidence_refs=[], notes="batch LLM (filename tag)",
                ))
            elif field.name in llm_values:
                val, conf = llm_values[field.name]
                results.append(ExtractedFieldResult(
                    field_name=field.name, value=val, normalized_value=val,
                    confidence=min(conf, 0.90), status=ExtractionStatus.FILLED,
                    evidence_refs=[], notes="batch LLM extraction",
                ))
            else:
                results.append(ExtractedFieldResult(
                    field_name=field.name, value="", normalized_value="",
                    confidence=0.0, status=ExtractionStatus.BLANK_NO_EVIDENCE,
                    evidence_refs=[], notes="batch LLM: not found",
                ))
        with self._extractor_lock:
            self._batch_llm_cache[cache_key] = results
        return results

    def _extract_tu_datasheet_fast(
        self, parsed: ParsedDocument, schema: SchemaFamily
    ) -> list[ExtractedFieldResult]:
        """Extraction for Gerätedatenblätter PDFs.

        Uses VLM + heuristic datasheet parser.  Maps ALL extracted sections
        (Identification, Parameters, Connections, Physical) to field results
        so the export phase reads from records instead of re-parsing PDFs.
        """
        results: list[ExtractedFieldResult] = []
        ds: dict[str, dict[str, str]] = {}
        try:
            from iev4pi_transformation_tool.core.datasheet_parser import parse_datasheet_smart
            ds = parse_datasheet_smart(parsed.document.relative_path, llm_client=self.llm_client)
        except Exception:
            pass

        ident = ds.get("Identification", {})
        filename = parsed.document.path.stem

        # Case-insensitive lookup helper for VLM Identification dict
        def _ident_get(key: str) -> str:
            for k, v in ident.items():
                if k.lower() == key.lower():
                    return str(v)
            return ""

        for field in schema.fields:
            value = ""
            confidence = 0.0
            if field.name == "tag":
                value = filename
                confidence = 0.95
            elif field.name == "device":
                value = _ident_get("Model") or _ident_get("Device_Type") or _ident_get("order_number")
                confidence = 0.85
            elif field.name == "manufacturer":
                value = _ident_get("Manufacturer")
                confidence = 0.85
            elif field.name in ("model", "typ"):
                value = _ident_get("Model") or _ident_get("device_type")
                confidence = 0.85
            elif field.name in ("description", "beschreibung"):
                value = _ident_get("Description")
                confidence = 0.80

            status = ExtractionStatus.FILLED if value else ExtractionStatus.BLANK_NO_EVIDENCE
            results.append(ExtractedFieldResult(
                field_name=field.name, value=value, normalized_value=value,
                confidence=confidence, status=status,
                evidence_refs=[], notes="datasheet fast path" if value else "no datasheet evidence",
            ))

        # Map ALL VLM/heuristic sections to field results for export use.
        # Batch-classify parameters via LLM into process/technical/geometric/
        # connection categories so they flow into the correct export sheets.
        all_params: dict[str, str] = {}
        for section_name, params in ds.items():
            if section_name == "Identification":
                continue
            for param_name, param_value in params.items():
                pv = str(param_value).strip()
                if not pv:
                    continue
                all_params[f"{section_name}:{param_name}"] = pv

        # Extract connection specs via VLM (one extra call per datasheet).
        conn_specs = self._extract_datasheet_connections(parsed.document.relative_path)
        for conn_field, conn_value in conn_specs.items():
            pv = str(conn_value).strip()
            if pv:
                all_params[conn_field] = pv

        # Extract order/serial info via VLM (DPI=200, multi-page).  Cached.
        order_specs = self._extract_datasheet_ordering(parsed.document.relative_path)
        for k, v in order_specs.items():
            pv = str(v).strip()
            if pv:
                all_params[k] = pv

        # Pre-populate value/unit splitting cache for export use.
        # Batch all values through LLM once; export hits cache = 0 API calls.
        if self.llm_client and self.llm_client.available():
            from iev4pi_transformation_tool.core.datasheet_parser import split_value_unit_llm_batch
            all_values = list(all_params.values())
            split_value_unit_llm_batch(all_values, self.llm_client)

        # LLM post-processing pipeline — each step cached by PDF path.
        # Placeholder removal + name cleaning + category classification.
        all_params = self._filter_placeholders_cached(
            parsed.document.relative_path, all_params)
        all_params = self._clean_param_names_cached(
            parsed.document.relative_path, all_params)
        category_map = self._classify_params_cached(
            parsed.document.relative_path, all_params)

        for param_full_name, param_value in all_params.items():
            category = category_map.get(param_full_name, "parameters")
            # Strip original section prefix (e.g. "Parameters:", "Physical:")
            short_name = param_full_name.split(":", 1)[1] if ":" in param_full_name else param_full_name
            field_name = f"{category}:{short_name}"
            results.append(ExtractedFieldResult(
                field_name=field_name, value=param_value,
                normalized_value=param_value, confidence=0.80,
                status=ExtractionStatus.FILLED,
                evidence_refs=[], notes="VLM/heuristic datasheet",
            ))

        return results

    # Per-PDF caches for LLM datasheet param processing — avoid repeated
    # API calls when the same datasheet is processed during extraction + export.
    _placeholder_cache: dict[str, set[str]] = {}
    _name_clean_cache: dict[str, dict[str, str]] = {}
    _classification_cache: dict[str, dict[str, str]] = {}

    def _filter_placeholders_cached(self, pdf_path, params: dict[str, str]) -> dict[str, str]:
        """LLM-based placeholder filter.  Cached by PDF path."""
        cache_key = str(pdf_path)
        from pathlib import Path as _P
        try: cache_key = str(_P(cache_key).relative_to(_P.cwd()))
        except ValueError: pass
        if cache_key in self._placeholder_cache:
            placeholders = self._placeholder_cache[cache_key]
            return {k: v for k, v in params.items()
                    if v.strip().lower() not in placeholders} if placeholders else params

        if not params or not (self.llm_client and self.llm_client.available()):
            with self._extractor_lock:
                self._placeholder_cache[cache_key] = set()
            return {k: v for k, v in params.items() if v}

        candidates = {k: v for k, v in params.items()
                      if not any(c.isdigit() for c in v) and len(v) < 40}
        if not candidates:
            with self._extractor_lock:
                self._placeholder_cache[cache_key] = set()
            return params

        val_list = "\n".join(f'"{v}"' for v in list(candidates.values())[:30])
        prompt = (
            "Which of these values are placeholder text (not real data)?\n"
            "Examples: 'Not specified', 'N/A', 'Refer to manual', 'TBD', '-', "
            "'Not explicitly stated', 'Not provided', 'Not mentioned'\n\n"
            f"{val_list}\n\n"
            'Return JSON: {"placeholders": ["Not specified", ...]}'
        )
        try:
            response = self.llm_client.chat_json(
                "You detect placeholder text. Return ONLY valid JSON.", prompt)
            if isinstance(response, dict):
                ph = set(str(p).strip().lower()
                        for p in response.get("placeholders", []))
                with self._extractor_lock:
                    self._placeholder_cache[cache_key] = ph
                return {k: v for k, v in params.items()
                        if v.strip().lower() not in ph} if ph else params
        except Exception:
            pass
        with self._extractor_lock:
            self._placeholder_cache[cache_key] = set()
        return params

    def _clean_param_names_cached(self, pdf_path, params: dict[str, str]) -> dict[str, str]:
        """LLM-based parameter name cleaner.  Cached by PDF path."""
        cache_key = str(pdf_path)
        if cache_key in self._name_clean_cache:
            name_map = self._name_clean_cache[cache_key]
            if name_map:
                for old, new in name_map.items():
                    if old in params:
                        params[str(new).strip()] = params.pop(old)
            return params

        garbled = {k: v for k, v in params.items()
                   if any(c in k for c in "[]()") or len(k) > 50}
        if not garbled or not (self.llm_client and self.llm_client.available()):
            with self._extractor_lock:
                self._name_clean_cache[cache_key] = {}
            return params

        name_list = "\n".join(f'"{k}": "{v[:60]}"' for k, v in list(garbled.items())[:20])
        prompt = (
            "Clean these garbled parameter names. Extract just the parameter "
            "NAME (not value or unit).\n\n"
            f"{name_list}\n\n"
            'Return JSON: {"cleaned": {"old_name": "Clean Name", ...}}'
        )
        try:
            response = self.llm_client.chat_json(
                "You clean parameter names. Return ONLY valid JSON.", prompt)
            if isinstance(response, dict):
                cleaned = response.get("cleaned", {})
                if isinstance(cleaned, dict):
                    name_map = {str(k): str(v) for k, v in cleaned.items() if v}
                    with self._extractor_lock:
                        self._name_clean_cache[cache_key] = name_map
                    for old, new in name_map.items():
                        if old in params:
                            params[str(new).strip()] = params.pop(old)
        except Exception:
            with self._extractor_lock:
                self._name_clean_cache[cache_key] = {}
        return params

    def _classify_params_cached(self, pdf_path, params: dict[str, str]) -> dict[str, str]:
        """LLM batch classification.  Cached by PDF path."""
        cache_key = str(pdf_path)
        if cache_key in self._classification_cache:
            return self._classification_cache[cache_key]

        if not params or not (self.llm_client and self.llm_client.available()):
            with self._extractor_lock:
                self._classification_cache[cache_key] = {}
            return {}

        param_list = "\n".join(f'"{k}": "{v[:80]}"' for k, v in list(params.items())[:60])
        prompt = (
            "Classify each datasheet parameter:\n"
            "- process: measurement/control (temp, pressure, flow, level, range, "
            "process connection, output signal, accuracy, response, medium)\n"
            "- technical: electrical (voltage, current, power, signal type, "
            "bus protocol, inputs/outputs, functional specs)\n"
            "- geometric: physical (dimensions, weight, material, mounting, "
            "protection class, housing)\n"
            "- connection: connector/terminal/wiring\n\n"
            f"Parameters:\n{param_list}\n\n"
            'Return JSON: {"classifications": {"param_name": "technical", ...}}'
        )
        try:
            response = self.llm_client.chat_json(
                "You classify datasheet parameters. Return ONLY valid JSON.", prompt)
            if isinstance(response, dict):
                cl = response.get("classifications", {})
                if isinstance(cl, dict):
                    result = {str(k): str(v) for k, v in cl.items() if v}
                    with self._extractor_lock:
                        self._classification_cache[cache_key] = result
                    return result
        except Exception:
            pass
        with self._extractor_lock:
            self._classification_cache[cache_key] = {}
        return {}

    _vlm_connection_cache: dict[str, dict[str, str]] = {}

    def _extract_datasheet_connections(self, pdf_path) -> dict[str, str]:
        """Multi-strategy connection extraction.  Cached by relative PDF path."""
        from pathlib import Path as _Path
        cache_key = str(pdf_path)  # already relative from caller
        abs_path = _Path.cwd() / pdf_path  # resolve for file operations
        if cache_key in self._vlm_connection_cache:
            return self._vlm_connection_cache[cache_key]
        if not (self.llm_client and self.llm_client.available()):
            return {}

        try:
            import fitz, base64
            doc = fitz.open(str(abs_path)); total = len(doc)

            # Step 1: OCR keyword search for connection-related pages
            _conn_kw = ["connect", "terminal", "pin", "plug", "socket", "bus",
                "signal type", "power supply", "versorgung", "anschluss", "stecker",
                "klemme", "wiring", "front connector", "HART", "PROFIBUS",
                "4-20 mA", "0-10 V", "relay", "transistor"]
            scored_pages = []
            for pg in range(total):
                text = doc[pg].get_text().lower()
                score = sum(1 for kw in _conn_kw if kw in text)
                if score >= 2:
                    scored_pages.append((pg, score))

            # Step 2: DPI=200 VLM on top 3 keyword pages + page 1
            scored_pages.sort(key=lambda x: -x[1])
            target = [p for p, _ in scored_pages[:3]]
            if 0 not in target: target.append(0)
            target = list(dict.fromkeys(target))[:4]

            found = {}
            for pg in target:
                if len(found) >= 3: break
                pix = doc[pg].get_pixmap(dpi=200)
                img_b64 = base64.b64encode(pix.tobytes("png")).decode()
                resp = self.llm_client.chat_json_messages(
                    [{"role": "user", "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                        {"type": "text", "text": (
                            "Extract: connection_type, signal_type, power_supply, bus_protocol.\n"
                            'Return JSON: {"connection_type":"...","signal_type":"...",'
                            '"power_supply":"...","bus_protocol":"..."}. Empty if not found.'
                        )},
                    ]}],
                )
                if isinstance(resp, dict):
                    if "connection_specs" in resp:
                        resp = resp["connection_specs"]
                    for k, v in resp.items():
                        vs = str(v).strip()
                        if vs and len(vs) > 2 and "not specified" not in vs.lower():
                            found[k] = vs[:60]

            # Step 3: LLM text fallback if VLM found nothing
            if not found:
                all_text = ""
                for pg in range(min(5, total)): all_text += doc[pg].get_text()[:500]
                resp = self.llm_client.chat_json(
                    "Return ONLY valid JSON.",
                    f"Extract connection specs from this datasheet text.\n"
                    f'Return JSON: {{"connection_type":"...","signal_type":"...",'
                    f'"power_supply":"...","bus_protocol":"..."}}\nText: {all_text[:2000]}')
                if isinstance(resp, dict):
                    for k, v in resp.items():
                        vs = str(v).strip()
                        if vs and len(vs) > 2: found[k] = vs[:60]

            doc.close()
            with self._extractor_lock:
                self._vlm_connection_cache[cache_key] = found
            return found
        except Exception:
            pass
        with self._extractor_lock:
            self._vlm_connection_cache[cache_key] = {}
        return {}

    _vlm_ordering_cache: dict[str, dict[str, str]] = {}

    def _extract_datasheet_ordering(self, pdf_path) -> dict[str, str]:
        """Extract order_code and serial_number via VLM (DPI=200, multi-page).
        Scans last page + page 2 + keyword-matched pages.  Results cached."""
        from pathlib import Path as _Path
        cache_key = str(pdf_path)  # already relative from caller
        abs_path = _Path.cwd() / pdf_path
        if cache_key in self._vlm_ordering_cache:
            return self._vlm_ordering_cache[cache_key]
        if not (self.llm_client and self.llm_client.available()):
            return {}

        try:
            import fitz, base64
            doc = fitz.open(str(abs_path)); total = len(doc)

            # Find pages with order/eclass keywords
            order_kw = ["order", "bestell", "order code", "bestellnummer",
                        "article no", "part no", "ordering data", "eclass", "ecl@ss"]
            candidate_pages = set()
            eclass_pages = set()
            for pg in range(total):
                text = doc[pg].get_text().lower()
                if any(kw in text for kw in order_kw):
                    candidate_pages.add(pg)
                if "eclass" in text or "ecl@ss" in text:
                    eclass_pages.add(pg)

            # Scan: last page + first keyword page + eclass page + page 2
            target_pages = [total - 1]
            if candidate_pages:
                target_pages.append(min(candidate_pages))
            if eclass_pages:
                target_pages.append(min(eclass_pages))
            if 1 < total:
                target_pages.append(1)
            target_pages = list(dict.fromkeys(target_pages))[:5]

            found = {}
            for pg in target_pages:
                if pg < 0 or pg >= total:
                    continue
                pix = doc[pg].get_pixmap(dpi=200)
                img_b64 = base64.b64encode(pix.tobytes("png")).decode()
                resp = self.llm_client.chat_json_messages(
                    [{"role": "user", "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                        {"type": "text", "text": (
                            'Extract order_code (part number / Bestellnummer), '
                            'serial_number, and eclass (ECLASS classification code '
                            'like "27-24-22-04").\n'
                            'Return JSON: {"order_code":"...","serial_number":"...",'
                            '"eclass":"..."}. Empty string if not found.'
                        )},
                    ]}],
                )
                if isinstance(resp, dict):
                    for k, v in resp.items():
                        vs = str(v).strip()
                        if vs and len(vs) > 2 and "not specified" not in vs.lower():
                            found[k] = vs[:60]
            # LLM validation: filter non-order-code values (document numbers, dates, noise)
            if found.get("order_code"):
                found["order_code"] = self._clean_order_code(found["order_code"])
            doc.close()
            self._vlm_ordering_cache[cache_key] = found
            return found
        except Exception:
            pass
        self._vlm_ordering_cache[cache_key] = {}
        return {}

    def _clean_order_code(self, value: str) -> str:
        """LLM-based order code validation.  Delegates to shared implementation."""
        from iev4pi_transformation_tool.core.standardized_export import _clean_order_code_llm
        return _clean_order_code_llm(value)

    _STELLEN_CONNECTION_PATTERNS = (
        re.compile(r"=\+?\d+(?:\.[A-Z][A-Za-z0-9_]*)+(?:[A-Z][A-Za-z0-9_]*)?"),
        re.compile(r"-[A-Z]\d+(?:-\(\d+\))?-?[A-Z]?\d*"),
        re.compile(r"\+\d+\.[A-Z]\d+(?:\.[A-Z][A-Za-z0-9_-]*)+"),
        re.compile(r"=\.[A-Z]+\+?"),
    )

    def _extract_stellen_connections(self, parsed: ParsedDocument) -> ExtractedFieldResult | None:
        text_chunks: list[tuple[str, int]] = []
        for page in parsed.pages:
            for block in page.blocks:
                value = (block.text or "").strip()
                if value:
                    text_chunks.append((value, page.page_number))
            for table in page.tables:
                for cell in table.cells:
                    value = (cell.text or "").strip() if hasattr(cell, "text") else ""
                    if value:
                        text_chunks.append((value, page.page_number))
        seen: set[str] = set()
        ordered: list[tuple[str, int, str]] = []
        for chunk, page_no in text_chunks:
            for pattern in self._STELLEN_CONNECTION_PATTERNS:
                for match in pattern.findall(chunk):
                    token = match.strip()
                    if not token or len(token) < 3:
                        continue
                    if token in seen:
                        continue
                    seen.add(token)
                    ordered.append((token, page_no, chunk))
        if not ordered:
            return None
        evidence_refs: list[EvidenceRef] = []
        for token, page_no, snippet in ordered[:25]:
            evidence_refs.append(
                EvidenceRef(
                    source_path=parsed.document.relative_path,
                    page_or_sheet=f"page {page_no}",
                    cell_range_or_bbox="text_block",
                    snippet=f"{token} ← {snippet[:80]}",
                    score=0.85,
                    evidence_type="native_text",
                    engine="pymupdf",
                )
            )
        combined = " | ".join(token for token, _page, _snippet in ordered)
        return ExtractedFieldResult(
            field_name="connections",
            value=combined,
            normalized_value=combined,
            confidence=0.85,
            status=ExtractionStatus.FILLED,
            evidence_refs=evidence_refs,
            notes=f"{len(ordered)} cross-position references parsed from PDF text.",
        )

    def _extract_tu_field(
        self,
        *,
        parsed: ParsedDocument,
        field: SchemaField,
        retrieval_top_k: int,
        key_values: dict[str, tuple[str, list[EvidenceRef], float, str]],
        filename_tag: str,
        progress: ProgressCallback | None = None,
    ) -> ExtractedFieldResult:
        value = ""
        confidence = 0.0
        evidence_refs: list[EvidenceRef] = []
        notes = ""
        hits = []
        matched_key = ""
        resolver_status = ""
        resolver_uncertainty = ""
        resolver_rule_support = ["field_specific_retrieval"] if field.name != "tag" else []
        evidence_bundle_id = ""
        if field.name == "tag":
            value = filename_tag
            confidence = 0.95
            evidence_refs = [
                EvidenceRef(
                    source_path=parsed.document.relative_path,
                    page_or_sheet="Filename",
                    cell_range_or_bbox="filename",
                    snippet=filename_tag,
                    score=confidence,
                    evidence_type="native_text",
                    engine="filename",
                )
            ]
        else:
            candidates = self._candidate_aliases(field)
            # Phase 1: Check key_values first — no RAG needed
            for alias in candidates:
                entry = key_values.get(alias)
                if entry:
                    value, evidence_refs, confidence, matched_key = entry
                    break
            evidence_bundle = None
            evidence_bundle_id = ""
            # Phase 2: Only do RAG if key_values didn't find a high-confidence match.
            # Cache the evidence_bundle per document path so we don't re-chunk
            # and re-embed the same document for every field.
            if not value or confidence < 0.76:
                _doc_key = parsed.document.relative_path
                _cached = getattr(self._evidence_bundle_local, "cache", {}).get(_doc_key)
                if _cached is not None:
                    evidence_bundle = _cached
                    evidence_bundle_id = evidence_bundle.id
                else:
                    if progress:
                        progress(-1, f"RAG retrieving field {field.name}")
                    evidence_bundle = self.retriever.evidence_bundle(
                        query="; ".join(candidates),
                        top_k=retrieval_top_k,
                        document_path=parsed.document.relative_path,
                        family=DocumentFamily.STELLEN_TU_DATASHEET,
                    )
                    evidence_bundle_id = evidence_bundle.id
                    if not hasattr(self._evidence_bundle_local, "cache"):
                        self._evidence_bundle_local.cache = {}
                    self._evidence_bundle_local.cache[_doc_key] = evidence_bundle
                if not value:
                    hits = evidence_bundle.hits
                    value, evidence_refs, confidence, matched_key = self._try_structured_hit_match(candidates, hits)
                if (
                    self.evidence_resolver is not None
                    and (not value or confidence < 0.76)
                    and evidence_bundle.hits
                ):
                    if progress:
                        progress(-1, f"LLM verifying field {field.name}")
                    resolved = self.evidence_resolver.extract_field_value(
                        field=field,
                        source_path=parsed.document.relative_path,
                        evidence_bundle=evidence_bundle,
                    )
                    if resolved:
                        value = str(resolved.get("value", "") or "")
                        confidence = float(resolved.get("confidence", 0.0) or 0.0)
                        resolver_status = str(resolved.get("llm_verification_status", "") or "")
                        resolver_uncertainty = str(resolved.get("uncertainty_reason", "") or "")
                        if resolver_status:
                            resolver_rule_support = [*resolver_rule_support, "llm_evidence_verifier"]
                        evidence_refs = self._evidence_refs_from_bundle(
                            evidence_bundle,
                            support_ids=resolved.get("support_evidence_ids", []),
                            fallback_limit=2,
                        )
                        if not evidence_refs:
                            evidence_refs = self._evidence_refs_from_bundle(
                                evidence_bundle,
                                fallback_limit=2,
                            )
                if not value and self.llm_client and self.llm_client.available():
                    if progress:
                        progress(-1, f"LLM normalizing field {field.name}")
                    value, evidence_refs, confidence = self._llm_try_extract(
                        field,
                        evidence_bundle.hits if evidence_bundle else [],
                        parsed.document.relative_path,
                    )
            value = self._with_tu_field_context(field, value, matched_key)
        status = ExtractionStatus.FILLED if value else ExtractionStatus.BLANK_NO_EVIDENCE
        if not value:
            notes = "No conservative evidence found in OCR/native text."
        return ExtractedFieldResult(
            field_name=field.name,
            value=value,
            normalized_value=value,
            confidence=confidence,
            decision_confidence=confidence if value else None,
            status=status,
            evidence_refs=evidence_refs,
            notes=notes,
            evidence_bundle_id=evidence_bundle_id,
            uncertainty_reason=resolver_uncertainty or ("" if value else "no_supporting_evidence"),
            llm_verification_status=resolver_status or (
                "verified"
                if value and any(ref.evidence_type == "llm_normalization" for ref in evidence_refs)
                else ""
            ),
            rule_support=list(dict.fromkeys(resolver_rule_support)),
        )

    def _extract_component_groups(
        self, parsed: ParsedDocument, schema: SchemaFamily
    ) -> list[ExtractedRecord]:
        records: list[ExtractedRecord] = []
        for page in parsed.pages:
            structured = page.structured_diagram
            if structured is None:
                continue
            for group in structured.groups:
                values = {
                    "group_id": group.id,
                    "page_number": str(group.page_number),
                    "group_role": group.group_role,
                    "zone_path": group.zone_path,
                    "signal_tag": group.signal_tag,
                    "cabinet": group.cabinet or self._infer_cabinet(parsed.document.relative_path, group.signal_tag),
                    "bbox": self._bbox_value(group.bbox),
                    "part_ids": " | ".join(group.part_ids),
                    "raw_context": group.raw_context,
                }
                records.append(
                    ExtractedRecord(
                        family=DocumentFamily.STROMLAUF_COMPONENT_GROUP,
                        source_path=parsed.document.relative_path,
                        record_key=f"{parsed.document.relative_path}::{group.id}",
                        display_name=group.signal_tag or group.id,
                        results=self._results_from_values(
                            schema,
                            values,
                            group.evidence_refs,
                            default_confidence=0.88,
                        ),
                        notes="Derived from structured grouped diagram analysis.",
                    )
                )
        return records

    def _collect_key_values(self, parsed: ParsedDocument) -> dict[str, tuple[str, list[EvidenceRef], float, str]]:
        pattern = re.compile(r"^\s*([A-Za-zA-Z0-9/\- .]{2,40})\s*:\s*(.+)$")
        collected: dict[str, tuple[str, list[EvidenceRef], float, str]] = {}
        for page in parsed.pages:
            for table in page.tables:
                rows = self._group_table_rows(table)
                for row_id, row_cells in rows:
                    populated_cells = [(cell, clean_cell(cell.text)) for cell in row_cells]
                    populated_cells = [(cell, text) for cell, text in populated_cells if text]
                    if len(populated_cells) < 2:
                        continue
                    key_cell, key_text = populated_cells[0]
                    value_cells = populated_cells[1:]
                    key = normalize_label(key_text)
                    value = " | ".join(text for _, text in value_cells)
                    if not key or not value or key in collected:
                        continue
                    evidence = [
                        EvidenceRef(
                            source_path=parsed.document.relative_path,
                            page_or_sheet=f"Page {page.page_number}",
                            cell_range_or_bbox=f"{table.table_id}:r{row_id}c{cell.col_id}",
                            snippet=f"{key_text}: {text}"[:240],
                            score=max(0.6, cell.confidence),
                            evidence_type="table_cell",
                            engine=cell.engine,
                        )
                        for cell, text in value_cells
                    ]
                    confidence = max(0.6, min(cell.confidence for cell, _ in value_cells))
                    collected[key] = (value, evidence, confidence, key_text)
            for pair in page.kv_pairs:
                evidence = EvidenceRef(
                    source_path=parsed.document.relative_path,
                    page_or_sheet=f"Page {page.page_number}",
                    cell_range_or_bbox=str(pair.value_bbox or pair.key_bbox or ""),
                    snippet=f"{pair.key}: {pair.value}"[:240],
                    score=max(0.6, pair.confidence),
                    evidence_type="kv_pair",
                    engine=pair.engine,
                )
                collected[normalize_label(pair.key)] = (pair.value, [evidence], max(0.6, pair.confidence), pair.key)
            for block in page.blocks:
                match = pattern.match(block.text)
                if not match:
                    continue
                key = normalize_label(match.group(1))
                value = clean_cell(match.group(2))
                if not value or key in collected:
                    continue
                evidence = EvidenceRef(
                    source_path=parsed.document.relative_path,
                    page_or_sheet=f"Page {page.page_number}",
                    cell_range_or_bbox=str(block.bbox),
                    snippet=block.text[:240],
                    score=max(0.6, block.confidence),
                    evidence_type=block.source,
                    engine=block.engine,
                )
                collected[key] = (value, [evidence], max(0.6, block.confidence), match.group(1).strip())
        return collected

    def _group_table_rows(self, table) -> list[tuple[int, list]]:
        rows: dict[int, list] = defaultdict(list)
        for cell in sorted(table.cells, key=lambda item: (item.row_id, item.col_id)):
            rows[cell.row_id].append(cell)
        return sorted(rows.items())

    def _candidate_aliases(self, field: SchemaField) -> list[str]:
        aliases = [field.name, *field.aliases]
        normalized = []
        for alias in aliases:
            key = normalize_label(alias)
            if key and key not in normalized:
                normalized.append(key)
        return normalized

    def _try_structured_hit_match(self, candidates: list[str], hits) -> tuple[str, list[EvidenceRef], float, str]:
        patterns = [
            re.compile(rf"^\s*{re.escape(alias)}\s*[:\-]\s*(.+?)\s*$", flags=re.IGNORECASE)
            for alias in candidates
            if alias
        ]
        for hit in hits:
            metadata = hit.chunk.metadata
            kind = str(metadata.get("kind", metadata.get("source", "")))
            if kind == "kv_pair":
                key = normalize_label(str(metadata.get("key", "")))
                if key in candidates:
                    value = clean_cell(metadata.get("value", ""))
                    if value:
                        evidence = EvidenceRef(
                            source_path=hit.chunk.document_path,
                            page_or_sheet=str(hit.chunk.metadata.get("page", "")),
                            cell_range_or_bbox=hit.chunk.source_locator,
                            snippet=hit.chunk.text[:240],
                            score=hit.score,
                            evidence_type="kv_pair",
                            engine=str(hit.chunk.metadata.get("engine", "")),
                        )
                        return value, [evidence], min(0.85, max(0.4, hit.score)), str(metadata.get("key", ""))
            lines = [segment.strip() for segment in str(hit.chunk.text).splitlines() if segment.strip()]
            for line in lines:
                normalized_line = normalize_label(line)
                if not any(alias in normalized_line for alias in candidates):
                    continue
                for pattern in patterns:
                    match = pattern.match(line)
                    if not match:
                        continue
                    value = clean_cell(match.group(1))
                    if not value:
                        continue
                    evidence = EvidenceRef(
                        source_path=hit.chunk.document_path,
                        page_or_sheet=str(hit.chunk.metadata.get("page", hit.chunk.metadata.get("sheet_name", ""))),
                        cell_range_or_bbox=hit.chunk.source_locator,
                        snippet=line[:240],
                        score=hit.score,
                        evidence_type=str(hit.chunk.metadata.get("kind", hit.chunk.metadata.get("source", "ocr_text"))),
                        engine=str(hit.chunk.metadata.get("engine", "")),
                    )
                    return value, [evidence], min(0.75, max(0.4, hit.score)), line.split(":", 1)[0].strip()
        return "", [], 0.0, ""

    def _evidence_refs_from_bundle(
        self,
        evidence_bundle,
        *,
        support_ids: list[str] | None = None,
        fallback_limit: int = 2,
    ) -> list[EvidenceRef]:
        support_set = {str(item) for item in (support_ids or []) if str(item).strip()}
        selected_hits = [
            hit for hit in evidence_bundle.hits
            if not support_set or hit.chunk.id in support_set
        ]
        if not selected_hits:
            selected_hits = list(evidence_bundle.hits[:fallback_limit])
        refs: list[EvidenceRef] = []
        for hit in selected_hits[:fallback_limit]:
            metadata = hit.chunk.metadata
            refs.append(
                EvidenceRef(
                    source_path=hit.chunk.document_path,
                    page_or_sheet=str(metadata.get("page", metadata.get("sheet_name", "Retrieved evidence"))),
                    cell_range_or_bbox=hit.chunk.source_locator,
                    snippet=hit.chunk.text[:240],
                    score=hit.score,
                    evidence_type=str(metadata.get("kind", metadata.get("source", "retrieved_chunk"))),
                    engine=str(metadata.get("engine", "")),
                )
            )
        return refs

    def _with_tu_field_context(self, field: SchemaField, value: str, matched_key: str) -> str:
        if not value or not matched_key:
            return value
        cleaned_key = clean_cell(matched_key).lstrip(".")
        if not cleaned_key:
            return value
        match = TU_IDENTIFIER_PREFIX_PATTERN.match(cleaned_key)
        if not match or not looks_like_identifier(match.group("identifier")):
            return value
        key_label = clean_cell(match.group("label"))
        if normalize_label(key_label) != normalize_label(field.name):
            return value
        if value.startswith(cleaned_key):
            return value
        return f"{cleaned_key}, {value}"

    def _try_line_match(self, hits) -> tuple[str, list[EvidenceRef], float]:
        for hit in hits:
            evidence = EvidenceRef(
                source_path=hit.chunk.document_path,
                page_or_sheet=str(hit.chunk.metadata.get("page", hit.chunk.metadata.get("sheet_name", ""))),
                cell_range_or_bbox=hit.chunk.source_locator,
                snippet=hit.chunk.text[:240],
                score=hit.score,
                evidence_type=str(hit.chunk.metadata.get("kind", hit.chunk.metadata.get("source", "ocr_text"))),
                engine=str(hit.chunk.metadata.get("engine", "")),
            )
            return hit.chunk.text[:240], [evidence], min(0.75, max(0.3, hit.score))
        return "", [], 0.0

    def _llm_normalization_model(self) -> str:
        if self.llm_client is None:
            return ""
        return str(self.llm_client.config.chat_model or "").strip()

    def _llm_normalization_cache_key(
        self,
        *,
        field: SchemaField,
        hits,
        source_path: str,
    ) -> str:
        payload = {
            "prompt_version": LLM_NORMALIZATION_PROMPT_VERSION,
            "model": self._llm_normalization_model(),
            "field": field.name,
            "aliases": field.aliases,
            "source_path": source_path,
            "hits": [
                {
                    "chunk_id": hit.chunk.id,
                    "score": round(float(hit.score), 4),
                    "source_locator": hit.chunk.source_locator,
                    "text": hit.chunk.text[:400],
                }
                for hit in hits
            ],
        }
        digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return f"field_normalization:{digest}"

    def _llm_normalization_cache_path(self, cache_key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _load_llm_normalization_cache(self, cache_key: str) -> dict[str, Any] | None:
        cache_path = self._llm_normalization_cache_path(cache_key)
        if cache_path is None or not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _save_llm_normalization_cache(self, cache_key: str, payload: dict[str, Any]) -> None:
        cache_path = self._llm_normalization_cache_path(cache_key)
        if cache_path is None:
            return
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _llm_try_extract(
        self,
        field: SchemaField,
        hits,
        source_path: str,
    ) -> tuple[str, list[EvidenceRef], float]:
        evidence_text = "\n\n".join(
            f"[{index}] score={hit.score:.2f} locator={hit.chunk.source_locator}\n{hit.chunk.text[:400]}"
            for index, hit in enumerate(hits, start=1)
        )
        if not evidence_text:
            return "", [], 0.0
        system_prompt = (
            "You normalize a single field value from evidence. "
            "Never invent topology or missing identifiers. If the evidence is insufficient, return an empty value."
        )
        user_prompt = (
            f"Field name: {field.name}\n"
            f"Aliases: {', '.join(field.aliases)}\n"
            f"Return JSON with keys: value, confidence, reason.\n"
            f"If the value is not explicitly present in the evidence, set value to an empty string.\n\n"
            f"Evidence:\n{evidence_text}"
        )
        cache_key = self._llm_normalization_cache_key(
            field=field,
            hits=hits,
            source_path=source_path,
        )
        payload = self._load_llm_normalization_cache(cache_key)
        if payload is not None:
            self._log_debug(
                action="cache_hit",
                message=f"LLM normalization cache hit for {field.name}",
                details={
                    "workflow": "llm_field_normalization",
                    "field_name": field.name,
                    "source_path": source_path,
                    "hit_count": len(hits),
                    "model": self._llm_normalization_model(),
                    "prompt_version": LLM_NORMALIZATION_PROMPT_VERSION,
                    "cache_key": cache_key,
                    "output": payload,
                },
            )
        else:
            payload = {}
        try:
            if not payload:
                payload = self.llm_client.chat_json(
                    system_prompt,
                    user_prompt,
                    trace_context={
                        "workflow": "llm_field_normalization",
                        "field_name": field.name,
                        "source_path": source_path,
                        "hit_count": len(hits),
                        "model": self._llm_normalization_model(),
                        "prompt_version": LLM_NORMALIZATION_PROMPT_VERSION,
                        "cache_key": cache_key,
                    },
                )
                if payload:
                    self._save_llm_normalization_cache(cache_key, payload)
        except Exception:
            return "", [], 0.0
        value = clean_cell(payload.get("value", ""))
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if not value or confidence < 0.4:
            return "", [], 0.0
        evidence = EvidenceRef(
            source_path=source_path,
            page_or_sheet="Retrieved evidence",
            cell_range_or_bbox=hits[0].chunk.source_locator if hits else "",
            snippet=hits[0].chunk.text[:240] if hits else "",
            score=min(confidence, 1.0),
            evidence_type="llm_normalization",
            engine="llm",
        )
        return value, [evidence], min(confidence, 1.0)

    def _extract_components(
        self,
        parsed: ParsedDocument,
        schema: SchemaFamily,
        reference_tokens: set[str],
    ) -> list[ExtractedRecord]:
        structured_records = self._extract_structured_components(parsed, schema)
        if structured_records:
            return structured_records
        graph_records = self._extract_graph_components(parsed, schema, reference_tokens)
        if graph_records:
            return graph_records
        return self._extract_text_components(parsed, schema, reference_tokens)

    def _extract_structured_components(
        self,
        parsed: ParsedDocument,
        schema: SchemaFamily,
    ) -> list[ExtractedRecord]:
        records: list[ExtractedRecord] = []
        for page in parsed.pages:
            structured = page.structured_diagram
            if structured is None:
                continue
            for part in structured.parts:
                raw_context = " | ".join(
                    value for value in [part.display_label, part.logical_tag, part.unit] if value
                )
                values = {
                    "component_id": part.id,
                    "group_id": part.group_id,
                    "parent_component_id": part.parent_component_id,
                    "component_role": part.component_role,
                    "display_label": part.display_label,
                    "logical_tag": part.logical_tag,
                    "article": part.article,
                    "type_code": part.type_code,
                    "channel": part.channel,
                    "address": part.address,
                    "terminal_labels": " | ".join(part.terminal_labels),
                    "unit": part.unit,
                    "page_number": str(part.page_number),
                    "cabinet": self._infer_cabinet(parsed.document.relative_path, part.logical_tag or part.display_label),
                    "bbox": self._bbox_value(part.bbox),
                    "raw_context": raw_context,
                }
                confidence = 0.9 if part.component_role == "controller_module" else 0.84
                records.append(
                    ExtractedRecord(
                        family=DocumentFamily.STROMLAUF_COMPONENT,
                        source_path=parsed.document.relative_path,
                        record_key=f"{parsed.document.relative_path}::{part.id}",
                        display_name=part.display_label or part.id,
                        results=self._results_from_values(
                            schema,
                            values,
                            part.evidence_refs,
                            default_confidence=confidence,
                        ),
                        notes="Derived from structured grouped diagram parts.",
                    )
                )
        return records

    def _extract_graph_components(
        self,
        parsed: ParsedDocument,
        schema: SchemaFamily,
        reference_tokens: set[str],
    ) -> list[ExtractedRecord]:
        best_nodes = {}
        for page in parsed.pages:
            graph = page.diagram_graph
            if graph is None:
                continue
            for node in graph.nodes:
                existing = best_nodes.get(node.label)
                current_score = existing.evidence_refs[0].score if existing and existing.evidence_refs else 0.0
                candidate_score = node.evidence_refs[0].score if node.evidence_refs else 0.0
                if existing is None or candidate_score > current_score:
                    best_nodes[node.label] = node
        if not best_nodes:
            return []
        records: list[ExtractedRecord] = []
        for label, node in best_nodes.items():
            confidence = 0.7 if label in reference_tokens else 0.62
            values = {
                "component_id": label,
                "group_id": "",
                "parent_component_id": "",
                "component_role": node.node_type,
                "display_label": label,
                "logical_tag": label,
                "article": "",
                "type_code": "",
                "channel": "",
                "address": "",
                "terminal_labels": "",
                "unit": "",
                "page_number": str(node.page_number),
                "cabinet": self._infer_cabinet(parsed.document.relative_path, label),
                "bbox": self._bbox_value(node.bbox),
                "raw_context": node.evidence_refs[0].snippet if node.evidence_refs else label,
            }
            records.append(
                ExtractedRecord(
                    family=DocumentFamily.STROMLAUF_COMPONENT,
                    source_path=parsed.document.relative_path,
                    record_key=f"{parsed.document.relative_path}::{label}",
                    display_name=label,
                    results=self._results_from_values(
                        schema,
                        values,
                        node.evidence_refs[:1],
                        default_confidence=confidence,
                    ),
                    notes="Derived from OCR-backed diagram graph nodes.",
                )
            )
        return records

    def _extract_text_components(
        self,
        parsed: ParsedDocument,
        schema: SchemaFamily,
        reference_tokens: set[str],
    ) -> list[ExtractedRecord]:
        seen: dict[str, EvidenceRef] = {}
        contexts: dict[str, str] = {}
        pages: dict[str, int] = {}
        for page in parsed.pages:
            for block in page.blocks:
                for token in extract_component_tokens(block.text):
                    if token not in seen:
                        seen[token] = EvidenceRef(
                            source_path=parsed.document.relative_path,
                            page_or_sheet=f"Page {page.page_number}",
                            cell_range_or_bbox=str(block.bbox),
                            snippet=block.text[:240],
                            score=max(0.5, block.confidence),
                            evidence_type=block.source,
                            engine=block.engine,
                        )
                        contexts[token] = block.text[:240]
                        pages[token] = page.page_number
        records: list[ExtractedRecord] = []
        for token, evidence in seen.items():
            component_type = self._component_type(token)
            confidence = 0.65 if token in reference_tokens else 0.55
            values = {
                "component_id": token,
                "group_id": "",
                "parent_component_id": "",
                "component_role": component_type,
                "display_label": token,
                "logical_tag": token,
                "article": "",
                "type_code": "",
                "channel": "",
                "address": "",
                "terminal_labels": "",
                "unit": "",
                "page_number": str(pages[token]),
                "cabinet": self._infer_cabinet(parsed.document.relative_path, token),
                "bbox": "",
                "raw_context": contexts[token],
            }
            records.append(
                ExtractedRecord(
                    family=DocumentFamily.STROMLAUF_COMPONENT,
                    source_path=parsed.document.relative_path,
                    record_key=f"{parsed.document.relative_path}::{token}",
                    display_name=token,
                    results=self._results_from_values(
                        schema,
                        values,
                        [evidence],
                        default_confidence=confidence,
                    ),
                    notes="Derived from explicit component-like tokens in drawing text.",
                )
            )
        return records

    def _extract_structured_connections(
        self, parsed: ParsedDocument, schema: SchemaFamily
    ) -> list[ExtractedRecord]:
        records: list[ExtractedRecord] = []
        for page in parsed.pages:
            structured = page.structured_diagram
            if structured is None:
                continue
            part_lookup = {part.id: part for part in structured.parts}
            for trace in structured.traces:
                from_part = part_lookup.get(trace.from_component_id)
                to_part = part_lookup.get(trace.to_component_id)
                via_part = part_lookup.get(trace.via_component_id) if trace.via_component_id else None
                display_name = " -> ".join(
                    value
                    for value in [
                        from_part.display_label if from_part is not None else trace.from_component_id,
                        via_part.display_label if via_part is not None else "",
                        to_part.display_label if to_part is not None else trace.to_component_id,
                    ]
                    if value
                )
                values = {
                    "connection_id": trace.id,
                    "group_id": trace.group_id,
                    "from_component_id": trace.from_component_id,
                    "from_terminal": trace.from_terminal,
                    "via_component_id": trace.via_component_id,
                    "via_terminal": trace.via_terminal,
                    "to_component_id": trace.to_component_id,
                    "to_terminal": trace.to_terminal,
                    "wire_label": trace.wire_label,
                    "page_number": str(trace.page_number),
                    "trace_path": self._polyline_value(trace.trace_path),
                    "confidence": f"{trace.confidence:.2f}",
                    "raw_context": display_name or trace.id,
                }
                status = (
                    ExtractionStatus.FILLED
                    if trace.confidence >= 0.75
                    else ExtractionStatus.NEEDS_REVIEW
                )
                results = []
                for field in schema.fields:
                    value = values.get(field.name, "")
                    results.append(
                        ExtractedFieldResult(
                            field_name=field.name,
                            value=value,
                            normalized_value=value,
                            confidence=trace.confidence if value else 0.0,
                            status=status if value else ExtractionStatus.BLANK_NO_EVIDENCE,
                            evidence_refs=trace.evidence_refs[:2] if value else [],
                            notes="" if value else "No structured trace evidence for this field.",
                        )
                    )
                records.append(
                    ExtractedRecord(
                        family=DocumentFamily.STROMLAUF_CONNECTION,
                        source_path=parsed.document.relative_path,
                        record_key=f"{parsed.document.relative_path}::{trace.id}",
                        display_name=display_name or trace.id,
                        results=results,
                        notes="Derived from structured grouped wire traces.",
                    )
                )
        return records

    def _results_from_values(
        self,
        schema: SchemaFamily,
        values: dict[str, str],
        evidences: list[EvidenceRef],
        *,
        default_confidence: float,
    ) -> list[ExtractedFieldResult]:
        results: list[ExtractedFieldResult] = []
        for field in schema.fields:
            value = values.get(field.name, "")
            status = ExtractionStatus.FILLED if value else ExtractionStatus.BLANK_NO_EVIDENCE
            results.append(
                ExtractedFieldResult(
                    field_name=field.name,
                    value=value,
                    normalized_value=value,
                    confidence=default_confidence if value else 0.0,
                    status=status,
                    evidence_refs=evidences[:2] if value else [],
                    notes="" if value else "No structured evidence for this field.",
                )
            )
        return results

    def _bbox_value(self, bbox: tuple[float, float, float, float] | None) -> str:
        if bbox is None:
            return ""
        return ",".join(f"{value:.1f}" for value in bbox)

    def _polyline_value(self, polyline: list[tuple[float, float]]) -> str:
        if not polyline:
            return ""
        return " -> ".join(f"({x:.1f},{y:.1f})" for x, y in polyline)

    def _component_type(self, token: str) -> str:
        upper = token.upper()
        if upper.startswith("X"):
            return "terminal"
        if upper.startswith("PXC") or upper.startswith("IO"):
            return "module"
        if looks_like_identifier(token):
            return "instrument"
        return "component"

    def _infer_cabinet(self, relative_path: str, token: str) -> str:
        upper = relative_path.upper()
        for marker in ("HC10", "HC20", "HC30", "HC40"):
            if marker in upper or marker in token.upper():
                return marker
        return ""

    def _extract_ri_nodes(self, parsed: ParsedDocument, schema: SchemaFamily) -> list[ExtractedRecord]:
        package = parsed.ri_package
        if package is None:
            return []
        category = self._ri_category_for_family(schema.family)
        records: list[ExtractedRecord] = []
        for node in package.xml_nodes:
            if node.category != category:
                continue
            evidences = list(node.source_refs)
            evidences.extend(self._ri_pdf_evidence_for_tag(parsed, node.tag_name))
            results: list[ExtractedFieldResult] = []
            for field in schema.fields:
                value, status, notes = self._ri_node_field_value(field.name, node, evidences)
                confidence = 1.0 if value and field.name in {"tag_name", "node_id", "class_name", "sub_class"} else 0.92
                if field.name == "evidence_summary" and value:
                    confidence = 0.88
                if field.name == "source_locator" and value:
                    confidence = 1.0
                if not value:
                    confidence = 0.0
                results.append(
                    ExtractedFieldResult(
                        field_name=field.name,
                        value=value,
                        normalized_value=value,
                        confidence=confidence,
                        status=status,
                        evidence_refs=evidences[:2] if value else [],
                        notes=notes,
                    )
                )
            if not any(result.value for result in results):
                continue
            records.append(
                ExtractedRecord(
                    family=schema.family,
                    source_path=parsed.document.relative_path,
                    record_key=f"{parsed.document.bundle_id or parsed.document.relative_path}::{node.node_id}",
                    display_name=node.tag_name or node.node_id,
                    results=results,
                    notes="R&I record derived from DEXPI XML with PDF evidence assistance.",
                    source_root=parsed.document.source_root,
                    scope_id=parsed.document.bundle_id or "",
                )
            )
        return records

    def _extract_ri_instrument_instances(self, parsed: ParsedDocument, schema: SchemaFamily) -> list[ExtractedRecord]:
        package = parsed.ri_package
        if package is None:
            return []
        records: list[ExtractedRecord] = []
        for instance in package.instrument_instances:
            evidences = list(instance.evidence_refs)
            evidences.extend(self._ri_pdf_evidence_for_tag(parsed, instance.canonical_tag))
            results: list[ExtractedFieldResult] = []
            for field in schema.fields:
                value, status, notes = self._ri_instrument_field_value(field.name, instance, evidences)
                confidence = 0.95 if value else 0.0
                if field.name in {"canonical_tag", "function_code", "loop_node_id", "function_node_id"} and value:
                    confidence = 1.0
                if field.name in {"context_summary", "evidence_summary"} and value:
                    confidence = 0.88
                results.append(
                    ExtractedFieldResult(
                        field_name=field.name,
                        value=value,
                        normalized_value=value,
                        confidence=confidence,
                        status=status,
                        evidence_refs=evidences[:3] if value else [],
                        notes=notes,
                    )
                )
            if not any(result.value for result in results):
                continue
            records.append(
                ExtractedRecord(
                    family=schema.family,
                    source_path=parsed.document.relative_path,
                    record_key=(
                        f"{parsed.document.bundle_id or parsed.document.relative_path}"
                        f"::instrument::{instance.loop_node_id or instance.function_node_id}"
                    ),
                    display_name=instance.label_text or instance.canonical_tag,
                    results=results,
                    notes="R&I instrument instance derived from InstrumentationLoopFunction plus DEXPI topology.",
                    source_root=parsed.document.source_root,
                    scope_id=parsed.document.bundle_id or "",
                )
            )
        return records

    def _extract_ri_connections(self, parsed: ParsedDocument, schema: SchemaFamily) -> list[ExtractedRecord]:
        package = parsed.ri_package
        if package is None:
            return []
        records: list[ExtractedRecord] = []
        node_lookup = {node.node_id: node for node in package.xml_nodes}
        if package.xml_edges:
            for edge in package.xml_edges:
                evidences = list(edge.source_refs)
                source_node = node_lookup.get(edge.from_id)
                target_node = node_lookup.get(edge.to_id)
                if source_node:
                    evidences.extend(self._ri_pdf_evidence_for_tag(parsed, source_node.tag_name))
                if target_node:
                    evidences.extend(self._ri_pdf_evidence_for_tag(parsed, target_node.tag_name))
                results: list[ExtractedFieldResult] = []
                for field in schema.fields:
                    value, status, notes = self._ri_connection_field_value(field.name, edge, source_node, target_node, evidences)
                    confidence = 1.0 if value and field.name in {"from_id", "to_id", "edge_type"} else 0.9
                    if field.name == "evidence_summary" and value:
                        confidence = 0.85
                    if not value:
                        confidence = 0.0
                    results.append(
                        ExtractedFieldResult(
                            field_name=field.name,
                            value=value,
                            normalized_value=value,
                            confidence=confidence,
                            status=status,
                            evidence_refs=evidences[:3] if value else [],
                            notes=notes,
                        )
                    )
                records.append(
                    ExtractedRecord(
                        family=schema.family,
                        source_path=parsed.document.relative_path,
                        record_key=f"{parsed.document.bundle_id or parsed.document.relative_path}::{edge.edge_id}",
                        display_name=f"{edge.from_id} -> {edge.to_id}",
                        results=results,
                        notes="R&I connection derived from DEXPI topology.",
                        source_root=parsed.document.source_root,
                        scope_id=parsed.document.bundle_id or "",
                    )
                )
            return records

        for page in parsed.pages:
            graph = page.diagram_graph
            if graph is None:
                continue
            for edge in graph.edges:
                evidences = edge.evidence_refs[:2]
                results: list[ExtractedFieldResult] = []
                for field in schema.fields:
                    if field.name == "from_id":
                        value = edge.from_node
                    elif field.name == "to_id":
                        value = edge.to_node
                    elif field.name == "edge_type":
                        value = edge.edge_type
                    elif field.name == "class_name":
                        value = edge.edge_type
                    elif field.name == "sub_class":
                        value = ""
                    elif field.name == "source_locator":
                        value = f"Page {page.page_number}"
                    elif field.name == "evidence_summary":
                        value = evidences[0].snippet if evidences else ""
                    else:
                        value = ""
                    status = ExtractionStatus.NEEDS_REVIEW if value else ExtractionStatus.BLANK_NO_EVIDENCE
                    notes = "Derived from PDF graph fallback without explicit DEXPI edge." if value else "No PDF fallback evidence."
                    results.append(
                        ExtractedFieldResult(
                            field_name=field.name,
                            value=value,
                            normalized_value=value,
                            confidence=min(edge.confidence, 0.75) if value else 0.0,
                            status=status,
                            evidence_refs=evidences if value else [],
                            notes=notes,
                        )
                    )
                records.append(
                    ExtractedRecord(
                        family=schema.family,
                        source_path=parsed.document.relative_path,
                        record_key=f"{parsed.document.bundle_id or parsed.document.relative_path}::pdf::{edge.id}",
                        display_name=f"{edge.from_node} -> {edge.to_node}",
                        results=results,
                        notes="R&I connection inferred from PDF diagram fallback.",
                        source_root=parsed.document.source_root,
                        scope_id=parsed.document.bundle_id or "",
                        cross_validation_warnings=["PDF-derived fallback edge without explicit DEXPI topology."],
                    )
                )
        return records

    def _ri_pdf_evidence_for_tag(self, parsed: ParsedDocument, tag_name: str) -> list[EvidenceRef]:
        if not tag_name:
            return []
        target = normalize_label(tag_name)
        evidences: list[EvidenceRef] = []
        for page in parsed.pages:
            for block in page.blocks:
                if target and target in normalize_label(block.text):
                    evidences.append(
                        EvidenceRef(
                            source_path=parsed.document.relative_path,
                            page_or_sheet=f"Page {page.page_number}",
                            cell_range_or_bbox=str(block.bbox),
                            snippet=block.text[:240],
                            score=max(0.5, block.confidence),
                            evidence_type=block.source,
                            engine=block.engine,
                        )
                    )
                    if len(evidences) >= 2:
                        return evidences
        return evidences

    # Mapping from schema field names to fallback attribute names on the node
    # object (when getattr(node, field_name) doesn't match).
    _RI_NODE_ATTR_ALIASES: dict[str, str] = {
        "source_locator": "locator",
    }

    def _ri_node_field_value(
        self,
        field_name: str,
        node,
        evidences: list[EvidenceRef],
    ) -> tuple[str, ExtractionStatus, str]:
        # Special composite fields
        if field_name == "evidence_summary":
            value = " | ".join(evidence.snippet for evidence in evidences[:2])
        else:
            # Try direct attribute access (handles tag_name, node_id,
            # class_name, sub_class, normalized_type and any future fields).
            attr_name = self._RI_NODE_ATTR_ALIASES.get(field_name, field_name)
            value = getattr(node, attr_name, None)
            if value is None:
                value = clean_cell(node.attributes.get(field_name, ""))
        if value:
            return value, ExtractionStatus.FILLED, ""
        return "", ExtractionStatus.BLANK_NO_EVIDENCE, "No explicit XML/PDF evidence for this field."

    # Mapping from schema field names to edge attribute names (when they differ).
    _RI_EDGE_ATTR_ALIASES: dict[str, str] = {
        "source_locator": "locator",
    }

    def _ri_connection_field_value(
        self,
        field_name: str,
        edge,
        source_node,
        target_node,
        evidences: list[EvidenceRef],
    ) -> tuple[str, ExtractionStatus, str]:
        # Special composite fields
        if field_name == "evidence_summary":
            source_label = source_node.tag_name if source_node is not None else edge.from_id
            target_label = target_node.tag_name if target_node is not None else edge.to_id
            value = f"{source_label} -> {target_label}"
        else:
            # Direct attribute access via getattr (handles from_id, to_id,
            # edge_type, class_name, sub_class and any future fields).
            attr_name = self._RI_EDGE_ATTR_ALIASES.get(field_name, field_name)
            value = getattr(edge, attr_name, None)
            if value is None:
                value = clean_cell(edge.attributes.get(field_name, ""))
        if value:
            return value, ExtractionStatus.FILLED, ""
        return "", ExtractionStatus.BLANK_NO_EVIDENCE, "No explicit XML evidence for this connection field."

    # Explicit field_name → value overrides for instrument function instances.
    # Keys are schema field names; values are either a direct string value or
    # a callable ``(instance, evidences) -> str``.
    _RI_INSTRUMENT_FIELD_MAP: dict[str, object] = {
        "canonical_tag": lambda i, _e: i.canonical_tag,
        "function_code": lambda i, _e: i.function_code,
        "label_text": lambda i, _e: i.label_text,
        "loop_node_id": lambda i, _e: i.loop_node_id,
        "function_node_id": lambda i, _e: i.function_node_id,
        "tag_name": lambda i, _e: i.canonical_tag,
        "node_id": lambda i, _e: i.function_node_id,
        "class_name": lambda _i, _e: "ProcessInstrumentationFunction",
        "sub_class": lambda _i, _e: "",
        "normalized_type": lambda _i, _e: "instrument_function",
        "description": lambda i, _e: i.description,
        "name": lambda i, _e: i.canonical_tag,
        "piping_anchor_id": lambda i, _e: i.piping_anchor_id,
        "from_equipment": lambda i, _e: i.from_equipment,
        "to_equipment": lambda i, _e: i.to_equipment,
        "context_summary": lambda i, _e: i.context_summary,
        "label": lambda i, _e: i.full_label or i.label_text,
        "source_locator": lambda i, _e: " / ".join(
            locator for locator in [i.loop_node_id, i.function_node_id, i.piping_anchor_id] if locator
        ),
    }

    def _ri_instrument_field_value(
        self,
        field_name: str,
        instance,
        evidences: list[EvidenceRef],
    ) -> tuple[str, ExtractionStatus, str]:
        # Check explicit mapping first
        handler = self._RI_INSTRUMENT_FIELD_MAP.get(field_name)
        if handler is not None:
            if callable(handler):
                value = clean_cell(str(handler(instance, evidences)))
            else:
                value = clean_cell(str(handler))
        elif field_name == "evidence_summary":
            value = " | ".join(evidence.snippet for evidence in evidences[:2])
        else:
            # Fallback: try getattr, then attributes dict
            value = getattr(instance, field_name, None)
            if value is None:
                value = clean_cell(str(getattr(instance, "attributes", {}).get(field_name, "")))
            value = clean_cell(str(value) if value else "")
        if value:
            return value, ExtractionStatus.FILLED, ""
        return "", ExtractionStatus.BLANK_NO_EVIDENCE, "No explicit loop/function evidence for this instrument field."

    def _extract_ifc_nodes(self, parsed: ParsedDocument, schema: SchemaFamily) -> list[ExtractedRecord]:
        package = parsed.ifc_package
        if package is None:
            return []
        records: list[ExtractedRecord] = []
        for node in package.ifc_nodes:
            evidences = list(node.source_refs)
            results: list[ExtractedFieldResult] = []
            for field in schema.fields:
                value, status, notes = self._ifc_node_field_value(field.name, node, evidences)
                confidence = 0.95 if value else 0.0
                if field.name in {"node_id", "ifc_class"} and value:
                    confidence = 1.0
                results.append(
                    ExtractedFieldResult(
                        field_name=field.name,
                        value=value,
                        normalized_value=value,
                        confidence=confidence,
                        status=status,
                        evidence_refs=evidences[:2] if value else [],
                        notes=notes,
                    )
                )
            if not any(result.value for result in results):
                continue
            records.append(
                ExtractedRecord(
                    family=schema.family,
                    source_path=parsed.document.relative_path,
                    record_key=f"{parsed.document.relative_path}::ifc::{node.node_id}",
                    display_name=node.tag or node.name or node.node_id,
                    results=results,
                    notes="IFC piping item derived from attribute-level IFC parsing.",
                    source_root=parsed.document.source_root,
                )
            )
        return records

    def _extract_ifc_edges(self, parsed: ParsedDocument, schema: SchemaFamily) -> list[ExtractedRecord]:
        package = parsed.ifc_package
        if package is None:
            return []
        records: list[ExtractedRecord] = []
        for edge in package.ifc_edges:
            evidences = list(edge.source_refs)
            results: list[ExtractedFieldResult] = []
            for field in schema.fields:
                value, status, notes = self._ifc_edge_field_value(field.name, edge, evidences)
                confidence = 0.95 if value else 0.0
                if field.name in {"from_id", "to_id", "relation_type"} and value:
                    confidence = 1.0
                results.append(
                    ExtractedFieldResult(
                        field_name=field.name,
                        value=value,
                        normalized_value=value,
                        confidence=confidence,
                        status=status,
                        evidence_refs=evidences[:2] if value else [],
                        notes=notes,
                    )
                )
            if not any(result.value for result in results):
                continue
            records.append(
                ExtractedRecord(
                    family=schema.family,
                    source_path=parsed.document.relative_path,
                    record_key=f"{parsed.document.relative_path}::ifc_edge::{edge.edge_id}",
                    display_name=f"{edge.from_id} -> {edge.to_id}",
                    results=results,
                    notes="IFC connection derived from explicit IFC relations.",
                    source_root=parsed.document.source_root,
                )
            )
        return records

    # Attribute name aliases for IFC node/edge objects (schema field → attr).
    _IFC_NODE_ATTR_ALIASES: dict[str, str] = {
        "source_locator": "locator",
    }
    _IFC_EDGE_ATTR_ALIASES: dict[str, str] = {
        "source_locator": "locator",
    }

    def _ifc_node_field_value(
        self,
        field_name: str,
        node,
        evidences: list[EvidenceRef],
    ) -> tuple[str, ExtractionStatus, str]:
        if field_name == "evidence_summary":
            value = " | ".join(evidence.snippet for evidence in evidences[:2])
        elif field_name == "match_keys":
            value = " | ".join(node.match_keys) if hasattr(node, "match_keys") else ""
        elif field_name == "flange_complete":
            fc = getattr(node, "flange_complete", None)
            value = "" if fc is None else ("true" if fc else "false")
        else:
            attr_name = self._IFC_NODE_ATTR_ALIASES.get(field_name, field_name)
            value = getattr(node, attr_name, None)
            if value is None:
                value = clean_cell(node.attributes.get(field_name, ""))
        if value:
            return value, ExtractionStatus.FILLED, ""
        return "", ExtractionStatus.BLANK_NO_EVIDENCE, "No explicit IFC evidence for this field."

    def _ifc_edge_field_value(
        self,
        field_name: str,
        edge,
        evidences: list[EvidenceRef],
    ) -> tuple[str, ExtractionStatus, str]:
        if field_name == "evidence_summary":
            value = " | ".join(evidence.snippet for evidence in evidences[:2])
        else:
            attr_name = self._IFC_EDGE_ATTR_ALIASES.get(field_name, field_name)
            value = getattr(edge, attr_name, None)
            if value is None:
                value = clean_cell(edge.attributes.get(field_name, ""))
        if value:
            return value, ExtractionStatus.FILLED, ""
        return "", ExtractionStatus.BLANK_NO_EVIDENCE, "No explicit IFC evidence for this field."

    def _ri_category_for_family(self, family: DocumentFamily) -> str:
        mapping = {
            DocumentFamily.RI_EQUIPMENT_ROW: "equipment",
            DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW: "instrument_function",
            DocumentFamily.RI_PIPING_COMPONENT_ROW: "piping_component",
        }
        return mapping.get(family, "equipment")
