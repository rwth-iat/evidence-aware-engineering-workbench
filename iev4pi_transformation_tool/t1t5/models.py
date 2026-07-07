from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


ALLOWED_T1T5_NODE_TYPES = {
    "BuiltinContext",
    "WorkbookSheet",
    "HeaderMatch",
    "RowIterator",
    "CellValue",
    "Constant",
    "NormalizeIdentifier",
    "RegexExtract",
    "Concat",
    "Condition",
    "LookupMap",
    "StrictMatch",
    "ResolverMatch",
    "MissingPlaceholder",
    "CompletionMerge",
    "RelationBuild",
    "BuildRow",
    "OutputSheet",
}


class WorkbookSignature(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workbook_kind: str = ""
    sheet_name: str = ""
    required_headers: list[str] = Field(default_factory=list)
    optional_headers: list[str] = Field(default_factory=list)
    header_fingerprint: str = ""
    source_root: str = ""


class T1T5Node(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    node_type: str
    label: str = ""
    position: tuple[float, float] = (0.0, 0.0)
    config: dict[str, Any] = Field(default_factory=dict)


class T1T5Edge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    from_node: str
    to_node: str
    source_port: str = "out"
    target_port: str = "in"
    order: int = 0


class T1T5ValidationIssue(BaseModel):
    severity: str = "error"
    code: str
    message: str
    node_id: str = ""
    edge_id: str = ""


class T1T5ProfileMatch(BaseModel):
    profile_id: str = ""
    score: float = 0.0
    matched_sheet_name: str = ""
    matched_headers: list[str] = Field(default_factory=list)
    reason: str = ""


class T1T5RuleProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage_id: str
    profile_id: str
    title: str = ""
    description: str = ""
    enabled: bool = True
    priority: int = 100
    input_mode: str = "builtin_context"
    workbook_signature: WorkbookSignature = Field(default_factory=WorkbookSignature)
    output_sheet_name: str = ""
    output_fields: list[str] = Field(default_factory=list)
    nodes: list[T1T5Node] = Field(default_factory=list)
    edges: list[T1T5Edge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class T1T5RuleBundle(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage_id: str
    version: int = 1
    title: str = ""
    description: str = ""
    default_profile_id: str = ""
    profiles: list[T1T5RuleProfile] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class T1T5PreviewResult(BaseModel):
    bundle: T1T5RuleBundle
    selected_profile_id: str = ""
    profile_match: T1T5ProfileMatch | None = None
    output_rows: list[dict[str, str]] = Field(default_factory=list)
    issues: list[T1T5ValidationIssue] = Field(default_factory=list)
