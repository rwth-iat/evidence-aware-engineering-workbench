"""AIO Schema Mapping — canonical mapping from legacy 3 templates to AIO 28-sheet schema.

This file is the single source of truth for the migration.  Every column in the
3 legacy templates (Klemmenplan, Stellenplan, Stromlaufplan) is mapped to its
AIO counterpart: target sheet, attribute name, scope, and encoding rules.

Design principle (per Phase 0.2 validation):
  - LLM handles *semantic classification* (Element_Type, field-name matching)
  - Deterministic lookups handle *spec-defined encodings* (IEC 60757 colors,
    IEC 81346-2 class letters, enum allowed-values)
  - This file provides the fallback / reference mapping when LLM is unavailable
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════════
# 1. Sheet-level mapping: (legacy_template, legacy_sheet) → AIO sheet(s)
# ══════════════════════════════════════════════════════════════════════════════

# Each entry maps one legacy sheet to one or more AIO target sheets.
# A value of None means the sheet content goes to a specific sub-structure
# (e.g. EAV rows in Element_Data) rather than a 1:1 sheet copy.

SHEET_MAPPING: dict[tuple[str, str], list[str]] = {
    # ── Klemmenplan ──
    ("klemmenplan", "Document_ID"):       ["Document_ID", "Schema_Metadata"],
    ("klemmenplan", "Dokument_Data"):     ["Document_Data", "Document_Data_Source"],
    ("klemmenplan", "Layer_ID"):          ["Layer_ID"],
    ("klemmenplan", "Object_ID"):         ["Object", "Cluster", "Object_Cluster"],
    ("klemmenplan", "Object_Data"):       ["Element_Data", "Element_Data_Source"],
    ("klemmenplan", "Terminal_ID"):       ["Element_ID", "Elements_TopDown",
                                           "Elements_from_Cluster", "Match_Result"],
    ("klemmenplan", "Terminal_Data"):     ["Element_Data", "Element_Data_Source",
                                           "Connection_ID", "Connection_Data",
                                           "Connection_Data_Source"],

    # ── Stellenplan ──
    ("stellenplan", "Document_ID"):       ["Document_ID", "Schema_Metadata"],
    ("stellenplan", "Document_Data"):     ["Document_Data", "Document_Data_Source"],
    ("stellenplan", "Revision_Data"):     ["Revision_Data", "Revision_Data_Source"],
    ("stellenplan", "Layer_ID"):          ["Layer_ID"],
    ("stellenplan", "Instrument_Data"):   ["Document_RepresentedItem",
                                           "Element_ID", "Elements_TopDown"],
    ("stellenplan", "Object_ID"):         ["Object", "Cluster", "Object_Cluster"],
    ("stellenplan", "Component_ID"):      ["Element_ID", "Elements_TopDown",
                                           "Elements_from_Cluster", "Match_Result"],
    ("stellenplan", "Component_Classification"): ["Element_Classification",
                                                   "Element_Classification_Source"],
    ("stellenplan", "Component_Data"):    ["Element_Data", "Element_Data_Source"],
    ("stellenplan", "Connection_Data"):   ["Connection_ID", "Connection_Data",
                                           "Connection_Data_Source"],

    # ── Stromlaufplan ──
    ("stromlaufplan", "Document_ID"):     ["Document_ID", "Schema_Metadata"],
    ("stromlaufplan", "Document_Data"):   ["Document_Data", "Document_Data_Source"],
    ("stromlaufplan", "Revision_Data"):   ["Revision_Data", "Revision_Data_Source"],
    ("stromlaufplan", "Layer_ID"):        ["Layer_ID"],
    ("stromlaufplan", "Object_ID"):       ["Object", "Cluster", "Object_Cluster"],
    ("stromlaufplan", "Element_ID"):      ["Element_ID", "Elements_TopDown",
                                           "Elements_from_Cluster", "Match_Result"],
    ("stromlaufplan", "Element_Classification"): ["Element_Classification",
                                                   "Element_Classification_Source"],
    ("stromlaufplan", "Element_Data"):    ["Element_Data", "Element_Data_Source"],
    ("stromlaufplan", "Connection_Data"): ["Connection_ID", "Connection_Data",
                                           "Connection_Data_Source"],
}


# ══════════════════════════════════════════════════════════════════════════════
# 2. Document-type → AIO Document_Type (for Document_ID.Document_Type column)
# ══════════════════════════════════════════════════════════════════════════════

DOCUMENT_TYPE_MAP: dict[str, str] = {
    "stellenplan":   "Instrument_Loop_Diagram",
    "klemmenplan":   "Terminal_Diagram",
    "stromlaufplan": "Circuit_Diagram",
}


# ══════════════════════════════════════════════════════════════════════════════
# 3. Element_Type mapping: legacy type/classification → AIO Element_Type
# ══════════════════════════════════════════════════════════════════════════════

# Maps from legacy component/object classifiers to AIO Element_Types (per §5).
# These are fallbacks; the LLM classifier (llm_element_classifier.py) handles
# ambiguous cases.  IEC 81346-2 class per AIO spec §5.12.

ELEMENT_TYPE_MAP: dict[str, dict[str, str]] = {
    # ── Terminal & Terminal_Strip ──
    "terminal":                {"element_type": "Terminal",       "iec_class": "X", "caex_type": "ExternalInterface"},
    "terminal_strip":          {"element_type": "Terminal_Strip", "iec_class": "X", "caex_type": "InternalElement"},
    "klemme":                  {"element_type": "Terminal",       "iec_class": "X", "caex_type": "ExternalInterface"},
    "klemmenleiste":           {"element_type": "Terminal_Strip", "iec_class": "X", "caex_type": "InternalElement"},
    "klemmleiste":             {"element_type": "Terminal_Strip", "iec_class": "X", "caex_type": "InternalElement"},

    # ── Contactor / Relay ──
    "contactor":               {"element_type": "Contactor",            "iec_class": "Q", "caex_type": "InternalElement"},
    "schuetz":                 {"element_type": "Contactor",            "iec_class": "Q", "caex_type": "InternalElement"},
    "leistungsschuetz":        {"element_type": "Contactor",            "iec_class": "Q", "caex_type": "InternalElement"},
    "auxiliary_contactor":     {"element_type": "Auxiliary_Contactor",  "iec_class": "K", "caex_type": "InternalElement"},
    "hilfsschuetz":            {"element_type": "Auxiliary_Contactor",  "iec_class": "K", "caex_type": "InternalElement"},
    "relais":                  {"element_type": "Auxiliary_Contactor",  "iec_class": "K", "caex_type": "InternalElement"},
    "coil":                    {"element_type": "Coil",                 "iec_class": "Q", "caex_type": "InternalElement"},
    "spule":                   {"element_type": "Coil",                 "iec_class": "Q", "caex_type": "InternalElement"},
    "main_contact":            {"element_type": "Main_Contact",         "iec_class": "Q", "caex_type": "InternalElement"},
    "hauptkontakt":            {"element_type": "Main_Contact",         "iec_class": "Q", "caex_type": "InternalElement"},
    "auxiliary_contact":       {"element_type": "Auxiliary_Contact",    "iec_class": "Q", "caex_type": "InternalElement"},
    "hilfskontakt":            {"element_type": "Auxiliary_Contact",    "iec_class": "Q", "caex_type": "InternalElement"},
    "schliesser":              {"element_type": "Auxiliary_Contact",    "iec_class": "Q", "caex_type": "InternalElement"},
    "oeffner":                 {"element_type": "Auxiliary_Contact",    "iec_class": "Q", "caex_type": "InternalElement"},

    # ── Protection ──
    "fuse":                    {"element_type": "Fuse",             "iec_class": "F", "caex_type": "InternalElement"},
    "sicherung":               {"element_type": "Fuse",             "iec_class": "F", "caex_type": "InternalElement"},
    "circuit_breaker":         {"element_type": "Circuit_Breaker",  "iec_class": "F", "caex_type": "InternalElement"},
    "leitungsschutzschalter":  {"element_type": "Circuit_Breaker",  "iec_class": "F", "caex_type": "InternalElement"},
    "ls_schalter":             {"element_type": "Circuit_Breaker",  "iec_class": "F", "caex_type": "InternalElement"},
    "fi_ls":                   {"element_type": "Circuit_Breaker",  "iec_class": "F", "caex_type": "InternalElement"},
    "rcd":                     {"element_type": "Circuit_Breaker",  "iec_class": "F", "caex_type": "InternalElement"},
    "rcbo":                    {"element_type": "Circuit_Breaker",  "iec_class": "F", "caex_type": "InternalElement"},

    # ── Switch ──
    "switch":                  {"element_type": "Switch",           "iec_class": "Q", "caex_type": "InternalElement"},
    "schalter":                {"element_type": "Switch",           "iec_class": "Q", "caex_type": "InternalElement"},
    "hauptschalter":           {"element_type": "Switch",           "iec_class": "Q", "caex_type": "InternalElement"},
    "not_aus":                 {"element_type": "Switch",           "iec_class": "S", "caex_type": "InternalElement"},
    "lasttrennschalter":       {"element_type": "Switch",           "iec_class": "Q", "caex_type": "InternalElement"},
    "wahlschalter":            {"element_type": "Switch",           "iec_class": "S", "caex_type": "InternalElement"},
    "drucktaster":             {"element_type": "Switch",           "iec_class": "S", "caex_type": "InternalElement"},

    # ── Socket ──
    "socket_outlet":           {"element_type": "Socket_Outlet",    "iec_class": "X", "caex_type": "InternalElement"},
    "steckdose":               {"element_type": "Socket_Outlet",    "iec_class": "X", "caex_type": "InternalElement"},
    "schuko":                  {"element_type": "Socket_Outlet",    "iec_class": "X", "caex_type": "InternalElement"},

    # ── Power Supply ──
    "power_supply":            {"element_type": "Power_Supply",     "iec_class": "T", "caex_type": "InternalElement"},
    "netzteil":                {"element_type": "Power_Supply",     "iec_class": "T", "caex_type": "InternalElement"},

    # ── PLC ──
    "plc_module":              {"element_type": "PLC_Module",       "iec_class": "K", "caex_type": "InternalElement"},
    "io_module":               {"element_type": "PLC_Module",       "iec_class": "K", "caex_type": "InternalElement"},
    "baugruppe":               {"element_type": "PLC_Module",       "iec_class": "K", "caex_type": "InternalElement"},

    # ── Motor / Actuator / Sensor / Heater ──
    "motor":                   {"element_type": "Motor",            "iec_class": "M", "caex_type": "InternalElement"},
    "valve_actuator":          {"element_type": "Valve_Actuator",   "iec_class": "M", "caex_type": "InternalElement"},
    "ventil":                  {"element_type": "Valve_Actuator",   "iec_class": "M", "caex_type": "InternalElement"},
    "sensor":                  {"element_type": "Sensor",           "iec_class": "B", "caex_type": "InternalElement"},
    "messumformer":            {"element_type": "Sensor",           "iec_class": "B", "caex_type": "InternalElement"},
    "transmitter":             {"element_type": "Sensor",           "iec_class": "B", "caex_type": "InternalElement"},
    "heater":                  {"element_type": "Heater",           "iec_class": "E", "caex_type": "InternalElement"},
    "heizung":                 {"element_type": "Heater",           "iec_class": "E", "caex_type": "InternalElement"},

    # ── Indicator ──
    "indicator_lamp":          {"element_type": "Indicator_Lamp",   "iec_class": "P", "caex_type": "InternalElement"},
    "leuchtmelder":            {"element_type": "Indicator_Lamp",   "iec_class": "P", "caex_type": "InternalElement"},
    "meldeleuchte":            {"element_type": "Indicator_Lamp",   "iec_class": "P", "caex_type": "InternalElement"},

    # ── Cabinet ──
    "cabinet_aggregate":       {"element_type": "Cabinet_Aggregate","iec_class": "A", "caex_type": "InternalElement"},
    "schaltschrank":           {"element_type": "Cabinet_Aggregate","iec_class": "A", "caex_type": "InternalElement"},

    # ── Generic fallbacks ──
    "actuator":                {"element_type": "Actuator",         "iec_class": "M", "caex_type": "InternalElement"},
    "consumer":                {"element_type": "Consumer",         "iec_class": "M", "caex_type": "InternalElement"},
    "component":               {"element_type": "Consumer",         "iec_class": "M", "caex_type": "InternalElement"},
    "instrument":              {"element_type": "Sensor",           "iec_class": "B", "caex_type": "InternalElement"},
    # ── LLM-classified types → spec-compliant Element_Types ──
    # Rectifier/Transformer are subtypes of Power_Supply per IEC 81346-2 class T
    "transformer":             {"element_type": "Power_Supply",     "iec_class": "T", "caex_type": "InternalElement"},
    "rectifier":               {"element_type": "Power_Supply",     "iec_class": "T", "caex_type": "InternalElement"},
    # Connector → Socket_Outlet (closest valid spec type for connecting objects, IEC class X)
    "connector":               {"element_type": "Socket_Outlet",    "iec_class": "X", "caex_type": "InternalElement"},
    "transducer":              {"element_type": "Transducer",       "iec_class": "B", "caex_type": "InternalElement"},
    "thermostat":              {"element_type": "Thermostat",       "iec_class": "B", "caex_type": "InternalElement"},
}


# ══════════════════════════════════════════════════════════════════════════════
# 4. Wire Color: German source word → IEC 60757 code
# ══════════════════════════════════════════════════════════════════════════════

WIRE_COLOR_MAP: dict[str, str] = {
    # Standard IEC 60757 codes
    "rot": "RD", "rd": "RD", "red": "RD",
    "blau": "BU", "bu": "BU", "blue": "BU",
    "gruen": "GN", "gn": "GN", "green": "GN",
    "gelb": "YE", "ge": "YE", "yellow": "YE",
    "grau": "GY", "gr": "GY", "grey": "GY", "gray": "GY",
    "braun": "BN", "br": "BN", "brown": "BN",
    "schwarz": "BK", "sw": "BK", "black": "BK",
    "weiss": "WH", "ws": "WH", "white": "WH", "weiß": "WH",
    "orange": "OG", "or": "OG",
    "violett": "VT", "vi": "VT", "violet": "VT", "purple": "VT",
    "gruen/gelb": "GNYE", "gn/ge": "GNYE", "gr/ge": "GNYE",
    "gruenge": "GNYE", "gnge": "GNYE", "green/yellow": "GNYE",
    "rosa": "PK", "pink": "PK",
    "tuerkis": "TQ", "turkis": "TQ", "türkis": "TQ",
    "hellblau": "BU",   # IEC 60757: standard code is BU; no LBU in the norm
    "dunkelblau": "BU",  # IEC 60757: standard code is BU
    "hellgrau": "GY",
    "dunkelgrau": "GY",
    "hellbraun": "BN",
    "dunkelbraun": "BN",
    "rot/blau": "RDBU",
    # Non-encodable → Unspecifiable per E5 edge-case rule
    "transparent": "Unspecifiable",
    "beige": "Unspecifiable",
    "nicht belegt": "Unspecifiable",
    "unused": "Unspecifiable",
    "frei": "Unspecifiable",
    "reserve": "Unspecifiable",
}


# ══════════════════════════════════════════════════════════════════════════════
# 5. Polarity mapping: source word → AIO Polarity enum
# ══════════════════════════════════════════════════════════════════════════════

POLARITY_MAP: dict[str, str] = {
    "l1": "L1", "L1": "L1",
    "l2": "L2", "L2": "L2",
    "l3": "L3", "L3": "L3",
    "l+": "L+", "L+": "L+",
    "l-": "L-", "L-": "L-",
    "n": "N", "N": "N",
    "pe": "PE", "PE": "PE",
    "pen": "PEN", "PEN": "PEN",
    "+": "L+", "-": "L-",
    "g+": "G+", "g-": "G-",
    "v+": "V+", "v-": "V-",
    "fe": "FE",
    "ac": "AC", "dc": "DC",
    "1": "L1", "2": "L2", "3": "L3",
}


# ══════════════════════════════════════════════════════════════════════════════
# 6. Document-type-specific attribute mappings (legacy field → AIO Attribute_Name)
# ══════════════════════════════════════════════════════════════════════════════

# Scope=Document attributes (go into Document_Data EAV rows)
DOCUMENT_ATTRIBUTE_MAP: dict[str, str] = {
    # Stellenplan Document_Data fields
    "Plant_Entry":              "Plant",
    "Position_Entry":           "Position",
    "Document_Entry":           "Document_Designation",
    "Describtion_Entry":        "Description",
    "Projekt":                  "Project_Name",
    "Project_Nr_Entry":         "Project_Number",
    "Customer_Entry":           "Customer",
    "Order_Entry":              "Order_Number",
    "Date":                     "Creation_Date",
    "Date_Of_Creation_Entry":   "Creation_Date",
    "Edited_By_Entry":          "Edited_By",
    "Reviewed_Entry":           "Reviewed_By",
    "Norm":                     "Normative_Reference",
    "Software":                 "Software",
    # Klemmenplan Dokument_Data fields
    "Name_Entry":               "Document_Name",
    "Description":              "Description",
    "Plant_Entry":              "Plant",
    "PLC_Entry":                "PLC_Identifier",
    "Document_Type":            "Source_Format",
    "Version_Entry":            "Version",
    "Update_Entry":             "Last_Update",
    # Stromlaufplan Document_Data fields
    "Sheet_Number":             "Sheet_Number",
    "Total_Sheets":             "Total_Sheets",
    "Sheet_Name":               "Sheet_Name",
    "Sheet_Type":               "Document_Subtype",
    "Location_Entry":           "Location",
    "Project_Entry":            "Project_Name",
    "Date_Entry":               "Creation_Date",
    "Author_Entry":             "Author",
    "Drawing_Nr_Customer":      "Customer_Drawing_Number",
    "Drawing_Nr_Planner":       "Planner_Drawing_Number",
    "Origin_Entry":             "Origin",
    "Replaces_Entry":           "Replaces",
    "Replaced_By_Entry":        "Replaced_By",
}

# Scope=Element attributes (go into Element_Data EAV rows)
ELEMENT_ATTRIBUTE_MAP: dict[str, str] = {
    # German→English direct mappings (known from source documents)
    "beschreibung":             "Description",
    "funktion_und_bestelldaten": "Description",
    "bezeichnung_im_stromlaufplan": "Cross_Reference_Raw",
    "anschluss_der_feldgerate": "Connection_Point_From",
    "gerat":                    "Device_ID",
    "e_schrank":                "Cabinet",
    "m_s_r_schrank":            "Cabinet",
    "plt_stelle":               "PLT_Position",
    "klemmleiste_x01":          "Terminal_Strip_Designation",
    "klemmleiste_x1_2":         "Terminal_Strip_Designation",
    "klemmleiste_x_02":         "Terminal_Strip_Designation",
    "page_number":              "Sheet_Number",
    "raw_context":              "Cross_Reference_Raw",
    "trace_path":               "Current_Path_Number",
    "wire_label":               "Wire_Label",
    "display_label":            "Wire_Label",
    "component_id":             "Device_ID",
    "component_role":           "Component_Role",
    "logical_tag":              "Canonical_Tag",
    "confidence":               "Confidence",
    "bbox":                     "BBox",
    # Terminal attributes (Klemmenplan)
    "PLTStelle":                "PLT_Position",
    "Funktion":                 "Function",
    "Beschreibung":             "Description",
    "ESchrank":                 "Cabinet",
    "WireLabel":                "Wire_Label",
    "DeviceId":                 "Device_ID",
    "CanonicalTag":             "Canonical_Tag",
    # Component attributes (Stellenplan)
    "Component_Role":           "Component_Role",
    "Classification":           "IEC_60617_Classification",
    # Element attributes (Stromlaufplan)
    "IEC_60617_Ref":            "IEC_60617_Reference",
    "Main_Sub":                 "Main_Sub_Element",
    # Cross-reference attributes
    "Cross_Reference":          "Cross_Reference_Raw",
    # Terminal-specific
    "Terminal_Number":          "Terminal_Number",
    "Terminal_Strip_Designation": "Terminal_Strip_Designation",
    "Terminal_Type":            "Terminal_Type",
    "Rated_Cross_Section":      "Rated_Cross_Section",
    "Rated_Voltage":            "Rated_Voltage",
    "Rated_Current":            "Rated_Current",
    "Manufacturer":             "Manufacturer",
    "Type_Designation":         "Type_Designation",
    # Contactor attributes
    "Coil_Voltage":             "Coil_Voltage",
    "Rated_Operational_Current": "Rated_Operational_Current",
    "Main_Contact_Count":       "Main_Contact_Count",
    "Aux_Contact_NO_Count":     "Aux_Contact_NO_Count",
    "Aux_Contact_NC_Count":     "Aux_Contact_NC_Count",
    # Fuse/Circuit_Breaker attributes
    "Trip_Characteristic":      "Trip_Characteristic",
    "Protection_Form":          "Protection_Form",
    "Pole_Count":               "Pole_Count",
    "Rated_Breaking_Capacity":  "Rated_Breaking_Capacity",
    "Trip_Current_Residual":    "Trip_Current_Residual",
    # Power_Supply attributes
    "Input_Voltage":            "Input_Voltage",
    "Output_Voltage":           "Output_Voltage",
    "Output_Power":             "Output_Power",
    "Output_Current":           "Output_Current",
    # Switch attributes
    "Switch_Type":              "Switch_Type",
    # Socket attributes
    "Socket_Type":              "Socket_Type",
    "IP_Protection":            "IP_Protection",
    "Mounting_Type":            "Mounting_Type",
    # Motor/Actuator attributes
    "Target_Load":              "Target_Load",
    # Circuit_Diagram specific
    "Current_Path_Number":      "Current_Path_Number",
    "Contact_Designation":      "Contact_Designation",
    "Grid_Row":                 "Grid_Row",
    "Lamp_Color":               "Lamp_Color",
    "PCE_Channel_Suffix":       "PCE_Channel_Suffix",
}

# Scope=Connection attributes (go into Connection_Data EAV rows)
CONNECTION_ATTRIBUTE_MAP: dict[str, str] = {
    "Wire_Color":               "Wire_Color",
    "Wire_Number":              "Wire_Number",
    "Cable_Number":             "Cable_Number",
    "Cable_Type":               "Cable_Type",
    "Cross_Section":            "Cross_Section",
    "Cross-Section":            "Cross_Section",
    "Polarity":                 "Polarity",
    "Length":                   "Length",
    "Connection_Type":          "Connection_Type",
    "Shielding":                "Shielding",
    "Remark":                   "Remark",
    "Current_Path_Number":      "Current_Path_Number",
    "Voltage_Level":            "Voltage_Level",
    "Signal_Standard":          "Signal_Standard",
}

# Scope=RepresentedItem attributes
REPRESENTEDITEM_ATTRIBUTE_MAP: dict[str, str] = {
    "PCE_Category":             "PCE_Category",
    "PCE_Processing_Function":  "PCE_Processing_Function",
    "Instrument_Point_Reference": "Instrument_Point_Reference",
    "Topic_Identification_Status": "Topic_Identification_Status",
}


# ══════════════════════════════════════════════════════════════════════════════
# 7. Classification System mapping
# ══════════════════════════════════════════════════════════════════════════════

CLASSIFICATION_SYSTEM_MAP: dict[str, str] = {
    # Legacy classification → AIO Classification_System
    "IEC_81346":            "IEC 81346-2",
    "IEC_60617":            "IEC 60617",
    "IEC_62424":            "IEC 62424",
    "DIN_19227":            "DIN 19227-2",
    "ECLASS":               "ECLASS",
    "IEC_61987":            "IEC 61987",
}


# ══════════════════════════════════════════════════════════════════════════════
# 8. Layer type mapping
# ══════════════════════════════════════════════════════════════════════════════

LAYER_TYPE_MAP: dict[str, str] = {
    "steuerung":        "Signal_Line",
    "signalanpassung":  "Signal_Line",
    "rangierverteiler": "Distribution",
    "klemmleiste":      "Distribution",
    "supply":           "Supply",
    "distribution":     "Distribution",
    "signal_line":      "Signal_Line",
    "230vac":           "Supply",
    "24vdc":            "Signal_Line",
    "400v":             "Supply",
}


# ══════════════════════════════════════════════════════════════════════════════
# 9. PCE function letter decoding (hardcoded reference, fallback to LLM)
# ══════════════════════════════════════════════════════════════════════════════

PCE_CATEGORY: dict[str, str] = {
    "F": "Durchfluss / Flow",
    "T": "Temperatur / Temperature",
    "P": "Druck / Pressure",
    "L": "Füllstand / Level",
    "D": "Dichte / Density",
    "Q": "Qualität / Quality",
    "G": "Abstand / Distance",
    "S": "Geschwindigkeit / Speed",
    "W": "Gewicht / Weight",
    "H": "Handeingabe / Manual Input",
}

PCE_PROCESSING: dict[str, str] = {
    "I": "Anzeige / Indication",
    "C": "Regelung / Control",
    "R": "Registrierung / Recording",
    "S": "Schaltung / Switching",
    "Q": "Mengenzählung / Quantity Counting",
    "D": "Dichtemessung / Density",
    "A": "Alarm / Alarm",
    "Y": "Umrechnung / Conversion",
    "T": "Messumformung / Transducing",
}


# ══════════════════════════════════════════════════════════════════════════════
# 10. Helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_aio_element_info(key: str) -> dict[str, str] | None:
    """Look up AIO Element_Type, IEC class, and CAEX type for a legacy type key."""
    key_lower = key.lower().strip()
    # Try exact match first, then check common variants
    if key_lower in ELEMENT_TYPE_MAP:
        return ELEMENT_TYPE_MAP[key_lower]
    # Try removing leading hyphen (designation prefix)
    if key_lower.startswith("-"):
        key_lower = key_lower[1:]
        if key_lower in ELEMENT_TYPE_MAP:
            return ELEMENT_TYPE_MAP[key_lower]
    return None


def get_wire_color_code(german_word: str) -> str:
    """Convert a German wire color word to IEC 60757 code. Returns 'Unspecifiable' if unknown."""
    return WIRE_COLOR_MAP.get(german_word.lower().strip(), "Unspecifiable")


def get_polarity_code(raw: str) -> str:
    """Normalize polarity to AIO enum value."""
    return POLARITY_MAP.get(raw.strip(), raw.strip().upper())


def get_document_attribute_name(legacy_field: str) -> str:
    """Map legacy document-level field name to AIO Attribute_Name."""
    return DOCUMENT_ATTRIBUTE_MAP.get(legacy_field, legacy_field)


def get_element_attribute_name(legacy_field: str) -> str:
    """Map legacy element-level field name to AIO Attribute_Name."""
    return ELEMENT_ATTRIBUTE_MAP.get(legacy_field, legacy_field)


def get_connection_attribute_name(legacy_field: str) -> str:
    """Map legacy connection-level field name to AIO Attribute_Name."""
    return CONNECTION_ATTRIBUTE_MAP.get(legacy_field, legacy_field)


# ══════════════════════════════════════════════════════════════════════════
# RAG-based attribute name normalization (semantic fallback)
# ══════════════════════════════════════════════════════════════════════════
import hashlib
import json
import re

_RAG_NORMALIZER: "AttributeRAGNormalizer | None" = None


def _get_rag_normalizer() -> "AttributeRAGNormalizer | None":
    """Lazy-init the RAG normalizer. Returns None if embedding unavailable."""
    global _RAG_NORMALIZER
    if _RAG_NORMALIZER is not None:
        return _RAG_NORMALIZER if _RAG_NORMALIZER._available else None

    try:
        _RAG_NORMALIZER = AttributeRAGNormalizer()
        if not _RAG_NORMALIZER._available:
            _RAG_NORMALIZER = None
    except Exception:
        _RAG_NORMALIZER = None
    return _RAG_NORMALIZER


def normalize_attribute_name_rag(source_field: str, scope: str = "Element") -> str:
    """Try to normalize a source field name using RAG semantic search.

    Only called when deterministic maps fail. Returns the best matching
    standard attribute name, or the original field name if no good match.
    Results cached on disk.
    """
    normalizer = _get_rag_normalizer()
    if normalizer is None:
        return source_field
    return normalizer.normalize(source_field, scope)


class AttributeRAGNormalizer:
    """Semantic attribute name normalization using embedding-based retrieval.

    Builds a vector index from all known standard attribute names and their
    legacy→standard mappings.  When a source field name doesn't match any
    deterministic map, uses embedding similarity to find the closest standard
    name.  Both the vector index and lookup results are cached on disk.
    """

    def __init__(self) -> None:
        self._available = False
        self._vectors = None
        self._corpus: list[str] = []
        self._corpus_standards: list[str] = []  # parallel to _corpus: the standard name
        self._lookup_cache: dict[str, str] = {}
        self._client = None

        # Paths
        from pathlib import Path
        _repo = Path(__file__).resolve().parents[2]
        self._cache_dir = _repo / ".iev4pi" / "cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._lookup_cache_path = self._cache_dir / "attr_normalizer_lookup.json"
        self._embedding_cache_path = self._cache_dir / "attr_normalizer_vectors.npz"

        # Load lookup cache
        self._load_lookup_cache()

        # Init embedding client
        try:
            from iev4pi_transformation_tool.core.llm_client import OpenAICompatibleLLMClient
            from iev4pi_transformation_tool.services.workbench import Workbench
            wb = Workbench(_repo)
            self._client = OpenAICompatibleLLMClient(wb.settings.llm)
            if not self._client.embedding_available:
                return
        except Exception:
            return

        # Build corpus from all known mappings + standard names
        self._build_corpus()

        # Load or build vector index
        if not self._load_vectors():
            self._build_vectors()

        self._available = True

    def _build_corpus(self) -> None:
        """Build text corpus from all known attribute names."""
        corpus = []
        standards = []

        # All standard attribute names (comprehensive list from Attribute_Lookup and spec §9.2)
        standard_names = [
            # Document scope
            "Project_Name", "Plant", "Cabinet", "Position", "Revision", "Creation_Date",
            "Document_Subtype", "Source_Format", "Sheet_Number", "Primary_RKZ",
            "Document_Name", "Document_Designation", "Description",
            "Author", "Edited_By", "Reviewed_By", "Normative_Reference",
            "Project_Number", "Customer", "Order_Number", "Software",
            "PLC_Identifier", "Version", "Last_Update", "Location",
            "Origin", "Replaces", "Replaced_By", "Customer_Drawing_Number",
            "Planner_Drawing_Number", "Total_Sheets", "Sheet_Name",
            "Cross_Project_Reference", "Designation_Convention", "Bearbeiter",
            # Element scope
            "Function", "Terminal_Number", "Terminal_Strip_Designation",
            "Manufacturer", "Type_Designation", "Device_ID", "Wire_Label",
            "Canonical_Tag", "PLT_Position", "Component_Role",
            "IEC_60617_Reference", "Cross_Reference_Raw",
            "Rated_Cross_Section", "Rated_Voltage", "Rated_Current",
            "Coil_Voltage", "Rated_Operational_Current",
            "Main_Contact_Count", "Aux_Contact_NO_Count", "Aux_Contact_NC_Count",
            "Trip_Characteristic", "Protection_Form", "Pole_Count",
            "Rated_Breaking_Capacity", "Trip_Current_Residual", "Residual_Current_Type",
            "Input_Voltage", "Output_Voltage", "Output_Power", "Output_Current",
            "Switch_Type", "Socket_Type", "IP_Protection", "Mounting_Type",
            "Target_Load", "Utilization_Category",
            "Current_Path_Number", "Contact_Designation", "Lamp_Color",
            "Lamp_Function", "Circuit_Topology",
            "Terminal_Type", "Cable_Type", "PCE_Channel_Suffix",
            # Connection scope
            "Wire_Color", "Wire_Color_Secondary", "Polarity",
            "Cross_Section", "Wire_Number", "Cable_Number",
            "Connection_Type", "Shielding", "Length",
            "Connection_Point_From", "Connection_Point_To",
            "Remark", "Voltage_Level", "Signal_Standard",
            "Total_Wire_Count",
            # RepresentedItem scope
            "Loop_Description", "Terminal_Count", "Terminal_System",
            "Position_in_Cabinet", "Cabinet_Manufacturer", "Cabinet_Type",
            "Dimensions_HxWxD", "Terminal_Strip_Count",
            "PCE_Category", "PCE_Processing_Function",
        ]
        for sn in standard_names:
            corpus.append(f"Standard attribute name: {sn}")
            standards.append(sn)

        # All known legacy→standard mappings
        all_maps = {**DOCUMENT_ATTRIBUTE_MAP, **ELEMENT_ATTRIBUTE_MAP,
                     **CONNECTION_ATTRIBUTE_MAP, **REPRESENTEDITEM_ATTRIBUTE_MAP}
        for legacy, standard in sorted(all_maps.items()):
            if legacy != standard:
                corpus.append(f"Source field '{legacy}' maps to standard attribute '{standard}'")
                standards.append(standard)

        # German→English hints for common patterns
        de_hints = [
            ("beschreibung", "Description"),
            ("bezeichnung", "Designation"),
            ("funktion", "Function"),
            ("hersteller", "Manufacturer"),
            ("gerät", "Device_ID"),
            ("gerat", "Device_ID"),
            ("schrank", "Cabinet"),
            ("klemmleiste", "Terminal_Strip_Designation"),
            ("klemme", "Terminal_Number"),
            ("anschluss", "Connection_Point_From"),
            ("bestelldaten", "Order_Number"),
            ("stromlaufplan", "Cross_Reference_Raw"),
            ("verschaltung", "Connection_Type"),
            ("querschnitt", "Cross_Section"),
            ("spannung", "Rated_Voltage"),
            ("strom", "Rated_Current"),
            ("leistung", "Output_Power"),
            ("leitung", "Wire_Number"),
            ("farbe", "Wire_Color"),
            ("polarität", "Polarity"),
            ("e_schrank", "Cabinet"),
            ("plt_stelle", "PLT_Position"),
            ("m_s_r_schrank", "Cabinet"),
            # Precise mappings for known field names
            ("beschreibung field", "Description"),
            ("Beschreibung", "Description"),
            ("funktion_und_bestelldaten", "Description"),
            ("raw_context field", "Cross_Reference_Raw"),
            ("trace_path field", "Current_Path_Number"),
            ("page_number field", "Sheet_Number"),
            ("page_number", "Sheet_Number"),
        ]
        for de, en in de_hints:
            corpus.append(f"German field '{de}' typically maps to '{en}'")
            standards.append(en)

        self._corpus = corpus
        self._corpus_standards = standards

    def _load_vectors(self) -> bool:
        try:
            import numpy as np
            if self._embedding_cache_path.exists():
                self._vectors = np.load(self._embedding_cache_path)['v']
                return True
        except Exception:
            pass
        return False

    def _build_vectors(self) -> None:
        import numpy as np
        vecs = []
        batch = 20
        for i in range(0, len(self._corpus), batch):
            chunk = self._corpus[i:i + batch]
            try:
                vecs.extend(self._client.embed_texts(chunk))
            except Exception:
                vecs.extend([[0.0] * 768 for _ in chunk])
        self._vectors = np.array(vecs, dtype=np.float32)
        try:
            import numpy as np
            np.savez(self._embedding_cache_path, v=self._vectors)
        except Exception:
            pass

    def _load_lookup_cache(self) -> None:
        try:
            if self._lookup_cache_path.exists():
                self._lookup_cache = json.loads(self._lookup_cache_path.read_text())
        except Exception:
            self._lookup_cache = {}

    def _save_lookup_cache(self) -> None:
        try:
            self._lookup_cache_path.write_text(json.dumps(self._lookup_cache, ensure_ascii=False))
        except Exception:
            pass

    def normalize(self, source_field: str, scope: str = "Element") -> str:
        """Find the closest standard attribute name via semantic search.

        Returns the original field name if no good match found.
        Results cached in lookup cache on disk.
        """
        if source_field in self._lookup_cache:
            return self._lookup_cache[source_field]

        # If it's already a known standard name, don't remap
        if source_field in self._corpus_standards:
            self._lookup_cache[source_field] = source_field
            self._save_lookup_cache()
            return source_field

        # Short lowercase-only terms have no semantic content — skip RAG
        if len(source_field) <= 6 and source_field.islower() and '_' not in source_field:
            self._lookup_cache[source_field] = source_field
            self._save_lookup_cache()
            return source_field

        if self._vectors is None:
            self._lookup_cache[source_field] = source_field
            self._save_lookup_cache()
            return source_field

        try:
            import numpy as np
            q = np.array(self._client.embed_texts([source_field])[0], dtype=np.float32)
        except Exception:
            self._lookup_cache[source_field] = source_field
            self._save_lookup_cache()
            return source_field

        norms = np.linalg.norm(self._vectors, axis=1)
        qn = np.linalg.norm(q)
        if qn == 0:
            self._lookup_cache[source_field] = source_field
            self._save_lookup_cache()
            return source_field

        sims = np.dot(self._vectors, q) / (norms * qn + 1e-10)
        top_idx = int(np.argmax(sims))
        top_score = float(sims[top_idx])

        # Require higher confidence for short/non-semantic terms
        min_score = 0.55 if len(source_field) <= 6 else 0.45
        if top_score < min_score:
            self._lookup_cache[source_field] = source_field
            self._save_lookup_cache()
            return source_field

        best_standard = self._corpus_standards[top_idx]

        # Reject mappings that make no semantic sense (embedding noise)
        # Short generic terms (< 5 chars) with low scores are unreliable
        if len(source_field) <= 5 and top_score < 0.65:
            self._lookup_cache[source_field] = source_field
            self._save_lookup_cache()
            return source_field

        self._lookup_cache[source_field] = best_standard
        self._save_lookup_cache()
        return best_standard


# Wrapped lookup functions with RAG fallback
def get_document_attribute_name_rag(legacy_field: str) -> str:
    """Map document-level field name, with RAG semantic fallback."""
    result = DOCUMENT_ATTRIBUTE_MAP.get(legacy_field, legacy_field)
    if result == legacy_field:
        result = normalize_attribute_name_rag(legacy_field, "Document")
    return result


def get_element_attribute_name_rag(legacy_field: str) -> str:
    """Map element-level field name, with RAG semantic fallback."""
    result = ELEMENT_ATTRIBUTE_MAP.get(legacy_field, legacy_field)
    if result == legacy_field:
        result = normalize_attribute_name_rag(legacy_field, "Element")
    return result


def get_connection_attribute_name_rag(legacy_field: str) -> str:
    """Map connection-level field name, with RAG semantic fallback."""
    result = CONNECTION_ATTRIBUTE_MAP.get(legacy_field, legacy_field)
    if result == legacy_field:
        result = normalize_attribute_name_rag(legacy_field, "Connection")
    return result
