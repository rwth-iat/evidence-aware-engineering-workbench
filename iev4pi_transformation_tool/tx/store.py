from __future__ import annotations

import json
from pathlib import Path

from iev4pi_transformation_tool.core.utils import clean_cell, ensure_dir, normalize_identifier
from iev4pi_transformation_tool.tx.models import TxRuleSet


class TxRuleStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.root_dir = ensure_dir(self.state_dir / "tx_rules")

    def list_rule_paths(self) -> dict[str, list[Path]]:
        grouped: dict[str, list[Path]] = {}
        for path in sorted(self.root_dir.glob("*.json")):
            source_type = normalize_identifier(path.stem.split("__", 1)[0]) or path.stem
            grouped.setdefault(source_type, []).append(path)
        return grouped

    def default_path(self, source_type: str) -> Path:
        normalized = normalize_identifier(source_type) or "tx"
        return self.root_dir / f"{normalized}.json"

    def path_for(self, source_type: str, rule_set_id: str = "") -> Path:
        normalized_source = normalize_identifier(source_type) or "tx"
        normalized_rule_set_id = normalize_identifier(rule_set_id)
        if normalized_rule_set_id:
            return self.root_dir / f"{normalized_source}__{normalized_rule_set_id}.json"
        return self.default_path(source_type)

    def load(
        self,
        *,
        source_type: str = "",
        rule_path: Path | None = None,
        rule_set_id: str = "",
    ) -> TxRuleSet:
        resolved = Path(rule_path) if rule_path is not None else self.path_for(source_type, rule_set_id)
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        return TxRuleSet.model_validate(payload)

    def exists(self, *, source_type: str = "", rule_path: Path | None = None, rule_set_id: str = "") -> bool:
        resolved = Path(rule_path) if rule_path is not None else self.path_for(source_type, rule_set_id)
        return resolved.exists()

    def save(self, rule_set: TxRuleSet, *, rule_path: Path | None = None, rule_set_id: str = "") -> Path:
        resolved = Path(rule_path) if rule_path is not None else self.path_for(rule_set.source_type, rule_set_id)
        ensure_dir(resolved.parent)
        resolved.write_text(json.dumps(rule_set.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        return resolved

    def load_if_available(
        self,
        *,
        source_type: str = "",
        rule_path: Path | None = None,
        rule_set_id: str = "",
    ) -> TxRuleSet | None:
        resolved = Path(rule_path) if rule_path is not None else self.path_for(source_type, rule_set_id)
        if not resolved.exists():
            return None
        return self.load(source_type=source_type, rule_path=resolved, rule_set_id=rule_set_id)

    def export_bundle_metadata(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for source_type, paths in self.list_rule_paths().items():
            for path in paths:
                rows.append(
                    {
                        "source_type": source_type,
                        "rule_path": str(path),
                        "rule_name": clean_cell(path.stem),
                    }
                )
        return rows
