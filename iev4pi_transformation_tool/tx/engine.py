from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iev4pi_transformation_tool.core.utils import clean_cell, normalize_identifier
from iev4pi_transformation_tool.tx.models import (
    ALLOWED_TX_NODE_TYPES,
    TxEdge,
    TxExecutionTrace,
    TxNode,
    TxPreviewResult,
    TxRuleSet,
    TxTraceStep,
    TxValidationIssue,
)


TRUTHY_VALUES = {"1", "true", "yes", "present", "complete", "matched"}
FALSY_VALUES = {"0", "false", "no", "missing", "unknown", "deferred", "partial", ""}


@dataclass
class _NodeValue:
    value: str = ""
    gate_passed: bool = True


class TxExecutor:
    def validate(self, rule_set: TxRuleSet) -> list[TxValidationIssue]:
        issues: list[TxValidationIssue] = []
        node_ids = [node.id for node in rule_set.nodes]
        edge_ids = [edge.id for edge in rule_set.edges]
        duplicate_nodes = _duplicates(node_ids)
        duplicate_edges = _duplicates(edge_ids)
        for node_id in duplicate_nodes:
            issues.append(TxValidationIssue(code="duplicate_node_id", message=f"Duplicate node id `{node_id}`.", node_id=node_id))
        for edge_id in duplicate_edges:
            issues.append(TxValidationIssue(code="duplicate_edge_id", message=f"Duplicate edge id `{edge_id}`.", edge_id=edge_id))
        node_map = {node.id: node for node in rule_set.nodes}
        submodel_nodes = []
        for node in rule_set.nodes:
            if node.node_type not in ALLOWED_TX_NODE_TYPES:
                issues.append(
                    TxValidationIssue(
                        code="unsupported_node_type",
                        message=f"Node `{node.id}` uses unsupported node type `{node.node_type}`.",
                        node_id=node.id,
                    )
                )
                continue
            if node.node_type == "InputColumn" and not clean_cell(node.config.get("field", "")):
                issues.append(TxValidationIssue(code="missing_input_field", message="InputColumn requires `field`.", node_id=node.id))
            if node.node_type == "OutputProperty" and not clean_cell(node.config.get("property_name", "")):
                issues.append(TxValidationIssue(code="missing_property_name", message="OutputProperty requires `property_name`.", node_id=node.id))
            if node.node_type == "OutputSubmodel":
                if not clean_cell(node.config.get("id_short", "")):
                    issues.append(TxValidationIssue(code="missing_submodel_id_short", message="OutputSubmodel requires `id_short`.", node_id=node.id))
                submodel_nodes.append(node.id)
        for edge in rule_set.edges:
            if edge.from_node not in node_map:
                issues.append(TxValidationIssue(code="missing_from_node", message=f"Edge `{edge.id}` references missing source node `{edge.from_node}`.", edge_id=edge.id))
            if edge.to_node not in node_map:
                issues.append(TxValidationIssue(code="missing_to_node", message=f"Edge `{edge.id}` references missing target node `{edge.to_node}`.", edge_id=edge.id))
        if not submodel_nodes:
            issues.append(TxValidationIssue(code="missing_output_submodel", message="At least one OutputSubmodel node is required."))
        for node in rule_set.nodes:
            if node.node_type == "OutputProperty":
                has_submodel = any(edge.from_node == node.id and node_map.get(edge.to_node, TxNode(id="", node_type="")).node_type == "OutputSubmodel" for edge in rule_set.edges if edge.to_node in node_map)
                if not has_submodel:
                    issues.append(TxValidationIssue(code="unbound_output_property", message=f"OutputProperty `{node.id}` is not connected to an OutputSubmodel.", node_id=node.id))
        if not any(issue.severity == "error" for issue in issues):
            cycle = self._first_cycle(rule_set)
            if cycle:
                issues.append(TxValidationIssue(code="cycle_detected", message=f"Rule graph contains a cycle involving `{cycle}`.", node_id=cycle))
        return issues

    def preview(
        self,
        rule_set: TxRuleSet,
        rows: list[dict[str, Any]],
        *,
        identity_value: str = "",
        workbook_path: Path | None = None,
        source_type: str | None = None,
    ) -> TxPreviewResult:
        issues = self.validate(rule_set)
        if any(issue.severity == "error" for issue in issues):
            return TxPreviewResult(rule_set=rule_set, identity_key=identity_value or "", issues=issues)
        payload, traces = self.execute(
            rule_set,
            rows,
            identity_value=identity_value,
            workbook_path=workbook_path,
            source_type=source_type or rule_set.source_type,
        )
        derived_identity = identity_value or self._identity_value(rule_set, rows)
        return TxPreviewResult(
            rule_set=rule_set,
            identity_key=derived_identity,
            payload=payload,
            traces=traces,
            issues=issues,
        )

    def execute(
        self,
        rule_set: TxRuleSet,
        rows: list[dict[str, Any]],
        *,
        identity_value: str = "",
        workbook_path: Path | None = None,
        source_type: str | None = None,
    ) -> tuple[dict[str, Any], list[TxExecutionTrace]]:
        source_type = clean_cell(source_type or rule_set.source_type) or rule_set.source_type
        row_dicts = [{clean_cell(key): clean_cell(value) for key, value in row.items()} for row in rows]
        node_map = {node.id: node for node in rule_set.nodes}
        incoming = self._incoming_edges(rule_set.edges)
        ordered_nodes = self._topological_order(rule_set)
        values: dict[str, _NodeValue] = {}

        for node_id in ordered_nodes:
            node = node_map[node_id]
            parent_values = [
                values.get(edge.from_node, _NodeValue())
                for edge in sorted(incoming.get(node_id, []), key=lambda item: (item.order, item.id))
            ]
            values[node_id] = self._evaluate_node(node, parent_values, row_dicts)

        submodel_values: dict[str, list[tuple[TxNode, str]]] = defaultdict(list)
        traces: list[TxExecutionTrace] = []
        for node_id in ordered_nodes:
            node = node_map[node_id]
            if node.node_type != "OutputProperty":
                continue
            property_value = values.get(node_id, _NodeValue())
            submodel_node = self._connected_submodel_node(node_id, rule_set.edges, node_map)
            if submodel_node is None:
                continue
            submodel_values[submodel_node.config["id_short"]].append((node, property_value.value))
            traces.append(
                TxExecutionTrace(
                    source_type=source_type,
                    identity_key=identity_value or self._identity_value(rule_set, row_dicts),
                    submodel_id_short=clean_cell(submodel_node.config.get("id_short", "")),
                    output_property=clean_cell(node.config.get("property_name", "")) or node.label or node.id,
                    value=property_value.value,
                    gate_passed=property_value.gate_passed,
                    steps=self._trace_steps(node_id, incoming, node_map, values),
                )
            )

        payload = _make_aas_payload(
            source_type,
            identity_value or self._identity_value(rule_set, row_dicts),
            [
                (
                    submodel_name,
                    {clean_cell(node.config.get("property_name", "")) or node.label or node.id: clean_cell(value) for node, value in properties},
                )
                for submodel_name, properties in submodel_values.items()
            ],
        )
        if workbook_path is not None:
            payload["x-ievpi-tx-workbook"] = str(workbook_path)
        payload["x-ievpi-tx-rule_version"] = rule_set.version
        payload["x-ievpi-tx-source"] = clean_cell(rule_set.metadata.get("source", "")) or "custom"
        return payload, traces

    def _trace_steps(
        self,
        node_id: str,
        incoming: dict[str, list[TxEdge]],
        node_map: dict[str, TxNode],
        values: dict[str, _NodeValue],
    ) -> list[TxTraceStep]:
        ordered: list[TxTraceStep] = []
        seen: set[str] = set()
        queue = deque([node_id])
        while queue:
            current = queue.popleft()
            if current in seen or current not in node_map:
                continue
            seen.add(current)
            node = node_map[current]
            node_value = values.get(current, _NodeValue())
            ordered.append(
                TxTraceStep(
                    node_id=node.id,
                    node_type=node.node_type,
                    label=node.label,
                    value=node_value.value,
                    summary=self._node_summary(node),
                )
            )
            for edge in sorted(incoming.get(current, []), key=lambda item: (item.order, item.id), reverse=True):
                queue.appendleft(edge.from_node)
        return ordered

    def _node_summary(self, node: TxNode) -> str:
        if node.node_type == "InputColumn":
            return f"Input `{clean_cell(node.config.get('field', ''))}` via {clean_cell(node.config.get('mode', 'join'))}."
        if node.node_type == "OutputProperty":
            return f"Output property `{clean_cell(node.config.get('property_name', ''))}`."
        if node.node_type == "OutputSubmodel":
            return f"Output submodel `{clean_cell(node.config.get('id_short', ''))}`."
        if node.node_type == "Constant":
            return "Constant value."
        return node.node_type

    def _connected_submodel_node(
        self,
        node_id: str,
        edges: list[TxEdge],
        node_map: dict[str, TxNode],
    ) -> TxNode | None:
        for edge in sorted(edges, key=lambda item: (item.order, item.id)):
            if edge.from_node != node_id:
                continue
            candidate = node_map.get(edge.to_node)
            if candidate is not None and candidate.node_type == "OutputSubmodel":
                return candidate
        return None

    def _evaluate_node(self, node: TxNode, inputs: list[_NodeValue], rows: list[dict[str, str]]) -> _NodeValue:
        if node.node_type == "InputColumn":
            return _NodeValue(
                value=self._input_column_value(rows, clean_cell(node.config.get("field", "")), clean_cell(node.config.get("mode", "join")) or "join", clean_cell(node.config.get("separator", " | ")) or " | "),
            )
        if node.node_type == "Constant":
            return _NodeValue(value=clean_cell(node.config.get("value", "")))
        if node.node_type == "NormalizeIdentifier":
            return _NodeValue(value=normalize_identifier(inputs[0].value if inputs else ""))
        if node.node_type == "RegexExtract":
            return _NodeValue(value=self._regex_extract(inputs[0].value if inputs else "", node))
        if node.node_type == "MapEnum":
            return _NodeValue(value=self._map_enum(inputs[0].value if inputs else "", node))
        if node.node_type == "BoolMap":
            return _NodeValue(value=self._bool_map(inputs[0].value if inputs else "", node))
        if node.node_type == "Concat":
            separator = clean_cell(node.config.get("separator", " | ")) or " | "
            return _NodeValue(value=separator.join(value.value for value in inputs if clean_cell(value.value)))
        if node.node_type == "PreferFirstNonEmpty":
            return _NodeValue(value=next((clean_cell(value.value) for value in inputs if clean_cell(value.value)), clean_cell(node.config.get("fallback", ""))))
        if node.node_type == "Condition":
            return _NodeValue(value=self._condition_value(node, inputs))
        if node.node_type == "ConfidenceGate":
            return self._confidence_gate(node, inputs)
        if node.node_type in {"OutputProperty", "OutputSubmodel"}:
            return _NodeValue(value=inputs[0].value if inputs else "")
        return _NodeValue(value="")

    def _condition_value(self, node: TxNode, inputs: list[_NodeValue]) -> str:
        subject = clean_cell(inputs[0].value if inputs else "")
        compare_to = clean_cell(inputs[1].value if len(inputs) > 1 else node.config.get("compare_to", ""))
        operator = clean_cell(node.config.get("operator", "equals")) or "equals"
        matched = False
        if operator == "equals":
            matched = subject == compare_to
        elif operator == "not_equals":
            matched = subject != compare_to
        elif operator == "contains":
            matched = compare_to in subject if compare_to else False
        elif operator == "regex":
            matched = bool(compare_to and re.search(compare_to, subject))
        elif operator == "is_truthy":
            matched = _boolish(subject)
        true_value = clean_cell(node.config.get("true_value", ""))
        false_value = clean_cell(node.config.get("false_value", ""))
        if matched:
            return true_value or subject
        return false_value

    def _confidence_gate(self, node: TxNode, inputs: list[_NodeValue]) -> _NodeValue:
        value = clean_cell(inputs[0].value if inputs else "")
        confidence_input = clean_cell(inputs[1].value if len(inputs) > 1 else node.config.get("confidence", ""))
        try:
            confidence = float(confidence_input or "0")
        except ValueError:
            confidence = 0.0
        minimum = float(node.config.get("min_confidence", 0.0) or 0.0)
        if confidence >= minimum:
            return _NodeValue(value=value, gate_passed=True)
        return _NodeValue(value=clean_cell(node.config.get("fallback", "")), gate_passed=False)

    def _map_enum(self, value: str, node: TxNode) -> str:
        mapping = node.config.get("mapping", {})
        if not isinstance(mapping, dict):
            return clean_cell(value)
        cleaned_value = clean_cell(value)
        if cleaned_value in mapping:
            return clean_cell(mapping.get(cleaned_value, ""))
        normalized = cleaned_value.lower()
        for key, mapped_value in mapping.items():
            if clean_cell(key).lower() == normalized:
                return clean_cell(mapped_value)
        return clean_cell(node.config.get("default", cleaned_value))

    def _bool_map(self, value: str, node: TxNode) -> str:
        return clean_cell(node.config.get("true_value", "true")) if _boolish(value) else clean_cell(node.config.get("false_value", "false"))

    def _regex_extract(self, value: str, node: TxNode) -> str:
        pattern = clean_cell(node.config.get("pattern", ""))
        if not pattern:
            return ""
        flags = re.IGNORECASE if bool(node.config.get("ignore_case", True)) else 0
        match = re.search(pattern, clean_cell(value), flags)
        if not match:
            return clean_cell(node.config.get("default", ""))
        try:
            group = int(node.config.get("group", 1) or 1)
        except ValueError:
            group = 1
        return clean_cell(match.group(group))

    def _input_column_value(self, rows: list[dict[str, str]], field_name: str, mode: str, separator: str) -> str:
        if not field_name:
            return ""
        values = [clean_cell(row.get(field_name, "")) for row in rows]
        if mode == "first":
            return values[0] if values else ""
        if mode == "first_non_empty":
            return next((value for value in values if value), "")
        if mode == "count_present":
            return str(sum(1 for value in values if value))
        if mode == "bool_any":
            return "true" if any(_boolish(value) for value in values) else "false"
        unique_values: list[str] = []
        for value in values:
            if value and value not in unique_values:
                unique_values.append(value)
        return separator.join(unique_values)

    def _incoming_edges(self, edges: list[TxEdge]) -> dict[str, list[TxEdge]]:
        incoming: dict[str, list[TxEdge]] = defaultdict(list)
        for edge in edges:
            incoming[edge.to_node].append(edge)
        return incoming

    def _topological_order(self, rule_set: TxRuleSet) -> list[str]:
        outgoing: dict[str, list[str]] = defaultdict(list)
        indegree: dict[str, int] = {node.id: 0 for node in rule_set.nodes}
        for edge in rule_set.edges:
            if edge.from_node not in indegree or edge.to_node not in indegree:
                continue
            outgoing[edge.from_node].append(edge.to_node)
            indegree[edge.to_node] += 1
        queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
        ordered: list[str] = []
        while queue:
            node_id = queue.popleft()
            ordered.append(node_id)
            for target in sorted(outgoing.get(node_id, [])):
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        return ordered if len(ordered) == len(indegree) else list(indegree.keys())

    def _first_cycle(self, rule_set: TxRuleSet) -> str:
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in rule_set.edges:
            adjacency[edge.from_node].append(edge.to_node)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> str:
            if node_id in visited:
                return ""
            if node_id in visiting:
                return node_id
            visiting.add(node_id)
            for target in adjacency.get(node_id, []):
                cycle = visit(target)
                if cycle:
                    return cycle
            visiting.remove(node_id)
            visited.add(node_id)
            return ""

        for node in rule_set.nodes:
            cycle = visit(node.id)
            if cycle:
                return cycle
        return ""

    def _identity_value(self, rule_set: TxRuleSet, rows: list[dict[str, str]]) -> str:
        for field_name in rule_set.identity_fields:
            value = self._input_column_value(rows, field_name, "first_non_empty", " | ")
            if value:
                return value
        return clean_cell(rule_set.source_type or "entry")


def _make_aas_property(submodel_name: str, property_name: str, property_value: str) -> dict[str, object]:
    """Build a single AAS Property dict, optionally with semanticId."""
    from iev4pi_transformation_tool.core.semantic_ids import get_irdi

    prop: dict[str, object] = {
        "modelType": "Property",
        "idShort": property_name,
        "valueType": "xs:boolean" if clean_cell(property_value).lower() in {"true", "false"} else "xs:string",
        "value": clean_cell(property_value),
    }
    irdi = get_irdi(submodel_name, property_name)
    if irdi:
        prop["semanticId"] = {
            "type": "ExternalReference",
            "keys": [{"type": "GlobalReference", "value": irdi}],
        }
    return prop


def _make_aas_payload(
    source_type: str,
    identity_value: str,
    submodel_specs: list[tuple[str, dict[str, str]]],
) -> dict[str, object]:
    identity = normalize_identifier(identity_value) or "entry"
    shell_id = f"urn:ievpi:aas:{source_type}:{identity}"
    asset_id = f"urn:ievpi:asset:{source_type}:{identity}"
    submodels: list[dict[str, object]] = []
    submodel_refs: list[dict[str, object]] = []
    for submodel_name, properties in submodel_specs:
        submodel_id = f"{shell_id}:{normalize_identifier(submodel_name)}"
        submodels.append(
            {
                "modelType": "Submodel",
                "id": submodel_id,
                "idShort": submodel_name,
                "submodelElements": [
                    _make_aas_property(submodel_name, property_name, property_value)
                    for property_name, property_value in properties.items()
                ],
            }
        )
        submodel_refs.append({"type": "ModelReference", "keys": [{"type": "Submodel", "value": submodel_id}]})

    return {
        "x-ievpi-source_type": source_type,
        "assetAdministrationShells": [
            {
                "modelType": "AssetAdministrationShell",
                "id": shell_id,
                "idShort": identity_value.replace(".", "_").replace("-", "_"),
                "assetInformation": {"assetKind": "Instance", "globalAssetId": asset_id},
                "submodels": submodel_refs,
            }
        ],
        "submodels": submodels,
        "conceptDescriptions": [],
    }


def _boolish(value: str) -> bool:
    normalized = clean_cell(value).lower()
    if normalized in TRUTHY_VALUES:
        return True
    if normalized in FALSY_VALUES:
        return False
    try:
        return float(normalized or "0") > 0
    except ValueError:
        return False


def _duplicates(values: list[str]) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    duplicates: list[str] = []
    for value in values:
        counts[value] += 1
        if counts[value] == 2:
            duplicates.append(value)
    return duplicates
