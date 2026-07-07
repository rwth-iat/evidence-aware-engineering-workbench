from __future__ import annotations

import json
import logging
from collections import OrderedDict
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

from iev4pi_transformation_tool.core.standardized_export import export_standardized_workbook
from iev4pi_transformation_tool.core.standardized_templates import (
    FAMILY_TO_STANDARDIZED_TEMPLATE,
)
from iev4pi_transformation_tool.core.utils import clean_cell, ensure_dir
from iev4pi_transformation_tool.models import DocumentFamily, ExtractedRecord, SchemaFamily, SchemaField


ROW_TEMPLATE_FAMILIES = {
    DocumentFamily.STELLEN_OVERVIEW_RECORD.value,
    DocumentFamily.KLEMMENPLAN_ROW.value,
    DocumentFamily.VERSCHALTUNGSLISTE_ROW.value,
    DocumentFamily.CABINET_REFERENCE_ROW.value,
    DocumentFamily.STROMLAUF_COMPONENT_GROUP.value,
    DocumentFamily.STROMLAUF_COMPONENT.value,
    DocumentFamily.STROMLAUF_CONNECTION.value,
    DocumentFamily.RI_EQUIPMENT_ROW.value,
    DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW.value,
    DocumentFamily.RI_PIPING_COMPONENT_ROW.value,
    DocumentFamily.RI_CONNECTION_ROW.value,
    DocumentFamily.IFC_PIPING_ITEM_ROW.value,
    DocumentFamily.IFC_CONNECTION_ROW.value,
    DocumentFamily.IFC_3D_ASSEMBLY_STEP.value,
    DocumentFamily.IFC_3D_ASSEMBLY_CONNECTION.value,
    DocumentFamily.IFC_3D_POSITION.value,
    DocumentFamily.IFC_3D_PART_LIBRARY.value,
}


USE_CASE_1_FIELD_LABELS = {
    "en": {
        "canonical_tag": "Canonical Tag",
        "display_name": "Component",
        "context_summary": "Context",
        "stellenplaene_status": "Stellenplaene Status",
        "verschaltungslisten_status": "Verschaltungslisten Status",
        "ifc_match_status": "IFC Match Status",
        "flange_status": "Flange Status",
        "recommended_action": "Recommended Action",
        "source_row_key": "Source Row Key",
        "scope_id": "Scope ID",
        "tag": "Tag",
        "function_code": "Function Code",
        "plt_stelle": "PLT Stelle",
        "funktion": "Function",
        "beschreibung": "Description",
        "ifc_match_key": "IFC Match Key",
    },
    "de": {
        "canonical_tag": "Kanonisches Tag",
        "display_name": "Komponente",
        "context_summary": "Kontext",
        "stellenplaene_status": "Stellenplaene-Status",
        "verschaltungslisten_status": "Verschaltungslisten-Status",
        "ifc_match_status": "IFC-Match-Status",
        "flange_status": "Flansch-Status",
        "recommended_action": "Empfohlene Aktion",
        "source_row_key": "Quellzeilenschluessel",
        "scope_id": "Scope-ID",
        "tag": "Tag",
        "function_code": "Funktionscode",
        "plt_stelle": "PLT-Stelle",
        "funktion": "Funktion",
        "beschreibung": "Beschreibung",
        "ifc_match_key": "IFC-Match-Schluessel",
    },
    "zh": {
        "canonical_tag": "规范标签",
        "display_name": "组件",
        "context_summary": "上下文",
        "stellenplaene_status": "Stellenplaene 状态",
        "verschaltungslisten_status": "Verschaltungslisten 状态",
        "ifc_match_status": "IFC 匹配状态",
        "flange_status": "法兰状态",
        "recommended_action": "建议动作",
        "source_row_key": "源行键",
        "scope_id": "Scope 标识",
        "tag": "标签",
        "function_code": "功能代码",
        "plt_stelle": "PLT 位号",
        "funktion": "功能",
        "beschreibung": "描述",
        "ifc_match_key": "IFC 匹配键",
    },
}


class ExportService:
    def _excel_dir(self, base_dir: Path) -> Path:
        return ensure_dir(base_dir / "Excel")

    def _csv_dir(self, base_dir: Path) -> Path:
        return ensure_dir(base_dir / "CSV")

    def _record_excel_path(self, result_dir: Path, family: str) -> Path:
        return self._excel_dir(result_dir) / f"{family}.xlsx"

    def _record_csv_paths(self, result_dir: Path, family: str) -> list[Path]:
        csv_dir = self._csv_dir(result_dir)
        paths = [
            csv_dir / f"{family}.records.csv",
            csv_dir / f"{family}.fields.csv",
        ]
        if family in ROW_TEMPLATE_FAMILIES:
            paths.append(csv_dir / f"{family}.template.csv")
        return paths

    def _ri_excel_path(self, base_dir: Path, bundle_name: str) -> Path:
        return self._excel_dir(base_dir) / f"{bundle_name}.xlsx"

    def _ri_record_csv_paths(self, result_dir: Path, bundle_name: str) -> list[Path]:
        csv_dir = self._csv_dir(result_dir)
        paths: list[Path] = []
        for sheet_name in ["equipment", "instrument_functions", "piping_components", "connections"]:
            paths.append(csv_dir / f"{bundle_name}.{sheet_name}.records.csv")
        return paths

    def use_case_1_excel_path(self, base_dir: Path, workbook_name: str = "use_case_1_new_field_devices.xlsx") -> Path:
        return ensure_dir(base_dir) / workbook_name

    def use_case_1_standardized_excel_path(
        self,
        base_dir: Path,
        workbook_name: str = "use_case_1_standardized_transformation.xlsx",
    ) -> Path:
        return ensure_dir(base_dir) / workbook_name

    def export_record_family(self, result_dir: Path, family: str, family_records: list[ExtractedRecord]) -> Path:
        ensure_dir(result_dir)
        rows = []
        field_rows = []
        filled_template_rows = []
        ordered_field_names = []
        for record in family_records:
            for result in record.results:
                if result.field_name not in ordered_field_names:
                    ordered_field_names.append(result.field_name)
        for record in family_records:
            row = {}
            template_row = {}
            has_filled_value = False
            for result in record.results:
                row[result.field_name] = result.value
                template_row[result.field_name] = result.value
                has_filled_value = has_filled_value or bool(result.value.strip())
                first_evidence = result.evidence_refs[0] if result.evidence_refs else None
                field_rows.append(
                    {
                        "record_key": record.record_key,
                        "display_name": record.display_name,
                        "field_name": result.field_name,
                        "value": result.value,
                        "normalized_value": result.normalized_value,
                        "confidence": result.confidence,
                        "status": result.status.value,
                        "notes": result.notes,
                        "page_or_sheet": first_evidence.page_or_sheet if first_evidence else "",
                        "location": first_evidence.cell_range_or_bbox if first_evidence else "",
                        "snippet": first_evidence.snippet if first_evidence else "",
                    }
                )
            if not has_filled_value:
                continue
            row["record_key"] = record.record_key
            row["display_name"] = record.display_name
            row["source_path"] = record.source_path
            row["notes"] = record.notes
            row["warnings"] = " | ".join(record.cross_validation_warnings)
            rows.append(row)
            filled_template_rows.append(template_row)
        workbook_path = self._record_excel_path(result_dir, family)
        csv_paths = self._record_csv_paths(result_dir, family)
        records_csv_path = next(path for path in csv_paths if path.name.endswith(".records.csv"))
        fields_csv_path = next(path for path in csv_paths if path.name.endswith(".fields.csv"))
        template_csv_path = next((path for path in csv_paths if path.name.endswith(".template.csv")), None)
        with pd.ExcelWriter(workbook_path, engine="xlsxwriter") as writer:
            if family in ROW_TEMPLATE_FAMILIES:
                pd.DataFrame(filled_template_rows, columns=ordered_field_names).to_excel(writer, sheet_name="filled_template", index=False)
            pd.DataFrame(rows).to_excel(writer, sheet_name="records", index=False)
            pd.DataFrame(field_rows).to_excel(writer, sheet_name="field_results", index=False)
        pd.DataFrame(rows).to_csv(records_csv_path, index=False, encoding="utf-8-sig")
        pd.DataFrame(field_rows).to_csv(fields_csv_path, index=False, encoding="utf-8-sig")
        if family in ROW_TEMPLATE_FAMILIES and template_csv_path is not None:
            pd.DataFrame(filled_template_rows, columns=ordered_field_names).to_csv(
                template_csv_path,
                index=False,
                encoding="utf-8-sig",
            )
        try:
            export_standardized_workbook(result_dir, family, family_records)
        except Exception:
            # Standardized export is additive; never break the legacy export path.
            logger.warning(
                "Standardized export skipped for family=%s (%d records): %s",
                family, len(family_records),
                "template missing or incompatible — see traceback below",
                exc_info=True,
            )
        return workbook_path

    def record_output_paths(self, result_dir: Path, family: str) -> list[Path]:
        paths = [self._record_excel_path(result_dir, family), *self._record_csv_paths(result_dir, family)]
        if family in FAMILY_TO_STANDARDIZED_TEMPLATE:
            paths.append(self._excel_dir(result_dir) / f"{family}.standardized.xlsx")
        return paths

    def ri_record_output_paths(self, result_dir: Path, bundle_name: str) -> list[Path]:
        return [self._ri_excel_path(result_dir, bundle_name), *self._ri_record_csv_paths(result_dir, bundle_name)]

    def _split_aliases(self, value: object) -> list[str]:
        aliases: list[str] = []
        for part in str(value or "").split("|"):
            alias = clean_cell(part)
            if alias and alias not in aliases:
                aliases.append(alias)
        return aliases

    def _coerce_bool(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        text = clean_cell(value)
        return text.lower() in {"1", "true", "yes", "y"}

    def _default_display_name(self, family: DocumentFamily) -> str:
        return family.value.replace("_", " ").title()

    def export_ri_record_workbook(
        self,
        result_dir: Path,
        bundle_name: str,
        bundle_records: dict[DocumentFamily, list[ExtractedRecord]],
    ) -> Path:
        ensure_dir(result_dir)
        path = self._ri_excel_path(result_dir, bundle_name)
        csv_paths = self._ri_record_csv_paths(result_dir, bundle_name)
        csv_map = {path_item.name: path_item for path_item in csv_paths}
        first_record = next((records[0] for records in bundle_records.values() if records), None)
        meta_rows = [
            {
                "bundle_id": first_record.scope_id if first_record is not None else "",
                "display_name": bundle_name,
                "source_root": first_record.source_root if first_record is not None else "R&I-Fließbild",
            }
        ]
        with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
            pd.DataFrame(meta_rows).to_excel(writer, sheet_name="bundle_meta", index=False)
            for sheet_name, family, records in self._ordered_ri_record_groups(bundle_records):
                record_rows = self._record_rows(records)
                field_rows = self._field_rows(records)
                field_names = [field_name for field_name in self._ordered_field_names(records)]
                main_rows = [{field_name: row.get(field_name, "") for field_name in field_names} for row in record_rows]
                pd.DataFrame(main_rows, columns=field_names).to_excel(writer, sheet_name=sheet_name, index=False)
                pd.DataFrame(field_rows).to_excel(
                    writer,
                    sheet_name=self._ri_field_results_sheet_name(sheet_name),
                    index=False,
                )
                pd.DataFrame(main_rows, columns=field_names).to_csv(
                    csv_map[f"{bundle_name}.{sheet_name}.records.csv"],
                    index=False,
                    encoding="utf-8-sig",
                )
        return path

    def _ri_field_results_sheet_name(self, sheet_name: str) -> str:
        candidate = f"{sheet_name}_fields"
        if len(candidate) <= 31:
            return candidate
        return f"{sheet_name[:24]}_fields"

    def _ordered_ri_record_groups(
        self,
        bundle_records: dict[DocumentFamily, list[ExtractedRecord]],
    ) -> list[tuple[str, DocumentFamily, list[ExtractedRecord]]]:
        ordered: list[tuple[str, DocumentFamily, list[ExtractedRecord]]] = []
        for family, sheet_name in [
            (DocumentFamily.RI_EQUIPMENT_ROW, "equipment"),
            (DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW, "instrument_functions"),
            (DocumentFamily.RI_PIPING_COMPONENT_ROW, "piping_components"),
            (DocumentFamily.RI_CONNECTION_ROW, "connections"),
        ]:
            records = bundle_records.get(family, [])
            if records:
                ordered.append((sheet_name, family, records))
        return ordered

    def _ordered_field_names(self, family_records: list[ExtractedRecord]) -> list[str]:
        ordered_field_names: list[str] = []
        for record in family_records:
            for result in record.results:
                if result.field_name not in ordered_field_names:
                    ordered_field_names.append(result.field_name)
        return ordered_field_names

    def _record_rows(self, family_records: list[ExtractedRecord]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for record in family_records:
            row = {}
            has_filled_value = False
            for result in record.results:
                row[result.field_name] = result.value
                has_filled_value = has_filled_value or bool(result.value.strip())
            if not has_filled_value:
                continue
            row["record_key"] = record.record_key
            row["display_name"] = record.display_name
            row["source_path"] = record.source_path
            rows.append(row)
        return rows

    def _field_rows(self, family_records: list[ExtractedRecord]) -> list[dict[str, object]]:
        field_rows: list[dict[str, object]] = []
        for record in family_records:
            for result in record.results:
                first_evidence = result.evidence_refs[0] if result.evidence_refs else None
                field_rows.append(
                    {
                        "record_key": record.record_key,
                        "display_name": record.display_name,
                        "field_name": result.field_name,
                        "value": result.value,
                        "normalized_value": result.normalized_value,
                        "confidence": result.confidence,
                        "status": result.status.value,
                        "notes": result.notes,
                        "page_or_sheet": first_evidence.page_or_sheet if first_evidence else "",
                        "location": first_evidence.cell_range_or_bbox if first_evidence else "",
                        "snippet": first_evidence.snippet if first_evidence else "",
                    }
                )
        return field_rows

    def export_use_case_1_workbook(
        self,
        base_dir: Path,
        *,
        overview_rows: list[dict[str, object]],
        stellenplaene_rows: list[dict[str, object]],
        verschaltung_rows: list[dict[str, object]],
        ifc_rows: list[dict[str, object]],
        language: str = "en",
        workbook_name: str = "use_case_1_new_field_devices.xlsx",
    ) -> Path:
        path = self.use_case_1_excel_path(base_dir, workbook_name)
        sheets = {
            "overview": overview_rows,
            "stellenplaene_proposal": stellenplaene_rows,
            "verschaltung_proposal": verschaltung_rows,
            "ifc_flange_gap": ifc_rows,
        }
        with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
            meta_frame = pd.DataFrame(
                [
                    {"key": "ui_language", "value": language},
                    {"key": "sheet_names_fixed", "value": "true"},
                    {"key": "machine_columns_fixed", "value": "true"},
                ]
            )
            meta_frame.to_excel(writer, sheet_name="meta", index=False)
            self._freeze_header(writer, "meta", meta_frame)
            for sheet_name, rows in sheets.items():
                frame = pd.DataFrame(rows or [{}])
                frame.to_excel(writer, sheet_name=sheet_name, index=False)
                self._freeze_header(writer, sheet_name, frame)
            i18n_frame = pd.DataFrame(self._use_case_1_i18n_rows(language))
            i18n_frame.to_excel(writer, sheet_name="i18n_legend", index=False)
            self._freeze_header(writer, "i18n_legend", i18n_frame)
        return path

    def export_use_case_1_standardized_workbook(
        self,
        base_dir: Path,
        *,
        documents_rows: list[dict[str, object]],
        ri_device_rows: list[dict[str, object]],
        stellenplan_rows: list[dict[str, object]],
        wiring_rows: list[dict[str, object]],
        datasheet_rows: list[dict[str, object]],
        ifc_rows: list[dict[str, object]],
        relation_rows: list[dict[str, object]],
        completion_rows: list[dict[str, object]],
        coverage_rows: list[dict[str, object]] | None = None,
        language: str = "en",
        catalog_path: str = "Datenpunkte_V1.xlsx",
        workbook_name: str = "use_case_1_standardized_transformation.xlsx",
    ) -> Path:
        path = self.use_case_1_standardized_excel_path(base_dir, workbook_name)
        sheets = {
            "documents": documents_rows,
            "ri_devices": ri_device_rows,
            "stellenplan_entries": stellenplan_rows,
            "wiring_entries": wiring_rows,
            "datasheet_entries": datasheet_rows,
            "ifc_entries": ifc_rows,
            "relations": relation_rows,
            "completion_candidates": completion_rows,
            "catalog_coverage": coverage_rows or [],
        }
        with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
            meta_frame = pd.DataFrame(
                [
                    {"key": "ui_language", "value": language},
                    {"key": "workbook_kind", "value": "uc1_standardized"},
                    {"key": "catalog_path", "value": catalog_path},
                    {"key": "sheet_names_fixed", "value": "true"},
                    {"key": "machine_columns_fixed", "value": "true"},
                    {"key": "device_centric_aas", "value": "true"},
                ]
            )
            meta_frame.to_excel(writer, sheet_name="meta", index=False)
            self._freeze_header(writer, "meta", meta_frame)
            for sheet_name, rows in sheets.items():
                frame = pd.DataFrame(rows or [{}])
                frame.to_excel(writer, sheet_name=sheet_name, index=False)
                self._freeze_header(writer, sheet_name, frame)
        return path

    def export_uc1_source_standardized_workbook(
        self,
        base_dir: Path,
        *,
        workbook_name: str,
        workbook_kind: str,
        primary_sheet_name: str,
        primary_rows: list[dict[str, object]],
        documents_rows: list[dict[str, object]] | None = None,
        relation_rows: list[dict[str, object]] | None = None,
        coverage_rows: list[dict[str, object]] | None = None,
        language: str = "en",
        catalog_path: str = "Datenpunkte_V1.xlsx",
    ) -> Path:
        path = self.use_case_1_standardized_excel_path(base_dir, workbook_name)
        sheets: OrderedDict[str, list[dict[str, object]]] = OrderedDict()
        sheets["documents"] = documents_rows or []
        sheets[primary_sheet_name] = primary_rows or []
        if relation_rows is not None:
            sheets["relations"] = relation_rows
        sheets["catalog_coverage"] = coverage_rows or []

        with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
            meta_frame = pd.DataFrame(
                [
                    {"key": "ui_language", "value": language},
                    {"key": "workbook_kind", "value": workbook_kind},
                    {"key": "catalog_path", "value": catalog_path},
                    {"key": "sheet_names_fixed", "value": "true"},
                    {"key": "machine_columns_fixed", "value": "true"},
                    {"key": "primary_sheet_name", "value": primary_sheet_name},
                ]
            )
            meta_frame.to_excel(writer, sheet_name="meta", index=False)
            self._freeze_header(writer, "meta", meta_frame)
            for sheet_name, rows in sheets.items():
                frame = pd.DataFrame(rows or [{}])
                frame.to_excel(writer, sheet_name=sheet_name, index=False)
                self._freeze_header(writer, sheet_name, frame)
        return path

    def _use_case_1_i18n_rows(self, language: str) -> list[dict[str, str]]:
        active_language = (language or "en").strip().lower()
        if active_language not in {"en", "de", "zh"}:
            active_language = "en"
        sheets = {
            "overview": [
                "canonical_tag",
                "display_name",
                "context_summary",
                "stellenplaene_status",
                "verschaltungslisten_status",
                "ifc_match_status",
                "flange_status",
                "recommended_action",
                "source_row_key",
                "scope_id",
            ],
            "stellenplaene_proposal": [
                "source_row_key",
                "canonical_tag",
                "tag",
                "function_code",
                "context_summary",
                "recommended_action",
            ],
            "verschaltung_proposal": [
                "source_row_key",
                "canonical_tag",
                "plt_stelle",
                "funktion",
                "beschreibung",
                "context_summary",
                "recommended_action",
            ],
            "ifc_flange_gap": [
                "source_row_key",
                "canonical_tag",
                "ifc_match_status",
                "ifc_match_key",
                "flange_status",
                "context_summary",
                "recommended_action",
            ],
        }
        rows: list[dict[str, str]] = []
        for sheet_name, field_names in sheets.items():
            for field_name in field_names:
                rows.append(
                    {
                        "sheet_name": sheet_name,
                        "field_name": field_name,
                        "active_language": active_language,
                        "active_label": USE_CASE_1_FIELD_LABELS.get(active_language, USE_CASE_1_FIELD_LABELS["en"]).get(field_name, field_name),
                        "label_en": USE_CASE_1_FIELD_LABELS["en"].get(field_name, field_name),
                        "label_de": USE_CASE_1_FIELD_LABELS["de"].get(field_name, field_name),
                        "label_zh": USE_CASE_1_FIELD_LABELS["zh"].get(field_name, field_name),
                    }
                )
        return rows

    def export_json_payload(self, path: Path, payload: dict[str, object]) -> Path:
        ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _freeze_header(self, writer: pd.ExcelWriter, sheet_name: str, frame: pd.DataFrame) -> None:
        worksheet = writer.sheets.get(sheet_name)
        if worksheet is None:
            return
        worksheet.freeze_panes(1, 0)
        for index, column_name in enumerate(frame.columns):
            width = max(18, min(48, len(str(column_name)) + 4))
            worksheet.set_column(index, index, width)

    def export_records(self, export_dir: Path, records: list[ExtractedRecord]) -> list[Path]:
        result_dir = ensure_dir(export_dir / "results")
        written: list[Path] = []
        families = sorted({record.family.value for record in records})
        for family in families:
            family_records = [record for record in records if record.family.value == family]
            written.append(self.export_record_family(result_dir, family, family_records))
        return written
