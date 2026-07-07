#!/usr/bin/env python3
"""Prepare state and audit Excel transformation quality.

This script does NOT run the full extraction pipeline (that requires the
GUI or main.py).  It prepares the environment (fresh state, input dirs,
source manifest) and runs the audit.  For full regeneration, run the
extraction pipeline first, then use --audit-only here.

Usage:
  # Prepare state and audit
  python scripts/prepare_and_audit_excel.py \\
    --fresh-state \\
    --input-dirs Documents Documents-Others \\
    --fail-on key,header,empty-required,stale-data

  # Audit-only mode (no state changes)
  python scripts/prepare_and_audit_excel.py --audit-only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETTINGS_PATH = REPO / "Exports" / "settings.json"
DB_PATH = REPO / ".iev4pi" / "state.sqlite"
AUDIT_DIR = REPO / "Exports" / "audit"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_db_locked() -> bool:
    """Check whether .iev4pi/state.sqlite is locked by another process."""
    if not DB_PATH.is_file():
        return False
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=1)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        conn.close()
        return False
    except sqlite3.OperationalError:
        return True


def _backup_db() -> Path | None:
    """Create a timestamped backup of state.sqlite."""
    if not DB_PATH.is_file():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DB_PATH.with_suffix(f".sqlite.bak_{ts}")
    shutil.copy2(DB_PATH, backup)
    return backup


def _fresh_state() -> None:
    """Create a fresh state by backing up and removing the old DB."""
    if DB_PATH.is_file():
        backup = _backup_db()
        print(f"  Backed up DB to: {backup}")
        DB_PATH.unlink()
    # Also clean export cache
    for d in (REPO / "Exports" / "Excel").glob("*"):
        if d.is_dir():
            for f in d.glob("*.xlsx"):
                f.unlink()
    print("  Fresh state ready.")


def _update_settings(input_dirs: list[str]) -> None:
    """Write input_dirs to Exports/settings.json."""
    if not SETTINGS_PATH.is_file():
        print("  Warning: settings.json not found, cannot update input_dirs")
        return
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        settings = json.load(f)
    settings["input_dirs"] = input_dirs
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
    print(f"  Updated input_dirs: {input_dirs}")


def _generate_source_manifest() -> Path:
    """Scan workspace and write source_manifest.json, return its path."""
    sys.path.insert(0, str(REPO))
    from iev4pi_transformation_tool.services.workbench import Workbench
    from iev4pi_transformation_tool.core.document_classifier import DocumentClassifier

    wb = Workbench(REPO)
    classifier = DocumentClassifier(REPO)
    input_dirs = wb.resolve_input_dirs()

    source_counts: dict[str, int] = {}
    all_files = 0
    for d in input_dirs:
        files = classifier.iter_supported_files([d])
        for f in files:
            all_files += 1
            try:
                desc = classifier.classify(f, relative_to=REPO)
                kind = desc.source_kind.value
                source_counts[kind] = source_counts.get(kind, 0) + 1
            except Exception:
                source_counts["unknown"] = source_counts.get("unknown", 0) + 1

    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "input_dirs": [str(d) for d in input_dirs],
        "total_files": all_files,
        "source_counts": source_counts,
    }

    os.makedirs(AUDIT_DIR, exist_ok=True)
    manifest_path = AUDIT_DIR / "source_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"  Source manifest: {all_files} files across {len(input_dirs)} dirs")
    for kind, count in sorted(source_counts.items()):
        print(f"    {kind}: {count}")

    return manifest_path


def _run_audit(manifest_path: Path | None, fail_on: str,
               current_root: Path | None = None) -> int:
    """Run audit script and return exit code."""
    audit_script = REPO / "scripts" / "audit_excel_transformation_quality.py"
    current = current_root or (REPO / "Exports" / "Excel")
    cmd = [
        sys.executable, str(audit_script),
        "--current-root", str(current),
        "--golden-root", str(REPO / "data" / "examples"),
        "--output-dir", str(AUDIT_DIR),
    ]
    if manifest_path:
        cmd.extend(["--source-manifest", str(manifest_path)])
    if fail_on:
        cmd.extend(["--fail-on", fail_on])

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate Excel exports and audit quality."
    )
    parser.add_argument(
        "--fresh-state", action="store_true",
        help="Backup and clear .iev4pi/state.sqlite and stale Excel exports",
    )
    parser.add_argument(
        "--input-dirs", nargs="+", default=None,
        help="Space-separated input directories (default: read from settings)",
    )
    parser.add_argument(
        "--audit-only", action="store_true",
        help="Skip regeneration, run audit on current exports only",
    )
    parser.add_argument(
        "--audit-filled-templates", action="store_true",
        help="Audit data/filled_templates/ instead of Exports/Excel/",
    )
    parser.add_argument(
        "--fail-on", type=str, default="key,header,empty-required,missing-export",
        help="Comma-separated fail criteria (default: key,header,empty-required,missing-export)",
    )
    args = parser.parse_args()

    # Pre-flight: check DB lock
    if _is_db_locked():
        print("ERROR: .iev4pi/state.sqlite is locked by another process.")
        print("Wait for extraction/GUI to finish or use --fresh-state with a temp DB.")
        return 1

    # Step 1: Setup
    if args.fresh_state:
        print("[1/4] Setting up fresh state...")
        _fresh_state()

    if args.input_dirs:
        print("[1/4] Updating input dirs...")
        _update_settings(args.input_dirs)

    # Step 2: Generate source manifest
    print("[2/4] Generating source manifest...")
    manifest_path = _generate_source_manifest()

    if args.audit_only:
        print("[3/4] Skipping regeneration (--audit-only)")
    else:
        print("[3/4] Automated regeneration is not available.")
        print("      Extraction must run via GUI or main.py first.")
        print(f"      Run: cd {REPO} && python -m iev4pi_transformation_tool.main")
        print("      Then: File → Scan → Extract All → Export Standardized")
        print("      Then re-run: python scripts/prepare_and_audit_excel.py --audit-only")
        if args.fresh_state:
            print("\nState has been prepared (--fresh-state). Run extraction, then audit.")
        return 2

    # Step 4: Audit
    if args.audit_filled_templates:
        current_root = REPO / "data" / "filled_templates"
        print(f"[4/4] Running audit on data/filled_templates/...")
        xlsx_files = list(current_root.rglob("*.xlsx")) if current_root.is_dir() else []
        if not xlsx_files:
            print("ERROR: data/filled_templates/ has no .xlsx files.")
            print("       Run extraction + fill standardized templates first,")
            print("       or use --audit-only (without --audit-filled-templates)")
            print("       to audit Exports/Excel/ instead.")
            return 1
    else:
        current_root = REPO / "Exports" / "Excel"
        print("[4/4] Running audit on Exports/Excel/...")
    rc = _run_audit(manifest_path, args.fail_on, current_root=current_root)

    if rc == 0:
        print("\nAudit PASSED.")
    else:
        print(f"\nAudit FAILED (exit code {rc}).")
        if not args.audit_filled_templates:
            print("NOTE: Exports/Excel/ may contain results from a previous run.")
            print("      Run extraction + export first, or use --audit-filled-templates")
            print("      to audit data/filled_templates/ instead.")
        print(f"See: {AUDIT_DIR / 'excel_transformation_quality.md'}")

    return rc


if __name__ == "__main__":
    sys.exit(main())
