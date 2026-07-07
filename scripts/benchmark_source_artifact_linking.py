#!/usr/bin/env python3
"""Benchmark ML-assisted v0.8 SourceArtifact evidence linking.

Default mode is offline and non-mutating for the main pipeline.  Use
``--live-api`` to exercise the configured OpenAI-compatible chat/VLM/embedding
models from ``.iev4pi/settings.json``.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from iev4pi_transformation_tool.core.aio_exporter import IDGen
from iev4pi_transformation_tool.core.llm_client import OpenAICompatibleLLMClient
from iev4pi_transformation_tool.core.source_artifacts import build_source_artifact_objects
from iev4pi_transformation_tool.models import (
    DocumentFamily,
    EvidenceRef,
    ExtractedFieldResult,
    ExtractedRecord,
    LLMBackendConfig,
    ProjectSettings,
)


DOCUMENT_PATTERNS = [
    "Documents/Verschaltungslisten/E-SchrankAufbau/Stromlaufplan_E_Schrank.pdf",
    "Documents-Others/Verschaltungslisten/Stromlaufpläne_*/*.pdf",
    "Documents/**/*.xls",
    "Documents/**/*.xlsx",
    "Documents-Others/**/*.xls",
    "Documents-Others/**/*.xlsx",
    "Documents/**/*Stellenplan*.pdf",
    "Documents-Others/**/*Stellenplan*.pdf",
    "Documents/**/*Datasheet*.pdf",
    "Documents-Others/**/*Datasheet*.pdf",
    "Documents/**/*RI*.pdf",
    "Documents-Others/**/*RI*.pdf",
]

THRESHOLDS = {
    "json_parse_success": 0.99,
    "top1_accuracy": 0.90,
    "top3_accuracy": 0.97,
    "derived_false_positive": 0.02,
    "consistency": 0.90,
    "attribute_mapping_accuracy": 0.90,
    "canonicalization_accuracy": 0.90,
    "minimum_live_samples": 100,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--settings", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--live-api", action="store_true", help="Call configured chat/embedding models.")
    parser.add_argument("--sample-count", type=int, default=120, help="Live API sample count for strict large benchmark.")
    parser.add_argument("--repeat-count", type=int, default=3, help="Repeated runs per live API sample.")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    settings = _load_settings(workspace, args.settings)
    report = run_benchmark(
        workspace,
        settings,
        live_api=args.live_api,
        sample_count=args.sample_count,
        repeat_count=args.repeat_count,
    )

    output = args.output or workspace / ".iev4pi" / "source_artifact_linking_benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "passed": report["passed"], "ml_linking_enabled": report["ml_linking_enabled"]}, indent=2))
    return 0 if report["passed"] else 2


def run_benchmark(
    workspace: Path,
    settings: ProjectSettings,
    *,
    live_api: bool = False,
    sample_count: int = 120,
    repeat_count: int = 3,
) -> dict[str, Any]:
    docs = _discover_documents(workspace)
    offline = _run_offline_policy_tests()
    live = (
        _run_live_model_tests(settings.llm, sample_count=sample_count, repeat_count=repeat_count)
        if live_api
        else {"enabled": False, "reason": "Run with --live-api to test configured models."}
    )
    metrics = _merge_metrics(offline, live)
    passed = _passes_thresholds(metrics, live_api=live_api)
    return {
        "benchmark": "v0.8_source_artifact_linking",
        "workspace": str(workspace),
        "documents": docs,
        "settings": {
            "base_url": settings.llm.base_url,
            "chat_model": settings.llm.chat_model,
            "vlm_model": settings.llm.vlm_model,
            "embedding_model": settings.llm.embedding_model,
            "aio_ml_evidence_linking_enabled": settings.aio_ml_evidence_linking_enabled,
            "sample_count": sample_count,
            "repeat_count": repeat_count,
            "strict_large": sample_count >= THRESHOLDS["minimum_live_samples"],
        },
        "thresholds": THRESHOLDS,
        "offline_policy_tests": offline,
        "live_model_tests": live,
        "metrics": metrics,
        "passed": passed,
        "ml_linking_enabled": bool(passed and live_api),
    }


def _run_offline_policy_tests() -> dict[str, Any]:
    text_evidence = EvidenceRef(
        source_path="Documents/Stromlaufplan_E_Schrank.pdf",
        page_or_sheet="Page 1",
        cell_range_or_bbox="100,200,160,220",
        snippet="-F1 NOT AUS",
        score=0.94,
        evidence_type="ocr_text",
        engine="fixture_ocr",
    )
    rec = ExtractedRecord(
        family=DocumentFamily.STROMLAUF_COMPONENT,
        source_path=text_evidence.source_path,
        record_key="fixture:pdf:1",
        display_name="-F1",
        results=[
            ExtractedFieldResult(field_name="display_label", value="-F1", confidence=0.92, evidence_refs=[text_evidence]),
            ExtractedFieldResult(field_name="bbox", value="3676.0,2835.6,3885.8,2951.7", confidence=0.62),
            ExtractedFieldResult(field_name="component_role", value="Fuse", confidence=0.70),
        ],
    )
    vl = ExtractedRecord(
        family=DocumentFamily.VERSCHALTUNGSLISTE_ROW,
        source_path="Documents/Verschaltungsliste.xlsx",
        record_key="fixture:vl:1",
        display_name="VL 1",
        results=[
            ExtractedFieldResult(field_name="klemmleiste_x01", value="6", confidence=1.0),
            ExtractedFieldResult(field_name="gerat", value="-K1", confidence=1.0),
            ExtractedFieldResult(field_name="verschaltung", value="N", confidence=1.0),
        ],
    )
    rows, index = build_source_artifact_objects(IDGen(), "D.fixture", [rec, vl])
    content_values = [str(row.get("Content_Text", "")) for row in rows]
    negative_values = {"3676.0,2835.6,3885.8,2951.7", "Fuse"}
    false_positive_count = sum(1 for value in content_values if value in negative_values)
    field_link = index.object_for_field(rec, rec.results[0])
    return {
        "enabled": True,
        "object_rows": len(rows),
        "content_values": content_values,
        "derived_false_positive_count": false_positive_count,
        "derived_false_positive": false_positive_count / max(1, len(negative_values)),
        "source_text_preserved": "-F1 NOT AUS" in content_values,
        "vl_row_serialized": any("6 | -K1 | N" == value for value in content_values),
        "field_link_resolved": bool(field_link),
        "passed": (
            false_positive_count == 0
            and "-F1 NOT AUS" in content_values
            and any("6 | -K1 | N" == value for value in content_values)
            and bool(field_link)
        ),
    }


def _run_live_model_tests(
    config: LLMBackendConfig,
    *,
    sample_count: int,
    repeat_count: int,
) -> dict[str, Any]:
    client = OpenAICompatibleLLMClient(config)
    probe = client.runtime_probe()
    samples = _linking_samples(sample_count)
    selected: list[str] = []
    json_success = 0
    schema_success = 0
    top1_success = 0
    top3_success = 0
    attribute_success = 0
    canonical_success = 0
    positive_runs = 0
    attribute_expected_runs = 0
    canonical_expected_runs = 0
    negative_false_positive = 0
    negative_runs = 0
    errors: list[str] = []

    if not probe.get("available"):
        return {"enabled": True, "available": False, "probe": probe, "errors": ["Configured chat model is unavailable."]}

    for sample in samples:
        for run_idx in range(repeat_count):
            try:
                result = client.chat_json(
                    _system_prompt(),
                    json.dumps(sample["prompt"], ensure_ascii=False),
                    trace_context={"benchmark": "source_artifact_linking", "sample": sample["id"], "run": run_idx},
                )
                json_success += 1
                if not _valid_linker_response(result):
                    selected.append("__invalid__")
                    continue
                schema_success += 1
                ids = result.get("selected_artifact_ids", [])
                selected_ids = [str(item) for item in ids] if isinstance(ids, list) else []
                first = selected_ids[0] if selected_ids else ""
                selected.append(str(first))
                if sample["expected_top1"]:
                    positive_runs += 1
                    if first == sample["expected_top1"]:
                        top1_success += 1
                    if sample["expected_top1"] in selected_ids[:3]:
                        top3_success += 1
                expected_attr = sample.get("expected_attribute_name", "")
                if expected_attr:
                    attribute_expected_runs += 1
                    if str(result.get("attribute_name", "")) == expected_attr:
                        attribute_success += 1
                expected_canonical = sample.get("expected_canonical_value", "")
                if expected_canonical:
                    canonical_expected_runs += 1
                    if str(result.get("canonical_value", "")) == expected_canonical:
                        canonical_success += 1
                if sample.get("negative"):
                    negative_runs += 1
                    if first:
                        negative_false_positive += 1
            except Exception as exc:  # pragma: no cover - live API dependent
                errors.append(f"{sample['id']} run {run_idx}: {exc}")

    embedding_ok = False
    embedding_dims = 0
    try:
        vectors = client.embed_texts(["-F1 NOT AUS", "-X01 terminal 6"], trace_context={"benchmark": "source_artifact_linking"})
        embedding_ok = bool(vectors and vectors[0])
        embedding_dims = len(vectors[0]) if embedding_ok else 0
    except Exception as exc:  # pragma: no cover - live API dependent
        errors.append(f"embedding: {exc}")

    total_runs = len(samples) * repeat_count
    choices_by_sample = [selected[i : i + repeat_count] for i in range(0, len(selected), repeat_count)]
    consistency_scores = [
        max(group.count(choice) for choice in set(group)) / len(group)
        for group in choices_by_sample
        if group
    ]
    return {
        "enabled": True,
        "available": True,
        "probe": probe,
        "sample_count": len(samples),
        "repeat_count": repeat_count,
        "json_parse_success": json_success / max(1, total_runs),
        "schema_success": schema_success / max(1, total_runs),
        "top1_accuracy": top1_success / max(1, positive_runs),
        "top3_accuracy": top3_success / max(1, positive_runs),
        "attribute_mapping_accuracy": attribute_success / max(1, attribute_expected_runs),
        "canonicalization_accuracy": canonical_success / max(1, canonical_expected_runs),
        "derived_false_positive": negative_false_positive / max(1, negative_runs),
        "consistency": statistics.mean(consistency_scores) if consistency_scores else 0.0,
        "embedding_available": embedding_ok,
        "embedding_dimensions": embedding_dims,
        "errors": errors,
    }


def _linking_samples(sample_count: int = 120) -> list[dict[str, Any]]:
    rule = "Object.Content_Text must be parser/OCR/Excel source-verbatim text. Derived bbox/component role values are not source text."
    base = [
        {
            "id": "pdf_text_label",
            "expected_top1": "A.text.1",
            "expected_attribute_name": "Device_ID",
            "expected_canonical_value": "-F1",
            "prompt": {
                "rule": rule,
                "attribute_lookup_scope": "Element",
                "allowed_attribute_names": ["Device_ID", "Terminal_Number", "Terminal_Strip_Designation", "Wire_Color"],
                "canonicalization_rules": {"leiterfarbe=rot": {"attribute_name": "Wire_Color", "canonical_value": "RD"}},
                "field": {"name": "device_id", "value": "-F1"},
                "candidate_artifacts": [
                    {"artifact_id": "A.text.1", "content_text": "-F1 NOT AUS", "object_type": "Text"},
                    {"artifact_id": "A.geom.1", "content_text": "", "object_type": "Symbol", "bbox": [3676, 2835, 3885, 2951]},
                    {"artifact_id": "A.manual.1", "content_text": "component_role=Fuse", "object_type": "Text"},
                ],
            },
        },
        {
            "id": "negative_bbox",
            "expected_top1": "",
            "negative": True,
            "prompt": {
                "rule": rule,
                "field": {"name": "bbox", "value": "3676.0,2835.6,3885.8,2951.7"},
                "allowed_attribute_names": ["Device_ID", "Terminal_Number", "Terminal_Strip_Designation", "Wire_Color"],
                "must_abstain_if": ["field name is bbox", "candidate content is a bbox CSV", "candidate is derived role/type/context"],
                "candidate_artifacts": [
                    {"artifact_id": "A.geom.2", "content_text": "", "object_type": "Symbol", "bbox": [3676, 2835, 3885, 2951]},
                    {"artifact_id": "A.bad.1", "content_text": "3676.0,2835.6,3885.8,2951.7", "object_type": "Text"},
                ],
            },
        },
        {
            "id": "vl_terminal_row",
            "expected_top1": "A.vl.1",
            "expected_attribute_name": "Terminal_Number",
            "expected_canonical_value": "6",
            "prompt": {
                "rule": rule,
                "attribute_lookup_scope": "Element",
                "allowed_attribute_names": ["Device_ID", "Terminal_Number", "Terminal_Strip_Designation", "Wire_Color"],
                "canonicalization_rules": {"terminal number values remain unchanged": {"attribute_name": "Terminal_Number"}},
                "field": {"name": "klemmleiste_x01", "value": "6"},
                "candidate_artifacts": [
                    {"artifact_id": "A.vl.1", "content_text": "-X01 | 6 | -K1 | N", "object_type": "Text", "source_operation": "VL_Row"},
                    {"artifact_id": "A.bad.2", "content_text": "component_type=Terminal", "object_type": "Text"},
                ],
            },
        },
        {
            "id": "wire_color_canonical",
            "expected_top1": "A.cell.1",
            "expected_attribute_name": "Wire_Color",
            "expected_canonical_value": "RD",
            "prompt": {
                "rule": rule,
                "attribute_lookup_scope": "Connection",
                "allowed_attribute_names": ["Wire_Color", "Polarity", "Cross_Section"],
                "canonicalization_rules": {"rot": {"attribute_name": "Wire_Color", "canonical_value": "RD"}},
                "field": {"name": "leiterfarbe", "value": "rot"},
                "candidate_artifacts": [
                    {"artifact_id": "A.cell.1", "content_text": "rot", "object_type": "Text", "source_operation": "Cell"},
                    {"artifact_id": "A.geom.3", "content_text": "", "object_type": "Symbol"},
                ],
            },
        },
        {
            "id": "negative_component_role",
            "expected_top1": "",
            "negative": True,
            "prompt": {
                "rule": rule,
                "attribute_lookup_scope": "Element",
                "allowed_attribute_names": ["Device_ID", "Terminal_Number", "Terminal_Strip_Designation", "Wire_Color"],
                "must_abstain_if": ["field name is component_role", "candidate content is derived role/type/context"],
                "field": {"name": "component_role", "value": "Fuse"},
                "candidate_artifacts": [
                    {"artifact_id": "A.role.1", "content_text": "component_role=Fuse", "object_type": "Text"},
                    {"artifact_id": "A.geom.4", "content_text": "", "object_type": "Symbol"},
                ],
            },
        },
    ]
    samples: list[dict[str, Any]] = []
    for idx in range(max(1, sample_count)):
        template = json.loads(json.dumps(base[idx % len(base)]))
        template["id"] = f"{template['id']}:{idx:03d}"
        for candidate in template["prompt"].get("candidate_artifacts", []):
            candidate["source_path"] = _sample_source_path(idx)
            candidate["page_or_sheet"] = f"Page {(idx % 8) + 1}"
        samples.append(template)
    return samples


def _system_prompt() -> str:
    return (
        "You are a strict v0.8 AIO evidence linker. Return ONLY one JSON object with exactly these keys: "
        "selected_artifact_ids (array of strings), attribute_name (string), canonical_value (string), "
        "confidence (number 0..1), abstain_reason (string, empty when not abstaining), rule_basis (string). "
        "Select only parser/OCR/Excel source-verbatim artifacts. If the field or candidate is bbox, geometry, "
        "component_role, component_type, raw_context, or any derived value, selected_artifact_ids MUST be [] "
        "and abstain_reason MUST explain why. Attribute_name MUST be one of allowed_attribute_names. "
        "Use canonicalization_rules when supplied; otherwise preserve source-verbatim value."
    )


def _valid_linker_response(result: dict[str, object]) -> bool:
    ids = result.get("selected_artifact_ids")
    has_ids = isinstance(ids, list)
    has_confidence = isinstance(result.get("confidence"), (int, float))
    has_rule_basis = isinstance(result.get("rule_basis"), str)
    has_abstain = isinstance(result.get("abstain_reason"), str)
    return has_ids and has_confidence and has_rule_basis and has_abstain


def _merge_metrics(offline: dict[str, Any], live: dict[str, Any]) -> dict[str, float]:
    return {
        "json_parse_success": float(live.get("json_parse_success", 0.0)),
        "schema_success": float(live.get("schema_success", 0.0)),
        "top1_accuracy": float(live.get("top1_accuracy", 0.0)),
        "top3_accuracy": float(live.get("top3_accuracy", 0.0)),
        "derived_false_positive": float(live.get("derived_false_positive", offline.get("derived_false_positive", 1.0))),
        "consistency": float(live.get("consistency", 0.0)),
        "attribute_mapping_accuracy": float(live.get("attribute_mapping_accuracy", 0.0)),
        "canonicalization_accuracy": float(live.get("canonicalization_accuracy", 0.0)),
        "live_sample_count": float(live.get("sample_count", 0.0)),
        "offline_policy_passed": 1.0 if offline.get("passed") else 0.0,
        "embedding_available": 1.0 if live.get("embedding_available") else 0.0,
    }


def _passes_thresholds(metrics: dict[str, float], *, live_api: bool) -> bool:
    if not live_api:
        return False
    return (
        metrics["offline_policy_passed"] >= 1.0
        and metrics["embedding_available"] >= 1.0
        and metrics["json_parse_success"] >= THRESHOLDS["json_parse_success"]
        and metrics["schema_success"] >= THRESHOLDS["json_parse_success"]
        and metrics["top1_accuracy"] >= THRESHOLDS["top1_accuracy"]
        and metrics["top3_accuracy"] >= THRESHOLDS["top3_accuracy"]
        and metrics["derived_false_positive"] <= THRESHOLDS["derived_false_positive"]
        and metrics["consistency"] >= THRESHOLDS["consistency"]
        and metrics["attribute_mapping_accuracy"] >= THRESHOLDS["attribute_mapping_accuracy"]
        and metrics["canonicalization_accuracy"] >= THRESHOLDS["canonicalization_accuracy"]
        and metrics["live_sample_count"] >= THRESHOLDS["minimum_live_samples"]
    )


def _sample_source_path(idx: int) -> str:
    sources = [
        "Documents/Verschaltungslisten/E-SchrankAufbau/Stromlaufplan_E_Schrank.pdf",
        "Documents-Others/Verschaltungslisten/Stromlaufpläne_HC10/2025-11-24_IO-Baugruppe01_HC10.pdf",
        "Documents-Others/Verschaltungslisten/2025-01-15Klemmenplan Wabe 10.xlsx",
        "Documents-Others/Stellenplaene/2023_01 Stellenübersicht_HC10.xlsx",
        "Documents/Piping Diagram/Assembly_3D_template_filled.xlsx",
    ]
    return sources[idx % len(sources)]


def _discover_documents(workspace: Path) -> dict[str, Any]:
    matched: list[str] = []
    for pattern in DOCUMENT_PATTERNS:
        matched.extend(str(path.relative_to(workspace)) for path in workspace.glob(pattern) if path.is_file())
    return {"patterns": DOCUMENT_PATTERNS, "matched_count": len(set(matched)), "matched": sorted(set(matched))[:200]}


def _load_settings(workspace: Path, explicit: Path | None) -> ProjectSettings:
    settings_path = explicit or workspace / ".iev4pi" / "settings.json"
    if not settings_path.exists():
        settings_path = workspace / "Exports" / "settings.json"
    if settings_path.exists():
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        payload = {}
    payload.setdefault("workspace_root", str(workspace))
    payload.setdefault("database_path", str(workspace / ".iev4pi" / "state.sqlite"))
    payload.setdefault("export_dir", str(workspace / "Exports"))
    return ProjectSettings.model_validate(payload)


if __name__ == "__main__":
    raise SystemExit(main())
