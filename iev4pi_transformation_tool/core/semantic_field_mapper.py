"""Semantic field-to-column mapper for Excel template filling.

Maps extracted field names to template column names using a three-tier
strategy:

1. **Exact synonym lookup** — fast, free, handles ~80% of cases.
   Pre-built mapping of common engineering terms across English, German,
   and variant spellings.

2. **Embedding similarity** — uses the configured embedding model
   (e.g. ``local-hash-768``) to find the closest template column for
   an unrecognised field name.

3. **LLM semantic match** — when embedding confidence is below threshold,
   asks the LLM to choose the best column.  Highest accuracy, highest cost.
"""

from __future__ import annotations

from typing import Any

from iev4pi_transformation_tool.core.utils import cosine_similarity


# ---------------------------------------------------------------------------
# Tier-1: exact synonym dictionary
# ---------------------------------------------------------------------------
# Keys are canonical (lowercase, underscored) field names that the extractor
# might produce.  Values are lists of template column header variants (also
# lowercase) that could match.
#
# Each list is ordered from best → acceptable match.  The first match found
# in the template column set wins.

_EXACT_SYNONYMS: dict[str, list[str]] = {
    # --- Document-level fields
    "document_id": ["document_id", "document id", "doc_id"],
    "document": ["document", "document_name", "filename", "file_name", "source_file"],
    "index": ["index", "idx", "row_index", "row_number"],
    "semantic_id": ["semanticid", "semantic_id", "semantic id", "eclass_irdi"],

    # --- Device / Instrument identification
    "tag": ["akz", "tagname", "tag_name", "tag", "device_id", "instrument_id"],
    "akz": ["akz", "tagname", "tag_name", "tag"],
    "canonical_tag": ["akz_canonical", "canonicaltag", "canonical_tag"],
    "device_id": ["device_id", "deviceid", "device_id", "instrument_id"],
    "instrument_id": ["instrument_id", "device_id", "deviceid"],

    # --- Manufacturer / Vendor
    "manufacturer": ["manufacturer", "hersteller", "vendor", "source_vendor", "make"],
    "hersteller": ["manufacturer", "hersteller", "vendor", "source_vendor"],
    "vendor": ["vendor", "source_vendor", "manufacturer", "hersteller"],
    "model": ["model", "typ", "type", "device_type", "device_model"],
    "typ": ["model", "typ", "type", "device_type"],
    "serial_number": ["serial_number", "serial", "serial_no", "sn", "serialnumber"],

    # --- Location / Plant
    "plant": ["plant_entry", "plant", "anlage", "plant_name", "assetlocation"],
    "anlage": ["plant_entry", "plant", "anlage"],
    "position": ["position_entry", "position", "ort", "location", "assetlocation"],
    "ort": ["position_entry", "position", "location_entry"],
    "location": ["location_entry", "position_entry", "position", "ort"],

    # --- Project / Order
    "projekt": ["projekt", "project_entry", "project", "project_name"],
    "projekt_nr": ["project_nr_entry", "project_nr", "projekt_nr", "project_number"],
    "auftrag": ["order_entry", "order", "auftrag", "order_number"],
    "kunde": ["customer_entry", "customer", "kunde", "client"],

    # --- Revision / Date
    "revision": ["revision_entry", "revision", "rev", "version_entry"],
    "date": ["date_entry", "date", "date_of_creation_entry", "created_date"],
    "author": ["author_entry", "author", "edited_by_entry", "bearb", "created_by"],
    "reviewed": ["reviewed_entry", "reviewed", "geprueft", "checked_by"],

    # --- Function / Classification
    "function": ["function_designation", "funktion", "function", "function_code"],
    "funktion": ["function_designation", "funktion", "function"],
    "beschreibung": ["describtion_entry", "description_entry", "beschreibung", "description"],
    "description": ["describtion_entry", "description_entry", "beschreibung", "description"],

    # --- PCE Classification
    "pce_category": ["pce_category", "pce_category", "category"],
    "pce_processing": ["pce_processing_function", "pce_processing", "processing_function"],

    # --- Layer / Sheet
    "layer": ["layer_id", "layerid", "layer", "ebene"],
    "sheet": ["instrument_sheet_id", "sheet_id", "documentblatt_id", "sheet_number"],

    # --- Object / Component
    "object_id": ["object_id", "objectid", "object_id"],
    "component_id": ["component_id", "componentid", "componenet_id", "component_id"],
    "component_role": ["component_role", "classification", "main_sub"],
    "classification": ["classification", "component_role", "class_name"],

    # --- Connection
    "connection_type": ["connection_type", "connection_type", "type"],
    "from_id": ["from_attribute_id", "from_element_id", "from_id", "from"],
    "to_id": ["to_attribute_id", "to_element_id", "to_id", "to"],
    "wire_color": ["wire_color", "wire_colour", "color"],

    # --- Norm / Standard
    "norm": ["norm", "standard", "norm_reference"],
    "iec_ref": ["iec_60617_ref", "iec_ref", "norm_reference"],

    # --- Technical attributes
    "nominal_diameter": ["nominal_diameter", "dn", "nennweite", "size", "diameter"],
    "nominal_pressure": ["nominal_pressure", "pn", "nenndruck", "pressure_rating"],
    "medium": ["medium", "fluid", "media"],
    "measuring_range": ["measuring_range", "messbereich", "range", "measurement_range"],
    "messbereich": ["measuring_range", "messbereich", "range"],
    "output_signal": ["output_signal", "ausgangssignal", "signal_output"],
    "power_supply": ["power_supply", "supply_voltage", "versorgung", "power"],
    "weight": ["weight", "gewicht", "mass"],

    # --- Datasheet-specific
    "order_code": ["ordercode", "order_code", "order_number", "bestellnummer"],
    "device_type": ["device_type", "class_name", "classification", "type"],
    "attribute_name": ["attribute_name", "attribute_key", "parameter", "property"],
    "attribute_value": ["attribute_value", "value", "parameter_value"],
    "attribute_unit": ["attribute_unit", "unit", "measurement_unit"],
    "attribute_source": ["attribute_source", "source", "data_source"],
    "mapping_confidence": ["mapping_confidence", "confidence", "match_confidence"],
    "llm_reasoning": ["llm_reasoning", "reasoning", "explanation"],

    # --- Presence / Status
    "presence_status": ["presencestatus", "presence_status", "status"],
    "match_confidence": ["matchconfidence", "match_confidence", "confidence"],
    "match_method": ["matchmethod", "match_method", "method"],
    "source_locator": ["sourcelocator", "source_locator", "source_loc"],
    "source_doc_id": ["sourcedocid", "source_doc_id", "source_document"],

    # --- IEC 81346 references
    "reference_data": ["object_reference_data", "layer_reference_data", "reference", "ref_data"],
    "object_reference": ["object_reference_data", "reference_data", "reference"],
    "layer_reference": ["layer_reference_data", "reference_data"],

    # --- Cabinet / Enclosure
    "e_schrank": ["eschrank", "e_schrank", "cabinetid", "cabinet_id", "enclosure"],
    "cabinet": ["cabinetid", "cabinet_id", "eschrank", "e_schrank"],

    # --- Terminal
    "terminal_id": ["terminal_id", "terminalid", "terminal"],
    "terminal_name": ["terminal_name", "terminalname", "wirelabel", "wire_label"],
    "plt_stelle": ["pltstelle", "plt_stelle", "plt_reference"],

    # --- Address
    "address": ["address", "adresse", "addr"],
    "art": ["art", "type_code", "article"],
    "kanal": ["kanal", "channel", "channel_number"],

    # --- File
    "source_file": ["source_file", "sourcefile", "document", "filename", "file"],
    "file_name": ["document", "source_file", "filename", "file_name"],
}


def _normalize(s: str) -> str:
    """Normalize a string for matching: lowercase, collapse whitespace/underscores."""
    import re
    return re.sub(r"[_\s]+", "_", s.strip().lower())


# Pre-normalize all synonym keys and values
_NORMALIZED_SYNONYMS: dict[str, list[str]] = {
    _normalize(k): [_normalize(v) for v in vals]
    for k, vals in _EXACT_SYNONYMS.items()
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SemanticFieldMapper:
    """Map extracted field names to template column names.

    Three-tier strategy:
    1. Exact synonym lookup (free, instant)
    2. Embedding similarity (low cost)
    3. LLM semantic match (highest accuracy, highest cost)
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        *,
        embedding_threshold: float = 0.5,
        llm_threshold: float = 0.6,
    ) -> None:
        """*llm_client* is an :class:`OpenAICompatibleLLMClient` instance (or
        ``None`` to skip LLM tiers)."""
        self._llm_client = llm_client
        self._embedding_threshold = embedding_threshold
        self._llm_threshold = llm_threshold
        # Disk-persisted caches
        from iev4pi_transformation_tool.core.disk_cache import DiskDict
        self._embedding_cache = DiskDict("semantic_field_embedding")
        self._llm_cache = DiskDict("semantic_field_llm")

    # --- Tier 1: exact synonym match ----------------------------------------

    def match_exact(
        self, field_name: str, column_names: list[str]
    ) -> tuple[str | None, float]:
        """Try to match *field_name* to a column via the synonym dictionary.

        Returns ``(column_name, confidence)`` or ``(None, 0.0)``.
        Confidence is 0.95 for exact synonym hits.
        """
        norm_field = _normalize(field_name)

        # Direct match: field name equals a column name (normalised)
        norm_cols = {c: _normalize(c) for c in column_names}
        for col, norm_col in norm_cols.items():
            if norm_field == norm_col:
                return col, 0.98

        # Synonym lookup
        synonyms = _NORMALIZED_SYNONYMS.get(norm_field, [])
        for syn in synonyms:
            for col, norm_col in norm_cols.items():
                if syn == norm_col:
                    return col, 0.95
                # Partial match: column contains the synonym
                if syn in norm_col or norm_col in syn:
                    return col, 0.85

        return None, 0.0

    # --- Tier 2: embedding similarity ---------------------------------------

    def match_embedding(
        self, field_name: str, column_names: list[str]
    ) -> tuple[str | None, float]:
        """Match via embedding cosine similarity.

        Returns ``(column_name, score)`` or ``(None, 0.0)``.
        Requires ``embedding_available()`` on the LLM client.
        """
        cache_key = field_name + "||" + "||".join(sorted(column_names))
        cached = self._embedding_cache.get(cache_key)
        if cached is not None and isinstance(cached, list) and len(cached) == 2:
            return (cached[0], float(cached[1]))

        if self._llm_client is None:
            return None, 0.0
        if not self._llm_client.embedding_available():
            return None, 0.0

        try:
            texts = [field_name] + list(column_names)
            vectors = self._llm_client.embed_texts(texts)
            if not vectors or len(vectors) < 2:
                return None, 0.0

            field_vec = vectors[0]
            col_vecs = vectors[1:]

            best_col: str | None = None
            best_score: float = 0.0

            for col_name, col_vec in zip(column_names, col_vecs):
                score = cosine_similarity(field_vec, col_vec)
                if score > best_score:
                    best_score = score
                    best_col = col_name

            if best_score >= self._embedding_threshold:
                result = (best_col, round(best_score, 3))
                self._embedding_cache[cache_key] = list(result)
                return result
            self._embedding_cache[cache_key] = [None, 0.0]
            return None, 0.0
        except Exception:
            return None, 0.0

    # --- Tier 3: LLM semantic match -----------------------------------------

    def match_llm(
        self,
        field_name: str,
        field_value: str,
        column_names: list[str],
    ) -> tuple[str | None, float]:
        """Ask the LLM to choose the best column for a field.

        Returns ``(column_name, confidence)`` or ``(None, 0.0)``.
        """
        cache_key = f"{field_name}|{field_value[:50]}|" + "||".join(sorted(column_names))
        cached = self._llm_cache.get(cache_key)
        if cached is not None and isinstance(cached, list) and len(cached) == 2:
            return (cached[0], float(cached[1]))

        if self._llm_client is None:
            return None, 0.0
        if not self._llm_client.available():
            return None, 0.0

        prompt = (
            f"You are mapping an extracted data field to an Excel template column.\n"
            f"Extracted field name: \"{field_name}\"\n"
            f"Extracted field value (for context): \"{field_value}\"\n"
            f"Available template columns: {json_dumps(column_names)}\n\n"
            f"Choose the best matching column. Consider:\n"
            f"- The meaning of the field name (it may be in German or English)\n"
            f"- The format of the field value (e.g., numbers, codes, text)\n"
            f"- Engineering context (tags, measurements, identifiers)\n\n"
            f"Return JSON: {{\"matched_column\": \"ColumnName\", \"confidence\": 0.95, "
            f"\"reasoning\": \"brief explanation\"}}\n\n"
            f"If no column matches well, set matched_column to null and confidence to 0."
        )

        try:
            result = self._llm_client.chat_json(prompt)
            if not result or not isinstance(result, dict):
                return None, 0.0
            matched = result.get("matched_column")
            confidence = float(result.get("confidence", 0))
            if matched and confidence >= self._llm_threshold and matched in column_names:
                self._llm_cache[cache_key] = [matched, confidence]
                return matched, confidence
            self._llm_cache[cache_key] = [None, 0.0]
            return None, 0.0
        except Exception:
            return None, 0.0

    # --- Combined match -----------------------------------------------------

    def match(
        self,
        field_name: str,
        column_names: list[str],
        field_value: str = "",
    ) -> tuple[str | None, float]:
        """Three-tier match: exact → embedding → LLM.

        Returns ``(best_column_name, confidence)`` or ``(None, 0.0)``.
        """
        # Tier 1: exact synonym match
        col, conf = self.match_exact(field_name, column_names)
        if col and conf >= 0.95:
            return col, conf

        # Tier 2: embedding similarity
        col, conf = self.match_embedding(field_name, column_names)
        if col and conf >= 0.75:
            return col, conf

        # Tier 3: LLM semantic match
        if field_value:
            col, conf = self.match_llm(field_name, field_value, column_names)
            if col:
                return col, conf

        # Return best embedding result even if below threshold
        if col:
            return col, conf

        return None, 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


