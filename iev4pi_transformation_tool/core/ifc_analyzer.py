from __future__ import annotations

from pathlib import Path
from typing import Any

from iev4pi_transformation_tool.core.utils import canonical_field_name, clean_cell, normalize_identifier
from iev4pi_transformation_tool.models import DocumentDescriptor, EvidenceRef, IfcEdge, IfcNode, IfcPackageData

try:  # pragma: no cover - optional runtime dependency
    import ifcopenshell
except Exception:  # pragma: no cover - optional runtime dependency
    ifcopenshell = None

try:  # pragma: no cover - optional runtime dependency
    from ifcopenshell.util.element import get_psets
except Exception:  # pragma: no cover - optional runtime dependency
    get_psets = None


IFC_RELEVANT_TYPES = (
    "IfcDistributionElement",
    "IfcFlowSegment",
    "IfcFlowFitting",
    "IfcPipeSegment",
    "IfcPipeFitting",
    "IfcValve",
    "IfcActuator",
    "IfcPump",
    "IfcTank",
    "IfcBuildingElementProxy",
)


class IfcPackageAnalyzer:
    def analyze(self, document: DocumentDescriptor) -> IfcPackageData:
        bundle_metadata = {
            "relative_path": document.relative_path,
            "source_root": document.source_root,
        }
        if ifcopenshell is None:
            return IfcPackageData(
                document_id=document.relative_path,
                bundle_metadata=bundle_metadata,
                validation_errors=["ifcopenshell is not installed."],
            )
        try:
            model = ifcopenshell.open(document.path.as_posix())
        except Exception as exc:
            return IfcPackageData(
                document_id=document.relative_path,
                bundle_metadata=bundle_metadata,
                validation_errors=[str(exc)],
            )
        nodes = self._parse_nodes(model, document)
        edges = self._parse_edges(model, document)
        return IfcPackageData(
            document_id=document.relative_path,
            ifc_nodes=nodes,
            ifc_edges=edges,
            bundle_metadata=bundle_metadata,
        )

    def _parse_nodes(self, model: Any, document: DocumentDescriptor) -> list[IfcNode]:
        nodes: dict[str, IfcNode] = {}
        for type_name in IFC_RELEVANT_TYPES:
            for entity in model.by_type(type_name):
                if not self._is_relevant_entity(entity):
                    continue
                node_id = clean_cell(getattr(entity, "GlobalId", "")) or f"{type_name}:{getattr(entity, 'id', lambda: 0)()}"
                if node_id in nodes:
                    continue
                attributes = self._collect_entity_attributes(entity)
                match_keys = self._match_keys(entity, attributes)
                locator = f"{entity.is_a()}::{node_id}"
                nodes[node_id] = IfcNode(
                    node_id=node_id,
                    ifc_class=entity.is_a(),
                    name=clean_cell(getattr(entity, "Name", "")),
                    tag=clean_cell(getattr(entity, "Tag", "")),
                    object_type=clean_cell(getattr(entity, "ObjectType", "")),
                    predefined_type=clean_cell(getattr(entity, "PredefinedType", "")),
                    description=clean_cell(getattr(entity, "Description", "")),
                    attributes=attributes,
                    match_keys=match_keys,
                    flange_complete=self._flange_complete(entity, attributes),
                    source_refs=[self._ifc_evidence(document.path, locator, entity)],
                    locator=locator,
                )
        return list(nodes.values())

    def _parse_edges(self, model: Any, document: DocumentDescriptor) -> list[IfcEdge]:
        edges: list[IfcEdge] = []
        edge_index = 0
        relation_types = (
            "IfcRelConnects",
            "IfcRelConnectsElements",
            "IfcRelConnectsPortToElement",
            "IfcRelConnectsPorts",
            "IfcRelFlowControlElements",
            "IfcRelAssignsToProduct",
        )
        for relation_type in relation_types:
            for relation in model.by_type(relation_type):
                endpoints = self._relation_endpoints(relation)
                if endpoints is None:
                    continue
                from_id, to_id = endpoints
                locator = f"{relation.is_a()}::{from_id}->{to_id}::{edge_index}"
                edges.append(
                    IfcEdge(
                        edge_id=f"{document.relative_path}:{edge_index}",
                        from_id=from_id,
                        to_id=to_id,
                        relation_type=relation.is_a(),
                        attributes=self._collect_relation_attributes(relation),
                        source_refs=[self._ifc_evidence(document.path, locator, relation)],
                        locator=locator,
                    )
                )
                edge_index += 1
        return edges

    def _is_relevant_entity(self, entity: Any) -> bool:
        class_name = entity.is_a()
        if class_name == "IfcBuildingElementProxy":
            # Filter FreeCAD assembly helpers (not real components)
            name = getattr(entity, "Name", "") or ""
            if name in ("Assembly", "Origin", "X-axis", "Y-axis", "Z-axis",
                        "XY-plane", "XZ-plane", "YZ-plane", "Joints", "Link",
                        "Fixed", "GroundedJoint", "Joint", "LCS"):
                return False
            if name.startswith(("Origin", "X-axis", "Y-axis", "Z-axis",
                               "XY-plane", "XZ-plane", "YZ-plane")):
                return False
            return True
        if class_name in {"IfcPipeSegment", "IfcPipeFitting", "IfcValve", "IfcActuator", "IfcPump", "IfcTank"}:
            return True
        return any(marker in class_name for marker in ("Pipe", "Valve", "Actuator", "Pump", "Tank", "Fitting"))

    def _collect_entity_attributes(self, entity: Any) -> dict[str, str]:
        attributes: dict[str, str] = {}
        for attr_name in ("GlobalId", "Name", "Tag", "ObjectType", "PredefinedType", "Description"):
            value = clean_cell(getattr(entity, attr_name, ""))
            if value:
                attributes[canonical_field_name(attr_name)] = value
        if get_psets is not None:
            try:
                property_sets = get_psets(entity) or {}
            except Exception:
                property_sets = {}
            for set_name, payload in property_sets.items():
                if not isinstance(payload, dict):
                    continue
                for key, value in payload.items():
                    cleaned = clean_cell(value)
                    if not cleaned:
                        continue
                    field_name = canonical_field_name(f"{set_name}_{key}")
                    attributes[field_name] = cleaned
        return attributes

    def _collect_relation_attributes(self, relation: Any) -> dict[str, str]:
        attributes: dict[str, str] = {}
        for attr_name in ("GlobalId", "Name", "Description"):
            value = clean_cell(getattr(relation, attr_name, ""))
            if value:
                attributes[canonical_field_name(attr_name)] = value
        return attributes

    def _match_keys(self, entity: Any, attributes: dict[str, str]) -> list[str]:
        values = [
            clean_cell(getattr(entity, "Tag", "")),
            clean_cell(getattr(entity, "Name", "")),
            clean_cell(getattr(entity, "ObjectType", "")),
        ]
        values.extend(
            value
            for key, value in attributes.items()
            if any(token in key for token in ("tag", "line", "segment", "id", "identifier"))
        )
        match_keys: list[str] = []
        for value in values:
            normalized = normalize_identifier(value)
            if normalized and normalized not in match_keys:
                match_keys.append(normalized)
        return match_keys

    def _flange_complete(self, entity: Any, attributes: dict[str, str]) -> bool | None:
        class_name = entity.is_a().lower()
        if "flange" in class_name:
            return True
        flange_related = {
            key: value
            for key, value in attributes.items()
            if "flange" in key or "flansch" in key
        }
        if not flange_related:
            return None
        return all(bool(clean_cell(value)) for value in flange_related.values())

    def _relation_endpoints(self, relation: Any) -> tuple[str, str] | None:
        candidates = [
            (getattr(relation, "RelatingElement", None), getattr(relation, "RelatedElement", None)),
            (getattr(relation, "RelatingPort", None), getattr(relation, "RelatedPort", None)),
            (getattr(relation, "RelatingProduct", None), getattr(relation, "RelatedObjects", None)),
            (getattr(relation, "RelatingFlowElement", None), getattr(relation, "RelatedControlElements", None)),
        ]
        for left, right in candidates:
            if left is None or right is None:
                continue
            if isinstance(right, (list, tuple)):
                if not right:
                    continue
                right = right[0]
            left_id = clean_cell(getattr(left, "GlobalId", ""))
            right_id = clean_cell(getattr(right, "GlobalId", ""))
            if left_id and right_id:
                return left_id, right_id
        return None

    def _ifc_evidence(self, source_path: Path, locator: str, entity: Any) -> EvidenceRef:
        label = clean_cell(getattr(entity, "Name", "")) or clean_cell(getattr(entity, "Tag", "")) or entity.is_a()
        return EvidenceRef(
            source_path=source_path.as_posix(),
            page_or_sheet="IFC",
            cell_range_or_bbox=locator,
            snippet=f"{entity.is_a()} {label}".strip(),
            score=1.0,
            evidence_type="ifc",
            engine="ifcopenshell",
        )
