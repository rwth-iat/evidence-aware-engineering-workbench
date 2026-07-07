from __future__ import annotations

import json
from pathlib import Path

from iev4pi_transformation_tool.core.utils import ensure_dir, normalize_identifier
from iev4pi_transformation_tool.t1t5.models import T1T5RuleBundle


class T1T5RuleStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.root_dir = ensure_dir(self.state_dir / "t1_t5_rules")

    def default_path(self, stage_id: str) -> Path:
        normalized = normalize_identifier(stage_id) or "t1"
        return self.root_dir / f"{normalized}.json"

    def load(self, *, stage_id: str = "", rule_path: Path | None = None) -> T1T5RuleBundle:
        resolved = Path(rule_path) if rule_path is not None else self.default_path(stage_id)
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        return T1T5RuleBundle.model_validate(payload)

    def load_if_available(self, *, stage_id: str = "", rule_path: Path | None = None) -> T1T5RuleBundle | None:
        resolved = Path(rule_path) if rule_path is not None else self.default_path(stage_id)
        if not resolved.exists():
            return None
        return self.load(stage_id=stage_id, rule_path=resolved)

    def exists(self, *, stage_id: str = "", rule_path: Path | None = None) -> bool:
        resolved = Path(rule_path) if rule_path is not None else self.default_path(stage_id)
        return resolved.exists()

    def save(self, bundle: T1T5RuleBundle, *, rule_path: Path | None = None) -> Path:
        resolved = Path(rule_path) if rule_path is not None else self.default_path(bundle.stage_id)
        ensure_dir(resolved.parent)
        resolved.write_text(json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        return resolved
