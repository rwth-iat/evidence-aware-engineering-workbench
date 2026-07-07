"""Dual-track extraction: OCR ensemble + VLM parallel paths with field-level voting.

Path A (OCR): OCR ensemble → text → LLM structured extraction.
Path B (VLM): VLM directly sees the document image → structured JSON output.

The two paths vote at the field level:
  - Both agree → high confidence, auto-accept.
  - Disagree → take the higher-confidence path, mark needs_review.
  - Only one path succeeds → medium confidence.

This architecture is particularly valuable for PDFs with complex table
layouts where OCR may misread cell boundaries but VLM understands the
visual structure — and for dense text where VLM may hallucinate but
OCR reads correctly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DualTrackResult:
    """Output of a dual-track extraction pass."""

    fields: list[dict[str, Any]] = field(default_factory=list)
    path_a_fields: list[dict[str, Any]] = field(default_factory=list)
    path_b_fields: list[dict[str, Any]] = field(default_factory=list)
    voting_results: list[dict[str, Any]] = field(default_factory=list)
    overall_confidence: float = 0.0
    agreement_ratio: float = 0.0  # fields where both paths agree / total fields
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


def vote_fields(
    path_a: list[dict[str, Any]],
    path_b: list[dict[str, Any]],
    *,
    key_field: str = "key",
    value_field: str = "value",
    min_confidence: float = 0.7,
) -> DualTrackResult:
    """Merge two extraction paths with field-level voting.

    Args:
        path_a: Fields from OCR + LLM path.
        path_b: Fields from VLM path.
        key_field: Dict key for the field name.
        value_field: Dict key for the field value.
        min_confidence: Below this, flag for review even if paths agree.

    Returns:
        DualTrackResult with merged fields and voting metadata.
    """
    # Build lookup by normalized key.
    def _norm_key(field: dict[str, Any]) -> str:
        return str(field.get(key_field, "")).strip().lower().replace(" ", "_")

    a_by_key = {_norm_key(f): f for f in path_a}
    b_by_key = {_norm_key(f): f for f in path_b}

    all_keys = set(a_by_key.keys()) | set(b_by_key.keys())

    merged: list[dict[str, Any]] = []
    voting_log: list[dict[str, Any]] = []
    agreements = 0
    review_reasons: list[str] = []

    for key in sorted(all_keys):
        a_field = a_by_key.get(key)
        b_field = b_by_key.get(key)

        if a_field and b_field:
            # Both paths found this field.
            a_val = str(a_field.get(value_field, "")).strip()
            b_val = str(b_field.get(value_field, "")).strip()
            a_conf = float(a_field.get("confidence", 0.5))
            b_conf = float(b_field.get("confidence", 0.5))

            if a_val.lower() == b_val.lower():
                # Agreement.
                agreements += 1
                merged_conf = max(a_conf, b_conf)
                winner = a_field if a_conf >= b_conf else b_field
                merged.append({**winner, "confidence": merged_conf, "voting": "agreed"})
                voting_log.append({
                    "key": key,
                    "verdict": "agreed",
                    "a_value": a_val,
                    "b_value": b_val,
                    "confidence": merged_conf,
                })
                if merged_conf < min_confidence:
                    review_reasons.append(f"Low confidence ({merged_conf:.2f}) for agreed field '{key}'")
            else:
                # Disagreement — take higher confidence.
                winner = a_field if a_conf >= b_conf else b_field
                loser = b_field if a_conf >= b_conf else a_field
                merged.append({**winner, "voting": "disagreed", "alternate_value": str(loser.get(value_field, ""))})
                voting_log.append({
                    "key": key,
                    "verdict": "disagreed",
                    "a_value": a_val,
                    "b_value": b_val,
                    "confidence": float(winner.get("confidence", 0.5)),
                    "chosen": "path_a" if a_conf >= b_conf else "path_b",
                })
                review_reasons.append(f"Disagreement on '{key}': A='{a_val}' vs B='{b_val}'")
        elif a_field:
            merged.append({**a_field, "voting": "path_a_only"})
            voting_log.append({"key": key, "verdict": "path_a_only", "confidence": float(a_field.get("confidence", 0.5))})
        else:
            merged.append({**b_field, "voting": "path_b_only"})
            voting_log.append({"key": key, "verdict": "path_b_only", "confidence": float(b_field.get("confidence", 0.5))})

    total = len(all_keys)
    agreement_ratio = agreements / max(1, total)
    overall_conf = (
        sum(f.get("confidence", 0.5) for f in merged) / max(1, len(merged))
    )

    return DualTrackResult(
        fields=merged,
        path_a_fields=path_a,
        path_b_fields=path_b,
        voting_results=voting_log,
        overall_confidence=round(overall_conf, 4),
        agreement_ratio=round(agreement_ratio, 4),
        needs_review=len(review_reasons) > 0 or agreement_ratio < 0.7,
        review_reasons=review_reasons,
    )
