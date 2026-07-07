from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iev4pi_transformation_tool.core.utils import clean_cell, normalize_identifier
from iev4pi_transformation_tool.t1t5.models import (
    ALLOWED_T1T5_NODE_TYPES,
    T1T5Edge,
    T1T5PreviewResult,
    T1T5ProfileMatch,
    T1T5RuleBundle,
    T1T5RuleProfile,
    T1T5ValidationIssue,
)


@dataclass
class _NodeValue:
    value: Any = ""


class T1T5Executor:
    def validate_profile(self, profile: T1T5RuleProfile) -> list[T1T5ValidationIssue]:
        issues: list[T1T5ValidationIssue] = []
        node_ids = [node.id for node in profile.nodes]
        edge_ids = [edge.id for edge in profile.edges]
        duplicate_nodes = _duplicates(node_ids)
        duplicate_edges = _duplicates(edge_ids)
        for node_id in duplicate_nodes:
            issues.append(T1T5ValidationIssue(code="duplicate_node_id", message=f"Duplicate node id `{node_id}`.", node_id=node_id))
        for edge_id in duplicate_edges:
            issues.append(T1T5ValidationIssue(code="duplicate_edge_id", message=f"Duplicate edge id `{edge_id}`.", edge_id=edge_id))

        node_map = {node.id: node for node in profile.nodes}
        build_rows = []
        output_sheets = []
        for node in profile.nodes:
            if node.node_type not in ALLOWED_T1T5_NODE_TYPES:
                issues.append(
                    T1T5ValidationIssue(
                        code="unsupported_node_type",
                        message=f"Node `{node.id}` uses unsupported node type `{node.node_type}`.",
                        node_id=node.id,
                    )
                )
                continue
            if node.node_type == "CellValue" and not clean_cell(node.config.get("field", "")):
                issues.append(T1T5ValidationIssue(code="missing_cell_field", message="CellValue requires `field`.", node_id=node.id))
            if node.node_type == "BuildRow":
                field_names = node.config.get("field_names", [])
                if not isinstance(field_names, list) or not any(clean_cell(item) for item in field_names):
                    issues.append(T1T5ValidationIssue(code="missing_build_fields", message="BuildRow requires `field_names`.", node_id=node.id))
                build_rows.append(node.id)
            if node.node_type == "OutputSheet":
                if not clean_cell(node.config.get("sheet_name", "")):
                    issues.append(T1T5ValidationIssue(code="missing_sheet_name", message="OutputSheet requires `sheet_name`.", node_id=node.id))
                output_sheets.append(node.id)
        for edge in profile.edges:
            if edge.from_node not in node_map:
                issues.append(T1T5ValidationIssue(code="missing_from_node", message=f"Edge `{edge.id}` references missing source node `{edge.from_node}`.", edge_id=edge.id))
            if edge.to_node not in node_map:
                issues.append(T1T5ValidationIssue(code="missing_to_node", message=f"Edge `{edge.id}` references missing target node `{edge.to_node}`.", edge_id=edge.id))
        if not build_rows:
            issues.append(T1T5ValidationIssue(code="missing_build_row", message="At least one BuildRow node is required."))
        if not output_sheets:
            issues.append(T1T5ValidationIssue(code="missing_output_sheet", message="At least one OutputSheet node is required."))
        if not any(issue.severity == "error" for issue in issues):
            cycle = self._first_cycle(profile)
            if cycle:
                issues.append(T1T5ValidationIssue(code="cycle_detected", message=f"Rule graph contains a cycle involving `{cycle}`.", node_id=cycle))
        return issues

    def validate_bundle(self, bundle: T1T5RuleBundle) -> list[T1T5ValidationIssue]:
        issues: list[T1T5ValidationIssue] = []
        profile_ids = [profile.profile_id for profile in bundle.profiles]
        for profile_id in _duplicates(profile_ids):
            issues.append(T1T5ValidationIssue(code="duplicate_profile_id", message=f"Duplicate profile id `{profile_id}`."))
        for profile in bundle.profiles:
            issues.extend(self.validate_profile(profile))
        return issues

    def resolve_profile(
        self,
        bundle: T1T5RuleBundle,
        *,
        workbook_sheets: dict[str, list[dict[str, str]]] | None = None,
        requested_profile_id: str = "",
    ) -> tuple[T1T5RuleProfile | None, T1T5ProfileMatch | None]:
        requested = clean_cell(requested_profile_id)
        if requested:
            for profile in bundle.profiles:
                if profile.profile_id == requested:
                    match = self.match_profile(profile, workbook_sheets) if profile.input_mode == "custom_workbook" else T1T5ProfileMatch(profile_id=profile.profile_id, score=1.0, reason="selected_profile")
                    return profile, match
            return None, None

        active_profiles = [profile for profile in bundle.profiles if profile.enabled]
        if workbook_sheets:
            matches = [
                (profile, self.match_profile(profile, workbook_sheets))
                for profile in active_profiles
                if profile.input_mode == "custom_workbook"
            ]
            matches = [item for item in matches if item[1].score > 0.0]
            matches.sort(key=lambda item: (item[1].score, item[0].priority), reverse=True)
            if matches:
                return matches[0]

        default_profile_id = clean_cell(bundle.default_profile_id)
        if default_profile_id:
            for profile in active_profiles:
                if profile.profile_id == default_profile_id:
                    return profile, T1T5ProfileMatch(profile_id=profile.profile_id, score=1.0, reason="default_profile")

        builtin = next((profile for profile in active_profiles if profile.input_mode == "builtin_context"), None)
        if builtin is not None:
            return builtin, T1T5ProfileMatch(profile_id=builtin.profile_id, score=1.0, reason="builtin_context")

        custom = active_profiles[0] if active_profiles else None
        if custom is None:
            return None, None
        return custom, self.match_profile(custom, workbook_sheets) if workbook_sheets else T1T5ProfileMatch(profile_id=custom.profile_id, score=0.0, reason="no_input")

    def match_profile(
        self,
        profile: T1T5RuleProfile,
        workbook_sheets: dict[str, list[dict[str, str]]] | None,
    ) -> T1T5ProfileMatch:
        if profile.input_mode != "custom_workbook" or not workbook_sheets:
            return T1T5ProfileMatch(profile_id=profile.profile_id, score=0.0, reason="not_custom_workbook")
        signature = profile.workbook_signature
        required_headers = [normalize_identifier(item) for item in signature.required_headers if clean_cell(item)]
        optional_headers = [normalize_identifier(item) for item in signature.optional_headers if clean_cell(item)]
        target_sheet = normalize_identifier(signature.sheet_name)
        best = T1T5ProfileMatch(profile_id=profile.profile_id, score=0.0, reason="no_matching_sheet")
        for sheet_name, rows in workbook_sheets.items():
            headers = []
            if rows:
                headers = [normalize_identifier(key) for key in rows[0].keys() if clean_cell(key)]
            sheet_score = 0.0
            if target_sheet:
                if normalize_identifier(sheet_name) == target_sheet:
                    sheet_score += 0.5
                else:
                    sheet_score -= 0.2
            matched_required = [header for header in required_headers if header in headers]
            matched_optional = [header for header in optional_headers if header in headers]
            if required_headers:
                sheet_score += 0.4 * (len(matched_required) / max(1, len(required_headers)))
            elif headers:
                sheet_score += 0.15
            if optional_headers:
                sheet_score += 0.1 * (len(matched_optional) / max(1, len(optional_headers)))
            if signature.header_fingerprint:
                fingerprint = "|".join(headers[: min(12, len(headers))])
                if clean_cell(signature.header_fingerprint).lower() == fingerprint.lower():
                    sheet_score += 0.1
            normalized_score = max(0.0, min(1.0, round(sheet_score, 4)))
            if normalized_score > best.score:
                best = T1T5ProfileMatch(
                    profile_id=profile.profile_id,
                    score=normalized_score,
                    matched_sheet_name=sheet_name,
                    matched_headers=matched_required,
                    reason="signature_match" if normalized_score > 0 else "no_matching_sheet",
                )
        return best

    def preview(
        self,
        bundle: T1T5RuleBundle,
        *,
        workbook_path: Path | None = None,
        workbook_sheets: dict[str, list[dict[str, str]]] | None = None,
        input_rows: list[dict[str, str]] | None = None,
        requested_profile_id: str = "",
    ) -> T1T5PreviewResult:
        bundle_issues = self.validate_bundle(bundle)
        if any(issue.severity == "error" for issue in bundle_issues):
            return T1T5PreviewResult(bundle=bundle, issues=bundle_issues)
        profile, profile_match = self.resolve_profile(
            bundle,
            workbook_sheets=workbook_sheets,
            requested_profile_id=requested_profile_id,
        )
        if profile is None:
            return T1T5PreviewResult(
                bundle=bundle,
                issues=[T1T5ValidationIssue(code="missing_profile", message="No active T1-T5 profile is available.")],
            )
        issues = self.validate_profile(profile)
        if any(issue.severity == "error" for issue in issues):
            return T1T5PreviewResult(
                bundle=bundle,
                selected_profile_id=profile.profile_id,
                profile_match=profile_match,
                issues=issues,
            )
        output_rows = self.execute_profile(
            profile,
            workbook_path=workbook_path,
            workbook_sheets=workbook_sheets,
            input_rows=input_rows,
            profile_match=profile_match,
        )
        return T1T5PreviewResult(
            bundle=bundle,
            selected_profile_id=profile.profile_id,
            profile_match=profile_match,
            output_rows=output_rows,
            issues=issues,
        )

    def execute_profile(
        self,
        profile: T1T5RuleProfile,
        *,
        workbook_path: Path | None = None,
        workbook_sheets: dict[str, list[dict[str, str]]] | None = None,
        input_rows: list[dict[str, str]] | None = None,
        profile_match: T1T5ProfileMatch | None = None,
    ) -> list[dict[str, str]]:
        rows = self._source_rows(
            profile,
            workbook_path=workbook_path,
            workbook_sheets=workbook_sheets,
            input_rows=input_rows,
            profile_match=profile_match,
        )
        if not rows:
            return []
        node_map = {node.id: node for node in profile.nodes}
        incoming = self._incoming_edges(profile.edges)
        ordered_nodes = self._topological_order(profile)
        output_rows: list[dict[str, str]] = []
        output_sheet_ids = {
            edge.from_node
            for edge in profile.edges
            if node_map.get(edge.to_node) is not None and node_map[edge.to_node].node_type == "OutputSheet"
        }
        if not output_sheet_ids:
            output_sheet_ids = {node.id for node in profile.nodes if node.node_type == "BuildRow"}

        for row in rows:
            values: dict[str, _NodeValue] = {}
            for node_id in ordered_nodes:
                node = node_map[node_id]
                parent_values = [
                    values.get(edge.from_node, _NodeValue())
                    for edge in sorted(incoming.get(node_id, []), key=lambda item: (item.order, item.id))
                ]
                parent_map = {
                    clean_cell(edge.target_port) or f"in_{index + 1}": values.get(edge.from_node, _NodeValue()).value
                    for index, edge in enumerate(sorted(incoming.get(node_id, []), key=lambda item: (item.order, item.id)))
                }
                values[node_id] = _NodeValue(value=self._evaluate_node(node.node_type, node.config, row, parent_values, parent_map))
            for build_row_id in output_sheet_ids:
                built = values.get(build_row_id, _NodeValue()).value
                if isinstance(built, dict):
                    output_rows.append({clean_cell(key): clean_cell(value) for key, value in built.items()})
        return output_rows

    def _source_rows(
        self,
        profile: T1T5RuleProfile,
        *,
        workbook_path: Path | None = None,
        workbook_sheets: dict[str, list[dict[str, str]]] | None = None,
        input_rows: list[dict[str, str]] | None = None,
        profile_match: T1T5ProfileMatch | None = None,
    ) -> list[dict[str, str]]:
        if profile.input_mode == "builtin_context":
            return [{clean_cell(key): clean_cell(value) for key, value in row.items()} for row in (input_rows or [])]
        if not workbook_sheets:
            return []
        matched_sheet = clean_cell(profile_match.matched_sheet_name if profile_match is not None else "")
        sheet_name = matched_sheet or clean_cell(profile.workbook_signature.sheet_name)
        if not sheet_name or sheet_name not in workbook_sheets:
            sheet_name = next(iter(workbook_sheets.keys()), "")
        base_rows = workbook_sheets.get(sheet_name, [])
        source_rows: list[dict[str, str]] = []
        for index, row in enumerate(base_rows, start=2):
            enriched = {clean_cell(key): clean_cell(value) for key, value in row.items()}
            enriched["__sheet_name"] = sheet_name
            enriched["__row_number"] = str(index)
            if workbook_path is not None:
                enriched["__workbook_path"] = str(workbook_path)
            source_rows.append(enriched)
        return source_rows

    def _evaluate_node(
        self,
        node_type: str,
        config: dict[str, Any],
        row: dict[str, str],
        parent_values: list[_NodeValue],
        parent_map: dict[str, Any],
    ) -> Any:
        if node_type in {"BuiltinContext", "WorkbookSheet", "HeaderMatch", "RowIterator", "StrictMatch", "ResolverMatch", "MissingPlaceholder", "CompletionMerge", "RelationBuild"}:
            return clean_cell(config.get("value", ""))
        if node_type == "CellValue":
            return clean_cell(row.get(clean_cell(config.get("field", "")), ""))
        if node_type == "Constant":
            return clean_cell(config.get("value", ""))
        if node_type == "NormalizeIdentifier":
            return normalize_identifier(clean_cell(parent_values[0].value if parent_values else ""))
        if node_type == "RegexExtract":
            pattern = clean_cell(config.get("pattern", ""))
            if not pattern:
                return ""
            flags = re.IGNORECASE if bool(config.get("ignore_case", True)) else 0
            match = re.search(pattern, clean_cell(parent_values[0].value if parent_values else ""), flags)
            if not match:
                return clean_cell(config.get("default", ""))
            group = int(config.get("group", 1) or 1)
            return clean_cell(match.group(group))
        if node_type == "Concat":
            separator = clean_cell(config.get("separator", " | ")) or " | "
            return separator.join(clean_cell(value.value) for value in parent_values if clean_cell(value.value))
        if node_type == "Condition":
            subject = clean_cell(parent_values[0].value if parent_values else "")
            compare_to = clean_cell(parent_values[1].value if len(parent_values) > 1 else config.get("compare_to", ""))
            operator = clean_cell(config.get("operator", "equals")) or "equals"
            matched = False
            if operator == "equals":
                matched = subject == compare_to
            elif operator == "not_equals":
                matched = subject != compare_to
            elif operator == "contains":
                matched = bool(compare_to) and compare_to in subject
            elif operator == "regex":
                matched = bool(compare_to and re.search(compare_to, subject))
            true_value = clean_cell(config.get("true_value", ""))
            false_value = clean_cell(config.get("false_value", ""))
            return true_value if matched else false_value
        if node_type == "LookupMap":
            mapping = config.get("mapping", {})
            current = clean_cell(parent_values[0].value if parent_values else "")
            if isinstance(mapping, dict):
                if current in mapping:
                    return clean_cell(mapping[current])
                lowered = current.lower()
                for key, value in mapping.items():
                    if clean_cell(key).lower() == lowered:
                        return clean_cell(value)
            return clean_cell(config.get("default", current))
        if node_type == "BuildRow":
            field_names = [clean_cell(item) for item in config.get("field_names", []) if clean_cell(item)]
            built: dict[str, str] = {}
            for field_name in field_names:
                built[field_name] = clean_cell(parent_map.get(field_name, ""))
            return built
        if node_type == "OutputSheet":
            for value in parent_values:
                if isinstance(value.value, dict):
                    return value.value
            return {}
        return ""

    def _incoming_edges(self, edges: list[T1T5Edge]) -> dict[str, list[T1T5Edge]]:
        incoming: dict[str, list[T1T5Edge]] = defaultdict(list)
        for edge in edges:
            incoming[edge.to_node].append(edge)
        return incoming

    def _topological_order(self, profile: T1T5RuleProfile) -> list[str]:
        outgoing: dict[str, list[str]] = defaultdict(list)
        indegree: dict[str, int] = {node.id: 0 for node in profile.nodes}
        for edge in profile.edges:
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

    def _first_cycle(self, profile: T1T5RuleProfile) -> str:
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in profile.edges:
            outgoing[edge.from_node].append(edge.to_node)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> str:
            if node_id in visited:
                return ""
            if node_id in visiting:
                return node_id
            visiting.add(node_id)
            for target in outgoing.get(node_id, []):
                cycle = visit(target)
                if cycle:
                    return cycle
            visiting.remove(node_id)
            visited.add(node_id)
            return ""

        for node in profile.nodes:
            cycle = visit(node.id)
            if cycle:
                return cycle
        return ""


def _duplicates(values: list[str]) -> list[str]:
    counts = defaultdict(int)
    for value in values:
        counts[value] += 1
    return sorted(value for value, count in counts.items() if value and count > 1)
