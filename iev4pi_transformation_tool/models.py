from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from iev4pi_transformation_tool.core.ocr_defaults import get_ocr_platform_defaults


def _default_ocr_backend() -> str:
    return get_ocr_platform_defaults().ocr_backend


def _default_ocr_fallback_backend() -> str:
    return get_ocr_platform_defaults().ocr_fallback_backend


def _default_ocr_device() -> str:
    return get_ocr_platform_defaults().ocr_device


class DocumentFamily(str, Enum):
    STELLEN_OVERVIEW_RECORD = "stellen_overview_record"
    STELLEN_TU_DATASHEET = "stellen_tu_datasheet"
    KLEMMENPLAN_ROW = "klemmenplan_row"
    VERSCHALTUNGSLISTE_ROW = "verschaltungsliste_row"
    CABINET_REFERENCE_ROW = "cabinet_reference_row"
    STROMLAUF_COMPONENT_GROUP = "stromlauf_component_group"
    STROMLAUF_COMPONENT = "stromlauf_component"
    STROMLAUF_CONNECTION = "stromlauf_connection"
    RI_EQUIPMENT_ROW = "ri_equipment_row"
    RI_INSTRUMENT_FUNCTION_ROW = "ri_instrument_function_row"
    RI_PIPING_COMPONENT_ROW = "ri_piping_component_row"
    RI_CONNECTION_ROW = "ri_connection_row"
    IFC_PIPING_ITEM_ROW = "ifc_piping_item_row"
    IFC_CONNECTION_ROW = "ifc_connection_row"
    IFC_3D_ASSEMBLY_STEP = "ifc_3d_assembly_step"
    IFC_3D_ASSEMBLY_CONNECTION = "ifc_3d_assembly_connection"
    IFC_3D_POSITION = "ifc_3d_position"
    IFC_3D_PART_LIBRARY = "ifc_3d_part_library"


class SourceDocumentKind(str, Enum):
    STELLEN_OVERVIEW = "stellen_overview"
    STELLEN_TU = "stellen_tu"
    DEVICE_DATASHEET = "device_datasheet"
    KLEMMENPLAN = "klemmenplan"
    VERSCHALTUNGSLISTE = "verschaltungsliste"
    CABINET_REFERENCE = "cabinet_reference"
    STROMLAUFPLAN = "stromlaufplan"
    RI_FLOWSHEET = "ri_flowsheet"
    IFC_MODEL = "ifc_model"


class ExtractionStatus(str, Enum):
    FILLED = "filled"
    BLANK_NO_EVIDENCE = "blank_no_evidence"
    NEEDS_REVIEW = "needs_review"


class LLMBackendConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    base_url: str = ""
    chat_model: str = "mistralai/Mistral-Small-4-119B-2603"
    vlm_model: str = "mistralai/Mistral-Small-4-119B-2603"
    embedding_model: str = "local-hash-768"
    api_key: str | None = None
    timeout: float = 60.0
    temperature: float = 0.0
    max_retries: int = 1
    parallel_workers: int = 8


class ProjectSettings(BaseModel):
    # 新增阈值用于自动检测图纸是否包含元器件
    # 是否启用自动检测（默认开启）
    auto_diagram_component_detection: bool = True
    # 判定为图纸所需的最少 diagram 节点数（默认 3）
    diagram_min_nodes: int = 3
    # OCR 中匹配的元器件 token 最少数量（默认 5）
    diagram_min_ocr_tokens: int = 5
    model_config = ConfigDict(extra="ignore")

    workspace_root: Path
    input_dirs: list[str] = Field(default_factory=lambda: ["Documents", "Documents-Others"])
    scan_root_dir: str = "Documents"
    database_path: Path
    export_dir: Path
    results_export_dir: Path | None = None
    ui_language: str = "en"
    use_custom_t1_t5_rules: bool = False
    use_custom_tx_rules: bool = False
    ocr_enabled: bool = True
    schema_generation_use_ocr: bool = True
    extraction_use_ocr: bool = True
    ocr_zoom: float = 2.0
    ocr_backend: str = Field(default_factory=_default_ocr_backend)
    ocr_fallback_backend: str = Field(default_factory=_default_ocr_fallback_backend)
    ocr_device: str = Field(default_factory=_default_ocr_device)
    apple_ocr_framework: str = "vision"
    apple_ocr_recognition_level: str = "accurate"
    ocr_dpi: int = 300
    diagram_dpi: int = 400
    diagram_analysis_mode: str = "hybrid"
    diagram_extraction_backend: str = "none"
    ocr_min_confidence: float = 0.82
    ocr_pipeline_mode: str = "fallback"
    ocr_ensemble_backends: list[str] = Field(default_factory=lambda: ["apple", "paddle", "surya", "rapidocr"])
    aio_ml_evidence_linking: bool = False
    aio_ml_evidence_linking_enabled: bool = False
    aio_ml_evidence_linking_benchmark_report: str = ""
    enable_diagram_relation_extraction: bool = True
    enable_hard_page_fallback: bool = True
    retrieval_top_k: int = 6
    review_low_confidence_threshold: float = 0.8
    review_need_review_threshold: float = 0.5
    clear_database_before_extraction: bool = True
    llm: LLMBackendConfig = Field(default_factory=LLMBackendConfig)


class DocumentDescriptor(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    relative_path: str
    extension: str
    source_kind: SourceDocumentKind
    output_families: list[DocumentFamily]
    size_bytes: int
    modified_at: float
    source_root: str = ""
    bundle_id: str | None = None
    bundle_role: str | None = None


class CellData(BaseModel):
    sheet_name: str
    row: int
    column: int
    coord: str
    value: str


class SheetData(BaseModel):
    name: str
    rows: list[list[str]]
    cells: list[CellData]
    header_rows: list[int] = Field(default_factory=list)


class TextBlock(BaseModel):
    page_number: int
    text: str
    bbox: tuple[float, float, float, float]
    source: str = "native_text"
    score: float = 1.0
    confidence: float = 1.0
    engine: str = "pymupdf"
    block_type: str = "text"
    reading_order: int | None = None
    table_id: str | None = None
    row_id: int | None = None
    col_id: int | None = None
    line_id: str | None = None


class LayoutBlock(BaseModel):
    page_number: int
    block_type: str
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float = 1.0
    reading_order: int | None = None
    engine: str = "pymupdf"


class TableCellData(BaseModel):
    table_id: str
    page_number: int
    row_id: int
    col_id: int
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float = 1.0
    engine: str = "pymupdf"
    is_header: bool = False


class TableData(BaseModel):
    table_id: str
    page_number: int
    bbox: tuple[float, float, float, float]
    cells: list[TableCellData] = Field(default_factory=list)
    engine: str = "pymupdf"


class KeyValuePair(BaseModel):
    page_number: int
    key: str
    value: str
    key_bbox: tuple[float, float, float, float] | None = None
    value_bbox: tuple[float, float, float, float] | None = None
    confidence: float = 1.0
    source: str = "native_text"
    engine: str = "pymupdf"


class PageData(BaseModel):
    page_number: int
    blocks: list[TextBlock]
    has_native_text: bool
    used_ocr: bool
    image_size: tuple[int, int] | None = None
    ocr_engine_used: str = ""
    layout_blocks: list[LayoutBlock] = Field(default_factory=list)
    tables: list[TableData] = Field(default_factory=list)
    kv_pairs: list[KeyValuePair] = Field(default_factory=list)
    diagram_graph: "DiagramGraph | None" = None
    structured_diagram: "StructuredDiagramPage | None" = None
    analysis_flags: list[str] = Field(default_factory=list)
    rendered_dpi: int | None = None


class ParsedDocument(BaseModel):
    document: DocumentDescriptor
    sheets: list[SheetData] = Field(default_factory=list)
    pages: list[PageData] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    ri_package: "DexpiPackageData | None" = None
    ifc_package: "IfcPackageData | None" = None


class Chunk(BaseModel):
    id: str
    document_path: str
    family: DocumentFamily
    source_kind: SourceDocumentKind
    source_locator: str
    text: str
    tokens: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class SchemaField(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    family: DocumentFamily
    value_type: str = "string"
    repeatable: bool = False
    normalizer: str | None = None
    extraction_hint: str = ""


class SchemaFamily(BaseModel):
    family: DocumentFamily
    version: int = 1
    display_name: str
    fields: list[SchemaField] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)
    scope_id: str = ""
    source_root: str = ""
    bundle_name: str = ""
    sheet_name: str = ""


class RetrievalHit(BaseModel):
    chunk: Chunk
    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    source_path: str
    page_or_sheet: str
    cell_range_or_bbox: str
    snippet: str
    score: float
    evidence_type: str = "native_text"
    engine: str = ""


class SourceArtifact(BaseModel):
    """Atomic source artifact used as the v0.8 Object evidence layer."""

    artifact_id: str = ""
    source_path: str = ""
    page_or_sheet: str = ""
    bbox: tuple[float, float, float, float] | None = None
    object_type: str = "Text"
    source_operation: str = "Manual_Entry"
    content_text: str = ""
    confidence: float | None = None
    method: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    source_role: str = "Label"
    record_key: str = ""
    field_names: list[str] = Field(default_factory=list)
    requires_review: bool = False
    abstain_reason: str = ""


class EvidenceNode(BaseModel):
    id: str
    document_path: str
    family: DocumentFamily
    source_kind: SourceDocumentKind
    source_locator: str
    text: str
    identifiers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceEdge(BaseModel):
    id: str
    from_node_id: str
    to_node_id: str
    edge_type: str
    score: float = 0.0
    reason: str = ""


class EvidenceGraph(BaseModel):
    nodes: list[EvidenceNode] = Field(default_factory=list)
    edges: list[EvidenceEdge] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    id: str
    query: str
    hits: list[RetrievalHit] = Field(default_factory=list)
    support_evidence_ids: list[str] = Field(default_factory=list)
    contradiction_evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConsistencyDecision(BaseModel):
    decision: str = "needs_review"
    canonical_entity_id: str = ""
    support_evidence_ids: list[str] = Field(default_factory=list)
    contradiction_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    uncertainty_reason: str = ""
    recommended_action: str = ""
    needs_review: bool = True
    evidence_bundle_id: str = ""
    rule_support: list[str] = Field(default_factory=list)
    llm_verification_status: str = ""


class DiagramNode(BaseModel):
    id: str
    node_type: str
    label: str
    bbox: tuple[float, float, float, float]
    page_number: int
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class DiagramEdge(BaseModel):
    id: str
    from_node: str
    to_node: str
    edge_type: str
    polyline: list[tuple[float, float]] = Field(default_factory=list)
    confidence: float = 0.0
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    label: str = ""


class DiagramGraph(BaseModel):
    page_number: int
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)


class TextAssociation(BaseModel):
    id: str
    page_number: int
    target_id: str
    target_type: str
    role: str
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float = 0.0
    source: str = "native_text"
    engine: str = ""


class ComponentPart(BaseModel):
    id: str
    page_number: int
    group_id: str = ""
    parent_component_id: str = ""
    component_role: str = ""
    display_label: str = ""
    logical_tag: str = ""
    article: str = ""
    type_code: str = ""
    channel: str = ""
    address: str = ""
    terminal_labels: list[str] = Field(default_factory=list)
    unit: str = ""
    bbox: tuple[float, float, float, float]
    content_bbox: tuple[float, float, float, float] | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class WireTrace(BaseModel):
    id: str
    page_number: int
    group_id: str = ""
    from_component_id: str = ""
    from_terminal: str = ""
    via_component_id: str = ""
    via_terminal: str = ""
    to_component_id: str = ""
    to_terminal: str = ""
    wire_label: str = ""
    trace_path: list[tuple[float, float]] = Field(default_factory=list)
    confidence: float = 0.0
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ComponentGroup(BaseModel):
    id: str
    page_number: int
    group_role: str = ""
    zone_path: str = ""
    signal_tag: str = ""
    cabinet: str = ""
    bbox: tuple[float, float, float, float]
    part_ids: list[str] = Field(default_factory=list)
    raw_context: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class StructuredDiagramPage(BaseModel):
    page_number: int
    groups: list[ComponentGroup] = Field(default_factory=list)
    parts: list[ComponentPart] = Field(default_factory=list)
    traces: list[WireTrace] = Field(default_factory=list)
    text_associations: list[TextAssociation] = Field(default_factory=list)
    analysis_mode: str = "hybrid"
    ignored_texts: list[str] = Field(default_factory=list)


class ExtractedFieldResult(BaseModel):
    field_name: str
    value: str = ""
    unit: str = ""
    normalized_value: str = ""
    confidence: float = 0.0
    decision_confidence: float | None = None
    status: ExtractionStatus = ExtractionStatus.BLANK_NO_EVIDENCE
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    notes: str = ""
    evidence_bundle_id: str = ""
    uncertainty_reason: str = ""
    llm_verification_status: str = ""
    rule_support: list[str] = Field(default_factory=list)
    review_feedback_status: str = ""


class ExtractedRecord(BaseModel):
    family: DocumentFamily
    source_path: str
    record_key: str
    display_name: str
    results: list[ExtractedFieldResult]
    notes: str = ""
    cross_validation_warnings: list[str] = Field(default_factory=list)
    source_root: str = ""
    scope_id: str = ""
    decision_trace: dict[str, Any] = Field(default_factory=dict)


class ExcelCellProvenance(BaseModel):
    workbook_name: str = ""
    sheet_name: str = ""
    row: int = 0
    column: int = 0
    coord: str = ""
    source_path: str = ""
    record_key: str = ""
    record_display_name: str = ""
    field_name: str = ""
    value: str = ""
    normalized_value: str = ""
    confidence: float = 0.0
    decision_confidence: float | None = None
    status: ExtractionStatus = ExtractionStatus.BLANK_NO_EVIDENCE
    notes: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    evidence_bundle_id: str = ""
    uncertainty_reason: str = ""
    llm_verification_status: str = ""
    rule_support: list[str] = Field(default_factory=list)
    review_feedback_status: str = ""


class ExcelCellTooltipContext(BaseModel):
    workbook_name: str = ""
    sheet_name: str = ""
    row: int = 0
    column: int = 0
    coord: str = ""
    value: str = ""
    field_name: str = ""
    source_type: str = "no_direct_source"
    confidence: float | None = None
    decision_confidence: float | None = None
    extraction_method: str = ""
    source_path: str = ""
    location: str = ""
    current_location: str = ""
    note: str = ""


class ExcelSheetPreview(BaseModel):
    name: str
    rows: list[list[str]] = Field(default_factory=list)
    cell_provenance: dict[str, ExcelCellProvenance] = Field(default_factory=dict)
    tooltip_contexts: dict[str, ExcelCellTooltipContext] = Field(default_factory=dict)


class ExcelWorkbookPreview(BaseModel):
    workbook_name: str
    path: str
    sheets: list[ExcelSheetPreview] = Field(default_factory=list)


class XsdFieldDef(BaseModel):
    name: str
    xml_name: str
    value_type: str = "string"
    description: str = ""
    enumeration_values: list[str] = Field(default_factory=list)
    category: str = ""
    source_path: str = ""


class RiBundle(BaseModel):
    bundle_id: str
    source_root: str
    pdf_path: Path | None = None
    xml_path: Path | None = None
    xsd_path: Path | None = None
    display_name: str = ""
    drawing_name: str = ""
    drawing_title: str = ""
    pairing_score: float = 0.0
    pairing_notes: str = ""
    pairing_status: str = "incomplete"


class DexpiNode(BaseModel):
    node_id: str
    tag_name: str
    class_name: str = ""
    sub_class: str = ""
    category: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    position: tuple[float, float] | None = None
    source_refs: list[EvidenceRef] = Field(default_factory=list)
    locator: str = ""
    normalized_type: str = ""


class DexpiEdge(BaseModel):
    edge_id: str
    from_id: str
    to_id: str
    edge_type: str
    class_name: str = ""
    sub_class: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    source_refs: list[EvidenceRef] = Field(default_factory=list)
    locator: str = ""


class DexpiPackageData(BaseModel):
    bundle_id: str
    pdf_pages: list[int] = Field(default_factory=list)
    xml_nodes: list[DexpiNode] = Field(default_factory=list)
    xml_edges: list[DexpiEdge] = Field(default_factory=list)
    instrument_instances: list["RiInstrumentInstance"] = Field(default_factory=list)
    xsd_field_defs: list[XsdFieldDef] = Field(default_factory=list)
    bundle_metadata: dict[str, Any] = Field(default_factory=dict)
    validation_errors: list[str] = Field(default_factory=list)


class RiInstrumentInstance(BaseModel):
    canonical_tag: str
    function_code: str = ""
    loop_node_id: str = ""
    function_node_id: str = ""
    label_text: str = ""
    full_label: str = ""
    description: str = ""
    piping_anchor_id: str = ""
    from_equipment: str = ""
    to_equipment: str = ""
    context_summary: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class IfcNode(BaseModel):
    node_id: str
    ifc_class: str
    name: str = ""
    tag: str = ""
    object_type: str = ""
    predefined_type: str = ""
    description: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    match_keys: list[str] = Field(default_factory=list)
    flange_complete: bool | None = None
    source_refs: list[EvidenceRef] = Field(default_factory=list)
    locator: str = ""


class IfcEdge(BaseModel):
    edge_id: str
    from_id: str
    to_id: str
    relation_type: str
    attributes: dict[str, str] = Field(default_factory=dict)
    source_refs: list[EvidenceRef] = Field(default_factory=list)
    locator: str = ""


class IfcPackageData(BaseModel):
    document_id: str
    ifc_nodes: list[IfcNode] = Field(default_factory=list)
    ifc_edges: list[IfcEdge] = Field(default_factory=list)
    bundle_metadata: dict[str, Any] = Field(default_factory=dict)
    validation_errors: list[str] = Field(default_factory=list)


class ScanSnapshot(BaseModel):
    documents: list[DocumentDescriptor] = Field(default_factory=list)
    family_counts: dict[str, int] = Field(default_factory=dict)
    source_kind_counts: dict[str, int] = Field(default_factory=dict)
    scan_root: str = ""
    ri_bundles: list[RiBundle] = Field(default_factory=list)


class RunSummary(BaseModel):
    run_id: int
    status: str
    record_count: int
    family_counts: dict[str, int] = Field(default_factory=dict)
    output_dir: str = ""


class ReviewRow(BaseModel):
    family: str
    record_key: str
    display_name: str
    field_name: str
    value: str
    status: str
    confidence: float
    source_path: str
    location: str
    snippet: str
    decision_confidence: float | None = None
    evidence_bundle_id: str = ""
    uncertainty_reason: str = ""
    llm_verification_status: str = ""
    review_feedback_status: str = ""


class ReviewFeedback(BaseModel):
    run_id: int
    record_key: str
    field_name: str
    feedback_status: str
    comment: str = ""
    created_at: str = ""


class PidJumpTarget(BaseModel):
    keyword: str
    preferred_source_root: str = ""
    preferred_scope_id: str = ""
    matching_record_keys: list[str] = Field(default_factory=list)
    preferred_record_key: str = ""


class PidInconsistencyRow(BaseModel):
    component_key: str
    display_name: str
    normalized_key: str
    canonical_tag: str = ""
    primary_type: str = ""
    pdf_status: str = "missing"
    xml_status: str = "missing"
    xsd_status: str = "missing"
    stellenplaene_status: str = "missing"
    verschaltungslisten_status: str = "missing"
    missing_in_stellenplan: bool = False
    missing_in_verschaltung: bool = False
    ifc_match_status: str = "deferred"
    flange_status: str = "unknown"
    ifc_match_key: str = ""
    context_summary: str = ""
    proposal_status: str = "not_applicable"
    aas_generation_status: str = "not_generated"
    recommended_action: str = ""
    is_uc1_candidate: bool = False
    issue_count: int = 0
    issues: list[str] = Field(default_factory=list)
    jump_targets: dict[str, PidJumpTarget] = Field(default_factory=dict)
    scope_id: str = ""
    decision_confidence: float = 0.0
    evidence_bundle_id: str = ""
    uncertainty_reason: str = ""
    llm_verification_status: str = ""
    rule_support: list[str] = Field(default_factory=list)
    review_feedback_status: str = ""
    decision_trace: dict[str, ConsistencyDecision] = Field(default_factory=dict)


class PidInconsistencySummary(BaseModel):
    total_components: int = 0
    problem_component_count: int = 0
    problem_item_count: int = 0
    uc1_candidate_count: int = 0
    rows: list[PidInconsistencyRow] = Field(default_factory=list)
    empty_reason: str = ""


class AASGenerationRequest(BaseModel):
    excel_path: Path
    output_dir: Path
    excel_template_type: str
    source_row_key: str = ""
    aas_template_path: Path | None = None
    mapping_config_path: Path | None = None
    tx_rule_set_id: str = ""
    tx_rule_path: Path | None = None
    target_format: str = "json"


class AASGenerationResult(BaseModel):
    generated_path: Path
    template_type: str
    source_row_key: str = ""
    target_format: str = "json"
    backend: str = ""


class UC1CatalogEntry(BaseModel):
    row_number: int
    document: str = ""
    class_name: str = ""
    data_property: str = ""
    data_type: str = ""
    example: str = ""
    notes: str = ""
    priority: str = "best_effort"


class UC1CatalogCoverageRow(BaseModel):
    row_number: int
    document: str = ""
    class_name: str = ""
    data_property: str = ""
    data_type: str = ""
    example: str = ""
    priority: str = "best_effort"
    coverage_status: str = "missing"
    matched_field: str = ""
    notes: str = ""


class UC1CatalogCoverageReport(BaseModel):
    catalog_path: Path
    total_rows: int = 0
    highlighted_rows: int = 0
    guaranteed_count: int = 0
    partial_count: int = 0
    missing_count: int = 0
    rows: list[UC1CatalogCoverageRow] = Field(default_factory=list)


PageData.model_rebuild()
