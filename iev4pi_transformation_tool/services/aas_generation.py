from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

import pandas as pd

from iev4pi_transformation_tool.core.utils import clean_cell, ensure_dir, normalize_identifier
from iev4pi_transformation_tool.models import AASGenerationRequest, AASGenerationResult
from iev4pi_transformation_tool.tx.defaults import build_default_uc1_rule_set
from iev4pi_transformation_tool.tx.engine import TxExecutor

try:  # pragma: no cover - optional runtime dependency
    from aas_editor.package import Package as AASManagerPackage
except Exception:  # pragma: no cover - optional runtime dependency
    AASManagerPackage = None

try:  # pragma: no cover - optional runtime dependency
    from basyx.aas.adapter.json import read_aas_json_file as _basyx_read_json
except Exception:  # pragma: no cover - optional runtime dependency
    _basyx_read_json = None


PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
STANDARDIZED_REQUIRED_SHEETS = {"ri_devices", "completion_candidates"}
STANDARDIZED_TEMPLATE_TYPE = "uc1_standardized_device"
TEMPLATE_DISPLAY_NAMES = {
    "stellenplaene": {
        "en": "Stellenplan Proposal",
        "de": "Stellenplan-Vorschlag",
        "zh": "Stellenplan 补全提案",
    },
    "verschaltung": {
        "en": "Wiring Proposal",
        "de": "Verschaltungslisten-Vorschlag",
        "zh": "接线表补全提案",
    },
    "stromlaufplan": {
        "en": "Circuit Diagram",
        "de": "Stromlaufplan",
        "zh": "电路图",
    },
}


class AASGenerationService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.template_dir = self.workspace_root / "assets" / "aas_templates"

    def generate(self, request: AASGenerationRequest) -> AASGenerationResult:
        if self._is_standardized_request(request):
            return self._generate_standardized_device(request)

        config = self._config_for_request(request)
        row = self._load_source_row(request.excel_path, config["sheet_name"], request.source_row_key, request.excel_template_type)
        context = self._context_from_row(row, config, request)
        output_dir = ensure_dir(request.output_dir)
        identity = context["identity"]
        backend = "builtin"

        if request.target_format == "json":
            template_path = request.aas_template_path or self.template_dir / config["json_template"]
            target_path = output_dir / f"{identity}.{request.target_format}"
            rendered = self._render_template(template_path, context)
            target_path.write_text(rendered, encoding="utf-8")
        elif request.target_format == "xml":
            template_path = request.aas_template_path or self.template_dir / config["xml_template"]
            target_path = output_dir / f"{identity}.{request.target_format}"
            rendered = self._render_template(template_path, context)
            target_path.write_text(rendered, encoding="utf-8")
        elif request.target_format == "aasx":
            backend = "aas_manager_bridge"
            target_path = output_dir / f"{identity}.aasx"
            json_template = request.aas_template_path or self.template_dir / config["json_template"]
            rendered_json = self._render_template(json_template, context)
            temp_json_path = output_dir / f".{identity}.intermediate.json"
            temp_json_path.write_text(rendered_json, encoding="utf-8")
            try:
                self._write_aasx(temp_json_path, target_path)
            finally:
                if temp_json_path.exists():
                    temp_json_path.unlink()
        else:
            raise ValueError(f"Unsupported AAS target format: {request.target_format}")

        return AASGenerationResult(
            generated_path=target_path,
            template_type=request.excel_template_type,
            source_row_key=request.source_row_key,
            target_format=request.target_format,
            backend=backend,
        )

    def _is_standardized_request(self, request: AASGenerationRequest) -> bool:
        if request.excel_template_type == STANDARDIZED_TEMPLATE_TYPE:
            return True
        try:
            with pd.ExcelFile(request.excel_path) as workbook:
                return STANDARDIZED_REQUIRED_SHEETS.issubset(set(workbook.sheet_names))
        except Exception:
            return False

    def _generate_standardized_device(self, request: AASGenerationRequest) -> AASGenerationResult:
        sheets = self._load_standardized_sheets(request.excel_path)
        row = self._find_standardized_device_row(sheets["ri_devices"], request.source_row_key)
        context = self._standardized_context(row, sheets, request)

        # Use Tx rule engine as the single source of truth for AAS structure.
        executor = TxExecutor()
        rule_set = build_default_uc1_rule_set("standardized_device")

        identity_value = context["identity"]
        payload, _traces = executor.execute(
            rule_set,
            [context["properties"]],
            identity_value=identity_value,
            source_type="standardized_device",
        )
        # Use the context's shell/asset IDs to preserve backward-compatible identity.
        payload["assetAdministrationShells"][0]["id"] = context["shell_id"]
        payload["assetAdministrationShells"][0]["assetInformation"]["globalAssetId"] = context["asset_id"]

        output_dir = ensure_dir(request.output_dir)
        identity = context["identity"]
        backend = "builtin"
        if request.target_format == "json":
            target_path = output_dir / f"{identity}.json"
            target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._validate_aas_json(target_path)
        elif request.target_format == "xml":
            target_path = output_dir / f"{identity}.xml"
            target_path.write_text(self._standardized_payload_to_xml(payload), encoding="utf-8")
        elif request.target_format == "aasx":
            backend = "aas_manager_bridge"
            target_path = output_dir / f"{identity}.aasx"
            temp_json_path = output_dir / f".{identity}.intermediate.json"
            temp_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._validate_aas_json(temp_json_path)
            try:
                self._write_aasx(temp_json_path, target_path)
            finally:
                if temp_json_path.exists():
                    temp_json_path.unlink()
        else:
            raise ValueError(f"Unsupported AAS target format: {request.target_format}")

        return AASGenerationResult(
            generated_path=target_path,
            template_type=STANDARDIZED_TEMPLATE_TYPE,
            source_row_key=clean_cell(row.get("canonical_tag", "")) or clean_cell(row.get("device_id", "")),
            target_format=request.target_format,
            backend=backend,
        )

    def generate_batch(
        self,
        workbook_path: Path,
        output_dir: Path,
        target_format: str = "json",
        progress_callback: callable | None = None,
    ) -> list[AASGenerationResult]:
        """Generate an AAS for every device row in a standardized workbook.

        Args:
            workbook_path: Path to the standardized Excel workbook.
            output_dir: Directory to write AAS files into.
            target_format: ``"json"``, ``"xml"``, or ``"aasx"``.
            progress_callback: Optional ``callback(current, total)`` for UI progress.

        Returns:
            List of AASGenerationResult, one per device row.
        """
        sheets = self._load_standardized_sheets(workbook_path)
        device_rows = sheets.get("ri_devices", [])
        if not device_rows:
            return []

        results: list[AASGenerationResult] = []
        total = len(device_rows)
        for idx, row in enumerate(device_rows):
            source_key = (
                clean_cell(row.get("canonical_tag", ""))
                or clean_cell(row.get("device_id", ""))
                or clean_cell(row.get("process_instrumentation_function_number", ""))
                or f"row_{idx}"
            )
            request = AASGenerationRequest(
                excel_path=workbook_path,
                excel_template_type=STANDARDIZED_TEMPLATE_TYPE,
                source_row_key=source_key,
                target_format=target_format,
                output_dir=str(output_dir),
            )
            try:
                result = self.generate(request)
                results.append(result)
            except Exception:
                results.append(
                    AASGenerationResult(
                        generated_path=Path(),
                        template_type=STANDARDIZED_TEMPLATE_TYPE,
                        source_row_key=source_key,
                        target_format=target_format,
                        backend="builtin",
                        errors=[f"Batch generation failed for row key: {source_key}"],
                    )
                )
            if progress_callback is not None:
                progress_callback(idx + 1, total)

        return results

    def _load_standardized_sheets(self, excel_path: Path) -> dict[str, list[dict[str, str]]]:
        required_sheet_names = {
            "ri_devices",
            "stellenplan_entries",
            "wiring_entries",
            "datasheet_entries",
            "ifc_entries",
            "completion_candidates",
            "relations",
        }
        sheets: dict[str, list[dict[str, str]]] = {}
        with pd.ExcelFile(excel_path) as workbook:
            for sheet_name in required_sheet_names:
                if sheet_name not in workbook.sheet_names:
                    sheets[sheet_name] = []
                    continue
                frame = workbook.parse(sheet_name, dtype=object)
                frame = frame.where(pd.notna(frame), "")
                sheets[sheet_name] = [
                    {clean_cell(key): clean_cell(value) for key, value in row.items()}
                    for row in frame.astype(str).replace({"nan": ""}).to_dict(orient="records")
                ]
        return sheets

    def _find_standardized_device_row(self, rows: list[dict[str, str]], source_row_key: str) -> dict[str, str]:
        if not rows:
            raise ValueError("No rows found in standardized ri_devices sheet")
        normalized_target = normalize_identifier(source_row_key)
        if normalized_target:
            for row in rows:
                for key in ("device_id", "canonical_tag", "process_instrumentation_function_number"):
                    if normalize_identifier(clean_cell(row.get(key, ""))) == normalized_target:
                        return row
        for row in rows:
            if clean_cell(row.get("device_id", "")) or clean_cell(row.get("canonical_tag", "")):
                return row
        raise ValueError("No non-empty device rows found in standardized workbook")

    def _standardized_context(
        self,
        row: dict[str, str],
        sheets: dict[str, list[dict[str, str]]],
        request: AASGenerationRequest,
    ) -> dict[str, Any]:
        device_id = clean_cell(row.get("device_id", ""))
        canonical_tag = clean_cell(row.get("canonical_tag", "")) or clean_cell(row.get("process_instrumentation_function_number", ""))
        identity_value = canonical_tag or device_id or request.source_row_key or "device"
        identity = normalize_identifier(identity_value) or "device"
        related_rows = {
            sheet_name: [
                item
                for item in rows
                if normalize_identifier(clean_cell(item.get("device_id", ""))) == normalize_identifier(device_id)
            ]
            for sheet_name, rows in sheets.items()
            if sheet_name != "ri_devices"
        }
        completion_row = self._pick_preferred_row(related_rows.get("completion_candidates", []), "proposal_status")
        ifc_row = self._pick_preferred_row(related_rows.get("ifc_entries", []), "global_id")
        stellenplan_row = self._pick_preferred_row(related_rows.get("stellenplan_entries", []), "tag")
        wiring_row = self._pick_preferred_row(related_rows.get("wiring_entries", []), "plt_stelle")
        datasheet_row = self._pick_preferred_row(related_rows.get("datasheet_entries", []), "device_information")

        properties = {
            # SM_CoreIdentity
            "canonical_tag": canonical_tag,
            "device_id": device_id or f"urn:ievpi:device:{identity}",
            "has_instrumentation_loop_function_number": clean_cell(row.get("has_instrumentation_loop_function_number", "")) or canonical_tag,
            "process_instrumentation_function_number": clean_cell(row.get("process_instrumentation_function_number", "")) or canonical_tag,
            "process_instrumentation_function_category": clean_cell(row.get("process_instrumentation_function_category", "")),
            "process_instrumentation_function_modifier": clean_cell(row.get("process_instrumentation_function_modifier", "")),
            "process_instrumentation_functions": clean_cell(row.get("process_instrumentation_functions", "")),
            "context_summary": clean_cell(row.get("context_summary", "")),
            # SM_FunctionAndVendor
            "device_information": clean_cell(row.get("device_information", "")) or clean_cell(row.get("description", "")),
            "vendor_company_name": clean_cell(row.get("vendor_company_name", "")),
            "safety_relevance_class": clean_cell(row.get("safety_relevance_class", "")),
            "label_text": clean_cell(row.get("label_text", "")),
            "function_code": clean_cell(row.get("function_code", "")),
            "ri_source_doc_id": clean_cell(row.get("source_doc_id", "")),
            "ri_source_locator": clean_cell(row.get("source_locator", "")),
            # SM_ActuationAndPiping
            "actuating_function_number": clean_cell(row.get("actuating_function_number", "")),
            "actuating_location": clean_cell(row.get("actuating_location", "")),
            "actuating_system_number": clean_cell(row.get("actuating_system_number", "")),
            "operated_valve_reference": clean_cell(row.get("operated_valve_reference", "")),
            "flow_direction": clean_cell(row.get("flow_direction", "")),
            "nominal_diameter_numerical_value_representation": clean_cell(row.get("nominal_diameter_numerical_value_representation", "")),
            "nominal_diameter_representation": clean_cell(row.get("nominal_diameter_representation", "")),
            "nominal_diameter_standard": clean_cell(row.get("nominal_diameter_standard", "")),
            "nominal_diameter_type_representation": clean_cell(row.get("nominal_diameter_type_representation", "")),
            "line_number": clean_cell(row.get("line_number", "")),
            "piping_component_name": clean_cell(row.get("piping_component_name", "")),
            "from_equipment_id": clean_cell(row.get("from_equipment_id", "")),
            "to_equipment_id": clean_cell(row.get("to_equipment_id", "")),
            "piping_anchor_id": clean_cell(row.get("piping_anchor_id", "")),
            "recommended_action": clean_cell(row.get("recommended_action", "") or completion_row.get("recommended_action", "")),
            # SM_IFCConnectivity
            "ifc_class": clean_cell(ifc_row.get("ifc_class", "")),
            "global_id": clean_cell(ifc_row.get("global_id", "")),
            "ifc_tag": clean_cell(ifc_row.get("tag", "")),
            "has_ports": clean_cell(ifc_row.get("has_ports", "")),
            "connected_to": clean_cell(ifc_row.get("connected_to", "")),
            "connected_from": clean_cell(ifc_row.get("connected_from", "")),
            "has_control_elements": clean_cell(ifc_row.get("has_control_elements", "")),
            "predefined_type": clean_cell(ifc_row.get("predefined_type", "")),
            "size": clean_cell(ifc_row.get("size", "")),
            "valve_mechanism": clean_cell(ifc_row.get("valve_mechanism", "")),
            "flow_coefficient": clean_cell(ifc_row.get("flow_coefficient", "")),
            "fail_position": clean_cell(ifc_row.get("fail_position", "")),
            "manual_override": clean_cell(ifc_row.get("manual_override", "")),
            "actuator_application": clean_cell(ifc_row.get("actuator_application", "")),
            "ifc_source_doc_id": clean_cell(ifc_row.get("source_doc_id", "")),
            "ifc_source_locator": clean_cell(ifc_row.get("source_locator", "")),
            # SM_CompletionProposal
            "present_in_ri": self._string_bool(clean_cell(completion_row.get("present_in_ri", "")) or "true"),
            "present_in_stellenplan": self._string_bool(clean_cell(completion_row.get("present_in_stellenplan", ""))),
            "present_in_wiring": self._string_bool(clean_cell(completion_row.get("present_in_wiring", ""))),
            "present_in_datasheet": self._string_bool(clean_cell(completion_row.get("present_in_datasheet", ""))),
            "present_in_ifc": self._string_bool(clean_cell(completion_row.get("present_in_ifc", ""))),
            "flange_complete": self._string_bool(clean_cell(completion_row.get("flange_complete", ""))),
            "uc1_candidate": self._string_bool(clean_cell(completion_row.get("uc1_candidate", ""))),
            "missing_targets": clean_cell(completion_row.get("missing_targets", "")),
            "proposal_status": clean_cell(completion_row.get("proposal_status", "")),
            "stellenplan_source_doc_id": clean_cell(stellenplan_row.get("source_doc_id", "")),
            "stellenplan_source_locator": clean_cell(stellenplan_row.get("source_locator", "")),
            "wiring_source_doc_id": clean_cell(wiring_row.get("source_doc_id", "")),
            "wiring_source_locator": clean_cell(wiring_row.get("source_locator", "")),
            "datasheet_source_doc_id": clean_cell(datasheet_row.get("source_doc_id", "")),
            "datasheet_source_locator": clean_cell(datasheet_row.get("source_locator", "")),
            # SM_Traceability
            "decision_confidence": clean_cell(row.get("decision_confidence", "") or completion_row.get("decision_confidence", "")),
            "evidence_bundle_id": clean_cell(row.get("evidence_bundle_id", "") or completion_row.get("evidence_bundle_id", "")),
            "uncertainty_reason": clean_cell(row.get("uncertainty_reason", "") or completion_row.get("uncertainty_reason", "")),
            "llm_verification_status": clean_cell(row.get("llm_verification_status", "") or completion_row.get("llm_verification_status", "")),
            "rule_support": clean_cell(row.get("rule_support", "") or completion_row.get("rule_support", "")),
            "review_feedback_status": clean_cell(row.get("review_feedback_status", "") or completion_row.get("review_feedback_status", "")),
            "decision_trace_json": clean_cell(row.get("decision_trace_json", "") or completion_row.get("decision_trace_json", "")),
        }
        return {
            "identity": identity,
            "identity_raw": identity_value,
            "shell_id": clean_cell(row.get("shell_id", "")) or f"urn:ievpi:shell:{identity}",
            "asset_id": clean_cell(row.get("asset_id", "")) or f"urn:ievpi:asset:{identity}",
            "id_short": identity_value.replace(".", "_").replace("-", "_"),
            "properties": properties,
        }

    def _validate_aas_json(self, json_path: Path) -> None:
        """Validate an AAS JSON file using basyx-python-sdk.

        Raises ValueError if the file is not valid AAS JSON.
        No-op if basyx is unavailable.
        """
        if _basyx_read_json is None:
            return
        try:
            _basyx_read_json(str(json_path), failsafe=False)
        except Exception as exc:
            raise ValueError(f"AAS validation failed for {json_path.name}: {exc}") from exc

    def _standardized_payload_to_xml(self, payload: dict[str, Any]) -> str:
        root = ET.Element("aasEnvironment")
        for shell in payload.get("assetAdministrationShells", []):
            shell_element = ET.SubElement(
                root,
                "assetAdministrationShell",
                {"id": clean_cell(shell.get("id", "")), "idShort": clean_cell(shell.get("idShort", ""))},
            )
            asset_information = shell.get("assetInformation", {})
            ET.SubElement(shell_element, "assetKind").text = clean_cell(asset_information.get("assetKind", ""))
            ET.SubElement(shell_element, "globalAssetId").text = clean_cell(asset_information.get("globalAssetId", ""))
            submodels_element = ET.SubElement(shell_element, "submodels")
            for reference in shell.get("submodels", []):
                keys = reference.get("keys", [])
                if not keys:
                    continue
                ET.SubElement(submodels_element, "submodelRef", {"id": clean_cell(keys[0].get("value", ""))})

        for submodel in payload.get("submodels", []):
            submodel_element = ET.SubElement(
                root,
                "submodel",
                {"id": clean_cell(submodel.get("id", "")), "idShort": clean_cell(submodel.get("idShort", ""))},
            )
            for item in submodel.get("submodelElements", []):
                property_element = ET.SubElement(
                    submodel_element,
                    "property",
                    {
                        "idShort": clean_cell(item.get("idShort", "")),
                        "valueType": clean_cell(item.get("valueType", "")),
                    },
                )
                property_element.text = clean_cell(item.get("value", ""))

        return ET.tostring(root, encoding="unicode")

    def _pick_preferred_row(self, rows: list[dict[str, str]], key: str) -> dict[str, str]:
        if not rows:
            return {}
        for row in rows:
            if clean_cell(row.get("presence_status", "")) == "present" and clean_cell(row.get(key, "")):
                return row
        for row in rows:
            if clean_cell(row.get(key, "")):
                return row
        return rows[0]

    def _string_bool(self, value: str) -> str:
        normalized = clean_cell(value).lower()
        if normalized in {"true", "1", "yes", "present", "complete", "matched"}:
            return "true"
        if normalized in {"false", "0", "no", "missing", "unknown", "deferred", "partial"}:
            return "false"
        return ""

    def _config_for_request(self, request: AASGenerationRequest) -> dict[str, str]:
        default_map = {
            "stellenplaene": {
                "sheet_name": "stellenplaene_proposal",
                "identity_field": "tag",
                "json_template": "stellenplaene_aas_template.json",
                "xml_template": "stellenplaene_aas_template.xml",
            },
            "verschaltung": {
                "sheet_name": "verschaltung_proposal",
                "identity_field": "plt_stelle",
                "json_template": "verschaltung_aas_template.json",
                "xml_template": "verschaltung_aas_template.xml",
            },
            "assembly_3d": {
                "sheet_name": "Assembly_Steps",
                "identity_field": "label",
                "json_template": "assembly_3d_aas_template.json",
                "xml_template": "",
            },
        }
        config = dict(default_map.get(request.excel_template_type, default_map["stellenplaene"]))
        if request.mapping_config_path and request.mapping_config_path.exists():
            loaded = json.loads(request.mapping_config_path.read_text(encoding="utf-8"))
            for key, value in loaded.items():
                if isinstance(value, str) and value.strip():
                    config[key] = value.strip()
        return config

    def _load_source_row(self, excel_path: Path, sheet_name: str, source_row_key: str, template_type: str = "") -> dict[str, str]:
        frame = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=object)
        frame = frame.where(pd.notna(frame), "")
        records = frame.astype(str).replace({"nan": ""}).to_dict(orient="records")
        if not records:
            raise ValueError(f"No rows found in sheet {sheet_name}")
        if source_row_key:
            normalized_target = normalize_identifier(source_row_key)
            for row in records:
                for key in ("source_row_key", "record_key", "canonical_tag", "tag", "plt_stelle", "label", "ifc_global_id"):
                    if normalize_identifier(clean_cell(row.get(key, ""))) == normalized_target:
                        return {clean_cell(key): clean_cell(value) for key, value in row.items()}
        # assembly_3d: no silent fallback — source_row_key must match exactly
        if template_type == "assembly_3d":
            raise ValueError(
                f"Assembly AAS: source_row_key '{source_row_key}' not found "
                f"in sheet '{sheet_name}' of {excel_path.name}."
            )
        for row in records:
            if any(clean_cell(value) for value in row.values()):
                return {clean_cell(key): clean_cell(value) for key, value in row.items()}
        raise ValueError(f"No non-empty rows found in sheet {sheet_name}")

    def _context_from_row(
        self,
        row: dict[str, str],
        config: dict[str, str],
        request: AASGenerationRequest,
    ) -> dict[str, str]:
        normalized_row = {
            self._normalize_placeholder_key(key): clean_cell(value)
            for key, value in row.items()
            if clean_cell(key)
        }
        workbook_language = self._workbook_language(request.excel_path)
        identity_field = self._normalize_placeholder_key(config["identity_field"])
        identity_value = normalized_row.get(identity_field, "") or normalized_row.get("canonical_tag", "")
        if not identity_value:
            identity_value = request.source_row_key or request.excel_template_type
        identity = normalize_identifier(identity_value) or request.excel_template_type
        display_names = TEMPLATE_DISPLAY_NAMES.get(request.excel_template_type, TEMPLATE_DISPLAY_NAMES["stellenplaene"])
        context = {
            **normalized_row,
            "identity": identity,
            "identity_raw": identity_value,
            "template_type": request.excel_template_type,
            "source_row_key": request.source_row_key or identity_value,
            "asset_id": f"urn:ievpi:asset:{identity}",
            "shell_id": f"urn:ievpi:shell:{identity}",
            "submodel_id": f"urn:ievpi:submodel:{request.excel_template_type}:{identity}",
            "id_short": identity_value.replace(".", "_").replace("-", "_"),
            "ui_language": workbook_language,
            "template_display_name": display_names.get(workbook_language, display_names["en"]),
            "template_display_name_en": display_names["en"],
            "template_display_name_de": display_names["de"],
            "template_display_name_zh": display_names["zh"],
        }
        return context

    def _render_template(self, template_path: Path, context: dict[str, str]) -> str:
        if not template_path.exists():
            raise FileNotFoundError(f"Missing AAS template: {template_path}")
        template = template_path.read_text(encoding="utf-8")
        if template_path.suffix.lower() == ".json":
            return PLACEHOLDER_PATTERN.sub(
                lambda match: json.dumps(context.get(match.group(1), ""))[1:-1],
                template,
            )
        if template_path.suffix.lower() == ".xml":
            return PLACEHOLDER_PATTERN.sub(
                lambda match: xml_escape(context.get(match.group(1), "")),
                template,
            )
        return PLACEHOLDER_PATTERN.sub(lambda match: context.get(match.group(1), ""), template)

    def _write_aasx(self, json_path: Path, target_path: Path) -> None:
        if AASManagerPackage is None:
            raise RuntimeError(
                "AASX generation requires aas_manager/basyx runtime support. "
                "Install aas_manager or keep using JSON/XML outputs."
            )
        package = AASManagerPackage(json_path.as_posix(), failsafe=True)
        package.write(target_path.as_posix())

    def _normalize_placeholder_key(self, value: str) -> str:
        value = clean_cell(value)
        value = re.sub(r"[^a-zA-Z0-9]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_")
        return value.lower()

    def _workbook_language(self, excel_path: Path) -> str:
        try:
            meta = pd.read_excel(excel_path, sheet_name="meta", dtype=object).fillna("")
        except Exception:
            return "en"
        for row in meta.astype(str).replace({"nan": ""}).to_dict(orient="records"):
            key = self._normalize_placeholder_key(clean_cell(row.get("key", "")))
            if key != "ui_language":
                continue
            language = clean_cell(row.get("value", "")).lower()
            if language in {"en", "de", "zh"}:
                return language
        return "en"
