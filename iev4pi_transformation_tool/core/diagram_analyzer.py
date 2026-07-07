from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass, field
from typing import Iterable

try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np

from iev4pi_transformation_tool.core.utils import (
    bbox_center,
    bbox_contains_point,
    bbox_expand,
    bbox_intersection_area,
    bbox_overlaps,
    bbox_union,
    clean_cell,
    extract_component_tokens,
    normalize_label,
)
from iev4pi_transformation_tool.models import (
    ComponentGroup,
    ComponentPart,
    DiagramEdge,
    DiagramGraph,
    DiagramNode,
    EvidenceRef,
    SourceDocumentKind,
    StructuredDiagramPage,
    TextAssociation,
    TextBlock,
    WireTrace,
)


MODULE_LABEL_RE = re.compile(r"-A\d+(?:-\(0\))?-M\d+\b", re.IGNORECASE)
TERMINAL_LABEL_RE = re.compile(r"^-X\d+[A-Z0-9.-]*$", re.IGNORECASE)
FIELD_VALUE_RE = re.compile(r"^(Art|Typ|Kanal|Adresse)\s*:\s*(.+)$", re.IGNORECASE)
SIGNAL_TAG_RE = re.compile(
    r"[=+.]?[A-Z0-9]+(?:\.[A-Z0-9+\-]+){2,}",
    re.IGNORECASE,
)
PURE_NUMBER_RE = re.compile(r"^\d{1,3}$")

DEVICE_ROLE_CANDIDATE_RE = re.compile(r"^[.]?[A-Z][A-Z0-9+._-]{0,10}$")
WIRE_MARKER_RE = re.compile(r"^[.]?K\d{3,}[+-]?$", re.IGNORECASE)
NETWORK_CONNECTOR_LABEL_RE = re.compile(r"^-[PX]\d+[A-Z0-9.-]*$", re.IGNORECASE)
NETWORK_PORT_LABEL_RE = re.compile(
    r"^(?:AN(?:\d+)?-IN|AN-OUT|0V(?:-AN)?|DIG-IN\d+|AO|PE|U|V|W)$",
    re.IGNORECASE,
)
IGNORE_BLOCK_PATTERNS = (
    "projekt:",
    "projektnr",
    "kunde:",
    "auftrag:",
    "dokument:",
    "blattbeschreibung:",
    "beschreibung",
    "erstellt:",
    "bearb.:",
    "gepr.:",
    "norm:",
    "position:",
    "anlage:",
    "rev.",
    "datum",
)
ZONE_LABELS = {
    "steuerung": "Steuerung",
    "signalanpassung": "Signalanpassung",
    "rangierverteiler": "Rangierverteiler",
    "feldverteiler": "Feldverteiler",
    "umformer": "Umformer",
    "sensor": "Sensor",
    "feld": "Feld",
}


@dataclass
class DiagramAnalysisResult:
    graph: DiagramGraph
    structured_page: StructuredDiagramPage | None = None
    flags: list[str] = field(default_factory=list)


class DiagramAnalyzer:
    def analyze(
        self,
        *,
        image: np.ndarray,
        blocks: list[TextBlock],
        page_number: int,
        source_path: str,
        source_kind: SourceDocumentKind,
        vector_segments: list[tuple[float, float, float, float]] | None = None,
        analysis_mode: str = "hybrid",
    ) -> DiagramAnalysisResult:
        mode = analysis_mode if analysis_mode in {"hybrid", "vector_only", "raster_only"} else "hybrid"
        structured_page = self._build_structured_page(
            image=image,
            blocks=blocks,
            page_number=page_number,
            source_path=source_path,
            vector_segments=vector_segments or [],
            analysis_mode=mode,
        )
        if structured_page is not None and structured_page.groups:
            graph = self._graph_from_structured(structured_page, source_path)
            flags = [
                f"structured_groups:{len(structured_page.groups)}",
                f"structured_parts:{len(structured_page.parts)}",
                f"structured_traces:{len(structured_page.traces)}",
                f"diagram_mode:{mode}",
            ]
            return DiagramAnalysisResult(
                graph=graph,
                structured_page=structured_page,
                flags=flags,
            )

        nodes = self._extract_legacy_nodes(blocks, page_number, source_path, source_kind)
        segments = self._collect_segments(image, vector_segments or [], mode)
        edges = self._segments_to_edges(
            segments,
            nodes,
            page_number=page_number,
            source_path=source_path,
            source_kind=source_kind,
        )
        return DiagramAnalysisResult(
            graph=DiagramGraph(page_number=page_number, nodes=nodes, edges=edges),
            structured_page=None,
            flags=[f"diagram_mode:{mode}", "diagram_fallback:legacy_graph"],
        )

    def _build_structured_page(
        self,
        *,
        image: np.ndarray,
        blocks: list[TextBlock],
        page_number: int,
        source_path: str,
        vector_segments: list[tuple[float, float, float, float]],
        analysis_mode: str,
    ) -> StructuredDiagramPage | None:
        if not blocks:
            return None
        filtered_blocks = self._dedupe_blocks(blocks)
        page_width = float(image.shape[1]) if image.size else 0.0
        page_height = float(image.shape[0]) if image.size else 0.0

        segments = self._collect_segments(image, vector_segments, analysis_mode)
        module_parts = self._dedupe_component_parts(
            self._detect_module_parts(filtered_blocks, segments, page_number, source_path)
        )
        if self._looks_like_terminal_network_page(filtered_blocks, module_parts, page_height):
            network_page = self._build_terminal_network_page(
                blocks=filtered_blocks,
                segments=segments,
                vector_segments=vector_segments,
                page_number=page_number,
                source_path=source_path,
                analysis_mode=analysis_mode,
                page_width=page_width,
                page_height=page_height,
                module_parts=module_parts,
            )
            if network_page is not None and network_page.groups:
                return network_page

        vertical_segments = self._candidate_vertical_segments(segments, page_width, page_height)
        if not vertical_segments:
            return None
        bundles = self._bundle_vertical_segments(vertical_segments)
        if not bundles:
            return None

        zone_titles = self._detect_zone_titles(filtered_blocks)
        terminal_parts = self._dedupe_component_parts(
            self._detect_terminal_parts(filtered_blocks, segments, page_number, source_path)
        )
        device_parts = self._dedupe_component_parts(
            self._detect_device_parts(filtered_blocks, segments, page_number, source_path)
        )
        if not module_parts and not device_parts:
            return None

        all_parts = [*module_parts, *terminal_parts, *device_parts]
        groups: list[ComponentGroup] = []
        traces: list[WireTrace] = []
        associations: list[TextAssociation] = []
        ignored_texts = [
            block.text for block in filtered_blocks if self._is_metadata_text(block.text)
        ]

        used_bundle_ids: set[str] = set()
        used_device_ids: set[str] = set()
        used_terminal_ids: set[str] = set()
        for module in sorted(module_parts, key=lambda item: bbox_center(item.bbox)[0]):
            device_chain = self._device_chain_for_module(
                module,
                device_parts,
                max_dx=340.0,
                used_ids=used_device_ids,
            )
            if not device_chain:
                continue
            device = device_chain[0]
            terminal = self._nearest_terminal_between(
                module,
                device,
                terminal_parts,
                used_terminal_ids,
            )
            bundle = self._bundle_for_pair(module, device, bundles, used_bundle_ids)
            if bundle is None:
                continue
            used_bundle_ids.update(bundle["source_ids"])
            used_device_ids.update(part.id for part in device_chain)
            if terminal is not None:
                used_terminal_ids.add(terminal.id)

            part_ids = [module.id]
            if terminal is not None:
                part_ids.append(terminal.id)
            for device_part in device_chain:
                if device_part.id not in part_ids:
                    part_ids.append(device_part.id)
            for part in all_parts:
                if part.id in part_ids:
                    part.group_id = ""

            module.terminal_labels = self._terminal_labels_for_part(
                module,
                filtered_blocks,
                bundle,
                role="module",
            )
            if terminal is not None:
                terminal.terminal_labels = self._terminal_labels_for_part(
                    terminal,
                    filtered_blocks,
                    bundle,
                    role="terminal",
                )
            for device_part in device_chain:
                device_part.terminal_labels = self._terminal_labels_for_part(
                    device_part,
                    filtered_blocks,
                    bundle,
                    role="device",
                )

            group_signal = self._best_signal_for_bundle(filtered_blocks, bundle, module, device)
            group_bbox = bbox_union(
                [
                    module.bbox,
                    *( [terminal.bbox] if terminal is not None else [] ),
                    *(device_part.bbox for device_part in device_chain),
                    _segment_group_bbox(bundle["segments"]),
                ]
            )
            group_id = self._group_id(page_number, module, device, len(groups))
            for part in all_parts:
                if part.id in part_ids:
                    part.group_id = group_id
                    part.parent_component_id = ""
            if terminal is not None:
                terminal.parent_component_id = module.id
            parent_component_id = terminal.id if terminal is not None else module.id
            for device_part in device_chain:
                device_part.parent_component_id = parent_component_id
                parent_component_id = device_part.id

            group_context = " | ".join(
                value
                for value in [
                    module.display_label,
                    group_signal,
                    terminal.display_label if terminal is not None else "",
                    *[device_part.display_label for device_part in device_chain],
                ]
                if value
            )
            group = ComponentGroup(
                id=group_id,
                page_number=page_number,
                group_role="control_chain",
                zone_path=self._zone_path_for_bbox(zone_titles, group_bbox),
                signal_tag=group_signal,
                cabinet="",
                bbox=group_bbox,
                part_ids=part_ids,
                raw_context=group_context,
                evidence_refs=[
                    *(module.evidence_refs[:1]),
                    *[
                        evidence
                        for device_part in device_chain
                        for evidence in device_part.evidence_refs[:1]
                    ],
                    *(terminal.evidence_refs[:1] if terminal is not None else []),
                ],
            )
            groups.append(group)
            associations.extend(
                self._association_records(group, module, terminal, device_chain)
            )
            traces.extend(
                self._traces_for_device_chain(
                    bundle=bundle,
                    group=group,
                    module=module,
                    terminal=terminal,
                    devices=device_chain,
                    blocks=filtered_blocks,
                    source_path=source_path,
                )
            )

        if not groups:
            return None

        grouped_part_ids = {part_id for group in groups for part_id in group.part_ids}
        relevant_parts = [part for part in all_parts if part.id in grouped_part_ids]
        return StructuredDiagramPage(
            page_number=page_number,
            groups=groups,
            parts=relevant_parts,
            traces=traces,
            text_associations=associations,
            analysis_mode=analysis_mode,
            ignored_texts=ignored_texts,
        )

    def _looks_like_terminal_network_page(
        self,
        blocks: list[TextBlock],
        module_parts: list[ComponentPart],
        page_height: float,
    ) -> bool:
        row_terminals = [
            block
            for block in blocks
            if PURE_NUMBER_RE.fullmatch(clean_cell(block.text))
            and 20 <= int(clean_cell(block.text)) <= 60
            and page_height * 0.46 <= bbox_center(block.bbox)[1] <= page_height * 0.60
        ]
        port_labels = [
            block
            for block in blocks
            if NETWORK_PORT_LABEL_RE.fullmatch(clean_cell(block.text))
            and bbox_center(block.bbox)[1] >= page_height * 0.64
        ]
        connector_labels = [
            block for block in blocks if NETWORK_CONNECTOR_LABEL_RE.fullmatch(clean_cell(block.text))
        ]
        return (
            len(module_parts) >= 4
            and len(row_terminals) >= 8
            and len(port_labels) >= 4
            and len(connector_labels) >= 2
        )

    def _build_terminal_network_page(
        self,
        *,
        blocks: list[TextBlock],
        segments: list[tuple[float, float, float, float]],
        vector_segments: list[tuple[float, float, float, float]],
        page_number: int,
        source_path: str,
        analysis_mode: str,
        page_width: float,
        page_height: float,
        module_parts: list[ComponentPart],
    ) -> StructuredDiagramPage | None:
        connector_parts = self._dedupe_component_parts(
            self._detect_network_connector_parts(blocks, page_number, source_path)
        )
        if not connector_parts:
            return None
        port_parts = self._dedupe_component_parts(
            self._detect_network_port_parts(
                blocks,
                connector_parts,
                page_number,
                source_path,
            )
        )
        row_blocks = self._network_row_terminal_blocks(blocks, page_height)
        if len(row_blocks) < 4:
            return None

        exclude_boxes = [
            (
                bbox[0] + 10.0,
                bbox[1] + 10.0,
                bbox[2] - 10.0,
                bbox[3] - 10.0,
            )
            for bbox in (part.content_bbox or part.bbox for part in module_parts)
            if bbox[2] - bbox[0] > 30.0 and bbox[3] - bbox[1] > 30.0
        ]
        graph = self._build_segment_graph(
            vector_segments or segments,
            page_width=page_width,
            page_height=page_height,
            exclude_boxes=exclude_boxes,
        )
        if not graph["nodes"]:
            return None

        row_bus_blocks = [
            block
            for block in row_blocks
            if 29 <= int(clean_cell(block.text)) <= 42
        ]
        row_bus_part: ComponentPart | None = None
        if row_bus_blocks:
            row_bus_part = ComponentPart(
                id=f"p{page_number}:terminal_row",
                page_number=page_number,
                component_role="terminal_block",
                display_label="Terminal row 29-42",
                terminal_labels=sorted(
                    {clean_cell(block.text) for block in row_bus_blocks},
                    key=_numeric_sort_key,
                ),
                bbox=bbox_union(block.bbox for block in row_bus_blocks),
                content_bbox=bbox_union(block.bbox for block in row_bus_blocks),
                evidence_refs=self._evidence_for_blocks(source_path, page_number, row_bus_blocks),
            )

        x1_part = next(
            (
                part
                for part in connector_parts
                if normalize_label(part.display_label) == "x1"
            ),
            None,
        )
        p4_part = next(
            (
                part
                for part in connector_parts
                if normalize_label(part.display_label) == "p4"
            ),
            None,
        )

        row_entries: list[dict[str, object]] = []
        for block in sorted(
            row_blocks,
            key=lambda item: (int(clean_cell(item.text)), bbox_center(item.bbox)[0]),
        ):
            label = clean_cell(block.text)
            top_anchor = self._graph_anchor_for_block(
                graph,
                block,
                x_tolerance=70.0,
                y_min=bbox_center(block.bbox)[1] - 110.0,
                y_max=bbox_center(block.bbox)[1] + 25.0,
                prefer="min_y",
            )
            bottom_anchor = self._graph_anchor_for_block(
                graph,
                block,
                x_tolerance=70.0,
                y_min=bbox_center(block.bbox)[1],
                y_max=bbox_center(block.bbox)[1] + 520.0,
                prefer="max_y",
            )
            if top_anchor is None and bottom_anchor is None:
                continue
            value = int(label)
            via_part = x1_part if value <= 28 else row_bus_part
            row_entries.append(
                {
                    "label": label,
                    "anchor": top_anchor or bottom_anchor,
                    "top_anchor": top_anchor or bottom_anchor,
                    "bottom_anchor": bottom_anchor or top_anchor,
                    "downstream_anchors": self._graph_column_nodes(
                        graph,
                        target_x=bbox_center(block.bbox)[0],
                        x_tolerance=70.0,
                        min_y=bbox_center(block.bbox)[1],
                        max_y=bbox_center(block.bbox)[1] + 520.0,
                    ),
                    "block": block,
                    "via_part": via_part,
                }
            )
        if not row_entries:
            return None

        port_entries: list[dict[str, object]] = []
        for part in port_parts:
            anchor = self._graph_anchor_for_port_part(graph, part, blocks)
            if anchor is None:
                continue
            port_entries.append(
                {
                    "part": part,
                    "anchor": anchor,
                }
            )
        ordered_p4_ports = [
            item
            for item in sorted(
                port_entries,
                key=lambda item: (
                    bbox_center(item["part"].bbox)[0],
                    bbox_center(item["part"].bbox)[1],
                ),
            )
            if p4_part is not None
            and item["part"].parent_component_id == p4_part.id
            and item["part"].display_label != "0V"
        ]

        module_entries: list[dict[str, object]] = []
        for module in module_parts:
            module_entries.extend(self._terminal_entries_for_module(module, blocks, graph))
        if not module_entries:
            return None

        all_parts = [*module_parts, *connector_parts, *port_parts]
        if row_bus_part is not None:
            all_parts.append(row_bus_part)

        ignored_texts = [block.text for block in blocks if self._is_metadata_text(block.text)]
        groups: list[ComponentGroup] = []
        traces: list[WireTrace] = []
        associations: list[TextAssociation] = []
        group_by_module_id: dict[str, ComponentGroup] = {}
        group_targets: dict[str, list[str]] = {}
        row_tree_cache: dict[tuple[float, float], tuple[dict[tuple[float, float], float], dict[tuple[float, float], tuple[float, float]]]] = {}
        p4_fallback_index = 0

        used_rows: set[str] = set()
        for entry in sorted(
            module_entries,
            key=lambda item: (
                item["anchor"][0],
                item["anchor"][1],
                item["terminal"],
            ),
        ):
            module = entry["module"]
            tree = row_tree_cache.get(entry["anchor"])
            if tree is None:
                tree = self._graph_shortest_tree(graph, entry["anchor"])
                row_tree_cache[entry["anchor"]] = tree
            distances, previous = tree
            row_match = min(
                (
                    candidate
                    for candidate in row_entries
                    if candidate["label"] not in used_rows
                    and candidate["top_anchor"] in distances
                ),
                default=None,
                key=lambda item: (
                    distances[item["top_anchor"]],
                    abs(item["anchor"][0] - entry["anchor"][0]),
                ),
            )
            if row_match is None:
                continue
            row_path = self._reconstruct_graph_path(
                entry["anchor"],
                row_match["top_anchor"],
                previous,
            )
            if not row_path:
                continue
            used_rows.add(row_match["label"])

            via_part = row_match["via_part"]
            destination_part: ComponentPart | None = None
            destination_terminal = row_match["label"]
            final_path = row_path
            via_component_id = via_part.id if via_part is not None else ""
            via_terminal = row_match["label"] if via_part is not None else ""

            if module.article.upper() == "AO":
                destination_part = x1_part or via_part
                if destination_part is None:
                    continue
                destination_terminal = row_match["label"]
                if via_component_id and destination_part.id == via_component_id:
                    via_component_id = ""
                    via_terminal = ""
            else:
                best_port_match: dict[str, object] | None = None
                best_port_path: list[tuple[float, float]] = []
                best_bridge_path: list[tuple[float, float]] = []
                best_port_distance = float("inf")
                for downstream_anchor in row_match["downstream_anchors"] or [row_match["bottom_anchor"]]:
                    row_tree = row_tree_cache.get(downstream_anchor)
                    if row_tree is None:
                        row_tree = self._graph_shortest_tree(graph, downstream_anchor)
                        row_tree_cache[downstream_anchor] = row_tree
                    row_distances, row_previous = row_tree
                    candidate = min(
                        (
                            item
                            for item in port_entries
                            if item["anchor"] in row_distances
                        ),
                        default=None,
                        key=lambda item: (
                            row_distances[item["anchor"]],
                            abs(item["anchor"][0] - downstream_anchor[0]),
                        ),
                    )
                    if candidate is None:
                        continue
                    candidate_distance = row_distances[candidate["anchor"]]
                    if candidate_distance >= best_port_distance:
                        continue
                    best_port_distance = candidate_distance
                    best_port_match = candidate
                    best_bridge_path = self._shortest_graph_path(
                        graph,
                        row_match["top_anchor"],
                        downstream_anchor,
                    )
                    best_port_path = self._reconstruct_graph_path(
                        downstream_anchor,
                        candidate["anchor"],
                        row_previous,
                    )

                if (
                    best_port_match is not None
                    and p4_part is not None
                    and best_port_match["part"].parent_component_id == p4_part.id
                ):
                    destination_part = best_port_match["part"]
                    destination_terminal = _indexed_value(destination_part.terminal_labels, 0)
                    if best_port_path:
                        final_path = self._concat_paths(
                            self._concat_paths(row_path, best_bridge_path),
                            best_port_path,
                        )
                elif ordered_p4_ports:
                    fallback = ordered_p4_ports[min(p4_fallback_index, len(ordered_p4_ports) - 1)]
                    p4_fallback_index += 1
                    destination_part = fallback["part"]
                    destination_terminal = _indexed_value(destination_part.terminal_labels, 0)
                    fallback_anchor = fallback["anchor"]
                    downstream_anchor = min(
                        row_match["downstream_anchors"] or [row_match["bottom_anchor"]],
                        key=lambda anchor: (abs(anchor[0] - fallback_anchor[0]), abs(anchor[1] - fallback_anchor[1])),
                    )
                    bridge_path = self._shortest_graph_path(
                        graph,
                        row_match["top_anchor"],
                        downstream_anchor,
                    )
                    orthogonal_path = [
                        downstream_anchor,
                        (fallback_anchor[0], downstream_anchor[1]),
                        fallback_anchor,
                    ]
                    final_path = self._concat_paths(
                        self._concat_paths(row_path, bridge_path),
                        self._simplify_polyline(orthogonal_path),
                    )
                elif via_part is not None:
                    destination_part = via_part

            if destination_part is None and row_match["label"] in {"25", "26", "27", "28"}:
                destination_part = x1_part or via_part
            elif destination_part is None and via_part is not None:
                destination_part = via_part

            if destination_part is None:
                destination_part = via_part
            if destination_part is None:
                continue
            if via_component_id and destination_part.id == via_component_id:
                via_component_id = ""
                via_terminal = ""

            group = group_by_module_id.get(module.id)
            if group is None:
                group = ComponentGroup(
                    id=f"p{page_number}:netgroup:{len(group_by_module_id)}",
                    page_number=page_number,
                    group_role="terminal_network",
                    zone_path=self._zone_path_for_bbox(self._detect_zone_titles(blocks), module.bbox),
                    signal_tag=module.logical_tag,
                    cabinet="",
                    bbox=module.bbox,
                    part_ids=[module.id],
                    raw_context=module.display_label,
                    evidence_refs=module.evidence_refs[:1],
                )
                module.group_id = group.id
                group_by_module_id[module.id] = group
                group_targets[module.id] = []
                groups.append(group)

            if destination_part.display_label not in group_targets[module.id]:
                group_targets[module.id].append(destination_part.display_label)
            group.raw_context = " | ".join(
                value
                for value in [module.display_label, module.logical_tag, *group_targets[module.id]]
                if value
            )
            group.bbox = bbox_union([group.bbox, destination_part.bbox])

            confidence = 0.84 if destination_part.component_role == "field_port" else 0.8
            evidence = EvidenceRef(
                source_path=source_path,
                page_or_sheet=f"Page {page_number}",
                cell_range_or_bbox=str(final_path),
                snippet=f"{module.display_label} -> {destination_part.display_label}",
                score=confidence,
                evidence_type="structured_trace",
                engine="hybrid-diagram",
            )
            traces.append(
                WireTrace(
                    id=f"{group.id}:trace:{len(traces)}",
                    page_number=page_number,
                    group_id=group.id,
                    from_component_id=module.id,
                    from_terminal=entry["terminal"],
                    via_component_id=via_component_id,
                    via_terminal=via_terminal,
                    to_component_id=destination_part.id,
                    to_terminal=destination_terminal,
                    wire_label=module.logical_tag,
                    trace_path=final_path,
                    confidence=confidence,
                    evidence_refs=[evidence],
                )
            )

        created_keys = {
            (trace.from_component_id, trace.from_terminal)
            for trace in traces
        }
        if x1_part is not None:
            ao_rows = [
                row for row in row_entries if row["label"] in {"25", "26", "27", "28"}
            ]
            used_x1_terminals = {
                trace.to_terminal
                for trace in traces
                if trace.to_component_id == x1_part.id
            }
            for entry in sorted(
                (
                    item
                    for item in module_entries
                    if item["module"].article.upper() == "AO"
                    and (item["module"].id, item["terminal"]) not in created_keys
                ),
                key=lambda item: (item["anchor"][0], item["anchor"][1], item["terminal"]),
            ):
                module = entry["module"]
                tree = row_tree_cache.get(entry["anchor"])
                if tree is None:
                    tree = self._graph_shortest_tree(graph, entry["anchor"])
                    row_tree_cache[entry["anchor"]] = tree
                distances, previous = tree
                row_match = min(
                    (
                        row
                        for row in ao_rows
                        if row["label"] not in used_x1_terminals
                        and row["top_anchor"] in distances
                    ),
                    default=None,
                    key=lambda item: (
                        distances[item["top_anchor"]],
                        abs(item["anchor"][0] - entry["anchor"][0]),
                    ),
                )
                if row_match is None:
                    row_match = min(
                        (
                            row
                            for row in ao_rows
                            if row["label"] not in used_x1_terminals
                        ),
                        default=None,
                        key=lambda item: abs(item["anchor"][0] - entry["anchor"][0]),
                    )
                if row_match is None:
                    continue
                row_path = self._reconstruct_graph_path(
                    entry["anchor"],
                    row_match["top_anchor"],
                    previous,
                ) or self._simplify_polyline(
                    [
                        entry["anchor"],
                        (entry["anchor"][0], row_match["top_anchor"][1]),
                        row_match["top_anchor"],
                    ]
                )
                if not row_path:
                    continue
                group = group_by_module_id.get(module.id)
                if group is None:
                    group = ComponentGroup(
                        id=f"p{page_number}:netgroup:{len(group_by_module_id)}",
                        page_number=page_number,
                        group_role="terminal_network",
                        zone_path=self._zone_path_for_bbox(self._detect_zone_titles(blocks), module.bbox),
                        signal_tag=module.logical_tag,
                        cabinet="",
                        bbox=module.bbox,
                        part_ids=[module.id],
                        raw_context=module.display_label,
                        evidence_refs=module.evidence_refs[:1],
                    )
                    module.group_id = group.id
                    group_by_module_id[module.id] = group
                    group_targets[module.id] = []
                    groups.append(group)
                if x1_part.display_label not in group_targets[module.id]:
                    group_targets[module.id].append(x1_part.display_label)
                group.raw_context = " | ".join(
                    value
                    for value in [module.display_label, module.logical_tag, *group_targets[module.id]]
                    if value
                )
                group.bbox = bbox_union([group.bbox, x1_part.bbox])
                evidence = EvidenceRef(
                    source_path=source_path,
                    page_or_sheet=f"Page {page_number}",
                    cell_range_or_bbox=str(row_path),
                    snippet=f"{module.display_label} -> {x1_part.display_label}",
                    score=0.8,
                    evidence_type="structured_trace",
                    engine="hybrid-diagram",
                )
                traces.append(
                    WireTrace(
                        id=f"{group.id}:trace:{len(traces)}",
                        page_number=page_number,
                        group_id=group.id,
                        from_component_id=module.id,
                        from_terminal=entry["terminal"],
                        via_component_id="",
                        via_terminal="",
                        to_component_id=x1_part.id,
                        to_terminal=row_match["label"],
                        wire_label=module.logical_tag,
                        trace_path=row_path,
                        confidence=0.8,
                        evidence_refs=[evidence],
                    )
                )
                created_keys.add((module.id, entry["terminal"]))
                used_x1_terminals.add(row_match["label"])

        if not groups or not traces:
            return None

        part_by_id = {part.id: part for part in all_parts}
        for group in groups:
            part_by_id[group.part_ids[0]].group_id = group.id

        for part in all_parts:
            associations.append(
                TextAssociation(
                    id=f"{part.id}:assoc",
                    page_number=page_number,
                    target_id=part.id,
                    target_type="part",
                    role="component_label",
                    text=part.display_label,
                    bbox=part.bbox,
                    confidence=0.88,
                    source="hybrid",
                    engine="hybrid-diagram",
                )
            )
        for group in groups:
            if group.signal_tag:
                associations.append(
                    TextAssociation(
                        id=f"{group.id}:signal",
                        page_number=page_number,
                        target_id=group.id,
                        target_type="group",
                        role="signal_tag",
                        text=group.signal_tag,
                        bbox=group.bbox,
                        confidence=0.84,
                        source="hybrid",
                        engine="hybrid-diagram",
                    )
                )

        return StructuredDiagramPage(
            page_number=page_number,
            groups=groups,
            parts=all_parts,
            traces=traces,
            text_associations=associations,
            analysis_mode=analysis_mode,
            ignored_texts=ignored_texts,
        )

    def _dedupe_component_parts(
        self,
        parts: list[ComponentPart],
    ) -> list[ComponentPart]:
        deduped: list[ComponentPart] = []
        for part in sorted(parts, key=lambda item: (item.bbox[1], item.bbox[0], item.id)):
            part_bbox = part.content_bbox or part.bbox
            existing = next(
                (
                    candidate
                    for candidate in deduped
                    if candidate.component_role == part.component_role
                    and normalize_label(candidate.display_label) == normalize_label(part.display_label)
                    and bbox_intersection_area(candidate.content_bbox or candidate.bbox, part_bbox) >= 120.0
                    and math.hypot(
                        bbox_center(candidate.content_bbox or candidate.bbox)[0] - bbox_center(part_bbox)[0],
                        bbox_center(candidate.content_bbox or candidate.bbox)[1] - bbox_center(part_bbox)[1],
                    )
                    <= 70.0
                ),
                None,
            )
            if existing is None:
                deduped.append(part)
                continue
            existing_bbox = existing.content_bbox or existing.bbox
            existing.bbox = bbox_union([existing.bbox, part.bbox])
            existing.content_bbox = bbox_union([existing_bbox, part_bbox])
            existing.terminal_labels = sorted(
                {*(existing.terminal_labels or []), *(part.terminal_labels or [])},
                key=_numeric_sort_key,
            )
            if not existing.logical_tag and part.logical_tag:
                existing.logical_tag = part.logical_tag
            if not existing.article and part.article:
                existing.article = part.article
            if not existing.type_code and part.type_code:
                existing.type_code = part.type_code
            if not existing.channel and part.channel:
                existing.channel = part.channel
            if not existing.address and part.address:
                existing.address = part.address
            if not existing.unit and part.unit:
                existing.unit = part.unit
            existing.evidence_refs = [*existing.evidence_refs, *part.evidence_refs][:6]
        return deduped

    def _detect_network_connector_parts(
        self,
        blocks: list[TextBlock],
        page_number: int,
        source_path: str,
    ) -> list[ComponentPart]:
        parts: list[ComponentPart] = []
        seeds = [
            block
            for block in blocks
            if NETWORK_CONNECTOR_LABEL_RE.fullmatch(clean_cell(block.text))
        ]
        for index, seed in enumerate(sorted(seeds, key=lambda item: (item.bbox[1], item.bbox[0]))):
            center = bbox_center(seed.bbox)
            numeric_blocks = [
                candidate
                for candidate in blocks
                if PURE_NUMBER_RE.fullmatch(clean_cell(candidate.text))
                and abs(bbox_center(candidate.bbox)[1] - center[1]) <= 120.0
                and center[0] - 90.0 <= bbox_center(candidate.bbox)[0] <= center[0] + 1600.0
            ]
            terminal_labels = sorted(
                {
                    token
                    for candidate in numeric_blocks
                    for token in _numeric_tokens(candidate.text)
                },
                key=_numeric_sort_key,
            )
            content_bbox = bbox_union([seed.bbox, *(candidate.bbox for candidate in numeric_blocks)])
            parts.append(
                ComponentPart(
                    id=f"p{page_number}:connector:{index}",
                    page_number=page_number,
                    component_role="terminal_block",
                    display_label=clean_cell(seed.text),
                    terminal_labels=terminal_labels,
                    bbox=content_bbox,
                    content_bbox=content_bbox,
                    evidence_refs=self._evidence_for_blocks(
                        source_path,
                        page_number,
                        [seed, *numeric_blocks],
                    ),
                )
            )
        return parts

    def _detect_network_port_parts(
        self,
        blocks: list[TextBlock],
        connector_parts: list[ComponentPart],
        page_number: int,
        source_path: str,
    ) -> list[ComponentPart]:
        parts: list[ComponentPart] = []
        connector_rows = {
            connector.id: (
                float(
                    np.median(
                        [
                            bbox_center(block.bbox)[1]
                            for block in blocks
                            if PURE_NUMBER_RE.fullmatch(clean_cell(block.text))
                            and connector.content_bbox is not None
                            and connector.content_bbox[0] - 40.0
                            <= bbox_center(block.bbox)[0]
                            <= connector.content_bbox[2] + 1400.0
                            and abs(bbox_center(block.bbox)[1] - bbox_center(connector.bbox)[1]) <= 120.0
                        ]
                    )
                )
                if connector.content_bbox is not None
                and any(
                    PURE_NUMBER_RE.fullmatch(clean_cell(block.text))
                    and connector.content_bbox[0] - 40.0
                    <= bbox_center(block.bbox)[0]
                    <= connector.content_bbox[2] + 1400.0
                    and abs(bbox_center(block.bbox)[1] - bbox_center(connector.bbox)[1]) <= 120.0
                    for block in blocks
                )
                else bbox_center(connector.bbox)[1]
            )
            for connector in connector_parts
        }
        labels = [
            block
            for block in blocks
            if NETWORK_PORT_LABEL_RE.fullmatch(clean_cell(block.text))
        ]
        for index, label_block in enumerate(sorted(labels, key=lambda item: (item.bbox[1], item.bbox[0]))):
            center = bbox_center(label_block.bbox)
            if clean_cell(label_block.text).upper() == "AO" and any(
                clean_cell(candidate.text).upper() == "0V"
                and bbox_intersection_area(label_block.bbox, candidate.bbox) >= 200.0
                for candidate in blocks
            ):
                continue
            connector = min(
                connector_parts,
                default=None,
                key=lambda part: (
                    abs(connector_rows.get(part.id, bbox_center(part.bbox)[1]) - center[1]),
                    abs(bbox_center(part.bbox)[0] - center[0]),
                ),
            )
            if connector is None:
                continue
            pin_candidates = [
                candidate
                for candidate in blocks
                if PURE_NUMBER_RE.fullmatch(clean_cell(candidate.text))
                and abs(bbox_center(candidate.bbox)[0] - center[0]) <= 95.0
                and abs(
                    bbox_center(candidate.bbox)[1]
                    - connector_rows.get(connector.id, bbox_center(connector.bbox)[1])
                )
                <= 95.0
            ]
            if not pin_candidates:
                nearest_pin = min(
                    (
                        candidate
                        for candidate in blocks
                        if PURE_NUMBER_RE.fullmatch(clean_cell(candidate.text))
                        and abs(
                            bbox_center(candidate.bbox)[1]
                            - connector_rows.get(connector.id, bbox_center(connector.bbox)[1])
                        )
                        <= 95.0
                    ),
                    default=None,
                    key=lambda candidate: abs(bbox_center(candidate.bbox)[0] - center[0]),
                )
                pin_candidates = [nearest_pin] if nearest_pin is not None else []
            pin_blocks = [
                min(
                    pin_candidates,
                    key=lambda candidate: (
                        abs(bbox_center(candidate.bbox)[0] - center[0]),
                        abs(bbox_center(candidate.bbox)[1] - center[1]),
                    ),
                )
            ] if pin_candidates else []
            if not pin_blocks:
                continue
            content_bbox = bbox_union([label_block.bbox, *(candidate.bbox for candidate in pin_blocks)])
            parts.append(
                ComponentPart(
                    id=f"p{page_number}:port:{index}",
                    page_number=page_number,
                    component_role="field_port",
                    display_label=clean_cell(label_block.text),
                    parent_component_id=connector.id,
                    terminal_labels=sorted(
                        {
                            token
                            for candidate in pin_blocks
                            for token in _numeric_tokens(candidate.text)
                        },
                        key=_numeric_sort_key,
                    ),
                    bbox=content_bbox,
                    content_bbox=content_bbox,
                    evidence_refs=self._evidence_for_blocks(
                        source_path,
                        page_number,
                        [label_block, *pin_blocks],
                    ),
                )
            )
        return parts

    def _network_row_terminal_blocks(
        self,
        blocks: list[TextBlock],
        page_height: float,
    ) -> list[TextBlock]:
        return [
            block
            for block in blocks
            if PURE_NUMBER_RE.fullmatch(clean_cell(block.text))
            and 20 <= int(clean_cell(block.text)) <= 60
            and page_height * 0.46 <= bbox_center(block.bbox)[1] <= page_height * 0.60
        ]

    def _terminal_entries_for_module(
        self,
        module: ComponentPart,
        blocks: list[TextBlock],
        graph: dict[str, object],
    ) -> list[dict[str, object]]:
        bbox = module.content_bbox or module.bbox
        numeric_blocks = [
            block
            for block in blocks
            if _numeric_tokens(block.text)
            and bbox[0] - 85.0 <= bbox_center(block.bbox)[0] <= bbox[2] + 85.0
            and bbox[3] - 50.0 <= bbox_center(block.bbox)[1] <= bbox[3] + 140.0
        ]
        vertical_columns = self._graph_vertical_columns(
            graph,
            min_x=bbox[0] - 120.0,
            max_x=bbox[2] + 120.0,
            min_y=bbox[3] - 30.0,
            max_y=bbox[3] + 220.0,
        )
        entries: list[dict[str, object]] = []
        seen: set[tuple[str, tuple[float, float]]] = set()
        for block in sorted(numeric_blocks, key=lambda item: bbox_center(item.bbox)[0]):
            tokens = _numeric_tokens(block.text)
            if not tokens:
                continue
            local_columns = [
                column
                for column in vertical_columns
                if block.bbox[0] - 60.0 <= column <= block.bbox[2] + 60.0
            ]
            if len(local_columns) < len(tokens):
                local_columns = [
                    column
                    for column in vertical_columns
                    if block.bbox[0] - 160.0 <= column <= block.bbox[2] + 160.0
                ]
            local_columns = sorted(local_columns) or [bbox_center(block.bbox)[0]]
            if len(local_columns) < len(tokens):
                local_columns = [*local_columns, *([local_columns[-1]] * (len(tokens) - len(local_columns)))]
            for index, token in enumerate(tokens):
                x_hint = local_columns[min(index, len(local_columns) - 1)]
                anchor = self._graph_anchor_near(
                    graph,
                    (x_hint, bbox_center(block.bbox)[1]),
                    x_tolerance=90.0,
                    y_min=bbox[3] - 40.0,
                    y_max=bbox[3] + 260.0,
                )
                if anchor is None:
                    continue
                key = (token, anchor)
                if key in seen:
                    continue
                seen.add(key)
                entries.append(
                    {
                        "module": module,
                        "terminal": token,
                        "anchor": anchor,
                    }
                )
        return entries

    def _build_segment_graph(
        self,
        segments: list[tuple[float, float, float, float]],
        *,
        page_width: float,
        page_height: float,
        exclude_boxes: list[tuple[float, float, float, float]],
    ) -> dict[str, object]:
        normalized = self._normalize_graph_segments(
            segments,
            page_width=page_width,
            page_height=page_height,
            exclude_boxes=exclude_boxes,
        )
        if not normalized:
            return {"nodes": set(), "adj": {}, "vertical_segments": [], "horizontal_segments": []}

        points_by_segment: dict[int, set[tuple[float, float]]] = {
            index: {
                (round(segment[0], 1), round(segment[1], 1)),
                (round(segment[2], 1), round(segment[3], 1)),
            }
            for index, segment in enumerate(normalized)
        }
        vertical = [
            (index, segment)
            for index, segment in enumerate(normalized)
            if abs(segment[0] - segment[2]) < 1.0
        ]
        horizontal = [
            (index, segment)
            for index, segment in enumerate(normalized)
            if abs(segment[1] - segment[3]) < 1.0
        ]
        for vertical_index, v_segment in vertical:
            vx = v_segment[0]
            v_top = v_segment[1]
            v_bottom = v_segment[3]
            for horizontal_index, h_segment in horizontal:
                hy = h_segment[1]
                h_left = h_segment[0]
                h_right = h_segment[2]
                if h_left - 2.0 <= vx <= h_right + 2.0 and v_top - 2.0 <= hy <= v_bottom + 2.0:
                    point = (round(vx, 1), round(hy, 1))
                    points_by_segment[vertical_index].add(point)
                    points_by_segment[horizontal_index].add(point)

        adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]] = {}
        nodes: set[tuple[float, float]] = set()
        for index, segment in enumerate(normalized):
            points = sorted(
                points_by_segment[index],
                key=lambda point: (point[1], point[0]),
            )
            if abs(segment[1] - segment[3]) < 1.0:
                points = sorted(points, key=lambda point: (point[0], point[1]))
            for left, right in zip(points, points[1:]):
                distance = math.hypot(right[0] - left[0], right[1] - left[1])
                if distance < 3.0:
                    continue
                adjacency.setdefault(left, []).append((right, distance))
                adjacency.setdefault(right, []).append((left, distance))
                nodes.add(left)
                nodes.add(right)
        return {
            "nodes": nodes,
            "adj": adjacency,
            "vertical_segments": [segment for _, segment in vertical],
            "horizontal_segments": [segment for _, segment in horizontal],
        }

    def _normalize_graph_segments(
        self,
        segments: list[tuple[float, float, float, float]],
        *,
        page_width: float,
        page_height: float,
        exclude_boxes: list[tuple[float, float, float, float]],
    ) -> list[tuple[float, float, float, float]]:
        normalized: list[tuple[float, float, float, float]] = []
        for x1, y1, x2, y2 in segments:
            if abs(x1 - x2) <= 6.0 and abs(y1 - y2) >= 12.0:
                x = (x1 + x2) / 2.0
                top = min(y1, y2)
                bottom = max(y1, y2)
                if x < 150.0 or x > page_width - 80.0:
                    continue
                if bottom - top > page_height * 0.7 and x < 350.0:
                    continue
                segment = (round(x, 1), round(top, 1), round(x, 1), round(bottom, 1))
            elif abs(y1 - y2) <= 6.0 and abs(x1 - x2) >= 12.0:
                y = (y1 + y2) / 2.0
                left = min(x1, x2)
                right = max(x1, x2)
                if y < 80.0 or y > page_height - 80.0:
                    continue
                if right - left > page_width * 0.65:
                    continue
                segment = (round(left, 1), round(y, 1), round(right, 1), round(y, 1))
            else:
                continue
            midpoint = ((segment[0] + segment[2]) / 2.0, (segment[1] + segment[3]) / 2.0)
            if any(
                bbox[0] <= midpoint[0] <= bbox[2] and bbox[1] <= midpoint[1] <= bbox[3]
                for bbox in exclude_boxes
            ):
                continue
            normalized.append(segment)
        return self._merge_segments(normalized)

    def _graph_vertical_columns(
        self,
        graph: dict[str, object],
        *,
        min_x: float,
        max_x: float,
        min_y: float,
        max_y: float,
    ) -> list[float]:
        values = [
            segment[0]
            for segment in graph["vertical_segments"]
            if min_x <= segment[0] <= max_x
            and segment[1] <= max_y
            and segment[3] >= min_y
        ]
        columns: list[float] = []
        for value in sorted(values):
            if not columns or abs(columns[-1] - value) > 8.0:
                columns.append(value)
        return columns

    def _graph_column_nodes(
        self,
        graph: dict[str, object],
        *,
        target_x: float,
        x_tolerance: float,
        min_y: float,
        max_y: float,
    ) -> list[tuple[float, float]]:
        candidates = [
            node
            for node in graph["nodes"]
            if abs(node[0] - target_x) <= x_tolerance
            and min_y <= node[1] <= max_y
        ]
        ordered: list[tuple[float, float]] = []
        for node in sorted(candidates, key=lambda item: (item[1], item[0])):
            if not ordered or math.hypot(node[0] - ordered[-1][0], node[1] - ordered[-1][1]) > 12.0:
                ordered.append(node)
        return ordered

    def _graph_anchor_for_block(
        self,
        graph: dict[str, object],
        block: TextBlock,
        *,
        x_tolerance: float,
        y_min: float,
        y_max: float,
        prefer: str = "nearest",
    ) -> tuple[float, float] | None:
        center = bbox_center(block.bbox)
        return self._graph_anchor_near(
            graph,
            center,
            x_tolerance=x_tolerance,
            y_min=y_min,
            y_max=y_max,
            prefer=prefer,
        )

    def _graph_anchor_for_port_part(
        self,
        graph: dict[str, object],
        part: ComponentPart,
        blocks: list[TextBlock],
    ) -> tuple[float, float] | None:
        pin_label = _indexed_value(part.terminal_labels, 0)
        pin_block = next(
            (
                block
                for block in blocks
                if clean_cell(block.text) == pin_label
                and abs(bbox_center(block.bbox)[0] - bbox_center(part.bbox)[0]) <= 110.0
                and abs(bbox_center(block.bbox)[1] - part.bbox[1]) <= 140.0
            ),
            None,
        )
        target = bbox_center(pin_block.bbox) if pin_block is not None else bbox_center(part.bbox)
        return self._graph_anchor_near(
            graph,
            target,
            x_tolerance=90.0,
            y_min=target[1] - 120.0,
            y_max=target[1] + 120.0,
            prefer="max_y",
        )

    def _graph_anchor_near(
        self,
        graph: dict[str, object],
        target: tuple[float, float],
        *,
        x_tolerance: float,
        y_min: float,
        y_max: float,
        prefer: str = "nearest",
    ) -> tuple[float, float] | None:
        candidates = [
            node
            for node in graph["nodes"]
            if abs(node[0] - target[0]) <= x_tolerance
            and y_min <= node[1] <= y_max
        ]
        if not candidates:
            return None
        if prefer == "min_y":
            best = min(
                candidates,
                key=lambda node: (node[1], abs(node[0] - target[0])),
            )
        elif prefer == "max_y":
            best = max(
                candidates,
                key=lambda node: (node[1], -abs(node[0] - target[0])),
            )
        else:
            best = min(
                candidates,
                key=lambda node: math.hypot(node[0] - target[0], node[1] - target[1]),
            )
        if prefer == "nearest" and math.hypot(best[0] - target[0], best[1] - target[1]) > max(120.0, x_tolerance + 40.0):
            return None
        return best

    def _graph_shortest_tree(
        self,
        graph: dict[str, object],
        start: tuple[float, float],
    ) -> tuple[dict[tuple[float, float], float], dict[tuple[float, float], tuple[float, float]]]:
        distances: dict[tuple[float, float], float] = {start: 0.0}
        previous: dict[tuple[float, float], tuple[float, float]] = {}
        queue: list[tuple[float, tuple[float, float]]] = [(0.0, start)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances.get(node):
                continue
            for neighbor, weight in graph["adj"].get(node, []):
                candidate = distance + weight
                if candidate < distances.get(neighbor, float("inf")):
                    distances[neighbor] = candidate
                    previous[neighbor] = node
                    heapq.heappush(queue, (candidate, neighbor))
        return distances, previous

    def _shortest_graph_path(
        self,
        graph: dict[str, object],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> list[tuple[float, float]]:
        if start == end:
            return [start]
        distances, previous = self._graph_shortest_tree(graph, start)
        if end not in distances:
            return []
        return self._reconstruct_graph_path(start, end, previous)

    def _reconstruct_graph_path(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        previous: dict[tuple[float, float], tuple[float, float]],
    ) -> list[tuple[float, float]]:
        if start == end:
            return [start]
        if end not in previous:
            return []
        path = [end]
        while path[-1] != start:
            parent = previous.get(path[-1])
            if parent is None:
                return []
            path.append(parent)
        path.reverse()
        return self._simplify_polyline(path)

    def _concat_paths(
        self,
        left: list[tuple[float, float]],
        right: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        if not left:
            return right
        if not right:
            return left
        combined = [*left]
        if combined[-1] == right[0]:
            combined.extend(right[1:])
        else:
            combined.extend(right)
        return self._simplify_polyline(combined)

    def _simplify_polyline(
        self,
        points: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        if len(points) <= 2:
            return points
        simplified = [points[0]]
        for index, point in enumerate(points[1:-1], start=1):
            left = simplified[-1]
            right = points[index + 1]
            if (
                abs(left[0] - point[0]) < 1.0
                and abs(point[0] - right[0]) < 1.0
            ) or (
                abs(left[1] - point[1]) < 1.0
                and abs(point[1] - right[1]) < 1.0
            ):
                continue
            simplified.append(point)
        simplified.append(points[-1])
        return simplified

    def _detect_module_parts(
        self,
        blocks: list[TextBlock],
        segments: list[tuple[float, float, float, float]],
        page_number: int,
        source_path: str,
    ) -> list[ComponentPart]:
        modules: list[ComponentPart] = []
        seeds = [block for block in blocks if MODULE_LABEL_RE.search(block.text)]
        for index, seed in enumerate(sorted(seeds, key=lambda item: (item.bbox[1], item.bbox[0]))):
            seed_center = bbox_center(seed.bbox)
            related = [
                block
                for block in blocks
                if abs(bbox_center(block.bbox)[0] - seed_center[0]) <= 70
                and seed.bbox[1] - 20 <= bbox_center(block.bbox)[1] <= seed.bbox[1] + 380
                and not self._is_metadata_text(block.text)
            ]
            text_parts = [block.text for block in related]
            fields = self._parse_component_fields(text_parts)
            content_bbox = bbox_union(block.bbox for block in related)
            bbox = self._expand_bbox_with_segments(content_bbox, segments)
            display_label = clean_cell(seed.text)
            terminal_labels = sorted(
                {
                    token
                    for block in blocks
                    if content_bbox[0] - 50.0 <= bbox_center(block.bbox)[0] <= content_bbox[2] + 50.0
                    and content_bbox[3] - 28.0 <= bbox_center(block.bbox)[1] <= content_bbox[3] + 110.0
                    for token in _numeric_tokens(block.text)
                },
                key=_numeric_sort_key,
            )
            module_signal = self._pick_best_signal_tag(text_parts)
            modules.append(
                ComponentPart(
                    id=f"p{page_number}:module:{index}",
                    page_number=page_number,
                    component_role="controller_module",
                    display_label=display_label,
                    logical_tag=module_signal,
                    article=fields.get("art", ""),
                    type_code=fields.get("typ", ""),
                    channel=fields.get("kanal", ""),
                    address=fields.get("adresse", ""),
                    terminal_labels=terminal_labels,
                    unit="",
                    bbox=bbox,
                    content_bbox=content_bbox,
                    evidence_refs=self._evidence_for_blocks(source_path, page_number, related),
                )
            )
        return modules

    def _detect_terminal_parts(
        self,
        blocks: list[TextBlock],
        segments: list[tuple[float, float, float, float]],
        page_number: int,
        source_path: str,
    ) -> list[ComponentPart]:
        parts: list[ComponentPart] = []
        for index, block in enumerate(sorted(
            [block for block in blocks if TERMINAL_LABEL_RE.fullmatch(block.text)],
            key=lambda item: (item.bbox[1], item.bbox[0]),
        )):
            center = bbox_center(block.bbox)
            numeric_blocks = [
                candidate
                for candidate in blocks
                if PURE_NUMBER_RE.fullmatch(candidate.text)
                and abs(bbox_center(candidate.bbox)[0] - center[0]) <= 55
                and abs(bbox_center(candidate.bbox)[1] - center[1]) <= 90
            ]
            content_bbox = bbox_union([block.bbox, *(candidate.bbox for candidate in numeric_blocks)])
            bbox = self._expand_bbox_with_segments(content_bbox, segments)
            parts.append(
                ComponentPart(
                    id=f"p{page_number}:terminal:{index}",
                    page_number=page_number,
                    component_role="terminal_block",
                    display_label=block.text,
                    logical_tag="",
                    terminal_labels=sorted(
                        {
                            token
                            for candidate in numeric_blocks
                            for token in _numeric_tokens(candidate.text)
                        },
                        key=_numeric_sort_key,
                    ),
                    bbox=bbox,
                    content_bbox=content_bbox,
                    evidence_refs=self._evidence_for_blocks(
                        source_path,
                        page_number,
                        [block, *numeric_blocks],
                    ),
                )
            )
        return parts

    def _detect_device_parts(
        self,
        blocks: list[TextBlock],
        segments: list[tuple[float, float, float, float]],
        page_number: int,
        source_path: str,
    ) -> list[ComponentPart]:
        candidates = [
            block
            for block in blocks
            if self._looks_like_device_label(block.text)
        ]
        parts: list[ComponentPart] = []
        for index, block in enumerate(sorted(candidates, key=lambda item: (item.bbox[1], item.bbox[0]))):
            center = bbox_center(block.bbox)
            related = [
                candidate
                for candidate in blocks
                if -100.0 <= bbox_center(candidate.bbox)[0] - center[0] <= 260.0
                and abs(bbox_center(candidate.bbox)[1] - center[1]) <= 220.0
                and not self._is_metadata_text(candidate.text)
            ]
            numeric = [
                token
                for candidate in related
                for token in _numeric_tokens(candidate.text)
            ]
            unit = next(
                (candidate.text for candidate in related if "/" in candidate.text or "cm" in candidate.text.lower()),
                "",
            )
            logical_tag = self._pick_best_signal_tag([candidate.text for candidate in related])
            content_bbox = bbox_union(candidate.bbox for candidate in related)
            bbox = self._expand_bbox_with_segments(content_bbox, segments)
            parts.append(
                ComponentPart(
                    id=f"p{page_number}:device:{index}",
                    page_number=page_number,
                    component_role="field_device",
                    display_label=block.text,
                    logical_tag=logical_tag,
                    terminal_labels=sorted(set(numeric), key=_numeric_sort_key),
                    unit=unit,
                    bbox=bbox,
                    content_bbox=content_bbox,
                    evidence_refs=self._evidence_for_blocks(source_path, page_number, related),
                )
            )
        return parts

    def _traces_for_device_chain(
        self,
        *,
        bundle: dict[str, object],
        group: ComponentGroup,
        module: ComponentPart,
        terminal: ComponentPart | None,
        devices: list[ComponentPart],
        blocks: list[TextBlock],
        source_path: str,
    ) -> list[WireTrace]:
        traces: list[WireTrace] = []
        if not devices:
            return traces
        primary_device = devices[0]
        traces.extend(
            self._traces_between_parts(
                bundle=bundle,
                group=group,
                from_part=module,
                from_labels=list(module.terminal_labels),
                via_part=terminal,
                via_labels=list(terminal.terminal_labels) if terminal is not None else [],
                to_part=primary_device,
                to_labels=self._part_side_terminal_labels(primary_device, blocks, side="top")
                or list(primary_device.terminal_labels),
                blocks=blocks,
                source_path=source_path,
                trace_index_offset=len(traces),
                wire_label=group.signal_tag,
            )
        )
        for upper, lower in zip(devices, devices[1:]):
            traces.extend(
                self._traces_between_parts(
                    bundle=bundle,
                    group=group,
                    from_part=upper,
                    from_labels=self._part_side_terminal_labels(upper, blocks, side="bottom")
                    or list(upper.terminal_labels),
                    via_part=None,
                    via_labels=[],
                    to_part=lower,
                    to_labels=self._part_side_terminal_labels(lower, blocks, side="top")
                    or list(lower.terminal_labels),
                    blocks=blocks,
                    source_path=source_path,
                    trace_index_offset=len(traces),
                    wire_label=group.signal_tag,
                )
            )
        return traces

    def _traces_between_parts(
        self,
        *,
        bundle: dict[str, object],
        group: ComponentGroup,
        from_part: ComponentPart,
        from_labels: list[str],
        via_part: ComponentPart | None,
        via_labels: list[str],
        to_part: ComponentPart,
        to_labels: list[str],
        blocks: list[TextBlock],
        source_path: str,
        trace_index_offset: int,
        wire_label: str,
    ) -> list[WireTrace]:
        traces: list[WireTrace] = []
        bundle_segments = list(bundle["segments"])
        bundle_lines = self._line_columns(bundle_segments)
        bundle_bbox = bundle["bbox"]
        min_trace_y = (from_part.content_bbox or from_part.bbox)[1] - 80.0
        max_trace_y = (to_part.content_bbox or to_part.bbox)[3] + 120.0
        numeric_blocks = [
            block
            for block in blocks
            if PURE_NUMBER_RE.fullmatch(block.text)
            and bundle_bbox[0] - 60.0 <= bbox_center(block.bbox)[0] <= bundle_bbox[2] + 60.0
            and min_trace_y <= bbox_center(block.bbox)[1] <= max_trace_y
        ]
        if numeric_blocks:
            preferred_texts = set(via_labels or to_labels or from_labels)
            anchor_part = via_part or to_part or from_part
            anchor_bbox = anchor_part.content_bbox or anchor_part.bbox
            localized_blocks = [
                block
                for block in numeric_blocks
                if block.text in preferred_texts
                and anchor_bbox[1] - 70.0 <= bbox_center(block.bbox)[1] <= anchor_bbox[3] + 70.0
            ]
            trace_blocks = localized_blocks or [
                block for block in numeric_blocks if block.text in preferred_texts
            ] or numeric_blocks
            block_columns: list[float] = []
            for block in sorted(trace_blocks, key=lambda item: bbox_center(item.bbox)[0]):
                center_x = bbox_center(block.bbox)[0]
                if not block_columns or abs(block_columns[-1] - center_x) > 28.0:
                    block_columns.append(center_x)
            expected_count = max(1, len(from_labels), len(via_labels), len(to_labels))
            if len(block_columns) > expected_count:
                anchor_center_x = bbox_center(anchor_bbox)[0]
                block_columns = sorted(
                    sorted(
                        block_columns,
                        key=lambda value: (abs(value - anchor_center_x), value),
                    )[:expected_count]
                )
            bundle_lines = block_columns or [
                column
                for column in bundle_lines
                if any(abs(bbox_center(block.bbox)[0] - column) <= 18.0 for block in numeric_blocks)
            ] or bundle_lines
        numeric_blocks.sort(key=lambda item: bbox_center(item.bbox)[1])
        middle_labels = [
            block.text
            for block in numeric_blocks
            if block.text not in set(from_labels + to_labels)
        ]
        confidence = 0.86 if via_part is not None else 0.82

        trace_count = max(1, len(from_labels), len(via_labels), len(to_labels))
        if numeric_blocks:
            trace_count = max(trace_count, len(bundle_lines))
        for index in range(trace_count):
            line_x = bundle_lines[min(index, len(bundle_lines) - 1)] if bundle_lines else (bundle["bbox"][0] + bundle["bbox"][2]) / 2.0
            trace_path = _segment_trace_path_for_line(bundle_segments, line_x, bundle_bbox)
            from_terminal = _indexed_value(from_labels, index)
            to_terminal = _indexed_value(to_labels, index)
            via_terminal = _indexed_value(via_labels or middle_labels, index) if via_part is not None else ""
            evidence = EvidenceRef(
                source_path=source_path,
                page_or_sheet=f"Page {group.page_number}",
                cell_range_or_bbox=str(trace_path),
                snippet=f"{from_part.display_label} -> {to_part.display_label}",
                score=confidence,
                evidence_type="structured_trace",
                engine="hybrid-diagram",
            )
            traces.append(
                WireTrace(
                    id=f"{group.id}:trace:{trace_index_offset + index}",
                    page_number=group.page_number,
                    group_id=group.id,
                    from_component_id=from_part.id,
                    from_terminal=from_terminal,
                    via_component_id=via_part.id if via_part is not None else "",
                    via_terminal=via_terminal,
                    to_component_id=to_part.id,
                    to_terminal=to_terminal,
                    wire_label=wire_label,
                    trace_path=trace_path,
                    confidence=confidence,
                    evidence_refs=[evidence],
                )
            )
        return traces

    def _terminal_labels_for_part(
        self,
        part: ComponentPart,
        blocks: list[TextBlock],
        bundle: dict[str, object],
        *,
        role: str,
    ) -> list[str]:
        bundle_bbox = bundle["bbox"]
        anchor_bbox = part.content_bbox or part.bbox
        if role == "module":
            min_x = anchor_bbox[0] - 50.0
            max_x = anchor_bbox[2] + 50.0
            min_y = anchor_bbox[3] - 28.0
            max_y = anchor_bbox[3] + 120.0
        elif role == "terminal":
            min_x = bundle_bbox[0] - 50.0
            max_x = bundle_bbox[2] + 50.0
            min_y = anchor_bbox[1] - 120.0
            max_y = anchor_bbox[3] + 120.0
        else:
            min_x = anchor_bbox[0] - 60.0
            max_x = anchor_bbox[2] + 60.0
            min_y = anchor_bbox[1] - 80.0
            max_y = anchor_bbox[3] + 80.0
        values = {
            token
            for block in blocks
            if min_x <= bbox_center(block.bbox)[0] <= max_x
            and min_y <= bbox_center(block.bbox)[1] <= max_y
            for token in _numeric_tokens(block.text)
        }
        return sorted(values, key=_numeric_sort_key)

    def _part_side_terminal_labels(
        self,
        part: ComponentPart,
        blocks: list[TextBlock],
        *,
        side: str,
    ) -> list[str]:
        anchor_bbox = part.content_bbox or part.bbox
        midpoint_y = (anchor_bbox[1] + anchor_bbox[3]) / 2.0
        min_x = anchor_bbox[0] - 80.0
        max_x = anchor_bbox[2] + 80.0
        if side == "top":
            min_y = anchor_bbox[1] - 100.0
            max_y = midpoint_y + 20.0
        else:
            min_y = midpoint_y - 20.0
            max_y = anchor_bbox[3] + 120.0
        values = {
            token
            for block in blocks
            if min_x <= bbox_center(block.bbox)[0] <= max_x
            and min_y <= bbox_center(block.bbox)[1] <= max_y
            for token in _numeric_tokens(block.text)
        }
        return sorted(values, key=_numeric_sort_key)

    def _association_records(
        self,
        group: ComponentGroup,
        module: ComponentPart,
        terminal: ComponentPart | None,
        devices: list[ComponentPart],
    ) -> list[TextAssociation]:
        associations = [
            TextAssociation(
                id=f"{module.id}:assoc",
                page_number=group.page_number,
                target_id=module.id,
                target_type="part",
                role="component_label",
                text=module.display_label,
                bbox=module.bbox,
                confidence=0.9,
                source="hybrid",
                engine="hybrid-diagram",
            ),
        ]
        for device in devices:
            associations.append(
                TextAssociation(
                    id=f"{device.id}:assoc",
                    page_number=group.page_number,
                    target_id=device.id,
                    target_type="part",
                    role="component_label",
                    text=device.display_label,
                    bbox=device.bbox,
                    confidence=0.9,
                    source="hybrid",
                    engine="hybrid-diagram",
                )
            )
        if terminal is not None:
            associations.append(
                TextAssociation(
                    id=f"{terminal.id}:assoc",
                    page_number=group.page_number,
                    target_id=terminal.id,
                    target_type="part",
                    role="component_label",
                    text=terminal.display_label,
                    bbox=terminal.bbox,
                    confidence=0.88,
                    source="hybrid",
                    engine="hybrid-diagram",
                )
            )
        if group.signal_tag:
            associations.append(
                TextAssociation(
                    id=f"{group.id}:signal",
                    page_number=group.page_number,
                    target_id=group.id,
                    target_type="group",
                    role="signal_tag",
                    text=group.signal_tag,
                    bbox=group.bbox,
                    confidence=0.85,
                    source="hybrid",
                    engine="hybrid-diagram",
                )
            )
        return associations

    def _graph_from_structured(
        self, structured_page: StructuredDiagramPage, source_path: str
    ) -> DiagramGraph:
        nodes: list[DiagramNode] = []
        for part in structured_page.parts:
            snippet = part.logical_tag or part.display_label or part.id
            nodes.append(
                DiagramNode(
                    id=part.id,
                    node_type=part.component_role or "component",
                    label=part.display_label or part.logical_tag or part.id,
                    bbox=part.bbox,
                    page_number=structured_page.page_number,
                    evidence_refs=part.evidence_refs
                    or [
                        EvidenceRef(
                            source_path=source_path,
                            page_or_sheet=f"Page {structured_page.page_number}",
                            cell_range_or_bbox=str(part.bbox),
                            snippet=snippet[:240],
                            score=0.8,
                            evidence_type="structured_part",
                            engine="hybrid-diagram",
                        )
                    ],
                )
            )

        edges: list[DiagramEdge] = []
        for index, trace in enumerate(structured_page.traces):
            left = trace.from_component_id
            right = trace.to_component_id or trace.via_component_id
            if not left or not right or left == right:
                continue
            edges.append(
                DiagramEdge(
                    id=f"p{structured_page.page_number}:trace_edge:{index}",
                    from_node=left,
                    to_node=right,
                    edge_type="wired_to",
                    polyline=trace.trace_path,
                    confidence=trace.confidence,
                    evidence_refs=trace.evidence_refs,
                    label=trace.wire_label,
                )
            )
        return DiagramGraph(
            page_number=structured_page.page_number,
            nodes=nodes,
            edges=edges,
        )

    def _extract_legacy_nodes(
        self,
        blocks: list[TextBlock],
        page_number: int,
        source_path: str,
        source_kind: SourceDocumentKind,
    ) -> list[DiagramNode]:
        nodes_by_label: dict[str, DiagramNode] = {}
        for block in blocks:
            tokens = extract_component_tokens(block.text)
            if not tokens:
                continue
            for token in tokens:
                label = clean_cell(token)
                if not label:
                    continue
                node_type = self._classify_label(label, source_kind)
                evidence = EvidenceRef(
                    source_path=source_path,
                    page_or_sheet=f"Page {page_number}",
                    cell_range_or_bbox=str(block.bbox),
                    snippet=block.text[:240],
                    score=max(block.confidence, 0.4),
                    evidence_type=block.source,
                    engine=block.engine,
                )
                existing = nodes_by_label.get(label)
                if existing is None or evidence.score > (
                    existing.evidence_refs[0].score if existing.evidence_refs else 0.0
                ):
                    nodes_by_label[label] = DiagramNode(
                        id=f"p{page_number}:{label}",
                        node_type=node_type,
                        label=label,
                        bbox=block.bbox,
                        page_number=page_number,
                        evidence_refs=[evidence],
                    )
        return list(nodes_by_label.values())

    def _collect_segments(
        self,
        image: np.ndarray,
        vector_segments: list[tuple[float, float, float, float]],
        analysis_mode: str,
    ) -> list[tuple[float, float, float, float]]:
        segments: list[tuple[float, float, float, float]] = []
        if analysis_mode != "raster_only":
            segments.extend(vector_segments)
        if analysis_mode != "vector_only":
            segments.extend(self._detect_raster_segments(image))
        return self._merge_segments(segments)

    def _detect_raster_segments(
        self, image: np.ndarray
    ) -> list[tuple[float, float, float, float]]:
        if cv2 is None or image.size == 0:
            return []
        grayscale = (
            cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
        )
        blurred = cv2.GaussianBlur(grayscale, (3, 3), 0)
        _, binary = cv2.threshold(
            blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )
        lines = cv2.HoughLinesP(
            binary,
            rho=1,
            theta=np.pi / 180,
            threshold=40,
            minLineLength=max(24, image.shape[1] // 25),
            maxLineGap=max(6, image.shape[1] // 150),
        )
        if lines is None:
            return []
        segments: list[tuple[float, float, float, float]] = []
        for line in lines[:, 0, :]:
            x1, y1, x2, y2 = [float(value) for value in line]
            if math.hypot(x2 - x1, y2 - y1) < 20:
                continue
            segments.append((x1, y1, x2, y2))
        return segments

    def _candidate_vertical_segments(
        self,
        segments: list[tuple[float, float, float, float]],
        page_width: float,
        page_height: float,
    ) -> list[tuple[float, float, float, float]]:
        filtered: list[tuple[float, float, float, float]] = []
        for segment in segments:
            x1, y1, x2, y2 = segment
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            if dy < max(70.0, dx * 4.0):
                continue
            x = (x1 + x2) / 2.0
            top = min(y1, y2)
            bottom = max(y1, y2)
            if x <= 32 or x >= page_width - 32:
                continue
            if top <= 36 and bottom >= page_height - 36:
                continue
            filtered.append((x, top, x, bottom))
        return self._merge_segments(filtered)

    def _bundle_vertical_segments(
        self, segments: list[tuple[float, float, float, float]]
    ) -> list[dict[str, object]]:
        bundles: list[dict[str, object]] = []
        for segment in sorted(segments, key=lambda item: ((item[0] + item[2]) / 2.0, item[1])):
            center_x = (segment[0] + segment[2]) / 2.0
            assigned = False
            for bundle in bundles:
                if abs(center_x - bundle["center_x"]) <= 26:
                    bundle["segments"].append(segment)
                    bundle["center_x"] = sum(
                        ((item[0] + item[2]) / 2.0) for item in bundle["segments"]
                    ) / len(bundle["segments"])
                    assigned = True
                    break
            if not assigned:
                bundles.append(
                    {
                        "id": f"bundle:{len(bundles)}",
                        "center_x": center_x,
                        "segments": [segment],
                    }
                )
        bundles = [
            bundle
            for bundle in bundles
            if _segment_group_height(bundle["segments"]) >= 120
        ]
        for bundle in bundles:
            bundle["bbox"] = _segment_group_bbox(bundle["segments"])
        return bundles

    def _nearest_part_by_x(
        self,
        module: ComponentPart,
        candidates: list[ComponentPart],
        *,
        max_dx: float,
        used_ids: set[str],
    ) -> ComponentPart | None:
        center_x = bbox_center(module.bbox)[0]
        best_part: ComponentPart | None = None
        best_score = float("-inf")
        for candidate in candidates:
            if candidate.id in used_ids:
                continue
            candidate_center = bbox_center(candidate.bbox)
            if abs(candidate_center[0] - center_x) > max_dx:
                continue
            if candidate_center[1] <= bbox_center(module.bbox)[1]:
                continue
            score = 0.0
            score -= abs(candidate_center[0] - center_x)
            score += candidate_center[1]
            if candidate.component_role == "field_device":
                score += 40.0
            if score > best_score:
                best_part = candidate
                best_score = score
        return best_part

    def _device_chain_for_module(
        self,
        module: ComponentPart,
        candidates: list[ComponentPart],
        *,
        max_dx: float,
        used_ids: set[str],
    ) -> list[ComponentPart]:
        module_center = bbox_center(module.bbox)
        aligned = [
            candidate
            for candidate in candidates
            if candidate.id not in used_ids
            and abs(bbox_center(candidate.bbox)[0] - module_center[0]) <= max_dx
            and bbox_center(candidate.bbox)[1] > module_center[1]
        ]
        if not aligned:
            return []
        aligned.sort(
            key=lambda item: (
                bbox_center(item.bbox)[1],
                abs(bbox_center(item.bbox)[0] - module_center[0]),
            )
        )
        chain = [aligned[0]]
        chain_used = {aligned[0].id}
        while True:
            previous = chain[-1]
            previous_center = bbox_center(previous.bbox)
            next_device = min(
                (
                    candidate
                    for candidate in aligned
                    if candidate.id not in chain_used
                    and bbox_center(candidate.bbox)[1] > previous_center[1] + 40.0
                    and abs(bbox_center(candidate.bbox)[0] - previous_center[0]) <= 180.0
                ),
                default=None,
                key=lambda item: (
                    bbox_center(item.bbox)[1] - previous_center[1],
                    abs(bbox_center(item.bbox)[0] - previous_center[0]),
                ),
            )
            if next_device is None:
                break
            if bbox_center(next_device.bbox)[1] - previous_center[1] > 900.0:
                break
            chain.append(next_device)
            chain_used.add(next_device.id)
        return chain

    def _nearest_terminal_between(
        self,
        module: ComponentPart,
        device: ComponentPart,
        terminals: list[ComponentPart],
        used_ids: set[str],
    ) -> ComponentPart | None:
        module_center = bbox_center(module.bbox)
        device_center = bbox_center(device.bbox)
        min_x = min(module_center[0], device_center[0]) - 120.0
        max_x = max(module_center[0], device_center[0]) + 120.0
        min_y = module.bbox[3]
        max_y = device.bbox[1] + 60.0
        best_part: ComponentPart | None = None
        best_score = float("-inf")
        for terminal in terminals:
            if terminal.id in used_ids:
                continue
            center = bbox_center(terminal.bbox)
            if not (min_x <= center[0] <= max_x and min_y <= center[1] <= max_y):
                continue
            score = 100.0
            score -= abs(center[0] - device_center[0])
            score -= abs(center[1] - ((min_y + max_y) / 2.0))
            if score > best_score:
                best_part = terminal
                best_score = score
        return best_part

    def _bundle_for_pair(
        self,
        module: ComponentPart,
        device: ComponentPart,
        bundles: list[dict[str, object]],
        used_bundle_ids: set[str],
    ) -> dict[str, object] | None:
        module_center = bbox_center(module.bbox)
        device_center = bbox_center(device.bbox)
        min_x = min(module_center[0], device_center[0]) - 120.0
        max_x = max(module_center[0], device_center[0]) + 120.0
        selected = [
            bundle
            for bundle in bundles
            if bundle["id"] not in used_bundle_ids
            and min_x <= float(bundle["center_x"]) <= max_x
            and float(bundle["bbox"][3]) >= module.bbox[3]
        ]
        if not selected:
            midpoint = (module_center[0] + device_center[0]) / 2.0
            nearest = min(
                (bundle for bundle in bundles if bundle["id"] not in used_bundle_ids),
                default=None,
                key=lambda item: abs(float(item["center_x"]) - midpoint),
            )
            if nearest is None:
                return None
            selected = [nearest]
        segments: list[tuple[float, float, float, float]] = []
        source_ids: list[str] = []
        for bundle in selected:
            segments.extend(bundle["segments"])
            source_ids.append(bundle["id"])
        return {
            "id": "+".join(source_ids),
            "center_x": sum(float(bundle["center_x"]) for bundle in selected) / len(selected),
            "segments": segments,
            "bbox": _segment_group_bbox(segments),
            "source_ids": source_ids,
        }

    def _best_signal_for_bundle(
        self,
        blocks: list[TextBlock],
        bundle: dict[str, object],
        module: ComponentPart,
        device: ComponentPart,
    ) -> str:
        if module.logical_tag:
            return module.logical_tag
        if device.logical_tag:
            return device.logical_tag
        bundle_bbox = bbox_expand(bundle["bbox"], 70.0, 30.0)
        related = [
            block.text
            for block in blocks
            if SIGNAL_TAG_RE.search(block.text)
            and (
                bbox_overlaps(block.bbox, bundle_bbox, min_area=1.0)
                or bbox_contains_point(bundle_bbox, bbox_center(block.bbox))
            )
            and not self._is_metadata_text(block.text)
        ]
        if related:
            preferred = [
                text
                for text in related
                if ".fic." in normalize_label(text) or ".tu" in normalize_label(text)
            ]
            return max(preferred or related, key=lambda item: (len(item), item.count(".")))
        return ""

    def _zone_path_for_bbox(
        self,
        zone_titles: list[tuple[str, tuple[float, float, float, float]]],
        bbox: tuple[float, float, float, float],
    ) -> str:
        labels: list[str] = []
        for label, title_bbox in zone_titles:
            center_y = bbox_center(title_bbox)[1]
            if bbox[1] - 18 <= center_y <= bbox[3] + 18 and label not in labels:
                labels.append(label)
        return " > ".join(labels)

    def _detect_zone_titles(
        self, blocks: list[TextBlock]
    ) -> list[tuple[str, tuple[float, float, float, float]]]:
        titles: list[tuple[str, tuple[float, float, float, float]]] = []
        for block in blocks:
            normalized = normalize_label(block.text).replace(" ", "")
            label = ZONE_LABELS.get(normalized)
            if label is None:
                continue
            titles.append((label, block.bbox))
        titles.sort(key=lambda item: item[1][1])
        return titles

    def _parse_component_fields(self, texts: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for text in texts:
            cleaned = clean_cell(text)
            match = FIELD_VALUE_RE.match(cleaned)
            if match:
                result[match.group(1).lower()] = clean_cell(match.group(2))
                continue
            if cleaned in {"VW", "Siemens", "Turck"} and "art" not in result:
                result["manufacturer"] = cleaned
        return result

    def _pick_best_signal_tag(self, texts: list[str]) -> str:
        matches = [
            match.group(0)
            for text in texts
            for match in SIGNAL_TAG_RE.finditer(text)
            if not self._is_metadata_text(match.group(0))
        ]
        if not matches:
            return ""
        preferred = [
            value for value in matches if ".fic." in normalize_label(value) or ".tu" in normalize_label(value)
        ]
        return max(preferred or matches, key=lambda item: (len(item), item.count(".")))

    def _expand_bbox_with_segments(
        self,
        bbox: tuple[float, float, float, float],
        segments: list[tuple[float, float, float, float]],
    ) -> tuple[float, float, float, float]:
        nearby = [
            _segment_bbox(segment)
            for segment in segments
            if bbox_intersection_area(bbox_expand(bbox, 18.0, 22.0), _segment_bbox(segment)) > 0
        ]
        if not nearby:
            return bbox
        return bbox_union([bbox, *nearby])

    def _evidence_for_blocks(
        self,
        source_path: str,
        page_number: int,
        blocks: list[TextBlock],
    ) -> list[EvidenceRef]:
        evidences: list[EvidenceRef] = []
        for block in blocks:
            evidences.append(
                EvidenceRef(
                    source_path=source_path,
                    page_or_sheet=f"Page {page_number}",
                    cell_range_or_bbox=str(block.bbox),
                    snippet=block.text[:240],
                    score=max(block.confidence, 0.55),
                    evidence_type=block.source,
                    engine=block.engine,
                )
            )
        return evidences[:4]

    def _looks_like_device_label(self, text: str) -> bool:
        cleaned = clean_cell(text)
        normalized = normalize_label(cleaned)
        if not cleaned or self._is_metadata_text(cleaned):
            return False
        if MODULE_LABEL_RE.search(cleaned) or TERMINAL_LABEL_RE.fullmatch(cleaned):
            return False
        if PURE_NUMBER_RE.fullmatch(cleaned):
            return False
        if FIELD_VALUE_RE.match(cleaned):
            return False
        if WIRE_MARKER_RE.fullmatch(cleaned):
            return False
        if NETWORK_PORT_LABEL_RE.fullmatch(cleaned):
            return False
        if SIGNAL_TAG_RE.fullmatch(cleaned):
            return False
        if cleaned in {"A", "B", "C", "D", "E", "VW", "F"}:
            return False
        if cleaned.isalpha() and len(cleaned) > 2:
            return False
        if re.fullmatch(r"[A-Za-z.]+", cleaned) and len(cleaned.strip(".")) > 2:
            return False
        if cleaned.upper().startswith(".TU"):
            return False
        if normalized in {"martina", "prozesstechnik", "technikumsanlage", "pumpwerk"}:
            return False
        if DEVICE_ROLE_CANDIDATE_RE.fullmatch(cleaned):
            return True
        return normalized.startswith("a b") or normalized.startswith("b")

    def _is_metadata_text(self, text: str) -> bool:
        lowered = clean_cell(text).lower()
        if not lowered:
            return True
        if any(pattern in lowered for pattern in IGNORE_BLOCK_PATTERNS):
            return True
        if lowered in {"0", "1", "2", "3", "4", "5", "6", "a", "b", "c", "d", "e"}:
            return True
        if lowered.startswith("=+10.l001") or lowered.startswith("=+10.o001.msr"):
            return True
        if lowered.startswith("comos"):
            return True
        return False

    def _dedupe_blocks(self, blocks: list[TextBlock]) -> list[TextBlock]:
        seen: dict[tuple[str, int, int], TextBlock] = {}
        for block in blocks:
            normalized = normalize_label(block.text)
            center = bbox_center(block.bbox)
            key = (normalized, round(center[0] / 6), round(center[1] / 6))
            existing = seen.get(key)
            if existing is None or block.confidence > existing.confidence:
                seen[key] = block
        return sorted(
            seen.values(),
            key=lambda item: (item.bbox[1], item.bbox[0], -(item.confidence or 0.0)),
        )

    def _line_columns(
        self, segments: Iterable[tuple[float, float, float, float]]
    ) -> list[float]:
        values = sorted({round((item[0] + item[2]) / 2.0, 1) for item in segments})
        columns: list[float] = []
        for value in values:
            if not columns or abs(columns[-1] - value) > 10:
                columns.append(value)
        return columns

    def _line_matches_bundle(
        self, x: float, line_columns: list[float], *, tolerance: float
    ) -> bool:
        return any(abs(column - x) <= tolerance for column in line_columns)

    def _nearest_numeric_to_line(
        self, line_x: float, blocks: list[TextBlock]
    ) -> str:
        if not blocks:
            return ""
        best = min(blocks, key=lambda item: abs(bbox_center(item.bbox)[0] - line_x))
        return best.text

    def _group_id(
        self,
        page_number: int,
        module: ComponentPart,
        device: ComponentPart,
        index: int,
    ) -> str:
        base = normalize_label(module.display_label or device.display_label).replace(" ", "_")
        if not base:
            base = f"group_{index}"
        return f"p{page_number}:group:{base}:{index}"

    def _segments_to_edges(
        self,
        segments: Iterable[tuple[float, float, float, float]],
        nodes: list[DiagramNode],
        *,
        page_number: int,
        source_path: str,
        source_kind: SourceDocumentKind,
    ) -> list[DiagramEdge]:
        edge_map: dict[tuple[str, str], DiagramEdge] = {}
        for index, segment in enumerate(segments):
            left = self._nearest_node((segment[0], segment[1]), nodes)
            right = self._nearest_node((segment[2], segment[3]), nodes)
            if left is None or right is None or left.id == right.id:
                continue
            confidence = self._edge_confidence(segment, left, right)
            if confidence < 0.45:
                continue
            key = tuple(sorted((left.id, right.id)))
            edge_type = (
                "connected_to"
                if source_kind == SourceDocumentKind.RI_FLOWSHEET
                else "wired_to"
            )
            evidence_refs = [
                EvidenceRef(
                    source_path=source_path,
                    page_or_sheet=f"Page {page_number}",
                    cell_range_or_bbox=str(segment),
                    snippet=f"{left.label} -> {right.label}",
                    score=confidence,
                    evidence_type="diagram_edge",
                    engine="opencv",
                ),
                *(left.evidence_refs[:1] + right.evidence_refs[:1]),
            ]
            candidate = DiagramEdge(
                id=f"p{page_number}:edge:{index}",
                from_node=left.id,
                to_node=right.id,
                edge_type=edge_type,
                polyline=[(segment[0], segment[1]), (segment[2], segment[3])],
                confidence=confidence,
                evidence_refs=evidence_refs,
                label=self._infer_edge_label(left, right),
            )
            existing = edge_map.get(key)
            if existing is None or candidate.confidence > existing.confidence:
                edge_map[key] = candidate
        return list(edge_map.values())

    def _nearest_node(
        self, point: tuple[float, float], nodes: list[DiagramNode]
    ) -> DiagramNode | None:
        nearest: DiagramNode | None = None
        nearest_distance = float("inf")
        for node in nodes:
            center = bbox_center(node.bbox)
            distance = math.hypot(point[0] - center[0], point[1] - center[1])
            threshold = max(
                48.0,
                max(node.bbox[2] - node.bbox[0], node.bbox[3] - node.bbox[1]) * 2.0,
            )
            if distance <= threshold and distance < nearest_distance:
                nearest = node
                nearest_distance = distance
        return nearest

    def _edge_confidence(
        self,
        segment: tuple[float, float, float, float],
        left: DiagramNode,
        right: DiagramNode,
    ) -> float:
        base = 0.45
        if left.node_type in {"terminal", "terminal_pin", "instrument"}:
            base += 0.1
        if right.node_type in {"terminal", "terminal_pin", "instrument"}:
            base += 0.1
        length = math.hypot(segment[2] - segment[0], segment[3] - segment[1])
        if length > 80:
            base += 0.05
        if (
            "/" in left.label
            or ":" in left.label
            or "/" in right.label
            or ":" in right.label
        ):
            base += 0.05
        return min(base, 0.95)

    def _classify_label(self, label: str, source_kind: SourceDocumentKind) -> str:
        upper = label.upper()
        if "/" in upper or ":" in upper:
            return "terminal_pin"
        if upper.startswith("X"):
            return "terminal"
        if upper.startswith(("PXC", "IO", "AI", "AO", "DI", "DO")):
            return "module"
        if source_kind == SourceDocumentKind.RI_FLOWSHEET and any(
            char.isdigit() for char in upper
        ):
            return "instrument"
        return "component"

    def _infer_edge_label(self, left: DiagramNode, right: DiagramNode) -> str:
        if left.node_type == "terminal_pin":
            return left.label
        if right.node_type == "terminal_pin":
            return right.label
        return ""

    def _merge_segments(
        self, segments: list[tuple[float, float, float, float]]
    ) -> list[tuple[float, float, float, float]]:
        merged: list[tuple[float, float, float, float]] = []
        for segment in segments:
            if not merged:
                merged.append(segment)
                continue
            matched = False
            for index, current in enumerate(merged):
                if self._segments_close(current, segment):
                    merged[index] = self._combine_segments(current, segment)
                    matched = True
                    break
            if not matched:
                merged.append(segment)
        return merged

    def _segments_close(
        self,
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> bool:
        left_angle = math.atan2(left[3] - left[1], left[2] - left[0])
        right_angle = math.atan2(right[3] - right[1], right[2] - right[0])
        if abs(left_angle - right_angle) > math.radians(8):
            return False
        for point_a in ((left[0], left[1]), (left[2], left[3])):
            for point_b in ((right[0], right[1]), (right[2], right[3])):
                if math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1]) <= 16:
                    return True
        return False

    def _combine_segments(
        self,
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        points = [
            (left[0], left[1]),
            (left[2], left[3]),
            (right[0], right[1]),
            (right[2], right[3]),
        ]
        is_vertical = abs(left[0] - left[2]) <= abs(left[1] - left[3])
        if is_vertical:
            center_x = sum(point[0] for point in points) / len(points)
            by_y = sorted(points, key=lambda item: (item[1], item[0]))
            return (center_x, by_y[0][1], center_x, by_y[-1][1])
        by_x = sorted(points, key=lambda item: (item[0], item[1]))
        center_y = sum(point[1] for point in points) / len(points)
        return (by_x[0][0], center_y, by_x[-1][0], center_y)


def _segment_bbox(
    segment: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    return (
        float(min(segment[0], segment[2])),
        float(min(segment[1], segment[3])),
        float(max(segment[0], segment[2])),
        float(max(segment[1], segment[3])),
    )


def _segment_group_bbox(
    segments: Iterable[tuple[float, float, float, float]]
) -> tuple[float, float, float, float]:
    return bbox_union(_segment_bbox(segment) for segment in segments)


def _segment_group_height(segments: Iterable[tuple[float, float, float, float]]) -> float:
    bbox = _segment_group_bbox(segments)
    return bbox[3] - bbox[1]


def _segment_trace_path(
    segments: Iterable[tuple[float, float, float, float]]
) -> list[tuple[float, float]]:
    bbox = _segment_group_bbox(segments)
    center_x = (bbox[0] + bbox[2]) / 2.0
    return [(center_x, bbox[1]), (center_x, bbox[3])]


def _segment_trace_path_for_line(
    segments: Iterable[tuple[float, float, float, float]],
    line_x: float,
    fallback_bbox: tuple[float, float, float, float],
    *,
    tolerance: float = 45.0,
) -> list[tuple[float, float]]:
    matching = [
        segment
        for segment in segments
        if abs(((segment[0] + segment[2]) / 2.0) - line_x) <= tolerance
    ]
    if matching:
        bbox = _segment_group_bbox(matching)
        return [(line_x, bbox[1]), (line_x, bbox[3])]
    return [(line_x, fallback_bbox[1]), (line_x, fallback_bbox[3])]


def _numeric_sort_key(value: str) -> tuple[int, str]:
    try:
        return (int(value), value)
    except ValueError:
        return (10**9, value)


def _indexed_value(values: list[str], index: int) -> str:
    if not values:
        return ""
    if index < len(values):
        return values[index]
    return ""


def _numeric_tokens(text: str) -> list[str]:
    cleaned = clean_cell(text)
    if not re.fullmatch(r"[0-9 ]{1,16}", cleaned):
        return []
    return re.findall(r"\b\d{1,3}\b", cleaned)
