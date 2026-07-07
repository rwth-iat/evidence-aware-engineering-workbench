from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from iev4pi_transformation_tool.core.utils import clean_cell, normalize_identifier
from iev4pi_transformation_tool.ui.i18n import normalize_language, tr


@dataclass(slots=True)
class NodeTooltipContext:
    language: str
    editor_kind: str
    node_type: str
    label: str = ""
    config: dict[str, Any] | None = None
    stage_id: str = ""
    source_type: str = ""
    port_names: list[str] | None = None
    connected_input_count: int = 0
    connected_output_count: int = 0


def _translate_if_present(language: str, key: str) -> str:
    value = tr(language, key)
    return "" if value == key else value


def _display_title(context: NodeTooltipContext) -> str:
    node_type = clean_cell(context.node_type)
    label = clean_cell(context.label)
    if label and label != node_type:
        return f"{node_type}: {label}"
    return node_type or label or tr(context.language, "tooltip.node.fallback.title")


def _specific_detail(context: NodeTooltipContext) -> str:
    language = normalize_language(context.language)
    node_type = clean_cell(context.node_type)
    label_slug = normalize_identifier(context.label)
    candidates: list[str] = []
    if context.editor_kind == "t1t5":
        stage_id = normalize_identifier(context.stage_id)
        if label_slug:
            candidates.append(f"tooltip.t1t5.label.{label_slug}.detail")
        if stage_id:
            candidates.append(f"tooltip.t1t5.{stage_id}.{node_type}.detail")
        candidates.append(f"tooltip.t1t5.default.{node_type}.detail")
    elif context.editor_kind == "tx":
        source_type = normalize_identifier(context.source_type)
        if label_slug:
            candidates.append(f"tooltip.tx.label.{label_slug}.detail")
        if source_type:
            candidates.append(f"tooltip.tx.{source_type}.{node_type}.detail")
        candidates.append(f"tooltip.tx.default.{node_type}.detail")
    for key in candidates:
        text = _translate_if_present(language, key)
        if text:
            return text
    return ""


def _list_values(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_cell(item) for item in value if clean_cell(item)]
    if isinstance(value, tuple):
        return [clean_cell(item) for item in value if clean_cell(item)]
    return []


def _summary_parts(context: NodeTooltipContext) -> list[str]:
    language = normalize_language(context.language)
    node_type = clean_cell(context.node_type)
    config = dict(context.config or {})
    parts: list[str] = []

    def add(key: str, **kwargs) -> None:
        text = tr(language, key, **kwargs)
        if text and text != key:
            parts.append(text)

    field_value = clean_cell(config.get("field", ""))
    if node_type in {"CellValue", "InputColumn"} and field_value:
        add("tooltip.summary.field", value=field_value)
    if node_type == "InputColumn":
        mode = clean_cell(config.get("mode", ""))
        separator = clean_cell(config.get("separator", ""))
        if mode:
            add("tooltip.summary.mode", value=mode)
        if separator:
            add("tooltip.summary.separator", value=separator)
    elif node_type == "Constant":
        constant_value = clean_cell(config.get("value", ""))
        if constant_value:
            add("tooltip.summary.constant", value=constant_value)
    elif node_type == "RegexExtract":
        pattern = clean_cell(config.get("pattern", ""))
        group = config.get("group", 1)
        default_value = clean_cell(config.get("default", ""))
        if pattern:
            add("tooltip.summary.pattern", value=pattern)
        add("tooltip.summary.group", value=group)
        if default_value:
            add("tooltip.summary.default_value", value=default_value)
    elif node_type in {"Concat", "PreferFirstNonEmpty"}:
        separator = clean_cell(config.get("separator", ""))
        if separator:
            add("tooltip.summary.separator", value=separator)
        add("tooltip.summary.connected_inputs", count=context.connected_input_count)
        fallback = clean_cell(config.get("fallback", ""))
        if fallback:
            add("tooltip.summary.fallback", value=fallback)
    elif node_type == "BuildRow":
        field_names = _list_values(config.get("field_names", []))
        flow_ports = _list_values(config.get("flow_ports", []))
        add("tooltip.summary.target_fields", count=len(field_names))
        if flow_ports:
            add("tooltip.summary.flow_inputs", count=len(flow_ports))
        add("tooltip.summary.connected_inputs", count=context.connected_input_count)
    elif node_type == "OutputSheet":
        sheet_name = clean_cell(config.get("sheet_name", "")) or clean_cell(context.label)
        if sheet_name:
            add("tooltip.summary.sheet", value=sheet_name)
    elif node_type in {"LookupMap", "MapEnum"}:
        mapping = config.get("mapping", {})
        mapping_count = len(mapping) if isinstance(mapping, dict) else 0
        add("tooltip.summary.mapping_entries", count=mapping_count)
        default_value = clean_cell(config.get("default", ""))
        if default_value:
            add("tooltip.summary.default_value", value=default_value)
    elif node_type == "BoolMap":
        true_value = clean_cell(config.get("true_value", ""))
        false_value = clean_cell(config.get("false_value", ""))
        if true_value:
            add("tooltip.summary.true_value", value=true_value)
        if false_value:
            add("tooltip.summary.false_value", value=false_value)
    elif node_type == "Condition":
        operator = clean_cell(config.get("operator", ""))
        compare_to = clean_cell(config.get("compare_to", ""))
        true_value = clean_cell(config.get("true_value", ""))
        false_value = clean_cell(config.get("false_value", ""))
        if operator:
            add("tooltip.summary.operator", value=operator)
        if compare_to:
            add("tooltip.summary.compare_to", value=compare_to)
        if true_value:
            add("tooltip.summary.true_value", value=true_value)
        if false_value:
            add("tooltip.summary.false_value", value=false_value)
    elif node_type == "ConfidenceGate":
        min_confidence = config.get("min_confidence", "")
        fallback = clean_cell(config.get("fallback", ""))
        if min_confidence != "":
            add("tooltip.summary.min_confidence", value=min_confidence)
        if fallback:
            add("tooltip.summary.fallback", value=fallback)
    elif node_type == "WorkbookSheet":
        sheet_name = clean_cell(config.get("sheet_name", ""))
        if sheet_name:
            add("tooltip.summary.sheet", value=sheet_name)
    elif node_type == "HeaderMatch":
        required_headers = _list_values(config.get("required_headers", []))
        optional_headers = _list_values(config.get("optional_headers", []))
        add("tooltip.summary.required_headers", count=len(required_headers))
        add("tooltip.summary.optional_headers", count=len(optional_headers))
    elif node_type == "OutputProperty":
        property_name = clean_cell(config.get("property_name", ""))
        if property_name:
            add("tooltip.summary.property", value=property_name)
    elif node_type == "OutputSubmodel":
        submodel_name = clean_cell(config.get("id_short", ""))
        if submodel_name:
            add("tooltip.summary.submodel", value=submodel_name)
        add("tooltip.summary.connected_inputs", count=context.connected_input_count)

    if not parts:
        value = clean_cell(config.get("value", ""))
        if value:
            add("tooltip.summary.value", value=value)
    return parts


def build_node_tooltip(context: NodeTooltipContext, *, include_config_summary: bool = True) -> str:
    language = normalize_language(context.language)
    node_type = clean_cell(context.node_type)
    generic = _translate_if_present(language, f"tooltip.node.{node_type}.generic")
    if not generic:
        generic = tr(language, "tooltip.node.fallback.generic")
    specific = _specific_detail(context)
    usage = _translate_if_present(language, f"tooltip.node.{node_type}.usage")
    if not usage:
        usage = tr(language, "tooltip.node.fallback.usage")
    details = generic if not specific else f"{generic} {specific}"

    lines = [
        _display_title(context),
        tr(language, "tooltip.section.what", text=details),
    ]
    if include_config_summary:
        summary = "; ".join(_summary_parts(context)) or tr(language, "tooltip.summary.none")
        lines.append(tr(language, "tooltip.section.current", text=summary))
    lines.append(tr(language, "tooltip.section.how", text=usage))
    return "\n\n".join(line for line in lines if clean_cell(line))


def build_palette_node_tooltip(
    *,
    language: str,
    editor_kind: str,
    node_type: str,
    stage_id: str = "",
    source_type: str = "",
) -> str:
    return build_node_tooltip(
        NodeTooltipContext(
            language=language,
            editor_kind=editor_kind,
            node_type=node_type,
            stage_id=stage_id,
            source_type=source_type,
        ),
        include_config_summary=False,
    )


def build_inspector_tooltip(
    *,
    language: str,
    control_key: str,
    context: NodeTooltipContext | None = None,
) -> str:
    language = normalize_language(language)
    if context is None:
        return tr(language, "tooltip.inspector.none")
    base = build_node_tooltip(context)
    control_text = _translate_if_present(language, f"tooltip.control.{control_key}")
    if not control_text:
        return base
    return f"{base}\n\n{control_text}"
