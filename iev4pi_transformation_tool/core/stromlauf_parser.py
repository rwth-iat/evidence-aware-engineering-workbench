"""Dedicated parser for Stromlaufplan (circuit diagram) PDFs.

Extracts the structured document/object/element/connection model needed
to fill the 9-sheet standardized Stromlaufplan workbook.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fitz
except ImportError:
    try:
        import pymupdf as fitz
    except ImportError:
        fitz = None


# ---------------------------------------------------------------------------
# Colour → German wire colour name
# ---------------------------------------------------------------------------

_WIRE_COLOR_MAP: dict[tuple[float, float, float], str] = {
    (1.0, 0.0, 0.0): "rot",
    (1.0, 0.270588, 0.0): "orange",
    (1.0, 1.0, 0.0): "gelb",
    (0.0, 0.501961, 0.0): "grün",
    (0.545098, 0.270588, 0.07451): "braun",
    (0.827451, 0.827451, 0.827451): "grau",
}


def _closest_wire_color(rgb: tuple[float, float, float]) -> str | None:
    """Map an RGB tuple to the closest German wire colour name."""
    best = None
    best_dist = float("inf")
    for ref, name in _WIRE_COLOR_MAP.items():
        dist = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if dist < best_dist:
            best_dist = dist
            best = name
    return best if best_dist < 0.3 else None


# ---------------------------------------------------------------------------
# Parsed data model
# ---------------------------------------------------------------------------


@dataclass
class StromlaufTitleBlock:
    sheet_number: str = ""
    total_sheets: str = ""
    sheet_name: str = ""
    sheet_type: str = "Stromlaufplan"
    plant: str = ""
    location: str = ""
    project_nr: str = ""
    project: str = ""
    date: str = ""
    author: str = ""


@dataclass
class StromlaufPin:
    """A single pin/terminal belonging to an object."""
    pin_label: str = ""        # e.g. "1", "2", "A1"
    address: str = ""           # e.g. "A0.0", "%IW1.5"
    vw_left: str = ""          # cross-reference left
    vw_right: str = ""         # cross-reference right
    potential: str = ""        # potential name if present
    grid_col: str = ""          # grid column letter A-F
    grid_row: str = ""          # grid row number 1-8
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    element_key: int = 0        # assigned during element tree building


@dataclass
class StromlaufObject:
    """One electrical object (component) in the circuit diagram."""
    object_id: str = ""         # O001, O002, ... (assigned later)
    reference: str = ""         # e.g. "-Beckhoff_04_EL4374"
    display_label: str = ""     # e.g. "Beckhoff_04_EL4374"
    description: str = ""       # e.g. "4 analoge Ausgänge (2p)"
    manufacturer: str = ""      # e.g. "Beckhoff"
    type_code: str = ""         # e.g. "EL4374"
    block_label: str = ""       # e.g. "B06"
    grid_col: str = ""
    grid_row: str = ""
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    pins: list[StromlaufPin] = field(default_factory=list)
    # Classification metadata
    classification: str = ""    # "Objekt (Block)", "Sicherung", etc.
    iec_ref: str = ""           # IEC 60617 reference
    is_main_classified: bool = True  # whether to emit classification row


@dataclass
class StromlaufConnection:
    """A wire/connection between two elements."""
    connection_key: int = 0
    from_element_key: int = 0
    to_element_key: int = 0
    wire_color: str = ""
    from_object_ref: str = ""
    to_object_ref: str = ""


@dataclass
class StromlaufDocument:
    """Parsed representation of one Stromlaufplan PDF."""
    pdf_path: Path
    document_id: str = ""
    file_name: str = ""
    title_block: StromlaufTitleBlock = field(default_factory=StromlaufTitleBlock)
    objects: list[StromlaufObject] = field(default_factory=list)
    connections: list[StromlaufConnection] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


class StromlaufParser:
    """Parse a Stromlaufplan PDF into structured document/object/element data."""

    def __init__(self) -> None:
        if fitz is None:
            raise ImportError("PyMuPDF (fitz) is required for Stromlaufplan parsing")

    def parse(self, pdf_path: Path) -> StromlaufDocument:
        doc = StromlaufDocument(pdf_path=pdf_path, file_name=pdf_path.name)
        try:
            fitz_doc = fitz.open(str(pdf_path))
        except Exception:
            return doc

        if len(fitz_doc) == 0:
            fitz_doc.close()
            return doc

        page = fitz_doc[0]
        all_spans = self._collect_spans(page)
        grid = self._build_grid(all_spans)

        doc.title_block = self._parse_title_block(all_spans)
        doc.objects = self._parse_objects(all_spans, grid)
        doc.connections = self._parse_connections(page, doc.objects)

        fitz_doc.close()
        return doc

    # ------------------------------------------------------------------
    # Span collection
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_spans(page) -> list[dict[str, Any]]:
        spans: list[dict[str, Any]] = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    spans.append({
                        "x0": span["bbox"][0],
                        "y0": span["bbox"][1],
                        "x1": span["bbox"][2],
                        "y1": span["bbox"][3],
                        "text": text,
                        "size": span.get("size", 0),
                        "cx": (span["bbox"][0] + span["bbox"][2]) / 2,
                        "cy": (span["bbox"][1] + span["bbox"][3]) / 2,
                    })
        spans.sort(key=lambda s: (round(s["y0"] / 5) * 5, s["x0"]))
        return spans

    # ------------------------------------------------------------------
    # Grid detection
    # ------------------------------------------------------------------

    @staticmethod
    def _build_grid(spans: list[dict[str, Any]]) -> dict[str, Any]:
        """Detect the drawing grid (column letters A-F, row numbers 1-8)."""
        col_centers: dict[str, float] = {}
        row_centers: dict[str, float] = {}

        for s in spans:
            t = s["text"]
            # Column letters: single uppercase letter near top or bottom
            if re.fullmatch(r"[A-F]", t):
                y = s["cy"]
                if y < 60 or y > 520:
                    col_centers.setdefault(t, []).append(s["cx"])
            # Row numbers: single digit near left or right edge
            if re.fullmatch(r"\d", t):
                x = s["cx"]
                if x < 90 or x > 810:
                    row_centers.setdefault(t, []).append(s["cy"])

        cols = {}
        for letter, cxs in col_centers.items():
            cols[letter] = sum(cxs) / len(cxs)
        rows = {}
        for digit, cys in row_centers.items():
            rows[digit] = sum(cys) / len(cys)

        return {"cols": cols, "rows": rows}

    @staticmethod
    def _grid_position(cx: float, cy: float, grid: dict[str, Any]) -> tuple[str, str]:
        """Map a center point (cx, cy) to (col_letter, row_number)."""
        col = ""
        row = ""
        if grid["cols"]:
            col = min(grid["cols"].items(), key=lambda kv: abs(kv[1] - cx))[0]
        if grid["rows"]:
            row = min(grid["rows"].items(), key=lambda kv: abs(kv[1] - cy))[0]
        return col, row

    # ------------------------------------------------------------------
    # Title block
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_title_block(spans: list[dict[str, Any]]) -> StromlaufTitleBlock:
        tb = StromlaufTitleBlock()
        title_spans = [s for s in spans if s["x0"] > 180 and s["y0"] > 500]

        for s in title_spans:
            t = s["text"]
            if "Projekt-Nr" in t or "Projekt-Nr." in t:
                tb.project_nr = t.split(":", 1)[-1].strip() if ":" in t else t.split(".", 1)[-1].strip()
            elif t.startswith("Projekt:"):
                tb.project = t.split(":", 1)[-1].strip()
            elif t.startswith("Ort:"):
                tb.location = t.split(":", 1)[-1].strip()
            elif t.startswith("Anlage:"):
                tb.plant = t.split(":", 1)[-1].strip()

        blatt_spans = sorted(
            [s for s in title_spans if s["x0"] > 700 and s["y0"] > 555],
            key=lambda s: (s["y0"], s["x0"]),
        )
        for i, s in enumerate(blatt_spans):
            t = s["text"]
            if t == "Blatt:" and i + 1 < len(blatt_spans):
                tb.sheet_number = blatt_spans[i + 1]["text"]
            if t == "Von:" and i + 1 < len(blatt_spans):
                tb.total_sheets = blatt_spans[i + 1]["text"]

        date_author_spans = [
            s for s in title_spans
            if 180 < s["x0"] < 250 and s["y0"] > 540 and s["size"] > 5
        ]
        for s in date_author_spans:
            t = s["text"]
            if re.match(r"\d{1,2}\.\d{1,2}\.\d{2,4}", t):
                tb.date = t
            elif re.match(r"^[A-Z]\.[A-Z]+$", t):
                tb.author = t

        name_spans = [
            s for s in title_spans
            if s["x0"] > 480 and s["x0"] < 700 and 555 < s["y0"] < 566 and s["size"] > 5
        ]
        for s in name_spans:
            if "stromlauf" in s["text"].lower():
                tb.sheet_name = s["text"]
                tb.sheet_type = "Stromlaufplan"

        return tb

    # ------------------------------------------------------------------
    # Object & pin parsing
    # ------------------------------------------------------------------

    # Text that looks like a grid address/coordinate, not an object reference.
    # Matches: AW0, AW2, A0.0, %IW1.5, VW, single digits
    _ADDRESS_PATTERN = re.compile(
        r"^(?:AW\d+|[A-F]\d+\.\d+|%\w+|VW|\d+|[A-F])$"
    )

    def _parse_objects(
        self, spans: list[dict[str, Any]], grid: dict[str, Any]
    ) -> list[StromlaufObject]:
        """Find all electrical objects in both PDF formats."""
        objects: list[StromlaufObject] = []
        seen_refs: set[str] = set()

        # Pass 1: "Kennzeichen:"-style objects (IO module PDFs)
        for i, s in enumerate(spans):
            if s["text"] != "Kennzeichen:":
                continue
            if i + 1 >= len(spans):
                continue
            ref_span = spans[i + 1]
            ref = _clean_reference(ref_span["text"])
            if not ref.startswith("-") or ref in seen_refs:
                continue
            seen_refs.add(ref)
            ref_span["text"] = ref
            obj = self._parse_one_object(spans, ref_span, grid, from_kennzeichen=True)
            if obj:
                obj.reference = ref
                objects.append(obj)

        # Pass 2: standalone "-" prefixed references (power/secondary circuit PDFs)
        for s in spans:
            ref = _clean_reference(s["text"])
            if not ref.startswith("-"):
                continue
            if ref in seen_refs:
                continue
            if self._ADDRESS_PATTERN.match(ref.lstrip("-")):
                continue
            if s["size"] < 4.0:
                continue
            seen_refs.add(ref)
            s_copy = dict(s)
            s_copy["text"] = ref
            obj = self._parse_one_object(spans, s_copy, grid, from_kennzeichen=False)
            if obj:
                obj.reference = ref
                objects.append(obj)

        # Pass 3: detect objects without "-" prefix
        # (e.g. CU8803-0000, X5, XD002)
        _COMPONENT_PATTERN = re.compile(
            r"^([A-Z]{2,}\d+(?:-\d+)?|[A-Z]\d+)\b"
        )
        for s in spans:
            ref = s["text"]
            if ref in seen_refs:
                continue
            m = _COMPONENT_PATTERN.match(ref)
            if not m:
                continue
            component_id = m.group(1)
            if self._ADDRESS_PATTERN.match(component_id):
                continue
            if s["size"] < 5.0:
                continue
            # Check context: near a label like Klemme, Beckhoff, Wago
            _OBJECT_LABEL_KW = (
                "klemme", "beckhoff", "wago", "hauptschalter",
                "phoenix", "contact", "durchgang", "sicherung",
                "motor", "schütz", "relais", "schalter",
            )
            near_label = any(
                any(kw in s2["text"].lower() for kw in _OBJECT_LABEL_KW)
                for s2 in spans
                if abs(s2["cy"] - s["cy"]) < 20 and abs(s2["cx"] - s["cx"]) < 100
            )
            if not near_label:
                continue
            prefixed_ref = f"-{component_id}"
            seen_refs.add(ref)
            seen_refs.add(prefixed_ref)
            # Create a synthetic ref_span with just the component id
            import copy
            synth_span = copy.copy(s)
            synth_span["text"] = prefixed_ref
            obj = self._parse_one_object(spans, synth_span, grid, from_kennzeichen=False)
            if obj:
                obj.reference = prefixed_ref
                objects.append(obj)

        return objects

    def _parse_one_object(
        self,
        spans: list[dict[str, Any]],
        ref_span: dict[str, Any],
        grid: dict[str, Any],
        *,
        from_kennzeichen: bool = True,
    ) -> StromlaufObject | None:
        """Parse a single object and its pins from its reference span position."""
        ref = ref_span["text"]
        obj_y = ref_span["cy"]
        obj_x = ref_span["cx"]

        # Find description text: must be within x-proximity of the object reference
        nearby_spans = [
            s for s in spans
            if abs(s["cy"] - obj_y) < 35
            and abs(s["cx"] - obj_x) < 100  # same column/module
            and s["text"] != ref
            and s["text"] not in ("Kennzeichen:", "Anlage:", "Ort:", "Kunde:", "Auftrag:")
        ]

        display_label = ref.lstrip("-")
        description = ""

        for ds in nearby_spans:
            t = ds["text"]
            if t.startswith("-"):
                continue
            if self._ADDRESS_PATTERN.match(t):
                continue
            if any(kw in t.lower() for kw in (
                "ausgänge", "ausgang", "eingänge", "eingang",
                "analog", "digital", "redundanz", "licence",
                "kanal", "kanäle", "kanalig", "motor",
                "sicherung", "schutz", "schalter",
                "hauptschalter", "not", "pumpe", "rührer",
                "potential", "spule", "klemme", "key",
            )):
                if ds["size"] >= 6.0:
                    description = t
            elif ds["size"] >= 6.5 and not description:
                display_label = t

        # Clean object reference text (fix known OCR errors)
        ref = _clean_reference(ref)
        ref_span["text"] = ref  # update for downstream use

        # Parse manufacturer/type from ref
        manufacturer = ""
        type_code = ""
        ref_clean = ref.lstrip("-")
        # Pattern: Beckhoff_NN_EL4374 → Hersteller=Beckhoff, Typ=EL4374
        m = re.match(r"^(Beckhoff|Wago|Phoenix|Siemens|Turck)_\d+_(.+)$", ref_clean)
        if m:
            manufacturer = m.group(1)
            type_code = m.group(2)
        elif "_" in ref_clean:
            parts = ref_clean.split("_", 1)
            manufacturer = parts[0]
            type_code = parts[1] if len(parts) > 1 else ""
        elif " " in ref_clean:
            parts = ref_clean.split(" ", 1)
            manufacturer = parts[0] if parts[0] in ("Beckhoff", "Wago", "Phoenix") else ""
            type_code = parts[1] if len(parts) > 1 and not manufacturer else parts[0]

        col, row = self._grid_position(obj_x, obj_y, grid)

        classification, iec_ref, is_main = _classify_object(ref, description)

        obj = StromlaufObject(
            reference=ref,
            display_label=display_label,
            description=description,
            manufacturer=manufacturer,
            type_code=type_code,
            grid_col=col,
            grid_row=row,
            bbox=(ref_span["x0"], ref_span["y0"], ref_span["x1"], ref_span["y1"]),
            classification=classification,
            iec_ref=iec_ref,
            is_main_classified=is_main,
        )

        # Parse pin rows: looking at the pin-number spans near this object's x position
        obj.pins = self._parse_pins(spans, obj_x, obj_y, grid, ref)
        return obj

    def _parse_pins(
        self,
        spans: list[dict[str, Any]],
        obj_x: float,
        obj_y: float,
        grid: dict[str, Any],
        obj_ref: str,
    ) -> list[StromlaufPin]:
        """Parse pin/terminal rows for an object."""
        pins: list[StromlaufPin] = []

        # Pin rows are small-font text (size 3-4) within the object's column
        pin_spans = [
            s for s in spans
            if 3.0 < s["size"] < 5.0
            and abs(s["cx"] - obj_x) < 100
            and s["cy"] > obj_y
        ]

        # Group spans by y-position (each row of pins)
        row_groups: dict[int, list[dict[str, Any]]] = {}
        for s in pin_spans:
            y_key = round(s["y0"])
            row_groups.setdefault(y_key, []).append(s)

        for y_key in sorted(row_groups.keys()):
            row_spans = sorted(row_groups[y_key], key=lambda s: s["x0"])
            pin = self._parse_pin_row(row_spans, grid)
            if pin and pin.pin_label:
                pins.append(pin)

        return pins

    @staticmethod
    def _parse_pin_row(
        row_spans: list[dict[str, Any]], grid: dict[str, Any]
    ) -> StromlaufPin | None:
        """Parse one row of pin data into a StromlaufPin."""
        pin = StromlaufPin()
        texts = [(s["cx"], s["text"], s) for s in row_spans]

        # Pin number is usually the first pure number
        # Address is something like "A0.0", "%IW1.5"
        # VW is literal "VW"
        # Potential name is something like "L+-", "L6"

        vw_count = 0
        for cx, t, s in texts:
            if t == "VW":
                if vw_count == 0:
                    pin.vw_left = t
                else:
                    pin.vw_right = t
                vw_count += 1
            elif re.match(r"^\d+$", t) and not pin.pin_label:
                pin.pin_label = t
                pin.bbox = (s["x0"], s["y0"], s["x1"], s["y1"])
                pin.grid_col, pin.grid_row = StromlaufParser._grid_position(
                    s["cx"], s["cy"], grid
                )
            elif re.match(r"^[A-Z]\d+\.\d+$|^%\w+", t) and not pin.address:
                pin.address = t
            elif re.match(r"^[A-Z]{2,}\d*$", t) and not pin.address:
                pin.address = t
            elif t not in ("VW",) and not re.match(r"^\d+$", t) and not pin.potential:
                if re.match(r"^[A-Z][+-]|^L\d", t) or len(t) <= 4:
                    pin.potential = t

        return pin if pin.pin_label else None

    # ------------------------------------------------------------------
    # Connection (colored wire) parsing
    # ------------------------------------------------------------------

    _STRUCTURAL_BLACK = {(0, 0, 0), (0.003922, 0.0, 0.0)}

    def _parse_connections(
        self, page, objects: list[StromlaufObject]
    ) -> list[StromlaufConnection]:
        """Extract colored wire connections from vector drawings.

        Groups line segments by colour, computes each colour's overall path
        endpoints, then matches endpoints to the nearest object (by bounding box).
        """
        from collections import defaultdict

        # Group line segments by color
        color_segments: dict[tuple[float, ...], list[tuple[float, float, float, float]]] = defaultdict(list)
        for drawing in page.get_drawings():
            color = drawing.get("color")
            if not color:
                continue
            rc = tuple(round(v, 6) for v in color)
            if rc in self._STRUCTURAL_BLACK or all(v < 0.01 for v in rc):
                continue

            wire_color_name = _closest_wire_color(tuple(color))
            if not wire_color_name:
                continue

            for item in drawing.get("items", []):
                if item[0] != "l":
                    continue
                p1, p2 = item[1], item[2]
                color_segments.setdefault(rc, []).append((p1.x, p1.y, p2.x, p2.y))

        connections: list[StromlaufConnection] = []
        seen_pairs: set[tuple[str, str, str]] = set()
        conn_key = 0

        for rc, segments in color_segments.items():
            wire_name = _closest_wire_color(tuple(float(v) for v in rc))
            if not wire_name:
                continue

            all_pts: list[tuple[float, float]] = []
            for x1, y1, x2, y2 in segments:
                all_pts.append((x1, y1))
                all_pts.append((x2, y2))

            if len(all_pts) < 2:
                continue

            from collections import Counter
            pt_counts = Counter((round(x, 1), round(y, 1)) for x, y in all_pts)
            endpoints = [(x, y) for (x, y), n in pt_counts.items() if n == 1]

            if len(endpoints) < 2:
                endpoints = [
                    min(all_pts, key=lambda p: (p[1], p[0])),
                    max(all_pts, key=lambda p: (p[1], p[0])),
                ]

            # Sort endpoints: bottom (larger y) goes to terminal/connector,
            # top (smaller y) goes to IO module
            endpoints.sort(key=lambda p: p[1])  # small y = top, large y = bottom
            top_pt = endpoints[0]
            bot_pt = endpoints[-1]

            from_obj = self._nearest_object(bot_pt[0], bot_pt[1], objects)
            to_obj = self._nearest_object(top_pt[0], top_pt[1], objects)

            if from_obj and to_obj and from_obj.reference != to_obj.reference:
                key = (from_obj.reference, to_obj.reference, wire_name)
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    conn_key += 1
                    connections.append(StromlaufConnection(
                        connection_key=conn_key,
                        from_element_key=0,
                        to_element_key=0,
                        wire_color=wire_name,
                        from_object_ref=from_obj.reference,
                        to_object_ref=to_obj.reference,
                    ))

        return connections

    @staticmethod
    def _nearest_object(
        x: float, y: float, objects: list[StromlaufObject], max_dist: float = 120.0,
    ) -> StromlaufObject | None:
        """Find the nearest object whose bounding box contains or is near a point."""
        best_obj = None
        best_dist = max_dist
        for obj in objects:
            bx0, by0, bx1, by1 = obj.bbox
            # Expand bbox slightly for matching
            bx0 -= 20
            by0 -= 20
            bx1 += 20
            by1 += 20
            if bx0 <= x <= bx1 and by0 <= y <= by1:
                return obj  # Direct hit
            # Distance to closest edge
            dx = max(bx0 - x, 0, x - bx1)
            dy = max(by0 - y, 0, y - by1)
            dist = (dx**2 + dy**2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_obj = obj
        return best_obj


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REFERENCE_CLEANUPS: dict[str, str] = {
    "Beclkhoff": "Beckhoff",
    "beclkhoff": "Beckhoff",
}

# References that need trimming (extra text appended)
_REFERENCE_TRIM_PATTERNS: list[tuple[str, str]] = [
    (r"^(-L6)\s+L\+\-.*$", r"\1"),  # "-L6   L+- Potential B05/Wago" → "-L6"
]


def _clean_reference(ref: str) -> str:
    """Fix known OCR/PDF text errors in object references."""
    for wrong, correct in _REFERENCE_CLEANUPS.items():
        if wrong in ref:
            ref = ref.replace(wrong, correct)
    for pattern, replacement in _REFERENCE_TRIM_PATTERNS:
        ref = re.sub(pattern, replacement, ref)
    return ref


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

_IEC_REF_MAP: dict[str, str] = {
    "Objekt (Block)": "IEC 60617-2, 02-01-01",
    "Anschluss/Pin": "IEC 60617-3, 03-02-xx",
    "Anschluss/Klemme": "IEC 60617-3, 03-02-xx",
    "Sicherung": "IEC 60617-7, 07-21-xx",
    "Drehstrommotor": "IEC 60617-6, 06-08-xx",
    "Relaisspule": "IEC 60617-7, 07-15-xx",
    "Leitungsschutzschalter": "IEC 60617-7, 07-13-xx",
    "Klemmenleiste": "IEC 60617-3, 03-01-xx",
    "Leiter/Potentialschiene": "IEC 60617-3, 03-01-xx",
}


def _classify_object(ref: str, description: str) -> tuple[str, str, bool]:
    """Classify an object and return (classification, iec_ref, is_main_classified)."""
    ref_lower = ref.lower()
    desc_lower = description.lower()

    if any(kw in ref_lower for kw in ("sicherung", "fuse", "-f1", "-f4", "-f5")):
        return "Sicherung", _IEC_REF_MAP["Sicherung"], True
    if any(kw in desc_lower for kw in ("schutz", "schalter", "hauptschalter", "not_aus")):
        return "Leitungsschutzschalter", _IEC_REF_MAP["Leitungsschutzschalter"], True
    if any(kw in ref_lower for kw in ("motor", "-m1", "-m2")):
        return "Drehstrommotor", _IEC_REF_MAP["Drehstrommotor"], True
    if any(kw in ref_lower for kw in ("relais", "relay", "-k1", "-k2", "-k5", "-k6", "-k7", "-k8")):
        if any(kw in desc_lower for kw in ("spule", "coil", "rührer", "pumpe")):
            return "Relaisspule", _IEC_REF_MAP["Relaisspule"], True
        return "Objekt (Block)", _IEC_REF_MAP["Objekt (Block)"], True
    if any(kw in ref_lower for kw in ("klemme", "klemmenleiste", "-x1", "-x2", "-x3", "-xd")):
        return "Klemmenleiste", _IEC_REF_MAP["Klemmenleiste"], True
    if any(kw in ref_lower for kw in ("-l1", "-l2", "-l3", "-l6", "-l7", "-n", "-pe",
                                        "potential", "schiene")):
        return "Leiter/Potentialschiene", _IEC_REF_MAP["Leiter/Potentialschiene"], True
    if "beckhoff" in ref_lower or "wago" in ref_lower:
        return "Objekt (Block)", _IEC_REF_MAP["Objekt (Block)"], True

    return "Objekt (Block)", _IEC_REF_MAP["Objekt (Block)"], True
