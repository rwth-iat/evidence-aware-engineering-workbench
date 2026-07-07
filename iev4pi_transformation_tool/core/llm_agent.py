"""Unified LLM entry point with deterministic-first guard rails.

Design principle:
    Deterministic rules handle ~80% of cases. LLM is only invoked for the
    remaining ~20% that require semantic understanding (cross-vendor field
    mapping, OCR error disambiguation, edge-case inconsistency judgments).

All LLM calls go through a mandatory cache (input_hash → output, SQLite-backed)
so that the same input always produces the same output — critical for
reproducibility, regression testing, and cost control.

Each method returns a standardised result dataclass with ``confidence``
and ``reasoning`` fields so the review queue can triage low-confidence outputs.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from iev4pi_transformation_tool.core.llm_client import OpenAICompatibleLLMClient
from iev4pi_transformation_tool.core.utils import ensure_dir


# ---------------------------------------------------------------------------
# Pydantic output schemas — every LLM call returns one of these
# ---------------------------------------------------------------------------

class ExtractionResult(BaseModel):
    fields: list[dict[str, Any]] = []
    raw_text: str = ""
    confidence: float = 0.0
    reasoning: str = ""


class FieldMappingResult(BaseModel):
    target_sheet: str = ""
    target_column: str = ""
    attribute_key: str = ""
    confidence: float = 0.0
    reasoning: str = ""


class CorrespondenceJudgment(BaseModel):
    is_same: bool = False
    canonical_form: str = ""
    confidence: float = 0.0
    reasoning: str = ""


class ProfileDraft(BaseModel):
    vendor: str = ""
    doc_type: str = ""
    field_aliases: dict[str, list[str]] = {}
    akz_patterns: list[dict[str, str]] = []
    confidence: float = 0.0
    reasoning: str = ""


class UC1Verdict(BaseModel):
    verdict: str = ""  # "consistent" | "missing_correspondence" | "needs_review"
    missing_in: list[str] = []
    present_in: list[str] = []
    severity: str = ""  # "critical" | "warning" | "info"
    reasoning: str = ""
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    total_calls: int = 0


class LLMCache:
    """SQLite-backed cache keyed on (method, input_hash, model)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.stats = CacheStats()
        self._ensure_table()

    def _ensure_table(self) -> None:
        ensure_dir(self.db_path.parent)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_cache (
                    cache_key TEXT PRIMARY KEY,
                    method TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    tokens_used INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_llm_cache_method
                ON llm_cache(method, input_hash)
            """)

    def _cache_key(self, method: str, input_hash: str, model: str) -> str:
        payload = f"{method}::{input_hash}::{model}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, method: str, input_hash: str, model: str) -> dict[str, Any] | None:
        key = self._cache_key(method, input_hash, model)
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT output_json FROM llm_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        if row:
            self.stats.hits += 1
            return json.loads(row[0])
        self.stats.misses += 1
        return None

    def put(
        self, method: str, input_hash: str, model: str, output: dict[str, Any],
        tokens_used: int = 0,
    ) -> None:
        key = self._cache_key(method, input_hash, model)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    key, method, input_hash, model,
                    json.dumps(output, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                    tokens_used,
                ),
            )

    def clear(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("DELETE FROM llm_cache")


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@dataclass
class LLMAgentConfig:
    model: str = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    vlm_model: str = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    temperature: float = 0.0
    max_retries: int = 2
    cache_enabled: bool = True
    cache_dir: Path = Path(".iev4pi/cache")
    low_confidence_threshold: float = 0.7


class LLMAgent:
    """Unified LLM entry point with caching, schema validation, and confidence.

    All methods that call the LLM:
    1. Hash (method, inputs, model) → check cache → return cached if hit.
    2. Call LLM with ``response_format={"type": "json_object"}``.
    3. Validate output against the corresponding Pydantic schema.
    4. On validation failure, retry once with a stricter prompt.
    5. Store in cache, return result.

    Methods that do NOT call the LLM (deterministic paths) are prefixed
    with ``deterministic_`` and are guaranteed zero-cost / zero-latency.
    """

    def __init__(
        self,
        client: OpenAICompatibleLLMClient,
        config: LLMAgentConfig | None = None,
        *,
        logger: Callable[..., Any] | None = None,
    ) -> None:
        self.client = client
        self.config = config or LLMAgentConfig()
        self._logger = logger
        self._cache = LLMCache(self.config.cache_dir / "llm_agent.db")

    # ------------------------------------------------------------------
    # Extraction (LLM required — PDFs are unstructured)
    # ------------------------------------------------------------------

    def extract_fields(
        self,
        document_text: str,
        profile: dict[str, Any],
        target_columns: list[str],
        *,
        use_vlm: bool = False,
        image_b64: str | None = None,
    ) -> ExtractionResult:
        """Extract structured fields from unstructured document text.

        This IS an LLM-required path — OCR output from PDFs has no
        guaranteed structure. Uses the active vendor profile's field_aliases
        to guide extraction.
        """
        profile_yaml = json.dumps(profile, ensure_ascii=False)
        columns_yaml = json.dumps(target_columns, ensure_ascii=False)

        system_prompt = (
            "You are an industrial engineering document parser. "
            "Extract all measurement/control device fields from the document. "
            "Output valid JSON matching this schema: "
            '{"fields": [{"key": "...", "value": "...", "unit": "...", '
            '"source_text": "...", "confidence": 0.0}], '
            '"confidence": 0.0, "reasoning": "..."}'
            "\n\nVendor profile (field aliases):\n" + profile_yaml +
            "\n\nTarget columns:\n" + columns_yaml
        )

        user_prompt = f"Document text:\n\n{document_text[:16000]}"

        if image_b64 and use_vlm:
            user_prompt = (
                f"Analyze this engineering document image and extract all fields.\n"
                f"Vendor profile: {profile_yaml}\n"
                f"Target columns: {columns_yaml}"
            )

        result = self._call_llm(
            method="extract_fields",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=ExtractionResult,
            image_b64=image_b64 if use_vlm else None,
        )
        return result

    # ------------------------------------------------------------------
    # Field-to-template mapping (LLM only for unknown fields)
    # ------------------------------------------------------------------

    def map_field_to_template(
        self,
        field_name: str,
        field_value: str,
        field_unit: str | None,
        profile: dict[str, Any],
        column_semantics: dict[str, Any],
    ) -> FieldMappingResult:
        """Map an extracted field to a template column.

        Deterministic path first: check profile.field_aliases for exact match.
        Only calls LLM if no alias hits.
        """
        # Deterministic: check profile field_aliases
        aliases = profile.get("field_aliases", {})
        for canonical_col, alias_list in aliases.items():
            if field_name.lower() in {a.lower() for a in alias_list}:
                sheet = self._infer_sheet_from_column(canonical_col, column_semantics)
                return FieldMappingResult(
                    target_sheet=sheet,
                    target_column=canonical_col,
                    attribute_key=field_name,
                    confidence=0.95,
                    reasoning=f"profile alias match: {field_name} → {canonical_col}",
                )

        # LLM fallback — semantic matching against column descriptions
        semantics_yaml = json.dumps(column_semantics, ensure_ascii=False)
        system_prompt = (
            "You are a schema matching assistant for industrial engineering data. "
            "Given an extracted field (name, value, unit) and a set of target "
            "template columns with semantic descriptions, determine which "
            "column the field should map to.\n\n"
            "Output JSON: "
            '{"target_sheet": "...", "target_column": "...", '
            '"attribute_key": "...", "confidence": 0.0, '
            '"reasoning": "..."}\n\n'
            "Template column semantics:\n" + semantics_yaml
        )
        user_prompt = (
            f"Field to map:\n"
            f"  name: {field_name}\n"
            f"  value: {field_value}\n"
            f"  unit: {field_unit or 'N/A'}\n"
            f"  vendor: {profile.get('vendor', 'unknown')}"
        )

        return self._call_llm(
            method="map_field_to_template",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=FieldMappingResult,
        )

    def _infer_sheet_from_column(
        self, canonical_column: str, column_semantics: dict[str, Any]
    ) -> str:
        templates = column_semantics.get("templates", {})
        for tmpl_name, tmpl in templates.items():
            for sheet_name, sheet_def in tmpl.get("sheets", {}).items():
                if canonical_column in sheet_def.get("columns", {}):
                    return sheet_name
        return ""

    # ------------------------------------------------------------------
    # AKZ correspondence (LLM only for ambiguous fuzzy matches)
    # ------------------------------------------------------------------

    def judge_akz_correspondence(
        self,
        akz_a: str,
        akz_b: str,
        context_a: dict[str, Any] | None = None,
        context_b: dict[str, Any] | None = None,
    ) -> CorrespondenceJudgment:
        """LLM-based judgment of whether two AKZ strings refer to the same entity.

        ONLY call this when deterministic fuzzy matching (edit distance ≤ 2)
        has produced a candidate but confidence is not 100%.
        """
        ctx_a = json.dumps(context_a or {}, ensure_ascii=False)
        ctx_b = json.dumps(context_b or {}, ensure_ascii=False)

        system_prompt = (
            "You are an expert in industrial plant engineering tag identification "
            "(AKZ / Anlagenkennzeichen per DIN 19227-2 / IEC 81346). "
            "Two strings are presented that may refer to the same equipment. "
            "Consider: (1) OCR errors — 'T41' vs 'TA1' with similar context suggest same; "
            "(2) separator variants — 'TU10.T41' = 'TU10-T41' = 'TU10T41'; "
            "(3) prefix stripping — some documents omit the function letter prefix; "
            "(4) surrounding fields — same manufacturer/model/description → higher confidence.\n\n"
            "Output JSON: "
            '{"is_same": true/false, "canonical_form": "...", '
            '"confidence": 0.0, "reasoning": "..."}'
        )
        user_prompt = (
            f"AKZ A: {akz_a}\nContext A: {ctx_a}\n\n"
            f"AKZ B: {akz_b}\nContext B: {ctx_b}"
        )

        return self._call_llm(
            method="judge_akz_correspondence",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=CorrespondenceJudgment,
        )

    # ------------------------------------------------------------------
    # UC1 inconsistency judgment (LLM only for edge cases)
    # ------------------------------------------------------------------

    def judge_uc1_inconsistency(
        self,
        canonical_akz: str,
        occurrences: dict[str, list[dict[str, Any]]],
        rule_set: dict[str, Any],
    ) -> UC1Verdict:
        """LLM-based UC1 inconsistency judgment.

        ONLY call when deterministic cardinality rules produce an ambiguous
        result (e.g. one document missing but AKZ may be a merged entry).
        """
        occ_json = json.dumps(occurrences, ensure_ascii=False)
        rules_json = json.dumps(rule_set, ensure_ascii=False)

        system_prompt = (
            "You are an engineering consistency checker for process plant documentation. "
            "Given a PLT-Stelle (identified by its AKZ) and its occurrence map "
            "across multiple engineering documents, together with cardinality rules, "
            "determine whether there is an inconsistency (missing correspondence).\n\n"
            "Rules from slide 30 of the project spec:\n"
            "- Each PLT-Stelle in the R&I P&ID must appear at least once in Stellenplan.\n"
            "- Each PLT-Stelle in the R&I P&ID must appear at least once in Verschaltungsliste.\n"
            "- May optionally appear in Datasheet and 3D data.\n\n"
            "Output JSON: "
            '{"verdict": "consistent|missing_correspondence|needs_review", '
            '"missing_in": [...], "present_in": [...], '
            '"severity": "critical|warning|info", '
            '"reasoning": "...", "confidence": 0.0}'
        )
        user_prompt = (
            f"Canonical AKZ: {canonical_akz}\n\n"
            f"Occurrences by document:\n{occ_json}\n\n"
            f"Cardinality rules:\n{rules_json}"
        )

        return self._call_llm(
            method="judge_uc1_inconsistency",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=UC1Verdict,
        )

    # ------------------------------------------------------------------
    # Profile generation (LLM required — one-time, offline)
    # ------------------------------------------------------------------

    def generate_profile(
        self,
        sample_texts: list[str],
        doc_type: str,
        vendor_hint: str | None,
        column_semantics: dict[str, Any],
        existing_profile: dict[str, Any] | None = None,
    ) -> ProfileDraft:
        """Generate a per-vendor extraction profile from sample documents.

        This IS LLM-required — it's a one-time, offline operation that
        analyzes sample documents and produces a YAML profile mapping
        vendor-specific field names to standardized template columns.

        The LLM receives the template column semantics (hand-written) as
        the target schema and maps the vendor's fields to them.
        """
        semantics_yaml = json.dumps(column_semantics, ensure_ascii=False)
        existing_yaml = json.dumps(existing_profile or {}, ensure_ascii=False)
        samples_combined = "\n\n--- NEXT SAMPLE ---\n\n".join(
            text[:4000] for text in sample_texts[:5]
        )

        system_prompt = (
            "You are an industrial document analyst specialized in cross-vendor "
            "field mapping for process plant engineering. "
            "Given sample document texts from a specific vendor, map every "
            "vendor-specific field name to the standardized template columns "
            "described below.\n\n"
            "Template column semantics:\n" + semantics_yaml + "\n\n"
            "Existing default profile (use as base, extend with new aliases):\n" + existing_yaml + "\n\n"
            "Output JSON:\n"
            '{"vendor": "...", "doc_type": "...", '
            '"field_aliases": {"canonical_column": ["vendor_alias1", ...]}, '
            '"akz_patterns": [{"regex": "...", "description": "..."}], '
            '"confidence": 0.0, "reasoning": "..."}\n\n'
            "IMPORTANT: field_aliases keys MUST be canonical column names from "
            "the template column semantics above. Do NOT invent new column names. "
            "Values are lists of vendor-specific field name variants."
        )
        user_prompt = (
            f"Vendor: {vendor_hint or 'unknown'}\n"
            f"Document type: {doc_type}\n\n"
            f"Sample document texts:\n{samples_combined}"
        )

        return self._call_llm(
            method="generate_profile",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=ProfileDraft,
        )

    # ------------------------------------------------------------------
    # Internal: LLM call with cache + validation + retry
    # ------------------------------------------------------------------

    def _call_llm(
        self,
        method: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        image_b64: str | None = None,
    ) -> Any:
        input_payload = {
            "method": method,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "image_b64": image_b64,
        }
        input_hash = hashlib.sha256(
            json.dumps(input_payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

        model = self.config.vlm_model if image_b64 else self.config.model

        # Cache lookup
        if self.config.cache_enabled:
            cached = self._cache.get(method, input_hash, model)
            if cached:
                try:
                    return schema(**cached)
                except ValidationError:
                    pass  # stale cache with schema change — recompute

        # LLM call
        if image_b64 and hasattr(self.client, 'chat_json_messages'):
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ]},
            ]
            raw = self.client.chat_json_messages(messages, model=model)
        else:
            raw = self.client.chat_json(system_prompt, user_prompt)

        self._cache.stats.total_calls += 1

        # Validate
        for attempt in range(self.config.max_retries + 1):
            try:
                result = schema(**raw)
                break
            except ValidationError:
                if attempt < self.config.max_retries:
                    # Retry with stricter prompt
                    strict_prompt = (
                        system_prompt + "\n\nCRITICAL: Output MUST be valid JSON "
                        f"matching the schema exactly. Previous attempt failed validation."
                    )
                    raw = self.client.chat_json(strict_prompt, user_prompt)
                else:
                    # Return empty/default result
                    result = schema()

        # Cache
        if self.config.cache_enabled:
            self._cache.put(method, input_hash, model, result.model_dump())

        return result

    @property
    def cache_stats(self) -> CacheStats:
        return self._cache.stats
