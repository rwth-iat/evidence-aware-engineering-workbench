from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from iev4pi_transformation_tool.core.llm_client import OpenAICompatibleLLMClient
from iev4pi_transformation_tool.core.retriever import Retriever
from iev4pi_transformation_tool.core.utils import clean_cell, ensure_dir, normalize_identifier
from iev4pi_transformation_tool.models import ConsistencyDecision, EvidenceBundle, SchemaField


class EvidenceResolver:
    def __init__(
        self,
        retriever: Retriever,
        llm_client: OpenAICompatibleLLMClient | None = None,
        *,
        cache_dir: Path | None = None,
        logger: Callable[..., Any] | None = None,
    ) -> None:
        self.retriever = retriever
        self.llm_client = llm_client
        self.cache_dir = ensure_dir(cache_dir) if cache_dir is not None else None
        self._logger = logger

    def _log_debug(
        self,
        *,
        action: str,
        message: str,
        level: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._logger is None:
            return
        self._logger(
            source="llm",
            action=action,
            message=message,
            level=level,
            details=details,
        )

    def verify_document_presence(
        self,
        *,
        source_type: str,
        canonical_entity_id: str,
        query: str,
        evidence_bundle: EvidenceBundle,
        strict_present: bool = False,
        recommended_action: str = "",
        rule_support: list[str] | None = None,
    ) -> ConsistencyDecision:
        support_rules = list(rule_support or [])
        if strict_present:
            return ConsistencyDecision(
                decision="present",
                canonical_entity_id=canonical_entity_id,
                support_evidence_ids=evidence_bundle.support_evidence_ids,
                contradiction_evidence_ids=[],
                confidence=1.0,
                uncertainty_reason="",
                recommended_action=recommended_action,
                needs_review=False,
                evidence_bundle_id=evidence_bundle.id,
                rule_support=support_rules or ["strict_exact_match"],
                llm_verification_status="not_needed",
            )

        top_hit = evidence_bundle.hits[0] if evidence_bundle.hits else None
        top_score = top_hit.score if top_hit is not None else 0.0
        _support_count = len(evidence_bundle.support_evidence_ids)
        _contra_count = len(evidence_bundle.contradiction_evidence_ids)

        # Fast heuristic: clear evidence without LLM verification.
        # High score OR multiple supporting evidence → "present"
        if top_score >= 0.70 or (_support_count >= 2 and top_score >= 0.40):
            return ConsistencyDecision(
                decision="present",
                canonical_entity_id=canonical_entity_id,
                support_evidence_ids=evidence_bundle.support_evidence_ids,
                contradiction_evidence_ids=[],
                confidence=min(0.95, max(0.65, top_score)),
                uncertainty_reason="" if top_score >= 0.85 else "retrieval_only_alignment",
                recommended_action=recommended_action,
                needs_review=top_score < 0.85,
                evidence_bundle_id=evidence_bundle.id,
                rule_support=support_rules or ["hybrid_retrieval_present"],
                llm_verification_status="not_needed",
            )
        # Low score AND no supporting evidence → "missing"
        if top_score <= 0.20 and _support_count == 0:
            return ConsistencyDecision(
                decision="missing",
                canonical_entity_id=canonical_entity_id,
                support_evidence_ids=[],
                contradiction_evidence_ids=evidence_bundle.contradiction_evidence_ids,
                confidence=0.9,
                uncertainty_reason="no_supporting_evidence",
                recommended_action=recommended_action,
                needs_review=False,
                evidence_bundle_id=evidence_bundle.id,
                rule_support=support_rules or ["hybrid_retrieval_missing"],
                llm_verification_status="not_needed",
            )

        if self.llm_client is None or not self.llm_client.available():
            return ConsistencyDecision(
                decision="needs_review",
                canonical_entity_id=canonical_entity_id,
                support_evidence_ids=evidence_bundle.support_evidence_ids,
                contradiction_evidence_ids=evidence_bundle.contradiction_evidence_ids,
                confidence=min(0.75, max(0.35, top_score)),
                uncertainty_reason="ambiguous_retrieval_without_llm",
                recommended_action=recommended_action,
                needs_review=True,
                evidence_bundle_id=evidence_bundle.id,
                rule_support=support_rules or ["hybrid_retrieval_ambiguous"],
                llm_verification_status="unavailable",
            )
        decision = self._cached_llm_document_decision(
            source_type=source_type,
            canonical_entity_id=canonical_entity_id,
            query=query,
            evidence_bundle=evidence_bundle,
            recommended_action=recommended_action,
            rule_support=support_rules,
        )
        if decision is not None:
            return decision
        return ConsistencyDecision(
            decision="needs_review",
            canonical_entity_id=canonical_entity_id,
            support_evidence_ids=evidence_bundle.support_evidence_ids,
            contradiction_evidence_ids=evidence_bundle.contradiction_evidence_ids,
            confidence=min(0.75, max(0.35, top_score)),
            uncertainty_reason="llm_verifier_failed",
            recommended_action=recommended_action,
            needs_review=True,
            evidence_bundle_id=evidence_bundle.id,
            rule_support=support_rules or ["hybrid_retrieval_ambiguous"],
            llm_verification_status="failed",
        )

    def extract_field_value(
        self,
        *,
        field: SchemaField,
        source_path: str,
        evidence_bundle: EvidenceBundle,
        minimum_confidence: float = 0.45,
    ) -> dict[str, Any]:
        if self.llm_client is None or not self.llm_client.available():
            return {}
        payload = self._cached_llm_field_decision(
            field=field,
            source_path=source_path,
            evidence_bundle=evidence_bundle,
        )
        value = clean_cell(payload.get("value", ""))
        if not value:
            return {}
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < minimum_confidence:
            return {}
        support_ids = payload.get("support_evidence_ids", [])
        return {
            "value": value,
            "normalized_value": clean_cell(payload.get("normalized_value", "")) or value,
            "confidence": min(1.0, confidence),
            "decision_confidence": min(1.0, confidence),
            "uncertainty_reason": clean_cell(payload.get("uncertainty_reason", "")),
            "needs_review": bool(payload.get("needs_review", False)),
            "llm_verification_status": "verified",
            "rule_support": ["field_specific_retrieval", "constrained_llm_extraction"],
            "evidence_bundle_id": evidence_bundle.id,
            "support_evidence_ids": [str(item) for item in support_ids if str(item).strip()],
        }

    def _cached_llm_document_decision(
        self,
        *,
        source_type: str,
        canonical_entity_id: str,
        query: str,
        evidence_bundle: EvidenceBundle,
        recommended_action: str,
        rule_support: list[str],
    ) -> ConsistencyDecision | None:
        cache_key = self._cache_key(
            "document_presence",
            {
                "source_type": source_type,
                "canonical_entity_id": canonical_entity_id,
                "query": query,
                "bundle_id": evidence_bundle.id,
                "hits": [hit.chunk.id for hit in evidence_bundle.hits],
            },
        )
        payload = self._load_cache(cache_key)
        if payload is None:
            evidence_text = self._evidence_text(evidence_bundle)
            payload = self.llm_client.chat_json(
                (
                    "You are a conservative engineering evidence verifier. "
                    "You must only reason over the supplied evidence. "
                    "Return JSON only. Never invent missing records."
                ),
                (
                    f"Source type: {source_type}\n"
                    f"Canonical entity id: {canonical_entity_id}\n"
                    f"Query: {query}\n"
                    f"Recommended action: {recommended_action}\n"
                    "Return JSON with keys: decision, canonical_entity_id, support_evidence_ids, "
                    "contradiction_evidence_ids, confidence, uncertainty_reason, recommended_action, needs_review.\n"
                    "Allowed decision values: present, missing, ambiguous, needs_review.\n"
                    "If evidence is weak or contradictory, prefer ambiguous or needs_review.\n\n"
                    f"Evidence:\n{evidence_text}"
                ),
                trace_context={
                    "workflow": "llm_document_presence",
                    "source_type": source_type,
                    "canonical_entity_id": canonical_entity_id,
                    "query": query,
                    "recommended_action": recommended_action,
                    "evidence_bundle_id": evidence_bundle.id,
                    "cache_key": cache_key,
                },
            )
            if payload:
                self._save_cache(cache_key, payload)
        else:
            self._log_debug(
                action="cache_hit",
                message=f"LLM document verifier cache hit for {canonical_entity_id}",
                details={
                    "workflow": "llm_document_presence",
                    "source_type": source_type,
                    "canonical_entity_id": canonical_entity_id,
                    "query": query,
                    "recommended_action": recommended_action,
                    "evidence_bundle_id": evidence_bundle.id,
                    "cache_key": cache_key,
                    "output": payload,
                },
            )
        if not payload:
            return None
        decision = clean_cell(payload.get("decision", "")) or "needs_review"
        if decision not in {"present", "missing", "ambiguous", "needs_review"}:
            decision = "needs_review"
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return ConsistencyDecision(
            decision=decision,
            canonical_entity_id=clean_cell(payload.get("canonical_entity_id", "")) or canonical_entity_id,
            support_evidence_ids=[str(item) for item in payload.get("support_evidence_ids", []) if str(item).strip()],
            contradiction_evidence_ids=[str(item) for item in payload.get("contradiction_evidence_ids", []) if str(item).strip()],
            confidence=min(1.0, max(0.0, confidence)),
            uncertainty_reason=clean_cell(payload.get("uncertainty_reason", "")),
            recommended_action=clean_cell(payload.get("recommended_action", "")) or recommended_action,
            needs_review=bool(payload.get("needs_review", decision in {"ambiguous", "needs_review"})),
            evidence_bundle_id=evidence_bundle.id,
            rule_support=rule_support or ["llm_verifier"],
            llm_verification_status="verified",
        )

    def _cached_llm_field_decision(
        self,
        *,
        field: SchemaField,
        source_path: str,
        evidence_bundle: EvidenceBundle,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(
            "field_extraction",
            {
                "field": field.name,
                "aliases": field.aliases,
                "source_path": source_path,
                "bundle_id": evidence_bundle.id,
                "hits": [hit.chunk.id for hit in evidence_bundle.hits],
            },
        )
        payload = self._load_cache(cache_key)
        if payload is not None:
            self._log_debug(
                action="cache_hit",
                message=f"LLM field verifier cache hit for {field.name}",
                details={
                    "workflow": "llm_field_extraction",
                    "field_name": field.name,
                    "aliases": field.aliases,
                    "source_path": source_path,
                    "evidence_bundle_id": evidence_bundle.id,
                    "cache_key": cache_key,
                    "output": payload,
                },
            )
            return payload
        evidence_text = self._evidence_text(evidence_bundle)
        payload = self.llm_client.chat_json(
            (
                "You extract one engineering field from evidence. "
                "You must only use explicit evidence from the supplied snippets. "
                "Return JSON only."
            ),
            (
                f"Field name: {field.name}\n"
                f"Aliases: {', '.join(field.aliases)}\n"
                f"Source path: {source_path}\n"
                "Return JSON with keys: value, normalized_value, confidence, uncertainty_reason, "
                "support_evidence_ids, needs_review.\n"
                "If the value is not explicit, return an empty value.\n\n"
                f"Evidence:\n{evidence_text}"
            ),
            trace_context={
                "workflow": "llm_field_extraction",
                "field_name": field.name,
                "aliases": field.aliases,
                "source_path": source_path,
                "evidence_bundle_id": evidence_bundle.id,
                "cache_key": cache_key,
            },
        )
        if payload:
            self._save_cache(cache_key, payload)
        return payload

    def _evidence_text(self, evidence_bundle: EvidenceBundle) -> str:
        return "\n\n".join(
            (
                f"[{hit.chunk.id}] score={hit.score:.2f} "
                f"source={hit.chunk.document_path} locator={hit.chunk.source_locator}\n"
                f"{hit.chunk.text[:500]}"
            )
            for hit in evidence_bundle.hits
        )

    def _cache_key(self, prefix: str, payload: dict[str, Any]) -> str:
        digest = hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return f"{prefix}:{digest}"

    def _cache_path(self, cache_key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _load_cache(self, cache_key: str) -> dict[str, Any] | None:
        cache_path = self._cache_path(cache_key)
        if cache_path is None or not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _save_cache(self, cache_key: str, payload: dict[str, Any]) -> None:
        cache_path = self._cache_path(cache_key)
        if cache_path is None:
            return
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
