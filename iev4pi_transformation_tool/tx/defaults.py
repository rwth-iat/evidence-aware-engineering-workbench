from __future__ import annotations

from dataclasses import dataclass

from iev4pi_transformation_tool.core.utils import clean_cell
from iev4pi_transformation_tool.tx.models import TxEdge, TxNode, TxRuleSet


@dataclass(frozen=True)
class _RulePropertySpec:
    submodel: str
    property_name: str
    field_name: str
    mode: str = "join"
    separator: str = " | "


def build_default_uc1_rule_set(source_type: str) -> TxRuleSet:
    specs, workbook_kind, primary_sheet_name, identity_fields = _default_specs(source_type)
    nodes: list[TxNode] = []
    edges: list[TxEdge] = []
    node_order = 0
    edge_order = 0
    input_nodes: dict[tuple[str, str, str], str] = {}
    submodel_nodes: dict[str, str] = {}

    for submodel_name, _properties in _group_specs(specs).items():
        node_id = _node_id("submodel", submodel_name)
        submodel_nodes[submodel_name] = node_id
        nodes.append(
            TxNode(
                id=node_id,
                node_type="OutputSubmodel",
                label=submodel_name,
                position=(780.0, 80.0 + (len(submodel_nodes) - 1) * 120.0),
                config={"id_short": submodel_name},
            )
        )
        node_order += 1

    for spec in specs:
        input_key = (spec.field_name, spec.mode, spec.separator)
        input_node_id = input_nodes.get(input_key)
        if input_node_id is None:
            input_node_id = _node_id("input", spec.field_name, spec.mode, str(len(input_nodes)))
            input_nodes[input_key] = input_node_id
            nodes.append(
                TxNode(
                    id=input_node_id,
                    node_type="InputColumn",
                    label=spec.field_name,
                    position=(120.0, 80.0 + len(input_nodes) * 56.0),
                    config={
                        "field": spec.field_name,
                        "mode": spec.mode,
                        "separator": spec.separator,
                    },
                )
            )
            node_order += 1

        property_node_id = _node_id("property", spec.submodel, spec.property_name, str(node_order))
        nodes.append(
            TxNode(
                id=property_node_id,
                node_type="OutputProperty",
                label=spec.property_name,
                position=(460.0, 80.0 + node_order * 32.0),
                config={
                    "property_name": spec.property_name,
                    "value_type": "xs:string",
                },
            )
        )
        node_order += 1

        edges.append(
            TxEdge(
                id=_edge_id("value", input_node_id, property_node_id, edge_order),
                from_node=input_node_id,
                to_node=property_node_id,
                target_port="value",
                order=edge_order,
            )
        )
        edge_order += 1
        edges.append(
            TxEdge(
                id=_edge_id("submodel", property_node_id, submodel_nodes[spec.submodel], edge_order),
                from_node=property_node_id,
                to_node=submodel_nodes[spec.submodel],
                target_port="property",
                order=edge_order,
            )
        )
        edge_order += 1

    return TxRuleSet(
        source_type=source_type,
        version=1,
        title=f"Default UC1 {source_type} Tx",
        description="Built-in deterministic UC1 Tx rule set generated from the previous hard-coded mapping.",
        workbook_kind=workbook_kind,
        primary_sheet_name=primary_sheet_name,
        identity_fields=identity_fields,
        nodes=nodes,
        edges=edges,
        metadata={"builtin": True, "source": "uc1_defaults"},
    )


def _default_specs(source_type: str) -> tuple[list[_RulePropertySpec], str, str, list[str]]:
    if source_type == "pid":
        specs = [
            _RulePropertySpec("SM_CoreIdentity", "canonicalTag", "canonical_tag", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "DeviceId", "device_id", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "hasInstrumentationLoopFunctionNumber", "has_instrumentation_loop_function_number", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ProcessInstrumentationFunctionNumber", "process_instrumentation_function_number", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ProcessInstrumentationFunctionCategory", "process_instrumentation_function_category", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ProcessInstrumentationFunctionModifier", "process_instrumentation_function_modifier", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ProcessInstrumentationFunctions", "process_instrumentation_functions", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ContextSummary", "context_summary", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "XSDStatus", "xsd_status", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "DeviceInformation", "device_information", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "VendorCompanyName", "vendor_company_name", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "SafetyRelevanceClass", "safety_relevance_class", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "LabelText", "label_text", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "FunctionCode", "function_code", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "SourceDocument", "source_doc_id", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "SourceLocator", "source_locator", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "DexpiClass", "dexpi_class", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "DexpiSubClass", "dexpi_subclass", mode="first_non_empty"),
            # --- Nameplate (IDTA-aligned, transitional coexistence) ---
            _RulePropertySpec("Nameplate", "ManufacturerName", "vendor_company_name", mode="first_non_empty"),
            _RulePropertySpec("Nameplate", "ManufacturerProductDesignation", "device_information", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "ActuatingFunctionNumber", "actuating_function_number", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "ActuatingLocation", "actuating_location", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "ActuatingSystemNumber", "actuating_system_number", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "OperatedValveReference", "operated_valve_reference", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "FlowDirection", "flow_direction", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "NominalDiameterNumericalValueRepresentation", "nominal_diameter_numerical_value_representation", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "NominalDiameterRepresentation", "nominal_diameter_representation", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "NominalDiameterStandard", "nominal_diameter_standard", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "NominalDiameterTypeRepresentation", "nominal_diameter_type_representation", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "LineNumber", "line_number", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "PipingComponentName", "piping_component_name", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "FromEquipmentId", "from_equipment_id", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "ToEquipmentId", "to_equipment_id", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "PipingAnchorId", "piping_anchor_id", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "RecommendedAction", "recommended_action", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "SourceDocument", "source_doc_id", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "SourceLocator", "source_locator", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "Confidence", "confidence", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "NeedsReview", "needs_review", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "RecommendedAction", "recommended_action", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "ProposalStatus", "proposal_status", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "MissingTargets", "missing_targets", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "EvidenceBundleId", "evidence_bundle_id", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "DecisionConfidence", "decision_confidence", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "UncertaintyReason", "uncertainty_reason", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "LLMVerificationStatus", "llm_verification_status", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "RuleSupport", "rule_support", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "ReviewFeedbackStatus", "review_feedback_status", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "DecisionTrace", "decision_trace_json", mode="first_non_empty"),
        ]
        return specs, "uc1_standardized", "ri_devices", ["canonical_tag", "device_id"]

    common_trace_specs = [
        _RulePropertySpec("SM_Traceability", "SourceDocument", "source_doc_id"),
        _RulePropertySpec("SM_Traceability", "SourceLocator", "source_locator"),
        _RulePropertySpec("SM_Traceability", "Confidence", "confidence"),
        _RulePropertySpec("SM_Traceability", "NeedsReview", "needs_review"),
        _RulePropertySpec("SM_Traceability", "RecommendedAction", "recommended_action"),
        _RulePropertySpec("SM_Traceability", "ProposalStatus", "proposal_status"),
        _RulePropertySpec("SM_Traceability", "MissingTargets", "missing_targets"),
        _RulePropertySpec("SM_Traceability", "EvidenceBundleId", "evidence_bundle_id"),
        _RulePropertySpec("SM_Traceability", "DecisionConfidence", "decision_confidence"),
        _RulePropertySpec("SM_Traceability", "UncertaintyReason", "uncertainty_reason"),
        _RulePropertySpec("SM_Traceability", "LLMVerificationStatus", "llm_verification_status"),
        _RulePropertySpec("SM_Traceability", "RuleSupport", "rule_support"),
        _RulePropertySpec("SM_Traceability", "ReviewFeedbackStatus", "review_feedback_status"),
        _RulePropertySpec("SM_Traceability", "DecisionTrace", "decision_trace_json"),
    ]
    completion_specs = [
        _RulePropertySpec("SM_CompletionProposal", "SourceDocument", "source_doc_id"),
        _RulePropertySpec("SM_CompletionProposal", "SourceLocator", "source_locator"),
        _RulePropertySpec("SM_CompletionProposal", "Confidence", "confidence"),
        _RulePropertySpec("SM_CompletionProposal", "NeedsReview", "needs_review"),
        _RulePropertySpec("SM_CompletionProposal", "RecommendedAction", "recommended_action"),
        _RulePropertySpec("SM_CompletionProposal", "ProposalStatus", "proposal_status"),
        _RulePropertySpec("SM_CompletionProposal", "MissingTargets", "missing_targets"),
    ]

    if source_type == "instrument_list":
        specs = [
            _RulePropertySpec("SM_InstrumentListEntry", "EntryId", "entry_id"),
            _RulePropertySpec("SM_InstrumentListEntry", "DeviceId", "device_id"),
            _RulePropertySpec("SM_InstrumentListEntry", "canonicalTag", "canonical_tag"),
            _RulePropertySpec("SM_InstrumentListEntry", "Tag", "tag"),
            _RulePropertySpec("SM_InstrumentListEntry", "DeviceInformation", "device_information"),
            _RulePropertySpec("SM_InstrumentListEntry", "PresenceStatus", "presence_status"),
            _RulePropertySpec("SM_InstrumentListEntry", "DisplayName", "display_name"),
            *common_trace_specs,
            *completion_specs,
        ]
        return specs, "uc1_instrument_list", "instrument_list_entries", ["canonical_tag", "device_id", "tag", "entry_id"]

    if source_type == "wiring":
        specs = [
            _RulePropertySpec("SM_WiringEntry", "EntryId", "entry_id"),
            _RulePropertySpec("SM_WiringEntry", "DeviceId", "device_id"),
            _RulePropertySpec("SM_WiringEntry", "canonicalTag", "canonical_tag"),
            _RulePropertySpec("SM_WiringEntry", "PLTStelle", "plt_stelle"),
            _RulePropertySpec("SM_WiringEntry", "Funktion", "funktion"),
            _RulePropertySpec("SM_WiringEntry", "Beschreibung", "beschreibung"),
            _RulePropertySpec("SM_WiringEntry", "ESchrank", "e_schrank"),
            _RulePropertySpec("SM_WiringEntry", "WireLabel", "wire_label"),
            _RulePropertySpec("SM_WiringEntry", "PresenceStatus", "presence_status"),
            *common_trace_specs,
            *completion_specs,
        ]
        return specs, "uc1_wiring", "wiring_entries", ["canonical_tag", "device_id", "plt_stelle", "entry_id"]

    if source_type == "datasheet":
        specs = [
            _RulePropertySpec("SM_DatasheetEntry", "EntryId", "entry_id"),
            _RulePropertySpec("SM_DatasheetEntry", "DeviceId", "device_id"),
            _RulePropertySpec("SM_DatasheetEntry", "canonicalTag", "canonical_tag"),
            _RulePropertySpec("SM_DatasheetEntry", "Tag", "tag"),
            _RulePropertySpec("SM_DatasheetEntry", "DeviceInformation", "device_information"),
            _RulePropertySpec("SM_DatasheetEntry", "Art", "art"),
            _RulePropertySpec("SM_DatasheetEntry", "Kanal", "kanal"),
            _RulePropertySpec("SM_DatasheetEntry", "YP", "yp"),
            _RulePropertySpec("SM_DatasheetEntry", "Position", "position"),
            _RulePropertySpec("SM_DatasheetEntry", "Address", "address"),
            _RulePropertySpec("SM_DatasheetEntry", "Project", "project"),
            _RulePropertySpec("SM_DatasheetEntry", "PresenceStatus", "presence_status"),
            *common_trace_specs,
            *completion_specs,
        ]
        return specs, "uc1_datasheet", "datasheet_entries", ["canonical_tag", "device_id", "tag", "entry_id"]

    if source_type == "standardized_device":
        specs = [
            # --- SM_CoreIdentity (8 properties) ---
            _RulePropertySpec("SM_CoreIdentity", "canonicalTag", "canonical_tag", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "DeviceId", "device_id", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "hasInstrumentationLoopFunctionNumber", "has_instrumentation_loop_function_number", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ProcessInstrumentationFunctionNumber", "process_instrumentation_function_number", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ProcessInstrumentationFunctionCategory", "process_instrumentation_function_category", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ProcessInstrumentationFunctionModifier", "process_instrumentation_function_modifier", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ProcessInstrumentationFunctions", "process_instrumentation_functions", mode="first_non_empty"),
            _RulePropertySpec("SM_CoreIdentity", "ContextSummary", "context_summary", mode="first_non_empty"),
            # --- SM_FunctionAndVendor (7 properties) ---
            _RulePropertySpec("SM_FunctionAndVendor", "DeviceInformation", "device_information", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "VendorCompanyName", "vendor_company_name", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "SafetyRelevanceClass", "safety_relevance_class", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "LabelText", "label_text", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "FunctionCode", "function_code", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "RISourceDocument", "ri_source_doc_id", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "RILocator", "ri_source_locator", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "DexpiClass", "dexpi_class", mode="first_non_empty"),
            _RulePropertySpec("SM_FunctionAndVendor", "DexpiSubClass", "dexpi_subclass", mode="first_non_empty"),
            # --- Nameplate (IDTA-aligned, transitional coexistence) ---
            _RulePropertySpec("Nameplate", "ManufacturerName", "vendor_company_name", mode="first_non_empty"),
            _RulePropertySpec("Nameplate", "ManufacturerProductDesignation", "device_information", mode="first_non_empty"),
            # --- SM_ActuationAndPiping (15 properties) ---
            _RulePropertySpec("SM_ActuationAndPiping", "ActuatingFunctionNumber", "actuating_function_number", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "ActuatingLocation", "actuating_location", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "ActuatingSystemNumber", "actuating_system_number", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "OperatedValveReference", "operated_valve_reference", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "FlowDirection", "flow_direction", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "NominalDiameterNumericalValueRepresentation", "nominal_diameter_numerical_value_representation", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "NominalDiameterRepresentation", "nominal_diameter_representation", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "NominalDiameterStandard", "nominal_diameter_standard", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "NominalDiameterTypeRepresentation", "nominal_diameter_type_representation", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "LineNumber", "line_number", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "PipingComponentName", "piping_component_name", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "FromEquipmentId", "from_equipment_id", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "ToEquipmentId", "to_equipment_id", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "PipingAnchorId", "piping_anchor_id", mode="first_non_empty"),
            _RulePropertySpec("SM_ActuationAndPiping", "RecommendedAction", "recommended_action", mode="first_non_empty"),
            # --- SM_IFCConnectivity (16 properties) ---
            _RulePropertySpec("SM_IFCConnectivity", "IFCClass", "ifc_class", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "GlobalId", "global_id", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "Tag", "ifc_tag", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "HasPorts", "has_ports", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "ConnectedTo", "connected_to", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "ConnectedFrom", "connected_from", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "HasControlElements", "has_control_elements", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "PredefinedType", "predefined_type", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "Size", "size", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "ValveMechanism", "valve_mechanism", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "FlowCoefficient", "flow_coefficient", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "FailPosition", "fail_position", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "ManualOverride", "manual_override", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "ActuatorApplication", "actuator_application", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "IFCSourceDocument", "ifc_source_doc_id", mode="first_non_empty"),
            _RulePropertySpec("SM_IFCConnectivity", "IFCLocator", "ifc_source_locator", mode="first_non_empty"),
            # --- SM_CompletionProposal (16 properties) ---
            _RulePropertySpec("SM_CompletionProposal", "PresentInRI", "present_in_ri", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "PresentInStellenplan", "present_in_stellenplan", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "PresentInWiring", "present_in_wiring", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "PresentInDatasheet", "present_in_datasheet", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "PresentInIFC", "present_in_ifc", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "FlangeComplete", "flange_complete", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "UC1Candidate", "uc1_candidate", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "MissingTargets", "missing_targets", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "RecommendedAction", "recommended_action", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "ProposalStatus", "proposal_status", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "StellenplanSourceDocument", "stellenplan_source_doc_id", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "StellenplanLocator", "stellenplan_source_locator", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "WiringSourceDocument", "wiring_source_doc_id", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "WiringLocator", "wiring_source_locator", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "DatasheetSourceDocument", "datasheet_source_doc_id", mode="first_non_empty"),
            _RulePropertySpec("SM_CompletionProposal", "DatasheetLocator", "datasheet_source_locator", mode="first_non_empty"),
            # --- SM_Traceability (7 properties) ---
            _RulePropertySpec("SM_Traceability", "DecisionConfidence", "decision_confidence", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "EvidenceBundleId", "evidence_bundle_id", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "UncertaintyReason", "uncertainty_reason", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "LLMVerificationStatus", "llm_verification_status", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "RuleSupport", "rule_support", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "ReviewFeedbackStatus", "review_feedback_status", mode="first_non_empty"),
            _RulePropertySpec("SM_Traceability", "DecisionTrace", "decision_trace_json", mode="first_non_empty"),
        ]
        return specs, "uc1_standardized", "ri_devices", ["canonical_tag", "device_id"]

    if source_type == "piping":
        specs = [
            _RulePropertySpec("SM_IFCConnectivity", "EntryId", "entry_id"),
            _RulePropertySpec("SM_IFCConnectivity", "DeviceId", "device_id"),
            _RulePropertySpec("SM_IFCConnectivity", "canonicalTag", "canonical_tag"),
            _RulePropertySpec("SM_IFCConnectivity", "IFCClass", "ifc_class"),
            _RulePropertySpec("SM_IFCConnectivity", "GlobalId", "global_id"),
            _RulePropertySpec("SM_IFCConnectivity", "Tag", "tag"),
            _RulePropertySpec("SM_IFCConnectivity", "HasPorts", "has_ports"),
            _RulePropertySpec("SM_IFCConnectivity", "ConnectedTo", "connected_to"),
            _RulePropertySpec("SM_IFCConnectivity", "ConnectedFrom", "connected_from"),
            _RulePropertySpec("SM_IFCConnectivity", "HasControlElements", "has_control_elements"),
            _RulePropertySpec("SM_IFCConnectivity", "PredefinedType", "predefined_type"),
            _RulePropertySpec("SM_IFCConnectivity", "Size", "size"),
            _RulePropertySpec("SM_IFCConnectivity", "ValveMechanism", "valve_mechanism"),
            _RulePropertySpec("SM_IFCConnectivity", "FlowCoefficient", "flow_coefficient"),
            _RulePropertySpec("SM_IFCConnectivity", "FailPosition", "fail_position"),
            _RulePropertySpec("SM_IFCConnectivity", "ManualOverride", "manual_override"),
            _RulePropertySpec("SM_IFCConnectivity", "ActuatorApplication", "actuator_application"),
            _RulePropertySpec("SM_IFCConnectivity", "FlangeComplete", "flange_complete"),
            _RulePropertySpec("SM_IFCConnectivity", "PresenceStatus", "presence_status"),
            *common_trace_specs,
            *completion_specs,
        ]
        return specs, "uc1_piping", "piping_entries", ["canonical_tag", "device_id", "global_id", "tag", "entry_id"]

    if source_type == "stromlaufplan":
        specs = [
            _RulePropertySpec("SM_StromlaufDocument", "DocumentId", "document_id"),
            _RulePropertySpec("SM_StromlaufDocument", "SheetNumber", "sheet_number"),
            _RulePropertySpec("SM_StromlaufDocument", "SheetName", "sheet_name"),
            _RulePropertySpec("SM_StromlaufDocument", "Project", "project"),
            _RulePropertySpec("SM_StromlaufObject", "ObjectId", "object_id"),
            _RulePropertySpec("SM_StromlaufObject", "ObjectReference", "object_reference_data"),
            _RulePropertySpec("SM_StromlaufElement", "ElementId", "element_id"),
            _RulePropertySpec("SM_StromlaufElement", "Classification", "classification"),
            _RulePropertySpec("SM_StromlaufElement", "AttributeName", "attribute_name"),
            _RulePropertySpec("SM_StromlaufElement", "AttributeValue", "attribute_value"),
            _RulePropertySpec("SM_StromlaufConnection", "ConnectionKey", "connection_key"),
            _RulePropertySpec("SM_StromlaufConnection", "FromElementId", "from_element_id"),
            _RulePropertySpec("SM_StromlaufConnection", "ToElementId", "to_element_id"),
            _RulePropertySpec("SM_StromlaufConnection", "WireColor", "wire_color"),
            *common_trace_specs,
        ]
        return specs, "uc1_stromlaufplan", "stromlaufplan_documents", ["document_id", "object_id", "element_id"]

    raise ValueError(f"Unsupported UC1 Tx source type: {source_type}")


def available_default_uc1_rule_sets() -> dict[str, TxRuleSet]:
    return {
        source_type: build_default_uc1_rule_set(source_type)
        for source_type in ("pid", "standardized_device", "instrument_list", "wiring", "datasheet", "piping", "stromlaufplan")
    }


def _group_specs(specs: list[_RulePropertySpec]) -> dict[str, list[_RulePropertySpec]]:
    grouped: dict[str, list[_RulePropertySpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.submodel, []).append(spec)
    return grouped


def _node_id(*parts: str) -> str:
    return "_".join(clean_cell(part).replace(" ", "_") for part in parts if clean_cell(part))


def _edge_id(kind: str, from_node: str, to_node: str, order: int) -> str:
    return f"{kind}_{from_node}_{to_node}_{order}"
