from __future__ import annotations

from dataclasses import dataclass

from iev4pi_transformation_tool.core.utils import clean_cell, normalize_identifier
from iev4pi_transformation_tool.t1t5.models import T1T5Edge, T1T5Node, T1T5RuleBundle, T1T5RuleProfile, WorkbookSignature


STAGE_IDS = ("t1", "t2", "t3", "t4", "t5")

STAGE_TO_SOURCE_TYPE = {
    "t1": "pid",
    "t2": "instrument_list",
    "t3": "wiring",
    "t4": "datasheet",
    "t5": "piping",
}

STAGE_TO_PRIMARY_SHEET = {
    "t1": "ri_devices",
    "t2": "instrument_list_entries",
    "t3": "wiring_entries",
    "t4": "datasheet_entries",
    "t5": "piping_entries",
}

STAGE_TO_OUTPUT_FIELDS = {
    "t1": [
        "device_id",
        "canonical_tag",
        "class_name",
        "has_instrumentation_loop_function_number",
        "process_instrumentation_function_number",
        "process_instrumentation_function_category",
        "process_instrumentation_function_modifier",
        "process_instrumentation_functions",
        "device_information",
        "vendor_company_name",
        "safety_relevance_class",
        "actuating_function_number",
        "actuating_location",
        "actuating_system_number",
        "operated_valve_reference",
        "flow_direction",
        "nominal_diameter_numerical_value_representation",
        "nominal_diameter_representation",
        "nominal_diameter_standard",
        "nominal_diameter_type_representation",
        "line_number",
        "piping_component_name",
        "label_text",
        "function_code",
        "piping_anchor_id",
        "from_equipment_id",
        "to_equipment_id",
        "context_summary",
        "source_doc_id",
        "source_locator",
        "xsd_status",
        "confidence",
        "recommended_action",
        "proposal_status",
        "missing_targets",
        "needs_review",
        "canonical_entity_id",
        "match_confidence",
        "match_method",
        "needs_review_reason",
        "constraint_violations",
    ],
    "t2": [
        "entry_id",
        "device_id",
        "canonical_tag",
        "tag",
        "device_information",
        "source_doc_id",
        "source_locator",
        "confidence",
        "presence_status",
        "record_key",
        "display_name",
        "recommended_action",
        "proposal_status",
        "missing_targets",
        "needs_review",
        "canonical_entity_id",
        "match_confidence",
        "match_method",
        "needs_review_reason",
        "constraint_violations",
    ],
    "t3": [
        "entry_id",
        "device_id",
        "canonical_tag",
        "plt_stelle",
        "funktion",
        "beschreibung",
        "e_schrank",
        "wire_label",
        "source_doc_id",
        "source_locator",
        "confidence",
        "presence_status",
        "record_key",
        "display_name",
        "recommended_action",
        "proposal_status",
        "missing_targets",
        "needs_review",
        "canonical_entity_id",
        "match_confidence",
        "match_method",
        "needs_review_reason",
        "constraint_violations",
    ],
    "t4": [
        "entry_id",
        "device_id",
        "canonical_tag",
        "tag",
        "device_information",
        "art",
        "kanal",
        "yp",
        "position",
        "address",
        "project",
        "source_doc_id",
        "source_locator",
        "confidence",
        "presence_status",
        "record_key",
        "display_name",
        "recommended_action",
        "proposal_status",
        "missing_targets",
        "needs_review",
        "canonical_entity_id",
        "match_confidence",
        "match_method",
        "needs_review_reason",
        "constraint_violations",
    ],
    "t5": [
        "entry_id",
        "device_id",
        "canonical_tag",
        "ifc_class",
        "global_id",
        "tag",
        "has_ports",
        "connected_to",
        "connected_from",
        "has_control_elements",
        "predefined_type",
        "size",
        "valve_mechanism",
        "flow_coefficient",
        "fail_position",
        "manual_override",
        "actuator_application",
        "source_doc_id",
        "source_locator",
        "confidence",
        "presence_status",
        "flange_complete",
        "recommended_action",
        "proposal_status",
        "missing_targets",
        "needs_review",
        "canonical_entity_id",
        "match_confidence",
        "match_method",
        "needs_review_reason",
        "constraint_violations",
    ],
}


@dataclass(frozen=True)
class _MarkerSpec:
    node_type: str
    label: str


def stage_source_type(stage_id: str) -> str:
    stage_key = normalize_identifier(stage_id)
    if stage_key not in STAGE_TO_SOURCE_TYPE:
        raise ValueError(f"Unsupported T1-T5 stage: {stage_id}")
    return STAGE_TO_SOURCE_TYPE[stage_key]


def stage_primary_sheet_name(stage_id: str) -> str:
    return STAGE_TO_PRIMARY_SHEET[normalize_identifier(stage_id)]


def stage_output_fields(stage_id: str) -> list[str]:
    return list(STAGE_TO_OUTPUT_FIELDS[normalize_identifier(stage_id)])


def build_default_t1_t5_bundle(stage_id: str) -> T1T5RuleBundle:
    stage_key = normalize_identifier(stage_id)
    if stage_key not in STAGE_TO_SOURCE_TYPE:
        raise ValueError(f"Unsupported T1-T5 stage: {stage_id}")
    builtin_profile = build_builtin_t1_t5_profile(stage_key)
    return T1T5RuleBundle(
        stage_id=stage_key,
        version=1,
        title=f"Default {stage_key.upper()} rule bundle",
        description="Built-in T1-T5 standardized Excel rules.",
        default_profile_id=builtin_profile.profile_id,
        profiles=[builtin_profile],
        metadata={"builtin": True, "source": "uc1_t1_t5_defaults"},
    )


def build_builtin_t1_t5_profile(stage_id: str) -> T1T5RuleProfile:
    stage_key = normalize_identifier(stage_id)
    fields = stage_output_fields(stage_key)
    return _build_profile(
        stage_key,
        profile_id="builtin",
        title=f"{stage_key.upper()} built-in rules",
        description="Legacy standardized pipeline rendered as an editable T1-T5 rule profile.",
        input_mode="builtin_context",
        workbook_signature=WorkbookSignature(workbook_kind="builtin_context"),
        output_fields=fields,
        marker_specs=_stage_markers(stage_key),
        metadata={
            "builtin": True,
            "source": "legacy_pipeline",
            "source_type": stage_source_type(stage_key),
        },
    )


def build_custom_workbook_profile(stage_id: str, *, profile_id: str = "", title: str = "") -> T1T5RuleProfile:
    stage_key = normalize_identifier(stage_id)
    fields = stage_output_fields(stage_key)
    cleaned_profile_id = clean_cell(profile_id).replace(" ", "_") or "custom"
    return _build_profile(
        stage_key,
        profile_id=cleaned_profile_id,
        title=title or f"{stage_key.upper()} custom workbook",
        description="Map a custom workbook template to the standardized Excel output sheet.",
        input_mode="custom_workbook",
        workbook_signature=WorkbookSignature(sheet_name="", required_headers=[]),
        output_fields=fields,
        marker_specs=[
            _MarkerSpec("WorkbookSheet", "Workbook sheet"),
            _MarkerSpec("HeaderMatch", "Header signature match"),
            _MarkerSpec("RowIterator", "Workbook row iterator"),
        ],
        metadata={
            "builtin": False,
            "source": "custom_workbook",
            "source_type": stage_source_type(stage_key),
        },
    )


def _build_profile(
    stage_id: str,
    *,
    profile_id: str,
    title: str,
    description: str,
    input_mode: str,
    workbook_signature: WorkbookSignature,
    output_fields: list[str],
    marker_specs: list[_MarkerSpec],
    metadata: dict[str, object],
) -> T1T5RuleProfile:
    nodes: list[T1T5Node] = []
    edges: list[T1T5Edge] = []
    edge_order = 0
    input_x = 120.0
    input_y_start = 120.0
    input_y_gap = 128.0
    match_x = 420.0
    match_y_start = 180.0
    match_y_gap = 138.0
    field_x = 760.0
    field_y_start = 80.0
    field_y_gap = 96.0
    build_y = field_y_start + max(0.0, (len(output_fields) - 1) * field_y_gap / 2.0)
    build_x = 1120.0
    output_x = 1440.0

    marker_nodes: list[tuple[_MarkerSpec, T1T5Node]] = []
    input_markers = [marker for marker in marker_specs if _marker_lane(marker.node_type) == "input"]
    match_markers = [marker for marker in marker_specs if _marker_lane(marker.node_type) == "match"]
    flow_ports: list[str] = []
    if input_markers:
        flow_ports.append("_source_flow")
    if match_markers:
        flow_ports.append("_match_flow")

    for index, marker in enumerate(input_markers):
        node = T1T5Node(
            id=_node_id(profile_id, marker.node_type, index),
            node_type=marker.node_type,
            label=marker.label,
            position=(input_x, input_y_start + index * input_y_gap),
            config={},
        )
        nodes.append(node)
        marker_nodes.append((marker, node))

    for index, marker in enumerate(match_markers):
        node = T1T5Node(
            id=_node_id(profile_id, marker.node_type, len(input_markers) + index),
            node_type=marker.node_type,
            label=marker.label,
            position=(match_x, match_y_start + index * match_y_gap),
            config={},
        )
        nodes.append(node)
        marker_nodes.append((marker, node))

    for index, marker in enumerate(marker_specs):
        if marker in input_markers or marker in match_markers:
            continue
        nodes.append(
            T1T5Node(
                id=_node_id(profile_id, marker.node_type, index),
                node_type=marker.node_type,
                label=marker.label,
                position=(input_x, input_y_start + index * input_y_gap),
                config={},
            )
        )

    build_node = T1T5Node(
        id=_node_id(profile_id, "BuildRow"),
        node_type="BuildRow",
        label="BuildRow",
        position=(build_x, build_y),
        config={
            "field_names": list(output_fields),
            "flow_ports": list(flow_ports),
        },
    )
    output_node = T1T5Node(
        id=_node_id(profile_id, "OutputSheet"),
        node_type="OutputSheet",
        label=stage_primary_sheet_name(stage_id),
        position=(output_x, build_y + 48.0),
        config={"sheet_name": stage_primary_sheet_name(stage_id)},
    )
    nodes.append(build_node)
    nodes.append(output_node)

    edges.append(
        T1T5Edge(
            id=_edge_id(profile_id, build_node.id, output_node.id, edge_order),
            from_node=build_node.id,
            to_node=output_node.id,
            target_port="row",
            order=edge_order,
        )
    )
    edge_order += 1

    for field_index, field_name in enumerate(output_fields):
        node = T1T5Node(
            id=_node_id(profile_id, "CellValue", field_name),
            node_type="CellValue",
            label=field_name,
            position=(field_x, field_y_start + field_index * field_y_gap),
            config={"field": field_name},
        )
        nodes.append(node)
        edges.append(
            T1T5Edge(
                id=_edge_id(profile_id, node.id, build_node.id, edge_order),
                from_node=node.id,
                to_node=build_node.id,
                target_port=field_name,
                order=edge_order,
            )
        )
        edge_order += 1

    input_nodes = [node for marker, node in marker_nodes if _marker_lane(marker.node_type) == "input"]
    match_nodes = [node for marker, node in marker_nodes if _marker_lane(marker.node_type) == "match"]

    for source_node, target_node in zip(input_nodes, input_nodes[1:]):
        edges.append(
            T1T5Edge(
                id=_edge_id(profile_id, source_node.id, target_node.id, edge_order),
                from_node=source_node.id,
                to_node=target_node.id,
                target_port="value",
                order=edge_order,
            )
        )
        edge_order += 1

    for source_node, target_node in zip(match_nodes, match_nodes[1:]):
        edges.append(
            T1T5Edge(
                id=_edge_id(profile_id, source_node.id, target_node.id, edge_order),
                from_node=source_node.id,
                to_node=target_node.id,
                target_port="value",
                order=edge_order,
            )
        )
        edge_order += 1

    if input_nodes and match_nodes:
        edges.append(
            T1T5Edge(
                id=_edge_id(profile_id, input_nodes[-1].id, match_nodes[0].id, edge_order),
                from_node=input_nodes[-1].id,
                to_node=match_nodes[0].id,
                target_port="value",
                order=edge_order,
            )
        )
        edge_order += 1

    if input_nodes and flow_ports:
        edges.append(
            T1T5Edge(
                id=_edge_id(profile_id, input_nodes[-1].id, build_node.id, edge_order),
                from_node=input_nodes[-1].id,
                to_node=build_node.id,
                target_port=flow_ports[0],
                order=edge_order,
            )
        )
        edge_order += 1

    if match_nodes and flow_ports:
        edges.append(
            T1T5Edge(
                id=_edge_id(profile_id, match_nodes[-1].id, build_node.id, edge_order),
                from_node=match_nodes[-1].id,
                to_node=build_node.id,
                target_port=flow_ports[-1],
                order=edge_order,
            )
        )
        edge_order += 1

    return T1T5RuleProfile(
        stage_id=stage_id,
        profile_id=profile_id,
        title=title,
        description=description,
        enabled=True,
        priority=100,
        input_mode=input_mode,
        workbook_signature=workbook_signature,
        output_sheet_name=stage_primary_sheet_name(stage_id),
        output_fields=list(output_fields),
        nodes=nodes,
        edges=edges,
        metadata=dict(metadata),
    )


def _stage_markers(stage_id: str) -> list[_MarkerSpec]:
    if stage_id == "t1":
        return [
            _MarkerSpec("BuiltinContext", "R&I source context"),
            _MarkerSpec("StrictMatch", "Cross-document strict match"),
            _MarkerSpec("CompletionMerge", "Completion candidate merge"),
        ]
    if stage_id == "t2":
        return [
            _MarkerSpec("BuiltinContext", "Instrument list source rows"),
            _MarkerSpec("StrictMatch", "Strict tag match"),
            _MarkerSpec("CompletionMerge", "Completion candidate merge"),
        ]
    if stage_id == "t3":
        return [
            _MarkerSpec("BuiltinContext", "Wiring source rows"),
            _MarkerSpec("StrictMatch", "Strict tag match"),
            _MarkerSpec("RelationBuild", "Relation context build"),
        ]
    if stage_id == "t4":
        return [
            _MarkerSpec("BuiltinContext", "Datasheet source rows"),
            _MarkerSpec("StrictMatch", "Strict tag match"),
            _MarkerSpec("ResolverMatch", "Resolver fallback"),
            _MarkerSpec("CompletionMerge", "Completion candidate merge"),
        ]
    return [
        _MarkerSpec("BuiltinContext", "IFC source rows"),
        _MarkerSpec("StrictMatch", "Strict tag match"),
        _MarkerSpec("RelationBuild", "Connectivity build"),
        _MarkerSpec("CompletionMerge", "Completion candidate merge"),
    ]


def _marker_lane(node_type: str) -> str:
    if node_type in {"BuiltinContext", "WorkbookSheet", "HeaderMatch", "RowIterator"}:
        return "input"
    return "match"


def _node_id(*parts: object) -> str:
    cleaned = [normalize_identifier(clean_cell(str(part))) for part in parts if clean_cell(str(part))]
    return "_".join(part for part in cleaned if part)


def _edge_id(profile_id: str, from_node: str, to_node: str, order: int) -> str:
    return f"{normalize_identifier(profile_id) or 'profile'}__{normalize_identifier(from_node) or 'from'}__{normalize_identifier(to_node) or 'to'}__{order}"
