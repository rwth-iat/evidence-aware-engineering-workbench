from __future__ import annotations

import re
from collections import Counter, OrderedDict

from iev4pi_transformation_tool.core.utils import (
    build_header_map,
    canonical_field_name,
    clean_cell,
    family_title,
    guess_value_type,
    looks_like_identifier,
    normalize_label,
)
from iev4pi_transformation_tool.models import DocumentFamily, ParsedDocument, SchemaFamily, SchemaField


DEFAULT_TU_FIELDS: list[tuple[str, list[str], str]] = [
    ("tag", ["plt", "tag", "messstelle"], "Plant tag or instrument identifier"),
    ("signal_type", ["signal", "fkt", "funktion"], "Signal or function class"),
    ("manufacturer", ["hersteller", "manufacturer"], "Manufacturer name"),
    ("device", ["geraet", "device", "typ"], "Device designation or model"),
    ("serial_number", ["ser no", "s/n", "serial", "seriennummer"], "Serial number when available"),
    ("process_connection", ["prozess anschluss", "process connection", "anschluss"], "Process connection or fitting"),
    ("power_supply", ["hilfsenergie", "versorgung", "power supply"], "Supply voltage or auxiliary energy"),
    ("current_draw", ["stromaufnahme", "current draw"], "Current consumption"),
    ("measurement_range", ["range", "messbereich"], "Measurement range"),
    ("notes", ["bemerkung", "hinweis", "kommentar"], "Free-form remarks"),
    # Title block metadata fields (Stellenplan PDF header)
    ("projekt", ["projekt", "project"], "Project name from title block"),
    ("projekt_nr", ["projektnr", "projekt_nr", "project number"], "Project number from title block"),
    ("kunde", ["kunde", "customer"], "Customer from title block"),
    ("auftrag", ["auftrag", "order"], "Order number from title block"),
    ("position", ["position", "plant"], "Plant/position designator from title block"),
    ("anlage", ["anlage", "facility"], "Facility/plant code from title block"),
    ("dokument", ["dokument", "document"], "Document code from title block"),
    ("erstellt", ["erstellt", "created", "date_of_creation"], "Creation date from title block"),
    ("bearb", ["bearb", "edited_by"], "Edited by from title block"),
    ("geprueft", ["geprueft", "reviewed"], "Reviewed by from title block"),
    ("norm", ["norm", "standard"], "Standard/norm from title block"),
    ("software", ["software", "tool"], "Software tool from title block"),
    # Revision metadata
    ("revision_entry", ["revision", "rev", "revision_entry"], "Revision index from title block"),
    ("revision_date", ["revision_date", "rev_date", "revisionsdatum"], "Revision date from title block"),
    ("revision_name", ["revision_name", "rev_name"], "Revision author from title block"),
    ("revision_description", ["revision_description", "rev_desc"], "Revision description from title block"),
]

RI_DYNAMIC_FIELD_MIN_OCCURRENCES = 2
RI_NOISE_FIELD_TOKENS = {
    "uri",
    "vsui",
    "allow_",
    "disable_",
    "graphical_",
    "mapping",
    "rotation",
    "angle",
    "degree",
    "sandbox",
    "xml_class",
    "sort_",
    "version_",
    "autodelete",
    "pointer",
    "unit_system",
}

TU_IDENTIFIER_PREFIX_PATTERN = re.compile(
    r"^\s*\.?(?P<identifier>[A-Za-z]{1,4}\d+(?:\.[A-Za-z]\d+)+)\s+(?P<label>.+?)\s*$"
)


class SchemaMiner:
    def mine_family(self, family: DocumentFamily, documents: list[ParsedDocument]) -> SchemaFamily:
        if family in {
            DocumentFamily.STELLEN_OVERVIEW_RECORD,
            DocumentFamily.KLEMMENPLAN_ROW,
            DocumentFamily.VERSCHALTUNGSLISTE_ROW,
            DocumentFamily.CABINET_REFERENCE_ROW,
        }:
            return self._mine_tabular_family(family, documents)
        if family == DocumentFamily.STELLEN_TU_DATASHEET:
            return self._mine_tu_family(documents)
        if family == DocumentFamily.STROMLAUF_COMPONENT_GROUP:
            return self._fixed_component_group_schema()
        if family == DocumentFamily.STROMLAUF_COMPONENT:
            return self._fixed_component_schema()
        if family == DocumentFamily.STROMLAUF_CONNECTION:
            return self._fixed_connection_schema()
        if family in {
            DocumentFamily.RI_EQUIPMENT_ROW,
            DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW,
            DocumentFamily.RI_PIPING_COMPONENT_ROW,
            DocumentFamily.RI_CONNECTION_ROW,
        }:
            return self._mine_ri_family(family, documents)
        if family in {
            DocumentFamily.IFC_PIPING_ITEM_ROW,
            DocumentFamily.IFC_CONNECTION_ROW,
        }:
            return self._mine_ifc_family(family, documents)
        if family in {
            DocumentFamily.IFC_3D_ASSEMBLY_STEP,
            DocumentFamily.IFC_3D_ASSEMBLY_CONNECTION,
            DocumentFamily.IFC_3D_POSITION,
            DocumentFamily.IFC_3D_PART_LIBRARY,
        }:
            sheet_filter = {
                DocumentFamily.IFC_3D_ASSEMBLY_STEP: "Assembly_Steps",
                DocumentFamily.IFC_3D_ASSEMBLY_CONNECTION: "Connection_Topology",
                DocumentFamily.IFC_3D_POSITION: "Position_Data",
                DocumentFamily.IFC_3D_PART_LIBRARY: "Part_Library",
            }.get(family, "")
            return self._mine_tabular_family(family, documents, sheet_name=sheet_filter)
        return SchemaFamily(family=family, display_name=family_title(family))

    def _mine_tabular_family(self, family: DocumentFamily, documents: list[ParsedDocument], sheet_name: str = "") -> SchemaFamily:
        ordered_fields: OrderedDict[str, SchemaField] = OrderedDict()
        review_notes = [
            "Fields were mined from spreadsheet header rows.",
            "Review aliases and extraction hints before large batch runs.",
        ]
        for document in documents:
            for sheet in document.sheets:
                if sheet_name and sheet.name != sheet_name:
                    continue
                header_map = build_header_map(sheet.rows, sheet.header_rows)
                for header in header_map.values():
                    canonical = canonical_field_name(header)
                    if canonical not in ordered_fields:
                        ordered_fields[canonical] = SchemaField(
                            name=canonical,
                            aliases=[header],
                            family=family,
                            value_type=guess_value_type(header),
                            extraction_hint=f"Column header match for `{header}`.",
                        )
                    elif header not in ordered_fields[canonical].aliases:
                        ordered_fields[canonical].aliases.append(header)
        return SchemaFamily(
            family=family,
            display_name=family_title(family),
            fields=list(ordered_fields.values()),
            review_notes=review_notes,
        )

    def _mine_tu_family(self, documents: list[ParsedDocument]) -> SchemaFamily:
        fields: OrderedDict[str, SchemaField] = OrderedDict()
        for name, aliases, hint in DEFAULT_TU_FIELDS:
            fields[name] = SchemaField(
                name=name,
                aliases=aliases,
                family=DocumentFamily.STELLEN_TU_DATASHEET,
                extraction_hint=hint,
            )

        key_pattern = re.compile(r"^\s*([A-Za-zA-Z0-9/\- .]{2,40})\s*:\s*(.+)$")
        for document in documents:
            for page in document.pages:
                for table in page.tables:
                    rows = _group_table_rows(table.cells)
                    for row_index, (_, row_cells) in enumerate(rows, start=1):
                        cleaned_cells = [clean_cell(cell.text) for cell in row_cells if clean_cell(cell.text)]
                        if row_index == 1:
                            for header_text in cleaned_cells:
                                self._register_tu_field(
                                    fields,
                                    header_text,
                                    "Discovered from PDF table headers.",
                                )
                        if len(cleaned_cells) >= 2:
                            self._register_tu_field(
                                fields,
                                cleaned_cells[0],
                                "Discovered from PDF table key/value rows.",
                            )
                for pair in page.kv_pairs:
                    self._register_tu_field(fields, pair.key, "Discovered from structured key-value analysis.")
                for block in page.blocks:
                    for line in block.text.split("  "):
                        match = key_pattern.match(line.strip())
                        if not match:
                            continue
                        key = match.group(1).strip()
                        self._register_tu_field(fields, key, "Discovered from colon-delimited PDF/OCR text.")

        return SchemaFamily(
            family=DocumentFamily.STELLEN_TU_DATASHEET,
            display_name=family_title(DocumentFamily.STELLEN_TU_DATASHEET),
            fields=list(fields.values()),
            review_notes=[
                "TU datasheet fields use structured key-value analysis before retrieval.",
                "Fields with no supporting evidence remain blank during extraction.",
            ],
        )

    def _register_tu_field(
        self,
        fields: OrderedDict[str, SchemaField],
        alias: str,
        hint: str,
    ) -> None:
        cleaned_alias = alias.strip()
        if not cleaned_alias:
            return
        generic_alias = self._tu_generic_alias(cleaned_alias)
        # Skip OCR garbage: field names that are too long or have too many
        # underscores are merged multi-line OCR artifacts, not real parameters.
        if generic_alias.count("_") > 5 or len(generic_alias) > 60:
            return
        canonical = self._find_existing_tu_field(fields, [generic_alias, cleaned_alias]) or canonical_field_name(generic_alias)
        alias_values = self._tu_alias_values(cleaned_alias, generic_alias)
        if canonical not in fields:
            fields[canonical] = SchemaField(
                name=canonical,
                aliases=alias_values,
                family=DocumentFamily.STELLEN_TU_DATASHEET,
                extraction_hint=hint,
            )
            return
        for alias_value in alias_values:
            if alias_value not in fields[canonical].aliases:
                fields[canonical].aliases.append(alias_value)

    def _tu_generic_alias(self, alias: str) -> str:
        match = TU_IDENTIFIER_PREFIX_PATTERN.match(alias)
        if not match:
            return alias
        identifier = match.group("identifier").strip()
        label = clean_cell(match.group("label"))
        if not label or not looks_like_identifier(identifier):
            return alias
        return label

    def _find_existing_tu_field(self, fields: OrderedDict[str, SchemaField], aliases: list[str]) -> str | None:
        target_forms = {normalize_label(alias) for alias in aliases if alias.strip()}
        if not target_forms:
            return None
        for field_name, field in fields.items():
            known_forms = {normalize_label(field_name)}
            known_forms.update(normalize_label(alias) for alias in field.aliases)
            if known_forms.intersection(target_forms):
                return field_name
        return None

    def _tu_alias_values(self, original_alias: str, generic_alias: str) -> list[str]:
        values: list[str] = []
        for value in [generic_alias, original_alias]:
            if value and value not in values:
                values.append(value)
        return values

    def _fixed_component_group_schema(self) -> SchemaFamily:
        fields = [
            SchemaField(name="group_id", aliases=["group", "group id"], family=DocumentFamily.STROMLAUF_COMPONENT_GROUP),
            SchemaField(name="page_number", aliases=["page"], family=DocumentFamily.STROMLAUF_COMPONENT_GROUP, value_type="integer"),
            SchemaField(name="group_role", aliases=["role"], family=DocumentFamily.STROMLAUF_COMPONENT_GROUP),
            SchemaField(name="zone_path", aliases=["zones"], family=DocumentFamily.STROMLAUF_COMPONENT_GROUP),
            SchemaField(name="signal_tag", aliases=["signal", "tag"], family=DocumentFamily.STROMLAUF_COMPONENT_GROUP),
            SchemaField(name="cabinet", aliases=["cabinet", "panel"], family=DocumentFamily.STROMLAUF_COMPONENT_GROUP),
            SchemaField(name="bbox", aliases=["geometry"], family=DocumentFamily.STROMLAUF_COMPONENT_GROUP),
            SchemaField(name="part_ids", aliases=["parts"], family=DocumentFamily.STROMLAUF_COMPONENT_GROUP),
            SchemaField(name="raw_context", aliases=["context"], family=DocumentFamily.STROMLAUF_COMPONENT_GROUP),
        ]
        return SchemaFamily(
            family=DocumentFamily.STROMLAUF_COMPONENT_GROUP,
            display_name=family_title(DocumentFamily.STROMLAUF_COMPONENT_GROUP),
            fields=fields,
            review_notes=[
                "Structured component groups are detected from diagram geometry plus text pairing.",
                "Each record represents one connected component chain rather than one OCR token.",
            ],
        )

    def _fixed_component_schema(self) -> SchemaFamily:
        fields = [
            SchemaField(name="component_id", aliases=["component", "label"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="group_id", aliases=["group"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="parent_component_id", aliases=["parent"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="component_role", aliases=["role"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="display_label", aliases=["display", "label"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="logical_tag", aliases=["signal", "tag"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="article", aliases=["art"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="type_code", aliases=["typ", "device type"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="channel", aliases=["kanal"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="address", aliases=["adresse"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="terminal_labels", aliases=["pins", "terminals"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="unit", aliases=["measure unit"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="page_number", aliases=["page"], family=DocumentFamily.STROMLAUF_COMPONENT, value_type="integer"),
            SchemaField(name="cabinet", aliases=["cabinet", "panel"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="bbox", aliases=["geometry"], family=DocumentFamily.STROMLAUF_COMPONENT),
            SchemaField(name="raw_context", aliases=["context"], family=DocumentFamily.STROMLAUF_COMPONENT),
        ]
        return SchemaFamily(
            family=DocumentFamily.STROMLAUF_COMPONENT,
            display_name=family_title(DocumentFamily.STROMLAUF_COMPONENT),
            fields=fields,
            review_notes=[
                "Each component record represents one grouped subcomponent detected from the drawing.",
                "Token-only components are no longer emitted when no structured group exists.",
            ],
        )

    def _fixed_connection_schema(self) -> SchemaFamily:
        fields = [
            SchemaField(name="connection_id", aliases=["connection"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="group_id", aliases=["group"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="from_component_id", aliases=["source", "from"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="from_terminal", aliases=["source_pin", "from pin"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="via_component_id", aliases=["via"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="via_terminal", aliases=["via pin"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="to_component_id", aliases=["target", "to"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="to_terminal", aliases=["target_pin", "to pin"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="wire_label", aliases=["signal", "line"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="page_number", aliases=["page"], family=DocumentFamily.STROMLAUF_CONNECTION, value_type="integer"),
            SchemaField(name="trace_path", aliases=["geometry"], family=DocumentFamily.STROMLAUF_CONNECTION),
            SchemaField(name="confidence", aliases=["score"], family=DocumentFamily.STROMLAUF_CONNECTION, value_type="number"),
            SchemaField(name="raw_context", aliases=["context"], family=DocumentFamily.STROMLAUF_CONNECTION),
        ]
        return SchemaFamily(
            family=DocumentFamily.STROMLAUF_CONNECTION,
            display_name=family_title(DocumentFamily.STROMLAUF_CONNECTION),
            fields=fields,
            review_notes=[
                "Connections are emitted from traced structured wire bundles.",
                "Legacy graph-only fallback remains available for non-structured diagrams.",
            ],
        )

    def _mine_ri_family(self, family: DocumentFamily, documents: list[ParsedDocument]) -> SchemaFamily:
        field_map: OrderedDict[str, SchemaField] = OrderedDict()
        category = self._ri_category_for_family(family)
        dynamic_counts: Counter[str] = Counter()
        dynamic_aliases: dict[str, list[str]] = {}
        relevant_xsd = []
        for field_name, hint, value_type in self._ri_base_fields(family):
            field_map[field_name] = SchemaField(
                name=field_name,
                aliases=list(self._ri_core_aliases(family).get(field_name, [])),
                family=family,
                value_type=value_type,
                extraction_hint=hint,
            )

        for document in documents:
            package = document.ri_package
            if package is None:
                continue
            pdf_aliases = self._ri_pdf_aliases(document)
            for field_name, aliases in pdf_aliases.items():
                existing = field_map.get(field_name)
                if existing is None:
                    continue
                for alias in aliases:
                    if alias not in existing.aliases:
                        existing.aliases.append(alias)

            if family == DocumentFamily.RI_CONNECTION_ROW:
                for edge in package.xml_edges:
                    for attribute_name in edge.attributes:
                        self._accumulate_ri_dynamic_field(
                            field_map,
                            attribute_name,
                            family,
                            dynamic_counts,
                            dynamic_aliases,
                        )
                relevant_xsd.extend(
                    field for field in package.xsd_field_defs if field.category in {"connection", "common"}
                )
            else:
                relevant_nodes = [node for node in package.xml_nodes if node.category == category]
                for node in relevant_nodes:
                    for attribute_name in node.attributes:
                        self._accumulate_ri_dynamic_field(
                            field_map,
                            attribute_name,
                            family,
                            dynamic_counts,
                            dynamic_aliases,
                        )
                relevant_xsd.extend(
                    field for field in package.xsd_field_defs if field.category in {category, "common"}
                )

        for field_name, count in sorted(dynamic_counts.items()):
            if count < RI_DYNAMIC_FIELD_MIN_OCCURRENCES:
                continue
            aliases = dynamic_aliases.get(field_name, [])
            field_map[field_name] = SchemaField(
                name=field_name,
                aliases=list(aliases),
                family=family,
                value_type=guess_value_type(aliases[0] if aliases else field_name),
                extraction_hint="Frequently occurring DEXPI attribute kept for R&I review and extraction.",
            )

        for xsd_field in relevant_xsd:
            existing = field_map.get(xsd_field.name)
            if existing is None:
                continue
            if xsd_field.xml_name and xsd_field.xml_name not in existing.aliases:
                existing.aliases.append(xsd_field.xml_name)
            if existing.value_type == "string" and xsd_field.value_type != "string":
                existing.value_type = xsd_field.value_type
            hint_bits = [existing.extraction_hint.strip()] if existing.extraction_hint.strip() else []
            if xsd_field.description:
                hint_bits.append(xsd_field.description)
            if xsd_field.enumeration_values:
                hint_bits.append(f"Allowed values: {', '.join(xsd_field.enumeration_values[:8])}.")
            existing.extraction_hint = " ".join(dict.fromkeys(bit for bit in hint_bits if bit)).strip()

        display_name = family_title(family)
        bundle_name = next(
            (document.metadata.get("ri_display_name", "") for document in documents if document.metadata.get("ri_display_name")),
            "",
        )
        return SchemaFamily(
            family=family,
            display_name=display_name,
            fields=list(field_map.values()),
            review_notes=[
                f"R&I schema derived from DEXPI XML, XSD definitions, and PDF evidence for {bundle_name or 'the current bundle'}.",
                "XML/DEXPI values take priority. PDF OCR contributes aliases and evidence but does not override explicit XML topology.",
            ],
            bundle_name=bundle_name,
            sheet_name=self._ri_sheet_name(family),
            source_root="R&I-Fließbild",
        )

    def _accumulate_ri_dynamic_field(
        self,
        field_map: OrderedDict[str, SchemaField],
        family: DocumentFamily,
        field_name: str,
        counter: Counter[str],
        alias_map: dict[str, list[str]],
    ) -> None:
        canonical = canonical_field_name(field_name)
        if not canonical:
            return
        core_field = self._ri_core_field_lookup(family).get(canonical)
        alias = clean_cell(field_name)
        if core_field:
            existing = field_map.get(core_field)
            if existing is not None and alias and alias not in existing.aliases:
                existing.aliases.append(alias)
            return
        if not self._ri_allow_dynamic_field(canonical):
            return
        counter[canonical] += 1
        if alias:
            alias_map.setdefault(canonical, [])
            if alias not in alias_map[canonical]:
                alias_map[canonical].append(alias)

    def _ri_category_for_family(self, family: DocumentFamily) -> str:
        mapping = {
            DocumentFamily.RI_EQUIPMENT_ROW: "equipment",
            DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW: "instrument_function",
            DocumentFamily.RI_PIPING_COMPONENT_ROW: "piping_component",
            DocumentFamily.RI_CONNECTION_ROW: "connection",
        }
        return mapping[family]

    def _ri_sheet_name(self, family: DocumentFamily) -> str:
        mapping = {
            DocumentFamily.RI_EQUIPMENT_ROW: "equipment",
            DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW: "instrument_functions",
            DocumentFamily.RI_PIPING_COMPONENT_ROW: "piping_components",
            DocumentFamily.RI_CONNECTION_ROW: "connections",
        }
        return mapping[family]

    def _ri_base_fields(self, family: DocumentFamily) -> list[tuple[str, str, str]]:
        if family == DocumentFamily.RI_CONNECTION_ROW:
            return [
                ("from_id", "DEXPI source node identifier.", "string"),
                ("to_id", "DEXPI target node identifier.", "string"),
                ("edge_type", "DEXPI connection type.", "string"),
                ("class_name", "DEXPI connection class.", "string"),
                ("sub_class", "DEXPI connection subclass.", "string"),
                ("name", "DEXPI connection name or label.", "string"),
                ("purpose", "DEXPI connection purpose or function.", "string"),
                ("context", "Connection context or free-form supporting detail.", "string"),
                ("source_locator", "XML connection locator or PDF fallback region.", "string"),
                ("evidence_summary", "Key XML/PDF evidence used for this connection.", "string"),
            ]
        if family == DocumentFamily.RI_INSTRUMENT_FUNCTION_ROW:
            return [
                ("canonical_tag", "Canonical loop tag such as TU10.T41.", "string"),
                ("function_code", "Instrument function code such as TI or PI.", "string"),
                ("label_text", "Combined display label such as TI TU10.T41.", "string"),
                ("loop_node_id", "DEXPI loop object identifier.", "string"),
                ("function_node_id", "DEXPI process instrumentation identifier.", "string"),
                ("tag_name", "Preferred displayed tag from DEXPI/PDF.", "string"),
                ("node_id", "Canonical DEXPI object identifier.", "string"),
                ("class_name", "DEXPI ComponentClass value.", "string"),
                ("sub_class", "DEXPI GenericAttribute SUB_CLASS value.", "string"),
                ("normalized_type", "Normalized equipment or instrument type.", "string"),
                ("description", "Description or long text from DEXPI/PDF.", "string"),
                ("name", "DEXPI object name when available.", "string"),
                ("piping_anchor_id", "Nearest piping anchor used for context.", "string"),
                ("from_equipment", "Piping segment source equipment.", "string"),
                ("to_equipment", "Piping segment target equipment.", "string"),
                ("context_summary", "Short process context derived from DEXPI topology.", "string"),
                ("label", "Visible label or short caption from DEXPI/PDF.", "string"),
                ("source_locator", "XML object locator or supporting PDF page.", "string"),
                ("evidence_summary", "Key XML/PDF evidence used for this object.", "string"),
            ]
        return [
            ("tag_name", "Preferred displayed tag from DEXPI/PDF.", "string"),
            ("node_id", "Canonical DEXPI object identifier.", "string"),
            ("class_name", "DEXPI ComponentClass value.", "string"),
            ("sub_class", "DEXPI GenericAttribute SUB_CLASS value.", "string"),
            ("normalized_type", "Normalized equipment or instrument type.", "string"),
            ("description", "Description or long text from DEXPI/PDF.", "string"),
            ("name", "DEXPI object name when available.", "string"),
            ("label", "Visible label or short caption from DEXPI/PDF.", "string"),
            ("source_locator", "XML object locator or supporting PDF page.", "string"),
            ("evidence_summary", "Key XML/PDF evidence used for this object.", "string"),
        ]

    def _ri_pdf_aliases(self, document: ParsedDocument) -> dict[str, list[str]]:
        alias_map: dict[str, list[str]] = {}
        for page in document.pages:
            for pair in page.kv_pairs:
                canonical = canonical_field_name(pair.key)
                if not canonical:
                    continue
                alias_map.setdefault(canonical, [])
                if pair.key not in alias_map[canonical]:
                    alias_map[canonical].append(pair.key)
            for table in page.tables:
                rows = _group_table_rows(table.cells)
                if not rows:
                    continue
                header_cells = [clean_cell(cell.text) for cell in rows[0][1] if clean_cell(cell.text)]
                for header in header_cells:
                    canonical = canonical_field_name(header)
                    alias_map.setdefault(canonical, [])
                    if header not in alias_map[canonical]:
                        alias_map[canonical].append(header)
        return alias_map

    def _ri_core_aliases(self, family: DocumentFamily) -> dict[str, list[str]]:
        if family == DocumentFamily.RI_CONNECTION_ROW:
            return {
                "from_id": ["FromID", "from_id"],
                "to_id": ["ToID", "to_id"],
                "edge_type": ["ConnectionType", "edge_type"],
                "class_name": ["ComponentClass", "class_name"],
                "sub_class": ["SUB_CLASS", "sub_class"],
                "name": ["Name", "name"],
                "purpose": ["Purpose", "purpose"],
                "context": ["Context", "context"],
            }
        return {
            "canonical_tag": ["TagName", "LoopTag", "canonical_tag"],
            "function_code": ["FunctionCode", "function_code"],
            "label_text": ["LabelText", "label_text"],
            "loop_node_id": ["LoopID", "loop_node_id"],
            "function_node_id": ["FunctionID", "function_node_id"],
            "tag_name": ["TagName", "tag_name"],
            "node_id": ["ID", "node_id"],
            "class_name": ["ComponentClass", "class_name"],
            "sub_class": ["SUB_CLASS", "sub_class"],
            "normalized_type": ["normalized_type"],
            "description": ["Description", "description"],
            "name": ["Name", "name"],
            "piping_anchor_id": ["PipingAnchorID", "piping_anchor_id"],
            "from_equipment": ["From equipment", "from_equipment"],
            "to_equipment": ["To equipment", "to_equipment"],
            "context_summary": ["ContextSummary", "context_summary"],
            "label": ["Label", "label"],
        }

    def _mine_ifc_family(self, family: DocumentFamily, documents: list[ParsedDocument]) -> SchemaFamily:
        field_map: OrderedDict[str, SchemaField] = OrderedDict()
        dynamic_counts: Counter[str] = Counter()
        dynamic_aliases: dict[str, list[str]] = {}
        for field_name, hint, value_type in self._ifc_base_fields(family):
            field_map[field_name] = SchemaField(
                name=field_name,
                aliases=[field_name],
                family=family,
                value_type=value_type,
                extraction_hint=hint,
            )
        for document in documents:
            package = document.ifc_package
            if package is None:
                continue
            if family == DocumentFamily.IFC_PIPING_ITEM_ROW:
                for node in package.ifc_nodes:
                    for attribute_name in node.attributes:
                        self._accumulate_ifc_dynamic_field(field_map, attribute_name, family, dynamic_counts, dynamic_aliases)
            else:
                for edge in package.ifc_edges:
                    for attribute_name in edge.attributes:
                        self._accumulate_ifc_dynamic_field(field_map, attribute_name, family, dynamic_counts, dynamic_aliases)
        for field_name, count in sorted(dynamic_counts.items()):
            if count < 1:
                continue
            if field_name in field_map:
                continue
            field_map[field_name] = SchemaField(
                name=field_name,
                aliases=list(dynamic_aliases.get(field_name, [])),
                family=family,
                extraction_hint="IFC attribute preserved for attribute-level comparison.",
            )
        return SchemaFamily(
            family=family,
            display_name=family_title(family),
            fields=list(field_map.values()),
            review_notes=[
                "IFC fields are extracted from exact attributes and property sets only.",
                "Strict matching uses normalized explicit identifiers rather than fuzzy OCR similarity.",
            ],
            source_root="IFC",
        )

    def _ifc_base_fields(self, family: DocumentFamily) -> list[tuple[str, str, str]]:
        if family == DocumentFamily.IFC_CONNECTION_ROW:
            return [
                ("from_id", "IFC source object GlobalId.", "string"),
                ("to_id", "IFC target object GlobalId.", "string"),
                ("relation_type", "IFC relation class.", "string"),
                ("source_locator", "IFC relation locator.", "string"),
                ("evidence_summary", "Key IFC relation evidence.", "string"),
            ]
        return [
            ("node_id", "IFC GlobalId or stable object identifier.", "string"),
            ("ifc_class", "IFC class name.", "string"),
            ("name", "IFC Name attribute.", "string"),
            ("tag", "IFC Tag attribute.", "string"),
            ("object_type", "IFC ObjectType value.", "string"),
            ("predefined_type", "IFC PredefinedType value.", "string"),
            ("description", "IFC Description attribute.", "string"),
            ("match_keys", "Strict normalized identifiers extracted from IFC.", "string"),
            ("flange_complete", "Whether explicit flange metadata is complete.", "boolean"),
            ("source_locator", "IFC node locator.", "string"),
            ("evidence_summary", "Key IFC object evidence.", "string"),
        ]

    def _accumulate_ifc_dynamic_field(
        self,
        field_map: OrderedDict[str, SchemaField],
        field_name: str,
        family: DocumentFamily,
        counter: Counter[str],
        alias_map: dict[str, list[str]],
    ) -> None:
        canonical = canonical_field_name(field_name)
        if not canonical or canonical in field_map:
            return
        if not self._ri_allow_dynamic_field(canonical):
            return
        counter[canonical] += 1
        alias = clean_cell(field_name)
        if alias:
            alias_map.setdefault(canonical, [])
            if alias not in alias_map[canonical]:
                alias_map[canonical].append(alias)

    def _ri_core_field_lookup(self, family: DocumentFamily) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for field_name, aliases in self._ri_core_aliases(family).items():
            lookup[canonical_field_name(field_name)] = field_name
            for alias in aliases:
                alias_name = canonical_field_name(alias)
                if alias_name:
                    lookup[alias_name] = field_name
        return lookup

    def _ri_allow_dynamic_field(self, canonical: str) -> bool:
        if not canonical or canonical in {"x", "y", "z"}:
            return False
        return not any(token in canonical for token in RI_NOISE_FIELD_TOKENS)


def _group_table_rows(cells) -> list[tuple[int, list]]:
    rows: OrderedDict[int, list] = OrderedDict()
    for cell in sorted(cells, key=lambda item: (item.row_id, item.col_id)):
        rows.setdefault(cell.row_id, []).append(cell)
    return list(rows.items())
