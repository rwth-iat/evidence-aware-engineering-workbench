"""Per-vendor profile generator — LLM-assisted drafting with human review.

Produces a YAML profile that maps a vendor's field naming conventions to
standardised template columns. The LLM is fed:

1. Sample document texts (OCR output) from the vendor.
2. The template column semantics YAML (hand-written, one-time).
3. The existing default profile as a base (to extend, not rewrite).

The output is a draft profile written to ``profiles/_drafts/``. After human
review and approval, it moves to ``profiles/`` for production use.

Usage:
    $ python -m iev4pi_transformation_tool.core.profile_generator \\
          --vendor SAMSON --doc-type Datasheet \\
          --samples Documents-Others/datasheet/samson_*.pdf \\
          --out profiles/_drafts/samson__datasheet.yaml
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

import yaml

from iev4pi_transformation_tool.core.llm_agent import LLMAgent, ProfileDraft


REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO_ROOT / "profiles"
DRAFTS_DIR = PROFILES_DIR / "_drafts"
SCHEMA_DIR = PROFILES_DIR / "_schema"

COLUMN_SEMANTICS_PATH = SCHEMA_DIR / "template_column_semantics.yaml"


def load_column_semantics() -> dict[str, Any]:
    with open(COLUMN_SEMANTICS_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_existing_profile(vendor: str, doc_type: str) -> dict[str, Any] | None:
    """Load the best-matching existing profile."""
    candidates = [
        PROFILES_DIR / f"{vendor}__{doc_type.lower()}.yaml",
        PROFILES_DIR / f"default__{doc_type.lower()}.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            with open(candidate, encoding="utf-8") as fh:
                return yaml.safe_load(fh)
    return None


def generate_profile(
    llm_agent: LLMAgent,
    sample_texts: list[str],
    vendor: str,
    doc_type: str,
    *,
    column_semantics: dict[str, Any] | None = None,
    existing_profile: dict[str, Any] | None = None,
) -> ProfileDraft:
    """Run LLM-assisted profile generation.

    Returns a ProfileDraft pydantic model with field_aliases, akz_patterns,
    etc. The caller is responsible for writing the result to a YAML file
    and presenting it for human review.
    """
    semantics = column_semantics or load_column_semantics()
    existing = existing_profile or load_existing_profile(vendor, doc_type)

    return llm_agent.generate_profile(
        sample_texts=sample_texts,
        doc_type=doc_type,
        vendor_hint=vendor,
        column_semantics=semantics,
        existing_profile=existing,
    )


def write_profile_draft(
    draft: ProfileDraft,
    output_path: Path,
) -> None:
    """Persist a ProfileDraft to a YAML file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "vendor": draft.vendor,
        "doc_type": draft.doc_type,
        "description": f"Auto-generated profile for {draft.vendor} {draft.doc_type} — needs review",
        "field_aliases": draft.field_aliases,
        "akz_patterns": [
            {"regex": p["regex"], "description": p.get("description", "")}
            for p in draft.akz_patterns
        ],
        "_generated_confidence": draft.confidence,
        "_generated_reasoning": draft.reasoning,
    }

    with open(output_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)


def diff_with_existing(
    draft: ProfileDraft,
    existing_profile: dict[str, Any],
) -> str:
    """Generate a human-readable diff between draft and existing profile."""
    existing_yaml = yaml.dump(existing_profile, allow_unicode=True, sort_keys=True)
    draft_yaml = yaml.dump(
        {
            "vendor": draft.vendor,
            "doc_type": draft.doc_type,
            "field_aliases": draft.field_aliases,
            "akz_patterns": [
                {"regex": p["regex"], "description": p.get("description", "")}
                for p in draft.akz_patterns
            ],
        },
        allow_unicode=True,
        sort_keys=True,
    )

    diff_lines = difflib.unified_diff(
        existing_yaml.splitlines(keepends=True),
        draft_yaml.splitlines(keepends=True),
        fromfile="existing_profile",
        tofile=f"draft_{draft.vendor}",
    )
    return "".join(diff_lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate a per-vendor profile draft")
    parser.add_argument("--vendor", required=True, help="Vendor name (e.g. SAMSON)")
    parser.add_argument("--doc-type", required=True, help="Document type (e.g. Datasheet)")
    parser.add_argument("--samples", nargs="+", required=True, help="Sample document paths")
    parser.add_argument("--out", required=True, help="Output YAML path")
    parser.add_argument("--base-profile", help="Base profile path (optional)")
    args = parser.parse_args()

    # Read sample texts
    sample_texts: list[str] = []
    for sample_path in args.samples:
        p = Path(sample_path)
        if p.suffix.lower() in {".txt", ".md"}:
            sample_texts.append(p.read_text(encoding="utf-8"))
        else:
            sample_texts.append(f"[File: {p.name} — binary, needs OCR preprocessing]")

    column_semantics = load_column_semantics()

    existing = None
    if args.base_profile:
        with open(args.base_profile, encoding="utf-8") as fh:
            existing = yaml.safe_load(fh)
    else:
        existing = load_existing_profile(args.vendor, args.doc_type)

    # This requires a configured LLM client; in practice called from GUI.
    print(f"Profile generation for {args.vendor} ({args.doc_type})")
    print(f"  Samples: {len(sample_texts)}")
    print(f"  Output: {args.out}")
    print("  (LLM client must be configured in settings)")
    print("  Run from the GUI or wire up llm_client before calling generate_profile().")


if __name__ == "__main__":
    main()
