from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


ALLOWED_TX_NODE_TYPES = {
    "InputColumn",
    "Constant",
    "NormalizeIdentifier",
    "RegexExtract",
    "MapEnum",
    "BoolMap",
    "Concat",
    "PreferFirstNonEmpty",
    "Condition",
    "ConfidenceGate",
    "OutputProperty",
    "OutputSubmodel",
}


class TxNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    node_type: str
    label: str = ""
    position: tuple[float, float] = (0.0, 0.0)
    config: dict[str, Any] = Field(default_factory=dict)


class TxEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    from_node: str
    to_node: str
    source_port: str = "out"
    target_port: str = "in"
    order: int = 0


class TxValidationIssue(BaseModel):
    severity: str = "error"
    code: str
    message: str
    node_id: str = ""
    edge_id: str = ""


class TxTraceStep(BaseModel):
    node_id: str
    node_type: str
    label: str = ""
    summary: str = ""
    value: str = ""


class TxExecutionTrace(BaseModel):
    source_type: str
    identity_key: str = ""
    submodel_id_short: str = ""
    output_property: str
    value: str = ""
    gate_passed: bool = True
    steps: list[TxTraceStep] = Field(default_factory=list)


class TxRuleSet(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_type: str
    version: int = 1
    title: str = ""
    description: str = ""
    workbook_kind: str = ""
    primary_sheet_name: str = ""
    identity_fields: list[str] = Field(default_factory=list)
    nodes: list[TxNode] = Field(default_factory=list)
    edges: list[TxEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TxPreviewResult(BaseModel):
    rule_set: TxRuleSet
    identity_key: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    traces: list[TxExecutionTrace] = Field(default_factory=list)
    issues: list[TxValidationIssue] = Field(default_factory=list)


class TxSuggestionResult(BaseModel):
    suggested_rule_set: TxRuleSet
    fallback_used: bool = False
    issues: list[TxValidationIssue] = Field(default_factory=list)
    prompt_summary: str = ""
