from __future__ import annotations

from collections import defaultdict

from iev4pi_transformation_tool.core.utils import extract_component_tokens, normalize_identifier
from iev4pi_transformation_tool.models import Chunk, EvidenceEdge, EvidenceGraph, EvidenceNode


class EvidenceGraphBuilder:
    def build(self, chunks: list[Chunk]) -> EvidenceGraph:
        nodes: list[EvidenceNode] = []
        edges: list[EvidenceEdge] = []
        by_identifier: defaultdict[str, list[str]] = defaultdict(list)
        by_document: defaultdict[str, list[str]] = defaultdict(list)

        for chunk in chunks:
            identifiers = self._chunk_identifiers(chunk)
            node = EvidenceNode(
                id=chunk.id,
                document_path=chunk.document_path,
                family=chunk.family,
                source_kind=chunk.source_kind,
                source_locator=chunk.source_locator,
                text=chunk.text,
                identifiers=identifiers,
                tags=extract_component_tokens(chunk.text),
                metadata=dict(chunk.metadata),
            )
            nodes.append(node)
            by_document[node.document_path].append(node.id)
            for identifier in identifiers:
                by_identifier[identifier].append(node.id)

        edge_ids: set[str] = set()
        for document_path, node_ids in by_document.items():
            if len(node_ids) < 2:
                continue
            for left, right in zip(node_ids, node_ids[1:]):
                edge = EvidenceEdge(
                    id=f"{left}::{right}::same_document",
                    from_node_id=left,
                    to_node_id=right,
                    edge_type="same_document",
                    score=0.2,
                    reason=document_path,
                )
                if edge.id not in edge_ids:
                    edges.append(edge)
                    edge_ids.add(edge.id)

        for identifier, node_ids in by_identifier.items():
            if len(node_ids) < 2:
                continue
            unique_ids = list(dict.fromkeys(node_ids))
            for index, left in enumerate(unique_ids):
                for right in unique_ids[index + 1 :]:
                    edge = EvidenceEdge(
                        id=f"{left}::{right}::shared_identifier::{identifier}",
                        from_node_id=left,
                        to_node_id=right,
                        edge_type="shared_identifier",
                        score=1.0,
                        reason=identifier,
                    )
                    if edge.id not in edge_ids:
                        edges.append(edge)
                        edge_ids.add(edge.id)

        return EvidenceGraph(nodes=nodes, edges=edges)

    def _chunk_identifiers(self, chunk: Chunk) -> list[str]:
        values: list[str] = []
        metadata = chunk.metadata or {}
        for key in (
            "tag_name",
            "node_id",
            "key",
            "value",
            "sheet_name",
            "source_row_key",
            "canonical_tag",
            "display_name",
            "record_key",
            "logical_tag",
        ):
            normalized = normalize_identifier(str(metadata.get(key, "") or ""))
            if normalized and normalized not in values:
                values.append(normalized)
        for token in extract_component_tokens(chunk.text):
            normalized = normalize_identifier(token)
            if normalized and normalized not in values:
                values.append(normalized)
        return values
