"""Title-block metadata extractor for Stellenplan PDFs.

Uses VLM (visual language model) to read the title block directly from the
page image — no spatial separation, no hardcoded regexes, no heuristics.
A minimal regex safety net catches the rare VLM mistake on revision_entry.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

try:
    import fitz  # type: ignore
except ImportError:
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        fitz = None  # type: ignore


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _page_to_base64(pdf_path: Path, dpi: int = 150) -> str | None:
    """Render first page of *pdf_path* to a base64-encoded PNG."""
    if fitz is None:
        return None
    try:
        doc = fitz.open(str(pdf_path))
        pix = doc[0].get_pixmap(dpi=dpi)
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        doc.close()
        return b64
    except Exception:
        return None


def _page_text(pdf_path: Path) -> str:
    """Extract full text from the first page."""
    if fitz is None:
        return ""
    try:
        doc = fitz.open(str(pdf_path))
        text = doc[0].get_text()
        doc.close()
        return text
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# VLM-based extraction — primary path
# ---------------------------------------------------------------------------

_VLM_TITLE_BLOCK_PROMPT = (
    "Look at this engineering document page (Stellenplan / instrument list). "
    "The page has a title block (usually at the bottom or top-right) and a "
    "grid with component data.\n\n"
    "Extract ONLY the title block metadata fields:\n"
    "- revision_entry: revision number (e.g. \"001\", \"002\")\n"
    "- revision_date: date in DD.MM.YYYY format\n"
    "- revision_name: author/editor name (e.g. \"MARTINA\")\n"
    "- erstellt: creation date (DD.MM.YYYY)\n"
    "- bearb: editor/author name (NOT a description like \"-\")\n"
    "- projekt: project name (e.g. \"Technikumsanlage Pumpwerk\")\n"
    "- position: IEC plant designator (e.g. \"=0.H1.T1\")\n"
    "- dokument: document designation\n"
    "- norm: technical standard referenced (e.g. \"DIN EN 60751\", \"ISO 9001\")\n\n"
    "IMPORTANT: \"-\" is a placeholder/dash meaning \"no description\", "
    "NOT a person's name.  If the editor field shows \"-\", look nearby "
    "for the ACTUAL person name (usually an uppercase word like \"MARTINA\").\n\n"
    'Return ONLY a JSON object: {"fields": {"revision_entry": "001", ...}}'
)


def _extract_title_block_vlm(pdf_path: Path, llm_config) -> dict[str, str]:
    """Extract title block using VLM — reads the page image directly."""
    img_b64 = _page_to_base64(pdf_path)
    if not img_b64:
        return {}
    try:
        import hashlib
        from iev4pi_transformation_tool.core.disk_cache import DiskDict
        stat = pdf_path.stat()
        cache_key = hashlib.sha256(json.dumps({
            "kind": "stellenplan_title_block_vlm",
            "path": str(pdf_path.resolve()),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "base_url": getattr(llm_config, "base_url", ""),
            "model": getattr(llm_config, "chat_model", ""),
            "prompt": _VLM_TITLE_BLOCK_PROMPT,
            "image_sha256": hashlib.sha256(img_b64.encode("ascii")).hexdigest(),
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        disk_cache = DiskDict("vlm_stellenplan_title_block_api")
        cached = disk_cache.get(cache_key)
        if isinstance(cached, dict):
            return {str(k): str(v).strip() for k, v in cached.items() if v and str(v).strip()}
    except Exception:
        disk_cache = None
        cache_key = ""

    import requests
    try:
        r = requests.post(
            f"{llm_config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {llm_config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": llm_config.chat_model,
                "max_tokens": 400,
                "temperature": 0.0,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                        {"type": "text", "text": _VLM_TITLE_BLOCK_PROMPT},
                    ],
                }],
            },
            timeout=60,
        )
        content = r.json()["choices"][0]["message"]["content"]
    except Exception:
        return {}

    # Parse JSON (may be wrapped in markdown code block)
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        return {}
    try:
        parsed = json.loads(m.group())
    except json.JSONDecodeError:
        return {}
    fields = parsed.get("fields", {})
    result = {k: str(v).strip() for k, v in fields.items() if v and str(v).strip()}
    try:
        if result and disk_cache is not None and cache_key:
            disk_cache[cache_key] = result
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Text-based LLM extraction — secondary path
# ---------------------------------------------------------------------------

_TEXT_TITLE_BLOCK_PROMPT = (
    "You are extracting metadata from an engineering document title block. "
    "The text below comes from the title-block area of the page.\n\n"
    "Extract these fields if present:\n"
    "- revision_entry: revision number (e.g. \"001\")\n"
    "- revision_date: date in DD.MM.YYYY format\n"
    "- revision_name: author/editor name (e.g. \"MARTINA\")\n"
    "- erstellt: creation date\n"
    "- bearb: editor/author name\n"
    "- projekt: project name\n"
    "- position: IEC plant designator (e.g. \"=0.H1.T1\")\n"
    "- dokument: document designation\n"
    "- norm: technical standard (e.g. \"DIN EN 60751\", \"ISO 9001\")\n\n"
    "CRITICAL: \"-\" is a placeholder meaning \"none\", not a person name. "
    "Return ONLY actual values, not labels.\n\n"
    "Document text:\n{text}\n\n"
    'Return JSON: {"fields": {"erstellt": "01.04.2009", ...}}'
)


def _extract_title_block_text_llm(text: str, llm_client: object) -> dict[str, str]:
    """Extract title block fields from OCR text using LLM."""
    try:
        response = llm_client.chat_json(
            "You are an engineering document metadata extractor. Return ONLY valid JSON.",
            _TEXT_TITLE_BLOCK_PROMPT.format(text=text[:3000]),
        )
        if isinstance(response, dict):
            fields = response.get("fields", {})
            if isinstance(fields, dict):
                return {k: str(v) for k, v in fields.items() if v and str(v).strip()}
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Regex extraction — fallback (no LLM available)
# ---------------------------------------------------------------------------

def _extract_regex(text: str) -> dict[str, str]:
    """Regex-based extraction — only truly generic patterns.

    No project-specific names, IEC designators, or document numbers.
    Those are handled by the VLM/LLM paths.  This fallback catches
    only the unambiguous, format-independent patterns.
    """
    result: dict[str, str] = {}

    # Revision number: "001", "002" — unambiguous in German engineering PDFs
    m = re.search(r"\b(00[12])\b", text)
    if m:
        result["revision_entry"] = m.group(1)

    # Date: DD.MM.YYYY — universal format
    m = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", text)
    if m:
        result["revision_date"] = m.group(1)
        result["erstellt"] = m.group(1)

    # Person name: 4-15 uppercase letters, excluding known non-name tokens
    _NAME_EXCLUDE = {"COMOS", "ECLASS", "DEXPI", "IEC", "MRS", "GEPR", "NORM",
                     "BEARB", "ERSTELLT", "DATUM", "NAMUR", "PROFIBUS",
                     "FOUNDATION", "FIELDBUS", "ETHERNET"}
    for _nm in re.finditer(r"\b([A-Z]{4,15})\b", text):
        _candidate = _nm.group(1)
        if _candidate not in _NAME_EXCLUDE:
            result["revision_name"] = _candidate
            result["bearb"] = _candidate
            break

    # Generic IEC plant designator: starts with "=" followed by digits/dots
    m = re.search(r"=\s*\d+\.[A-Z]\d+(?:\.[A-Z]\d+)*", text)
    if m:
        result["position"] = _clean(m.group(0))

    # Project name: "Projekt:" label followed by text
    m = re.search(r"Projekt:\s*(\S.{0,60})", text, re.IGNORECASE)
    if m:
        result["projekt"] = _clean(m.group(1))

    # Technical standard: DIN/ISO/IEC/EN/VDI/VDE followed by number
    m = re.search(
        r'\b(DIN\s*(?:EN\s*)?\d+(?:[./-]\d+)?'
        r'|ISO\s*\d+(?:[.:/-]\d+)*'
        r'|IEC\s*\d+(?:[./-]\d+)?'
        r'|EN\s*\d+(?:[./-]\d+)?'
        r'|VDI\s*\d+'
        r'|VDE\s*\d+)',
        text, re.IGNORECASE,
    )
    if m:
        result["norm"] = m.group(0).strip()

    return {k: v for k, v in result.items() if v and (len(v) > 1 or v == "-")}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_title_block(pdf_path: Path, llm_client: object | None = None) -> dict[str, str]:
    """Extract title-block metadata from a Stellenplan PDF.

    Tries three paths in order:
    1. VLM — reads the page image directly, understands visual layout
    2. Text LLM — sends OCR text to the chat model
    3. Regex — fast, reliable pattern matching for standard fields

    Results are disk-cached per PDF path.
    """
    from iev4pi_transformation_tool.core.disk_cache import DiskDict
    _tb_cache = DiskDict("stellenplan_title_block")
    cache_key = str(pdf_path)
    if cache_key in _tb_cache:
        cached = _tb_cache[cache_key]
        if isinstance(cached, dict):
            return {str(k): str(v) for k, v in cached.items()}

    if llm_client is not None and hasattr(llm_client, "available") and llm_client.available():
        # Path 1: VLM — best quality, handles any layout
        result = _extract_title_block_vlm(pdf_path, llm_client.config)

        # Path 2: if VLM failed, try text-based LLM
        if not result:
            text = _page_text(pdf_path)
            if text:
                result = _extract_title_block_text_llm(text, llm_client)

        # Regex safety net: revision_entry has an unambiguous pattern
        # ("001"/"002") that the OCR text catches even when VLM/LLM miss it.
        if not result.get("revision_entry"):
            full_text = _page_text(pdf_path)
            if full_text:
                regex_result = _extract_regex(full_text)
                for key in ("revision_entry", "bearb", "erstellt", "revision_date",
                            "revision_name", "projekt", "position", "dokument", "norm"):
                    if not result.get(key) and regex_result.get(key):
                        result[key] = regex_result[key]

        if result:
            _tb_cache[cache_key] = result
            return result

    # No LLM available — regex only
    text = _page_text(pdf_path)
    return _extract_regex(text) if text else {}
