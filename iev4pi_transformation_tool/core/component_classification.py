"""Classification rules for Stellenplan components and their attributes.

Encodes the small vocabulary the curated ``Standardized_Stellenplan.xlsx`` uses:

* ``Component_Classification`` ↔ ``(Component_Role, Classification)``:
    Main → ``SPS-Kanal | Klemme | Umformer | Unterbrechung``
    Sub  → ``Klemmpunkt | Aufnehmer``
* ``Component_Data.Attribute_Class``:
    ``IEC 81346-1`` (Referenzkennzeichnung — 4 aspect variants),
    ``DIN 19227-2`` (Anschlussbezeichnung / Eingangs- / Ausgangs- /
    Messgröße / Unterbrechungsnummer),
    ``CAE-systemspezifisch (COMOS)`` (VW etc.),
    ``Herstellerspezifisch`` (Art / Kanal / Adresse / Typ).
* ``Component_Data.Attribute_Source``: ``Normativ | Explizit | Unklar``.

Used by :mod:`iev4pi_transformation_tool.core.standardized_export` to populate the
``Component_ID``, ``Component_Classification``, and ``Component_Data`` sheets
in a way that mirrors the curated example.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# --- Attribute classification -------------------------------------------------

_REF_FUNK = "Referenzkennzeichnung (Funktionsaspekt)"
_REF_PROD = "Referenzkennzeichnung (Produktaspekt)"
_REF_ORT = "Referenzkennzeichnung (Ortsaspekt)"
_REF_ORT_PROD = "Referenzkennzeichnung (Orts+Produktaspekt)"

# DIN 19227-2 attribute name regexes (applied to the *value* when a line is
# a standalone token, not a "key: value" pair).
_DIN_INPUT_RE = re.compile(r"^E[WX]?\d+(?:\.\d+)?$", re.IGNORECASE)        # EW8, E1.0
_DIN_OUTPUT_RE = re.compile(r"^A[WX]?\d+(?:\.\d+)?$", re.IGNORECASE)       # AW8, A1.0
_DIN_NUMERIC_RE = re.compile(r"^\d{1,3}$")                                   # 12, 13
_DIN_MEASURE_RE = re.compile(r"^[\w°]+(?:/\w+)?(?:[²³])?$")   # g/cm³

# COMOS and similar CAE-system tokens (uppercase 2-3 letter codes that
# survive untranslated)
_CAE_TOKEN_RE = re.compile(r"^[A-Z]{2,3}$")

# Attribute-name → (Attribute_Class, Attribute_Source)
_NAMED_ATTRIBUTE_RULES: dict[str, tuple[str, str]] = {
    "art": ("Herstellerspezifisch", "Explizit"),
    "kanal": ("Herstellerspezifisch", "Explizit"),
    "adresse": ("Herstellerspezifisch", "Explizit"),
    "typ": ("Herstellerspezifisch", "Explizit"),
    "type": ("Herstellerspezifisch", "Explizit"),
    "messgröße": ("DIN 19227-2", "Normativ"),
    "messgroesse": ("DIN 19227-2", "Normativ"),
    "anschluss": ("DIN 19227-2", "Normativ"),
    "anschlussbezeichnung": ("DIN 19227-2", "Normativ"),
    "eingang": ("DIN 19227-2", "Normativ"),
    "eingangskennzeichen": ("DIN 19227-2", "Normativ"),
    "ausgang": ("DIN 19227-2", "Normativ"),
    "ausgangskennzeichen": ("DIN 19227-2", "Normativ"),
    "unterbrechung": ("DIN 19227-2", "Normativ"),
    "unterbrechungsnummer": ("DIN 19227-2", "Normativ"),
}


def classify_iec_reference(token: str) -> str:
    """Identify which IEC 81346-1 aspect a reference identifier belongs to.

    Examples:
        ``=.FIC+`` → Funktionsaspekt
        ``-A1-(0)-M05`` → Produktaspekt
        ``+10.O001.MSR_Schrank`` → Ortsaspekt
        ``=+10.L001.G001.R001.P001`` → Orts+Produktaspekt (mixed)
        ``=0.H1.T1.TU10.F17.FIC.I`` → Orts+Produktaspekt
    """
    token = token.strip()
    if not token:
        return _REF_PROD
    head = token[0]
    has_orts = "+" in token[1:] or token.startswith("+")
    has_prod = "-" in token[1:] or token.startswith("-")
    has_funk = "=" in token
    if head == "=" and has_orts:
        return _REF_ORT_PROD
    if head == "=" and has_prod:
        return _REF_ORT_PROD
    if head == "=":
        return _REF_FUNK
    if head == "+":
        return _REF_ORT_PROD if has_prod else _REF_ORT
    if head == "-":
        return _REF_PROD
    if has_orts and has_prod:
        return _REF_ORT_PROD
    if has_orts:
        return _REF_ORT
    return _REF_PROD


def classify_named_attribute(name: str) -> tuple[str, str]:
    """Look up an attribute name (left side of ``Art: AI``) in the known table."""
    key = (name or "").strip().lower().rstrip(":")
    return _NAMED_ATTRIBUTE_RULES.get(key, ("Herstellerspezifisch", "Explizit"))


def classify_standalone_token(
    value: str, llm_client: object | None = None
) -> tuple[str, str, str]:
    """Classify a token that appears alone on a line (no ``key:`` prefix).

    Returns ``(attribute_name, attribute_class, attribute_source)``.

    Standard patterns (IEC 81346, DIN 19227, CAE) are detected via their
    well-defined syntax.  Tokens that don't match any standard pattern are
    classified via LLM batch when available, falling back to a conservative
    ``Bemerkung`` label.
    """
    token = value.strip()
    if not token:
        return ("Bemerkung", "Herstellerspezifisch", "Unklar")
    if token[0] in "=+-":
        return (classify_iec_reference(token), "IEC 81346-1", "Normativ")
    if _DIN_INPUT_RE.match(token):
        return ("Eingangskennzeichen", "DIN 19227-2", "Normativ")
    if _DIN_OUTPUT_RE.match(token):
        return ("Ausgangskennzeichen", "DIN 19227-2", "Normativ")
    if _DIN_NUMERIC_RE.match(token):
        return ("Anschlussbezeichnung", "DIN 19227-2", "Normativ")
    if "/" in token and _DIN_MEASURE_RE.match(token):
        return ("Messgröße", "DIN 19227-2", "Normativ")
    if _CAE_TOKEN_RE.match(token):
        return (token, "CAE-systemspezifisch (COMOS)", "Unklar")

    # Defer to batch LLM classification (handled in decompose_stellen_cell
    # which collects all unknown tokens and sends one batch call).
    return _BATCH_PENDING  # type: ignore[return-value]


# Sentinel: returned by classify_standalone_token for tokens that need LLM
# classification.  decompose_stellen_cell collects these and resolves them
# in a single batch LLM call.
_BATCH_PENDING = ("__batch_pending__", "", "")


# Cache for LLM token classifications to avoid repeated API calls.
# Persisted centrally via llm_cache module → .iev4pi/llm_cache.json
_token_classification_cache: dict[str, tuple[str, str, str]] = {}


def _resolve_token_batch_llm(
    tokens: list[str], llm_client: object
) -> dict[str, tuple[str, str, str]]:
    """Batch-classify unknown tokens via LLM — one call for all tokens."""
    uncached = [t for t in tokens if t not in _token_classification_cache]
    if not uncached:
        return {t: _token_classification_cache[t] for t in tokens}

    token_list = "\n".join(f'- "{t}"' for t in uncached)
    prompt = (
        "Classify each token from an industrial engineering Stellenplan "
        "grid cell.\n\n"
        "Categories:\n"
        "- Typ: a manufacturer part number or order code "
        "(e.g. 321-1BL00-0AA0, 6ES7321-1BL00, VEGAFLEX81)\n"
        "- Bemerkung: a comment, label, grid header, or description "
        "(e.g. Prozesstechnik, Steuerung, Feld, Rangierverteiler)\n"
        "- Art: a device type or model series designation\n"
        "- Kanal: a channel number or configuration\n"
        "- Adresse: an address or bus address (e.g. EW8, E1.0)\n\n"
        "Part numbers typically contain digits and hyphens.  Grid labels "
        "are descriptive German words.  If unsure, use Bemerkung.\n\n"
        f"Tokens to classify:\n{token_list}\n\n"
        'Return JSON: {"classifications": {"token": '
        '{"attribute_name": "Typ", "attribute_class": "Herstellerspezifisch", '
        '"attribute_source": "Explizit"}, ...}}'
    )
    try:
        response = llm_client.chat_json(
            "You are a token classifier for engineering documents. Return ONLY valid JSON.",
            prompt,
        )
        if isinstance(response, dict):
            cl = response.get("classifications", {})
            if isinstance(cl, dict):
                for token, info in cl.items():
                    if isinstance(info, dict):
                        name = str(info.get("attribute_name", "Bemerkung"))
                        cls = str(info.get("attribute_class", "Herstellerspezifisch"))
                        src = str(info.get("attribute_source", "Unklar"))
                        _token_classification_cache[str(token)] = (name, cls, src)
    except Exception:
        pass

    # Return all tokens with defaults for any that weren't classified
    return {
        t: _token_classification_cache.get(
            t, ("Bemerkung", "Herstellerspezifisch", "Unklar")
        )
        for t in tokens
    }


# --- Component decomposition --------------------------------------------------


@dataclass
class ComponentAttribute:
    name: str
    value: str
    attribute_class: str
    source: str = "Explizit"


@dataclass
class SubComponent:
    classification: str
    attributes: list[ComponentAttribute] = field(default_factory=list)


@dataclass
class CellDecomposition:
    main_classification: str
    main_attributes: list[ComponentAttribute] = field(default_factory=list)
    subs: list[SubComponent] = field(default_factory=list)


# Heuristics for picking the Main component classification
_KLEMME_HINTS = ("-x", "klemmleiste", "klemme")
_UMFORMER_HINTS = ("umformer", "transmitter", "messumformer")
_UNTERBRECHUNG_HINTS = ("unterbrechung", "trennstelle")

# Valid classifications that the LLM can choose from.
_VALID_CLASSIFICATIONS = [
    "SPS-Kanal",
    "Klemme",
    "Umformer",
    "Unterbrechung",
    "Aufnehmer",
    "Klemmpunkt",
    "Sicherung",
    "Schalter",
    "Relais",
    "Motor",
    "Pumpe",
    "Ventil",
]


def _guess_main_classification(
    lines: list[str],
    llm_client: object | None = None,
) -> str:
    """Classify a Stellenplan cell into a component type.

    When an LLM client is available, uses semantic classification as the
    primary path (handles any language/format).  Falls back to keyword
    matching when LLM is unavailable.
    """
    blob = " ".join(lines).lower()

    # LLM primary path: semantic classification adapts to any document
    if llm_client is not None and hasattr(llm_client, "available") and llm_client.available():
        result = _classify_component_llm(lines, llm_client)
        if result and result != "SPS-Kanal":
            return result
        # LLM returned default — fall through to keyword matching

    # Keyword fast path (English/German)
    if any(hint in blob for hint in _UMFORMER_HINTS):
        return "Umformer"
    if any(hint in blob for hint in _UNTERBRECHUNG_HINTS):
        return "Unterbrechung"
    if any(hint in blob for hint in _KLEMME_HINTS):
        return "Klemme"

    return "SPS-Kanal"


# Cache for component classification to avoid repeated LLM calls for same cell text.
_component_classification_cache: dict[str, str] = {}


def _classify_component_llm(lines: list[str], llm_client: object) -> str:
    """Use the LLM to classify a component from its cell text lines.  Cached."""
    blob = " ".join(lines)
    cache_key = blob.strip().lower()
    if cache_key in _component_classification_cache:
        return _component_classification_cache[cache_key]

    prompt = (
        "You are classifying an industrial engineering component from a "
        "Stellenplan (instrument list) grid cell.\n\n"
        "Cell text:\n"
        f"{blob}\n\n"
        "Choose the best classification from this list:\n"
        + ", ".join(_VALID_CLASSIFICATIONS)
        + "\n\n"
        "Guidelines:\n"
        "- SPS-Kanal: PLC I/O channel (analog/digital input/output)\n"
        "- Klemme: Terminal block, terminal strip (Klemmenleiste)\n"
        "- Umformer: Transmitter, signal converter, transducer\n"
        "- Unterbrechung: Isolation point, break point\n"
        "- Sicherung: Fuse, circuit breaker\n"
        "- Schalter: Switch, main switch\n"
        "- Relais: Relay\n"
        "- If the text mentions a specific device type not listed, choose "
        "the closest match or suggest a new classification.\n\n"
        'Return JSON: {"classification": "SPS-Kanal", "confidence": 0.9, '
        '"reasoning": "brief explanation"}'
    )
    try:
        response = llm_client.chat_json(
            "You are an industrial component classifier. Return ONLY valid JSON.",
            prompt,
        )
        if isinstance(response, dict):
            classification = str(response.get("classification", ""))
            confidence = float(response.get("confidence", 0))
            if classification and confidence >= 0.5:
                _component_classification_cache[cache_key] = classification
                return classification
    except Exception:
        pass
    _component_classification_cache[cache_key] = "SPS-Kanal"
    return "SPS-Kanal"


def decompose_stellen_cell(
    lines: list[str],
    llm_client: object | None = None,
) -> CellDecomposition:
    """Split a Stellenplan grid cell's content into Main + Sub components.

    Rules of thumb (informed by the curated reference workbook):

    * Lines like ``Art: AI`` → key/value attribute on the Main component
      (classified by the key name via ``classify_named_attribute``).
    * Lines starting with ``=``, ``+``, or ``-`` → IEC 81346-1
      Referenzkennzeichnung attribute on the Main component (the aspect
      variant comes from ``classify_iec_reference``).
    * Standalone purely-numeric tokens (e.g. ``12``, ``13``) → one Sub
      ``Klemmpunkt`` component each, with a single
      ``Anschlussbezeichnung`` attribute (DIN 19227-2 / Normativ).
    * Other standalone tokens fall through ``classify_standalone_token``
      and get attached to the Main component.

    When *llm_client* is provided, the classification step uses LLM
    semantic classification as a fallback when keyword matching returns
    the default value.
    """
    main_attrs: list[ComponentAttribute] = []
    subs: list[SubComponent] = []
    pending_tokens: list[tuple[int, str]] = []  # (index, token) for batch LLM

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Key/value attribute (e.g. "Adresse: EW8")
        if ":" in line and not line.startswith(("=", "+", "-")):
            name, _sep, value = line.partition(":")
            name = name.strip()
            value = value.strip()
            if not value:
                continue
            attr_class, source = classify_named_attribute(name)
            main_attrs.append(
                ComponentAttribute(name=name, value=value,
                                   attribute_class=attr_class, source=source)
            )
            continue

        # Reference identifier
        if line.startswith(("=", "+", "-")):
            aspect = classify_iec_reference(line)
            main_attrs.append(
                ComponentAttribute(name=aspect, value=line,
                                   attribute_class="IEC 81346-1", source="Normativ")
            )
            continue

        # Standalone numeric token → Sub Klemmpunkt
        if _DIN_NUMERIC_RE.match(line):
            subs.append(
                SubComponent(
                    classification="Klemmpunkt",
                    attributes=[
                        ComponentAttribute(
                            name="Anschlussbezeichnung",
                            value=line,
                            attribute_class="DIN 19227-2",
                            source="Normativ",
                        )
                    ],
                )
            )
            continue

        # Generic standalone token classification
        attr_name, attr_class, source = classify_standalone_token(line, llm_client=llm_client)
        if attr_name == "__batch_pending__":
            pending_tokens.append((len(main_attrs), line))
            main_attrs.append(ComponentAttribute(
                name="Bemerkung", value=line,
                attribute_class="Herstellerspezifisch", source="Unklar",
            ))
        else:
            main_attrs.append(
                ComponentAttribute(name=attr_name, value=line,
                                   attribute_class=attr_class, source=source)
            )

    # Batch-resolve unknown tokens via LLM (one API call for all)
    if pending_tokens and llm_client is not None and hasattr(llm_client, "available") and llm_client.available():
        token_texts = [t for _, t in pending_tokens]
        resolved = _resolve_token_batch_llm(token_texts, llm_client)
        for idx, token in pending_tokens:
            if idx < len(main_attrs):
                name, cls, src = resolved.get(token, ("Bemerkung", "Herstellerspezifisch", "Unklar"))
                main_attrs[idx] = ComponentAttribute(
                    name=name, value=token,
                    attribute_class=cls, source=src,
                )

    return CellDecomposition(
        main_classification=_guess_main_classification(lines, llm_client=llm_client),
        main_attributes=main_attrs,
        subs=subs,
    )
