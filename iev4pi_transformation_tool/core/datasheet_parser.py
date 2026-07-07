"""Parse structured technical datasheets into EAV attributes.

Supports multiple PDF formats via two complementary strategies:

**Heuristic parser** (``parse_datasheet``) — fast, regex-based, works well for
table-format datasheets (Siemens-style).  No LLM dependency.

**LLM parser** (``parse_datasheet_llm``) — semantic extraction using an LLM.
Handles any datasheet format (table, narrative, mixed) by understanding the
content rather than matching patterns.  Requires an LLM client.

**Smart combiner** (``parse_datasheet_smart``) — runs both and merges results.

Strategy:
1. Parse all text into {section: {param: value}} using stateful scan
2. Score each param by value quality (short, numeric, with units)
3. Filter out sections/params below quality threshold
4. Deduplicate identical key:value pairs across sections
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    import fitz  # type: ignore
except ImportError:
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        fitz = None  # type: ignore


# ── Line classification ────────────────────────────────────────────────────

_SECTION_RE = re.compile(r"^[A-Z][A-Za-z\s/-]{2,40}$")
_PARAM_RE = re.compile(
    r"^\s*[●\-•]\s*(.+)$|^([A-Za-z][A-Za-z\s/.,()°μ%±–\-0-9]{3,60})$"
)
_VALUE_RE = re.compile(
    r"^(?:\d+[\d.,\s]*(?:\s*(?:V|A|W|mA|mm|g|kg|ms|m|Hz|bar|°[CF]|%|DC|AC|VA|bit|Ω|µs))?\s*)$|"
    r"^(?:Yes|No|Typ\.?)\s*$|"
    r"^(?:[A-Z][a-z]+(?:\s+[a-z]+){0,2})$"
)
_NON_SECTION = {
    "data sheet", "general information", "product function",
    "supply voltage", "input current", "power loss",
    "digital inputs", "input voltage", "output voltage",
    "analog inputs", "analog outputs", "digital outputs",
    "dimensions", "weight", "ambient conditions",
    "alarms", "diagnostics", "interrupts", "isochronous mode",
    "galvanic isolation", "cable length",
}
_ORDER_RE = re.compile(r"\b([A-Z]{2,5}\s*[\d][\dA-Za-z\s/\-.]{6,30})\b")
_SPARE_RE = re.compile(r"\*+\s*spare\s*part\s*\*+\s*(.+)", re.IGNORECASE)

# Boilerplate / meta-commentary patterns stripped from VLM/LLM output before caching
_BOILERPLATE_PATTERNS = [
    "not explicitly stated", "not provided", "not mentioned",
    "not specified", "visit product website",
]


def _is_param_name(text: str) -> bool:
    t = text.strip()
    if not t or len(t) < 3:
        return False
    if t.lower() in _NON_SECTION:
        return False
    if t.startswith(('●', '-', '—', '•')):
        return True
    if re.match(r"^[A-Za-z][A-Za-z\s/.,()°μ%±\-0-9]{3,70}$", t) and not t.endswith(
        ('.', '!', '?')
    ):
        return True
    return False


def _is_value(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if _VALUE_RE.match(t):
        return True
    return False


# ── Quality scoring ────────────────────────────────────────────────────────


def _param_value_quality(value: str) -> float:
    """Score 0.0–1.0: how much a value looks like a real parameter value."""
    if not value:
        return 0.0
    v = value.strip()
    w = v.split()
    score = 0.0

    # Strong signal: value contains a unit of measurement
    if re.search(r"\b(?:V|A|W|mA|mm|g|kg|ms|m|Hz|bar|°[CF]|%|DC|AC|VA|bit|Ω|µs|µm|MPa|kg/)\b", v):
        score += 0.5
    # Good signal: purely numeric (with optional unit already covered above)
    elif re.match(r"^[\d.,\s]+$", v) and len(v) < 12:
        score += 0.4
    # Moderate signal: contains digits (might be a measurement with unit)
    elif re.search(r"\d", v) and len(w) <= 3:
        score += 0.25

    # Brevity is good
    if len(w) <= 2:
        score += 0.15
    elif len(w) <= 4:
        score += 0.05

    # Penalties
    if len(w) > 8:
        score -= 0.4
    if len(v) > 120:
        score -= 0.3
    # Sentence-like endings (narrative text)
    if v.rstrip().endswith(('.', '!', '?')) and len(w) > 3:
        score -= 0.3
    # URL, file path
    if any(x in v.lower() for x in ('www.', 'http:', '\\\\', '.pdf')):
        score = 0.0
    # Isolated small integer (likely page/section number)
    if re.match(r"^\d{1,3}$", v) and len(w) == 1:
        score -= 0.15
    return max(0.0, min(1.0, score))


def _param_name_quality(name: str) -> float:
    """Score 0.0–1.0: how much a name looks like a parameter label."""
    if not name:
        return 0.0
    score = 0.4
    words = name.split()
    if len(words) <= 5:
        score += 0.15
    if len(words) > 12:
        score -= 0.4
    if name[0].isupper() and not name.isupper() and not name.islower():
        score += 0.1
    if name.rstrip().endswith(('.', '!', '?')):
        score -= 0.3
    if 'www.' in name.lower() or 'http' in name.lower():
        score -= 0.5
    # ALL CAPS and long → likely a section header, not a param
    if name.isupper() and len(name) > 8:
        score -= 0.2
    return max(0.0, min(1.0, score))


def _section_quality(section: str, params: dict[str, str]) -> float:
    """Average quality of all params in a section."""
    if not params:
        return 0.0
    scores = [
        (_param_name_quality(n) + _param_value_quality(v)) / 2
        for n, v in params.items()
    ]
    return sum(scores) / len(scores)


# ── Pattern-based key:value extraction ──────────────────────────────────────

# Universal "Label: Value" pattern found in technical documents of all vendors.
# Label: short descriptive text (2-8 words), followed by colon, then value.
_KV_PATTERN = re.compile(
    r"(?:^|\n)\s*([A-Z][A-Za-z0-9\s/\-.,()°±]{4,60}?)\s*[:：]\s*(.{1,120})",
    re.MULTILINE,
)

# Compact spec lines: "Nominal diameter DN 8 … 50" or "Max. 6 W" etc.
_COMPACT_SPEC = re.compile(
    r"(?:^|\n)\s*([A-Z][A-Za-z\s/\-]{4,40})\s{2,}(.{1,100})",
    re.MULTILINE,
)

# Table-like rows: label followed by whitespace gap then value
_TABLE_ROW = re.compile(
    r"^\s*([A-Za-z][A-Za-z\s/\-.,()°±]{4,50}?)\s{4,}(\S.{1,80})",
    re.MULTILINE,
)

# Numeric value with optional unit — for validating extracted values
_VALUE_HAS_NUMBER = re.compile(r"\d")
_VALUE_HAS_UNIT = re.compile(
    r"\b(?:V|A|W|mA|mm|cm|m|km|g|kg|ms|s|Hz|kHz|bar|°[CF]|K|%|DC|AC|VA|bit|Ω|µm|MPa|psi)\b"
)
# Noise patterns to reject
_NOISE_VALUE = re.compile(
    r"^(?:Page\s+\d|https?://|www\.|©|All rights|\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)


# TOC line: "Parameter name  . . . . . .  9" (dots may be spaced)
_TOC_PATTERN = re.compile(r"^(.{4,60}?)\s{2,}[.\s]{4,}\s*\d{1,3}$", re.MULTILINE)
# Value after label on actual spec page: "Label  value"
_LABEL_VALUE_GAP = re.compile(
    r"^\s*([A-Z][A-Za-z0-9\s/\-.,()°±]{4,60}?)\s{4,}(\S.{1,80})",
    re.MULTILINE,
)


def _extract_toc_entries(full_text: str) -> set[str]:
    """Extract parameter names from table-of-contents lines."""
    entries: set[str] = set()
    for m in _TOC_PATTERN.finditer(full_text):
        name = m.group(1).strip().rstrip('. ')
        # Filter noise: skip "Table of contents", page numbers, very short
        if len(name) < 5 or name.lower().startswith('table of'):
            continue
        if name[0].isdigit():
            continue
        entries.add(name)
    return entries


def _find_value_for_label(full_text: str, label: str) -> str | None:
    """Search for a value associated with a label in the document text.

    Skips TOC-line occurrences (where label is followed by dot leaders).
    """
    escaped = re.escape(label)
    # Find all occurrences of the label
    for m in re.finditer(escaped, full_text, re.IGNORECASE):
        start = m.end()
        after = full_text[start:start + 150]
        # Skip TOC lines (followed by dot leaders like ". . . . .")
        if re.match(r'\s{2,}[.\s]{4,}\s*\d', after):
            continue
        # Try: Label: value (colon separated)
        cm = re.match(r'\s*:\s*(\S.{0,80})', after)
        if cm:
            val = cm.group(1).strip()
            if val and len(val) > 1:
                return val
        # Try: Label followed by whitespace gap then value
        gm = re.match(r'\s{2,}(\S.{0,80})', after)
        if gm:
            val = gm.group(1).strip()
            # Must look like a value (contains number, unit, or is short)
            if _VALUE_HAS_NUMBER.search(val) or _VALUE_HAS_UNIT.search(val) or len(val.split()) <= 3:
                return val
        # Try: value on next line
        nm = re.match(r'\n\s*(\S.{0,80})', after)
        if nm:
            val = nm.group(1).strip()
            if (_VALUE_HAS_NUMBER.search(val) or _VALUE_HAS_UNIT.search(val)) and len(val) > 1:
                return val
    return None


def _extract_kv_from_text(full_text: str) -> dict[str, str]:
    """Extract key:value pairs from raw text using universal patterns.

    Works on any technical document regardless of vendor or layout.
    Returns {param_name: value} dict.
    """
    result: dict[str, str] = {}

    for pattern in (_KV_PATTERN, _TABLE_ROW, _COMPACT_SPEC):
        for m in pattern.finditer(full_text):
            key = m.group(1).strip().rstrip(',:;')
            val = m.group(2).strip().rstrip(',;')

            # Quality checks on key
            if len(key) < 3 or len(key) > 70:
                continue
            if key.isdigit():
                continue
            if key.isupper() and len(key) > 8:
                continue  # likely a section header, not a param

            # Quality checks on value
            if not _VALUE_HAS_NUMBER.search(val) and not _VALUE_HAS_UNIT.search(val):
                # Value without number or unit — must be short to be valid
                if len(val.split()) > 4:
                    continue
            if _NOISE_VALUE.match(val):
                continue
            if len(val) > 150:
                continue
            if 'www.' in val.lower() or 'http' in val.lower():
                continue

            # Deduplicate: prefer first occurrence (usually the spec table)
            key_lower = key.lower().rstrip(' :')
            if key_lower not in {k.lower().rstrip(' :') for k in result}:
                result[key] = val

    return result


# ── Manufacturer detection ──────────────────────────────────────────────────

# Common English words that are NOT company names
_NON_COMPANY = {
    "data", "sheet", "technical", "information", "operating", "instructions",
    "products", "solutions", "services", "general", "product", "function",
    "manual", "handbook", "page", "document", "table", "contents",
    "order", "number", "description", "application", "your", "benefits",
    "system", "design", "installation", "input", "output", "power",
    "digital", "analog", "supply", "voltage", "current", "weight",
    "dimensions", "material", "materials", "with", "without", "from",
    "note", "important", "warning", "caution", "safety", "about",
    "the", "for", "and", "this", "that", "all", "not", "are",
    # Document type labels (not company names)
    "data", "sheet", "technical", "information", "datasheet",
    "operating", "instructions", "betriebsanleitung", "manuel",
    "temperature", "bluetooth", "hart", "profibus", "foundation",
    "fieldbus", "ethernet", "modbus", "signal", "output",
    # Common non-manufacturer phrases found in datasheet headers
    "valid", "version", "applications", "access",
    "print", "engine", "grundfos print engine",
    "technical datasheet", "technical information",
    "operating instructions", "data sheet",
    "safety instructions", "functional safety",
    "brief overview", "special documentation",
}

# Known manufacturer names for fallback when heuristic fails.
# Maps directory name or filename substring → canonical manufacturer name.
_FALLBACK_MANUFACTURERS: dict[str, str] = {
    "siemens": "Siemens",
    "endress": "Endress+Hauser",
    "hauser": "Endress+Hauser",
    "buerkert": "Christian Bürkert",
    "bürkert": "Christian Bürkert",
    "burkert": "Christian Bürkert",
    "wika": "WIKA",
    "krohne": "Krohne",
    "vega": "VEGA",
    "yokogawa": "Yokogawa",
    "grundfos": "Grundfos",
    "beckhoff": "Beckhoff",
    "wago": "Wago",
    "phoenix": "Phoenix Contact",
    "turck": "Turck",
    "emerson": "Emerson",
    "abb": "ABB",
    "schneider": "Schneider Electric",
    "pepperl": "Pepperl+Fuchs",
    "ifm": "ifm electronic",
}


def _extract_manufacturer(pdf_path: Path) -> str:
    """Extract manufacturer name from PDF header text.

    Uses font-size and position heuristics: the largest-font text near the
    top of page 1 is typically a company or product name.
    """
    if fitz is None:
        return ""
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return ""
    if not doc:
        return ""

    page = doc[0]
    page_h = page.rect.height

    # Collect text spans with size info from first page (top 80% to catch headers)
    spans: list[tuple[float, float, str, float]] = []  # (y, size, text, width)
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                y = (span["bbox"][1] + span["bbox"][3]) / 2
                size = span.get("size", 8)
                w = span["bbox"][2] - span["bbox"][0]
                if y < page_h * 0.8 and t and len(t) > 2:
                    spans.append((y, size, t, w))

    # Pass 1: PDF metadata (author field often contains company name)
    meta = doc.metadata
    _author_raw = (meta.get("author") or "").strip()
    doc.close()

    if _author_raw and _author_raw not in ("00", ""):
        # Extract company name from author: "Christian Bürkert GmbH & Co. KG" → "Bürkert"
        _parts = _author_raw.split()
        _company_words = []
        for _p in _parts[:4]:
            _pw = _p.strip('.,;')
            if _pw.lower() in ("gmbh", "co", "kg", "inc", "ltd", "llc", "ag", "se", "&"):
                break
            _company_words.append(_pw)
        _author = " ".join(_company_words)
        # Must be a plausible company name
        if len(_author) >= 3 and _author[0].isupper():
            return _author

    if not spans:
        return ""

    # Pass 2: largest-font word (usually product name, but sometimes brand)
    spans.sort(key=lambda s: -s[1])
    for _y, _size, text, _w in spans[:15]:
        word = text.strip().rstrip('.,;:')
        lower = word.lower()
        if lower in _NON_COMPANY: continue
        if word.isdigit() or len(word) < 3: continue
        if re.match(r'^(?:Type|Model|Typ|Order)\s+\d', word, re.IGNORECASE): continue
        if re.search(r'\d', word): continue
        if 4 <= len(word) <= 30 and word[0].isupper() and word.isalpha():
            return word

    # Pass 3: top-position text (company in header, any font size)
    spans.sort(key=lambda s: s[0])
    for _y, _size, text, _w in spans[:25]:
        word = text.strip().rstrip('.,;:')
        if word.lower() in _NON_COMPANY or word.isdigit() or len(word) < 3: continue
        if re.search(r'\d', word): continue
        if 3 <= len(word) <= 25 and (word.isupper() or word[0].isupper()):
            return word

    return ""


def _extract_order_code(full_text: str, pdf_path: Path) -> str:
    """Extract order/part number using manufacturer-specific patterns.

    More reliable than generic regex because different manufacturers use
    different order-code formats (Siemens: 6ES7..., Endress+Hauser: TI...,
    Yokogawa: GS..., Vega: PA...).
    """
    # Strategy 1: Siemens 6ES7/6ES5 pattern (most common format)
    _siemens = re.search(r'\b(6ES[57]\s*[\dA-Za-z\s\-.]{8,30})\b', full_text[:2000])
    if _siemens:
        return _siemens.group(1).strip()[:40]

    # Strategy 2: Manufacturer-specific document/order codes from first page
    _header = full_text[:1500]

    # Endress+Hauser: TI..., BA..., SD..., GP..., XA... followed by digits
    _eh = re.search(r'\b((?:TI|BA|SD|GP|XA|KA|EA)\d{2,6}[A-Z]?\d*[/.]\d{2}[/.]\w{2}(?:[/.]\d{2}\.\d{2})?)', _header)
    if _eh:
        return _eh.group(1).strip()

    # Yokogawa: GS 01F06A00-01EN pattern
    _ykw = re.search(r'\b(GS\s+\d{2}[A-Z]\d{2}[A-Z]\d{2}[-.]\d{2}[A-Z]{2,3})\b', _header, re.IGNORECASE)
    if _ykw:
        return _ykw.group(1).strip()

    # Vega: PA 44217-EN-XXXXXX
    _vega = re.search(r'\b(PA\s*\d{4,6}[-.]\w{2}[-.]\d{6})\b', _header, re.IGNORECASE)
    if _vega:
        return _vega.group(1).strip()

    # Generic pattern — broad match for any order-code-like string
    # But filter out known false positives
    _om = re.search(
        r'\b((?:[A-Z]{2,5}\s*[\d][\dA-Za-z\s/\-.]{6,30})|'
        r'(?:[\d][\dA-Za-z\-.]{8,30}))\b',
        _header,
    )
    if _om:
        _raw = _om.group(1).strip()
        # Reject if it's a standards reference
        _is_standard = re.match(
            r'^(?:ETIM|IEC\s+\d|ISO\s+\d|DIN\s+\d|EN\s+\d|UL\s+\d|'
            r'DC\s*\d|AC\s*\d)', _raw, re.IGNORECASE,
        )
        if not _is_standard:
            # Also reject if it looks like a document section heading
            if not re.match(r'^(?:Page|Chapter|Section|Table|Figure)\s', _raw, re.IGNORECASE):
                return _raw

    return ""


def _extract_model(pdf_path: Path, full_text: str) -> str:
    """Extract device model/type number from filename and PDF content.

    Uses multiple strategies in priority order:
    1. Filename pattern matching (most reliable for Siemens, Vega, etc.)
    2. Explicit "Type:" / "Model:" / "Typ:" labels in PDF text
    3. Order-code-like patterns that look like model numbers
    """
    fname = pdf_path.stem

    # Strategy 1: Filename-based extraction
    # Siemens filenames: "Siemens_321-1BL00-0AA0" → model "321-1BL00-0AA0"
    _siemens_pat = re.search(r"(?:Siemens_)?(\d{3}[-.]\d[A-Za-z\d]{2,4}[-.]\d[A-Za-z\d]{2,4})", fname)
    if _siemens_pat:
        return _siemens_pat.group(1)

    # Vega filenames: "Vega_Puls_64" or "VEGAFLEX_81"
    _vega_pat = re.search(r"(?:Vega_|VEGA)?(?:Puls_\d+|VEGAFLEX_\d+|VEGAPULS_\d+)", fname, re.IGNORECASE)
    if _vega_pat:
        return _vega_pat.group(0).replace("_", " ")

    # Bürkert filenames: "0290_solenoid_valve" → "0290"
    _bkr_pat = re.search(r"^(\d{4})_", fname)
    if _bkr_pat:
        return _bkr_pat.group(1)

    # Yokogawa filenames: "AXF_magnetic_flowmeter" → "AXF"
    _ykw_pat = re.search(r"^([A-Z]{2,4}\d*)", fname)
    if _ykw_pat and len(_ykw_pat.group(1)) >= 2:
        return _ykw_pat.group(1)

    # WIKA filenames: "T16_temperature_transmitter" or "TIF50_TIF52"
    _wika_pat = re.search(r"^(T\d{2}|TIF\d{2,3})", fname)
    if _wika_pat:
        return _wika_pat.group(1)

    # Endress+Hauser: "Promass_P500", "Liquicap_M", "TR12" — stop before noise words
    _eh_pat = re.search(
        r"(Promass_[A-Z]?\d+|Liquicap_[A-Z]?\d*|Levelflex_[A-Z]?\d*|Micropilot_[A-Z]?\d*|"
        r"Cerabar_[A-Z]?\d*|Deltabar_[A-Z]?\d*|TR\d{2,4})",
        fname, re.IGNORECASE,
    )
    if _eh_pat:
        return _eh_pat.group(1).replace("_", " ")

    # Grundfos: "Magna3", "ALPHA1"
    _gfo_pat = re.search(r"(Magna\d+|ALPHA\d+)", fname, re.IGNORECASE)
    if _gfo_pat:
        return _gfo_pat.group(1)

    # Endress+Hauser: better pattern — stop at "Technical" or "Operating" suffixes
    _eh2_pat = re.search(
        r"(Promass_\w+|Liquicap_\w+|Levelflex_\w+|Micropilot_\w+|Cerabar_\w+|Deltabar_\w+)",
        fname, re.IGNORECASE,
    )
    if _eh2_pat:
        model = _eh2_pat.group(1).replace("_", " ")
        # Trim trailing noise words
        for _noise in [" Technical Information", " Operating Instructions",
                        " Technical Datasheet", " Brief"]:
            model = model.replace(_noise, "")
        return model.strip()

    # Krohne: "Optiflex_1300C" or "Optiflex_7200C"
    _kro_pat = re.search(r"(Optiflex_\d+[A-Z]?)", fname)
    if _kro_pat:
        return _kro_pat.group(1).replace("_", " ")

    # Strategy 2: Explicit Type/Model labels in PDF text (first 1000 chars)
    _header = full_text[:1000] if full_text else ""
    for _label_pat in [r"Type\s*:\s*(\S[^\n]{2,40})", r"Model\s*:\s*(\S[^\n]{2,40})",
                       r"Typ\s*:\s*(\S[^\n]{2,40})", r"Order code\s*:\s*(\S[^\n]{2,40})"]:
        _lm = re.search(_label_pat, _header, re.IGNORECASE)
        if _lm:
            _val = _lm.group(1).strip().rstrip(".,;")
            if _val and len(_val) >= 2:
                return _val

    # Strategy 3: Fallback — any short alphanumeric code in the first few lines
    for _line in _header.split("\n")[:8]:
        _line = _line.strip()
        # Look for patterns like "SM 321" or "6ES7321-..." in Siemens docs
        _code = re.search(r'(?:SM|FM|CP|IM)\s+\d{3}', _line)
        if _code:
            return _code.group(0)
        _code = re.search(r'(?:6ES7\d[\dA-Za-z\s\-.]+)\b', _line)
        if _code:
            return _code.group(0).strip()[:30]

    return ""


def _extract_manufacturer_smart(pdf_path: Path) -> str:
    """Extract manufacturer name using heuristic + path-based fallback.

    Strategy:
    1. Try PDF metadata (author field) — most reliable
    2. Try font-size/position heuristics
    3. If heuristic result looks like a common phrase (not a proper noun),
       fall back to path-based matching
    4. Always prefer path-based match when it conflicts with a generic phrase
    """
    heuristic = _extract_manufacturer(pdf_path)

    # Path-based fallback: check directory names and filename for known manufacturers
    path_str = str(pdf_path).lower()
    path_mfr = ""
    for key, canonical in _FALLBACK_MANUFACTURERS.items():
        if key in path_str:
            path_mfr = canonical
            break

    # If heuristic looks like a common English/German phrase rather than a
    # proper company name, prefer the path-based match.
    if heuristic:
        heuristic_lower = heuristic.lower()
        # Common datasheet header phrases that are NOT manufacturers
        _generic_phrases = {
            "data sheet", "technical information", "technical datasheet",
            "operating instructions", "valid as of version", "applications",
            "access", "grundfos print engine", "general information",
            "supply voltage", "process industry", "key features and benefits",
            "product function", "safety instructions", "functional safety",
            "brief overview", "special documentation", "installation",
            "description", "specifications", "overview",
        }
        # Words that signal a generic phrase, not a proper company name
        _generic_words = {
            "sheet", "information", "instructions", "general", "overview",
            "features", "benefits", "specifications", "industry", "process",
            "supply", "voltage", "product", "function", "installation",
            "description", "safety", "brief", "special", "documentation",
            "application", "version", "access", "valid",
        }

        # If the heuristic is entirely composed of generic words, it's not a company
        words = set(heuristic_lower.split())
        if heuristic_lower in _generic_phrases:
            return path_mfr or heuristic
        if words and all(w in _generic_words for w in words):
            return path_mfr or heuristic
        if len(words) == 1 and words.pop() in _generic_words:
            return path_mfr or heuristic

        # If we have a path-based match AND the heuristic doesn't contain
        # the path match, prefer the path match when heuristic is generic-looking
        if path_mfr and path_mfr.lower() not in heuristic_lower:
            # Heuristic doesn't match the directory name → might be wrong
            if any(w in _generic_words for w in words):
                return path_mfr

        return heuristic

    return path_mfr


# ── Main parser ────────────────────────────────────────────────────────────


def parse_datasheet(pdf_path: Path) -> dict[str, dict[str, str]]:
    """Parse a datasheet PDF into {section: {param: value}}.

    Automatically scores and filters by quality.  Returns {} if unreadable.
    """
    if fitz is None:
        return {}

    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return {}

    lines: list[str] = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                parts = [s.get("text", "").strip() for s in line.get("spans", [])]
                text = " ".join(p for p in parts if p)
                if text:
                    lines.append(text)
    doc.close()
    if not lines:
        return {}

    full_text = "\n".join(lines)
    result: dict[str, dict[str, str]] = {}

    # Identification
    ident = result.setdefault("Identification", {})

    # Order number — try manufacturer-specific patterns first (most reliable),
    # then fall back to the generic regex with smart noise filtering.
    _order = _extract_order_code(full_text, pdf_path)
    if _order:
        ident["Order_Number"] = _order

    # Model / type number — smart extraction from filename + PDF text
    _model = _extract_model(pdf_path, full_text)
    if _model:
        ident["Model"] = _model

    # Description from spare-part markers
    sm = _SPARE_RE.search(full_text)
    if sm:
        ident["Description"] = sm.group(1).strip()

    # If no description from spare-part markers, use the first substantive line
    if not ident.get("Description") and lines:
        for _line in lines[:10]:
            _lt = _line.strip()
            if _lt and len(_lt) > 15 and not _lt.startswith("©") and "http" not in _lt:
                _words = _lt.split()
                if len(_words) >= 3:
                    ident["Description"] = _lt[:200]
                    break

    # Manufacturer from PDF header (heuristic + path-based smart fallback)
    _mfr = _extract_manufacturer_smart(pdf_path)
    if _mfr:
        result.setdefault("Identification", {})["Manufacturer"] = _mfr

    # Device classification from description keywords
    _desc = result.get("Identification", {}).get("Description", "")
    if _desc:
        # Extract functional key phrases: "digital input", "analog output",
        # "Coriolis flowmeter", "level transmitter", "temperature", etc.
        _type_patterns = [
            (r"digital\s+input", "Digital Input Module"),
            (r"digital\s+output", "Digital Output Module"),
            (r"analog\s+input", "Analog Input Module"),
            (r"analog\s+output", "Analog Output Module"),
            (r"flowmeter|flow\s*meter|coriolis", "Flowmeter"),
            (r"level\s*(transmitter|sensor|measurement)|guided\s*radar|tdr", "Level Transmitter"),
            (r"temperature\s*(transmitter|sensor|thermometer)", "Temperature Transmitter"),
            (r"pressure\s*(transmitter|sensor|gauge)", "Pressure Transmitter"),
            (r"valve|proportional|solenoid", "Control Valve"),
            (r"pump|circulat", "Pump"),
            (r"positioner", "Positioner"),
        ]
        for pattern, classification in _type_patterns:
            if re.search(pattern, _desc, re.IGNORECASE):
                result.setdefault("Identification", {})["Device_Type"] = classification
                break

    # Stateful line-by-line parsing
    current_section = "General"
    pending_param: str | None = None

    for i, line in enumerate(lines):
        text = line.strip()
        if not text:
            continue
        if text.lower() in ("data sheet",):
            continue
        if _ORDER_RE.fullmatch(text) or _SPARE_RE.fullmatch(text):
            continue

        # Section header
        if _SECTION_RE.match(text) and text.lower() not in _NON_SECTION:
            nv = i + 1 < len(lines) and _is_value(lines[i + 1].strip())
            if nv or len(text) < 30:
                current_section = text
                pending_param = None
                continue

        # Parameter name
        if _is_param_name(text):
            if pending_param:
                result.setdefault(current_section, {})[pending_param] = ""
            pending_param = re.sub(r"^[●\-\—•]\s*", "", text).strip()
            continue

        # Value
        if pending_param and (_is_value(text) or len(text) < 50):
            result.setdefault(current_section, {})[pending_param] = text
            pending_param = None
            continue
        if pending_param and len(text) < 60:
            result.setdefault(current_section, {})[pending_param] = text
            pending_param = None

    if pending_param:
        result.setdefault(current_section, {})[pending_param] = ""

    # ── Second pass: universal key:value extraction from raw text ──
    # Also try with collapsed newlines (merges multi-line labels)
    _collapsed_text = re.sub(r"\n\s*", " ", full_text)
    _kv_extracted = _extract_kv_from_text(full_text)
    _kv_collapsed = _extract_kv_from_text(_collapsed_text)
    # Merge both, preferring the non-collapsed (more precise) versions
    for _k, _v in _kv_collapsed.items():
        if _k not in _kv_extracted:
            _kv_extracted[_k] = _v
    if _kv_extracted:
        # KV-extracted content comes from explicit label:value patterns,
        # so it's inherently more trustworthy. Use a lower quality bar.
        _kv_filtered = {
            k: v
            for k, v in _kv_extracted.items()
            if _param_value_quality(v) >= 0.15 and _param_name_quality(k) >= 0.1
        }
        if _kv_filtered:
            result["Specifications"] = _kv_filtered

    # ── TOC-based extraction: find parameter names from table of contents ──
    _toc_entries = _extract_toc_entries(full_text)
    if _toc_entries:
        _toc_kvs: dict[str, str] = {}
        for _label in _toc_entries:
            _val = _find_value_for_label(full_text, _label)
            if _val and _param_value_quality(_val) >= 0.2:
                _toc_kvs[_label] = _val
        if _toc_kvs:
            result["Specifications"] = result.get("Specifications", {})
            for _k, _v in _toc_kvs.items():
                if _k not in result["Specifications"]:
                    result["Specifications"][_k] = _v

    # ── Quality filtering ──
    scored = {s: _section_quality(s, p) for s, p in result.items()}
    total = len(scored) or 1
    high_q = sum(1 for v in scored.values() if v >= 0.4)
    narrative_ratio = 1.0 - (high_q / total)

    if narrative_ratio > 0.6:
        threshold = 0.55
    elif narrative_ratio > 0.4:
        threshold = 0.45
    elif narrative_ratio > 0.2:
        threshold = 0.35
    else:
        threshold = 0.0

    filtered: dict[str, dict[str, str]] = {}
    for sec, params in result.items():
        # Identification and Specifications always pass
        if sec not in ("Specifications", "Identification") and scored.get(sec, 0.0) < threshold:
            continue
        # Per-param filtering: lower bar for KV/TOC-extracted sections
        _is_spec_section = sec in ("Specifications", "Identification")
        keep = {}
        for n, v in params.items():
            vq = _param_value_quality(v)
            nq = _param_name_quality(n)
            _min_vq = 0.0 if _is_spec_section else 0.35
            _min_nq = 0.0 if _is_spec_section else 0.2
            if vq >= _min_vq and nq >= _min_nq:
                keep[n] = v
        if keep:
            filtered[sec] = keep

    # ── Deduplication ──
    seen: set[tuple[str, str]] = set()
    deduped: dict[str, dict[str, str]] = {}
    for sec, params in filtered.items():
        unique = {}
        for n, v in params.items():
            key = (n.lower().strip(), v.lower().strip())
            if key not in seen:
                seen.add(key)
                unique[n] = v
        if unique:
            deduped[sec] = unique

    return deduped


# ---------------------------------------------------------------------------
# LLM-based intelligent extraction
# ---------------------------------------------------------------------------


def _extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract all OCR-readable text from a PDF, one line per text span."""
    if fitz is None:
        return ""
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return ""
    lines: list[str] = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                parts = [s.get("text", "").strip() for s in line.get("spans", [])]
                text = " ".join(p for p in parts if p)
                if text:
                    lines.append(text)
    doc.close()
    return "\n".join(lines)


_LLM_DATASHEET_PROMPT = """You are an industrial engineering datasheet analyst. Extract structured
technical information from the OCR text of a device datasheet below.

Extract:
1. **manufacturer**: Company that made the device (e.g. Siemens, Endress+Hauser, WIKA)
2. **model**: Device model/type number (e.g. SITRANS P320, PROMAG 10)
3. **order_number**: Order code or part number (e.g. 7MF0300-1TE01-5AF2)
4. **device_type**: Classification (e.g. Pressure Transmitter, Flowmeter,
   Temperature Transmitter, Level Transmitter, Digital Input Module,
   Analog Output Module, Control Valve, Pump, Positioner, or specify other)
5. **parameters**: List of MEASURABLE technical parameters ONLY. Each parameter has:
   - section: the section/group it belongs to (e.g. "Input", "Output", "Power Supply",
     "Mechanical", "Ambient Conditions", "Performance" — or "General" if unclear)
   - name: parameter name (e.g. "Measuring range", "Output signal", "Supply voltage")
   - value: the parameter value as a string (e.g. "0-16 bar", "4-20 mA", "24 V DC")
   - unit: the unit of measurement if present (e.g. "bar", "mA", "V", "mm", "degC", "kg" — or null)

CRITICAL RULES:
- ONLY extract parameters that have a MEASURABLE numeric value with a unit, OR
  a specific technical specification (e.g. material type, protection class, accuracy class).
- Do NOT extract: installation instructions, safety warnings, chapter headings,
  table-of-contents entries, operational descriptions, troubleshooting guides,
  warranty text, or any narrative/descriptive paragraphs.
- If the text is a long document (manual/operating instructions), focus ONLY on
  the technical specifications section (usually near the beginning or in a
  dedicated "Technical Data" chapter). Skip all other chapters.
- Limit parameters to at most 50 of the most important ones.
- Set confidence LOW (<0.5) for anything that isn't clearly a technical spec.
- Do NOT invent or guess values.
- Do NOT output meta-commentary like "Not explicitly stated", "Not provided",
  "Not mentioned". Omit fields entirely when absent.
- For each field, provide a confidence score (0.0-1.0).

Return ONLY valid JSON (no markdown fences):
{
  "manufacturer": {"value": "...", "confidence": 0.95},
  "model": {"value": "...", "confidence": 0.9},
  "order_number": {"value": "...", "confidence": 0.9},
  "device_type": {"value": "...", "confidence": 0.9},
  "parameters": [
    {"section": "...", "name": "...", "value": "...", "unit": "...", "confidence": 0.9}
  ]
}

OCR text from datasheet:
"""


# Cache for text-based LLM datasheet parsing (by PDF path).
_llm_datasheet_cache: dict[str, dict[str, dict[str, str]]] = {}


def parse_datasheet_llm(
    pdf_path: Path,
    llm_client: Any | None = None,
    *,
    max_text_chars: int = 8000,
) -> dict[str, dict[str, str]]:
    """Parse a datasheet PDF using LLM semantic extraction.  Cached by PDF path.

    Args:
        pdf_path: Path to the datasheet PDF.
        llm_client: An :class:`OpenAICompatibleLLMClient` instance.  If ``None``
            or unavailable, returns ``{}``.
        max_text_chars: Truncate OCR text to this many characters before
            sending to the LLM (avoids token limits).

    Returns:
        ``{section: {param_name: param_value}}``, same format as
        :func:`parse_datasheet`.  Also includes special sections
        ``Identification`` (manufacturer, model, order_number, device_type)
        and ``Specifications`` (all extracted parameters merged).
    """
    from pathlib import Path as _P
    cache_key = str(pdf_path)  # relative path from caller
    abs_path = _P.cwd() / pdf_path if not str(pdf_path).startswith("/") else _P(pdf_path)
    if cache_key in _llm_datasheet_cache:
        return _llm_datasheet_cache[cache_key]

    if llm_client is None or not llm_client.available():
        return {}

    text = _extract_text_from_pdf(abs_path)
    if not text.strip():
        _llm_datasheet_cache[cache_key] = {}
        return {}

    # Truncate to stay within reasonable token limits
    if len(text) > max_text_chars:
        text = text[:max_text_chars] + "\n[... text truncated ...]"

    try:
        response = llm_client.chat_json(_LLM_DATASHEET_PROMPT + "\n" + text)
    except Exception:
        return {}

    if not response or not isinstance(response, dict):
        return {}

    result: dict[str, dict[str, str]] = {}
    ident: dict[str, str] = {}

    # Extract top-level fields
    for key in ("manufacturer", "model", "order_number", "device_type"):
        entry = response.get(key)
        if isinstance(entry, dict) and entry.get("value"):
            conf = float(entry.get("confidence", 0))
            if conf >= 0.5:
                ident[key.title() if key != "device_type" else "Device_Type"] = str(entry["value"])

    if ident:
        result["Identification"] = ident

    # Extract parameters — group by section, filter by LLM confidence
    params = response.get("parameters", [])
    if isinstance(params, list):
        sections: dict[str, dict[str, str]] = {}
        _param_count = 0
        # Sort by confidence descending — keep only the best
        _sorted_params = sorted(
            (p for p in params if isinstance(p, dict)),
            key=lambda p: float(p.get("confidence", 0)),
            reverse=True,
        )
        for p in _sorted_params:
            name = str(p.get("name", "")).strip()
            value = str(p.get("value", "")).strip()
            conf = float(p.get("confidence", 0))
            # Smart filtering: confidence-based, not hardcoded length limits.
            # The LLM prompt instructs low confidence for non-spec text, so
            # a 0.5 threshold naturally filters out narrative content.
            if not name or not value or conf < 0.5:
                continue
            # Cap at 50 best parameters to prevent explosion from long docs
            if _param_count >= 50:
                break
            _param_count += 1
            # Build value with unit if present
            unit = p.get("unit")
            if unit and str(unit).strip():
                value = f"{value} {str(unit).strip()}"
            section = str(p.get("section", "General")).strip() or "General"
            sections.setdefault(section, {})[name] = value
        result.update(sections)

    # Strip PUA glyphs & boilerplate from all values before caching
    for section in result:
        for k, v in list(result[section].items()):
            sv = str(v)
            sv = ''.join(c for c in sv if not (0xE000 <= ord(c) <= 0xF8FF))
            sv = sv.strip()
            sv_lower = sv.lower()
            if not sv or sv_lower in ("none", "n/a", "-"):
                del result[section][k]
            elif any(p in sv_lower for p in _BOILERPLATE_PATTERNS):
                del result[section][k]
            else:
                result[section][k] = sv
    _llm_datasheet_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Unit separation utility
# ---------------------------------------------------------------------------

# Units recognized in engineering datasheets.  Ordered longest-first so that
# compound units ("Nm", "kVA") match before simple ones ("m", "A").
_KNOWN_UNITS = sorted(
    [
        # Compound / derived (longest first)
        "kg/m³", "kg/m3", "g/cm³", "g/cm3", "kg/cm²", "g/ml", "mg/l",
        "kVA", "MVA", "kVAR", "MVAR", "kPa", "MPa", "GPa", "hPa",
        "mbar", "\u03bcbar", "\u00b5bar", "m³/h", "m³/s", "l/min", "l/s", "ml/min",
        "kg/h", "kg/s", "t/h", "g/min", "Nm", "kNm", "MNm",
        "rpm", "rps", "rad/s", "deg/s", "Hz", "kHz", "MHz", "GHz",
        "mA", "\u03bcA", "\u00b5A", "nA", "kA", "MV", "kV", "mV", "\u03bcV", "\u00b5V",
        "kW", "MW", "GW", "mW", "\u03bcW", "\u00b5W",
        "k\u03a9", "M\u03a9", "m\u03a9", "\u03bc\u03a9", "\u00b5\u03a9",
        "k\u2126", "M\u2126", "m\u2126",
        "km", "dm", "cm", "mm", "\u03bcm", "\u00b5m", "nm", "mil",
        "kg", "mg", "\u03bcg", "\u00b5g", "t", "g", "lb", "oz",
        "m³", "cm³", "mm³", "l", "ml", "hl",
        "bar", "psi", "ksi", "Pa",
        "\u00b0C", "\u00b0F", "\u00b0R",
        "s", "ms", "\u03bcs", "\u00b5s", "ns", "min", "h", "d",
        "V", "A", "W", "\u03a9", "\u2126", "F", "H", "S", "T", "J", "N", "C", "K",
        "VA", "var", "Wh", "Ah", "dB", "dBm", "dBA", "ppm", "ppb",
        "%", "\u2030", "bps", "baud", "inch", "ft", "yd",
        "m", "USD", "EUR", "CHF", "GBP",
        "DC", "AC",
    ],
    key=len,
    reverse=True,
)



def split_value_unit(value: str) -> tuple[str, str]:
    """Split a parameter value into (value, unit).

    Checks LLM cache first (disk-persistent, no API calls on cache hit).
    Falls back to iterative regex matching for uncached values or when
    no LLM client is configured.
    """
    # LLM cache hit — instant, no API call
    if value in _split_value_unit_llm_cache:
        return _split_value_unit_llm_cache[value]

    # Fallback to regex matching
    v = value.strip()

    # Pure bracket-only value: "[g/cm³]" means the value IS the unit
    if v.startswith("[") and v.endswith("]") and len(v) > 2:
        inner = v[1:-1].strip()
        # Try exact match first (longest unit wins)
        for unit in _KNOWN_UNITS:
            if inner == unit:
                return "", unit
        # Try prefix match: "[unit / variant]"
        for unit in _KNOWN_UNITS:
            if inner.startswith(unit + " ") or inner.startswith(unit + "/"):
                return "", unit
        # Try suffix: "[value unit]"
        for unit in _KNOWN_UNITS:
            if inner.endswith(" " + unit):
                return inner[:-len(unit)-1].strip(), unit

    # Strip trailing noise (preserve closing paren)
    while v and v[-1] in "/,.;:–—":
        v = v[:-1].strip()

    # Mid-sentence unit: "0 to 2 V DC. (text)" or "0 to 2 V DC (text)"
    # Try to find unit before period/paren, preferring compound units
    for sep in [". (", ".(", " (", "("]:
        idx = v.find(sep)
        if idx > 0:
            before = v[:idx].strip()
            # Try each unit, looking for longest match first
            best_unit = ""
            best_val = ""
            for unit in _KNOWN_UNITS:
                if before.endswith(" " + unit):
                    # Check for compound: "V DC" where both "V" and "DC" are units
                    remainder = before[:-len(unit)-1]
                    for u2 in _KNOWN_UNITS:
                        if remainder.endswith(" " + u2):
                            # Compound unit found: "V DC"
                            compound = u2 + " " + unit
                            val = remainder[:-len(u2)-1].strip()
                            if val and len(compound) > len(best_unit):
                                best_unit = compound
                                best_val = val
                    # Single unit match
                    val = before[:-len(unit)-1].strip()
                    if val and len(unit) > len(best_unit):
                        best_unit = unit
                        best_val = val
            if best_unit:
                return best_val, best_unit

    # Iterative suffix matching — try each unit (longest first)
    for unit in _KNOWN_UNITS:
        suffix = " " + unit
        if v.endswith(suffix):
            val = v[:-len(suffix)].strip()
            if val:
                return val, unit

    # Parenthesised / bracketed: "value (unit)", "value [unit]"
    for unit in _KNOWN_UNITS:
        for fmt in [" ({})", " [{}]", "[{}]"]:
            suffix = fmt.format(unit)
            if v.endswith(suffix):
                val = v[:-len(suffix)].strip()
                if val:
                    return val, unit

    # Trailing close-paren: "L+ (-53 V)" → extract unit from parentheses
    if v.endswith(")"):
        depth = 0
        paren_start = -1
        for i in range(len(v) - 1, -1, -1):
            if v[i] == ")":
                depth += 1
            elif v[i] == "(":
                depth -= 1
                if depth == 0:
                    paren_start = i
                    break
        if paren_start >= 0:
            inner = v[paren_start + 1:-1].strip()
            for unit in _KNOWN_UNITS:
                if inner.endswith(" " + unit) or inner == unit:
                    val = v[:paren_start].strip().rstrip("(").strip()
                    if val:
                        return val, unit

    return v, ""


# Cache for LLM-based value/unit splitting to avoid repeated API calls.
_split_value_unit_llm_cache: dict[str, tuple[str, str]] = {}


def split_value_unit_llm_batch(
    values: list[str],
    llm_client: Any | None = None,
) -> dict[str, tuple[str, str]]:
    """Split multiple values into (value, unit) using LLM - replaces 133 hardcoded units.

    One LLM call handles all values.  Results are cached so repeated values
    don't trigger new calls.  Falls back to :func:`split_value_unit` when
    no LLM client is available.
    """
    if not values:
        return {}
    if llm_client is None or not hasattr(llm_client, "available") or not llm_client.available():
        return {v: split_value_unit(v) for v in values}

    import json as _json
    uncached = [v for v in values if v not in _split_value_unit_llm_cache]
    if not uncached:
        return {v: _split_value_unit_llm_cache[v] for v in values}

    try:
        response = llm_client.chat_json(
            "You split engineering values into numeric value and unit. Return ONLY valid JSON.",
            f"Split each value into value and unit.\nValues: {_json.dumps(uncached)}\n"
            'Return JSON: {{"splits": {{"24 V DC": {{"value": "24", "unit": "V DC"}}, ...}}}}',
        )
        if isinstance(response, dict):
            splits = response.get("splits", {})
            if isinstance(splits, dict):
                for val, info in splits.items():
                    if isinstance(info, dict):
                        v = str(info.get("value", "") or "")
                        u = str(info.get("unit", "") or "")
                        _split_value_unit_llm_cache[str(val)] = (v, u)
    except Exception:
        pass

    return {v: _split_value_unit_llm_cache.get(v, split_value_unit(v)) for v in values}


def split_value_unit_with_name(value: str, param_name: str = "") -> tuple[str, str]:
    """Split parameter value into (value, unit), inferring unit from name.

    When the value itself doesn't contain a separable unit suffix (e.g. pure
    numbers like "33.4" or ranges like "100...4000"), this function looks at
    the parameter name for unit hints: "WEIGHT kg (lb)" → unit "kg".

    This is smarter than suffix-only extraction because many datasheets put
    the unit in the parameter name rather than next to every value.
    """
    # First try direct value-based extraction
    val, unit = split_value_unit(value)
    if unit:
        return val, unit

    if not param_name:
        return val, ""

    # Only infer units from names when the value looks like a numeric
    # measurement: digits, dots, commas, spaces, dashes, and range symbols.
    # Values containing letters (other than known unit suffixes already
    # stripped) are likely codes or descriptions, not measurements.
    _numeric_pattern = re.compile(r'^[\d.,\s–…\-—–+eE]+$')
    if not _numeric_pattern.match(val.strip()):
        return val, ""

    # Known unit keywords that appear in parameter names, with their canonical
    # unit symbols.  Ordered by specificity (longest patterns first).
    _name_unit_hints = [
        # Weight / mass
        ("weight", "kg"), ("gewicht", "kg"), ("masse", "kg"),
        ("kg (lb)", "kg"), ("(kg)", "kg"), (" kg", "kg"),
        # Length / distance
        ("length", "mm"), ("länge", "mm"), ("lange", "mm"),
        ("height", "mm"), ("höhe", "mm"), ("hohe", "mm"),
        ("width", "mm"), ("breite", "mm"),
        ("depth", "mm"), ("tiefe", "mm"),
        ("distance", "mm"), ("abstand", "mm"),
        ("diameter", "mm"), ("durchmesser", "mm"), ("Ø", "mm"),
        ("radius", "mm"),
        ("rod length", "mm"), ("probe length", "mm"),
        ("total length", "mm"), ("active rod length", "mm"),
        # Thread / pipe sizes (often in inches or mm)
        ("npt", "inch"), ("rc ", "inch"), ("g ", "inch"),
        # Volume
        ("volume", "m³"), ("volumen", "m³"),
        # Area
        ("area", "mm²"), ("fläche", "mm²"),
        # Electrical
        ("voltage", "V"), ("spannung", "V"),
        ("current", "A"), ("strom", "A"),
        ("power", "W"), ("leistung", "W"),
        ("resistance", "Ω"), ("widerstand", "Ω"),
        # Time
        ("time", "s"), ("zeit", "s"),
        ("duration", "s"), ("dauer", "s"),
        # Speed
        ("speed", "rpm"), ("drehzahl", "rpm"),
        ("velocity", "m/s"),
        # Temperature
        ("temperature", "°C"), ("temperatur", "°C"),
        ("temp", "°C"),
        # Pressure
        ("pressure", "bar"), ("druck", "bar"),
        # Flow
        ("flow", "m³/h"), ("durchfluss", "m³/h"),
        # Generic dimension (often mm in mechanical datasheets)
        ("dimension", "mm"), ("abmessung", "mm"),
        ("size", "mm"), ("größe", "mm"),
        ("inches", "inch"), ("(in)", "inch"), (" in)", "inch"),
    ]

    name_lower = param_name.lower().strip()
    for pattern, unit_symbol in _name_unit_hints:
        if pattern in name_lower:
            return val, unit_symbol

    return val, ""


# Cache for VLM datasheet parsing results (by PDF path) to avoid repeated
# API calls when the same PDF is parsed multiple times during export.
_vlm_datasheet_cache: dict[str, dict[str, dict[str, str]]] = {}


def parse_datasheet_vlm(
    pdf_path: Path,
    llm_config: Any | None = None,
    max_pages: int = 2,
) -> dict[str, dict[str, str]]:
    """Parse a datasheet using VLM — reads page images to extract structured data.

    Renders the first *max_pages* of the PDF as images and sends them to the
    VLM.  The model visually understands tables, headers, and layouts —
    no regex hardcoding, no OCR text extraction needed.

    Results are cached by PDF path so repeated calls for the same file
    (common during export) don't trigger redundant VLM API calls.

    Returns ``{section: {param: value}}``, typically with sections like
    ``"Identification"``, ``"Technical"``, ``"Electrical"``, etc.
    """
    from pathlib import Path as _P
    cache_key = str(pdf_path)  # relative path from caller
    abs_path = _P.cwd() / pdf_path if not str(pdf_path).startswith("/") else _P(pdf_path)
    if cache_key in _vlm_datasheet_cache:
        return _vlm_datasheet_cache[cache_key]

    if llm_config is None:
        return {}
    try:
        import hashlib as _hashlib
        from iev4pi_transformation_tool.core.disk_cache import DiskDict
        stat = abs_path.stat()
        disk_cache_key = _hashlib.sha256(
            json.dumps({
                "kind": "datasheet_vlm",
                "path": str(abs_path),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "base_url": getattr(llm_config, "base_url", ""),
                "model": getattr(llm_config, "chat_model", ""),
                "max_pages": max_pages,
            }, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        disk_cache = DiskDict("vlm_datasheet_api")
        cached = disk_cache.get(disk_cache_key)
        if isinstance(cached, dict):
            normalized = {
                str(section): {str(k): str(v) for k, v in params.items()}
                for section, params in cached.items()
                if isinstance(params, dict)
            }
            _vlm_datasheet_cache[cache_key] = normalized
            return normalized
    except Exception:
        disk_cache = None
        disk_cache_key = ""
    try:
        import fitz, base64, requests, json as _json, re as _re
    except ImportError:
        return {}

    # Render first N pages to base64 images
    images: list[str] = []
    try:
        doc = fitz.open(str(abs_path))
        for i in range(min(max_pages, len(doc))):
            pix = doc[i].get_pixmap(dpi=150)
            images.append(base64.b64encode(pix.tobytes("png")).decode())
        doc.close()
    except Exception:
        return {}
    if not images:
        return {}

    # Build VLM message with images + prompt
    content_parts: list[dict] = []
    for img_b64 in images:
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
        })
    content_parts.append({
        "type": "text",
        "text": (
            "You are parsing an industrial component datasheet (Gerätedatenblatt). "
            "Extract structured technical data from these page images.\n\n"
            "Return a JSON object with these sections:\n"
            "- Identification: manufacturer, model, order_number, device_type, "
            "description, eclass, supply_voltage, power_consumption\n"
            "- Parameters: ALL measurable technical parameters as name→value "
            "(include units in values, e.g. \"24 V\", \"15 mA\").  Extract up to "
            "50 most important parameters.\n"
            "- Connections: connector_type, signal_type, bus_protocol, "
            "front_connector, terminal_count\n"
            "- Physical: dimensions, weight, material, mounting_type, "
            "protection_class, ambient_temperature\n\n"
            "CRITICAL RULES:\n"
            "- Return ONLY values explicitly stated in the document.\n"
            "- If you cannot find a value, OMIT the field entirely — do NOT "
            "return \"Not specified\", \"N/A\", \"-\", or any placeholder text.\n"
            "- Do NOT output meta-commentary like \"Not explicitly stated\", "
            "\"Not provided in the text\", or similar phrases.\n"
            "- An empty/missing field means the key should not appear in the JSON.\n"
            "- Be precise with numbers and units.\n\n"
            'Format: {"Identification": {"manufacturer": "Siemens", ...}, '
            '"Parameters": {"Supply voltage": "24 V DC", ...}, ...}'
        ),
    })

    try:
        import requests as _requests
        r = _requests.post(
            f"{llm_config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {llm_config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": llm_config.chat_model,
                "max_tokens": 2000,
                "temperature": 0.0,
                "messages": [{"role": "user", "content": content_parts}],
            },
            timeout=120,
        )
        raw = r.json()["choices"][0]["message"]["content"]
    except Exception:
        return {}

    # Parse JSON (may be in markdown code block)
    m = _re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        result = _json.loads(m.group())
    except _json.JSONDecodeError:
        return {}

    # Convert to {section: {param: value}} format, strip PUA glyphs & boilerplate
    out: dict[str, dict[str, str]] = {}
    _BOILERPLATE_PATTERNS = [
        "not explicitly stated", "not provided", "not mentioned",
        "not specified", "visit product website",
    ]
    for section, params in result.items():
        if isinstance(params, dict):
            cleaned: dict[str, str] = {}
            for k, v in params.items():
                if not v:
                    continue
                sv = str(v)
                # Strip Unicode PUA glyphs (e.g. PDF link icons)
                sv = ''.join(c for c in sv if not (0xE000 <= ord(c) <= 0xF8FF))
                sv = sv.strip()
                sv_lower = sv.lower()
                if not sv or sv_lower in ("none", "n/a", "-"):
                    continue
                if any(p in sv_lower for p in _BOILERPLATE_PATTERNS):
                    continue
                cleaned[str(k)] = sv
            out[str(section)] = cleaned
    # Cache the result — same PDF parsed multiple times during export
    _vlm_datasheet_cache[cache_key] = out
    try:
        if disk_cache is not None and disk_cache_key:
            disk_cache[disk_cache_key] = out
    except Exception:
        pass
    return out


def parse_datasheet_smart(
    pdf_path: Path,
    llm_client: Any | None = None,
) -> dict[str, dict[str, str]]:
    """Parse a datasheet using VLM + LLM, with regex fallback.

    When an LLM client is available, uses VLM (page images) + text LLM
    (OCR text) — no regex hardcoding, adapts to any datasheet layout.
    The regex-based :func:`parse_datasheet` is kept only as a fallback
    when no LLM is available.

    Returns the merged ``{section: {param: value}}`` dict.
    """
    result: dict[str, dict[str, str]] = {}

    if llm_client is not None and hasattr(llm_client, "config"):
        # VLM primary — reads page images, handles any layout
        vlm_result = parse_datasheet_vlm(pdf_path, llm_client.config)
        for section, params in vlm_result.items():
            result.setdefault(section, {}).update(params)

        # LLM text — fills gaps VLM may have missed
        llm_result = parse_datasheet_llm(pdf_path, llm_client)
        if llm_result:
            for section, params in llm_result.items():
                existing = result.setdefault(section, {})
                for name, value in params.items():
                    if name.lower().strip() not in {k.lower().strip() for k in existing}:
                        existing[name] = value

        return result

    # No LLM available — fall back to regex heuristic parser
    return parse_datasheet(pdf_path)
