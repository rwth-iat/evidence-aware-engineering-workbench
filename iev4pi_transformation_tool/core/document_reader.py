from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import fitz
except ImportError:
    try:  # pragma: no cover - compatibility alias
        import pymupdf as fitz
    except ImportError:
        fitz = None
import numpy as np
import pandas as pd

from iev4pi_transformation_tool.core.diagram_analyzer import DiagramAnalyzer
from iev4pi_transformation_tool.core.ifc_analyzer import IfcPackageAnalyzer
from iev4pi_transformation_tool.core.vlm_diagram_analyzer import HybridDiagramAnalyzer
from iev4pi_transformation_tool.core.ocr_defaults import get_ocr_platform_defaults
from iev4pi_transformation_tool.core.ocr_backends import (
    AppleOCRBackend,
    EasyOCRBackend,
    OCRBackendResult,
    PaddleOCRBackend,
    RapidOCRBackend,
    SuryaBackend,
    extract_key_value_pairs,
)
from iev4pi_transformation_tool.core.ocr_ensemble import ensemble_results
from iev4pi_transformation_tool.core.utils import (
    cell_coordinate,
    clean_cell,
    detect_header_rows,
    ensure_dir,
    normalize_label,
    extract_component_tokens,
)
from iev4pi_transformation_tool.models import (
    CellData,
    DocumentDescriptor,
    LayoutBlock,
    LLMBackendConfig,
    PageData,
    ParsedDocument,
    SheetData,
    SourceDocumentKind,
    TextBlock,
    DocumentFamily,
)

_PREPARED_PDF_BATCH_MAX_PAGES = 4
_PREPARED_PDF_BATCH_MEMORY_LIMIT_BYTES = 192 * 1024 * 1024


@dataclass
class _PreparedPdfPage:
    document: DocumentDescriptor
    page_number: int
    page_total: int
    native_blocks: list[TextBlock]
    scaled_native_blocks: list[TextBlock]
    scaled_vector_segments: list[tuple[float, float, float, float]]
    image: np.ndarray
    analysis_flags: list[str]
    diagram_like: bool
    rendered_dpi: int
    cache_path: Path | None = None
    backend_results: dict[str, OCRBackendResult] = field(default_factory=dict)
    page_data: PageData | None = None


@dataclass
class _PreparedPdfDocument:
    document: DocumentDescriptor
    pages: list[PageData | None]


class DocumentReader:
    def __init__(
        self,
        *,
        ocr_enabled: bool = True,
        ocr_zoom: float = 2.0,
        ocr_backend: str | None = None,
        ocr_fallback_backend: str | None = None,
        ocr_device: str | None = None,
        apple_ocr_framework: str = "vision",
        apple_ocr_recognition_level: str = "accurate",
        ocr_dpi: int = 300,
        diagram_dpi: int = 400,
        diagram_analysis_mode: str = "hybrid",
        diagram_extraction_backend: str = "heuristic",
        ocr_min_confidence: float = 0.82,
        ocr_pipeline_mode: str = "fallback",
        ocr_ensemble_backends: list[str] | None = None,
        enable_diagram_relation_extraction: bool = True,
        enable_hard_page_fallback: bool = True,
        cache_dir: Path | None = None,
        llm_config: LLMBackendConfig | None = None,
        logger: Callable[..., Any] | None = None,
    ) -> None:
        platform_defaults = get_ocr_platform_defaults()
        self.ocr_enabled = ocr_enabled
        self.ocr_zoom = ocr_zoom
        self.ocr_backend = ocr_backend or platform_defaults.ocr_backend
        self.ocr_fallback_backend = ocr_fallback_backend or platform_defaults.ocr_fallback_backend
        self.ocr_device = ocr_device or platform_defaults.ocr_device
        self.apple_ocr_framework = apple_ocr_framework
        self.apple_ocr_recognition_level = apple_ocr_recognition_level
        self.ocr_dpi = ocr_dpi
        self.diagram_dpi = diagram_dpi
        self.diagram_analysis_mode = (
            diagram_analysis_mode
            if diagram_analysis_mode in {"hybrid", "vector_only", "raster_only"}
            else "hybrid"
        )
        self.ocr_min_confidence = ocr_min_confidence
        self.ocr_pipeline_mode = (
            ocr_pipeline_mode
            if ocr_pipeline_mode in {"fallback", "ensemble"}
            else "fallback"
        )
        self.ocr_ensemble_backends = set(ocr_ensemble_backends) if ocr_ensemble_backends else {"apple", "paddle", "surya", "rapidocr"}
        self.enable_diagram_relation_extraction = enable_diagram_relation_extraction
        self.enable_hard_page_fallback = enable_hard_page_fallback
        self.cache_dir = ensure_dir(cache_dir) if cache_dir is not None else None
        self._logger = logger
        self._backends = {
            "apple": AppleOCRBackend(
                device="cpu",
                framework=apple_ocr_framework,
                recognition_level=apple_ocr_recognition_level,
            ),
            "easyocr": EasyOCRBackend(device="auto"),
            "paddle": PaddleOCRBackend(device=self.ocr_device),
            "surya": SuryaBackend(device=self.ocr_device),
            "rapidocr": RapidOCRBackend(device="cpu"),
        }
        self._diagram_analyzer = DiagramAnalyzer()
        self.diagram_extraction_backend = (
            diagram_extraction_backend
            if diagram_extraction_backend in {"heuristic", "vlm"}
            else "heuristic"
        )
        self._llm_config = llm_config or LLMBackendConfig()
        self._vlm_diagram_analyzer: HybridDiagramAnalyzer | None = None
        if self.diagram_extraction_backend == "vlm":
            vlm_cache_dir = self.cache_dir.parent / "vlm_verifier" if self.cache_dir is not None else None
            self._vlm_diagram_analyzer = HybridDiagramAnalyzer(
                self._llm_config,
                self._diagram_analyzer,
                cache_dir=vlm_cache_dir,
                logger=self._logger,
            )
        self._task_ocr_result_cache: dict[tuple[str, str, int, str], OCRBackendResult] = {}
        self._backend_execution_order = {
            "apple": 0,
            "easyocr": 1,
            "paddle": 2,
            "surya": 3,
            "rapidocr": 4,
        }
        self._ifc_analyzer = IfcPackageAnalyzer()

    def read(self, document: DocumentDescriptor, progress: ProgressCallback | None = None) -> ParsedDocument:
        if document.extension in {".xlsx", ".xls"}:
            return self._read_spreadsheet(document)
        if document.extension == ".pdf":
            return self.read_many([document], progress)[document.relative_path]
        if document.extension == ".ifc":
            return self._read_ifc(document)
        raise ValueError(f"Unsupported document extension: {document.extension}")

    def read_many(
        self,
        documents: list[DocumentDescriptor],
        progress: ProgressCallback | None = None,
    ) -> dict[str, ParsedDocument]:
        parsed: dict[str, ParsedDocument] = {}
        pdf_documents: list[DocumentDescriptor] = []
        for document in documents:
            if document.extension in {".xlsx", ".xls"}:
                parsed[document.relative_path] = self._read_spreadsheet(document)
            elif document.extension == ".pdf":
                pdf_documents.append(document)
            elif document.extension == ".ifc":
                parsed[document.relative_path] = self._read_ifc(document)
            else:
                raise ValueError(f"Unsupported document extension: {document.extension}")
        if pdf_documents and fitz is None:
            raise RuntimeError(
                "PyMuPDF is not available in the active Python environment. "
                "Install 'PyMuPDF' to enable PDF parsing and extraction."
            )
        if pdf_documents:
            parsed.update(self._read_pdf_batch(pdf_documents, progress))
        return parsed

    def describe_runtime(self) -> dict[str, object]:
        easyocr_available = self._backend_available("easyocr")
        easyocr_details = EasyOCRBackend.availability_details()
        uses_apple_backend = any(
            name == "apple" for name in (self.ocr_backend, self.ocr_fallback_backend)
        )
        uses_easyocr_backend = any(
            name == "easyocr" for name in (self.ocr_backend, self.ocr_fallback_backend)
        )
        gpu_requested = self.ocr_device.lower().startswith("cuda")
        gpu_available = self._gpu_available() if gpu_requested else False
        if uses_apple_backend and self._backend_available("apple"):
            active_device = "apple-vision"
        elif uses_easyocr_backend and easyocr_available:
            active_device = self._easyocr_device()
        else:
            active_device = (
                self.ocr_device if gpu_requested and gpu_available else "cpu"
            )
        return {
            "primary_backend": self.ocr_backend,
            "primary_available": self._backend_available(self.ocr_backend),
            "fallback_backend": self.ocr_fallback_backend,
            "fallback_available": self._backend_available(self.ocr_fallback_backend),
            "apple_available": self._backend_available("apple"),
            "paddle_available": self._backend_available("paddle"),
            "surya_available": self._backend_available("surya"),
            "apple_framework": self.apple_ocr_framework,
            "apple_recognition_level": self.apple_ocr_recognition_level,
            "rapidocr_available": self._backend_available("rapidocr"),
            "easyocr_available": easyocr_available,
            "easyocr_device": self._easyocr_device() if easyocr_available else "n/a",
            "easyocr_details": easyocr_details,
            "gpu_requested": gpu_requested,
            "gpu_available": gpu_available,
            "active_device": active_device,
            "diagram_analysis_mode": self.diagram_analysis_mode,
        }

    def _read_spreadsheet(self, document: DocumentDescriptor) -> ParsedDocument:
        sheets: list[SheetData] = []
        with pd.ExcelFile(document.path, engine="calamine") as workbook:
            for sheet_name in workbook.sheet_names:
                frame = workbook.parse(sheet_name, header=None, dtype=object)
                frame = frame.fillna("")
                rows = [
                    [clean_cell(value) for value in row]
                    for row in frame.astype(str).replace({"nan": ""}).values.tolist()
                ]
                cells: list[CellData] = []
                for row_index, row in enumerate(rows, start=1):
                    for col_index, value in enumerate(row, start=1):
                        if not value:
                            continue
                        cells.append(
                            CellData(
                                sheet_name=sheet_name,
                                row=row_index,
                                column=col_index,
                                coord=cell_coordinate(row_index, col_index),
                                value=value,
                            )
                        )
                sheets.append(
                    SheetData(
                        name=sheet_name,
                        rows=rows,
                        cells=cells,
                        header_rows=detect_header_rows(rows),
                    )
                )
        return ParsedDocument(
            document=document,
            sheets=sheets,
            metadata={"sheet_count": len(sheets)},
        )

    def _read_ifc(self, document: DocumentDescriptor) -> ParsedDocument:
        ifc_package = self._ifc_analyzer.analyze(document)
        return ParsedDocument(
            document=document,
            metadata={
                "ifc_node_count": len(ifc_package.ifc_nodes),
                "ifc_edge_count": len(ifc_package.ifc_edges),
                "ifc_validation_errors": ifc_package.validation_errors,
            },
            ifc_package=ifc_package,
        )

    def _read_pdf(self, document: DocumentDescriptor, progress: ProgressCallback | None = None) -> ParsedDocument:
        return self._read_pdf_batch([document], progress)[document.relative_path]

    def _read_pdf_batch(
        self,
        documents: list[DocumentDescriptor],
        progress: ProgressCallback | None = None,
    ) -> dict[str, ParsedDocument]:
        self._task_ocr_result_cache = {}
        doc_total = max(1, len(documents))
        page_counts: dict[str, int] = {}
        total_pages_expected = 0
        for document in documents:
            pdf = fitz.open(document.path)
            try:
                page_count = pdf.page_count
            finally:
                pdf.close()
            page_counts[document.relative_path] = page_count
            total_pages_expected += page_count
        doc_positions = {
            document.relative_path: (index, doc_total, document.relative_path.split("/")[-1])
            for index, document in enumerate(documents, start=1)
        }
        last_reported_progress = 0

        def emit_progress(value: int, prepared: _PreparedPdfPage | None, message: str) -> None:
            nonlocal last_reported_progress
            if progress is None:
                return
            bounded_value = last_reported_progress if value < 0 else max(0, min(100, int(value)))
            if bounded_value < last_reported_progress:
                bounded_value = last_reported_progress
            last_reported_progress = bounded_value
            if prepared is None:
                progress(bounded_value, message)
                return
            doc_index, total_docs, short_label = doc_positions[prepared.document.relative_path]
            progress(bounded_value, f"{message} ({short_label}, {doc_index}/{total_docs})")

        prepared_documents: dict[str, _PreparedPdfDocument] = {}
        prepared_pages: list[_PreparedPdfPage] = []
        prepared_batch_bytes = 0
        completed_pages = 0
        total_pages = 0

        def flush_prepared_pages() -> None:
            nonlocal prepared_batch_bytes
            nonlocal completed_pages
            if not prepared_pages:
                return
            batch_size = len(prepared_pages)
            batch_completed_before = completed_pages

            def batch_value(value: int) -> int:
                if value < 0:
                    return -1
                normalized = max(0, min(100, int(value)))
                completed_units = batch_completed_before + (batch_size * normalized / 100.0)
                return 15 + round(completed_units * 85 / max(1, total_pages_expected))

            def batch_progress(value: int, message: str) -> None:
                emit_progress(batch_value(value), None, message)

            def batch_progress_reporter(
                value: int,
                prepared: _PreparedPdfPage | None,
                message: str,
            ) -> None:
                emit_progress(batch_value(value), prepared, message)

            self._populate_prepared_pages(prepared_pages, batch_progress, batch_progress_reporter)
            self._run_deferred_diagram_analysis(prepared_pages, batch_progress, batch_progress_reporter)
            for prepared in prepared_pages:
                prepared_documents[prepared.document.relative_path].pages[prepared.page_number - 1] = prepared.page_data
            completed_pages += batch_size
            prepared_pages.clear()
            prepared_batch_bytes = 0
            self._task_ocr_result_cache = {}

        for document in documents:
            pdf = fitz.open(document.path)
            page_count = page_counts.get(document.relative_path, pdf.page_count)
            doc_plan = _PreparedPdfDocument(document=document, pages=[None] * page_count)
            prepared_documents[document.relative_path] = doc_plan
            try:
                for page_number, page in enumerate(pdf, start=1):
                    total_pages += 1
                    native_blocks = self._extract_native_text(page, page_number)
                    native_char_count = sum(len(block.text) for block in native_blocks)
                    vector_segments = self._extract_vector_segments(page)
                    diagram_like = self._is_diagram_document(
                        document,
                        native_blocks=native_blocks,
                        vector_segments=vector_segments,
                    )
                    use_native_only = (
                        self._native_text_is_sufficient(native_blocks, native_char_count)
                        and not diagram_like
                    )
                    should_analyze_page = diagram_like or (self.ocr_enabled and not use_native_only)
                    if not should_analyze_page:
                        doc_plan.pages[page_number - 1] = self._native_page_data(
                            native_blocks=native_blocks,
                            page_number=page_number,
                        )
                        completed_pages += 1
                        pseudo_prepared = _PreparedPdfPage(
                            document=document,
                            page_number=page_number,
                            page_total=page_count,
                            native_blocks=native_blocks,
                            scaled_native_blocks=[],
                            scaled_vector_segments=[],
                            image=np.zeros((1, 1, 3), dtype=np.uint8),
                            analysis_flags=[],
                            diagram_like=diagram_like,
                            rendered_dpi=self.ocr_dpi,
                        )
                        emit_progress(
                            max(
                                round(total_pages * 15 / max(1, total_pages_expected)),
                                15 + round(completed_pages * 85 / max(1, total_pages_expected)),
                            ),
                            pseudo_prepared,
                            f"Analyzing {document.relative_path} page {page_number}/{page_count}",
                        )
                        continue
                    cache_path = self._page_cache_path(document, page_number, diagram_like)
                    if cache_path is not None and cache_path.exists():
                        cached = PageData.model_validate_json(
                            cache_path.read_text(encoding="utf-8")
                        )
                        if "cache_hit" not in cached.analysis_flags:
                            cached.analysis_flags.append("cache_hit")
                        doc_plan.pages[page_number - 1] = cached
                        completed_pages += 1
                        pseudo_prepared = _PreparedPdfPage(
                            document=document,
                            page_number=page_number,
                            page_total=page_count,
                            native_blocks=native_blocks,
                            scaled_native_blocks=[],
                            scaled_vector_segments=[],
                            image=np.zeros((1, 1, 3), dtype=np.uint8),
                            analysis_flags=[],
                            diagram_like=diagram_like,
                            rendered_dpi=self.ocr_dpi,
                        )
                        emit_progress(
                            max(
                                round(total_pages * 15 / max(1, total_pages_expected)),
                                15 + round(completed_pages * 85 / max(1, total_pages_expected)),
                            ),
                            pseudo_prepared,
                            f"Analyzing {document.relative_path} page {page_number}/{page_count}",
                        )
                        continue
                    prepared = self._prepare_pdf_page(
                        document=document,
                        page=page,
                        page_number=page_number,
                        page_total=page_count,
                        native_blocks=native_blocks,
                        diagram_like=diagram_like,
                        vector_segments=vector_segments,
                        cache_path=cache_path,
                    )
                    prepared_pages.append(prepared)
                    prepared_batch_bytes += max(1, int(prepared.image.nbytes))
                    if (
                        len(prepared_pages) >= _PREPARED_PDF_BATCH_MAX_PAGES
                        or prepared_batch_bytes >= _PREPARED_PDF_BATCH_MEMORY_LIMIT_BYTES
                    ):
                        flush_prepared_pages()
                    emit_progress(
                        round(total_pages * 15 / max(1, total_pages_expected)),
                        prepared,
                        f"Analyzing {document.relative_path} page {page_number}/{page_count}",
                    )
            finally:
                pdf.close()

        flush_prepared_pages()

        parsed_documents: dict[str, ParsedDocument] = {}
        for document in documents:
            doc_plan = prepared_documents[document.relative_path]
            pages = [page for page in doc_plan.pages if page is not None]
            parsed = ParsedDocument(
                document=document,
                pages=pages,
                metadata={"page_count": len(pages)},
            )
            if document.source_kind in {
                SourceDocumentKind.STROMLAUFPLAN,
                SourceDocumentKind.RI_FLOWSHEET,
            } and self._is_diagram_with_components(parsed):
                additions = [
                    DocumentFamily.STROMLAUF_COMPONENT_GROUP,
                    DocumentFamily.STROMLAUF_COMPONENT,
                    DocumentFamily.STROMLAUF_CONNECTION,
                ]
                document.output_families = list(document.output_families) + [
                    family for family in additions if family not in document.output_families
                ]
            parsed_documents[document.relative_path] = parsed

        if progress and total_pages:
            progress(
                100,
                f"Deferred PDF analysis complete: {len(documents)} documents, {total_pages} pages",
            )
        return parsed_documents

    def _extract_native_text(
        self, page: fitz.Page, page_number: int
    ) -> list[TextBlock]:
        blocks: list[TextBlock] = []
        for index, item in enumerate(page.get_text("blocks")):
            x0, y0, x1, y1, text = item[:5]
            cleaned = clean_cell(text)
            if not cleaned:
                continue
            bbox = self._page_bbox_to_render_space(page, (float(x0), float(y0), float(x1), float(y1)))
            blocks.append(
                TextBlock(
                    page_number=page_number,
                    text=cleaned,
                    bbox=bbox,
                    source="native_text",
                    score=1.0,
                    confidence=1.0,
                    engine="pymupdf",
                    block_type="text",
                    reading_order=index,
                )
            )
        return blocks

    def _analyze_pdf_page(
        self,
        document: DocumentDescriptor,
        page: fitz.Page,
        page_number: int,
        native_blocks: list[TextBlock],
        diagram_like: bool,
        vector_segments: list[tuple[float, float, float, float]],
        progress: ProgressCallback | None = None,
    ) -> PageData:
        prepared = self._prepare_pdf_page(
            document=document,
            page=page,
            page_number=page_number,
            page_total=page.parent.page_count,
            native_blocks=native_blocks,
            diagram_like=diagram_like,
            vector_segments=vector_segments,
            cache_path=self._page_cache_path(document, page_number, diagram_like),
        )
        self._populate_prepared_pages([prepared], progress)
        self._run_deferred_diagram_analysis([prepared], progress)
        return prepared.page_data or self._native_page_data(native_blocks, page_number)

    def _prepare_pdf_page(
        self,
        *,
        document: DocumentDescriptor,
        page: fitz.Page,
        page_number: int,
        page_total: int,
        native_blocks: list[TextBlock],
        diagram_like: bool,
        vector_segments: list[tuple[float, float, float, float]],
        cache_path: Path | None,
    ) -> _PreparedPdfPage:
        dpi = self.diagram_dpi if diagram_like else self.ocr_dpi
        image = self._render_page(page, dpi)
        scale_x = float(image.shape[1]) / max(page.rect.width, 1.0)
        scale_y = float(image.shape[0]) / max(page.rect.height, 1.0)
        return _PreparedPdfPage(
            document=document,
            page_number=page_number,
            page_total=page_total,
            native_blocks=native_blocks,
            scaled_native_blocks=self._scale_blocks(native_blocks, scale_x, scale_y),
            scaled_vector_segments=self._scale_segments(vector_segments, scale_x, scale_y),
            image=image,
            analysis_flags=[f"rendered_dpi:{dpi}"],
            diagram_like=diagram_like,
            rendered_dpi=dpi,
            cache_path=cache_path,
        )

    def _native_page_data(
        self,
        *,
        native_blocks: list[TextBlock],
        page_number: int,
    ) -> PageData:
        return PageData(
            page_number=page_number,
            blocks=native_blocks,
            has_native_text=bool(native_blocks),
            used_ocr=False,
            image_size=None,
            ocr_engine_used="native",
            layout_blocks=self._layout_blocks_from_text(native_blocks),
            tables=[],
            kv_pairs=extract_key_value_pairs(native_blocks, page_number),
            diagram_graph=None,
            structured_diagram=None,
            analysis_flags=["native_text_only"],
            rendered_dpi=None,
        )

    def _populate_prepared_pages(
        self,
        prepared_pages: list[_PreparedPdfPage],
        progress: ProgressCallback | None = None,
        progress_reporter: Any | None = None,
    ) -> None:
        if not prepared_pages:
            return
        active_backends = self._batch_active_backends()
        for backend in active_backends:
            try:
                backend.begin_batch()
            except Exception:
                continue
        try:
            if self.ocr_enabled and self.ocr_pipeline_mode == "ensemble":
                self._run_ocr_ensemble_batch(prepared_pages, progress, progress_reporter)
            else:
                self._run_prepared_pages_sequential_ocr(prepared_pages, progress, progress_reporter)
        finally:
            for backend in reversed(active_backends):
                try:
                    backend.end_batch()
                except Exception:
                    continue

    def _batch_active_backends(self) -> list[Any]:
        if not self.ocr_enabled:
            return []
        backend_names: list[str]
        if self.ocr_pipeline_mode == "ensemble":
            backend_names = sorted(
                self.ocr_ensemble_backends,
                key=lambda name: (self._backend_execution_order.get(name, 99), name),
            )
        else:
            backend_names = [self.ocr_backend, self.ocr_fallback_backend, "rapidocr"]
        active: list[Any] = []
        seen: set[str] = set()
        for name in backend_names:
            normalized = (name or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            backend = self._backends.get(normalized)
            if backend is None:
                continue
            try:
                if backend.is_available():
                    active.append(backend)
            except Exception:
                continue
        return active

    def _run_prepared_pages_sequential_ocr(
        self,
        prepared_pages: list[_PreparedPdfPage],
        progress: ProgressCallback | None = None,
        progress_reporter: Any | None = None,
    ) -> None:
        total = len(prepared_pages)
        if not prepared_pages:
            return
        if not self.ocr_enabled:
            for index, prepared in enumerate(prepared_pages, start=1):
                prepared.analysis_flags.append("ocr_disabled")
                prepared.page_data = self._build_page_data(prepared, OCRBackendResult(engine="native"))
                if progress_reporter is not None:
                    progress_reporter(
                        90 + round(index * 10 / max(1, total)),
                        prepared,
                        f"OCR disabled: finalized {prepared.document.relative_path} page {prepared.page_number}/{prepared.page_total}",
                    )
            return

        final_results: dict[tuple[str, int], OCRBackendResult] = {}
        primary = self._get_backend(self.ocr_backend)
        fallback = self._get_backend(self.ocr_fallback_backend)
        compat = self._get_backend("rapidocr")

        if progress:
            selected = [
                f"primary={primary.name}" if primary is not None else f"primary={self.ocr_backend}:missing",
                f"fallback={fallback.name}" if fallback is not None else f"fallback={self.ocr_fallback_backend}:missing",
                f"compat={compat.name}" if compat is not None else "compat=rapidocr:missing",
            ]
            progress(-1, f"OCR staged fallback: {', '.join(selected)}")

        if primary is not None:
            self._run_backend_batch_sweep(
                prepared_pages,
                primary,
                progress_prefix="OCR primary sweep",
                progress=progress,
                progress_reporter=progress_reporter,
                start_value=0,
                end_value=45,
            )
            for prepared in prepared_pages:
                prepared.analysis_flags.append(f"ocr_primary:{primary.name}")
                final_results[self._prepared_page_key(prepared)] = prepared.backend_results.get(
                    primary.name,
                    OCRBackendResult(engine="none"),
                )
        else:
            for prepared in prepared_pages:
                prepared.analysis_flags.append(f"ocr_primary_missing:{self.ocr_backend}")
                final_results[self._prepared_page_key(prepared)] = OCRBackendResult(engine="none")

        fallback_candidates = [
            prepared
            for prepared in prepared_pages
            if self._needs_hard_fallback(
                final_results[self._prepared_page_key(prepared)],
                prepared.diagram_like,
            )
        ]
        if fallback_candidates:
            if fallback is not None and (primary is None or fallback.name != primary.name):
                self._run_backend_batch_sweep(
                    fallback_candidates,
                    fallback,
                    progress_prefix="OCR fallback sweep",
                    progress=progress,
                    progress_reporter=progress_reporter,
                    start_value=45,
                    end_value=75,
                )
                for prepared in fallback_candidates:
                    prepared.analysis_flags.append(f"ocr_fallback:{fallback.name}")
                    fallback_result = prepared.backend_results.get(fallback.name)
                    if fallback_result is None:
                        continue
                    key = self._prepared_page_key(prepared)
                    final_results[key] = self._prefer_stronger_result(final_results[key], fallback_result)
            else:
                for prepared in fallback_candidates:
                    prepared.analysis_flags.append(f"ocr_fallback_missing:{self.ocr_fallback_backend}")

        compat_candidates = [
            prepared
            for prepared in prepared_pages
            if compat is not None
            and compat.name != final_results[self._prepared_page_key(prepared)].engine
            and not final_results[self._prepared_page_key(prepared)].blocks
        ]
        if compat_candidates and compat is not None:
            self._run_backend_batch_sweep(
                compat_candidates,
                compat,
                progress_prefix="OCR compat sweep",
                progress=progress,
                progress_reporter=progress_reporter,
                start_value=75,
                end_value=90,
            )
            for prepared in compat_candidates:
                prepared.analysis_flags.append("ocr_compat:rapidocr")
                compat_result = prepared.backend_results.get(compat.name)
                if compat_result is None:
                    continue
                key = self._prepared_page_key(prepared)
                final_results[key] = self._prefer_stronger_result(final_results[key], compat_result)

        for index, prepared in enumerate(prepared_pages, start=1):
            key = self._prepared_page_key(prepared)
            if progress_reporter is not None:
                progress_reporter(
                    90 + round((index - 1) * 10 / max(1, total)),
                    prepared,
                    f"OCR finalize page {index}/{total}: {prepared.document.relative_path} page {prepared.page_number}/{prepared.page_total}",
                )
            prepared.page_data = self._build_page_data(
                prepared,
                final_results.get(key, OCRBackendResult(engine="none")),
            )
            if progress_reporter is not None:
                progress_reporter(
                    90 + round(index * 10 / max(1, total)),
                    prepared,
                    f"OCR finalized page {index}/{total}: {prepared.document.relative_path} page {prepared.page_number}/{prepared.page_total}",
                )

    def _prepared_page_key(self, prepared: _PreparedPdfPage) -> tuple[str, int]:
        return (prepared.document.relative_path, prepared.page_number)

    def _needs_hard_fallback(
        self,
        result: OCRBackendResult,
        diagram_like: bool,
    ) -> bool:
        return self.enable_hard_page_fallback and (
            not result.blocks
            or result.average_confidence < self.ocr_min_confidence
            or (diagram_like and len(result.blocks) < 2)
        )

    def _run_backend_batch_sweep(
        self,
        prepared_pages: list[_PreparedPdfPage],
        backend: Any,
        *,
        progress_prefix: str,
        progress: ProgressCallback | None = None,
        progress_reporter: Any | None = None,
        start_value: int = 0,
        end_value: int = 100,
    ) -> None:
        total = len(prepared_pages)
        if total <= 0:
            return
        sweep_started = time.perf_counter()
        if progress:
            progress(start_value, f"{progress_prefix} [{backend.name}] starting {total} pages")

        from iev4pi_transformation_tool.core.qos_helpers import QoSAwareThreadPoolExecutor, pcore_worker_count

        import concurrent.futures
        as_completed = concurrent.futures.as_completed

        worker_count = min(pcore_worker_count(), total)

        def _process_page(index: int, prepared: _PreparedPdfPage) -> tuple[int, _PreparedPdfPage]:
            cache_key = self._ocr_cache_key(
                document=prepared.document,
                page_number=prepared.page_number,
                backend_name=backend.name,
                diagram_like=prepared.diagram_like,
            )
            if cache_key in self._task_ocr_result_cache:
                result = copy.deepcopy(self._task_ocr_result_cache[cache_key])
            else:
                result = self._run_backend_with_timing(
                    backend,
                    prepared.image,
                    prepared.page_number,
                    progress_prefix=progress_prefix,
                    progress=None,  # no per-page progress in parallel mode
                )
                self._task_ocr_result_cache[cache_key] = copy.deepcopy(result)
            prepared.backend_results[backend.name] = result
            return index, prepared

        if worker_count <= 1:
            # Serial path: avoid thread-pool overhead for single pages
            for _index, _prepared in [(i + 1, prepared_pages[i]) for i in range(total)]:
                _idx, _ = _process_page(_index - 1, _prepared)
                if progress_reporter is not None:
                    progress_reporter(
                        start_value + round(_idx * (end_value - start_value) / max(1, total)),
                        _prepared,
                        f"{progress_prefix} [{backend.name}] page {_idx}/{total}: {_prepared.document.relative_path} page {_prepared.page_number}/{_prepared.page_total}",
                    )
        else:
            with QoSAwareThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(_process_page, i, prepared_pages[i]): i
                    for i in range(total)
                }
                for future in as_completed(futures):
                    future.result()  # result stored in prepared.backend_results

            # Serial progress reporting after all pages complete
            for index, prepared in enumerate(prepared_pages, start=1):
                if progress_reporter is not None:
                    progress_reporter(
                        start_value + round(index * (end_value - start_value) / max(1, total)),
                        prepared,
                        f"{progress_prefix} [{backend.name}] completed page {index}/{total}: {prepared.document.relative_path} page {prepared.page_number}/{prepared.page_total}",
                    )

        if progress:
            duration_ms = round((time.perf_counter() - sweep_started) * 1000, 1)
            progress(
                end_value,
                f"{progress_prefix} [{backend.name}] completed in {duration_ms} ms across {total} pages",
            )

    def _build_page_data(
        self,
        prepared: _PreparedPdfPage,
        backend_result: OCRBackendResult,
    ) -> PageData:
        merged_blocks = self._merge_blocks(prepared.scaled_native_blocks, backend_result.blocks)
        if not merged_blocks:
            merged_blocks = prepared.scaled_native_blocks
        layout_blocks = backend_result.layout_blocks or self._layout_blocks_from_text(
            merged_blocks
        )
        kv_pairs = self._merge_kv_pairs(
            extract_key_value_pairs(merged_blocks, prepared.page_number),
            backend_result.kv_pairs,
        )
        return PageData(
            page_number=prepared.page_number,
            blocks=merged_blocks,
            has_native_text=bool(prepared.native_blocks),
            used_ocr=bool(backend_result.blocks),
            image_size=(int(prepared.image.shape[1]), int(prepared.image.shape[0])),
            ocr_engine_used=backend_result.engine,
            layout_blocks=layout_blocks,
            tables=backend_result.tables,
            kv_pairs=kv_pairs,
            diagram_graph=None,
            structured_diagram=None,
            analysis_flags=[*prepared.analysis_flags, *backend_result.flags],
            rendered_dpi=prepared.rendered_dpi,
        )

    def _run_deferred_diagram_analysis(
        self,
        prepared_pages: list[_PreparedPdfPage],
        progress: ProgressCallback | None = None,
        progress_reporter: Any | None = None,
    ) -> None:
        deferred_pages = [
            prepared
            for prepared in prepared_pages
            if prepared.page_data is not None
            and self.enable_diagram_relation_extraction
            and prepared.diagram_like
        ]
        for prepared in prepared_pages:
            if prepared.page_data is not None and prepared not in deferred_pages:
                self._write_page_cache(prepared)
        if not deferred_pages:
            return
        analyzer_name = (
            "vlm"
            if self.diagram_extraction_backend == "vlm" and self._vlm_diagram_analyzer is not None
            else "heuristic"
        )
        total = len(deferred_pages)
        if progress:
            progress(90, f"Diagram analysis [{analyzer_name}] starting {total} pages")
        for index, prepared in enumerate(deferred_pages, start=1):
            if progress_reporter is not None:
                progress_reporter(
                    90 + round((index - 1) * 10 / max(1, total)),
                    prepared,
                    f"Diagram analysis [{analyzer_name}] page {index}/{total}: {prepared.document.relative_path} page {prepared.page_number}/{prepared.page_total}",
                )
            started_at = time.perf_counter()
            page_data = prepared.page_data
            assert page_data is not None
            if analyzer_name == "vlm" and self._vlm_diagram_analyzer is not None:
                vlm_progress = progress
                if progress_reporter is not None:
                    def vlm_progress(value: int, message: str, _prepared: _PreparedPdfPage = prepared) -> None:
                        progress_reporter(-1 if value < 0 else value, _prepared, message)
                diagram_result = self._vlm_diagram_analyzer.analyze(
                    image=prepared.image,
                    blocks=page_data.blocks,
                    page_number=prepared.page_number,
                    source_path=prepared.document.relative_path,
                    source_kind=prepared.document.source_kind,
                    vector_segments=prepared.scaled_vector_segments,
                    analysis_mode=self.diagram_analysis_mode,
                    on_progress=vlm_progress,
                )
            else:
                diagram_result = self._diagram_analyzer.analyze(
                    image=prepared.image,
                    blocks=page_data.blocks,
                    page_number=prepared.page_number,
                    source_path=prepared.document.relative_path,
                    source_kind=prepared.document.source_kind,
                    vector_segments=prepared.scaled_vector_segments,
                    analysis_mode=self.diagram_analysis_mode,
                )
            page_data.diagram_graph = diagram_result.graph
            page_data.structured_diagram = diagram_result.structured_page
            if page_data.diagram_graph is not None:
                page_data.analysis_flags.append(
                    f"diagram_graph:{len(page_data.diagram_graph.nodes)}_nodes:{len(page_data.diagram_graph.edges)}_edges"
                )
            for flag in diagram_result.flags:
                if flag not in page_data.analysis_flags:
                    page_data.analysis_flags.append(flag)
            self._write_page_cache(prepared)
            if progress:
                duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
                node_count = len(page_data.diagram_graph.nodes) if page_data.diagram_graph is not None else 0
                edge_count = len(page_data.diagram_graph.edges) if page_data.diagram_graph is not None else 0
                if progress_reporter is not None:
                    progress_reporter(
                        90 + round(index * 10 / max(1, total)),
                        prepared,
                        f"Diagram analysis [{analyzer_name}] done in {duration_ms} ms with {node_count} nodes / {edge_count} edges",
                    )

    def _write_page_cache(self, prepared: _PreparedPdfPage) -> None:
        if prepared.cache_path is not None and prepared.page_data is not None:
            prepared.cache_path.write_text(
                prepared.page_data.model_dump_json(indent=2),
                encoding="utf-8",
            )

    def _scale_blocks(
        self,
        blocks: list[TextBlock],
        scale_x: float,
        scale_y: float,
    ) -> list[TextBlock]:
        if abs(scale_x - 1.0) < 1e-6 and abs(scale_y - 1.0) < 1e-6:
            return blocks
        scaled: list[TextBlock] = []
        for block in blocks:
            scaled.append(
                block.model_copy(
                    update={
                        "bbox": (
                            float(block.bbox[0] * scale_x),
                            float(block.bbox[1] * scale_y),
                            float(block.bbox[2] * scale_x),
                            float(block.bbox[3] * scale_y),
                        )
                    }
                )
            )
        return scaled

    def _scale_segments(
        self,
        segments: list[tuple[float, float, float, float]],
        scale_x: float,
        scale_y: float,
    ) -> list[tuple[float, float, float, float]]:
        if abs(scale_x - 1.0) < 1e-6 and abs(scale_y - 1.0) < 1e-6:
            return segments
        return [
            (
                float(x1 * scale_x),
                float(y1 * scale_y),
                float(x2 * scale_x),
                float(y2 * scale_y),
            )
            for x1, y1, x2, y2 in segments
        ]

    def _run_ocr_pipeline(
        self,
        image: np.ndarray,
        page_number: int,
        diagram_like: bool,
        analysis_flags: list[str],
        progress: ProgressCallback | None = None,
    ) -> OCRBackendResult:
        if self.ocr_pipeline_mode == "ensemble":
            return self._run_ocr_ensemble(image, page_number, analysis_flags, progress)
        return self._run_ocr_fallback(image, page_number, diagram_like, analysis_flags, progress)

    def _progress_message(
        self,
        prefix: str,
        backend: Any,
        page_number: int,
    ) -> str:
        message = f"{prefix} [{backend.name}] processing page {page_number}..."
        if getattr(backend, "name", "") == "easyocr":
            device = self._easyocr_device()
            message = f"{message} using {device}"
        return message

    def _run_backend_with_timing(
        self,
        backend: Any,
        image: np.ndarray,
        page_number: int,
        *,
        progress_prefix: str,
        progress: ProgressCallback | None = None,
    ) -> OCRBackendResult:
        if progress:
            progress(-1, self._progress_message(progress_prefix, backend, page_number))
        started_at = time.perf_counter()
        result = backend.process_page(image, page_number)
        duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
        if progress:
            device = getattr(result, "device", "cpu")
            progress(
                -1,
                (
                    f"{progress_prefix} [{backend.name}] done in {duration_ms} ms "
                    f"on {device} with {len(result.blocks)} blocks "
                    f"(avg_conf={result.average_confidence:.3f})"
                ),
            )
            if result.flags:
                progress(
                    -1,
                    f"{progress_prefix} [{backend.name}] flags: {', '.join(result.flags)}",
                )
        return result

    def _run_ocr_fallback(
        self,
        image: np.ndarray,
        page_number: int,
        diagram_like: bool,
        analysis_flags: list[str],
        progress: ProgressCallback | None = None,
    ) -> OCRBackendResult:
        result = OCRBackendResult(engine="none")
        primary = self._get_backend(self.ocr_backend)
        if primary is not None:
            result = self._run_backend_with_timing(
                primary,
                image,
                page_number,
                progress_prefix="OCR",
                progress=progress,
            )
            analysis_flags.append(f"ocr_primary:{primary.name}")
        else:
            analysis_flags.append(f"ocr_primary_missing:{self.ocr_backend}")

        need_hard_fallback = self._needs_hard_fallback(result, diagram_like)
        if need_hard_fallback:
            fallback = self._get_backend(self.ocr_fallback_backend)
            if fallback is not None and fallback.name != result.engine:
                fallback_result = self._run_backend_with_timing(
                    fallback,
                    image,
                    page_number,
                    progress_prefix="OCR fallback",
                    progress=progress,
                )
                analysis_flags.append(f"ocr_fallback:{fallback.name}")
                result = self._prefer_stronger_result(result, fallback_result)
            else:
                analysis_flags.append(
                    f"ocr_fallback_missing:{self.ocr_fallback_backend}"
                )

        if not result.blocks:
            compat = self._get_backend("rapidocr")
            if compat is not None and compat.name != result.engine:
                compat_result = self._run_backend_with_timing(
                    compat,
                    image,
                    page_number,
                    progress_prefix="OCR compat",
                    progress=progress,
                )
                analysis_flags.append("ocr_compat:rapidocr")
                result = self._prefer_stronger_result(result, compat_result)

        return result

    def _run_ocr_ensemble(
        self,
        image: np.ndarray,
        page_number: int,
        analysis_flags: list[str],
        progress: ProgressCallback | None = None,
    ) -> OCRBackendResult:
        """Run all available backends in backend-order sweep form for a single page."""
        prepared = _PreparedPdfPage(
            document=DocumentDescriptor(
                path=Path("<memory>"),
                relative_path="<memory>",
                extension=".pdf",
                source_kind=SourceDocumentKind.STELLEN_TU,
                output_families=[],
                size_bytes=0,
                modified_at=0.0,
            ),
            page_number=page_number,
            page_total=1,
            native_blocks=[],
            scaled_native_blocks=[],
            scaled_vector_segments=[],
            image=image,
            analysis_flags=analysis_flags,
            diagram_like=False,
            rendered_dpi=self.ocr_dpi,
        )
        self._run_ocr_ensemble_batch([prepared], progress)
        if prepared.page_data is None:
            return OCRBackendResult(engine="ensemble")
        ocr_flags = [
            flag
            for flag in prepared.page_data.analysis_flags
            if flag.startswith("ocr_")
            or flag.startswith("easyocr:")
            or flag.startswith("apple:")
            or flag.startswith("rapidocr:")
            or flag.startswith("paddle:")
            or flag.startswith("surya:")
        ]
        block_confidences = [block.confidence for block in prepared.page_data.blocks]
        average_confidence = (
            sum(block_confidences) / len(block_confidences) if block_confidences else 0.0
        )
        return OCRBackendResult(
            engine=prepared.page_data.ocr_engine_used,
            blocks=prepared.page_data.blocks,
            layout_blocks=prepared.page_data.layout_blocks,
            tables=prepared.page_data.tables,
            kv_pairs=prepared.page_data.kv_pairs,
            average_confidence=average_confidence,
            flags=ocr_flags,
        )

    def _run_ocr_ensemble_batch(
        self,
        prepared_pages: list[_PreparedPdfPage],
        progress: ProgressCallback | None = None,
        progress_reporter: Any | None = None,
    ) -> None:
        selected_backends = sorted(self.ocr_ensemble_backends)
        available_backends = []
        skipped_backends: list[str] = []
        for backend_name in selected_backends:
            backend = self._backends.get(backend_name)
            if backend is None:
                skipped_backends.append(f"{backend_name}:missing_registration")
                for prepared in prepared_pages:
                    prepared.analysis_flags.append(f"ocr_ensemble_missing:{backend_name}")
                continue
            try:
                backend_available = backend.is_available()
            except Exception as exc:
                skipped_backends.append(f"{backend_name}:availability_error:{exc.__class__.__name__}")
                for prepared in prepared_pages:
                    prepared.analysis_flags.append(
                        f"ocr_ensemble_error:{backend_name}:availability:{exc.__class__.__name__}"
                    )
                continue
            if not backend_available:
                skipped_backends.append(f"{backend_name}:unavailable")
                continue
            available_backends.append(backend)

        if progress:
            progress(
                -1,
                (
                    f"OCR ensemble sweep: selected={','.join(selected_backends) or 'none'}; "
                    f"available={','.join(backend.name for backend in available_backends) or 'none'}; "
                    f"skipped={','.join(skipped_backends) or 'none'}"
                ),
            )
            easyocr_details = EasyOCRBackend.availability_details()
            progress(
                -1,
                (
                    "EasyOCR availability: "
                    f"python={easyocr_details.get('python_executable')}; "
                    f"bridge_exists={easyocr_details.get('bridge_exists')}; "
                    f"module_found={easyocr_details.get('module_found')}; "
                    f"module_origin={easyocr_details.get('module_origin')}"
                ),
            )
        ordered_available_backends = sorted(
            available_backends,
            key=lambda backend: (self._backend_execution_order.get(backend.name, 99), backend.name),
        )
        total_pages = len(prepared_pages)
        available_backend_names = {backend.name for backend in ordered_available_backends}
        for prepared in prepared_pages:
            prepared.analysis_flags.append("ocr_pipeline:ensemble")
            for backend_name in selected_backends:
                if backend_name in available_backend_names:
                    continue
                prepared.analysis_flags.append(f"ocr_ensemble_unavailable:{backend_name}")
        if not ordered_available_backends:
            for prepared in prepared_pages:
                prepared.analysis_flags.append("ocr_ensemble:no_backends")
                prepared.page_data = self._build_page_data(prepared, OCRBackendResult(engine="ensemble"))
            return

        total_ops = max(1, len(ordered_available_backends) * total_pages)
        for backend_index, backend in enumerate(ordered_available_backends):
            sweep_started = time.perf_counter()
            if progress:
                progress(10, f"OCR sweep [{backend.name}] starting {total_pages} pages")
            for index, prepared in enumerate(prepared_pages, start=1):
                cache_key = self._ocr_cache_key(
                    document=prepared.document,
                    page_number=prepared.page_number,
                    backend_name=backend.name,
                    diagram_like=prepared.diagram_like,
                )
                if progress_reporter is not None:
                    completed_ops = backend_index * total_pages + (index - 1)
                    progress_reporter(
                        10 + round(completed_ops * 65 / total_ops),
                        prepared,
                        f"OCR sweep [{backend.name}] page {index}/{total_pages}: {prepared.document.relative_path} page {prepared.page_number}/{prepared.page_total}",
                    )
                try:
                    if cache_key in self._task_ocr_result_cache:
                        result = copy.deepcopy(self._task_ocr_result_cache[cache_key])
                        if progress_reporter is not None:
                            progress_reporter(
                                -1,
                                prepared,
                                f"OCR sweep [{backend.name}] cache hit for {prepared.document.relative_path} page {prepared.page_number}",
                            )
                    else:
                        backend_progress = progress
                        if progress_reporter is not None:
                            def backend_progress(value: int, message: str, _prepared: _PreparedPdfPage = prepared) -> None:
                                progress_reporter(-1 if value < 0 else value, _prepared, message)
                        result = self._run_backend_with_timing(
                            backend,
                            prepared.image,
                            prepared.page_number,
                            progress_prefix="OCR ensemble",
                            progress=backend_progress,
                        )
                        self._task_ocr_result_cache[cache_key] = copy.deepcopy(result)
                    prepared.analysis_flags.append(f"ocr_ensemble_backend:{backend.name}")
                    prepared.backend_results[backend.name] = result
                    if progress_reporter is not None:
                        completed_ops = backend_index * total_pages + index
                        progress_reporter(
                            10 + round(completed_ops * 65 / total_ops),
                            prepared,
                            f"OCR sweep [{backend.name}] completed page {index}/{total_pages}: {prepared.document.relative_path} page {prepared.page_number}/{prepared.page_total}",
                        )
                except Exception as exc:
                    prepared.analysis_flags.append(
                        f"ocr_ensemble_error:{backend.name}:{exc.__class__.__name__}"
                    )
                    if progress:
                        if progress_reporter is not None:
                            progress_reporter(
                                -1,
                                prepared,
                                f"OCR sweep [{backend.name}] failed for {prepared.document.relative_path} page {prepared.page_number}: {exc.__class__.__name__}",
                            )
            if progress:
                duration_ms = round((time.perf_counter() - sweep_started) * 1000, 1)
                progress(
                    75,
                    f"OCR sweep [{backend.name}] completed in {duration_ms} ms across {total_pages} pages",
                )

        backend_name_order = [backend.name for backend in ordered_available_backends]
        for index, prepared in enumerate(prepared_pages, start=1):
            if progress_reporter is not None:
                progress_reporter(
                    75 + round((index - 1) * 15 / max(1, total_pages)),
                    prepared,
                    f"OCR ensemble fuse page {index}/{total_pages}: {prepared.document.relative_path} page {prepared.page_number}/{prepared.page_total}",
                )
            started_at = time.perf_counter()
            backend_results = [
                prepared.backend_results[name]
                for name in backend_name_order
                if name in prepared.backend_results
            ]
            result = ensemble_results(backend_results) if backend_results else OCRBackendResult(engine="ensemble")
            prepared.page_data = self._build_page_data(prepared, result)
            if progress:
                duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
                if progress_reporter is not None:
                    progress_reporter(
                        75 + round(index * 15 / max(1, total_pages)),
                        prepared,
                        f"OCR ensemble fusion done in {duration_ms} ms with {len(result.blocks)} merged blocks",
                    )

    def _prefer_stronger_result(
        self, current: OCRBackendResult, candidate: OCRBackendResult
    ) -> OCRBackendResult:
        merged_layout_blocks = self._merge_layout_blocks(
            current.layout_blocks, candidate.layout_blocks
        )
        merged_tables = self._merge_tables(current.tables, candidate.tables)
        if not current.blocks:
            candidate.layout_blocks = merged_layout_blocks or candidate.layout_blocks
            candidate.tables = merged_tables or candidate.tables
            return candidate
        if not candidate.blocks:
            current.layout_blocks = merged_layout_blocks or current.layout_blocks
            current.tables = merged_tables or current.tables
            current.flags.extend(
                flag for flag in candidate.flags if flag not in current.flags
            )
            return current
        current_strength = (current.average_confidence, len(current.blocks))
        candidate_strength = (candidate.average_confidence, len(candidate.blocks))
        if candidate_strength > current_strength:
            candidate.blocks = self._merge_blocks(current.blocks, candidate.blocks)
            candidate.layout_blocks = (
                merged_layout_blocks or self._layout_blocks_from_text(candidate.blocks)
            )
            candidate.tables = merged_tables
            candidate.kv_pairs = self._merge_kv_pairs(
                current.kv_pairs, candidate.kv_pairs
            )
            candidate.flags = [*current.flags, *candidate.flags]
            return candidate
        current.blocks = self._merge_blocks(current.blocks, candidate.blocks)
        current.layout_blocks = merged_layout_blocks or self._layout_blocks_from_text(
            current.blocks
        )
        current.tables = merged_tables
        current.kv_pairs = self._merge_kv_pairs(current.kv_pairs, candidate.kv_pairs)
        current.flags.extend(
            flag for flag in candidate.flags if flag not in current.flags
        )
        return current

    def _merge_blocks(
        self, left: list[TextBlock], right: list[TextBlock]
    ) -> list[TextBlock]:
        merged: list[TextBlock] = []
        # 使用 source 区分同一位置的 native_text 与 ocr_text，防止覆盖
        seen: set[tuple[int, tuple[int, int, int, int], str]] = set()
        for block in [*left, *right]:
            # 页码、bbox、source 共同决定唯一性
            key = (block.page_number, tuple(round(v) for v in block.bbox), block.source)
            if key in seen:
                continue
            seen.add(key)
            merged.append(block)
        return sorted(
            merged,
            key=lambda item: (
                item.page_number,
                item.reading_order or 0,
                item.bbox[1],
                item.bbox[0],
            ),
        )

    def _merge_layout_blocks(
        self, left: list[LayoutBlock], right: list[LayoutBlock]
    ) -> list[LayoutBlock]:
        merged: list[LayoutBlock] = []
        seen: set[tuple[str, str, tuple[int, int, int, int], int]] = set()
        for block in [*left, *right]:
            bbox_key = tuple(round(value) for value in block.bbox)
            key = (
                block.block_type.lower(),
                normalize_label(block.text),
                bbox_key,
                block.page_number,
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(block)
        return sorted(
            merged,
            key=lambda item: (
                item.page_number,
                item.reading_order or 0,
                item.bbox[1],
                item.bbox[0],
            ),
        )

    def _merge_tables(self, left: list, right: list) -> list:
        merged = []
        by_key: dict[tuple[int, tuple[int, int, int, int]], Any] = {}
        for table in [*left, *right]:
            bbox_key = tuple(round(value) for value in table.bbox)
            key = (table.page_number, bbox_key)
            existing = by_key.get(key)
            if existing is None or len(table.cells) > len(existing.cells):
                by_key[key] = table
        merged.extend(by_key.values())
        return merged

    def _merge_kv_pairs(self, left: list, right: list) -> list:
        merged = []
        seen: set[tuple[str, str, int]] = set()
        for pair in [*left, *right]:
            key = (
                normalize_label(pair.key),
                normalize_label(pair.value),
                pair.page_number,
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(pair)
        return merged

    def _layout_blocks_from_text(self, blocks: list[TextBlock]) -> list[LayoutBlock]:
        return [
            LayoutBlock(
                page_number=block.page_number,
                block_type=block.block_type,
                text=block.text,
                bbox=block.bbox,
                confidence=block.confidence,
                reading_order=block.reading_order,
                engine=block.engine,
            )
            for block in blocks
        ]

    def _render_page(self, page: fitz.Page, dpi: int) -> np.ndarray:
        scale = max(float(dpi) / 72.0, self.ocr_zoom)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        samples = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )
        n_channels = pixmap.n
        del pixmap
        if n_channels == 4:
            samples = samples[:, :, :3]
        return samples.copy()

    def _native_text_is_sufficient(
        self, blocks: list[TextBlock], char_count: int
    ) -> bool:
        return bool(blocks) and char_count >= 120 and len(blocks) >= 3

    def _is_diagram_document(
        self,
        document: DocumentDescriptor,
        *,
        native_blocks: list[TextBlock],
        vector_segments: list[tuple[float, float, float, float]],
    ) -> bool:
        if document.source_kind in {
            SourceDocumentKind.STROMLAUFPLAN,
            SourceDocumentKind.RI_FLOWSHEET,
        }:
            return True
        if document.source_kind != SourceDocumentKind.STELLEN_TU:
            return False
        native_text = " ".join(block.text for block in native_blocks[:80])
        diagram_markers = ("-A1", "-X", "Kanal:", "Adresse:", "Typ:", "Art:")
        has_diagram_text = any(marker.lower() in native_text.lower() for marker in diagram_markers)
        has_vector_density = len(vector_segments) >= 40
        return has_diagram_text or has_vector_density

    def _is_diagram_with_components(self, parsed: ParsedDocument) -> bool:
        """判定 PDF 是否为需要抽取元器件的图纸。
        条件：
        1. 任意页面的 diagram_graph 节点数 >= self.settings.diagram_min_nodes（默认 3）
        2. 所有页面 OCR 块中累计匹配 COMPONENT_PATTERNS 的 token 数 >= self.settings.diagram_min_ocr_tokens（默认 5）
        """
        # 使用硬编码默认阈值（可在未来通过 settings 注入）
        min_nodes = 3
        min_tokens = 5
        # ① 检查是否有满足节点数的页面
        graph_ok = any(
            (
                page.structured_diagram is not None
                and len(page.structured_diagram.groups) >= 1
                and len(page.structured_diagram.parts) >= 2
            )
            or (page.diagram_graph and len(page.diagram_graph.nodes) >= min_nodes)
            for page in parsed.pages
        )
        if not graph_ok:
            return False
        # ② 统计 OCR（或所有）块中匹配的元器件 token 数量
        token_cnt = 0
        for page in parsed.pages:
            for block in page.blocks:
                token_cnt += len(extract_component_tokens(block.text))
        return token_cnt >= min_tokens

    def _page_bbox_to_render_space(
        self,
        page: fitz.Page,
        bbox: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        rect = fitz.Rect(bbox)
        if page.rotation:
            rect = rect * page.rotation_matrix
        return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))

    def _extract_vector_segments(
        self, page: fitz.Page
    ) -> list[tuple[float, float, float, float]]:
        segments: list[tuple[float, float, float, float]] = []
        try:
            drawings = page.get_drawings()
        except Exception:
            return segments
        for drawing in drawings:
            for item in drawing.get("items", []):
                if not item or item[0] != "l":
                    continue
                start, end = item[1], item[2]
                start_point = start * page.rotation_matrix if page.rotation else start
                end_point = end * page.rotation_matrix if page.rotation else end
                segments.append(
                    (
                        float(start_point.x),
                        float(start_point.y),
                        float(end_point.x),
                        float(end_point.y),
                    )
                )
        return segments

    def _page_cache_path(
        self, document: DocumentDescriptor, page_number: int, diagram_like: bool
    ) -> Path | None:
        if self.cache_dir is None:
            return None
        signature = json.dumps(
            {
                "relative_path": document.relative_path,
                "modified_at": document.modified_at,
                "page_number": page_number,
                "ocr_enabled": self.ocr_enabled,
                "ocr_backend": self.ocr_backend,
                "ocr_fallback_backend": self.ocr_fallback_backend,
                "ocr_device": self.ocr_device,
                "apple_ocr_framework": self.apple_ocr_framework,
                "apple_ocr_recognition_level": self.apple_ocr_recognition_level,
                "ocr_dpi": self.ocr_dpi,
                "diagram_dpi": self.diagram_dpi,
                "diagram_analysis_mode": self.diagram_analysis_mode,
                "ocr_min_confidence": self.ocr_min_confidence,
                "diagram_like": diagram_like,
                "enable_diagram_relation_extraction": self.enable_diagram_relation_extraction,
                "enable_hard_page_fallback": self.enable_hard_page_fallback,
                "diagram_extraction_backend": self.diagram_extraction_backend,
                "ocr_pipeline_mode": self.ocr_pipeline_mode,
                "ocr_ensemble_backends": sorted(list(self.ocr_ensemble_backends)),
                "ocr_pipeline_revision": "20260401_staged_batch_v2",
            },
            sort_keys=True,
        )
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _ocr_cache_key(
        self,
        *,
        document: DocumentDescriptor,
        page_number: int,
        backend_name: str,
        diagram_like: bool,
    ) -> tuple[str, str, int, str]:
        signature = json.dumps(
            {
                "relative_path": document.relative_path,
                "modified_at": document.modified_at,
                "page_number": page_number,
                "backend": backend_name,
                "diagram_like": diagram_like,
                "ocr_device": self.ocr_device,
                "apple_ocr_framework": self.apple_ocr_framework,
                "apple_ocr_recognition_level": self.apple_ocr_recognition_level,
                "ocr_dpi": self.ocr_dpi,
                "diagram_dpi": self.diagram_dpi,
                "ocr_min_confidence": self.ocr_min_confidence,
                "easyocr_device": self._easyocr_device(),
            },
            sort_keys=True,
        )
        return (document.relative_path, backend_name, page_number, signature)

    def _get_backend(self, name: str):
        backend = self._backends.get((name or "").strip().lower())
        if backend is None:
            return None
        if not self._backend_available(backend.name):
            return None
        return backend

    def _backend_available(self, name: str) -> bool:
        backend = self._backends.get((name or "").strip().lower())
        return bool(backend and backend.is_available())

    def _easyocr_device(self) -> str:
        backend = self._backends.get("easyocr")
        if backend is None:
            return "cpu"
        runtime_device = getattr(backend, "runtime_device", None)
        if callable(runtime_device):
            try:
                return str(runtime_device())
            except Exception:
                return "cpu"
        return "cpu"

    def _gpu_available(self) -> bool:
        if importlib.util.find_spec("torch") is not None:
            try:
                import torch

                if torch.cuda.is_available():
                    return True
            except Exception:
                pass
        if importlib.util.find_spec("paddle") is not None:
            try:
                import paddle

                if paddle.device.is_compiled_with_cuda():
                    return True
            except Exception:
                pass
        try:
            import cv2

            return cv2.cuda.getCudaEnabledDeviceCount() > 0
        except Exception:
            return False
