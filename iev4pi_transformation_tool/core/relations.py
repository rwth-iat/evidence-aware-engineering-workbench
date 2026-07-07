from __future__ import annotations

from iev4pi_transformation_tool.core.utils import extract_component_tokens
from iev4pi_transformation_tool.models import (
    DiagramEdge,
    DiagramNode,
    DocumentFamily,
    EvidenceRef,
    ExtractedFieldResult,
    ExtractedRecord,
    ExtractionStatus,
    ParsedDocument,
    SchemaFamily,
)


class RelationResolver:
    def resolve(
        self,
        parsed: ParsedDocument,
        schema: SchemaFamily,
        reference_tokens: set[str] | None = None,
    ) -> list[ExtractedRecord]:
        reference_tokens = reference_tokens or set()
        graph_records = self._resolve_from_diagram_graph(parsed, schema, reference_tokens)
        if graph_records:
            return graph_records
        return self._resolve_from_text(parsed, schema, reference_tokens)

    def _resolve_from_diagram_graph(
        self,
        parsed: ParsedDocument,
        schema: SchemaFamily,
        reference_tokens: set[str],
    ) -> list[ExtractedRecord]:
        records: list[ExtractedRecord] = []
        seen_edges: set[tuple[str, str, int]] = set()
        for page in parsed.pages:
            graph = page.diagram_graph
            if graph is None or not graph.edges:
                continue
            node_lookup = {node.id: node for node in graph.nodes}
            for edge in graph.edges:
                left = node_lookup.get(edge.from_node)
                right = node_lookup.get(edge.to_node)
                if left is None or right is None:
                    continue
                edge_key = (left.label, right.label, page.page_number)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                confidence = edge.confidence
                if left.label in reference_tokens:
                    confidence = min(confidence + 0.05, 1.0)
                if right.label in reference_tokens:
                    confidence = min(confidence + 0.05, 1.0)
                status = ExtractionStatus.FILLED if confidence >= 0.7 else ExtractionStatus.NEEDS_REVIEW
                evidence = self._edge_evidence(parsed.document.relative_path, page.page_number, edge)
                values = {
                    "connection_id": edge.id,
                    "group_id": "",
                    "from_component_id": left.label.split("/")[0],
                    "from_terminal": self._pin_from_label(left.label),
                    "via_component_id": "",
                    "via_terminal": "",
                    "to_component_id": right.label.split("/")[0],
                    "to_terminal": self._pin_from_label(right.label),
                    "wire_label": edge.label,
                    "page_number": str(page.page_number),
                    "trace_path": " -> ".join(f"({x:.1f},{y:.1f})" for x, y in edge.polyline),
                    "confidence": f"{confidence:.2f}",
                    "raw_context": self._raw_context(edge, left, right),
                }
                results = []
                for field in schema.fields:
                    value = values.get(field.name, "")
                    field_status = status if value else ExtractionStatus.BLANK_NO_EVIDENCE
                    field_confidence = confidence if value else 0.0
                    results.append(
                        ExtractedFieldResult(
                            field_name=field.name,
                            value=value,
                            normalized_value=value,
                            confidence=field_confidence,
                            status=field_status,
                            evidence_refs=[evidence, *left.evidence_refs[:1], *right.evidence_refs[:1]] if value else [],
                            notes="" if value else "No explicit graph evidence for this field.",
                        )
                    )
                records.append(
                    ExtractedRecord(
                        family=DocumentFamily.STROMLAUF_CONNECTION,
                        source_path=parsed.document.relative_path,
                        record_key=f"{parsed.document.relative_path}::p{page.page_number}::{left.label}->{right.label}",
                        display_name=f"{left.label} -> {right.label}",
                        results=results,
                        notes="Derived from OCR-backed diagram graph evidence.",
                    )
                )
        return records

    def _resolve_from_text(
        self,
        parsed: ParsedDocument,
        schema: SchemaFamily,
        reference_tokens: set[str],
    ) -> list[ExtractedRecord]:
        records: list[ExtractedRecord] = []
        seen_edges: set[tuple[str, str, int]] = set()

        for page in parsed.pages:
            for block in page.blocks:
                tokens = extract_component_tokens(block.text)
                if len(tokens) < 2:
                    continue
                for left, right in zip(tokens, tokens[1:]):
                    edge_key = (left, right, page.page_number)
                    if edge_key in seen_edges:
                        continue
                    seen_edges.add(edge_key)
                    confidence = 0.45
                    if left in reference_tokens:
                        confidence += 0.15
                    if right in reference_tokens:
                        confidence += 0.15
                    if "/" in left or "/" in right or ":" in left or ":" in right:
                        confidence += 0.1
                    if confidence < 0.4:
                        continue
                    status = ExtractionStatus.FILLED if confidence >= 0.6 else ExtractionStatus.NEEDS_REVIEW
                    evidence = EvidenceRef(
                        source_path=parsed.document.relative_path,
                        page_or_sheet=f"Page {page.page_number}",
                        cell_range_or_bbox=str(block.bbox),
                        snippet=block.text[:240],
                        score=confidence,
                        evidence_type=block.source,
                        engine=block.engine,
                    )
                    values = {
                        "connection_id": f"p{page.page_number}:{left}->{right}",
                        "group_id": "",
                        "from_component_id": left.split("/")[0],
                        "from_terminal": self._pin_from_label(left),
                        "via_component_id": "",
                        "via_terminal": "",
                        "to_component_id": right.split("/")[0],
                        "to_terminal": self._pin_from_label(right),
                        "wire_label": "",
                        "page_number": str(page.page_number),
                        "trace_path": "",
                        "confidence": f"{confidence:.2f}",
                        "raw_context": block.text[:240],
                    }
                    results = []
                    for field in schema.fields:
                        value = values.get(field.name, "")
                        field_status = status if value else ExtractionStatus.BLANK_NO_EVIDENCE
                        field_conf = confidence if value else 0.0
                        results.append(
                            ExtractedFieldResult(
                                field_name=field.name,
                                value=value,
                                normalized_value=value,
                                confidence=field_conf,
                                status=field_status,
                                evidence_refs=[evidence] if value else [],
                                notes="" if value else "No explicit evidence for this connection attribute.",
                            )
                        )
                    records.append(
                        ExtractedRecord(
                            family=DocumentFamily.STROMLAUF_CONNECTION,
                            source_path=parsed.document.relative_path,
                            record_key=f"{parsed.document.relative_path}::p{page.page_number}::{left}->{right}",
                            display_name=f"{left} -> {right}",
                            results=results,
                            notes="Auto-derived from co-located drawing text tokens.",
                        )
                    )
        return records

    def _edge_evidence(self, source_path: str, page_number: int, edge: DiagramEdge) -> EvidenceRef:
        polyline = " -> ".join(f"({round(x, 1)},{round(y, 1)})" for x, y in edge.polyline)
        return EvidenceRef(
            source_path=source_path,
            page_or_sheet=f"Page {page_number}",
            cell_range_or_bbox=polyline,
            snippet=f"{edge.from_node} -> {edge.to_node}",
            score=edge.confidence,
            evidence_type="diagram_edge",
            engine="opencv",
        )

    def _pin_from_label(self, label: str) -> str:
        if "/" in label:
            return label.split("/", 1)[1]
        if ":" in label:
            return label.split(":", 1)[1]
        return ""

    def _raw_context(self, edge: DiagramEdge, left: DiagramNode, right: DiagramNode) -> str:
        snippets = [ref.snippet for ref in [*left.evidence_refs[:1], *right.evidence_refs[:1]] if ref.snippet]
        if snippets:
            return " | ".join(snippets)[:240]
        return f"{left.label} -> {right.label} ({edge.edge_type})"
