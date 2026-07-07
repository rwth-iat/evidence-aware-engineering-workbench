from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
import re
from typing import Any
from xml.etree import ElementTree as ET

from iev4pi_transformation_tool.core.utils import canonical_field_name, clean_cell, normalize_identifier, normalize_label
from iev4pi_transformation_tool.models import DexpiEdge, DexpiNode, DexpiPackageData, EvidenceRef, RiBundle, RiInstrumentInstance, XsdFieldDef

try:
    from lxml import etree as LET
except Exception:  # pragma: no cover - optional runtime dependency
    LET = None


XML_OBJECT_TAGS = {
    "Equipment": "equipment",
    "InstrumentationLoopFunction": "instrument_loop",
    "ProcessInstrumentationFunction": "instrument_function",
    "PipingComponent": "piping_component",
    "Nozzle": "piping_component",
}

XSD_NAMESPACE = "{http://www.w3.org/2001/XMLSchema}"


def peek_drawing_metadata(xml_path: Path) -> dict[str, str]:
    try:
        tree = ET.parse(xml_path)
    except Exception:
        return {}
    root = tree.getroot()
    drawing = root.find("Drawing")
    if drawing is None:
        return {}
    return {
        "drawing_name": clean_cell(drawing.get("Name", "")),
        "drawing_title": clean_cell(drawing.get("Title", "")),
    }


class DexpiPackageAnalyzer:
    def analyze(self, bundle: RiBundle) -> DexpiPackageData:
        xml_nodes: list[DexpiNode] = []
        xml_edges: list[DexpiEdge] = []
        xsd_field_defs: list[XsdFieldDef] = []
        validation_errors: list[str] = []
        bundle_metadata = {
            "bundle_id": bundle.bundle_id,
            "display_name": bundle.display_name,
            "drawing_name": bundle.drawing_name,
            "drawing_title": bundle.drawing_title,
        }

        root: ET.Element | None = None
        if bundle.xml_path and bundle.xml_path.exists():
            root = ET.parse(bundle.xml_path).getroot()
            xml_nodes = self._parse_nodes(root, bundle)
            xml_edges = self._parse_edges(root, bundle)
            xml_edges.extend(self._parse_associations(root, bundle, start_index=len(xml_edges)))
        if bundle.xsd_path and bundle.xml_path:
            xsd_field_defs = self._parse_xsd(bundle.xsd_path, root)
            validation_errors = self._validate_xml(bundle.xml_path, bundle.xsd_path)

        return DexpiPackageData(
            bundle_id=bundle.bundle_id,
            xml_nodes=xml_nodes,
            xml_edges=xml_edges,
            instrument_instances=self._build_instrument_instances(xml_nodes, xml_edges),
            xsd_field_defs=xsd_field_defs,
            bundle_metadata=bundle_metadata,
            validation_errors=validation_errors,
        )

    def _parse_nodes(self, root: ET.Element, bundle: RiBundle) -> list[DexpiNode]:
        nodes: list[DexpiNode] = []
        for tag_name, category in XML_OBJECT_TAGS.items():
            for element in root.findall(f".//{tag_name}"):
                node_id = clean_cell(element.get("ID", ""))
                if not node_id:
                    continue
                attributes = self._collect_attributes(element)
                tag_label = clean_cell(element.get("TagName", "")) or node_id
                class_name = clean_cell(element.get("ComponentClass", "")) or attributes.get("class", "")
                sub_class = attributes.get("sub_class", "")
                normalized_type = self._normalize_component_type(class_name, sub_class, category, tag_label)
                locator = f"{tag_name}#{node_id}"
                source_ref = self._xml_evidence(
                    bundle,
                    locator,
                    f"{tag_name} {tag_label} ({class_name or sub_class or normalized_type})",
                )
                nodes.append(
                    DexpiNode(
                        node_id=node_id,
                        tag_name=tag_label,
                        class_name=class_name,
                        sub_class=sub_class,
                        category=category,
                        attributes=attributes,
                        position=self._position_of(element),
                        source_refs=[source_ref],
                        locator=locator,
                        normalized_type=normalized_type,
                    )
                )
        return nodes

    def _parse_edges(self, root: ET.Element, bundle: RiBundle) -> list[DexpiEdge]:
        edges: list[DexpiEdge] = []
        edge_index = 0
        for connection in root.findall(".//Connection"):
            from_id = clean_cell(connection.get("FromID", ""))
            to_id = clean_cell(connection.get("ToID", ""))
            if not from_id or not to_id:
                continue
            parent = self._find_parent(root, connection)
            parent_tag = parent.tag if parent is not None else "Connection"
            class_name = clean_cell(parent.get("ComponentClass", "")) if parent is not None else ""
            sub_class = ""
            attributes: dict[str, str] = {}
            if parent is not None:
                attributes = self._collect_attributes(parent)
                sub_class = attributes.get("sub_class", "")
            if parent_tag == "InformationFlow":
                edge_type = "information_flow"
            elif parent_tag == "PipingNetworkSegment":
                edge_type = "piping_connection"
            else:
                edge_type = "connection"
            locator = f"{parent_tag}::{from_id}->{to_id}::{edge_index}"
            edges.append(
                DexpiEdge(
                    edge_id=f"{bundle.bundle_id}:{edge_index}",
                    from_id=from_id,
                    to_id=to_id,
                    edge_type=edge_type,
                    class_name=class_name,
                    sub_class=sub_class,
                    attributes=attributes,
                    source_refs=[
                        self._xml_evidence(bundle, locator, f"{parent_tag} connection {from_id} -> {to_id}")
                    ],
                    locator=locator,
                )
            )
            edge_index += 1
        return edges

    def _parse_associations(self, root: ET.Element, bundle: RiBundle, *, start_index: int = 0) -> list[DexpiEdge]:
        edges: list[DexpiEdge] = []
        edge_index = start_index
        for parent in root.iter():
            parent_id = clean_cell(parent.get("ID", ""))
            if not parent_id:
                continue
            for association in parent.findall("Association"):
                association_type = clean_cell(association.get("Type", ""))
                item_id = clean_cell(association.get("ItemID", ""))
                if not item_id or not association_type:
                    continue
                edge_type = "association"
                if normalize_label(association_type) == "is a collection including":
                    edge_type = "loop_membership"
                locator = f"{parent.tag}::{parent_id}->{item_id}::{edge_index}"
                edges.append(
                    DexpiEdge(
                        edge_id=f"{bundle.bundle_id}:{edge_index}",
                        from_id=parent_id,
                        to_id=item_id,
                        edge_type=edge_type,
                        class_name=clean_cell(parent.get("ComponentClass", "")),
                        sub_class="",
                        attributes={"association_type": association_type},
                        source_refs=[
                            self._xml_evidence(bundle, locator, f"{parent.tag} association {association_type}: {parent_id} -> {item_id}")
                        ],
                        locator=locator,
                    )
                )
                edge_index += 1
        return edges

    def _parse_xsd(self, xsd_path: Path, xml_root: ET.Element | None) -> list[XsdFieldDef]:
        if LET is None or not xsd_path.exists():
            return []
        tree = LET.parse(str(xsd_path))
        root = tree.getroot()
        enum_map = self._collect_xsd_enums(root)
        actual_categories = self._actual_categories(xml_root)
        field_defs: dict[tuple[str, str], XsdFieldDef] = {}
        for complex_type in root.findall(f".//{XSD_NAMESPACE}complexType"):
            type_name = clean_cell(complex_type.get("name", ""))
            category = self._category_from_type_name(type_name)
            if actual_categories and category and category not in actual_categories:
                continue
            for attribute in complex_type.findall(f".//{XSD_NAMESPACE}attribute"):
                attr_name = clean_cell(attribute.get("name", ""))
                if not attr_name:
                    continue
                field_name = canonical_field_name(attr_name)
                type_name_ref = clean_cell(attribute.get("type", ""))
                enum_values = enum_map.get(type_name_ref, [])
                value_type = self._map_xsd_type(type_name_ref)
                description = f"Defined in XSD complexType {type_name}" if type_name else "Defined in XSD"
                key = (category or "common", field_name)
                if key not in field_defs:
                    field_defs[key] = XsdFieldDef(
                        name=field_name,
                        xml_name=attr_name,
                        value_type=value_type,
                        description=description,
                        enumeration_values=enum_values,
                        category=category or "common",
                        source_path=xsd_path.name,
                    )
        return list(field_defs.values())

    def _validate_xml(self, xml_path: Path, xsd_path: Path) -> list[str]:
        if LET is None or not xml_path.exists() or not xsd_path.exists():
            return []
        try:
            xml_doc = LET.parse(str(xml_path))
            xsd_doc = LET.parse(str(xsd_path))
            schema = LET.XMLSchema(xsd_doc)
            schema.assertValid(xml_doc)
            return []
        except Exception as exc:  # pragma: no cover - depends on local XML/XSD library
            return [str(exc)]

    def _collect_attributes(self, element: ET.Element) -> dict[str, str]:
        collected: dict[str, str] = {}
        for attr_name, attr_value in element.attrib.items():
            cleaned = clean_cell(attr_value)
            if cleaned:
                collected[canonical_field_name(attr_name)] = cleaned
        for generic in element.findall("GenericAttributes/GenericAttribute"):
            name = canonical_field_name(generic.get("Name", ""))
            value = clean_cell(generic.get("Value", ""))
            if name and value and name not in collected:
                collected[name] = value
        for key, value in self._collect_leaf_text_values(element).items():
            if key and value and key not in collected:
                collected[key] = value
        return collected

    def _collect_leaf_text_values(self, element: ET.Element, prefix: str = "", depth: int = 0) -> dict[str, str]:
        if depth > 2:
            return {}
        ignored_tags = {"GenericAttributes", "Position", "Connection", "Association"}
        collected: dict[str, str] = {}
        for child in list(element):
            tag_name = child.tag.split("}")[-1]
            if tag_name in ignored_tags:
                continue
            base_name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tag_name)
            field_name = canonical_field_name(f"{prefix} {base_name}".strip())
            text_value = clean_cell(child.text)
            if text_value and field_name and field_name not in collected:
                collected[field_name] = text_value
            if list(child):
                nested_prefix = field_name.replace("_", " ")
                for nested_key, nested_value in self._collect_leaf_text_values(child, nested_prefix, depth + 1).items():
                    if nested_key and nested_value and nested_key not in collected:
                        collected[nested_key] = nested_value
        return collected

    def _position_of(self, element: ET.Element) -> tuple[float, float] | None:
        location = element.find("Position/Location")
        if location is None:
            return None
        try:
            x = float(str(location.get("X", "0")).replace(",", "."))
            y = float(str(location.get("Y", "0")).replace(",", "."))
        except ValueError:
            return None
        return (x, y)

    def _xml_evidence(self, bundle: RiBundle, locator: str, snippet: str) -> EvidenceRef:
        source_path = bundle.xml_path.as_posix() if bundle.xml_path else bundle.display_name
        return EvidenceRef(
            source_path=source_path,
            page_or_sheet="DEXPI XML",
            cell_range_or_bbox=locator,
            snippet=snippet[:240],
            score=1.0,
            evidence_type="dexpi_node" if "::" not in locator else "dexpi_edge",
            engine="dexpi",
        )

    def _find_parent(self, root: ET.Element, child: ET.Element) -> ET.Element | None:
        for parent in root.iter():
            for candidate in list(parent):
                if candidate is child:
                    return parent
        return None

    def _normalize_component_type(
        self,
        class_name: str,
        sub_class: str,
        category: str,
        tag_name: str,
    ) -> str:
        joined = normalize_label(" ".join(part for part in [class_name, sub_class, tag_name] if part))
        if "pump" in joined:
            return "pump"
        if "heat exchanger" in joined or "exchanger" in joined:
            return "heat_exchanger"
        if "vessel" in joined or "tank" in joined:
            return "vessel"
        if "valve" in joined:
            return "valve"
        if "filter" in joined:
            return "filter"
        if category == "instrument_loop":
            return "instrument_loop"
        if category == "instrument_function":
            return "instrument_function"
        if category == "piping_component":
            return "piping_component"
        if category == "equipment":
            return "equipment"
        return "component"

    def _build_instrument_instances(
        self,
        xml_nodes: list[DexpiNode],
        xml_edges: list[DexpiEdge],
    ) -> list[RiInstrumentInstance]:
        node_lookup = {node.node_id: node for node in xml_nodes}
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in xml_edges:
            adjacency[edge.from_id].add(edge.to_id)
            adjacency[edge.to_id].add(edge.from_id)

        instances: list[RiInstrumentInstance] = []
        seen_tags: set[tuple[str, str]] = set()
        for loop_node in xml_nodes:
            if loop_node.category != "instrument_loop":
                continue
            canonical_tag = clean_cell(loop_node.tag_name or loop_node.attributes.get("tag_name", ""))
            if not canonical_tag:
                continue
            members = [
                edge.to_id
                for edge in xml_edges
                if edge.from_id == loop_node.node_id and edge.edge_type == "loop_membership"
            ]
            function_node = next(
                (
                    node_lookup[item_id]
                    for item_id in members
                    if item_id in node_lookup and node_lookup[item_id].category == "instrument_function"
                ),
                None,
            )
            if function_node is None:
                continue
            function_code = clean_cell(function_node.tag_name)
            evidence_refs = list(loop_node.source_refs) + list(function_node.source_refs)
            piping_anchor = self._resolve_piping_anchor(function_node.node_id, node_lookup, adjacency)
            context = self._instrument_context(loop_node, function_node, piping_anchor, node_lookup, adjacency)
            label_text = " ".join(part for part in [function_code, canonical_tag] if part).strip() or canonical_tag
            full_label = clean_cell(
                piping_anchor.attributes.get("full_label", "")
                if piping_anchor is not None
                else ""
            )
            key = (normalize_identifier(canonical_tag), function_node.node_id)
            if key in seen_tags:
                continue
            seen_tags.add(key)
            instances.append(
                RiInstrumentInstance(
                    canonical_tag=canonical_tag,
                    function_code=function_code,
                    loop_node_id=loop_node.node_id,
                    function_node_id=function_node.node_id,
                    label_text=label_text,
                    full_label=full_label,
                    description=clean_cell(
                        function_node.attributes.get("description", "")
                        or loop_node.attributes.get("description", "")
                    ),
                    piping_anchor_id=piping_anchor.node_id if piping_anchor is not None else "",
                    from_equipment=clean_cell(piping_anchor.attributes.get("from_equipment", "")) if piping_anchor is not None else "",
                    to_equipment=clean_cell(piping_anchor.attributes.get("to_equipment", "")) if piping_anchor is not None else "",
                    context_summary=context,
                    evidence_refs=evidence_refs[:4],
                )
            )
        return instances

    def _resolve_piping_anchor(
        self,
        start_id: str,
        node_lookup: dict[str, DexpiNode],
        adjacency: dict[str, set[str]],
    ) -> DexpiNode | None:
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])
        seen = {start_id}
        best_node: DexpiNode | None = None
        while queue:
            current_id, depth = queue.popleft()
            current = node_lookup.get(current_id)
            if current is None:
                continue
            if current.category == "piping_component":
                if any(
                    key in current.attributes
                    for key in ("from_equipment", "to_equipment", "full_name", "full_label")
                ):
                    return current
                if best_node is None:
                    best_node = current
            if depth >= 4:
                continue
            for neighbor_id in adjacency.get(current_id, set()):
                if neighbor_id in seen:
                    continue
                seen.add(neighbor_id)
                queue.append((neighbor_id, depth + 1))
        return best_node

    def _instrument_context(
        self,
        loop_node: DexpiNode,
        function_node: DexpiNode,
        piping_anchor: DexpiNode | None,
        node_lookup: dict[str, DexpiNode],
        adjacency: dict[str, set[str]],
    ) -> str:
        parts: list[str] = []
        if piping_anchor is not None:
            from_equipment = clean_cell(piping_anchor.attributes.get("from_equipment", ""))
            to_equipment = clean_cell(piping_anchor.attributes.get("to_equipment", ""))
            full_name = clean_cell(piping_anchor.attributes.get("full_name", ""))
            if from_equipment and to_equipment:
                parts.append(f"{from_equipment} -> {to_equipment}")
            if full_name:
                parts.append(full_name)
        nearby_pump = self._nearby_equipment_type(function_node.node_id, node_lookup, adjacency, target_type="pump")
        if nearby_pump:
            parts.append(f"near {nearby_pump}")
        if not parts:
            fallback = clean_cell(loop_node.attributes.get("name", "") or function_node.attributes.get("name", ""))
            if fallback:
                parts.append(fallback)
        return " | ".join(parts)

    def _nearby_equipment_type(
        self,
        start_id: str,
        node_lookup: dict[str, DexpiNode],
        adjacency: dict[str, set[str]],
        *,
        target_type: str,
    ) -> str:
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])
        seen = {start_id}
        while queue:
            current_id, depth = queue.popleft()
            current = node_lookup.get(current_id)
            if current is not None and current.category == "equipment" and current.normalized_type == target_type:
                return current.tag_name or current.node_id
            if depth >= 3:
                continue
            for neighbor_id in adjacency.get(current_id, set()):
                if neighbor_id in seen:
                    continue
                seen.add(neighbor_id)
                queue.append((neighbor_id, depth + 1))
        return ""

    def _collect_xsd_enums(self, root: Any) -> dict[str, list[str]]:
        enum_map: dict[str, list[str]] = defaultdict(list)
        for simple_type in root.findall(f".//{XSD_NAMESPACE}simpleType"):
            type_name = clean_cell(simple_type.get("name", ""))
            if not type_name:
                continue
            for enum_value in simple_type.findall(f".//{XSD_NAMESPACE}enumeration"):
                value = clean_cell(enum_value.get("value", ""))
                if value:
                    enum_map[type_name].append(value)
        return dict(enum_map)

    def _category_from_type_name(self, type_name: str) -> str:
        normalized = normalize_label(type_name)
        if "equipment" in normalized:
            return "equipment"
        if "instrumentation" in normalized or "process instrumentation function" in normalized:
            return "instrument_function"
        if "pipingcomponent" in normalized or "piping component" in normalized or "nozzle" in normalized:
            return "piping_component"
        if "connection" in normalized:
            return "connection"
        return ""

    def _actual_categories(self, xml_root: ET.Element | None) -> set[str]:
        if xml_root is None:
            return set()
        categories = set()
        for tag_name, category in XML_OBJECT_TAGS.items():
            if xml_root.find(f".//{tag_name}") is not None:
                categories.add(category)
        if xml_root.find(".//Connection") is not None:
            categories.add("connection")
        return categories

    def _map_xsd_type(self, type_name: str) -> str:
        lowered = type_name.lower()
        if any(token in lowered for token in ["int", "integer", "long", "short"]):
            return "integer"
        if any(token in lowered for token in ["float", "double", "decimal"]):
            return "number"
        if "bool" in lowered:
            return "boolean"
        return "string"
