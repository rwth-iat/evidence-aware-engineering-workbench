#!/usr/bin/env python3
"""Headless re-run of template filling + enrichment using cached extraction records.

Uses ``Workbench.fill_standardized_templates()`` — the same code path as the
GUI extraction pipeline — so there is no duplication of template-filling logic.

Usage: python scripts/regenerate_exports.py
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

from iev4pi_transformation_tool.services.workbench import Workbench

print("Initializing Workbench...")
wb = Workbench(REPO)

print("Scanning workspace...")
if not wb.documents:
    wb.scan()

print("Loading schemas from cache...")
wb.reload_schemas()
if not wb.schemas and not wb.ri_bundle_schemas:
    print("  No cached schemas, generating...")
    wb.generate_schemas(prune_blank_fields=False)

print("Loading records from database...")
wb.reload_records()
print(f"  {len(wb.records)} records loaded")

# ---- Fill standardized templates (same path as GUI) ----
print("\nFilling standardized templates...")
summary = wb.fill_standardized_templates()
print(f"  Status: {summary.status}, {summary.record_count} records in {len(summary.family_counts)} families")

# ---- Save to Exports/Excel ----
# fill_standardized_templates() already runs enrichment internally, so the
# filled templates in data/filled_templates/ are enriched.  save_extraction_results()
# also fills the Stellenplan (instrument list) from datasheet records before
# copying everything to Exports/Excel/{category}/.
print("\nSaving to Exports/Excel...")
try:
    result = wb.save_extraction_results()
    print(f"  Saved: {result}")
except Exception as e:
    print(f"  Save error: {e}")

print("\nDone. You can now run the audits to verify.")
