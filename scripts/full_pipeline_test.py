#!/usr/bin/env python3
"""Full extraction → fill → export pipeline test.

Runs the complete fill_standardized_templates() flow with fresh
schema generation and extraction, then saves results to Exports/Excel.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime

REPO = Path(__file__).resolve().parents[1]

from iev4pi_transformation_tool.services.workbench import Workbench

print(f"=== Full Pipeline Test ===")
print(f"Start: {datetime.now().strftime('%H:%M:%S')}")
print(f"Repo: {REPO}")

wb = Workbench(REPO)

# Clear cached schemas to force regeneration with new fields
from iev4pi_transformation_tool.core.schema_miner import DEFAULT_TU_FIELDS
print(f"\nSchema fields available: {len(DEFAULT_TU_FIELDS)} (including {len(DEFAULT_TU_FIELDS)-10} new title-block fields)")

print("\nRunning fill_standardized_templates()...")
summary = wb.fill_standardized_templates()

print(f"\nExtraction complete:")
print(f"  Records: {len(wb.records)}")
print(f"  Run ID: {summary.run_id if hasattr(summary, 'run_id') else 'N/A'}")

# Save to Exports/Excel
print("\nSaving to Exports/Excel...")
result = wb.save_extraction_results()
for k, v in result.items():
    print(f"  {k}: {v}")

print(f"\nDone at {datetime.now().strftime('%H:%M:%S')}")
