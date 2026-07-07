"""Optional ML/RAG verifier for v0.8 SourceArtifact linking.

This module is intentionally optional.  Callers use it only when the project
setting enables ML evidence linking and deterministic provenance cannot already
identify a reliable source artifact.
"""

from __future__ import annotations

import json
import math
import hashlib
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from iev4pi_transformation_tool.models import ExtractedFieldResult, SourceArtifact


@dataclass
class EvidenceLinkDecision:
    selected_artifact_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    abstain_reason: str = ""
    rule_basis: str = ""
    candidates: list[str] = field(default_factory=list)


class MLEvidenceLinker:
    """RAG + LLM verifier over already extracted SourceArtifacts."""

    def __init__(self, llm_client: Any, *, top_k: int = 6) -> None:
        self.llm_client = llm_client
        self.top_k = top_k
        self._decision_cache = None
        self._decision_locks: dict[str, threading.Lock] = {}
        self._decision_locks_guard = threading.Lock()
        self._artifact_vectors: dict[str, list[list[float]]] = {}
        self._artifact_vector_locks: dict[str, threading.Lock] = {}
        self._artifact_vector_locks_guard = threading.Lock()

    def link(
        self,
        field: ExtractedFieldResult,
        artifacts: list[SourceArtifact],
        *,
        source_path: str = "",
        page_or_sheet: str = "",
    ) -> EvidenceLinkDecision:
        if not artifacts:
            return EvidenceLinkDecision(abstain_reason="ML evidence linker has no candidate artifacts.")

        exact_decision = self._exact_match_decision(
            field,
            artifacts,
            source_path=source_path,
            page_or_sheet=page_or_sheet,
        )
        if exact_decision is not None:
            return exact_decision
        if _requires_exact_source_match(field):
            return EvidenceLinkDecision(
                confidence=0.0,
                abstain_reason="No source artifact contains the short field value exactly.",
                rule_basis="v0.8 exact-source rule for identifiers and numeric labels.",
                candidates=[
                    artifact.artifact_id
                    for artifact in self._scoped_text_artifacts(
                        artifacts,
                        source_path=source_path,
                        page_or_sheet=page_or_sheet,
                    )[: self.top_k]
                ],
            )

        cache_key = self._cache_key(field, artifacts, source_path=source_path, page_or_sheet=page_or_sheet)
        cached_decision = self._decision_cache_get(cache_key)
        if cached_decision is not None:
            return cached_decision

        with self._decision_lock(cache_key):
            cached_decision = self._decision_cache_get(cache_key)
            if cached_decision is not None:
                return cached_decision

            if not self._available():
                return EvidenceLinkDecision(abstain_reason="ML evidence linker is unavailable.")

            candidates = self._retrieve(field, artifacts, source_path=source_path, page_or_sheet=page_or_sheet)
            if not candidates:
                return EvidenceLinkDecision(abstain_reason="No source-verbatim candidates survived retrieval filters.")

            payload = {
                "v0_8_rule": (
                    "Object.Content_Text must be source-verbatim parser/OCR/Excel/manual rationale text. "
                    "Do not select bbox strings, geometry, topology, component_role, component_type, or other derived values. "
                    "For short identifiers, terminal numbers, RKZ-like values, and numeric labels, selected source text must contain the field value exactly; "
                    "if no candidate contains the exact value, abstain."
                ),
                "field": {
                    "name": field.field_name,
                    "value": field.value,
                    "confidence": field.confidence,
                },
                "candidate_artifacts": [
                    {
                        "artifact_id": artifact.artifact_id,
                        "source_path": artifact.source_path,
                        "page_or_sheet": artifact.page_or_sheet,
                        "object_type": artifact.object_type,
                        "source_operation": artifact.source_operation,
                        "content_text": artifact.content_text,
                        "confidence": artifact.confidence,
                    }
                    for artifact in candidates
                ],
            }
            try:
                result = self.llm_client.chat_json(
                    "Return ONLY JSON with selected_artifact_ids, confidence, abstain_reason, rule_basis.",
                    json.dumps(payload, ensure_ascii=False),
                    trace_context={"source": "aio_ml_evidence_linker", "field_name": field.field_name},
                )
            except Exception as exc:
                return EvidenceLinkDecision(abstain_reason=f"LLM verifier failed: {exc}", candidates=[a.artifact_id for a in candidates])

            ids = result.get("selected_artifact_ids", [])
            selected = [str(value) for value in ids if value] if isinstance(ids, list) else []
            candidates_by_id = {artifact.artifact_id: artifact for artifact in candidates}
            selected = [artifact_id for artifact_id in selected if artifact_id in candidates_by_id]
            confidence = _float_or_zero(result.get("confidence"))
            abstain_reason = str(result.get("abstain_reason", "") or "")
            if _requires_exact_source_match(field):
                exact_selected = [
                    artifact_id
                    for artifact_id in selected
                    if _artifact_contains_field_value(candidates_by_id[artifact_id], field)
                ]
                if not exact_selected:
                    abstain_reason = abstain_reason or "No selected source artifact contains the short field value exactly."
                    confidence = 0.0
                selected = exact_selected
            decision = EvidenceLinkDecision(
                selected_artifact_ids=selected,
                confidence=max(0.0, min(1.0, confidence)),
                abstain_reason=abstain_reason,
                rule_basis=str(result.get("rule_basis", "")),
                candidates=[artifact.artifact_id for artifact in candidates],
            )
            self._decision_cache_set(cache_key, decision)
            return decision

    def _retrieve(
        self,
        field: ExtractedFieldResult,
        artifacts: list[SourceArtifact],
        *,
        source_path: str,
        page_or_sheet: str,
    ) -> list[SourceArtifact]:
        scoped = [
            artifact
            for artifact in artifacts
            if artifact.content_text
            and artifact.object_type == "Text"
            and (not source_path or not artifact.source_path or artifact.source_path == source_path)
            and (not page_or_sheet or not artifact.page_or_sheet or artifact.page_or_sheet == page_or_sheet)
        ]
        if not scoped:
            scoped = [artifact for artifact in artifacts if artifact.content_text and artifact.object_type == "Text"]
        if not scoped:
            return []

        query_vectors = self.llm_client.embed_texts(
            [f"{field.field_name}: {field.value}"],
            trace_context={"source": "aio_ml_evidence_linker"},
        )
        artifact_vectors = self._artifact_text_vectors(scoped)
        if len(query_vectors) != 1 or len(artifact_vectors) != len(scoped):
            return scoped[: self.top_k]

        query = query_vectors[0]
        scored = [
            (_cosine(query, vector), artifact)
            for vector, artifact in zip(artifact_vectors, scoped)
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [artifact for _, artifact in scored[: self.top_k]]

    def _exact_match_decision(
        self,
        field: ExtractedFieldResult,
        artifacts: list[SourceArtifact],
        *,
        source_path: str,
        page_or_sheet: str,
    ) -> EvidenceLinkDecision | None:
        value = str(getattr(field, "value", "") or "").strip()
        if len(value) < 2:
            return None
        scoped = self._scoped_text_artifacts(artifacts, source_path=source_path, page_or_sheet=page_or_sheet)
        exact = [
            artifact
            for artifact in scoped
            if _artifact_contains_field_value(artifact, field)
            and not _looks_like_derived_content_text(artifact.content_text)
        ]
        if not exact:
            return None
        exact.sort(key=self._exact_match_rank)
        selected = exact[0]
        confidence = _source_artifact_link_confidence(selected)
        return EvidenceLinkDecision(
            selected_artifact_ids=[selected.artifact_id],
            confidence=confidence,
            abstain_reason="",
            rule_basis="Local exact source-verbatim match; LLM verifier skipped.",
            candidates=[artifact.artifact_id for artifact in exact[: self.top_k]],
        )

    def _scoped_text_artifacts(
        self,
        artifacts: list[SourceArtifact],
        *,
        source_path: str,
        page_or_sheet: str,
    ) -> list[SourceArtifact]:
        scoped = [
            artifact
            for artifact in artifacts
            if artifact.content_text
            and artifact.object_type == "Text"
            and (not source_path or not artifact.source_path or artifact.source_path == source_path)
            and (not page_or_sheet or not artifact.page_or_sheet or artifact.page_or_sheet == page_or_sheet)
        ]
        if scoped:
            return scoped
        return [artifact for artifact in artifacts if artifact.content_text and artifact.object_type == "Text"]

    @staticmethod
    def _exact_match_rank(artifact: SourceArtifact) -> tuple[int, int, float, str]:
        operation_priority = {
            "Cell": 0,
            "VL_Row": 0,
            "OCR_Text": 1,
            "Native_Text": 1,
            "Manual_Entry": 2,
        }.get(artifact.source_operation, 3)
        confidence = float(artifact.confidence) if artifact.confidence is not None else 0.9
        return (operation_priority, len(artifact.content_text or ""), -confidence, artifact.artifact_id)

    def _available(self) -> bool:
        return bool(
            self.llm_client
            and getattr(self.llm_client, "available", lambda: False)()
            and getattr(self.llm_client, "embedding_available", lambda: False)()
        )

    def _artifact_text_vectors(self, artifacts: list[SourceArtifact]) -> list[list[float]]:
        cache_key = self._artifact_vector_cache_key(artifacts)
        cached = self._artifact_vectors.get(cache_key)
        if cached is not None:
            return cached
        with self._artifact_vector_lock(cache_key):
            cached = self._artifact_vectors.get(cache_key)
            if cached is not None:
                return cached
            vectors = self.llm_client.embed_texts(
                [artifact.content_text for artifact in artifacts],
                trace_context={"source": "aio_ml_evidence_linker", "kind": "artifact_vectors"},
            )
            if len(vectors) == len(artifacts):
                self._artifact_vectors[cache_key] = vectors
                return vectors
            return []

    def _decision_lock(self, cache_key: str) -> threading.Lock:
        with self._decision_locks_guard:
            return self._decision_locks.setdefault(cache_key, threading.Lock())

    def _artifact_vector_lock(self, cache_key: str) -> threading.Lock:
        with self._artifact_vector_locks_guard:
            return self._artifact_vector_locks.setdefault(cache_key, threading.Lock())

    def _artifact_vector_cache_key(self, artifacts: list[SourceArtifact]) -> str:
        payload = {
            "version": 1,
            "client": self._client_cache_identity(),
            "artifacts": [
                {
                    "artifact_id": artifact.artifact_id,
                    "source_path": artifact.source_path,
                    "page_or_sheet": artifact.page_or_sheet,
                    "content_text": artifact.content_text,
                    "method": artifact.method,
                }
                for artifact in artifacts
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_key(
        self,
        field: ExtractedFieldResult,
        artifacts: list[SourceArtifact],
        *,
        source_path: str,
        page_or_sheet: str,
    ) -> str:
        payload = {
            "version": 1,
            "client": self._client_cache_identity(),
            "top_k": self.top_k,
            "source_path": source_path,
            "page_or_sheet": page_or_sheet,
            "field": {
                "name": field.field_name,
                "value": field.value,
                "confidence": field.confidence,
            },
            "artifacts": [
                {
                    "artifact_id": artifact.artifact_id,
                    "source_path": artifact.source_path,
                    "page_or_sheet": artifact.page_or_sheet,
                    "object_type": artifact.object_type,
                    "source_operation": artifact.source_operation,
                    "content_text": artifact.content_text,
                    "confidence": artifact.confidence,
                    "method": artifact.method,
                }
                for artifact in artifacts
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _client_cache_identity(self) -> dict[str, str]:
        config = getattr(self.llm_client, "config", None)
        return {
            "base_url": str(getattr(config, "base_url", "") or ""),
            "chat_model": str(getattr(config, "chat_model", "") or ""),
            "embedding_model": str(
                getattr(self.llm_client, "resolved_embedding_model", lambda: getattr(config, "embedding_model", "") or "")()
            ),
        }

    def _decision_disk_cache(self):
        if self._decision_cache is None:
            from iev4pi_transformation_tool.core.disk_cache import DiskDict
            self._decision_cache = DiskDict("aio_ml_evidence_linker")
        return self._decision_cache

    def _decision_cache_get(self, cache_key: str) -> EvidenceLinkDecision | None:
        cached = self._decision_disk_cache().get(cache_key)
        if not isinstance(cached, dict):
            return None
        return EvidenceLinkDecision(
            selected_artifact_ids=[str(value) for value in cached.get("selected_artifact_ids", []) if value]
            if isinstance(cached.get("selected_artifact_ids"), list)
            else [],
            confidence=_float_or_zero(cached.get("confidence")),
            abstain_reason=str(cached.get("abstain_reason", "")),
            rule_basis=str(cached.get("rule_basis", "")),
            candidates=[str(value) for value in cached.get("candidates", []) if value]
            if isinstance(cached.get("candidates"), list)
            else [],
        )

    def _decision_cache_set(self, cache_key: str, decision: EvidenceLinkDecision) -> None:
        self._decision_disk_cache()[cache_key] = {
            "selected_artifact_ids": decision.selected_artifact_ids,
            "confidence": decision.confidence,
            "abstain_reason": decision.abstain_reason,
            "rule_basis": decision.rule_basis,
            "candidates": decision.candidates,
        }


def benchmark_allows_ml_linking(report_path: Path) -> bool:
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("passed") and payload.get("ml_linking_enabled"))


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _source_artifact_link_confidence(artifact: SourceArtifact) -> float:
    if artifact.confidence is not None:
        try:
            return max(0.0, min(1.0, float(artifact.confidence)))
        except (TypeError, ValueError):
            pass
    if artifact.source_operation in {"Cell", "VL_Row"}:
        return 0.98
    if artifact.source_operation == "Manual_Entry":
        return 0.75
    if str(artifact.method or "").lower() in {"native_text", "pymupdf"}:
        return 0.92
    return 0.88


def _looks_like_derived_content_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if re.fullmatch(r"-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?", text):
        return True
    lowered = text.casefold()
    if any(token in lowered for token in ("bbox", "component_role", "component_type", "trace_path", "raw_context")):
        return True
    return False


def _requires_exact_source_match(field: ExtractedFieldResult) -> bool:
    value = str(getattr(field, "value", "") or "").strip()
    if not value:
        return False
    if len(value) <= 16 and re.search(r"[:\-/._]|\d", value):
        return True
    field_name = str(getattr(field, "field_name", "") or "").lower()
    if re.search(r"(rkz|klemm|terminal|klemme|tag|plt|stelle|device|component|anschluss)", field_name):
        return True
    return False


def _artifact_contains_field_value(artifact: SourceArtifact, field: ExtractedFieldResult) -> bool:
    value = _normalize_match_text(str(getattr(field, "value", "") or ""))
    text = _normalize_match_text(str(getattr(artifact, "content_text", "") or ""))
    if not value:
        return False
    prefix = r"(?<!\w)" if re.match(r"^\w", value) else ""
    suffix = r"(?!\w)" if re.search(r"\w$", value) else ""
    return re.search(f"{prefix}{re.escape(value)}{suffix}", text) is not None


def _normalize_match_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())
