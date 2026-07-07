from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.dom import minidom
from xml.etree import ElementTree as ET

from iev4pi_transformation_tool.core.utils import clean_cell, ensure_dir, normalize_identifier


NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "ievpi": "urn:ievpi:uc1#",
}

ONTOLOGY_CLASSES = {
    "FieldDevice",
    "SourceDocument",
    "CompletionProposal",
    "InstrumentationLoopFunction",
    "ProcessInstrumentationFunction",
    "ActuatingFunction",
    "ActuatingSystem",
    "PipingNetworkSegment",
    "OperatedValve",
    "IfcPipeSegment",
    "IfcValve",
    "IfcActuator",
    "StellenplanEntry",
    "WiringEntry",
    "DatasheetEntry",
    "Equipment",
    "Assembly3DComponent",
    "SpatialPose",
    "IFCPartReference",
}

OBJECT_PROPERTIES = {
    "appearsIn",
    "hasCompletionProposal",
    "anchoredTo",
    "connectedToEquipment",
    "hasIFCObject",
    "connectedTo",
    "connectedFrom",
    "hasPorts",
    "hasControlElements",
    "hasSpatialPose",
    "hasIFCPart",
    "tracesToAKZ",
    "tracesToValveTag",
}

BOOLEAN_PROPERTIES = {
    "PresentInRI",
    "PresentInStellenplan",
    "PresentInWiring",
    "PresentInDatasheet",
    "PresentInIFC",
    "FlangeComplete",
    "UC1Candidate",
}


class OntologyExportService:
    def __init__(self) -> None:
        for prefix, uri in NS.items():
            ET.register_namespace(prefix, uri)

    def export_from_aas_json(
        self,
        aas_paths: list[Path],
        output_path: Path,
        *,
        source_type: str | None = None,
    ) -> Path:
        ensure_dir(output_path.parent)
        root = ET.Element(ET.QName(NS["rdf"], "RDF"))
        payloads = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(path for path in aas_paths if path.suffix.lower() == ".json")
        ]
        active_source_type = clean_cell(source_type or "").lower() or self._infer_source_type(payloads)
        ontology_suffix = active_source_type or "abox"

        ET.SubElement(
            root,
            ET.QName(NS["owl"], "Ontology"),
            {ET.QName(NS["rdf"], "about"): f"urn:ievpi:uc1:{ontology_suffix}:abox"},
        )
        for class_name in sorted(ONTOLOGY_CLASSES):
            ET.SubElement(
                root,
                ET.QName(NS["owl"], "Class"),
                {ET.QName(NS["rdf"], "about"): f"{NS['ievpi']}{class_name}"},
            )
        for property_name in sorted(OBJECT_PROPERTIES):
            ET.SubElement(
                root,
                ET.QName(NS["owl"], "ObjectProperty"),
                {ET.QName(NS["rdf"], "about"): f"{NS['ievpi']}{property_name}"},
            )

        datatype_properties: set[str] = {
            "canonicalTag",
            "sourceLocator",
            "recommendedAction",
            "proposalStatus",
            "missingTargets",
            "confidence",
        }

        for payload in payloads:
            shells = payload.get("assetAdministrationShells", [])
            if not isinstance(shells, list) or not shells:
                continue
            shell = shells[0]
            shell_id = clean_cell(shell.get("id", "")) or f"urn:ievpi:device:{normalize_identifier(clean_cell(shell.get('idShort', 'device')))}"
            submodel_map = self._submodel_map(payload)
            properties = self._collect_shell_properties(shell, submodel_map)
            datatype_properties.update(properties.keys())
            payload_source_type = clean_cell(payload.get("x-ievpi-source_type", ""))
            # Ignore unrendered placeholder templates
            if payload_source_type and "{{" in payload_source_type:
                payload_source_type = ""
            payload_source_type = payload_source_type or active_source_type

            device_individual = ET.SubElement(
                root,
                ET.QName(NS["owl"], "NamedIndividual"),
                {ET.QName(NS["rdf"], "about"): shell_id},
            )
            self._resource(device_individual, "type", self._primary_class_uri(payload_source_type, properties), namespace="rdf")
            if payload_source_type == "assembly_3d":
                self._resource(device_individual, "type", f"{NS['ievpi']}Assembly3DComponent", namespace="rdf")
                # SpatialPose individual with flattened collection properties
                pose_iri = f"{shell_id}:pose"
                pose_ind = ET.SubElement(
                    root, ET.QName(NS["owl"], "NamedIndividual"),
                    {ET.QName(NS["rdf"], "about"): pose_iri},
                )
                self._resource(pose_ind, "type", f"{NS['ievpi']}SpatialPose", namespace="rdf")
                self._resource(device_individual, "hasSpatialPose", pose_iri)
                for coord in ("SpatialPose.positionX", "SpatialPose.positionY", "SpatialPose.positionZ",
                              "SpatialPose.rotationQW", "SpatialPose.rotationQX",
                              "SpatialPose.rotationQY", "SpatialPose.rotationQZ"):
                    val = properties.get(coord, "")
                    short = coord.split(".", 1)[1] if "." in coord else coord
                    if val:
                        self._literal(pose_ind, short, val)
                # IFC part reference
                ifc_gid = properties.get("AssetIdentity.ifcGlobalId", "")
                if not ifc_gid:
                    ifc_gid = properties.get("ifcGlobalId", "")
                if ifc_gid:
                    ifc_iri = f"urn:ievpi:ifc:{ifc_gid}"
                    ifc_ind = ET.SubElement(
                        root, ET.QName(NS["owl"], "NamedIndividual"),
                        {ET.QName(NS["rdf"], "about"): ifc_iri},
                    )
                    self._resource(ifc_ind, "type", f"{NS['ievpi']}IFCPartReference", namespace="rdf")
                    self._resource(device_individual, "hasIFCPart", ifc_iri)
                # AKZ/VV traceability
                akz = properties.get("FunctionalTraceability.akzTag", "") or properties.get("akzTag", "")
                if akz:
                    self._resource(device_individual, "tracesToAKZ", f"urn:ievpi:akz:{akz}")
                vv = properties.get("FunctionalTraceability.vvTag", "") or properties.get("vvTag", "")
                if vv:
                    self._resource(device_individual, "tracesToValveTag", f"urn:ievpi:vv:{vv}")
            elif payload_source_type in {"pid", "", "uc1_standardized"}:
                self._resource(device_individual, "type", f"{NS['ievpi']}FieldDevice", namespace="rdf")

            for property_name, property_value in sorted(properties.items()):
                if property_name in BOOLEAN_PROPERTIES:
                    continue
                self._literal(device_individual, property_name, property_value)

            if self._should_create_completion(properties):
                completion_iri = f"{shell_id}:completion"
                completion_individual = ET.SubElement(
                    root,
                    ET.QName(NS["owl"], "NamedIndividual"),
                    {ET.QName(NS["rdf"], "about"): completion_iri},
                )
                self._resource(completion_individual, "type", f"{NS['ievpi']}CompletionProposal", namespace="rdf")
                self._literal(completion_individual, "recommendedAction", properties.get("RecommendedAction"))
                self._literal(completion_individual, "proposalStatus", properties.get("ProposalStatus"))
                self._literal(completion_individual, "missingTargets", properties.get("MissingTargets"))
                for boolean_property in sorted(BOOLEAN_PROPERTIES):
                    self._literal(completion_individual, boolean_property, properties.get(boolean_property), datatype="boolean")
                needs_review = clean_cell(properties.get("NeedsReview", ""))
                if needs_review:
                    self._literal(completion_individual, "NeedsReview", needs_review, datatype="boolean")
                self._resource(device_individual, "hasCompletionProposal", completion_iri)

            for document_property, locator_property in (
                ("SourceDocument", "SourceLocator"),
                ("RISourceDocument", "RILocator"),
                ("StellenplanSourceDocument", "StellenplanLocator"),
                ("WiringSourceDocument", "WiringLocator"),
                ("DatasheetSourceDocument", "DatasheetLocator"),
                ("IFCSourceDocument", "IFCLocator"),
            ):
                document_id = clean_cell(properties.get(document_property, ""))
                if not document_id:
                    continue
                document_individual = ET.SubElement(
                    root,
                    ET.QName(NS["owl"], "NamedIndividual"),
                    {ET.QName(NS["rdf"], "about"): document_id},
                )
                self._resource(document_individual, "type", f"{NS['ievpi']}SourceDocument", namespace="rdf")
                self._literal(document_individual, "sourceLocator", properties.get(locator_property))
                self._resource(device_individual, "appearsIn", document_id)

            anchor_id = clean_cell(properties.get("ActuatingLocation", ""))
            if anchor_id:
                anchor_iri = f"urn:ievpi:piping-network-segment:{normalize_identifier(anchor_id)}"
                anchor_individual = ET.SubElement(
                    root,
                    ET.QName(NS["owl"], "NamedIndividual"),
                    {ET.QName(NS["rdf"], "about"): anchor_iri},
                )
                self._resource(anchor_individual, "type", f"{NS['ievpi']}PipingNetworkSegment", namespace="rdf")
                self._literal(anchor_individual, "canonicalTag", anchor_id)
                self._resource(device_individual, "anchoredTo", anchor_iri)

            for equipment_key in ("FromEquipmentId", "ToEquipmentId"):
                equipment_id = clean_cell(properties.get(equipment_key, ""))
                if not equipment_id:
                    continue
                equipment_iri = f"urn:ievpi:equipment:{normalize_identifier(equipment_id)}"
                equipment_individual = ET.SubElement(
                    root,
                    ET.QName(NS["owl"], "NamedIndividual"),
                    {ET.QName(NS["rdf"], "about"): equipment_iri},
                )
                self._resource(equipment_individual, "type", f"{NS['ievpi']}Equipment", namespace="rdf")
                self._literal(equipment_individual, "canonicalTag", equipment_id)
                self._resource(device_individual, "connectedToEquipment", equipment_iri)

            ifc_global_id = clean_cell(properties.get("GlobalId", ""))
            if ifc_global_id:
                ifc_iri = f"urn:ievpi:ifc:{normalize_identifier(ifc_global_id)}"
                ifc_individual = ET.SubElement(
                    root,
                    ET.QName(NS["owl"], "NamedIndividual"),
                    {ET.QName(NS["rdf"], "about"): ifc_iri},
                )
                self._resource(
                    ifc_individual,
                    "type",
                    self._ifc_class_uri(properties.get("IFCClass", "")),
                    namespace="rdf",
                )
                self._literal(ifc_individual, "GlobalId", ifc_global_id)
                self._literal(ifc_individual, "Tag", properties.get("Tag"))
                self._literal(ifc_individual, "PredefinedType", properties.get("PredefinedType"))
                self._literal(ifc_individual, "HasPorts", properties.get("HasPorts"))
                self._literal(ifc_individual, "ConnectedTo", properties.get("ConnectedTo"))
                self._literal(ifc_individual, "ConnectedFrom", properties.get("ConnectedFrom"))
                self._literal(ifc_individual, "HasControlElements", properties.get("HasControlElements"))
                self._resource(device_individual, "hasIFCObject", ifc_iri)

        for property_name in sorted(datatype_properties):
            ET.SubElement(
                root,
                ET.QName(NS["owl"], "DatatypeProperty"),
                {ET.QName(NS["rdf"], "about"): f"{NS['ievpi']}{property_name}"},
            )

        xml_bytes = ET.tostring(root, encoding="utf-8")
        pretty = minidom.parseString(xml_bytes).toprettyxml(indent="  ", encoding="utf-8")
        output_path.write_bytes(pretty)
        return output_path

    def _infer_source_type(self, payloads: list[dict[str, Any]]) -> str:
        for payload in payloads:
            source_type = clean_cell(payload.get("x-ievpi-source_type", "")).lower()
            if source_type:
                return source_type
        return ""

    def _primary_class_uri(self, source_type: str, properties: dict[str, str]) -> str:
        normalized = clean_cell(source_type).lower()
        if normalized == "pid":
            return f"{NS['ievpi']}ProcessInstrumentationFunction"
        if normalized == "instrument_list":
            return f"{NS['ievpi']}StellenplanEntry"
        if normalized == "wiring":
            return f"{NS['ievpi']}WiringEntry"
        if normalized == "datasheet":
            return f"{NS['ievpi']}DatasheetEntry"
        if normalized == "piping":
            return self._ifc_class_uri(properties.get("IFCClass", ""))
        if normalized == "assembly_3d":
            return f"{NS['ievpi']}Assembly3DComponent"
        return f"{NS['ievpi']}FieldDevice"

    def _should_create_completion(self, properties: dict[str, str]) -> bool:
        if any(clean_cell(properties.get(name, "")) for name in ("RecommendedAction", "ProposalStatus", "MissingTargets")):
            return True
        return any(clean_cell(properties.get(name, "")) for name in BOOLEAN_PROPERTIES | {"NeedsReview"})

    def _submodel_map(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        submodels = payload.get("submodels", [])
        if not isinstance(submodels, list):
            return {}
        return {
            clean_cell(model.get("id", "")): model
            for model in submodels
            if isinstance(model, dict) and clean_cell(model.get("id", ""))
        }

    def _collect_shell_properties(
        self,
        shell: dict[str, Any],
        submodel_map: dict[str, dict[str, Any]],
    ) -> dict[str, str]:
        properties: dict[str, str] = {}
        for reference in shell.get("submodels", []):
            if not isinstance(reference, dict):
                continue
            keys = reference.get("keys", [])
            if not isinstance(keys, list) or not keys:
                continue
            submodel_id = clean_cell(keys[0].get("value", ""))
            submodel = submodel_map.get(submodel_id)
            if not isinstance(submodel, dict):
                continue
            self._flatten_submodel_elements(submodel.get("submodelElements", []), properties)
        return properties

    def _flatten_submodel_elements(
        self, elements: list[dict[str, Any]], properties: dict[str, str]
    ) -> None:
        """Recursively flatten SubmodelElementCollection into idShort→value."""
        for element in elements:
            if not isinstance(element, dict):
                continue
            name = clean_cell(element.get("idShort", ""))
            value = element.get("value", "")
            if isinstance(value, list):
                # SubmodelElementCollection: recurse into children
                child_props: dict[str, str] = {}
                self._flatten_submodel_elements(value, child_props)
                # Merge children with collection prefix (e.g. AssetIdentity.assetId)
                for child_name, child_value in child_props.items():
                    properties[f"{name}.{child_name}"] = child_value
            elif name:
                properties[name] = clean_cell(value)

    def _literal(
        self,
        element: ET.Element,
        property_name: str,
        value: str | None,
        *,
        datatype: str = "string",
    ) -> None:
        cleaned = clean_cell(value)
        if not cleaned:
            return
        literal = ET.SubElement(element, ET.QName(NS["ievpi"], property_name))
        literal.text = cleaned
        literal.set(ET.QName(NS["rdf"], "datatype"), f"{NS['xsd']}{datatype}")

    def _resource(
        self,
        element: ET.Element,
        property_name: str,
        resource: str,
        *,
        namespace: str = "ievpi",
    ) -> None:
        child = ET.SubElement(element, ET.QName(NS[namespace], property_name))
        child.set(ET.QName(NS["rdf"], "resource"), resource)

    def _ifc_class_uri(self, ifc_class: str) -> str:
        normalized = clean_cell(ifc_class).lower()
        if "actuator" in normalized:
            return f"{NS['ievpi']}IfcActuator"
        if "valve" in normalized:
            return f"{NS['ievpi']}IfcValve"
        return f"{NS['ievpi']}IfcPipeSegment"
