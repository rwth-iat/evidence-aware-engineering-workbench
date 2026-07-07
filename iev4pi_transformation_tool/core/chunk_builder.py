from __future__ import annotations

from iev4pi_transformation_tool.core.utils import build_header_map, clean_cell, tokenize
from iev4pi_transformation_tool.models import Chunk, DocumentFamily, ParsedDocument


class ChunkBuilder:
    def build(self, parsed: ParsedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        for family in parsed.document.output_families:
            if parsed.sheets:
                chunks.extend(self._sheet_chunks(parsed, family))
            if parsed.pages:
                chunks.extend(self._page_chunks(parsed, family))
            if parsed.ri_package is not None:
                chunks.extend(self._ri_chunks(parsed, family))
            if parsed.ifc_package is not None:
                chunks.extend(self._ifc_chunks(parsed, family))
        return chunks

    def _sheet_chunks(self, parsed: ParsedDocument, family: DocumentFamily) -> list[Chunk]:
        chunks: list[Chunk] = []
        for sheet in parsed.sheets:
            header_map = build_header_map(sheet.rows, sheet.header_rows)
            data_start = max(sheet.header_rows, default=1) + 1
            for row_number, row in enumerate(sheet.rows, start=1):
                if row_number < data_start:
                    continue
                if not any(cell.strip() for cell in row):
                    continue
                parts: list[str] = []
                for column, value in enumerate(row, start=1):
                    cleaned = clean_cell(value)
                    if not cleaned:
                        continue
                    header = header_map.get(column, f"column_{column}")
                    parts.append(f"{header}: {cleaned}")
                if not parts:
                    continue
                text = f"Sheet {sheet.name} row {row_number}. " + "; ".join(parts)
                chunks.append(
                    Chunk(
                        id=f"{parsed.document.relative_path}::{family.value}::{sheet.name}::row{row_number}",
                        document_path=parsed.document.relative_path,
                        family=family,
                        source_kind=parsed.document.source_kind,
                        source_locator=f"{sheet.name}!row{row_number}",
                        text=text,
                        tokens=len(tokenize(text)),
                        metadata={"sheet_name": sheet.name, "row": row_number, "headers": header_map},
                    )
                )
        return chunks

    def _ri_chunks(self, parsed: ParsedDocument, family: DocumentFamily) -> list[Chunk]:
        package = parsed.ri_package
        if package is None:
            return []
        chunks: list[Chunk] = []
        for node in package.xml_nodes:
            if not self._ri_node_matches_family(node.category, family):
                continue
            text = (
                f"DEXPI node {node.category}: {node.tag_name or node.node_id}; "
                f"class {node.class_name}; sub class {node.sub_class}; type {node.normalized_type}"
            )
            chunks.append(
                Chunk(
                    id=f"{parsed.document.relative_path}::{family.value}::dexpi_node::{node.node_id}",
                    document_path=parsed.document.relative_path,
                    family=family,
                    source_kind=parsed.document.source_kind,
                    source_locator=node.locator,
                    text=text,
                    tokens=len(tokenize(text)),
                    metadata={
                        "kind": "dexpi_node",
                        "category": node.category,
                        "node_id": node.node_id,
                        "tag_name": node.tag_name,
                        "class_name": node.class_name,
                        "sub_class": node.sub_class,
                        "normalized_type": node.normalized_type,
                    },
                )
            )
        if family == DocumentFamily.RI_CONNECTION_ROW:
            for edge in package.xml_edges:
                text = f"DEXPI edge {edge.edge_type}: {edge.from_id} -> {edge.to_id}"
                chunks.append(
                    Chunk(
                        id=f"{parsed.document.relative_path}::{family.value}::dexpi_edge::{edge.edge_id}",
                        document_path=parsed.document.relative_path,
                        family=family,
                        source_kind=parsed.document.source_kind,
                        source_locator=edge.locator,
                        text=text,
                        tokens=len(tokenize(text)),
                        metadata={
                            "kind": "dexpi_edge",
                            "edge_type": edge.edge_type,
                            "from_id": edge.from_id,
                            "to_id": edge.to_id,
                            "class_name": edge.class_name,
                            "sub_class": edge.sub_class,
                        },
                    )
                )
        xsd_categories = self._ri_xsd_categories_for_family(family)
        for field_def in package.xsd_field_defs:
            if field_def.category not in xsd_categories:
                continue
            text = f"XSD field {field_def.xml_name}: type {field_def.value_type}"
            if field_def.enumeration_values:
                text += f"; allowed {', '.join(field_def.enumeration_values[:8])}"
            chunks.append(
                Chunk(
                    id=f"{parsed.document.relative_path}::{family.value}::xsd::{field_def.category}::{field_def.name}",
                    document_path=parsed.document.relative_path,
                    family=family,
                    source_kind=parsed.document.source_kind,
                    source_locator=f"xsd::{field_def.category}::{field_def.xml_name}",
                    text=text,
                    tokens=len(tokenize(text)),
                    metadata={
                        "kind": "xsd_field_def",
                        "category": field_def.category,
                        "field_name": field_def.name,
                        "xml_name": field_def.xml_name,
                        "value_type": field_def.value_type,
                        "enumeration_values": field_def.enumeration_values,
                    },
                )
            )
        return chunks

    def _ri_node_matches_family(self, category: str, family: DocumentFamily) -> bool:
        mapping = {
            DocumentFamily.RI_EQUIPMENT_ROW: "equipment",
            DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW: "instrument_function",
            DocumentFamily.RI_PIPING_COMPONENT_ROW: "piping_component",
        }
        return mapping.get(family) == category

    def _ri_xsd_categories_for_family(self, family: DocumentFamily) -> set[str]:
        mapping = {
            DocumentFamily.RI_EQUIPMENT_ROW: {"equipment", "common"},
            DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW: {"instrument_function", "common"},
            DocumentFamily.RI_PIPING_COMPONENT_ROW: {"piping_component", "common"},
            DocumentFamily.RI_CONNECTION_ROW: {"connection", "common"},
        }
        return mapping.get(family, {"common"})

    def _page_chunks(self, parsed: ParsedDocument, family: DocumentFamily) -> list[Chunk]:
        chunks: list[Chunk] = []
        for page in parsed.pages:
            page_text_parts: list[str] = []
            for index, block in enumerate(page.blocks):
                page_text_parts.append(block.text)
                chunks.append(
                    Chunk(
                        id=f"{parsed.document.relative_path}::{family.value}::p{page.page_number}::b{index}",
                        document_path=parsed.document.relative_path,
                        family=family,
                        source_kind=parsed.document.source_kind,
                        source_locator=f"p{page.page_number}@{block.bbox}",
                        text=block.text,
                        tokens=len(tokenize(block.text)),
                        metadata={
                            "page": page.page_number,
                            "bbox": block.bbox,
                            "source": block.source,
                            "score": block.score,
                            "block_type": block.block_type,
                            "engine": block.engine,
                            "reading_order": block.reading_order,
                            "table_id": block.table_id,
                            "row_id": block.row_id,
                            "col_id": block.col_id,
                            "line_id": block.line_id,
                        },
                    )
                )
            for kv_index, pair in enumerate(page.kv_pairs):
                text = f"{pair.key}: {pair.value}"
                chunks.append(
                    Chunk(
                        id=f"{parsed.document.relative_path}::{family.value}::p{page.page_number}::kv{kv_index}",
                        document_path=parsed.document.relative_path,
                        family=family,
                        source_kind=parsed.document.source_kind,
                        source_locator=f"p{page.page_number}@kv{kv_index}",
                        text=text,
                        tokens=len(tokenize(text)),
                        metadata={
                            "page": page.page_number,
                            "kind": "kv_pair",
                            "key": pair.key,
                            "value": pair.value,
                            "engine": pair.engine,
                            "confidence": pair.confidence,
                        },
                    )
                )
            for table in page.tables:
                by_row: dict[int, list[str]] = {}
                for cell in table.cells:
                    cell_text = clean_cell(cell.text)
                    if not cell_text:
                        continue
                    by_row.setdefault(cell.row_id, []).append(f"c{cell.col_id}: {cell_text}")
                    chunks.append(
                        Chunk(
                            id=(
                                f"{parsed.document.relative_path}::{family.value}::"
                                f"p{page.page_number}::{table.table_id}::r{cell.row_id}c{cell.col_id}"
                            ),
                            document_path=parsed.document.relative_path,
                            family=family,
                            source_kind=parsed.document.source_kind,
                            source_locator=f"p{page.page_number}@{table.table_id}:r{cell.row_id}c{cell.col_id}",
                            text=cell_text,
                            tokens=len(tokenize(cell_text)),
                            metadata={
                                "page": page.page_number,
                                "kind": "table_cell",
                                "table_id": table.table_id,
                                "row_id": cell.row_id,
                                "col_id": cell.col_id,
                                "bbox": cell.bbox,
                                "engine": cell.engine,
                                "is_header": cell.is_header,
                            },
                        )
                    )
                for row_id, parts in by_row.items():
                    row_text = "; ".join(parts)
                    chunks.append(
                        Chunk(
                            id=f"{parsed.document.relative_path}::{family.value}::p{page.page_number}::{table.table_id}::row{row_id}",
                            document_path=parsed.document.relative_path,
                            family=family,
                            source_kind=parsed.document.source_kind,
                            source_locator=f"p{page.page_number}@{table.table_id}:row{row_id}",
                            text=row_text,
                            tokens=len(tokenize(row_text)),
                            metadata={
                                "page": page.page_number,
                                "kind": "table_row",
                                "table_id": table.table_id,
                                "row_id": row_id,
                                "engine": table.engine,
                            },
                        )
                    )
            if page.diagram_graph is not None:
                for node in page.diagram_graph.nodes:
                    text = f"Diagram node {node.node_type}: {node.label}"
                    chunks.append(
                        Chunk(
                            id=f"{parsed.document.relative_path}::{family.value}::node::{node.id}",
                            document_path=parsed.document.relative_path,
                            family=family,
                            source_kind=parsed.document.source_kind,
                            source_locator=f"p{page.page_number}@node:{node.id}",
                            text=text,
                            tokens=len(tokenize(text)),
                            metadata={
                                "page": page.page_number,
                                "kind": "diagram_node",
                                "node_type": node.node_type,
                                "label": node.label,
                                "bbox": node.bbox,
                            },
                        )
                    )
                for edge in page.diagram_graph.edges:
                    edge_text = f"Diagram edge {edge.edge_type}: {edge.from_node} -> {edge.to_node}"
                    if edge.label:
                        edge_text += f" label {edge.label}"
                    chunks.append(
                        Chunk(
                            id=f"{parsed.document.relative_path}::{family.value}::edge::{edge.id}",
                            document_path=parsed.document.relative_path,
                            family=family,
                            source_kind=parsed.document.source_kind,
                            source_locator=f"p{page.page_number}@edge:{edge.id}",
                            text=edge_text,
                            tokens=len(tokenize(edge_text)),
                            metadata={
                                "page": page.page_number,
                                "kind": "diagram_edge",
                                "edge_type": edge.edge_type,
                                "from_node": edge.from_node,
                                "to_node": edge.to_node,
                                "label": edge.label,
                                "confidence": edge.confidence,
                            },
                        )
                    )
            if page.structured_diagram is not None:
                for group in page.structured_diagram.groups:
                    text = (
                        f"Structured group {group.group_role}: {group.signal_tag or group.id}; "
                        f"zones {group.zone_path}; parts {' '.join(group.part_ids)}"
                    )
                    chunks.append(
                        Chunk(
                            id=f"{parsed.document.relative_path}::{family.value}::group::{group.id}",
                            document_path=parsed.document.relative_path,
                            family=family,
                            source_kind=parsed.document.source_kind,
                            source_locator=f"p{page.page_number}@group:{group.id}",
                            text=text,
                            tokens=len(tokenize(text)),
                            metadata={
                                "page": page.page_number,
                                "kind": "structured_group",
                                "group_id": group.id,
                                "group_role": group.group_role,
                                "signal_tag": group.signal_tag,
                                "zone_path": group.zone_path,
                                "bbox": group.bbox,
                                "part_ids": group.part_ids,
                            },
                        )
                    )
                for part in page.structured_diagram.parts:
                    text = (
                        f"Structured part {part.component_role}: {part.display_label or part.id}; "
                        f"group {part.group_id}; tag {part.logical_tag}; terminals {' '.join(part.terminal_labels)}"
                    )
                    chunks.append(
                        Chunk(
                            id=f"{parsed.document.relative_path}::{family.value}::part::{part.id}",
                            document_path=parsed.document.relative_path,
                            family=family,
                            source_kind=parsed.document.source_kind,
                            source_locator=f"p{page.page_number}@part:{part.id}",
                            text=text,
                            tokens=len(tokenize(text)),
                            metadata={
                                "page": page.page_number,
                                "kind": "structured_part",
                                "part_id": part.id,
                                "group_id": part.group_id,
                                "role": part.component_role,
                                "display_label": part.display_label,
                                "logical_tag": part.logical_tag,
                                "terminal_labels": part.terminal_labels,
                                "bbox": part.bbox,
                            },
                        )
                    )
                for trace in page.structured_diagram.traces:
                    text = (
                        f"Structured trace {trace.id}: {trace.from_component_id} {trace.from_terminal} -> "
                        f"{trace.to_component_id} {trace.to_terminal}; via {trace.via_component_id} {trace.via_terminal}; "
                        f"label {trace.wire_label}"
                    )
                    chunks.append(
                        Chunk(
                            id=f"{parsed.document.relative_path}::{family.value}::trace::{trace.id}",
                            document_path=parsed.document.relative_path,
                            family=family,
                            source_kind=parsed.document.source_kind,
                            source_locator=f"p{page.page_number}@trace:{trace.id}",
                            text=text,
                            tokens=len(tokenize(text)),
                            metadata={
                                "page": page.page_number,
                                "kind": "structured_trace",
                                "trace_id": trace.id,
                                "group_id": trace.group_id,
                                "from_component_id": trace.from_component_id,
                                "to_component_id": trace.to_component_id,
                                "wire_label": trace.wire_label,
                                "confidence": trace.confidence,
                            },
                        )
                    )
            if page_text_parts:
                joined = " ".join(page_text_parts)
                chunks.append(
                    Chunk(
                        id=f"{parsed.document.relative_path}::{family.value}::p{page.page_number}::summary",
                        document_path=parsed.document.relative_path,
                        family=family,
                        source_kind=parsed.document.source_kind,
                        source_locator=f"p{page.page_number}",
                        text=joined,
                        tokens=len(tokenize(joined)),
                        metadata={"page": page.page_number, "summary": True},
                    )
                )
        return chunks

    def _ifc_chunks(self, parsed: ParsedDocument, family: DocumentFamily) -> list[Chunk]:
        package = parsed.ifc_package
        if package is None:
            return []
        chunks: list[Chunk] = []
        if family == DocumentFamily.IFC_PIPING_ITEM_ROW:
            for node in package.ifc_nodes:
                text = (
                    f"IFC node {node.ifc_class}: {node.tag or node.name or node.node_id}; "
                    f"object type {node.object_type}; predefined {node.predefined_type}; "
                    f"match keys {' '.join(node.match_keys)}"
                )
                chunks.append(
                    Chunk(
                        id=f"{parsed.document.relative_path}::{family.value}::ifc_node::{node.node_id}",
                        document_path=parsed.document.relative_path,
                        family=family,
                        source_kind=parsed.document.source_kind,
                        source_locator=node.locator,
                        text=text,
                        tokens=len(tokenize(text)),
                        metadata={
                            "kind": "ifc_node",
                            "node_id": node.node_id,
                            "ifc_class": node.ifc_class,
                            "tag": node.tag,
                            "name": node.name,
                            "match_keys": node.match_keys,
                            "flange_complete": node.flange_complete,
                        },
                    )
                )
        if family == DocumentFamily.IFC_CONNECTION_ROW:
            for edge in package.ifc_edges:
                text = f"IFC relation {edge.relation_type}: {edge.from_id} -> {edge.to_id}"
                chunks.append(
                    Chunk(
                        id=f"{parsed.document.relative_path}::{family.value}::ifc_edge::{edge.edge_id}",
                        document_path=parsed.document.relative_path,
                        family=family,
                        source_kind=parsed.document.source_kind,
                        source_locator=edge.locator,
                        text=text,
                        tokens=len(tokenize(text)),
                        metadata={
                            "kind": "ifc_edge",
                            "edge_id": edge.edge_id,
                            "from_id": edge.from_id,
                            "to_id": edge.to_id,
                            "relation_type": edge.relation_type,
                        },
                    )
                )
        return chunks
