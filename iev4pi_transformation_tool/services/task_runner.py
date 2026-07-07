from __future__ import annotations

import os as _os

from iev4pi_transformation_tool.core.qos_helpers import pcore_worker_count

# -- GPU memory safety: must execute before ANY framework import ----------
# Prevent PyTorch MPS from exhausting unified memory on integrated GPUs
# (MacBook built-in display).  Values are safe to set unconditionally.
for _k, _v in [
    ("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.15"),
    ("PYTORCH_ENABLE_MPS_FALLBACK", "1"),
    ("OMP_NUM_THREADS", str(pcore_worker_count())),
]:
    if _k not in _os.environ:
        _os.environ[_k] = _v
# -------------------------------------------------------------------------

import json
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from iev4pi_transformation_tool.models import DocumentFamily
from iev4pi_transformation_tool.services.workbench import Workbench


ProgressCallback = Callable[[int, str], None]


_STDOUT_EMIT_LOCK = threading.Lock()


def run_workbench_task_process(
    workspace_root: str,
    task_name: str,
    payload: dict[str, Any],
    emit: Callable[[dict[str, Any]], None],
) -> None:
    def progress(value: int, message: str) -> None:
        emit({"kind": "progress", "value": int(value), "message": message})

    def log_entry(entry: dict[str, Any]) -> None:
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        emit(
            {
                "kind": "log",
                "entry": {
                    **entry,
                    "details": {
                        **details,
                        "task_name": str(details.get("task_name") or task_name),
                    },
                },
            }
        )

    try:
        workbench = Workbench(Path(workspace_root), external_log_sink=log_entry)
        if task_name == "scan":
            result = workbench.scan(progress)
            emit({"kind": "result", "payload": result.model_dump(mode="json")})
            return
        if task_name == "generate_schemas":
            use_ocr = payload.get("use_ocr")
            schemas = workbench.generate_schemas(progress, use_ocr=bool(use_ocr) if use_ocr is not None else None)
            emit({"kind": "result", "payload": {"schema_count": len(schemas)}})
            return
        if task_name == "run_extraction":
            if "retrieval_top_k" in payload:
                workbench.settings.retrieval_top_k = int(payload["retrieval_top_k"])
            use_ocr = payload.get("use_ocr")
            summary = workbench.run_extraction(
                progress,
                use_ocr=bool(use_ocr) if use_ocr is not None else None,
            )
            emit({"kind": "result", "payload": summary.model_dump(mode="json")})
            return
        if task_name == "fill_standardized_templates":
            use_ocr = payload.get("use_ocr")
            summary = workbench.fill_standardized_templates(
                progress,
                use_ocr=bool(use_ocr) if use_ocr is not None else None,
            )
            emit({"kind": "result", "payload": summary.model_dump(mode="json")})
            return
        if task_name == "save_extraction_results":
            result = workbench.save_extraction_results(progress)
            emit({"kind": "result", "payload": result})
            return
        if task_name == "export_all":
            paths = workbench.export_all(progress)
            emit({"kind": "result", "payload": [str(path) for path in paths]})
            return
        if task_name == "export_results":
            paths = workbench.export_results(progress)
            emit({"kind": "result", "payload": [str(path) for path in paths]})
            return
        if task_name == "load_review_page":
            family = payload.get("family")
            offset = int(payload.get("offset", 0))
            limit = int(payload.get("limit", 200))
            progress(10, "Counting review rows")
            result = workbench.review_page(family, offset=offset, limit=limit)
            progress(100, f"Loaded review rows {result['offset']}..{result['offset'] + len(result['rows'])}")
            emit({"kind": "result", "payload": result})
            return
        if task_name == "load_review_record_page":
            family = payload.get("family")
            keyword = payload.get("keyword")
            offset = int(payload.get("offset", 0))
            limit = int(payload.get("limit", 0))
            progress(15, "Counting matching review records")
            result = workbench.review_record_page(family, keyword=keyword, offset=offset, limit=limit)
            progress(70, f"Loaded {len(result['rows'])} review records")
            progress(100, "Review search complete")
            emit({"kind": "result", "payload": result})
            return
        if task_name == "load_value_source_preview":
            progress(10, "Preparing source preview")
            result = workbench.load_value_source_preview(
                str(payload.get("source_path", "")),
                payload.get("evidences", []),
                evidence_index=int(payload.get("evidence_index", 0)),
                record_display_name=str(payload.get("record_display_name", "")),
                field_name=str(payload.get("field_name", "")),
                target_value=str(payload.get("target_value", "")),
            )
            progress(100, "Source preview ready")
            emit({"kind": "result", "payload": result})
            return
        if task_name == "load_pid_inconsistency_report":
            progress(10, "Collecting P&ID inconsistency sources")
            result = workbench.pid_inconsistency_report(progress)
            emit(
                {
                    "kind": "result",
                    "payload": {
                        "report": result.model_dump(mode="json"),
                        "snapshot": workbench.current_snapshot().model_dump(mode="json"),
                    },
                }
            )
            return
        if task_name == "export_use_case_1_workbook":
            progress(10, "Building UC1 completion workbook")
            output_dir = payload.get("output_dir")
            path = workbench.export_use_case_1_workbook(
                progress,
                output_dir=Path(output_dir) if output_dir else None,
            )
            emit({"kind": "result", "payload": str(path)})
            return
        if task_name == "export_use_case_1_standardized_workbook":
            progress(10, "Building standardized UC1 transformation workbook")
            output_dir = payload.get("output_dir")
            path = workbench.export_use_case_1_standardized_workbook(
                progress,
                output_dir=Path(output_dir) if output_dir else None,
            )
            emit({"kind": "result", "payload": str(path)})
            return
        if task_name == "export_uc1_catalog_coverage":
            progress(10, "Loading UC1 catalog coverage report")
            report = workbench.uc1_catalog_coverage_report()
            emit({"kind": "result", "payload": report.model_dump(mode="json")})
            return
        if task_name == "export_use_case_1_ontology_bundle":
            progress(10, "Building standardized UC1 ontology bundle")
            result = workbench.export_use_case_1_ontology_bundle(progress)
            emit({"kind": "result", "payload": result})
            return
        if task_name == "export_use_case_1_standardized_workbooks":
            progress(10, "Building 4 source-specific UC1 standardized workbooks")
            output_dir = payload.get("output_dir")
            result = workbench.export_use_case_1_standardized_workbooks(
                progress,
                output_dir=Path(output_dir) if output_dir else None,
            )
            emit({"kind": "result", "payload": result})
            return
        if task_name == "generate_use_case_1_aas_models":
            progress(10, "Generating 5 UC1 AAS groups")
            output_dir = payload.get("output_dir")
            target_formats = [str(item) for item in payload.get("target_formats", [])]
            tx_rule_paths = payload.get("tx_rule_paths")
            result = workbench.generate_use_case_1_aas_models(
                progress,
                output_dir=Path(output_dir) if output_dir else None,
                target_formats=target_formats or None,
                tx_rule_paths=tx_rule_paths if isinstance(tx_rule_paths, dict) else None,
            )
            emit({"kind": "result", "payload": result})
            return
        if task_name == "validate_tx_rules":
            progress(10, "Validating Tx rule graph")
            result = workbench.validate_tx_rules(payload.get("rule_set", {}))
            progress(100, "Tx validation finished")
            emit({"kind": "result", "payload": result})
            return
        if task_name == "save_tx_rules":
            progress(10, "Saving Tx rule graph")
            output_path = payload.get("output_path")
            result = workbench.save_tx_rules(
                payload.get("rule_set", {}),
                tx_rule_set_id=str(payload.get("tx_rule_set_id", "")),
                output_path=Path(output_path) if output_path else None,
            )
            progress(100, "Tx rule graph saved")
            emit({"kind": "result", "payload": result})
            return
        if task_name == "preview_tx_rules":
            progress(10, "Preparing Tx preview")
            workbook_path = Path(str(payload["workbook_path"]))
            tx_rule_path = payload.get("tx_rule_path")
            result = workbench.preview_tx_rules(
                str(payload["source_type"]),
                workbook_path,
                identity_key=str(payload.get("identity_key", "")),
                tx_rule_path=Path(tx_rule_path) if tx_rule_path else None,
                tx_rule_set_id=str(payload.get("tx_rule_set_id", "")),
                rule_payload=payload.get("rule_set") if isinstance(payload.get("rule_set"), dict) else None,
            )
            progress(100, "Tx preview ready")
            emit({"kind": "result", "payload": result})
            return
        if task_name == "suggest_tx_rules":
            progress(10, "Requesting Tx rule suggestion")
            workbook_path = Path(str(payload["workbook_path"]))
            result = workbench.suggest_tx_rules(
                str(payload["source_type"]),
                workbook_path,
                target_properties=payload.get("target_properties") if isinstance(payload.get("target_properties"), dict) else None,
            )
            progress(100, "Tx suggestion ready")
            emit({"kind": "result", "payload": result})
            return
        if task_name == "generate_uc1_aas_from_tx":
            progress(10, "Generating UC1 AAS from Tx rules")
            workbook_path = Path(str(payload["workbook_path"]))
            output_dir = payload.get("output_dir")
            tx_rule_path = payload.get("tx_rule_path")
            target_formats = [str(item) for item in payload.get("target_formats", [])]
            result = workbench.generate_uc1_aas_from_tx(
                str(payload["source_type"]),
                workbook_path,
                progress,
                output_dir=Path(output_dir) if output_dir else None,
                target_formats=target_formats or None,
                tx_rule_path=Path(tx_rule_path) if tx_rule_path else None,
                tx_rule_set_id=str(payload.get("tx_rule_set_id", "")),
                rule_payload=payload.get("rule_set") if isinstance(payload.get("rule_set"), dict) else None,
            )
            emit({"kind": "result", "payload": result})
            return
        if task_name == "export_use_case_1_source_ontologies":
            progress(10, "Exporting 5 UC1 ontologies")
            output_dir = payload.get("output_dir")
            result = workbench.export_use_case_1_source_ontologies(
                progress,
                output_dir=Path(output_dir) if output_dir else None,
            )
            emit({"kind": "result", "payload": result})
            return
        if task_name == "export_use_case_1_transformation_bundle":
            progress(10, "Running the full UC1 transformation bundle")
            result = workbench.export_use_case_1_transformation_bundle(progress)
            emit({"kind": "result", "payload": result})
            return
        if task_name == "generate_aas_from_excel":
            progress(10, "Preparing AAS generation jobs")
            excel_path = Path(str(payload["excel_path"]))
            output_dir = payload.get("output_dir")
            target_formats = [str(item) for item in payload.get("target_formats", [])]
            paths = workbench.generate_aas_from_excel(
                excel_path,
                progress,
                output_dir=Path(output_dir) if output_dir else None,
                target_formats=target_formats or None,
            )
            emit({"kind": "result", "payload": [str(path) for path in paths]})
            return
        if task_name == "export_ontology_from_aas":
            progress(10, "Preparing ontology export")
            aas_paths = [Path(str(item)) for item in payload.get("aas_paths", [])]
            output_path = payload.get("output_path")
            path = workbench.export_ontology_from_aas_files(
                aas_paths,
                output_path=Path(output_path) if output_path else None,
            )
            progress(100, "Ontology export complete")
            emit({"kind": "result", "payload": str(path)})
            return
        if task_name == "prewarm_surya_models":
            progress(0, "Checking Surya model cache")
            result = workbench.prewarm_surya_models(progress)
            emit({"kind": "result", "payload": result})
            return
        raise ValueError(f"Unknown task: {task_name}")
    except Exception:
        emit({"kind": "error", "message": traceback.format_exc()})


def _stdout_emit(payload: dict[str, Any]) -> None:
    with _STDOUT_EMIT_LOCK:
        print(json.dumps(payload, ensure_ascii=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) < 3:
        print(json.dumps({"kind": "error", "message": "Expected workspace_root, task_name, payload_json"}), flush=True)
        return 1
    workspace_root, task_name, payload_json = argv[0], argv[1], argv[2]
    payload = json.loads(payload_json)
    run_workbench_task_process(workspace_root, task_name, payload, _stdout_emit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
