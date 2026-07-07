"""Direct row-level parser for Klemmenplan / Verschaltungsliste Excel sources.

The schema-driven extractor in :mod:`iev4pi_transformation_tool.core.extractor` flattens these
sheets into per-row records keyed by normalized field names — losing the
information we need to reconstruct the standardized output (Klemmleiste
identifier, ditto markers, ``:N`` continuation syntax, title rows that act as
section headers, terminal-label phase markers).

This module reads the source workbook directly with the same calamine engine
the rest of the codebase uses, and returns a list of structured
:class:`TerminalRow` instances ready to be rendered into the standardized
Klemmenplan template.

Used by :mod:`iev4pi_transformation_tool.core.standardized_export` — the schema-driven
extractor is left unchanged so existing legacy outputs and downstream
consumers keep working.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


# --- Header detection -----------------------------------------------------

# Title row content shape: long descriptive German text, no column header keywords.
_TITLE_HEADER_KEYWORDS = ("plt-stelle", "funktion", "beschreibung", "klemmleiste", "klemme", "betriebsmittel")


# LLM-based column header → field type cache (populated once per run).
# Persisted centrally via llm_cache module → .iev4pi/llm_cache.json
_column_semantic_cache: dict[str, str | None] = {}

# Cell value shapes
_DITTO_RE = re.compile(r"^\s*\"+\s*$|^\s*''+\s*$|^[\s\"]+$")
_CONTINUATION_RE = re.compile(r"^\s*:\s*(\d+)\s*$")
_PURE_NUMBER_RE = re.compile(r"^\s*(\d+)\s*$")
_PHASE_DESIGNATOR_RE = re.compile(r"^\s*(\d+)\s*/\s*([A-Z][A-Z0-9]*)\s*$")
_LETTER_DESIGNATOR_RE = re.compile(r"^\s*([A-Z]\d+[A-Za-z]?)\s*$")
_KANAL_DESIGNATOR_RE = re.compile(r"^\s*Kanal\s+(\d+)\s*$", re.IGNORECASE)


# --- Output dataclasses --------------------------------------------------


@dataclass
class TerminalRow:
    """A single terminal entry recovered from the source sheet."""

    sheet_name: str
    layer_label: str
    object_ref: str          # e.g. "-X1.2"
    object_type: str         # Klemmenleiste / Sicherung / Relais / SPS-Modul
    terminal_name: str       # cleaned designator, e.g. "1", "1/L1", "Kanal 1"
    extra_attributes: dict[str, str] = field(default_factory=dict)
    plt_stelle: str = ""
    funktion: str = ""
    beschreibung: str = ""
    zugang: str = ""         # column name varies (Verschaltung / E-Schrank / MSR-Schrank)
    zugang_label: str = ""   # the actual column header (used for attribute name)
    bruecke: str = ""
    geraet: str = ""
    raw_row_index: int = 0


@dataclass
class _SheetLayout:
    title: str = ""
    plt_col: int | None = None
    funktion_col: int | None = None
    beschreibung_col: int | None = None
    klemm_col: int | None = None
    klemm_object_ref: str = ""
    bruecke_col: int | None = None
    geraet_col: int | None = None
    zugang_col: int | None = None
    zugang_label: str = ""
    header_row: int = -1


# --- Public API ----------------------------------------------------------


def parse_klemmenplan_source(xlsx_path: Path, llm_client=None) -> list[TerminalRow]:
    """Parse a Klemmenplan / Verschaltungsliste Excel file.

    Returns one :class:`TerminalRow` per non-empty terminal cell across all
    sheets. Returns an empty list for files that don't expose a
    ``Klemmleiste`` column (e.g. cabinet-reference workbooks like
    ``Betriebsmittel_E-Schrank.xls``) — callers should fall back to the
    schema-driven extractor in that case.
    """
    if not xlsx_path:
        return []
    xlsx_path = Path(xlsx_path)
    if not xlsx_path.is_file():
        return []
    try:
        xl = pd.ExcelFile(xlsx_path, engine="calamine")
    except Exception:
        return []

    rows: list[TerminalRow] = []
    for sheet_name in xl.sheet_names:
        try:
            df = xl.parse(sheet_name, header=None).fillna("")
        except Exception:
            continue
        if df.empty:
            continue
        rows.extend(_parse_sheet(sheet_name, df, llm_client=llm_client))
    return rows


# --- Sheet parsing -------------------------------------------------------


def _parse_sheet(sheet_name: str, df: pd.DataFrame, llm_client=None) -> list[TerminalRow]:
    layout = _detect_layout(df, llm_client=llm_client)
    if layout.klemm_col is None:
        return []  # not a terminal-list sheet

    _PLT_RE = re.compile(r"^TU\s*\d+\s*\.\s*\w+$")
    # Detect files where the PLT column contains tags with spaces (e.g. "TU 10.N18")
    # — in this layout the Funktion column is always empty (encoded in the PLT tag)
    # and all subsequent columns are shifted left by one.
    _plt_samples = [
        _clean_text(_safe_cell(list(df.iloc[r].values), layout.plt_col))
        for r in range(layout.header_row + 1, min(layout.header_row + 6, len(df)))
    ]
    _spaced_plts = sum(1 for p in _plt_samples if re.match(r"^TU\s+\d+\.\w+$", p))
    # Only shift if PLT tags are spaced AND the Funktion column is genuinely
    # empty (the spaced-PLT format puts the function description in the PLT
    # column itself, leaving the Funktion column unused).
    if _spaced_plts >= 2 and layout.funktion_col is not None:
        _fun_samples = [
            _clean_text(_safe_cell(list(df.iloc[r].values), layout.funktion_col))
            for r in range(layout.header_row + 1, min(layout.header_row + 6, len(df)))
        ]
        _fun_filled = sum(
            1 for f in _fun_samples
            if f and f.lower() not in ("nan", "none", "null", "na", "-", "")
            and len(f) > 1 and not f.startswith('"')
        )
        if _fun_filled == 0:
            # Funktion column is genuinely empty → all descriptive columns
            # are shifted left by one (funktion←beschreibung, beschreibung←zugang).
            # The old funktion_col becomes the new beschreibung_col, and the old
            # beschreibung_col becomes the new funktion_col.
            _old_fun, _old_bes = layout.funktion_col, layout.beschreibung_col
            _old_zug = layout.zugang_col
            layout.funktion_col = _old_bes          # funktion ← beschreibung
            layout.beschreibung_col = _old_zug       # beschreibung ← zugang
            layout.zugang_col = None                 # zugang is now unused

    out: list[TerminalRow] = []
    current_layer = _clean_layer_label(layout.title) if layout.title else "Allgemein"
    last_non_ditto: dict[int, str] = {}  # column index → last real value
    last_plt_tag: str = ""  # last recognised PLT tag (TUxx.xxx) for fill-down

    for row_idx in range(layout.header_row + 1, len(df)):
        row = list(df.iloc[row_idx].values)
        # Detect intra-sheet section headers: only the first column has text,
        # and that text doesn't look like a data identifier
        if _looks_like_section_header(row):
            current_layer = _clean_layer_label(_clean_text(row[0]))
            last_non_ditto.clear()
            last_plt_tag = ""
            continue

        terminal_raw = _safe_cell(row, layout.klemm_col)
        terminal_name, extras = _parse_terminal_designator(terminal_raw)
        if terminal_name is None:
            # Empty terminal cell → skip (not a terminal row)
            continue

        # Resolve ditto markers for descriptive columns
        for col_idx in (layout.plt_col, layout.funktion_col, layout.beschreibung_col,
                        layout.zugang_col, layout.bruecke_col, layout.geraet_col):
            if col_idx is None:
                continue
            raw = _safe_cell(row, col_idx)
            cleaned = _clean_text(raw)
            if _is_ditto(cleaned):
                cleaned = last_non_ditto.get(col_idx, "")
            if cleaned:
                last_non_ditto[col_idx] = cleaned

        # PLT tag fill-down: follow-up terminal rows (N, Pe, etc.) inherit
        # the last recognised PLT tag instead of their literal column value.
        _plt_raw = _resolved_text(row, layout.plt_col, last_non_ditto)
        if _PLT_RE.match(_plt_raw):
            last_plt_tag = _plt_raw
        elif last_plt_tag and not _PLT_RE.match(_plt_raw):
            _plt_raw = last_plt_tag

        out.append(
            TerminalRow(
                sheet_name=sheet_name,
                layer_label=current_layer,
                object_ref=layout.klemm_object_ref,
                object_type=_classify_object_type(layout.klemm_object_ref, layout.zugang_label),
                terminal_name=terminal_name,
                extra_attributes=extras,
                plt_stelle=_plt_raw,
                funktion=_resolved_text(row, layout.funktion_col, last_non_ditto),
                beschreibung=_resolved_text(row, layout.beschreibung_col, last_non_ditto),
                zugang=_resolved_text(row, layout.zugang_col, last_non_ditto),
                zugang_label=layout.zugang_label,
                bruecke=_resolved_text(row, layout.bruecke_col, last_non_ditto),
                geraet=_resolved_text(row, layout.geraet_col, last_non_ditto),
                raw_row_index=row_idx,
            )
        )
    return out


def _resolve_column_semantics_batch(headers: list[str], llm_client=None) -> dict[str, str | None]:
    """Map column headers to canonical field types using LLM semantic understanding.

    One LLM call classifies ALL headers — no hardcoded regexes, no synonym
    lists.  Results are cached so repeated headers don't trigger new calls.
    """
    uncached = [h for h in headers if h not in _column_semantic_cache]
    if not uncached:
        return {h: _column_semantic_cache[h] for h in headers}

    if llm_client is None or not hasattr(llm_client, "available") or not llm_client.available():
        return {}

    prompt = (
        "Map each column header from a terminal plan (Klemmenplan) spreadsheet "
        "to one of these canonical field types:\n"
        "- klemm: terminal block ID/name (e.g. Klemmleiste, Terminal Block)\n"
        "- plt: PLT reference / device tag (e.g. PLT-Stelle, Tag)\n"
        "- funktion: function/purpose (e.g. Funktion, Function)\n"
        "- beschreibung: description/comment (e.g. Beschreibung, Remarks)\n"
        "- bruecke: bridge/jumper (e.g. Brücke, Jumper)\n"
        "- geraet: device/equipment (e.g. Gerät, Device)\n"
        "- zugang: cabinet/panel/enclosure connection point (e.g. E-Schrank, MSR-Schrank, Verschaltung, Cabinet, Panel, Zugang)\n"
        "- querschnitt: wire cross-section (e.g. Querschnitt, Cross Section)\n"
        "- farbe: wire color (e.g. Farbe, Wire Color)\n"
        "- seite: page/row/column reference (e.g. Seite, Page, Row)\n"
        "- signalkabel: signal/cable identifier\n\n"
        "Headers to classify:\n"
        + "\n".join(f'- "{h}"' for h in uncached) + "\n\n"
        'Return JSON: {"mappings": {"Header Name": "klemm", ...}}'
    )
    try:
        response = llm_client.chat_json(
            "You map spreadsheet column headers to canonical field types. Return ONLY valid JSON.",
            prompt,
        )
        if isinstance(response, dict):
            mappings = response.get("mappings", {})
            if isinstance(mappings, dict):
                for h, ft in mappings.items():
                    _column_semantic_cache[str(h)] = str(ft) if ft else None
    except Exception:
        pass

    return {h: _column_semantic_cache.get(h) for h in headers}


def _match_column_semantic(cell_text: str) -> str | None:
    """Match a column header to a standard field type.

    Uses LLM semantic understanding for any language/format — no hardcoded
    regexes or synonym lists.  Results are cached per header text.
    """
    if cell_text in _column_semantic_cache:
        return _column_semantic_cache[cell_text]

    # For fallback (no LLM available), use a minimal regex set that covers
    # the most common German/English patterns.
    if re.search(r"klemmleiste|terminal\s*block|klemme", cell_text, re.IGNORECASE):
        return "klemm"
    if re.search(r"plt|tag|akz|device.*tag", cell_text, re.IGNORECASE):
        return "plt"
    return None  # Will be resolved by batch LLM if available


def _detect_layout(df: pd.DataFrame, llm_client=None) -> _SheetLayout:
    layout = _SheetLayout()
    title_chunks: list[str] = []
    for row_idx in range(min(10, len(df))):
        row = list(df.iloc[row_idx].values)
        cells = [_clean_text(c) for c in row]
        non_empty = [c for c in cells if c]
        if not non_empty:
            continue

        joined = " | ".join(non_empty).lower()
        if any(kw in joined for kw in _TITLE_HEADER_KEYWORDS):
            # This is the header row.
            layout.header_row = row_idx

            # Batch-resolve ALL column headers via LLM first (one API call).
            header_texts = [c for c in cells if c]
            if llm_client is not None:
                _resolve_column_semantics_batch(header_texts, llm_client)

            for c_idx, cell in enumerate(cells):
                if not cell:
                    continue
                field_type = _match_column_semantic(cell)
                if field_type == "klemm":
                    m = re.search(r"klemmleiste\s*[\n\s]*([A-Za-z][A-Za-z0-9._\-]*)", cell, re.IGNORECASE)
                    layout.klemm_col = c_idx
                    layout.klemm_object_ref = _normalize_klemmleiste(m.group(1)) if m else ""
                elif field_type == "plt":
                    layout.plt_col = c_idx
                elif field_type == "funktion":
                    layout.funktion_col = c_idx
                elif field_type == "beschreibung":
                    layout.beschreibung_col = c_idx
                elif field_type == "bruecke":
                    layout.bruecke_col = c_idx
                elif field_type == "geraet":
                    layout.geraet_col = c_idx
                elif field_type == "zugang":
                    layout.zugang_col = c_idx
                    layout.zugang_label = cell
            layout.title = " ".join(title_chunks)
            return layout
        else:
            # Pre-header → treat as title chunk
            title_chunks.append(" ".join(non_empty))
    return layout


# --- Cell helpers --------------------------------------------------------


def _safe_cell(row: list, idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return _clean_text(row[idx])


def _clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").strip()
    # Collapse internal whitespace (multiple spaces / newlines) to single spaces
    text = re.sub(r"\s+", " ", text)
    return text


def _is_ditto(value: str) -> bool:
    return bool(_DITTO_RE.match(value)) and value.strip() in {'"', '""', "'", "''", '" "', '" " "', '" " " "'}


def _resolved_text(row: list, col_idx: int | None, last_non_ditto: dict[int, str]) -> str:
    if col_idx is None:
        return ""
    raw = _safe_cell(row, col_idx)
    if _is_ditto(raw):
        return last_non_ditto.get(col_idx, "")
    return raw


def _looks_like_section_header(row: list) -> bool:
    cells = [_clean_text(c) for c in row]
    non_empty = [(i, c) for i, c in enumerate(cells) if c]
    if len(non_empty) != 1:
        return False
    idx, text = non_empty[0]
    if idx != 0:
        return False
    if len(text) < 8 or len(text) > 120:
        return False
    if any(ch.isdigit() for ch in text[:3]):
        return False
    return True


def _normalize_klemmleiste(suffix: str) -> str:
    """Translate the suffix found in a header (``X1.2``, ``x01``, ``X-02``) to a
    canonical reference like ``-X1.2``, ``-X01``, ``-X-02``.
    """
    cleaned = suffix.strip()
    if not cleaned:
        return ""
    # Force leading letter uppercase
    head = cleaned[0].upper()
    return f"-{head}{cleaned[1:]}"


def _classify_object_type(object_ref: str, zugang_label: str) -> str:
    ref = (object_ref or "").upper()
    label = (zugang_label or "").lower()
    if "sps" in label or "modul" in label:
        return "SPS-Modul"
    if ref.startswith("-K"):
        return "Relais"
    if ref.startswith("-F"):
        return "Sicherung"
    if "sicherung" in label:
        return "Sicherung"
    if "relais" in label:
        return "Relais"
    return "Klemmenleiste"


def _clean_layer_label(text: str) -> str:
    """Pretty-print a Layer label.

    Examples:
        "Verschaltungsliste MSR-Schrank - Spannungsversorgung 24 Volt DC"
        → "Spannungsversorgung 24 Volt DC"
        "Verschaltungsliste E-Schrank" → "Verschaltungsliste E-Schrank"
        "Allgemein" → "Allgemein"
    """
    text = _clean_text(text)
    if not text:
        return "Allgemein"
    # If text contains " - " split off the leading "Verschaltungsliste ..."
    parts = re.split(r"\s+-\s+", text, maxsplit=1)
    if len(parts) == 2 and parts[1]:
        return parts[1]
    return text


def _parse_terminal_designator(value: str) -> tuple[str | None, dict[str, str]]:
    """Convert a raw cell value into ``(terminal_name, extra_attributes)``.

    Returns ``(None, {})`` for empty / non-terminal cells.
    """
    value = _clean_text(value)
    if not value:
        return None, {}

    m = _PURE_NUMBER_RE.match(value)
    if m:
        return m.group(1), {}

    m = _CONTINUATION_RE.match(value)
    if m:
        return m.group(1), {}

    m = _PHASE_DESIGNATOR_RE.match(value)
    if m:
        return f"{m.group(1)}/{m.group(2)}", {"Phase": m.group(2)}

    m = _KANAL_DESIGNATOR_RE.match(value)
    if m:
        return f"Kanal {m.group(1)}", {"Funktionsklasse": "SPS-Kanal"}

    m = _LETTER_DESIGNATOR_RE.match(value)
    if m:
        return m.group(1), {"Klemmentyp": "Steuerklemme"}

    # Anything else: treat as opaque designator if it's short, else skip
    if 0 < len(value) <= 12 and not value.lower().startswith(("verschaltung", "klemm", "plt")):
        return value, {}
    return None, {}
