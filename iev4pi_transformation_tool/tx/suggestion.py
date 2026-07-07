from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from iev4pi_transformation_tool.core.llm_client import OpenAICompatibleLLMClient
from iev4pi_transformation_tool.core.utils import clean_cell
from iev4pi_transformation_tool.tx.defaults import build_default_uc1_rule_set
from iev4pi_transformation_tool.tx.engine import TxExecutor
from iev4pi_transformation_tool.tx.models import (
    ALLOWED_TX_NODE_TYPES,
    TxRuleSet,
    TxSuggestionResult,
    TxValidationIssue,
)


class TxRuleSuggester:
    def __init__(self, llm_client: OpenAICompatibleLLMClient | None = None) -> None:
        self.llm_client = llm_client
        self.executor = TxExecutor()

    def suggest(
        self,
        source_type: str,
        workbook_path: Path,
        *,
        target_properties: dict[str, list[str]] | None = None,
    ) -> TxSuggestionResult:
        default_rule_set = build_default_uc1_rule_set(source_type)
        if self.llm_client is None or not self.llm_client.available():
            return TxSuggestionResult(
                suggested_rule_set=default_rule_set,
                fallback_used=True,
                prompt_summary="LLM backend unavailable. Returned the built-in deterministic UC1 rule set.",
            )

        workbook_overview = self._workbook_overview(workbook_path)
        prompt_summary = (
            f"Source type: {source_type}\n"
            f"Workbook: {workbook_path.name}\n"
            f"Sheets and sample columns: {json.dumps(workbook_overview, ensure_ascii=False)}\n"
            f"Target properties: {json.dumps(target_properties or {}, ensure_ascii=False)}"
        )
        system_prompt = (
            "You design a deterministic low-code transformation rule graph.\n"
            "Return JSON with a single `rule_set` object compatible with the provided schema.\n"
            "Use only the allowed node types and never emit arbitrary code.\n"
            f"Allowed node types: {', '.join(sorted(ALLOWED_TX_NODE_TYPES))}."
        )
        user_prompt = (
            f"{prompt_summary}\n\n"
            "Produce a concise rule graph that maps workbook columns into UC1 AAS properties.\n"
            "The graph must be valid, acyclic, and conservative.\n"
            "If information is missing, leave the value empty instead of inventing it."
        )
        try:
            payload = self.llm_client.chat_json(
                system_prompt,
                user_prompt,
                trace_context={
                    "workflow": "tx_rule_suggestion",
                    "source_type": source_type,
                    "workbook_path": str(workbook_path),
                    "target_properties": target_properties or {},
                },
            )
            suggested = TxRuleSet.model_validate(payload.get("rule_set", {}))
            issues = self.executor.validate(suggested)
            if any(issue.severity == "error" for issue in issues):
                return TxSuggestionResult(
                    suggested_rule_set=default_rule_set,
                    fallback_used=True,
                    issues=issues,
                    prompt_summary=prompt_summary,
                )
            return TxSuggestionResult(
                suggested_rule_set=suggested,
                fallback_used=False,
                issues=issues,
                prompt_summary=prompt_summary,
            )
        except Exception as exc:
            return TxSuggestionResult(
                suggested_rule_set=default_rule_set,
                fallback_used=True,
                issues=[TxValidationIssue(code="llm_suggestion_failed", message=str(exc), severity="warning")],
                prompt_summary=prompt_summary,
            )

    def _workbook_overview(self, workbook_path: Path) -> dict[str, dict[str, object]]:
        overview: dict[str, dict[str, object]] = {}
        with pd.ExcelFile(workbook_path) as workbook:
            for sheet_name in workbook.sheet_names:
                frame = workbook.parse(sheet_name, dtype=object).fillna("")
                columns = [clean_cell(column) for column in frame.columns]
                sample_rows = frame.astype(str).replace({"nan": ""}).to_dict(orient="records")[:2]
                overview[sheet_name] = {
                    "columns": columns,
                    "sample_rows": sample_rows,
                }
        return overview
