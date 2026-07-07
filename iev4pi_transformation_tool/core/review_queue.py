"""Review queue for low-confidence extraction and detection results.

Results with confidence < threshold or ``needs_review`` flag are queued here.
The GUI (PyQt) reads from this queue to present items for human approval,
rejection, or correction. Approved corrections are fed back as few-shot
examples to improve future LLM accuracy.

Queue persistence: JSON file at ``.iev4pi/review_queue.json``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass
class ReviewItem:
    """A single item awaiting human review."""

    item_id: str
    category: str  # "field_mapping" | "akz_correspondence" | "uc1_verdict" | "extraction"
    source: str  # document path or identifier
    summary: str  # one-line description for the reviewer
    details: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    suggested_action: str = ""  # proposed resolution
    status: str = "pending"  # pending | approved | rejected | corrected
    reviewer: str = ""
    reviewed_at: str = ""
    correction: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ReviewQueue:
    """Persistent queue of items needing human review.

    Thread-safe for GUI integration (write from extraction pipeline,
    read from PyQt review panel).
    """

    def __init__(self, queue_path: Path | None = None) -> None:
        self._queue_path = queue_path or Path(".iev4pi/review_queue.json")
        self._items: list[ReviewItem] = []
        self._callbacks: list[Callable[[ReviewItem], None]] = []
        self._load()

    @property
    def pending_count(self) -> int:
        return sum(1 for item in self._items if item.status == "pending")

    @property
    def total_count(self) -> int:
        return len(self._items)

    def items(self, status: str | None = None) -> list[ReviewItem]:
        if status is None:
            return list(self._items)
        return [item for item in self._items if item.status == status]

    def add(
        self,
        category: str,
        source: str,
        summary: str,
        *,
        details: dict[str, Any] | None = None,
        confidence: float = 0.0,
        suggested_action: str = "",
    ) -> ReviewItem:
        item = ReviewItem(
            item_id=f"{category}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            category=category,
            source=source,
            summary=summary,
            details=details or {},
            confidence=confidence,
            suggested_action=suggested_action,
        )
        self._items.append(item)
        self._save()
        for cb in self._callbacks:
            try:
                cb(item)
            except Exception:
                pass
        return item

    def approve(self, item_id: str, reviewer: str = "user") -> ReviewItem | None:
        return self._update_status(item_id, "approved", reviewer)

    def reject(self, item_id: str, reviewer: str = "user") -> ReviewItem | None:
        return self._update_status(item_id, "rejected", reviewer)

    def correct(self, item_id: str, correction: dict[str, Any], reviewer: str = "user") -> ReviewItem | None:
        item = self._update_status(item_id, "corrected", reviewer)
        if item:
            item.correction = correction
            self._save()
        return item

    def on_item_added(self, callback: Callable[[ReviewItem], None]) -> None:
        """Register a callback invoked when a new item is added (GUI hook)."""
        self._callbacks.append(callback)

    def clear_approved(self) -> int:
        before = len(self._items)
        self._items = [item for item in self._items if item.status != "approved"]
        removed = before - len(self._items)
        if removed:
            self._save()
        return removed

    def get_few_shot_examples(self, category: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return approved corrections as few-shot examples for LLM prompts."""
        examples: list[dict[str, Any]] = []
        for item in self._items:
            if item.category == category and item.status == "corrected" and item.correction:
                examples.append({
                    "original": item.details,
                    "correction": item.correction,
                    "summary": item.summary,
                })
        return examples[-limit:]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_status(self, item_id: str, status: str, reviewer: str) -> ReviewItem | None:
        for item in self._items:
            if item.item_id == item_id:
                item.status = status
                item.reviewer = reviewer
                item.reviewed_at = datetime.now(timezone.utc).isoformat()
                self._save()
                return item
        return None

    def _load(self) -> None:
        if self._queue_path.is_file():
            try:
                data = json.loads(self._queue_path.read_text(encoding="utf-8"))
                self._items = [
                    ReviewItem(**item) for item in data.get("items", [])
                ]
            except (json.JSONDecodeError, TypeError):
                self._items = []

    def _save(self) -> None:
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "items": [
                {
                    "item_id": item.item_id,
                    "category": item.category,
                    "source": item.source,
                    "summary": item.summary,
                    "details": item.details,
                    "confidence": item.confidence,
                    "suggested_action": item.suggested_action,
                    "status": item.status,
                    "reviewer": item.reviewer,
                    "reviewed_at": item.reviewed_at,
                    "correction": item.correction,
                    "created_at": item.created_at,
                }
                for item in self._items
            ],
        }
        self._queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
