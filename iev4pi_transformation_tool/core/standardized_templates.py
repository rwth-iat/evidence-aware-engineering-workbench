"""Lookup helpers for the standardized Stellenplan / Klemmenplan blank templates.

The blank templates live in ``data/templates/`` and are derived
from the curated examples in ``data/examples/`` via
``scripts/build_standardized_blank_templates.py``.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import openpyxl

from iev4pi_transformation_tool.models import DocumentFamily

REPO_ROOT = Path(__file__).resolve().parents[2]
STANDARDIZED_TEMPLATE_DIR = REPO_ROOT / "data" / "templates"
FILLED_TEMPLATES_DIR = REPO_ROOT / "data" / "filled_templates"

STELLENPLAN_TEMPLATE = "Stellenplan_template.xlsx"
KLEMMENPLAN_TEMPLATE = "Klemmenplan_template.xlsx"
DATASHEET_TEMPLATE = "Datasheet_template.xlsx"
ASSEMBLY_3D_TEMPLATE = "Assembly_3D_template.xlsx"
STROMLAUFPLAN_TEMPLATE = "Stromlaufplan_template.xlsx"
AIO_TEMPLATE = "Schema_Specification_v0.8_FREEZE_template.xlsx"

# Note: AIO_TEMPLATE replaces the 3 legacy templates (Stellenplan, Klemmenplan,
# Stromlaufplan) for all document types.  Assembly_3D and Datasheet remain
# unchanged.  The old FAMILY_TO_STANDARDIZED_TEMPLATE entries for the 3 replaced
# types are preserved as comments for reference during the transitional period.
FAMILY_TO_STANDARDIZED_TEMPLATE: dict[str, str] = {
    # ── AIO template (replaces Stellenplan + Klemmenplan + Stromlaufplan) ──
    DocumentFamily.STELLEN_OVERVIEW_RECORD.value: AIO_TEMPLATE,
    DocumentFamily.KLEMMENPLAN_ROW.value: AIO_TEMPLATE,
    DocumentFamily.VERSCHALTUNGSLISTE_ROW.value: AIO_TEMPLATE,
    DocumentFamily.CABINET_REFERENCE_ROW.value: AIO_TEMPLATE,
    DocumentFamily.STROMLAUF_COMPONENT_GROUP.value: AIO_TEMPLATE,
    DocumentFamily.STROMLAUF_COMPONENT.value: AIO_TEMPLATE,
    DocumentFamily.STROMLAUF_CONNECTION.value: AIO_TEMPLATE,
    # ── Unchanged templates ──
    DocumentFamily.STELLEN_TU_DATASHEET.value: DATASHEET_TEMPLATE,
    DocumentFamily.IFC_3D_ASSEMBLY_STEP.value: ASSEMBLY_3D_TEMPLATE,
    DocumentFamily.IFC_3D_ASSEMBLY_CONNECTION.value: ASSEMBLY_3D_TEMPLATE,
    DocumentFamily.IFC_3D_POSITION.value: ASSEMBLY_3D_TEMPLATE,
    DocumentFamily.IFC_3D_PART_LIBRARY.value: ASSEMBLY_3D_TEMPLATE,
}

TEMPLATE_TO_EXPORT_CATEGORY: dict[str, str] = {
    AIO_TEMPLATE:           "AIO",
    DATASHEET_TEMPLATE:     "datasheet",
    ASSEMBLY_3D_TEMPLATE:   "piping_diagram",
    # Legacy categories (kept for transitional dual-write)
    STELLENPLAN_TEMPLATE:   "instrument_list",
    KLEMMENPLAN_TEMPLATE:   "instrument_wiring",
    STROMLAUFPLAN_TEMPLATE: "instrument_wiring",
}


def get_standardized_template_path(family: str) -> Path | None:
    file_name = FAMILY_TO_STANDARDIZED_TEMPLATE.get(family)
    if not file_name:
        return None
    path = STANDARDIZED_TEMPLATE_DIR / file_name
    return path if path.is_file() else None


def load_standardized_template(family: str) -> openpyxl.Workbook | None:
    path = get_standardized_template_path(family)
    if path is None:
        return None
    return openpyxl.load_workbook(path)


def get_template_output_path(family: str) -> Path | None:
    """Return the path where a filled template should be saved for a family.

    For AIO families, returns None — per‑document files are saved directly
    by :func:`~iev4pi_transformation_tool.core.aio_exporter.export_aio_workbook`
    with the ``{document_key}_AIO.xlsx`` naming pattern.
    """
    file_name = FAMILY_TO_STANDARDIZED_TEMPLATE.get(family)
    if not file_name:
        return None
    if file_name == AIO_TEMPLATE:
        return None  # AIO saves per-document files directly
    FILLED_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    return FILLED_TEMPLATES_DIR / file_name


def get_export_category(template_name: str) -> str | None:
    """Return the export category for a filled template.

    Handles both fixed-name templates (Datasheet, Assembly_3D) and per-document
    AIO files (``*_AIO.xlsx``).
    """
    if template_name.endswith("_AIO.xlsx"):
        return "AIO"
    return TEMPLATE_TO_EXPORT_CATEGORY.get(template_name)


def copy_filled_template_to_export(template_name: str, export_base_dir: Path) -> Path | None:
    """Copy a filled template from data/filled_templates/ to the export dir."""
    src = FILLED_TEMPLATES_DIR / template_name
    if not src.is_file():
        return None
    category = get_export_category(template_name)
    if not category:
        return None
    dest_dir = export_base_dir / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / template_name
    if src.resolve() != dest.resolve():
        shutil.copy2(str(src), str(dest))
    return dest


def collect_filled_templates() -> dict[str, Path]:
    """Return {template_name: path} for filled templates in the output dir.

    For AIO (per-document output), collects all ``*_AIO.xlsx`` files.
    Legacy templates (Klemmenplan, Stellenplan, Stromlaufplan) are excluded
    — they have been replaced by AIO.
    """
    result: dict[str, Path] = {}
    # AIO per-document workbooks
    for path in sorted(FILLED_TEMPLATES_DIR.glob("*_AIO.xlsx")):
        result[path.name] = path
    # Assembly_3D and Datasheet (unchanged)
    for name in [DATASHEET_TEMPLATE, ASSEMBLY_3D_TEMPLATE]:
        path = FILLED_TEMPLATES_DIR / name
        if path.is_file():
            result[name] = path
    return result
