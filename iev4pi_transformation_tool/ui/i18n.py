from __future__ import annotations

from iev4pi_transformation_tool.models import DocumentFamily


LANGUAGE_OPTIONS = [
    ("en", "English"),
    ("de", "Deutsch"),
    ("zh", "中文"),
]


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "app.title": "IEV4PI RAG Workbench",
        "nav.quick_start": "Quick Start",
        "nav.project": "Project",
        "nav.schema_discovery": "Schema Discovery",
        "nav.extraction_runs": "Extraction Runs",
        "nav.review": "Review",
        "nav.exports": "Exports",
        "nav.log": "Log",
        "nav.model_settings": "Model Settings",
        "page.quick_start.title": "IEV4PI UC1 Transformation Tool - Quick Start",
        "page.quick_start.subtitle": "Start here for the UC1 transformation flow, the retained pages, and the safest deterministic / non-deterministic split.",
        "page.project.title": "Project",
        "page.project.subtitle": "Scan the configured input folders, classify documents, and inspect the current project inventory.",
        "page.schema.title": "Schema Discovery",
        "page.schema.subtitle": "Generate document-family templates, review aliases, and adjust extraction hints before batch extraction.",
        "page.extraction.title": "Extraction Runs",
        "page.extraction.subtitle": "Execute the end-to-end extraction pipeline. Spreadsheet rows extract deterministically and PDF families stay conservative when evidence is weak.",
        "page.review.title": "Review",
        "page.review.subtitle": "Inspect extracted values together with status, confidence, and the first supporting evidence snippet.",
        "page.exports.title": "Exports",
        "page.exports.subtitle": "Export schema workbooks and per-family extraction outputs as Excel plus CSV mirrors.",
        "page.log.title": "Log",
        "page.log.subtitle": "Inspect background task flow, OCR backend/device usage, clicks, searches, and timing details for debugging.",
        "log.filter.label": "Filter",
        "log.filter.ocr": "OCR only",
        "log.filter.rag": "RAG only",
        "log.filter.llm": "LLM only",
        "log.filter.embedding": "Embedding only",
        "log.filter.vlm": "VLM only",
        "log.filter.errors": "Errors only",
        "log.filter.current_task": "Current task",
        "page.settings.title": "Model Settings",
        "page.settings.subtitle": "Configure the interface language, optional OpenAI-compatible endpoint, retrieval defaults, and OCR behavior.",
        "quickstart.markdown": (
            "## What this app does\n"
            "- Scans `Documents` and keeps source evidence\n"
            "- Uses source-specific extraction to normalize UC1 inputs\n"
            "- Exports four standardized Excel workbooks for `Instrument List`, `Wiring`, `Datasheet`, and `Piping`\n"
            "- Generates five AAS groups and five Protégé-readable ontology files\n\n"
            "## Recommended workflow\n"
            "1. Open **Project** and click **Scan Workspace**\n"
            "2. Open **UC1 Pipeline** and click **Generate Schemas**\n"
            "3. Run **Run Extraction** to build evidence-backed records\n"
            "4. Click **Export 4 XLSX** for the source-specific standardized workbooks\n"
            "5. Click **Generate 5 AAS** for `T1` plus `Tx`\n"
            "6. Click **Export 5 OWL** for `T6-T10`\n\n"
            "## Main pages\n"
            "- **Quick Start**: short UC1 orientation and workflow summary\n"
            "- **Project**: scan and classify files\n"
            "- **UC1 Pipeline**: run extraction and the full transformation chain\n"
            "- **Log**: inspect background steps for debugging and experiments\n"
            "- **Model Settings**: switch interface language and configure OCR / optional LLM settings\n\n"
            "## Interface language\n"
            "Go to **Model Settings**, change **Interface language**, and click **Save Settings**. The interface updates immediately."
        ),
        "quickstart.hero_title": "UC1 transformation with explicit semantic contracts",
        "quickstart.hero_body": "Use non-deterministic extraction only before standardized Excel, then keep Tx and ontology export deterministic and auditable.",
        "quickstart.badge.documents": "Mixed PDF / XLS / XLSX",
        "quickstart.badge.review": "Evidence + confidence",
        "quickstart.badge.export": "4 XLSX + 5 AAS + 5 OWL",
        "quickstart.card.scan.title": "1. Scan",
        "quickstart.card.scan.body": "Discover UC1 source files from DEXPI, PDF, XLS/XLSX, and IFC inputs.",
        "quickstart.card.schema.title": "2. Normalize",
        "quickstart.card.schema.body": "Generate or refresh extraction schemas before the deterministic UC1 transformation stages.",
        "quickstart.card.run.title": "3. Transform",
        "quickstart.card.run.body": "Run extraction, export 4 standardized Excel workbooks, then generate 5 AAS groups and 5 ontology files.",
        "common.family": "Family",
        "common.all": "All",
        "common.none": "None",
        "common.language": "Interface language",
        "common.base_url": "Base URL",
        "common.chat_model": "Chat model",
        "common.embedding_model": "Embedding model",
        "common.api_key": "API key",
        "common.timeout": "Timeout",
        "common.max_retries": "Max retries",
        "common.ocr_zoom": "OCR zoom",
        "common.top_k": "Top K",
        "common.save_settings": "Save Settings",
        "common.clear_log": "Clear Log",
        "common.copy_log": "Copy Log",
        "common.enable_http_llm": "Enable HTTP LLM backend",
        "common.enable_ocr": "Enable OCR fallback for low-text PDFs",
        "common.clear_db_before_extraction": "Clear database before each extraction run",
        "common.scan_workspace": "Scan Workspace",
        "common.generate_schemas": "Generate Schemas",
        "common.save_current_schema": "Save All Schemas",
        "common.run_extraction": "Run Extraction",
        "common.start_extraction": "Start Extraction",
        "common.save_extraction_results": "Save Extraction Results",
        "common.refresh_review": "Refresh Review",
        "common.export_all": "Export All",
        "common.scan_failed": "Scan failed",
        "common.schema_failed": "Schema generation failed",
        "common.extraction_failed": "Extraction failed",
        "common.export_failed": "Export failed",
        "project.no_scan": "No scan has been run yet.",
        "project.summary": "{count} files scanned. Families: {families}. Sources: {sources}.",
        "project.header.relative_path": "Relative Path",
        "project.header.source_kind": "Source Kind",
        "project.header.output_families": "Output Families",
        "project.header.size": "Size (bytes)",
        "schema.notes_placeholder": "Review notes for the selected schema family.",
        "schema.header.name": "Name",
        "schema.header.aliases": "Aliases",
        "schema.header.type": "Type",
        "schema.header.repeatable": "Repeatable",
        "schema.header.hint": "Hint",
        "extraction.no_run": "No extraction run has been completed yet.",
        "extraction.latest": "Latest run #{run_id} completed with {record_count} records. {family_bits}",
        "extraction.completed": "Run #{run_id} completed with {record_count} records across {family_count} families.",
        "review.header.family": "Family",
        "review.header.record": "Record",
        "review.header.field": "Field",
        "review.header.value": "Value",
        "review.header.status": "Status",
        "review.header.confidence": "Confidence",
        "review.header.source": "Source",
        "review.header.location": "Location",
        "review.header.snippet": "Snippet",
        "exports.placeholder": "Exported files will appear here.",
        "status.filled": "Filled",
        "status.blank_no_evidence": "Blank (no evidence)",
        "status.needs_review": "Needs review",
        "source_kind.stellen_overview": "Stellen overview",
        "source_kind.stellen_tu": "TU datasheet",
        "source_kind.klemmenplan": "Klemmenplan",
        "source_kind.verschaltungsliste": "Verschaltungsliste",
        "source_kind.cabinet_reference": "Cabinet reference",
        "source_kind.stromlaufplan": "Stromlaufplan",
        "family.stellen_overview_record": "Stellen overview record",
        "family.stellen_tu_datasheet": "TU datasheet",
        "family.klemmenplan_row": "Klemmenplan row",
        "family.verschaltungsliste_row": "Verschaltungsliste row",
        "family.cabinet_reference_row": "Cabinet reference row",
        "family.stromlauf_component_group": "Stromlauf component group",
        "family.stromlauf_component": "Stromlauf component",
        "family.stromlauf_connection": "Stromlauf connection",
    },
    "de": {
        "app.title": "IEV4PI RAG Arbeitsoberfläche",
        "nav.quick_start": "Schnellstart",
        "nav.project": "Projekt",
        "nav.schema_discovery": "Schema-Erkennung",
        "nav.extraction_runs": "Extraktionsläufe",
        "nav.review": "Prüfung",
        "nav.exports": "Exporte",
        "nav.log": "Log",
        "nav.model_settings": "Einstellungen",
        "page.quick_start.title": "IEV4PI UC1-Transformationstool - Schnellstart",
        "page.quick_start.subtitle": "Hier finden Sie den UC1-Transformationsablauf, die beibehaltenen Seiten und die Trennung zwischen nichtdeterministischen und deterministischen Schritten.",
        "page.project.title": "Projekt",
        "page.project.subtitle": "Eingabeordner scannen, Dokumente klassifizieren und den aktuellen Projektbestand prüfen.",
        "page.schema.title": "Schema-Erkennung",
        "page.schema.subtitle": "Vorlagen pro Dokumentfamilie erzeugen, Aliase prüfen und Extraktionshinweise anpassen.",
        "page.extraction.title": "Extraktionsläufe",
        "page.extraction.subtitle": "Die vollständige Pipeline ausführen. Tabellen werden deterministisch verarbeitet, PDFs bleiben bei schwacher Evidenz konservativ.",
        "page.review.title": "Prüfung",
        "page.review.subtitle": "Extrahierte Werte zusammen mit Status, Konfidenz und der ersten Evidenzstelle prüfen.",
        "page.exports.title": "Exporte",
        "page.exports.subtitle": "Schema-Arbeitsmappen und Ergebnisdateien pro Familie als Excel und CSV exportieren.",
        "page.log.title": "Log",
        "page.log.subtitle": "Hintergrundaufgaben, OCR-Backend/Geraet, Klicks, Suchvorgaenge und Laufzeiten fuer Debugging pruefen.",
        "log.filter.label": "Filter",
        "log.filter.ocr": "Nur OCR",
        "log.filter.rag": "Nur RAG",
        "log.filter.llm": "Nur LLM",
        "log.filter.embedding": "Nur Embedding",
        "log.filter.vlm": "Nur VLM",
        "log.filter.errors": "Nur Fehler",
        "log.filter.current_task": "Aktuelle Aufgabe",
        "page.settings.title": "Einstellungen",
        "page.settings.subtitle": "Oberflächensprache, optionalen OpenAI-kompatiblen Endpunkt, Retrieval-Standardwerte und OCR-Verhalten konfigurieren.",
        "quickstart.markdown": (
            "## Was die Anwendung macht\n"
            "- scannt `Documents` und behaelt Quell-Evidenzen\n"
            "- normalisiert UC1-Eingaenge mit quellenspezifischer Extraktion\n"
            "- exportiert vier standardisierte Excel-Arbeitsmappen fuer `Instrument List`, `Wiring`, `Datasheet` und `Piping`\n"
            "- erzeugt fuenf AAS-Gruppen und fuenf Protégé-lesbare Ontologie-Dateien\n\n"
            "## Empfohlener Ablauf\n"
            "1. **Project** oeffnen und **Scan Workspace** klicken\n"
            "2. **UC1 Pipeline** oeffnen und **Generate Schemas** klicken\n"
            "3. **Run Extraction** ausfuehren, um evidenzgestuetzte Datensaetze aufzubauen\n"
            "4. **4 XLSX exportieren** fuer die source-spezifischen standardisierten Arbeitsmappen klicken\n"
            "5. **5 AAS erzeugen** fuer `T1` plus `Tx` klicken\n"
            "6. **5 OWL exportieren** fuer `T6-T10` klicken\n\n"
            "## Hauptseiten\n"
            "- **Quick Start**: kurze UC1-Orientierung und Workflow-Zusammenfassung\n"
            "- **Project**: Dateien scannen und klassifizieren\n"
            "- **UC1 Pipeline**: Extraktion und gesamte Transformationskette ausfuehren\n"
            "- **Log**: Hintergrundschritte fuer Debugging und Experimente ansehen\n"
            "- **Model Settings**: Sprache wechseln und OCR / optionale LLM-Einstellungen konfigurieren\n\n"
            "## Sprache der Oberfläche\n"
            "Unter **Einstellungen** die **Oberflächensprache** wählen und **Einstellungen speichern** klicken. Die Oberfläche wird sofort aktualisiert."
        ),
        "quickstart.hero_title": "UC1-Transformation mit expliziten semantischen Vertraegen",
        "quickstart.hero_body": "Nichtdeterministische Extraktion nur vor dem standardisierten Excel einsetzen, danach Tx und Ontologie-Export deterministisch und nachvollziehbar halten.",
        "quickstart.badge.documents": "Gemischte PDF / XLS / XLSX",
        "quickstart.badge.review": "Evidenz + Konfidenz",
        "quickstart.badge.export": "4 XLSX + 5 AAS + 5 OWL",
        "quickstart.card.scan.title": "1. Scannen",
        "quickstart.card.scan.body": "UC1-Quelldateien aus DEXPI-, PDF-, XLS/XLSX- und IFC-Eingaengen entdecken.",
        "quickstart.card.schema.title": "2. Normalisieren",
        "quickstart.card.schema.body": "Extraktionsschemas vor den deterministischen UC1-Transformationsstufen erzeugen oder aktualisieren.",
        "quickstart.card.run.title": "3. Transformieren",
        "quickstart.card.run.body": "Extraktion ausfuehren, 4 standardisierte Excel-Arbeitsmappen exportieren und danach 5 AAS-Gruppen sowie 5 Ontologie-Dateien erzeugen.",
        "common.family": "Familie",
        "common.all": "Alle",
        "common.none": "Keine",
        "common.language": "Oberflächensprache",
        "common.base_url": "Basis-URL",
        "common.chat_model": "Chat-Modell",
        "common.embedding_model": "Embedding-Modell",
        "common.api_key": "API-Schlüssel",
        "common.timeout": "Zeitlimit",
        "common.max_retries": "Max. Wiederholungen",
        "common.ocr_zoom": "OCR-Zoom",
        "common.top_k": "Top K",
        "common.save_settings": "Einstellungen speichern",
        "common.clear_log": "Log leeren",
        "common.copy_log": "Log kopieren",
        "common.enable_http_llm": "HTTP-LLM-Backend aktivieren",
        "common.enable_ocr": "OCR-Fallback für textarme PDFs aktivieren",
        "common.clear_db_before_extraction": "Datenbank vor jedem Extraktionslauf leeren",
        "common.scan_workspace": "Arbeitsbereich scannen",
        "common.generate_schemas": "Schemas erzeugen",
        "common.save_current_schema": "Alle Schemas speichern",
        "common.run_extraction": "Extraktion starten",
        "common.start_extraction": "Extraktion starten",
        "common.save_extraction_results": "Ergebnisse speichern",
        "common.refresh_review": "Prüfung aktualisieren",
        "common.export_all": "Alles exportieren",
        "common.scan_failed": "Scan fehlgeschlagen",
        "common.schema_failed": "Schema-Erzeugung fehlgeschlagen",
        "common.extraction_failed": "Extraktion fehlgeschlagen",
        "common.export_failed": "Export fehlgeschlagen",
        "project.no_scan": "Es wurde noch kein Scan ausgeführt.",
        "project.summary": "{count} Dateien gescannt. Familien: {families}. Quellen: {sources}.",
        "project.header.relative_path": "Relativer Pfad",
        "project.header.source_kind": "Dokumenttyp",
        "project.header.output_families": "Zielfamilien",
        "project.header.size": "Größe (Bytes)",
        "schema.notes_placeholder": "Prüfnotizen für die ausgewählte Dokumentfamilie.",
        "schema.header.name": "Name",
        "schema.header.aliases": "Aliase",
        "schema.header.type": "Typ",
        "schema.header.repeatable": "Wiederholbar",
        "schema.header.hint": "Hinweis",
        "extraction.no_run": "Es wurde noch kein Extraktionslauf abgeschlossen.",
        "extraction.latest": "Letzter Lauf #{run_id} mit {record_count} Datensätzen abgeschlossen. {family_bits}",
        "extraction.completed": "Lauf #{run_id} mit {record_count} Datensätzen über {family_count} Familien abgeschlossen.",
        "review.header.family": "Familie",
        "review.header.record": "Datensatz",
        "review.header.field": "Feld",
        "review.header.value": "Wert",
        "review.header.status": "Status",
        "review.header.confidence": "Konfidenz",
        "review.header.source": "Quelle",
        "review.header.location": "Position",
        "review.header.snippet": "Ausschnitt",
        "exports.placeholder": "Exportierte Dateien werden hier angezeigt.",
        "status.filled": "Gefüllt",
        "status.blank_no_evidence": "Leer (keine Evidenz)",
        "status.needs_review": "Prüfen",
        "source_kind.stellen_overview": "Stellenübersicht",
        "source_kind.stellen_tu": "TU-Datenblatt",
        "source_kind.klemmenplan": "Klemmenplan",
        "source_kind.verschaltungsliste": "Verschaltungsliste",
        "source_kind.cabinet_reference": "Schrankreferenz",
        "source_kind.stromlaufplan": "Stromlaufplan",
        "family.stellen_overview_record": "Stellenübersicht-Datensatz",
        "family.stellen_tu_datasheet": "TU-Datenblatt",
        "family.klemmenplan_row": "Klemmenplan-Zeile",
        "family.verschaltungsliste_row": "Verschaltungsliste-Zeile",
        "family.cabinet_reference_row": "Schrankreferenz-Zeile",
        "family.stromlauf_component_group": "Stromlauf-Komponentengruppe",
        "family.stromlauf_component": "Stromlauf-Komponente",
        "family.stromlauf_connection": "Stromlauf-Verbindung",
    },
    "zh": {
        "app.title": "IEV4PI RAG工作台",
        "nav.quick_start": "快速开始",
        "nav.project": "项目",
        "nav.schema_discovery": "模板发现",
        "nav.extraction_runs": "抽取运行",
        "nav.review": "复核",
        "nav.exports": "导出",
        "nav.log": "日志",
        "nav.model_settings": "设置",
        "page.quick_start.title": "IEV4PI UC1 转换工具 - 快速开始",
        "page.quick_start.subtitle": "这里说明 UC1 转换流程、保留的页面，以及非确定性步骤与确定性步骤之间的边界。",
        "page.project.title": "项目",
        "page.project.subtitle": "扫描输入目录、分类文档，并查看当前项目中的文档清单。",
        "page.schema.title": "模板发现",
        "page.schema.subtitle": "为文档族生成模板，检查别名，并在批量抽取前调整抽取提示。",
        "page.extraction.title": "抽取运行",
        "page.extraction.subtitle": "执行完整抽取流程。表格类文档以确定性方式处理，PDF 在证据不足时保持保守。",
        "page.review.title": "复核",
        "page.review.subtitle": "查看抽取值、状态、置信度以及第一条证据片段。",
        "page.exports.title": "导出",
        "page.exports.subtitle": "将模板工作簿和各文档族结果导出为 Excel 与 CSV。",
        "page.log.title": "日志",
        "page.log.subtitle": "查看后台任务流程、OCR 后端与设备、点击、搜索以及耗时等调试信息。",
        "log.filter.label": "筛选",
        "log.filter.ocr": "仅 OCR",
        "log.filter.rag": "仅 RAG",
        "log.filter.llm": "仅 LLM",
        "log.filter.embedding": "仅 Embedding",
        "log.filter.vlm": "仅 VLM",
        "log.filter.errors": "仅错误",
        "log.filter.current_task": "当前任务",
        "page.settings.title": "设置",
        "page.settings.subtitle": "配置界面语言、可选的 OpenAI 兼容端点、检索默认值以及 OCR 行为。",
        "quickstart.markdown": (
            "## 这个程序做什么\n"
            "- 扫描 `Documents` 并保留源证据\n"
            "- 用 source-specific 抽取把 UC1 输入规范化\n"
            "- 导出 `Instrument List`、`Wiring`、`Datasheet`、`Piping` 四个标准化 Excel\n"
            "- 生成 5 组 AAS 和 5 个 Protégé 可读取的 ontology 文件\n\n"
            "## 推荐流程\n"
            "1. 打开 **Project**，点击 **Scan Workspace**\n"
            "2. 打开 **UC1 Pipeline**，点击 **Generate Schemas**\n"
            "3. 运行 **Run Extraction**，生成带证据的结构化记录\n"
            "4. 点击 **导出 4 个 XLSX**，生成 source-specific standardized workbook\n"
            "5. 点击 **生成 5 类 AAS**，完成 `T1` 和 `Tx`\n"
            "6. 点击 **导出 5 个 OWL**，完成 `T6-T10`\n\n"
            "## 主要页面\n"
            "- **Quick Start**：UC1 流程概览\n"
            "- **Project**：扫描并分类文件\n"
            "- **UC1 Pipeline**：运行抽取和完整转换链\n"
            "- **Log**：查看后台步骤，方便调试和实验复现\n"
            "- **Model Settings**：切换界面语言并配置 OCR / 可选 LLM 设置\n\n"
            "## 切换界面语言\n"
            "进入 **设置**，修改 **界面语言**，然后点击 **保存设置**。界面会立即刷新。"
        ),
        "quickstart.hero_title": "带显式语义契约的 UC1 转换",
        "quickstart.hero_body": "把非确定性抽取限制在 standardized Excel 之前，之后的 Tx 和 ontology 导出保持确定性、可审计。",
        "quickstart.badge.documents": "混合 PDF / XLS / XLSX",
        "quickstart.badge.review": "证据 + 置信度",
        "quickstart.badge.export": "4 XLSX + 5 AAS + 5 OWL",
        "quickstart.card.scan.title": "1. 扫描",
        "quickstart.card.scan.body": "从 DEXPI、PDF、XLS/XLSX 和 IFC 输入中发现 UC1 源文件。",
        "quickstart.card.schema.title": "2. 规范化",
        "quickstart.card.schema.body": "在确定性的 UC1 转换步骤之前，先生成或刷新抽取模板。",
        "quickstart.card.run.title": "3. 转换",
        "quickstart.card.run.body": "运行抽取，导出 4 个标准化 Excel，然后生成 5 组 AAS 和 5 个 ontology 文件。",
        "common.family": "文档族",
        "common.all": "全部",
        "common.none": "无",
        "common.language": "界面语言",
        "common.base_url": "基础 URL",
        "common.chat_model": "聊天模型",
        "common.embedding_model": "Embedding 模型",
        "common.api_key": "API Key",
        "common.timeout": "超时",
        "common.max_retries": "最大重试次数",
        "common.ocr_zoom": "OCR 放大倍率",
        "common.top_k": "Top K",
        "common.save_settings": "保存设置",
        "common.clear_log": "清空日志",
        "common.copy_log": "复制日志",
        "common.enable_http_llm": "启用 HTTP LLM 后端",
        "common.enable_ocr": "对低文本 PDF 启用 OCR 回退",
        "common.clear_db_before_extraction": "每次抽取前先清空数据库",
        "common.scan_workspace": "扫描工作区",
        "common.generate_schemas": "生成模板",
        "common.save_current_schema": "保存全部模板",
        "common.run_extraction": "运行抽取",
        "common.start_extraction": "开始抽取",
        "common.save_extraction_results": "保存抽取结果",
        "common.refresh_review": "刷新复核",
        "common.export_all": "全部导出",
        "common.scan_failed": "扫描失败",
        "common.schema_failed": "模板生成失败",
        "common.extraction_failed": "抽取失败",
        "common.export_failed": "导出失败",
        "project.no_scan": "尚未执行扫描。",
        "project.summary": "已扫描 {count} 个文件。文档族：{families}。来源类型：{sources}。",
        "project.header.relative_path": "相对路径",
        "project.header.source_kind": "来源类型",
        "project.header.output_families": "输出文档族",
        "project.header.size": "大小（字节）",
        "schema.notes_placeholder": "当前文档族的复核备注。",
        "schema.header.name": "名称",
        "schema.header.aliases": "别名",
        "schema.header.type": "类型",
        "schema.header.repeatable": "可重复",
        "schema.header.hint": "提示",
        "extraction.no_run": "尚未完成任何抽取运行。",
        "extraction.latest": "最近一次运行 #{run_id} 已完成，共 {record_count} 条记录。{family_bits}",
        "extraction.completed": "运行 #{run_id} 已完成，共生成 {record_count} 条记录，覆盖 {family_count} 个文档族。",
        "review.header.family": "文档族",
        "review.header.record": "记录",
        "review.header.field": "字段",
        "review.header.value": "值",
        "review.header.status": "状态",
        "review.header.confidence": "置信度",
        "review.header.source": "来源",
        "review.header.location": "位置",
        "review.header.snippet": "证据片段",
        "exports.placeholder": "导出的文件路径会显示在这里。",
        "status.filled": "已填写",
        "status.blank_no_evidence": "留空（无证据）",
        "status.needs_review": "待复核",
        "source_kind.stellen_overview": "Stellen 总览",
        "source_kind.stellen_tu": "TU 数据表",
        "source_kind.klemmenplan": "Klemmenplan",
        "source_kind.verschaltungsliste": "Verschaltungsliste",
        "source_kind.cabinet_reference": "机柜参考表",
        "source_kind.stromlaufplan": "电路图",
        "family.stellen_overview_record": "Stellen 总览记录",
        "family.stellen_tu_datasheet": "TU 数据表",
        "family.klemmenplan_row": "Klemmenplan 行",
        "family.verschaltungsliste_row": "Verschaltungsliste 行",
        "family.cabinet_reference_row": "机柜参考行",
        "family.stromlauf_component_group": "电路图组件组",
        "family.stromlauf_component": "电路图元件",
        "family.stromlauf_connection": "电路图连接",
    },
}

LANGUAGE_OPTIONS = [
    ("en", "English"),
    ("de", "Deutsch"),
    ("zh", "中文"),
]

TRANSLATIONS["en"].update(
    {
        "common.scan_root": "Document folder",
        "common.schema_save_dir": "Schema save folder",
        "common.export_dir": "Export folder",
        "common.browse": "Browse...",
        "dialog.select_scan_root": "Select document folder",
        "dialog.select_schema_export_dir": "Select schema save folder",
        "dialog.select_export_dir": "Select export folder",
        "busy.scan.title": "Scanning documents",
        "busy.scan.body": "Searching the selected folder and classifying supported files.",
        "busy.schema.title": "Generating schemas",
        "busy.schema.body": "Reading documents and building schema candidates for each family.",
        "busy.extraction.title": "Running extraction",
        "busy.extraction.body": "Parsing documents, retrieving evidence, and saving reviewable results.",
        "busy.export.title": "Exporting files",
        "busy.export.body": "Writing schema and extraction workbooks to the selected folder.",
        "busy.extraction_fill.title": "Running extraction",
        "busy.extraction_fill.body": "Scanning documents, generating schemas, extracting records, and filling standardized templates.",
        "busy.save_results.title": "Saving extraction results",
        "busy.save_results.body": "Copying filled templates to the export directory organized by category.",
        "schema.saved_to": "Current schema saved to: {path}",
    }
)

TRANSLATIONS["en"].update(
    {
        "quickstart.guide_title": "Current workflow",
        "quickstart.markdown": (
            "## What this app does\n"
            "- Scans the default `Documents` folder and keeps source evidence for UC1\n"
            "- Normalizes source-specific inputs into four standardized Excel workbooks\n"
            "- Runs deterministic `T1`, `Tx`, and `T6-T10` exports after extraction\n"
            "- Produces 4 XLSX workbooks, 5 AAS groups, and 5 Protégé-readable ontology files\n\n"
            "## Recommended workflow\n"
            "1. Open **Project** and click **Scan Workspace**.\n"
            "2. Open **UC1 Pipeline** and click **Generate Schemas**.\n"
            "3. Click **Run Extraction** to build evidence-backed records.\n"
            "4. Click **Export 4 XLSX** to generate the source-specific standardized workbooks.\n"
            "5. Click **Generate 5 AAS** to export direct PID AAS plus the four Tx-driven AAS groups.\n"
            "6. Click **Export 5 OWL** to produce the five ontology files for Protégé.\n\n"
            "## Main pages\n"
            "- **Quick Start**: short UC1 orientation and workflow summary\n"
            "- **Project**: choose the document folder, scan files, and inspect the inventory\n"
            "- **UC1 Pipeline**: run scan, extraction, standardized export, AAS generation, and ontology export\n"
            "- **Log**: inspect background steps for debugging and experiments\n"
            "- **Model Settings**: switch interface language and configure OCR / optional LLM settings\n\n"
            "## Notes\n"
            "- Non-deterministic extraction is allowed before standardized Excel only.\n"
            "- The later UC1 transformations stay deterministic and traceable.\n"
            "- Review rows and source previews remain available on the same **UC1 Pipeline** page."
        ),
    }
)

TRANSLATIONS["de"].update(
    {
        "quickstart.guide_title": "Aktueller Arbeitsablauf",
        "quickstart.markdown": (
            "## Was die Anwendung macht\n"
            "- scannt den Standardordner `Documents` und behaelt Quell-Evidenzen fuer UC1\n"
            "- normalisiert source-spezifische Eingaben in vier standardisierte Excel-Arbeitsmappen\n"
            "- fuehrt nach der Extraktion die deterministischen Exporte `T1`, `Tx` und `T6-T10` aus\n"
            "- erzeugt 4 XLSX-Arbeitsmappen, 5 AAS-Gruppen und 5 Protégé-lesbare Ontologie-Dateien\n\n"
            "## Empfohlener Ablauf\n"
            "1. **Project** oeffnen und **Scan Workspace** klicken.\n"
            "2. **UC1 Pipeline** oeffnen und **Generate Schemas** klicken.\n"
            "3. **Run Extraction** ausfuehren, um evidenzgestuetzte Datensaetze aufzubauen.\n"
            "4. **4 XLSX exportieren** klicken, um die source-spezifischen standardisierten Arbeitsmappen zu erzeugen.\n"
            "5. **5 AAS erzeugen** klicken, um direktes PID-AAS plus vier Tx-getriebene AAS-Gruppen zu exportieren.\n"
            "6. **5 OWL exportieren** klicken, um die fuenf Ontologie-Dateien fuer Protégé zu erzeugen.\n\n"
            "## Hauptseiten\n"
            "- **Quick Start**: kurze UC1-Orientierung und Workflow-Zusammenfassung\n"
            "- **Project**: Dokumentordner waehlen, Dateien scannen und Bestand pruefen\n"
            "- **UC1 Pipeline**: Scan, Extraktion, standardisierten Export, AAS-Erzeugung und Ontologie-Export ausfuehren\n"
            "- **Log**: Hintergrundschritte fuer Debugging und Experimente ansehen\n"
            "- **Model Settings**: Oberflaechensprache sowie OCR- und optionale LLM-Einstellungen konfigurieren\n\n"
            "## Hinweise\n"
            "- Nichtdeterministische Extraktion ist nur vor dem standardisierten Excel erlaubt.\n"
            "- Die spaeteren UC1-Transformationen bleiben deterministisch und rueckverfolgbar.\n"
            "- Review-Zeilen und Source-Previews bleiben auf derselben **UC1 Pipeline**-Seite verfuegbar."
        ),
    }
)

TRANSLATIONS["zh"].update(
    {
        "quickstart.guide_title": "\u5f53\u524d\u4f7f\u7528\u6d41\u7a0b",
        "quickstart.markdown": (
            "## \u8fd9\u4e2a\u7a0b\u5e8f\u80fd\u505a\u4ec0\u4e48\n"
            "- \u626b\u63cf\u9ed8\u8ba4\u7684 `Documents` \u6587\u4ef6\u5939\uff0c\u4e3a UC1 \u4fdd\u7559\u6e90\u8bc1\u636e\n"
            "- \u628a source-specific \u8f93\u5165\u89c4\u8303\u5316\u4e3a 4 \u4e2a standardized Excel workbook\n"
            "- \u5728\u62bd\u53d6\u540e\u8fd0\u884c\u786e\u5b9a\u6027\u7684 `T1`、`Tx` \u548c `T6-T10`\n"
            "- \u751f\u6210 4 \u4e2a XLSX\u30015 \u7ec4 AAS \u548c 5 \u4e2a Protégé \u53ef\u8bfb\u7684 ontology \u6587\u4ef6\n\n"
            "## \u63a8\u8350\u4f7f\u7528\u6d41\u7a0b\n"
            "1. \u6253\u5f00 **Project** \uff0c\u70b9\u51fb **Scan Workspace**\u3002\n"
            "2. \u6253\u5f00 **UC1 Pipeline** \uff0c\u70b9\u51fb **Generate Schemas**\u3002\n"
            "3. \u8fd0\u884c **Run Extraction** \uff0c\u751f\u6210\u5e26\u8bc1\u636e\u7684\u7ed3\u6784\u5316\u8bb0\u5f55\u3002\n"
            "4. \u70b9\u51fb **\u5bfc\u51fa 4 \u4e2a XLSX** \uff0c\u751f\u6210 source-specific standardized workbook\u3002\n"
            "5. \u70b9\u51fb **\u751f\u6210 5 \u7c7b AAS** \uff0c\u5b8c\u6210 `T1` \u548c `Tx`\u3002\n"
            "6. \u70b9\u51fb **\u5bfc\u51fa 5 \u4e2a OWL** \uff0c\u5b8c\u6210 `T6-T10`\u3002\n\n"
            "## \u4e3b\u8981\u9875\u9762\n"
            "- **Quick Start**\uff1aUC1 \u6d41\u7a0b\u6982\u89c8\n"
            "- **Project**\uff1a\u9009\u62e9\u6587\u6863\u6587\u4ef6\u5939\uff0c\u626b\u63cf\u6587\u4ef6\uff0c\u67e5\u770b\u6e05\u5355\n"
            "- **UC1 Pipeline**\uff1a\u8fd0\u884c\u626b\u63cf\u3001\u62bd\u53d6\u3001standardized export\u3001AAS generation \u548c ontology export\n"
            "- **Log**\uff1a\u67e5\u770b\u540e\u53f0\u6b65\u9aa4\uff0c\u65b9\u4fbf\u8c03\u8bd5\u548c\u5b9e\u9a8c\u590d\u73b0\n"
            "- **Model Settings**\uff1a\u5207\u6362\u754c\u9762\u8bed\u8a00\uff0c\u914d\u7f6e OCR \u548c\u53ef\u9009 LLM\n\n"
            "## \u8bf4\u660e\n"
            "- \u975e\u786e\u5b9a\u6027\u62bd\u53d6\u53ea\u5141\u8bb8\u51fa\u73b0\u5728 standardized Excel \u4e4b\u524d\u3002\n"
            "- \u540e\u9762\u7684 UC1 \u8f6c\u6362\u4fdd\u6301\u786e\u5b9a\u6027\u548c\u53ef\u8ffd\u6eaf\u6027\u3002\n"
            "- review rows \u548c source preview \u4ecd\u7136\u4f1a\u5728 **UC1 Pipeline** \u9875\u9762\u4e2d\u53ef\u7528\u3002"
        ),
    }
)

TRANSLATIONS["en"].update(
    {
        "dialog.load_schema_root": "Select schema root folder",
    }
)

TRANSLATIONS["de"].update(
    {
        "dialog.load_schema_root": "Schema-Stammordner auswaehlen",
    }
)

TRANSLATIONS["zh"].update(
    {
        "dialog.load_schema_root": "\u9009\u62e9\u6a21\u677f\u6839\u76ee\u5f55",
    }
)

TRANSLATIONS["de"].update(
    {
        "common.scan_root": "Dokumentenordner",
        "common.schema_save_dir": "Schema-Speicherordner",
        "common.export_dir": "Exportordner",
        "common.browse": "Ordner wählen...",
        "dialog.select_scan_root": "Dokumentenordner auswählen",
        "dialog.select_schema_export_dir": "Ordner für Schema-Speicherung auswählen",
        "dialog.select_export_dir": "Exportordner auswählen",
        "busy.scan.title": "Dokumente werden gescannt",
        "busy.scan.body": "Der gewählte Ordner wird durchsucht und unterstützte Dateien werden klassifiziert.",
        "busy.schema.title": "Schemas werden erzeugt",
        "busy.schema.body": "Dokumente werden gelesen und Schema-Kandidaten pro Familie erstellt.",
        "busy.extraction.title": "Extraktion läuft",
        "busy.extraction.body": "Dokumente werden verarbeitet, Evidenz gesucht und prüfbare Ergebnisse gespeichert.",
        "busy.export.title": "Dateien werden exportiert",
        "busy.export.body": "Schema- und Ergebnis-Arbeitsmappen werden in den gewählten Ordner geschrieben.",
        "busy.extraction_fill.title": "Extraktion läuft",
        "busy.extraction_fill.body": "Dokumente werden gescannt, Schemas erzeugt, Datensätze extrahiert und standardisierte Vorlagen befüllt.",
        "busy.save_results.title": "Ergebnisse werden gespeichert",
        "busy.save_results.body": "Befüllte Vorlagen werden nach Kategorie sortiert ins Export-Verzeichnis kopiert.",
        "schema.saved_to": "Aktuelles Schema gespeichert unter: {path}",
    }
)

TRANSLATIONS["zh"].update(
    {
        "common.scan_root": "文档文件夹",
        "common.schema_save_dir": "模板保存文件夹",
        "common.export_dir": "导出文件夹",
        "common.browse": "浏览...",
        "dialog.select_scan_root": "选择文档文件夹",
        "dialog.select_schema_export_dir": "选择模板保存文件夹",
        "dialog.select_export_dir": "选择导出文件夹",
        "busy.scan.title": "正在扫描文档",
        "busy.scan.body": "正在搜索所选文件夹并分类支持的文件。",
        "busy.schema.title": "正在生成模板",
        "busy.schema.body": "正在读取文档并为每个文档族生成模板候选。",
        "busy.extraction.title": "正在运行抽取",
        "busy.extraction.body": "正在解析文档、检索证据并保存可复核结果。",
        "busy.export.title": "正在导出文件",
        "busy.export.body": "正在将模板和抽取结果工作簿写入所选文件夹。",
        "schema.saved_to": "当前模板已保存到：{path}",
    }
)


TRANSLATIONS["en"].update(
    {
        "busy.schema_export.title": "Saving all schemas",
        "busy.schema_export.body": "Writing all schema workbooks to the selected folder.",
        "schema.saved_summary": "All schemas saved to: {path} ({count} files)",
    }
)

TRANSLATIONS["de"].update(
    {
        "busy.schema_export.title": "Alle Schemas werden gespeichert",
        "busy.schema_export.body": "Alle Schema-Arbeitsmappen werden in den gewaehlten Ordner geschrieben.",
        "schema.saved_summary": "Alle Schemas gespeichert unter: {path} ({count} Dateien)",
    }
)

TRANSLATIONS["zh"].update(
    {
        "busy.schema_export.title": "\u6b63\u5728\u4fdd\u5b58\u5168\u90e8\u6a21\u677f",
        "busy.schema_export.body": "\u6b63\u5728\u5c06\u5168\u90e8\u6a21\u677f\u5de5\u4f5c\u7c3f\u5199\u5165\u6240\u9009\u6587\u4ef6\u5939\u3002",
        "schema.saved_summary": "\u5df2\u4fdd\u5b58\u5168\u90e8\u6a21\u677f\uff1a{path}\uff08{count} \u4e2a\u6587\u4ef6\uff09",
    }
)

TRANSLATIONS["en"].update(
    {
        "source_kind.ri_flowsheet": "R&I flowsheet",
        "family.ri_equipment_row": "R&I equipment row",
        "family.ri_instrument_function_row": "R&I instrument function row",
        "family.ri_piping_component_row": "R&I piping component row",
        "family.ri_connection_row": "R&I connection row",
        "settings.ocr_backend_label": "OCR backend",
        "settings.ocr_fallback_label": "Fallback backend",
        "settings.apple_ocr_framework_label": "Apple OCR mode",
        "settings.apple_ocr_recognition_label": "Apple Vision speed",
        "settings.apple_ocr_framework_inline_label": "Mode",
        "settings.apple_ocr_recognition_inline_label": "Speed",
        "settings.surya_prewarm_button": "Download models",
        "settings.clear_ocr_cache_button": "Clear Cache",
        "settings.clear_ocr_cache_tooltip": "Delete cached OCR, embedding, LLM, VLM, and preview files from {path}.",
        "settings.clear_ocr_cache_confirm_title": "Clear caches?",
        "settings.clear_ocr_cache_confirm_body": (
            "Delete cached OCR, embedding, LLM, VLM, and preview files from {path}?\n\n"
            "This cannot be undone. These caches will be rebuilt the next time they are needed."
        ),
        "settings.clear_ocr_cache_success_title": "Caches cleared",
        "settings.clear_ocr_cache_success_body": "Removed {count} cached item(s) from {path}.",
        "settings.clear_ocr_cache_failed_title": "Failed to clear caches",
        "settings.surya_prewarm_ready": "Surya ready ({ready}/{total} models cached)",
        "settings.surya_prewarm_missing": "Surya cache incomplete ({ready}/{total} ready)",
        "settings.surya_prewarm_title": "Prewarming Surya models",
        "settings.surya_prewarm_body": "Downloading any missing Surya models into the local cache.",
        "settings.surya_prewarm_failed": "Surya model prewarm failed",
        "settings.ocr_device_label": "Device",
        "settings.ocr_dpi_label": "OCR DPI",
        "settings.diagram_dpi_label": "Diagram DPI",
        "settings.ocr_confidence_label": "OCR min confidence",
        "settings.ocr_pipeline_mode_label": "OCR Pipeline Mode",
        "settings.ocr_pipeline_mode.fallback": "Fallback (Serial)",
        "settings.ocr_pipeline_mode.ensemble": "Ensemble (Parallel Fusion)",
        "settings.schema_ocr_checkbox": "Use OCR when generating schemas",
        "settings.extraction_ocr_checkbox": "Use OCR when running extraction",
        "settings.custom_t1_t5_rules_checkbox": "Use custom T1-T5 rules",
        "settings.custom_tx_rules_checkbox": "Use custom Tx rules",
        "settings.diagram_checkbox": "Enable diagram relation extraction",
        "settings.diagram_backend_label": "Diagram extraction backend",
        "settings.diagram_backend.none": "None (fast heuristic only)",
        "settings.diagram_backend.vlm_florence_large": "VLM (florence-2-large-ft)",
        "settings.diagram_backend.vlm_florence_base": "VLM (florence-2-base-ft)",
        "settings.diagram_backend.vlm_florence_base_mlx": "VLM (florence-2-base-nsfw-v2-ext-mlx)",
        "settings.aio_ml_evidence_linking_checkbox": "Enable AIO ML evidence linking",
        "settings.aio_ml_evidence_linking_tooltip": "Use the configured chat and embedding models to link AIO values to source artifacts.",
        "settings.refresh_models_tooltip": "Fetch chat, embedding, and VLM models from /v1/models API",
        "settings.hard_fallback_checkbox": "Enable hard-page fallback",
        "settings.runtime_state.available": "available",
        "settings.runtime_state.unavailable": "unavailable",
        "settings.runtime_state.download_required": "download required",
        "settings.runtime_summary": (
            "Runtime: primary {primary} ({primary_state}), "
            "fallback {fallback} ({fallback_state}), "
            "RapidOCR ({rapidocr_state}), "
            "EasyOCR ({easyocr_state}, device {easyocr_device}), "
            "Apple {apple_framework}/{apple_recognition_level}, active device {active_device}"
        ),
        "settings.runtime_summary_ensemble": "Runtime: current Ensemble backends {ensemble_backends}",
        "settings.runtime_summary_non_macos": (
            "Runtime: PaddleOCR ({paddle_state}), "
            "RapidOCR ({rapidocr_state}), "
            "Surya ({surya_state}), "
            "EasyOCR ({easyocr_state}, device {easyocr_device})"
        ),
    }
)

TRANSLATIONS["de"].update(
    {
        "source_kind.ri_flowsheet": "R&I-Fliessbild",
        "family.ri_equipment_row": "R&I-Anlagenobjekt-Zeile",
        "family.ri_instrument_function_row": "R&I-Instrumentierungsfunktion-Zeile",
        "family.ri_piping_component_row": "R&I-Rohrleitungskomponente-Zeile",
        "family.ri_connection_row": "R&I-Verbindungs-Zeile",
        "settings.ocr_backend_label": "OCR-Backend",
        "settings.ocr_fallback_label": "Fallback-Backend",
        "settings.apple_ocr_framework_label": "Apple-OCR-Modus",
        "settings.apple_ocr_recognition_label": "Apple-Vision-Geschwindigkeit",
        "settings.apple_ocr_framework_inline_label": "Modus",
        "settings.apple_ocr_recognition_inline_label": "Tempo",
        "settings.surya_prewarm_button": "Modelle laden",
        "settings.clear_ocr_cache_button": "Cache leeren",
        "settings.clear_ocr_cache_tooltip": "Alle zwischengespeicherten OCR-, Embedding-, LLM-, VLM- und Preview-Dateien aus {path} loeschen.",
        "settings.clear_ocr_cache_confirm_title": "Caches leeren?",
        "settings.clear_ocr_cache_confirm_body": (
            "Alle zwischengespeicherten OCR-, Embedding-, LLM-, VLM- und Preview-Dateien aus {path} loeschen?\n\n"
            "Das kann nicht rueckgaengig gemacht werden. Diese Caches werden bei Bedarf neu erzeugt."
        ),
        "settings.clear_ocr_cache_success_title": "Caches geleert",
        "settings.clear_ocr_cache_success_body": "{count} Cache-Eintraege aus {path} entfernt.",
        "settings.clear_ocr_cache_failed_title": "Caches konnten nicht geleert werden",
        "settings.surya_prewarm_ready": "Surya bereit ({ready}/{total} Modelle im Cache)",
        "settings.surya_prewarm_missing": "Surya-Cache unvollstaendig ({ready}/{total} bereit)",
        "settings.surya_prewarm_title": "Surya-Modelle werden vorgeladen",
        "settings.surya_prewarm_body": "Fehlende Surya-Modelle werden in den lokalen Cache heruntergeladen.",
        "settings.surya_prewarm_failed": "Surya-Modellvorladung fehlgeschlagen",
        "settings.ocr_device_label": "Geraet",
        "settings.ocr_dpi_label": "OCR-DPI",
        "settings.diagram_dpi_label": "Diagramm-DPI",
        "settings.ocr_confidence_label": "OCR-Mindestkonfidenz",
        "settings.ocr_pipeline_mode_label": "OCR-Pipeline-Modus",
        "settings.ocr_pipeline_mode.fallback": "Fallback (Seriell)",
        "settings.ocr_pipeline_mode.ensemble": "Ensemble (Parallele Fusion)",
        "settings.schema_ocr_checkbox": "OCR bei der Schema-Erstellung verwenden",
        "settings.extraction_ocr_checkbox": "OCR bei der Extraktion verwenden",
        "settings.custom_t1_t5_rules_checkbox": "Benutzerdefinierte T1-T5-Regeln verwenden",
        "settings.custom_tx_rules_checkbox": "Benutzerdefinierte Tx-Regeln verwenden",
        "settings.diagram_checkbox": "Diagramm-Beziehungsextraktion aktivieren",
        "settings.diagram_backend_label": "Diagramm-Extraktions-Backend",
        "settings.diagram_backend.none": "Keins (nur schnelle Heuristik)",
        "settings.diagram_backend.vlm_florence_large": "VLM (florence-2-large-ft)",
        "settings.diagram_backend.vlm_florence_base": "VLM (florence-2-base-ft)",
        "settings.diagram_backend.vlm_florence_base_mlx": "VLM (florence-2-base-nsfw-v2-ext-mlx)",
        "settings.aio_ml_evidence_linking_checkbox": "AIO-ML-Evidenzverknuepfung aktivieren",
        "settings.aio_ml_evidence_linking_tooltip": "Verknuepft AIO-Werte mit Quell-Artefakten ueber die konfigurierten Chat- und Embedding-Modelle.",
        "settings.refresh_models_tooltip": "Chat-, Embedding- und VLM-Modelle ueber /v1/models laden",
        "settings.hard_fallback_checkbox": "Fallback fuer schwierige Seiten aktivieren",
        "settings.runtime_state.available": "verfuegbar",
        "settings.runtime_state.unavailable": "nicht verfuegbar",
        "settings.runtime_state.download_required": "Download erforderlich",
        "settings.runtime_summary": (
            "Laufzeit: primaer {primary} ({primary_state}), "
            "Fallback {fallback} ({fallback_state}), "
            "RapidOCR ({rapidocr_state}), "
            "EasyOCR ({easyocr_state}, Geraet {easyocr_device}), "
            "Apple {apple_framework}/{apple_recognition_level}, aktives Geraet {active_device}"
        ),
        "settings.runtime_summary_ensemble": "Laufzeit: aktuelle Ensemble-Backends {ensemble_backends}",
        "settings.runtime_summary_non_macos": (
            "Laufzeit: PaddleOCR ({paddle_state}), "
            "RapidOCR ({rapidocr_state}), "
            "Surya ({surya_state}), "
            "EasyOCR ({easyocr_state}, Geraet {easyocr_device})"
        ),
    }
)

TRANSLATIONS["zh"].update(
    {
        "source_kind.ri_flowsheet": "R&I流程图",
        "family.ri_equipment_row": "R&I设备记录",
        "family.ri_instrument_function_row": "R&I仪表功能记录",
        "family.ri_piping_component_row": "R&I管线组件记录",
        "family.ri_connection_row": "R&I连接记录",
        "settings.ocr_backend_label": "\u4e3b OCR \u540e\u7aef",
        "settings.ocr_fallback_label": "\u56de\u9000\u540e\u7aef",
        "settings.apple_ocr_framework_label": "Apple OCR \u6a21\u5f0f",
        "settings.apple_ocr_recognition_label": "Apple Vision \u901f\u5ea6",
        "settings.apple_ocr_framework_inline_label": "\u6a21\u5f0f",
        "settings.apple_ocr_recognition_inline_label": "\u901f\u5ea6",
        "settings.surya_prewarm_button": "\u4e0b\u8f7d\u6a21\u578b",
        "settings.clear_ocr_cache_button": "\u6e05\u7406\u7f13\u5b58",
        "settings.clear_ocr_cache_tooltip": "\u5220\u9664 {path} \u4e2d\u7684 OCR\u3001Embedding\u3001LLM\u3001VLM \u4ee5\u53ca\u9884\u89c8\u7f13\u5b58\u6587\u4ef6\u3002",
        "settings.clear_ocr_cache_confirm_title": "\u6e05\u7406\u7f13\u5b58\uff1f",
        "settings.clear_ocr_cache_confirm_body": (
            "\u8981\u5220\u9664 {path} \u4e2d\u7684 OCR\u3001Embedding\u3001LLM\u3001VLM \u4ee5\u53ca\u9884\u89c8\u7f13\u5b58\u6587\u4ef6\u5417\uff1f\n\n"
            "\u6b64\u64cd\u4f5c\u65e0\u6cd5\u64a4\u9500\u3002\u4e0b\u6b21\u9700\u8981\u65f6\u8fd9\u4e9b\u7f13\u5b58\u4f1a\u91cd\u65b0\u751f\u6210\u3002"
        ),
        "settings.clear_ocr_cache_success_title": "\u7f13\u5b58\u5df2\u6e05\u7406",
        "settings.clear_ocr_cache_success_body": "\u5df2\u4ece {path} \u5220\u9664 {count} \u4e2a\u7f13\u5b58\u9879\u3002",
        "settings.clear_ocr_cache_failed_title": "\u6e05\u7406\u7f13\u5b58\u5931\u8d25",
        "settings.surya_prewarm_ready": "Surya \u5df2\u5c31\u7eea\uff08{ready}/{total} \u4e2a\u6a21\u578b\u5df2\u7f13\u5b58\uff09",
        "settings.surya_prewarm_missing": "Surya \u7f13\u5b58\u4e0d\u5b8c\u6574\uff08\u5df2\u5c31\u7eea {ready}/{total}\uff09",
        "settings.surya_prewarm_title": "\u6b63\u5728\u9884\u70ed Surya \u6a21\u578b",
        "settings.surya_prewarm_body": "\u6b63\u5728\u5c06\u7f3a\u5931\u7684 Surya \u6a21\u578b\u4e0b\u8f7d\u5230\u672c\u5730\u7f13\u5b58\u3002",
        "settings.surya_prewarm_failed": "Surya \u6a21\u578b\u9884\u70ed\u5931\u8d25",
        "settings.ocr_device_label": "\u8bbe\u5907",
        "settings.ocr_dpi_label": "OCR DPI",
        "settings.diagram_dpi_label": "\u56fe\u7eb8 DPI",
        "settings.ocr_confidence_label": "OCR \u6700\u4f4e\u7f6e\u4fe1\u5ea6",
        "settings.ocr_pipeline_mode_label": "OCR \u7ba1\u7ebf\u6a21\u5f0f",
        "settings.ocr_pipeline_mode.fallback": "\u4e32\u884c\u964d\u7ea7",
        "settings.ocr_pipeline_mode.ensemble": "\u5e76\u884c\u878d\u5408",
        "settings.schema_ocr_checkbox": "\u751f\u6210\u6a21\u677f\u65f6\u4f7f\u7528OCR",
        "settings.extraction_ocr_checkbox": "\u62bd\u53d6\u65f6\u4f7f\u7528OCR",
        "settings.custom_t1_t5_rules_checkbox": "\u4f7f\u7528\u81ea\u5b9a\u4e49 T1-T5 \u89c4\u5219",
        "settings.custom_tx_rules_checkbox": "\u4f7f\u7528\u81ea\u5b9a\u4e49 Tx \u89c4\u5219",
        "settings.diagram_checkbox": "\u542f\u7528\u56fe\u7eb8\u5173\u7cfb\u62bd\u53d6",
        "settings.diagram_backend_label": "\u56fe\u7eb8\u5206\u6790\u540e\u7aef",
        "settings.diagram_backend.none": "\u65e0\uff08\u4ec5\u4f7f\u7528\u5feb\u901f\u542f\u53d1\u5f0f\uff09",
        "settings.diagram_backend.vlm_florence_large": "VLM\uff08florence-2-large-ft\uff09",
        "settings.diagram_backend.vlm_florence_base": "VLM\uff08florence-2-base-ft\uff09",
        "settings.diagram_backend.vlm_florence_base_mlx": "VLM\uff08florence-2-base-nsfw-v2-ext-mlx\uff09",
        "settings.aio_ml_evidence_linking_checkbox": "\u542f\u7528 AIO ML \u8bc1\u636e\u94fe\u63a5",
        "settings.aio_ml_evidence_linking_tooltip": "\u4f7f\u7528\u5f53\u524d\u914d\u7f6e\u7684 chat \u548c embedding \u6a21\u578b\u5c06 AIO \u503c\u94fe\u63a5\u5230\u6e90 artifact\u3002",
        "settings.refresh_models_tooltip": "\u4ece /v1/models \u5237\u65b0 chat\u3001embedding \u548c VLM \u6a21\u578b",
        "settings.hard_fallback_checkbox": "\u542f\u7528\u96be\u9875\u9762\u56de\u9000",
        "settings.runtime_state.available": "\u53ef\u7528",
        "settings.runtime_state.unavailable": "\u4e0d\u53ef\u7528",
        "settings.runtime_state.download_required": "\u9700\u4e0b\u8f7d\u6a21\u578b",
        "settings.runtime_summary": (
            "\u8fd0\u884c\u65f6\uff1a\u4e3b\u540e\u7aef {primary}\uff08{primary_state}\uff09\uff0c"
            "\u56de\u9000 {fallback}\uff08{fallback_state}\uff09\uff0c"
            "RapidOCR\uff08{rapidocr_state}\uff09\uff0c"
            "EasyOCR\uff08{easyocr_state}\uff0c\u8bbe\u5907 {easyocr_device}\uff09\uff0c"
            "Apple {apple_framework}/{apple_recognition_level}\uff0c\u5f53\u524d\u8bbe\u5907 {active_device}"
        ),
        "settings.runtime_summary_ensemble": "\u8fd0\u884c\u65f6\uff1a\u5f53\u524d Ensemble \u540e\u7aef {ensemble_backends}",
        "settings.runtime_summary_non_macos": (
            "\u8fd0\u884c\u65f6\uff1aPaddleOCR\uff08{paddle_state}\uff09\uff0c"
            "RapidOCR\uff08{rapidocr_state}\uff09\uff0c"
            "Surya\uff08{surya_state}\uff09\uff0c"
            "EasyOCR\uff08{easyocr_state}\uff0c\u8bbe\u5907 {easyocr_device}\uff09"
        ),
    }
)


def normalize_language(language: str) -> str:
    return language if language in TRANSLATIONS else "en"


def tr(language: str, key: str, **kwargs) -> str:
    language = normalize_language(language)
    template = TRANSLATIONS.get(language, {}).get(key, TRANSLATIONS["en"].get(key, key))
    return template.format(**kwargs)


def translate_family(language: str, family: DocumentFamily | str) -> str:
    value = family.value if isinstance(family, DocumentFamily) else family
    return tr(language, f"family.{value}")


def translate_source_kind(language: str, value: str) -> str:
    translated = tr(language, f"source_kind.{value}")
    if translated == f"source_kind.{value}":
        if value == "ri_flowsheet":
            return "R&I Flowsheet"
        return value.replace("_", " ").title()
    return translated


def translate_status(language: str, value: str) -> str:
    return tr(language, f"status.{value}")


def family_option_text(language: str, family: DocumentFamily) -> str:
    return f"{translate_family(language, family)} ({family.value})"


LANGUAGE_OPTIONS = [
    ("en", "English"),
    ("de", "Deutsch"),
    ("zh", "中文"),
]

TRANSLATIONS["en"].update(
    {
        "nav.extraction_review": "UC1 Pipeline",
        "page.extraction_review.title": "UC1 Pipeline",
        "page.extraction_review.subtitle": "Run scan, schema refresh, extraction, and the full T1-T10 UC1 transformation chain in one place.",
        "busy.schema_export.title": "Saving current schema",
        "busy.schema_export.body": "Writing the current schema workbook to the selected folder.",
    }
)

TRANSLATIONS["de"].update(
    {
        "nav.extraction_review": "UC1 Pipeline",
        "page.extraction_review.title": "UC1 Pipeline",
        "page.extraction_review.subtitle": "Scan, Schema-Aktualisierung, Extraktion und die gesamte UC1-Transformationskette T1-T10 an einem Ort ausführen.",
        "busy.schema_export.title": "Aktuelles Schema wird gespeichert",
        "busy.schema_export.body": "Die aktuelle Schema-Arbeitsmappe wird in den gewählten Ordner geschrieben.",
    }
)

TRANSLATIONS["zh"].update(
    {
        "nav.extraction_review": "UC1 流程",
        "page.extraction_review.title": "UC1 流程",
        "page.extraction_review.subtitle": "在同一个页面中完成扫描、模板刷新、抽取以及完整的 T1-T10 UC1 转换链。",
        "common.scan_root": "文档文件夹",
        "common.schema_save_dir": "模板保存文件夹",
        "common.export_dir": "导出文件夹",
        "common.browse": "浏览...",
        "dialog.select_scan_root": "选择文档文件夹",
        "dialog.select_schema_export_dir": "选择模板保存文件夹",
        "dialog.select_export_dir": "选择导出文件夹",
        "busy.scan.title": "正在扫描文档",
        "busy.scan.body": "正在搜索所选文件夹并分类支持的文件。",
        "busy.schema.title": "正在生成模板",
        "busy.schema.body": "正在读取文档并为每个文档族生成模板候选。",
        "busy.extraction.title": "正在运行抽取",
        "busy.extraction.body": "正在解析文档、检索证据并保存可复核结果。",
        "busy.export.title": "正在导出文件",
        "busy.export.body": "正在将模板和抽取结果工作簿写入所选文件夹。",
        "busy.schema_export.title": "正在保存当前模板",
        "busy.schema_export.body": "正在将当前模板工作簿写入所选文件夹。",
        "schema.saved_to": "当前模板已保存到：{path}",
    }
)
TRANSLATIONS["en"].update(
    {
        "nav.pid_inconsistency": "Existence Confidence",
        "page.pid_inconsistency.title": "Existence Confidence",
        "page.pid_inconsistency.subtitle": "Check whether component tags exist across P&ID, DEXPI, Instrument index sheets, Interconnection lists, and IFC.",
        "busy.pid.title": "Checking existence confidence",
        "busy.pid.body": "Building the latest existence confidence result.",
        "pid.summary.total": "Tags: {count}",
        "pid.summary.problem_components": "Open tags: {count}",
        "pid.summary.problem_items": "Open items: {count}",
        "pid.empty.no_data": "No source data is available yet.",
        "pid.header.component": "Component",
        "pid.header.type": "Type",
        "pid.header.pdf": "P&ID",
        "pid.header.xml": "DEXPI",
        "pid.header.xsd": "XSD",
        "pid.header.stellenplaene": "Instrument index sheets",
        "pid.header.verschaltungslisten": "Interconnection lists",
        "pid.header.issues": "Issues",
        "pid.header.ifc": "IFC",
        "pid.header.canonical_tag": "Canonical Tag",
        "pid.issue.none": "No open issue.",
        "pid.issue.missing_pdf": "Missing in P&ID evidence.",
        "pid.issue.missing_xml": "Missing in DEXPI XML.",
        "pid.issue.missing_xsd": "No compatible XSD coverage for this component.",
        "pid.issue.xsd_incompatible": "XSD validation or compatibility conflict detected.",
        "pid.issue.missing_stellenplaene": "No exact match found in instrument index sheets.",
        "pid.issue.missing_verschaltungslisten": "No exact match found in interconnection lists.",
        "pid.issue.type_conflict": "Conflicting internal component type metadata.",
        "pid.status.pdf.present": "Found in P&ID evidence. Click to jump into review results.",
        "pid.status.pdf.missing": "No matching P&ID evidence was found for this component.",
        "pid.status.pdf.conflict": "P&ID evidence exists but conflicts with other internal metadata.",
        "pid.status.xml.present": "Found in DEXPI XML instance data. Click to jump into review results.",
        "pid.status.xml.missing": "No matching DEXPI XML instance entry was found.",
        "pid.status.xml.conflict": "XML instance data conflicts with other internal metadata.",
        "pid.status.xsd.present": "The corresponding R&I structure is compatible with the XSD-derived schema.",
        "pid.status.xsd.missing": "No compatible XSD-derived structure is available for this component.",
        "pid.status.xsd.conflict": "The XML/XSD compatibility check reported a conflict.",
        "pid.status.stellenplaene.present": "Found as an exact normalized match in instrument index sheets. Click to jump into review results.",
        "pid.status.stellenplaene.missing": "No exact normalized match was found in instrument index sheets.",
        "pid.status.stellenplaene.conflict": "Matching instrument index sheet records exist but the source is inconsistent.",
        "pid.status.verschaltungslisten.present": "Found as an exact normalized match in interconnection lists. Click to jump into review results.",
        "pid.status.verschaltungslisten.missing": "No exact normalized match was found in interconnection lists.",
        "pid.status.verschaltungslisten.conflict": "Matching interconnection list records exist but the source is inconsistent.",
    }
)

TRANSLATIONS["de"].update(
    {
        "nav.pid_inconsistency": "Existenz Konfidenz",
        "page.pid_inconsistency.title": "Existenz Konfidenz",
        "page.pid_inconsistency.subtitle": "Prüft, ob Komponenten-Tags in PDF, XML, Stellenplaene, Verschaltungslisten und IFC vorhanden sind.",
        "busy.pid.title": "Existenz Konfidenz wird geprüft",
        "busy.pid.body": "Das aktuelle Existenz-Konfidenz-Ergebnis wird aufgebaut.",
        "pid.summary.total": "Tags: {count}",
        "pid.summary.problem_components": "Offene Tags: {count}",
        "pid.summary.problem_items": "Offene Einträge: {count}",
        "pid.empty.no_data": "Noch keine Quelldaten verfügbar.",
        "pid.header.component": "Komponente",
        "pid.header.type": "Typ",
        "pid.header.pdf": "P&ID",
        "pid.header.xml": "DEXPI",
        "pid.header.xsd": "XSD",
        "pid.header.stellenplaene": "Stellenplaene",
        "pid.header.verschaltungslisten": "Verschaltungslisten",
        "pid.header.issues": "Probleme",
        "pid.header.ifc": "IFC",
        "pid.header.canonical_tag": "Kanonische Kennung",
        "pid.issue.none": "Kein offener Punkt.",
        "pid.issue.missing_pdf": "Kein Treffer in den P&ID-Evidenzen.",
        "pid.issue.missing_xml": "Fehlt in der DEXPI-XML-Instanz.",
        "pid.issue.missing_xsd": "Keine passende XSD-Abdeckung für diese Komponente.",
        "pid.issue.xsd_incompatible": "Ein Konflikt bei XML/XSD-Validierung oder -Kompatibilität wurde erkannt.",
        "pid.issue.missing_stellenplaene": "Kein exakt normalisierter Treffer in Stellenplaene gefunden.",
        "pid.issue.missing_verschaltungslisten": "Kein exakt normalisierter Treffer in Verschaltungslisten gefunden.",
        "pid.issue.type_conflict": "Widersprüchliche interne Typinformationen zur Komponente.",
        "pid.status.pdf.present": "In PDF-OCR/Native-Text gefunden. Klicken, um zur Prüfung zu springen.",
        "pid.status.pdf.missing": "Keine passende PDF-OCR/Native-Text-Evidenz gefunden.",
        "pid.status.pdf.conflict": "PDF-Evidenz vorhanden, aber im Konflikt mit anderer interner Information.",
        "pid.status.xml.present": "In DEXPI-XML-Instanzdaten gefunden. Klicken, um zur Prüfung zu springen.",
        "pid.status.xml.missing": "Kein passender DEXPI-XML-Eintrag gefunden.",
        "pid.status.xml.conflict": "XML-Instanzdaten stehen im Konflikt mit anderer interner Information.",
        "pid.status.xsd.present": "Die zugehörige R&I-Struktur ist mit dem aus XSD abgeleiteten Schema kompatibel.",
        "pid.status.xsd.missing": "Für diese Komponente ist keine passende XSD-Struktur verfügbar.",
        "pid.status.xsd.conflict": "Die XML/XSD-Kompatibilitätsprüfung hat einen Konflikt gemeldet.",
        "pid.status.stellenplaene.present": "Als exakter normalisierter Treffer in Stellenplaene gefunden. Klicken, um zur Prüfung zu springen.",
        "pid.status.stellenplaene.missing": "Kein exakter normalisierter Treffer in Stellenplaene gefunden.",
        "pid.status.stellenplaene.conflict": "Treffer in Stellenplaene vorhanden, aber inkonsistent.",
        "pid.status.verschaltungslisten.present": "Als exakter normalisierter Treffer in Verschaltungslisten gefunden. Klicken, um zur Prüfung zu springen.",
        "pid.status.verschaltungslisten.missing": "Kein exakter normalisierter Treffer in Verschaltungslisten gefunden.",
        "pid.status.verschaltungslisten.conflict": "Treffer in Verschaltungslisten vorhanden, aber inkonsistent.",
    }
)

TRANSLATIONS["zh"].update(
    {
        "nav.pid_inconsistency": "存在性置信",
        "page.pid_inconsistency.title": "存在性置信检测",
        "page.pid_inconsistency.subtitle": "检查组件标签是否存在于 PDF、XML、Stellenplaene、Verschaltungslisten 和 IFC 中。",
        "busy.pid.title": "正在检查存在性置信",
        "busy.pid.body": "正在生成最新的存在置信结果。",
        "pid.summary.total": "标签总数：{count}",
        "pid.summary.problem_components": "待处理标签：{count}",
        "pid.summary.problem_items": "待处理项：{count}",
        "pid.empty.no_data": "当前还没有可用于检测的源数据。",
        "pid.header.component": "组件",
        "pid.header.type": "类型",
        "pid.header.pdf": "P&ID",
        "pid.header.xml": "DEXPI",
        "pid.header.xsd": "XSD",
        "pid.header.stellenplaene": "Stellenplaene",
        "pid.header.verschaltungslisten": "Verschaltungslisten",
        "pid.header.issues": "问题",
        "pid.header.ifc": "IFC",
        "pid.header.canonical_tag": "规范标签",
        "pid.issue.none": "没有待处理问题。",
        "pid.issue.missing_pdf": "在 P&ID 证据中未找到该组件。",
        "pid.issue.missing_xml": "在 DEXPI XML 中未找到该组件。",
        "pid.issue.missing_xsd": "该组件没有可兼容的 XSD 结构定义。",
        "pid.issue.xsd_incompatible": "检测到 XML/XSD 验证或兼容性冲突。",
        "pid.issue.missing_stellenplaene": "在 Stellenplaene 中未找到严格规范化完全匹配项。",
        "pid.issue.missing_verschaltungslisten": "在 Verschaltungslisten 中未找到严格规范化完全匹配项。",
        "pid.issue.type_conflict": "内部组件类型元数据存在冲突。",
        "pid.status.pdf.present": "已在 PDF OCR/原生文本证据中找到。点击可跳转到复核结果。",
        "pid.status.pdf.missing": "未找到匹配的 PDF OCR/原生文本证据。",
        "pid.status.pdf.conflict": "存在 PDF 证据，但与其他内部信息冲突。",
        "pid.status.xml.present": "已在 DEXPI XML 实例数据中找到。点击可跳转到复核结果。",
        "pid.status.xml.missing": "未找到匹配的 DEXPI XML 实例项。",
        "pid.status.xml.conflict": "XML 实例数据与其他内部信息冲突。",
        "pid.status.xsd.present": "对应的 R&I 结构与 XSD 推导出的 schema 兼容。",
        "pid.status.xsd.missing": "当前没有与该组件兼容的 XSD 结构。",
        "pid.status.xsd.conflict": "XML/XSD 兼容性检查报告了冲突。",
        "pid.status.stellenplaene.present": "已在 Stellenplaene 中找到严格规范化完全匹配项。点击可跳转到复核结果。",
        "pid.status.stellenplaene.missing": "在 Stellenplaene 中未找到严格规范化完全匹配项。",
        "pid.status.stellenplaene.conflict": "Stellenplaene 中存在匹配记录，但来源存在冲突。",
        "pid.status.verschaltungslisten.present": "已在 Verschaltungslisten 中找到严格规范化完全匹配项。点击可跳转到复核结果。",
        "pid.status.verschaltungslisten.missing": "在 Verschaltungslisten 中未找到严格规范化完全匹配项。",
        "pid.status.verschaltungslisten.conflict": "Verschaltungslisten 中存在匹配记录，但来源存在冲突。",
    }
)

TRANSLATIONS["en"].update(
    {
        "review.action.view_source_position": "View value source position",
        "review.preview.title": "Value source preview",
        "review.preview.source": "Source file",
        "review.preview.location": "Location",
        "review.preview.evidence": "Evidence",
        "review.preview.zoom_in": "Zoom in",
        "review.preview.zoom_out": "Zoom out",
        "review.preview.zoom_reset": "100%",
        "review.preview.fit": "Fit highlight",
        "review.preview.loading": "Loading source preview...",
        "review.preview.unsupported": "This source type is not supported for preview yet.",
        "review.preview.no_locator": "No precise locator",
        "review.preview.empty_value": "This value is empty, so there is no source position to show.",
        "review.preview.no_evidence": "No source evidence is available for this value.",
        "review.preview.file_missing": "The source file cannot be resolved.",
    }
)

TRANSLATIONS["de"].update(
    {
        "review.action.view_source_position": "Quellposition des Werts anzeigen",
        "review.preview.title": "Quellpositionsvorschau",
        "review.preview.source": "Quelldatei",
        "review.preview.location": "Position",
        "review.preview.evidence": "Evidenz",
        "review.preview.zoom_in": "Vergrößern",
        "review.preview.zoom_out": "Verkleinern",
        "review.preview.zoom_reset": "100%",
        "review.preview.fit": "Auf Markierung anpassen",
        "review.preview.loading": "Quellvorschau wird geladen...",
        "review.preview.unsupported": "Dieser Quelltyp wird für die Vorschau noch nicht unterstützt.",
        "review.preview.no_locator": "Keine genaue Position",
        "review.preview.empty_value": "Dieser Wert ist leer, daher gibt es keine Quellposition.",
        "review.preview.no_evidence": "Für diesen Wert ist keine Quellevidenz verfügbar.",
        "review.preview.file_missing": "Die Quelldatei konnte nicht aufgelöst werden.",
    }
)

TRANSLATIONS["zh"].update(
    {
        "review.action.view_source_position": "查看值的来源位置",
        "review.preview.title": "值来源定位预览",
        "review.preview.source": "来源文件",
        "review.preview.location": "位置",
        "review.preview.evidence": "证据",
        "review.preview.zoom_in": "放大",
        "review.preview.zoom_out": "缩小",
        "review.preview.zoom_reset": "100%",
        "review.preview.fit": "适配高亮",
        "review.preview.loading": "正在加载来源预览...",
        "review.preview.unsupported": "当前暂不支持这种来源文件的定位预览。",
        "review.preview.no_locator": "没有精确定位信息",
        "review.preview.empty_value": "这个值为空，所以没有可显示的来源位置。",
        "review.preview.no_evidence": "这个值没有可用的来源证据。",
        "review.preview.file_missing": "无法解析这个来源文件。",
    }
)

TRANSLATIONS["en"].update(
    {
        "app.title": "Inconsistence Extract",
        "page.quick_start.title": "Inconsistence Extract - Quick Start",
        "page.quick_start.subtitle": (
            "Start here for the Inconsistence Extract workflow, the Existence Confidence page, "
            "and the deterministic transformation chain."
        ),
        "quickstart.markdown": (
            "## What this app does\n"
            "- Scans the selected `Documents` folder and keeps source evidence\n"
            "- Shows scanned files, template fields, and extracted data in the main table area\n"
            "- Shows a compact **Existence Confidence** page for cross-document existence checks\n"
            "- Fills standardized templates directly and exports AAS models plus Protégé-readable OWL files\n\n"
            "## Recommended workflow\n"
            "1. Open **Inconsistence Extract**.\n"
            "2. Choose the document folder and click **Scan Workspace**.\n"
            "3. Click **Start Extraction** to extract records and fill the standardized templates.\n"
            "4. Use the view switch to inspect scanned files, template fields, or extracted data.\n"
            "5. Click **Save Extraction Results** to export the filled templates by category.\n"
            "6. Click **Generate AAS Model**.\n"
            "7. Click **Export OWL**.\n\n"
            "## Main pages\n"
            "- **Quick Start**: short workflow summary\n"
            "- **Inconsistence Extract**: scan, extract, inspect, and run the transformation exports\n"
            "- **Existence Confidence**: inspect compact existence confidence results and source evidence\n"
            "- **Log**: inspect background steps for debugging and experiments\n"
            "- **Model Settings**: switch interface language and configure OCR / optional LLM settings\n\n"
            "## Notes\n"
            "- The extraction step directly fills the standardized templates in one go.\n"
            "- The later AAS and OWL exports stay deterministic and traceable.\n"
            "- The same main page keeps the scan table, template table, and extracted review table."
        ),
        "quickstart.hero_title": "Inconsistence extraction with auditable transformations",
        "quickstart.hero_body": (
            "Keep OCR or LLM-assisted extraction before the standardized Excel stage, then export "
            "AAS and OWL deterministically with visible source evidence."
        ),
        "quickstart.badge.export": "Standardized Excel + AAS + OWL",
        "quickstart.card.scan.body": "Choose a document folder and scan DEXPI, PDF, XLS/XLSX, and IFC inputs.",
        "quickstart.card.schema.body": "Run Start Extraction to extract records and fill standardized templates in one step.",
        "quickstart.card.schema.title": "2. Extract",
        "quickstart.card.run.body": (
            "Save the filled templates by category, then export AAS models and OWL files."
        ),
        "quickstart.card.run.title": "3. Export",
        "common.export_all": "Export Raw Extracted Results",
        "busy.export.title": "Exporting raw extracted results",
        "busy.export.body": "Writing raw extraction workbooks and source-family outputs to the selected folder.",
        "busy.extraction_fill.title": "Running extraction",
        "busy.extraction_fill.body": "Scanning documents, generating schemas, extracting records, and filling standardized templates.",
        "busy.save_results.title": "Saving extraction results",
        "busy.save_results.body": "Copying filled templates to the export directory organized by category.",
        "nav.extraction_review": "Inconsistence Extract",
        "page.extraction_review.title": "Inconsistence Extract",
        "page.extraction_review.subtitle": (
            "Choose the document folder, scan files, inspect templates and extracted data, "
            "and run the transformation exports in one place."
        ),
    }
)

TRANSLATIONS["de"].update(
    {
        "app.title": "Inconsistence Extract",
        "page.quick_start.title": "Inconsistence Extract - Schnellstart",
        "page.quick_start.subtitle": (
            "Hier starten Sie mit dem Inconsistence-Extract-Ablauf, der wiederhergestellten "
            "Existenz-Konfidenz-Seite und der deterministischen Transformationskette."
        ),
        "quickstart.markdown": (
            "## Was die Anwendung macht\n"
            "- scannt den gewaehlten `Documents`-Ordner und behaelt Quell-Evidenzen\n"
            "- zeigt gescannte Dateien, Vorlagenfelder und extrahierte Daten im zentralen Tabellenbereich\n"
            "- zeigt die kompakte Seite **Existenz Konfidenz** fuer dokumentuebergreifende Existenzpruefungen\n"
            "- befuellt standardisierte Vorlagen direkt und exportiert AAS-Modelle sowie Protégé-lesbare OWL-Dateien\n\n"
            "## Empfohlener Ablauf\n"
            "1. **Inconsistence Extract** oeffnen.\n"
            "2. Dokumentordner waehlen und **Arbeitsbereich scannen** klicken.\n"
            "3. **Extraktion starten** klicken, um Datensaetze zu extrahieren und die standardisierten Vorlagen zu befuellen.\n"
            "4. Mit der Ansichtsumschaltung gescannte Dateien, Vorlagenfelder oder extrahierte Daten pruefen.\n"
            "5. **Ergebnisse speichern** klicken, um die befuellten Vorlagen nach Kategorie zu exportieren.\n"
            "6. **AAS-Modell erzeugen** klicken.\n"
            "7. **OWL exportieren** klicken.\n\n"
            "## Hauptseiten\n"
            "- **Quick Start**: kurze Workflow-Zusammenfassung\n"
            "- **Inconsistence Extract**: scannen, extrahieren, pruefen und Transformationsexporte ausfuehren\n"
            "- **Existenz Konfidenz**: kompakte Existenz-Konfidenz-Ergebnisse und Quellevidenz ansehen\n"
            "- **Log**: Hintergrundschritte fuer Debugging und Experimente ansehen\n"
            "- **Model Settings**: Oberflaechensprache sowie OCR- und optionale LLM-Einstellungen konfigurieren\n\n"
            "## Hinweise\n"
            "- Der Extraktionsschritt befuellt die standardisierten Vorlagen direkt in einem Durchlauf.\n"
            "- Die spaeteren AAS- und OWL-Exporte bleiben deterministisch und rueckverfolgbar.\n"
            "- Dieselbe Hauptseite behaelt Scan-Tabelle, Vorlagen-Tabelle und Extraktions-Tabelle zusammen."
        ),
        "quickstart.hero_title": "Inkonsistenz-Extraktion mit nachvollziehbaren Transformationen",
        "quickstart.hero_body": (
            "OCR- oder LLM-gestuetzte Extraktion bleibt vor dem standardisierten Excel-Schritt, "
            "danach werden AAS und OWL deterministisch mit sichtbarer Quellevidenz exportiert."
        ),
        "quickstart.badge.export": "Standardized Excel + AAS + OWL",
        "quickstart.card.scan.body": "Dokumentordner waehlen und DEXPI-, PDF-, XLS/XLSX- sowie IFC-Eingaenge scannen.",
        "quickstart.card.schema.body": "Extraktion starten klicken, um Datensaetze zu extrahieren und standardisierte Vorlagen direkt zu befuellen.",
        "quickstart.card.schema.title": "2. Extrahieren",
        "quickstart.card.run.body": (
            "Befuellte Vorlagen nach Kategorie speichern, danach AAS-Modelle und OWL-Dateien exportieren."
        ),
        "quickstart.card.run.title": "3. Exportieren",
        "common.export_all": "Roh extrahierte Ergebnisse exportieren",
        "busy.export.title": "Roh extrahierte Ergebnisse werden exportiert",
        "busy.export.body": "Roh-Extraktionsarbeitsmappen und familienbezogene Ausgaben werden in den gewaehlten Ordner geschrieben.",
        "busy.extraction_fill.title": "Extraktion läuft",
        "busy.extraction_fill.body": "Dokumente werden gescannt, Schemas erzeugt, Datensätze extrahiert und standardisierte Vorlagen befüllt.",
        "busy.save_results.title": "Ergebnisse werden gespeichert",
        "busy.save_results.body": "Befüllte Vorlagen werden nach Kategorie sortiert ins Export-Verzeichnis kopiert.",
        "nav.extraction_review": "Inconsistence Extract",
        "page.extraction_review.title": "Inconsistence Extract",
        "page.extraction_review.subtitle": (
            "Dokumentordner waehlen, Dateien scannen, Vorlagen und extrahierte Daten pruefen "
            "und die Transformationsexporte an einem Ort ausfuehren."
        ),
    }
)

TRANSLATIONS["zh"].update(
    {
        "app.title": "Inconsistence Extract",
        "page.quick_start.title": "Inconsistence Extract - 快速开始",
        "page.quick_start.subtitle": "这里说明 Inconsistence Extract 的主流程、存在性置信检测页面，以及确定性的转换链。",
        "quickstart.markdown": (
            "## 这个程序做什么\n"
            "- 扫描所选的 `Documents` 文件夹并保留源证据\n"
            "- 在主页面的表格区域里显示扫描结果、模板字段和抽取信息\n"
            "- 提供精简的 **存在性置信** 页面，用于跨文档存在性置信检测\n"
            "- 直接填写标准化模板，并导出 AAS Model 和 Protégé 可读取的 OWL 文件\n\n"
            "## 推荐流程\n"
            "1. 打开 **Inconsistence Extract**。\n"
            "2. 选择文档文件夹，点击 **扫描工作区**。\n"
            "3. 点击 **开始抽取**，抽取记录并直接填写标准化模板。\n"
            "4. 通过视图切换查看扫描结果、模板信息或抽取信息。\n"
            "5. 点击 **保存抽取结果**，按种类导出填好的模板。\n"
            "6. 点击 **生成 AAS Model**。\n"
            "7. 点击 **导出 OWL**。\n\n"
            "## 主要页面\n"
            "- **Quick Start**：流程总览\n"
            "- **Inconsistence Extract**：扫描、抽取、检查并运行转换导出\n"
            "- **存在性置信**：查看精简的存在性置信结果和源证据\n"
            "- **Log**：查看后台步骤，方便调试和实验复现\n"
            "- **Model Settings**：切换界面语言并配置 OCR / 可选 LLM 设置\n\n"
            "## 说明\n"
            "- 抽取步骤会一次性直接填写标准化模板。\n"
            "- 后续的 AAS 和 OWL 导出保持确定性和可追溯性。\n"
            "- 同一个主页面会统一保留扫描表格、模板表格和抽取复核表格。"
        ),
        "quickstart.hero_title": "带可审计转换链的不一致抽取",
        "quickstart.hero_body": "把 OCR 或 LLM 辅助抽取放在 standardized Excel 之前，之后再基于可见源证据确定性导出 AAS 和 OWL。",
        "quickstart.badge.export": "Standardized Excel + AAS + OWL",
        "quickstart.card.scan.body": "选择文档文件夹，扫描 DEXPI、PDF、XLS/XLSX 和 IFC 输入。",
        "quickstart.card.schema.body": "点击开始抽取，抽取记录并直接填写标准化模板。",
        "quickstart.card.schema.title": "2. 抽取",
        "quickstart.card.run.body": "按种类保存填好的模板，然后导出 AAS Model 和 OWL。",
        "quickstart.card.run.title": "3. 导出",
        "common.export_all": "导出原始抽取结果",
        "busy.export.title": "正在导出原始抽取结果",
        "busy.export.body": "正在将原始抽取工作簿和各文档族输出写入所选文件夹。",
        "busy.extraction_fill.title": "正在抽取",
        "busy.extraction_fill.body": "正在扫描文档、生成模板、抽取记录并填写标准化模板。",
        "busy.save_results.title": "正在保存抽取结果",
        "busy.save_results.body": "正在将填好的模板按种类复制到导出目录。",
        "nav.extraction_review": "Inconsistence Extract",
        "page.extraction_review.title": "Inconsistence Extract",
        "page.extraction_review.subtitle": "在同一个页面中选择文档目录、扫描文件、查看模板与抽取信息，并运行转换导出。",
    }
)

TRANSLATIONS["en"].update(
    {
        "nav.t1_t5_editor": "T1-T5 Editor",
        "page.t1_t5_editor.title": "T1-T5 / Tx Rule Editor",
        "page.t1_t5_editor.subtitle": "Edit T1-T5 source-to-standardized-Excel rules and Tx standardized-Excel-to-AAS graphs from one place.",
        "nav.tx_editor": "Tx Editor",
        "page.tx_editor.title": "Tx Rule Editor",
        "page.tx_editor.subtitle": "Design deterministic UC1 transformation graphs, preview single-record execution traces, and generate AAS with configurable rules.",
        "tx.tab.t1": "T1",
        "tx.tab.t2": "T2",
        "tx.tab.t3": "T3",
        "tx.tab.t4": "T4",
        "tx.tab.t5": "T5",
        "tx.tab.tx": "Tx",
        "tx.source_type": "Source Type",
        "tx.load_default": "Load Default",
        "tx.load_workbook": "Load Workbook",
        "tx.palette": "Node Palette",
        "tx.add_node": "Add Node",
        "tx.connect_nodes": "Connect Selected",
        "tx.delete_selection": "Delete Selected",
        "tx.columns": "Workbook Columns",
        "tx.canvas": "Rule Canvas",
        "tx.inspector": "Node Inspector",
        "tx.apply_node": "Apply Node Changes",
        "tx.preview": "Preview And Trace",
        "tx.identity": "Identity",
        "tx.validate": "Validate",
        "tx.save": "Save",
        "tx.import": "Import JSON",
        "tx.export": "Export JSON",
        "tx.suggest": "Suggest Rules",
        "tx.preview_action": "Preview",
        "tx.generate_aas": "Generate AAS",
        "tx.undo": "Undo",
        "tx.redo": "Redo",
        "tx.copy": "Copy",
        "tx.paste": "Paste",
        "tx.rule_origin.saved": "Loaded saved rule set",
        "tx.rule_origin.default": "Loaded built-in rule set",
        "tx.search_nodes": "Search nodes",
        "tx.no_workbook": "No workbook selected yet.",
        "tx.no_node_selected": "Select one node to inspect or edit it.",
        "tx.targets": "Suggested Targets",
        "tx.arrange": "Auto Arrange",
        "tx.zoom_in": "Zoom In",
        "tx.zoom_out": "Zoom Out",
        "tx.reset_zoom": "Reset Zoom",
        "tx.canvas_hint": "Drag from a node output port to an input port, use right click or Tab to add nodes, and hold Shift for box selection.",
        "tx.node": "Node",
        "tx.label": "Label",
        "tx.field": "Field",
        "tx.mode": "Mode",
        "tx.property": "Property",
        "tx.submodel": "Submodel",
        "tx.value": "Value",
        "tx.separator": "Separator",
        "tx.advanced_config": "Advanced JSON",
        "tx.preview_payload": "Preview Payload",
        "tx.preview_trace": "Execution Trace",
        "tx.validation_issues": "Validation Issues",
        "tx.selection_multiple": "{count} nodes selected. Select a single node to edit it.",
        "tx.edge_selected": "Selected edge\n{source} -> {target}",
        "tx.copied_selection": "Selected nodes copied to the clipboard.",
        "tx.pasted_selection": "Copied nodes pasted into the canvas.",
        "tx.nothing_to_paste": "No copied node selection is available yet.",
        "tx.invalid_json": "Advanced JSON is invalid: {message}",
        "tx.select_two_nodes": "Select exactly two nodes before creating an edge.",
        "tx.edge_exists": "That edge already exists.",
        "tx.validation_ok": "Rule graph is valid.",
        "tx.validation_has_issues": "Validation returned issues. Review the trace panel.",
        "tx.saved_to": "Rule set saved to {path}",
        "tx.preview_with_issues": "Preview completed with validation notes.",
        "tx.suggestion_failed": "No valid suggestion was returned.",
        "tx.suggestion_fallback": "LLM suggestion fell back to the built-in rule set.",
        "tx.suggestion_applied": "Suggested rule set applied to the editor.",
        "tx.generated_count": "Generated {count} AAS files from the current rule set.",
        "tx.imported": "Rule set imported.",
        "tx.exported": "Rule set exported.",
        "tx.fixed_source_type_mismatch": "This tab only accepts {source_type} rules.",
        "busy.tx_validate.body": "Validating the current Tx rule graph.",
        "busy.tx_save.body": "Saving the current Tx rule graph.",
        "busy.tx_preview.body": "Previewing the selected identity with execution traces.",
        "busy.tx_suggest.body": "Requesting a conservative Tx rule suggestion from the configured LLM.",
        "busy.tx_generate.body": "Generating AAS files from the current Tx rule graph.",
        "t1t5.profile": "Profile",
        "t1t5.new_profile": "New Profile",
        "t1t5.duplicate_profile": "Duplicate",
        "t1t5.delete_profile": "Delete",
        "t1t5.load_builtin": "Load Built-in",
        "t1t5.load_workbook": "Load Sample Workbook",
        "t1t5.no_workbook_loaded": "No workbook selected.",
        "t1t5.profile_settings": "Profile And Template",
        "t1t5.input_mode": "Input Mode",
        "t1t5.input_mode_builtin": "Builtin context",
        "t1t5.input_mode_custom_workbook": "Custom workbook",
        "t1t5.profile_title": "Profile Title",
        "t1t5.profile_description": "Description",
        "t1t5.priority": "Priority",
        "t1t5.sheet_name": "Sheet Name",
        "t1t5.required_headers": "Required Headers",
        "t1t5.optional_headers": "Optional Headers",
        "t1t5.match_status.no_workbook": "Load a sample workbook to preview template matching.",
        "t1t5.match_status.summary": "Template match score: {score}  Sheet: {sheet}",
        "t1t5.palette": "Node Palette",
        "t1t5.search_nodes": "Search nodes",
        "t1t5.add_node": "Add Node",
        "t1t5.columns": "Available Columns",
        "t1t5.output_fields": "Standardized Output Fields",
        "t1t5.canvas": "Rule Canvas",
        "t1t5.arrange": "Auto Arrange",
        "t1t5.zoom_in": "Zoom In",
        "t1t5.zoom_out": "Zoom Out",
        "t1t5.reset_zoom": "Reset Zoom",
        "t1t5.delete_selection": "Delete Selected",
        "t1t5.canvas_hint": "Use the graph to document matching steps and map workbook columns to standardized Excel fields.",
        "t1t5.inspector": "Node Inspector",
        "t1t5.node_type": "Node Type",
        "t1t5.node_label": "Label",
        "t1t5.field": "Field",
        "t1t5.value": "Value",
        "t1t5.pattern": "Regex Pattern",
        "t1t5.separator": "Separator",
        "t1t5.compare_to": "Compare To",
        "t1t5.true_value": "True Value",
        "t1t5.false_value": "False Value",
        "t1t5.field_names": "Row Fields",
        "t1t5.mapping": "Lookup Mapping",
        "t1t5.advanced": "Advanced JSON",
        "t1t5.apply_node": "Apply Node Changes",
        "t1t5.no_node_selected": "Select one node to inspect or edit.",
        "t1t5.preview": "Preview Standardized Rows",
        "t1t5.validate": "Validate",
        "t1t5.save": "Save",
        "t1t5.import": "Import JSON",
        "t1t5.export": "Export JSON",
        "t1t5.preview_button": "Preview",
        "t1t5.preview_rows": "Preview Rows",
        "t1t5.match_title": "Template Match",
        "t1t5.issues": "Validation Issues",
        "t1t5.validation_ok": "T1-T5 rule bundle is valid.",
        "t1t5.validation_has_issues": "Validation returned issues. Review the issues panel.",
        "t1t5.saved_to": "Rule bundle saved to {path}",
        "t1t5.preview_with_issues": "Preview completed with validation notes.",
        "t1t5.imported": "Rule bundle imported.",
        "t1t5.exported": "Rule bundle exported.",
        "t1t5.rule_origin.saved": "Loaded saved rule bundle",
        "t1t5.rule_origin.default": "Loaded built-in rule bundle",
        "t1t5.minimum_one_profile": "At least one profile must remain in the bundle.",
    }
)

TRANSLATIONS["de"].update(
    {
        "nav.t1_t5_editor": "T1-T5-Editor",
        "page.t1_t5_editor.title": "T1-T5-/Tx-Regel-Editor",
        "page.t1_t5_editor.subtitle": "Bearbeiten Sie T1-T5-Regeln von Quelldateien zu standardized Excel sowie Tx-Graphen von standardized Excel zu AAS an einem Ort.",
        "nav.tx_editor": "Tx-Editor",
        "page.tx_editor.title": "Tx-Regel-Editor",
        "page.tx_editor.subtitle": "Entwerfen Sie deterministische UC1-Transformationsgraphen, prüfen Sie Einzeldatensätze mit Execution Trace und erzeugen Sie AAS mit konfigurierbaren Regeln.",
        "tx.tab.t1": "T1",
        "tx.tab.t2": "T2",
        "tx.tab.t3": "T3",
        "tx.tab.t4": "T4",
        "tx.tab.t5": "T5",
        "tx.tab.tx": "Tx",
        "tx.source_type": "Quelltyp",
        "tx.load_default": "Standard laden",
        "tx.load_workbook": "Arbeitsmappe laden",
        "tx.palette": "Knotenpalette",
        "tx.add_node": "Knoten hinzufügen",
        "tx.connect_nodes": "Auswahl verbinden",
        "tx.delete_selection": "Auswahl löschen",
        "tx.columns": "Spalten der Arbeitsmappe",
        "tx.canvas": "Regel-Canvas",
        "tx.inspector": "Knoteninspektor",
        "tx.apply_node": "Knoten übernehmen",
        "tx.preview": "Vorschau und Trace",
        "tx.identity": "Identität",
        "tx.validate": "Validieren",
        "tx.save": "Speichern",
        "tx.import": "JSON importieren",
        "tx.export": "JSON exportieren",
        "tx.suggest": "Regeln vorschlagen",
        "tx.preview_action": "Vorschau",
        "tx.generate_aas": "AAS erzeugen",
        "tx.undo": "Rückgängig",
        "tx.redo": "Wiederholen",
        "tx.copy": "Kopieren",
        "tx.paste": "Einfügen",
        "tx.rule_origin.saved": "Gespeicherter Regelsatz geladen",
        "tx.rule_origin.default": "Eingebauter Regelsatz geladen",
        "tx.search_nodes": "Knoten suchen",
        "tx.no_workbook": "Noch keine Arbeitsmappe ausgewählt.",
        "tx.no_node_selected": "Wählen Sie einen Knoten zum Anzeigen oder Bearbeiten aus.",
        "tx.targets": "Zielvorschläge",
        "tx.arrange": "Automatisch anordnen",
        "tx.zoom_in": "Vergrößern",
        "tx.zoom_out": "Verkleinern",
        "tx.reset_zoom": "Zoom zurücksetzen",
        "tx.canvas_hint": "Ziehen Sie von einem Ausgangsport zu einem Eingangsport, fügen Sie Knoten per Rechtsklick oder Tab ein und halten Sie Shift für die Mehrfachauswahl.",
        "tx.node": "Knoten",
        "tx.label": "Bezeichnung",
        "tx.field": "Feld",
        "tx.mode": "Modus",
        "tx.property": "Property",
        "tx.submodel": "Submodell",
        "tx.value": "Wert",
        "tx.separator": "Trennzeichen",
        "tx.advanced_config": "Erweitertes JSON",
        "tx.preview_payload": "Vorschau-Payload",
        "tx.preview_trace": "Execution Trace",
        "tx.validation_issues": "Validierungshinweise",
        "tx.selection_multiple": "{count} Knoten ausgewählt. Bitte einen einzelnen Knoten zum Bearbeiten wählen.",
        "tx.edge_selected": "Ausgewählte Kante\n{source} -> {target}",
        "tx.copied_selection": "Die ausgewählten Knoten wurden in die Zwischenablage kopiert.",
        "tx.pasted_selection": "Die kopierten Knoten wurden in den Canvas eingefügt.",
        "tx.nothing_to_paste": "Es gibt noch keine kopierte Knotenauswahl.",
        "tx.invalid_json": "Das erweiterte JSON ist ungültig: {message}",
        "tx.select_two_nodes": "Wählen Sie genau zwei Knoten aus, bevor Sie eine Kante erzeugen.",
        "tx.edge_exists": "Diese Kante existiert bereits.",
        "tx.validation_ok": "Der Regelgraph ist gültig.",
        "tx.validation_has_issues": "Die Validierung hat Hinweise geliefert. Bitte den Trace-Bereich prüfen.",
        "tx.saved_to": "Regelsatz gespeichert unter {path}",
        "tx.preview_with_issues": "Die Vorschau wurde mit Hinweisen abgeschlossen.",
        "tx.suggestion_failed": "Es wurde kein gültiger Vorschlag zurückgegeben.",
        "tx.suggestion_fallback": "Der LLM-Vorschlag ist auf den eingebauten Regelsatz zurückgefallen.",
        "tx.suggestion_applied": "Der vorgeschlagene Regelsatz wurde übernommen.",
        "tx.generated_count": "{count} AAS-Dateien wurden aus dem aktuellen Regelsatz erzeugt.",
        "tx.imported": "Regelsatz importiert.",
        "tx.exported": "Regelsatz exportiert.",
        "tx.fixed_source_type_mismatch": "Dieser Tab akzeptiert nur Regeln für {source_type}.",
        "busy.tx_validate.body": "Der aktuelle Tx-Regelgraph wird validiert.",
        "busy.tx_save.body": "Der aktuelle Tx-Regelgraph wird gespeichert.",
        "busy.tx_preview.body": "Die ausgewählte Identität wird mit Execution Trace angezeigt.",
        "busy.tx_suggest.body": "Eine konservative Tx-Regel-Empfehlung wird vom konfigurierten LLM angefordert.",
        "busy.tx_generate.body": "AAS-Dateien werden aus dem aktuellen Tx-Regelgraph erzeugt.",
        "t1t5.profile": "Profil",
        "t1t5.new_profile": "Neues Profil",
        "t1t5.duplicate_profile": "Duplizieren",
        "t1t5.delete_profile": "Löschen",
        "t1t5.load_builtin": "Eingebaut laden",
        "t1t5.load_workbook": "Beispiel-Arbeitsmappe laden",
        "t1t5.no_workbook_loaded": "Keine Arbeitsmappe ausgewählt.",
        "t1t5.profile_settings": "Profil und Vorlage",
        "t1t5.input_mode": "Eingabemodus",
        "t1t5.input_mode_builtin": "Eingebaute Quelle",
        "t1t5.input_mode_custom_workbook": "Benutzerdefinierte Arbeitsmappe",
        "t1t5.profile_title": "Profiltitel",
        "t1t5.profile_description": "Beschreibung",
        "t1t5.priority": "Priorität",
        "t1t5.sheet_name": "Tabellenblatt",
        "t1t5.required_headers": "Pflicht-Header",
        "t1t5.optional_headers": "Optionale Header",
        "t1t5.match_status.no_workbook": "Laden Sie eine Beispiel-Arbeitsmappe, um das Template-Matching zu prüfen.",
        "t1t5.match_status.summary": "Vorlagen-Score: {score}  Blatt: {sheet}",
        "t1t5.palette": "Knotenpalette",
        "t1t5.search_nodes": "Knoten suchen",
        "t1t5.add_node": "Knoten hinzufügen",
        "t1t5.columns": "Verfügbare Spalten",
        "t1t5.output_fields": "Standardisierte Ausgabefelder",
        "t1t5.canvas": "Regel-Canvas",
        "t1t5.arrange": "Automatisch anordnen",
        "t1t5.zoom_in": "Vergrößern",
        "t1t5.zoom_out": "Verkleinern",
        "t1t5.reset_zoom": "Zoom zurücksetzen",
        "t1t5.delete_selection": "Auswahl löschen",
        "t1t5.canvas_hint": "Dokumentieren Sie Matching-Schritte im Graphen und mappen Sie Arbeitsmappen-Spalten auf standardisierte Excel-Felder.",
        "t1t5.inspector": "Knoteninspektor",
        "t1t5.node_type": "Knotentyp",
        "t1t5.node_label": "Bezeichnung",
        "t1t5.field": "Feld",
        "t1t5.value": "Wert",
        "t1t5.pattern": "Regex-Muster",
        "t1t5.separator": "Trennzeichen",
        "t1t5.compare_to": "Vergleichen mit",
        "t1t5.true_value": "Wert bei True",
        "t1t5.false_value": "Wert bei False",
        "t1t5.field_names": "Zeilenfelder",
        "t1t5.mapping": "Lookup-Mapping",
        "t1t5.advanced": "Erweitertes JSON",
        "t1t5.apply_node": "Knoten übernehmen",
        "t1t5.no_node_selected": "Wählen Sie einen Knoten zum Anzeigen oder Bearbeiten aus.",
        "t1t5.preview": "Standardisierte Zeilen prüfen",
        "t1t5.validate": "Validieren",
        "t1t5.save": "Speichern",
        "t1t5.import": "JSON importieren",
        "t1t5.export": "JSON exportieren",
        "t1t5.preview_button": "Vorschau",
        "t1t5.preview_rows": "Vorschau-Zeilen",
        "t1t5.match_title": "Vorlagen-Matching",
        "t1t5.issues": "Validierungshinweise",
        "t1t5.validation_ok": "Das T1-T5-Regelpaket ist gültig.",
        "t1t5.validation_has_issues": "Die Validierung hat Hinweise geliefert. Bitte den Hinweisbereich prüfen.",
        "t1t5.saved_to": "Regelpaket gespeichert unter {path}",
        "t1t5.preview_with_issues": "Die Vorschau wurde mit Hinweisen abgeschlossen.",
        "t1t5.imported": "Regelpaket importiert.",
        "t1t5.exported": "Regelpaket exportiert.",
        "t1t5.rule_origin.saved": "Gespeichertes Regelpaket geladen",
        "t1t5.rule_origin.default": "Eingebautes Regelpaket geladen",
        "t1t5.minimum_one_profile": "Mindestens ein Profil muss im Paket verbleiben.",
    }
)

TRANSLATIONS["zh"].update(
    {
        "nav.t1_t5_editor": "T1-T5 编辑器",
        "page.t1_t5_editor.title": "T1-T5 / Tx 规则编辑器",
        "page.t1_t5_editor.subtitle": "在同一个页面里编辑 T1-T5 从源文件到 standardized Excel 的规则，以及 Tx 从 standardized Excel 到 AAS 的规则图。",
        "nav.tx_editor": "Tx 编辑器",
        "page.tx_editor.title": "Tx 规则编辑器",
        "page.tx_editor.subtitle": "以确定性方式设计 UC1 转换图，预览单条记录的执行 trace，并用可配置规则生成 AAS。",
        "tx.tab.t1": "T1",
        "tx.tab.t2": "T2",
        "tx.tab.t3": "T3",
        "tx.tab.t4": "T4",
        "tx.tab.t5": "T5",
        "tx.tab.tx": "Tx",
        "tx.source_type": "源类型",
        "tx.load_default": "加载默认规则",
        "tx.load_workbook": "加载工作簿",
        "tx.palette": "节点面板",
        "tx.add_node": "添加节点",
        "tx.connect_nodes": "连接所选节点",
        "tx.delete_selection": "删除所选",
        "tx.columns": "工作簿列",
        "tx.canvas": "规则画布",
        "tx.inspector": "节点属性",
        "tx.apply_node": "应用节点修改",
        "tx.preview": "预览与 Trace",
        "tx.identity": "实体标识",
        "tx.validate": "校验",
        "tx.save": "保存",
        "tx.import": "导入 JSON",
        "tx.export": "导出 JSON",
        "tx.suggest": "建议规则",
        "tx.preview_action": "预览",
        "tx.generate_aas": "生成 AAS",
        "tx.undo": "撤销",
        "tx.redo": "重做",
        "tx.copy": "复制",
        "tx.paste": "粘贴",
        "tx.rule_origin.saved": "已加载已保存规则",
        "tx.rule_origin.default": "已加载内置规则",
        "tx.search_nodes": "搜索节点",
        "tx.no_workbook": "尚未选择工作簿。",
        "tx.no_node_selected": "请选择一个节点后再查看或编辑。",
        "tx.targets": "目标建议",
        "tx.arrange": "自动布局",
        "tx.zoom_in": "放大",
        "tx.zoom_out": "缩小",
        "tx.reset_zoom": "重置缩放",
        "tx.canvas_hint": "从节点输出端口拖到输入端口即可连线；右键或按 Tab 可插入节点；按住 Shift 可框选。",
        "tx.node": "节点",
        "tx.label": "标签",
        "tx.field": "字段",
        "tx.mode": "模式",
        "tx.property": "属性",
        "tx.submodel": "子模型",
        "tx.value": "值",
        "tx.separator": "分隔符",
        "tx.advanced_config": "高级 JSON",
        "tx.preview_payload": "预览载荷",
        "tx.preview_trace": "执行 Trace",
        "tx.validation_issues": "校验问题",
        "tx.selection_multiple": "已选择 {count} 个节点。请选择单个节点后再编辑。",
        "tx.edge_selected": "当前选中连线\n{source} -> {target}",
        "tx.copied_selection": "已将所选节点复制到剪贴板。",
        "tx.pasted_selection": "已将复制的节点粘贴到画布。",
        "tx.nothing_to_paste": "当前还没有可粘贴的节点选择。",
        "tx.invalid_json": "高级 JSON 无效：{message}",
        "tx.select_two_nodes": "请先精确选择两个节点，再创建连线。",
        "tx.edge_exists": "这条连线已经存在。",
        "tx.validation_ok": "规则图校验通过。",
        "tx.validation_has_issues": "校验返回了问题，请查看下方 trace 面板。",
        "tx.saved_to": "规则已保存到 {path}",
        "tx.preview_with_issues": "预览已完成，但带有校验提示。",
        "tx.suggestion_failed": "没有返回有效的建议规则。",
        "tx.suggestion_fallback": "LLM 建议失败，已回退到内置默认规则。",
        "tx.suggestion_applied": "建议规则已应用到编辑器。",
        "tx.generated_count": "已根据当前规则生成 {count} 个 AAS 文件。",
        "tx.imported": "规则已导入。",
        "tx.exported": "规则已导出。",
        "tx.fixed_source_type_mismatch": "这个标签页只接受 {source_type} 规则。",
        "busy.tx_validate.body": "正在校验当前 Tx 规则图。",
        "busy.tx_save.body": "正在保存当前 Tx 规则图。",
        "busy.tx_preview.body": "正在预览所选实体并生成执行 trace。",
        "busy.tx_suggest.body": "正在向已配置的 LLM 请求保守的 Tx 规则建议。",
        "busy.tx_generate.body": "正在根据当前 Tx 规则图生成 AAS 文件。",
        "t1t5.profile": "规则模板",
        "t1t5.new_profile": "新建模板",
        "t1t5.duplicate_profile": "复制",
        "t1t5.delete_profile": "删除",
        "t1t5.load_builtin": "加载内置规则",
        "t1t5.load_workbook": "加载示例工作簿",
        "t1t5.no_workbook_loaded": "尚未选择工作簿。",
        "t1t5.profile_settings": "模板与匹配设置",
        "t1t5.input_mode": "输入模式",
        "t1t5.input_mode_builtin": "内置上下文",
        "t1t5.input_mode_custom_workbook": "自定义工作簿",
        "t1t5.profile_title": "模板标题",
        "t1t5.profile_description": "说明",
        "t1t5.priority": "优先级",
        "t1t5.sheet_name": "工作表名称",
        "t1t5.required_headers": "必需表头",
        "t1t5.optional_headers": "可选表头",
        "t1t5.match_status.no_workbook": "加载一个示例工作簿后可预览模板匹配结果。",
        "t1t5.match_status.summary": "模板匹配分数：{score}  工作表：{sheet}",
        "t1t5.palette": "节点面板",
        "t1t5.search_nodes": "搜索节点",
        "t1t5.add_node": "添加节点",
        "t1t5.columns": "可用列",
        "t1t5.output_fields": "标准化输出字段",
        "t1t5.canvas": "规则画布",
        "t1t5.arrange": "自动布局",
        "t1t5.zoom_in": "放大",
        "t1t5.zoom_out": "缩小",
        "t1t5.reset_zoom": "重置缩放",
        "t1t5.delete_selection": "删除所选",
        "t1t5.canvas_hint": "用图形节点表达匹配步骤，并把工作簿列映射到 standardized Excel 字段。",
        "t1t5.inspector": "节点属性",
        "t1t5.node_type": "节点类型",
        "t1t5.node_label": "标签",
        "t1t5.field": "字段",
        "t1t5.value": "值",
        "t1t5.pattern": "正则表达式",
        "t1t5.separator": "分隔符",
        "t1t5.compare_to": "比较值",
        "t1t5.true_value": "为真时输出",
        "t1t5.false_value": "为假时输出",
        "t1t5.field_names": "行字段",
        "t1t5.mapping": "映射表",
        "t1t5.advanced": "高级 JSON",
        "t1t5.apply_node": "应用节点修改",
        "t1t5.no_node_selected": "请选择一个节点后再查看或编辑。",
        "t1t5.preview": "预览标准化行",
        "t1t5.validate": "校验",
        "t1t5.save": "保存",
        "t1t5.import": "导入 JSON",
        "t1t5.export": "导出 JSON",
        "t1t5.preview_button": "预览",
        "t1t5.preview_rows": "预览结果",
        "t1t5.match_title": "模板匹配",
        "t1t5.issues": "校验问题",
        "t1t5.validation_ok": "T1-T5 规则包校验通过。",
        "t1t5.validation_has_issues": "校验返回了问题，请查看问题面板。",
        "t1t5.saved_to": "规则包已保存到 {path}",
        "t1t5.preview_with_issues": "预览已完成，但带有校验提示。",
        "t1t5.imported": "规则包已导入。",
        "t1t5.exported": "规则包已导出。",
        "t1t5.rule_origin.saved": "已加载已保存规则包",
        "t1t5.rule_origin.default": "已加载内置规则包",
        "t1t5.minimum_one_profile": "规则包中至少需要保留一个模板。",
    }
)

TRANSLATIONS["en"].update(
    {
        "tooltip.node.fallback.title": "Rule Node",
        "tooltip.node.fallback.generic": "This node participates in the rule flow and passes data between steps.",
        "tooltip.node.fallback.usage": "Connect it to the previous and next step, then review the advanced settings if you need custom behavior.",
        "tooltip.section.what": "What it does: {text}",
        "tooltip.section.current": "Current configuration: {text}",
        "tooltip.section.how": "How to use it: {text}",
        "tooltip.summary.none": "No extra configuration yet.",
        "tooltip.summary.field": "Field: {value}",
        "tooltip.summary.mode": "Mode: {value}",
        "tooltip.summary.separator": "Separator: {value}",
        "tooltip.summary.constant": "Constant: {value}",
        "tooltip.summary.pattern": "Pattern: {value}",
        "tooltip.summary.group": "Group: {value}",
        "tooltip.summary.default_value": "Default: {value}",
        "tooltip.summary.connected_inputs": "Connected inputs: {count}",
        "tooltip.summary.target_fields": "Target fields: {count}",
        "tooltip.summary.flow_inputs": "Flow inputs: {count}",
        "tooltip.summary.sheet": "Sheet: {value}",
        "tooltip.summary.mapping_entries": "Mapping entries: {count}",
        "tooltip.summary.true_value": "True value: {value}",
        "tooltip.summary.false_value": "False value: {value}",
        "tooltip.summary.operator": "Operator: {value}",
        "tooltip.summary.compare_to": "Compare to: {value}",
        "tooltip.summary.min_confidence": "Min confidence: {value}",
        "tooltip.summary.fallback": "Fallback: {value}",
        "tooltip.summary.required_headers": "Required headers: {count}",
        "tooltip.summary.optional_headers": "Optional headers: {count}",
        "tooltip.summary.property": "Property: {value}",
        "tooltip.summary.submodel": "Submodel: {value}",
        "tooltip.summary.value": "Value: {value}",
        "tooltip.inspector.none": "Select a node to see contextual help.",
        "tooltip.control.node_type": "This shows the selected node type. Different node types expose different ports and configuration fields.",
        "tooltip.control.node_summary": "This shows the selected node id and node type so you can confirm which graph element you are editing.",
        "tooltip.control.label": "Use the label to give this node a business-friendly name without changing its technical type.",
        "tooltip.control.field": "Set the source field or workbook column this node should read.",
        "tooltip.control.mode": "Choose how multiple workbook cells from the same column should be combined before the value leaves the node.",
        "tooltip.control.property": "Select the target AAS property that this node should populate.",
        "tooltip.control.submodel": "Select the target AAS submodel that should receive the connected properties.",
        "tooltip.control.value": "Enter the literal value this node should emit or use as a default output.",
        "tooltip.control.pattern": "Enter the regular expression used to extract part of the incoming text.",
        "tooltip.control.separator": "Set the separator inserted between joined input values.",
        "tooltip.control.compare_to": "Enter the comparison value used by the condition node.",
        "tooltip.control.true_value": "Set the output emitted when the condition evaluates to true.",
        "tooltip.control.false_value": "Set the output emitted when the condition evaluates to false.",
        "tooltip.control.sheet_name": "Set the workbook sheet name that this profile or node should match.",
        "tooltip.control.field_names": "List the standardized output fields that BuildRow should expose as input ports.",
        "tooltip.control.mapping": "Edit the key-value mapping table used to translate source values.",
        "tooltip.control.advanced": "Use advanced JSON for additional node settings that are not exposed as dedicated form fields.",
        "tooltip.control.apply": "Apply the edited form values back to the selected node.",
        "tooltip.node.BuiltinContext.generic": "Reads records from the built-in Inconsistence Extract context instead of from a custom workbook.",
        "tooltip.node.WorkbookSheet.generic": "Selects the workbook sheet that subsequent workbook nodes should inspect.",
        "tooltip.node.HeaderMatch.generic": "Checks whether the current sheet matches the expected header pattern.",
        "tooltip.node.RowIterator.generic": "Iterates through matched sheet rows so downstream nodes can read cell values.",
        "tooltip.node.CellValue.generic": "Reads one field from the current standardized row or workbook row.",
        "tooltip.node.Constant.generic": "Emits a fixed literal value.",
        "tooltip.node.NormalizeIdentifier.generic": "Normalizes raw tags or IDs into a more stable identifier format.",
        "tooltip.node.RegexExtract.generic": "Extracts part of an input value with a regular expression.",
        "tooltip.node.Concat.generic": "Joins multiple input values into one string.",
        "tooltip.node.Condition.generic": "Compares input values and emits the true or false branch result.",
        "tooltip.node.LookupMap.generic": "Translates input values through a lookup table.",
        "tooltip.node.StrictMatch.generic": "Represents a strict matching step between sources.",
        "tooltip.node.ResolverMatch.generic": "Represents a resolver-based matching step when direct matching is not enough.",
        "tooltip.node.MissingPlaceholder.generic": "Creates placeholder output for missing or unresolved data.",
        "tooltip.node.CompletionMerge.generic": "Merges completion or review information back into the row.",
        "tooltip.node.RelationBuild.generic": "Builds a relation row or relation payload from upstream matches.",
        "tooltip.node.BuildRow.generic": "Collects incoming values into one standardized output row.",
        "tooltip.node.OutputSheet.generic": "Writes the assembled row to a target standardized sheet.",
        "tooltip.node.InputColumn.generic": "Reads one column from the source workbook.",
        "tooltip.node.MapEnum.generic": "Maps one source value to a controlled vocabulary or enumeration.",
        "tooltip.node.BoolMap.generic": "Converts an input value into a true/false style output.",
        "tooltip.node.PreferFirstNonEmpty.generic": "Selects the first populated input and falls back when needed.",
        "tooltip.node.ConfidenceGate.generic": "Lets a value pass only when its confidence meets a threshold.",
        "tooltip.node.OutputProperty.generic": "Writes one mapped value into an AAS property.",
        "tooltip.node.OutputSubmodel.generic": "Groups multiple properties into one AAS submodel output.",
        "tooltip.node.BuiltinContext.usage": "Use this when the stage should start from the built-in Inconsistence Extract context instead of a custom Excel template.",
        "tooltip.node.WorkbookSheet.usage": "Point this to the sheet name you want to inspect, then continue with header and row logic.",
        "tooltip.node.HeaderMatch.usage": "Fill in required and optional headers to describe the workbook template you expect.",
        "tooltip.node.RowIterator.usage": "Connect it after sheet or header detection so each row can be mapped field by field.",
        "tooltip.node.CellValue.usage": "Set the source field name, then connect its output to a transform or directly to BuildRow.",
        "tooltip.node.Constant.usage": "Use this for fixed fallback text, status values, or columns that should always receive the same content.",
        "tooltip.node.NormalizeIdentifier.usage": "Place it before matching or output nodes when tags need normalization first.",
        "tooltip.node.RegexExtract.usage": "Provide a pattern and capturing group when only part of the input string should be forwarded.",
        "tooltip.node.Concat.usage": "Connect several inputs in the order you want them joined, and adjust the separator as needed.",
        "tooltip.node.Condition.usage": "Feed the subject and comparison inputs, then set the compare value and branch outputs.",
        "tooltip.node.LookupMap.usage": "Populate the mapping table when source values need to be renamed or standardized.",
        "tooltip.node.StrictMatch.usage": "Keep this in the flow when exact cross-document matching is part of the business step.",
        "tooltip.node.ResolverMatch.usage": "Use this when a heuristic or resolver step should enrich direct matching.",
        "tooltip.node.MissingPlaceholder.usage": "Connect it when the pipeline should surface missing counterparts explicitly.",
        "tooltip.node.CompletionMerge.usage": "Place it after matching so review status or completion suggestions can be merged back in.",
        "tooltip.node.RelationBuild.usage": "Use it when the stage should emit relationship-style rows from multiple matched values.",
        "tooltip.node.BuildRow.usage": "Connect one source per target port to control which value lands in each standardized column.",
        "tooltip.node.OutputSheet.usage": "Keep one final output sheet node per branch that should emit standardized rows.",
        "tooltip.node.InputColumn.usage": "Choose the workbook column and reading mode, then connect it into transforms or outputs.",
        "tooltip.node.MapEnum.usage": "Edit the mapping table so workbook values land on the expected target vocabulary.",
        "tooltip.node.BoolMap.usage": "Configure the true or false outputs when downstream logic expects normalized boolean text.",
        "tooltip.node.PreferFirstNonEmpty.usage": "Connect candidates in priority order to keep the first filled value.",
        "tooltip.node.ConfidenceGate.usage": "Feed both the value and its confidence, then set the threshold and fallback.",
        "tooltip.node.OutputProperty.usage": "Map a prepared value into the exact AAS property you want to populate.",
        "tooltip.node.OutputSubmodel.usage": "Connect property nodes that should be grouped under the same submodel.",
        "tooltip.t1t5.t1.BuiltinContext.detail": "In the built-in T1 flow this stands for the parsed R&I source context that the legacy pipeline assembled before graph execution.",
        "tooltip.t1t5.t1.StrictMatch.detail": "In T1 this marks the strict cross-document match phase that checks whether an R&I device also appears in supporting sources.",
        "tooltip.t1t5.t1.CompletionMerge.detail": "In T1 this marks the completion merge phase where review flags, missing targets, and recommended actions are merged into the row.",
        "tooltip.t1t5.default.WorkbookSheet.detail": "Use this in custom workbook profiles to anchor the flow to one sheet before header checks and row iteration.",
        "tooltip.t1t5.default.HeaderMatch.detail": "This node describes the signature used to auto-detect whether a workbook profile matches the sample Excel template.",
        "tooltip.t1t5.default.RowIterator.detail": "After a sheet matches, this node represents the per-row loop that downstream CellValue nodes read from.",
        "tooltip.t1t5.default.BuildRow.detail": "This is the step that turns matched values into one standardized Excel row for the current T-stage.",
        "tooltip.t1t5.default.OutputSheet.detail": "This is the final export step for the stage. The connected BuildRow output becomes one row in the chosen standardized sheet.",
        "tooltip.t1t5.label.ri_source_context.detail": "This label marks the built-in R&I source context that seeds the default T1 flow.",
        "tooltip.t1t5.label.cross_document_strict_match.detail": "This label marks the strict cross-document comparison step in the default T1 flow.",
        "tooltip.t1t5.label.completion_candidate_merge.detail": "This label marks the completion merge step that adds review-oriented fields back into the row.",
        "tooltip.tx.default.InputColumn.detail": "In Tx this is the bridge from standardized Excel into the rule graph. It reads the source workbook column before any transformation.",
        "tooltip.tx.default.NormalizeIdentifier.detail": "In Tx this is typically used before matching to AAS identifiers or when a workbook tag needs cleanup.",
        "tooltip.tx.default.RegexExtract.detail": "In Tx this is often used to strip prefixes, units, or bracketed text before mapping into AAS properties.",
        "tooltip.tx.default.MapEnum.detail": "Use this when workbook codes need to be translated into the controlled values expected by the target AAS model.",
        "tooltip.tx.default.BoolMap.detail": "Useful when the workbook stores flags like X, Yes, or 1 and the target model expects normalized boolean text.",
        "tooltip.tx.default.Concat.detail": "This is helpful when one AAS property should be assembled from several workbook columns.",
        "tooltip.tx.default.PreferFirstNonEmpty.detail": "Use this when several candidate columns can supply the same AAS property and you want the first populated one.",
        "tooltip.tx.default.Condition.detail": "This lets one branch of Tx output depend on workbook content, for example to derive status text or fallback labels.",
        "tooltip.tx.default.ConfidenceGate.detail": "Use this when a value should only flow into the AAS output if the confidence column is high enough.",
        "tooltip.tx.default.OutputProperty.detail": "This is the final property mapping step. Each connected value becomes one property inside the target submodel.",
        "tooltip.tx.default.OutputSubmodel.detail": "This groups several OutputProperty nodes so the generated AAS payload is organized by submodel.",
    }
)

TRANSLATIONS["de"].update(
    {
        "tooltip.node.fallback.title": "Regelknoten",
        "tooltip.node.fallback.generic": "Dieser Knoten ist Teil des Regelflusses und gibt Daten zwischen Schritten weiter.",
        "tooltip.node.fallback.usage": "Verbinden Sie ihn mit dem vorherigen und nächsten Schritt und prüfen Sie bei Bedarf die erweiterten Einstellungen.",
        "tooltip.section.what": "Funktion: {text}",
        "tooltip.section.current": "Aktuelle Konfiguration: {text}",
        "tooltip.section.how": "Verwendung: {text}",
        "tooltip.summary.none": "Noch keine zusätzliche Konfiguration.",
        "tooltip.summary.field": "Feld: {value}",
        "tooltip.summary.mode": "Modus: {value}",
        "tooltip.summary.separator": "Trennzeichen: {value}",
        "tooltip.summary.constant": "Konstante: {value}",
        "tooltip.summary.pattern": "Muster: {value}",
        "tooltip.summary.group": "Gruppe: {value}",
        "tooltip.summary.default_value": "Standardwert: {value}",
        "tooltip.summary.connected_inputs": "Verbundene Eingänge: {count}",
        "tooltip.summary.target_fields": "Zielfelder: {count}",
        "tooltip.summary.flow_inputs": "Flow-Eingänge: {count}",
        "tooltip.summary.sheet": "Tabellenblatt: {value}",
        "tooltip.summary.mapping_entries": "Mapping-Einträge: {count}",
        "tooltip.summary.true_value": "Wert bei True: {value}",
        "tooltip.summary.false_value": "Wert bei False: {value}",
        "tooltip.summary.operator": "Operator: {value}",
        "tooltip.summary.compare_to": "Vergleichen mit: {value}",
        "tooltip.summary.min_confidence": "Minimale Konfidenz: {value}",
        "tooltip.summary.fallback": "Fallback: {value}",
        "tooltip.summary.required_headers": "Pflicht-Header: {count}",
        "tooltip.summary.optional_headers": "Optionale Header: {count}",
        "tooltip.summary.property": "Property: {value}",
        "tooltip.summary.submodel": "Submodell: {value}",
        "tooltip.summary.value": "Wert: {value}",
        "tooltip.inspector.none": "Wählen Sie einen Knoten aus, um kontextbezogene Hilfe zu sehen.",
        "tooltip.control.node_type": "Hier wird der ausgewählte Knotentyp angezeigt. Unterschiedliche Typen haben unterschiedliche Ports und Konfigurationsfelder.",
        "tooltip.control.node_summary": "Hier sehen Sie die ausgewählte Knoten-ID und den Typ, damit klar ist, welches Graph-Element gerade bearbeitet wird.",
        "tooltip.control.label": "Mit der Bezeichnung geben Sie dem Knoten einen fachlichen Namen, ohne den technischen Typ zu ändern.",
        "tooltip.control.field": "Legen Sie fest, welches Quellfeld oder welche Arbeitsmappen-Spalte der Knoten lesen soll.",
        "tooltip.control.mode": "Wählen Sie, wie mehrere Zellen derselben Spalte kombiniert werden, bevor der Wert den Knoten verlässt.",
        "tooltip.control.property": "Wählen Sie die Ziel-Property im AAS-Modell, die dieser Knoten füllen soll.",
        "tooltip.control.submodel": "Wählen Sie das Ziel-Submodell, das die verbundenen Properties aufnehmen soll.",
        "tooltip.control.value": "Geben Sie den Literalwert ein, den dieser Knoten ausgeben oder als Standard verwenden soll.",
        "tooltip.control.pattern": "Geben Sie den regulären Ausdruck ein, mit dem ein Teil des Eingangstextes extrahiert wird.",
        "tooltip.control.separator": "Legen Sie das Trennzeichen fest, das zwischen zusammengefügten Eingaben eingefügt wird.",
        "tooltip.control.compare_to": "Geben Sie den Vergleichswert für den Condition-Knoten ein.",
        "tooltip.control.true_value": "Legen Sie fest, was bei einem True-Ergebnis ausgegeben werden soll.",
        "tooltip.control.false_value": "Legen Sie fest, was bei einem False-Ergebnis ausgegeben werden soll.",
        "tooltip.control.sheet_name": "Legen Sie den Namen des Tabellenblatts fest, das dieses Profil oder dieser Knoten erkennen soll.",
        "tooltip.control.field_names": "Listen Sie die standardisierten Ausgabefelder auf, die BuildRow als Eingangsports bereitstellen soll.",
        "tooltip.control.mapping": "Bearbeiten Sie die Key-Value-Mapping-Tabelle zur Übersetzung von Quellwerten.",
        "tooltip.control.advanced": "Verwenden Sie das erweiterte JSON für zusätzliche Knoteneinstellungen, die nicht als eigene Formularfelder vorhanden sind.",
        "tooltip.control.apply": "Übernimmt die bearbeiteten Formularwerte in den ausgewählten Knoten.",
        "tooltip.node.BuiltinContext.generic": "Liest Datensätze aus dem eingebauten Inconsistence-Extract-Kontext statt aus einer benutzerdefinierten Arbeitsmappe.",
        "tooltip.node.WorkbookSheet.generic": "Wählt das Tabellenblatt aus, das nachfolgende Arbeitsmappen-Knoten prüfen sollen.",
        "tooltip.node.HeaderMatch.generic": "Prüft, ob das aktuelle Blatt zum erwarteten Header-Muster passt.",
        "tooltip.node.RowIterator.generic": "Iteriert über passende Blattzeilen, damit nachfolgende Knoten Zellwerte lesen können.",
        "tooltip.node.CellValue.generic": "Liest ein Feld aus der aktuellen standardisierten Zeile oder Arbeitsmappen-Zeile.",
        "tooltip.node.Constant.generic": "Gibt einen festen Literalwert aus.",
        "tooltip.node.NormalizeIdentifier.generic": "Normalisiert rohe Tags oder IDs in ein stabileres Identifikationsformat.",
        "tooltip.node.RegexExtract.generic": "Extrahiert mit einem regulären Ausdruck einen Teil eines Eingangswerts.",
        "tooltip.node.Concat.generic": "Verbindet mehrere Eingangswerte zu einem String.",
        "tooltip.node.Condition.generic": "Vergleicht Eingangswerte und gibt das Ergebnis für den True- oder False-Zweig aus.",
        "tooltip.node.LookupMap.generic": "Übersetzt Eingangswerte über eine Lookup-Tabelle.",
        "tooltip.node.StrictMatch.generic": "Repräsentiert einen strikten Matching-Schritt zwischen Quellen.",
        "tooltip.node.ResolverMatch.generic": "Repräsentiert einen resolverbasierten Matching-Schritt, wenn direktes Matching nicht ausreicht.",
        "tooltip.node.MissingPlaceholder.generic": "Erzeugt Platzhalter für fehlende oder nicht aufgelöste Daten.",
        "tooltip.node.CompletionMerge.generic": "Führt Completion- oder Review-Informationen wieder in die Zeile zurück.",
        "tooltip.node.RelationBuild.generic": "Erzeugt aus vorgeschalteten Treffern eine Beziehungszeile oder ein Beziehungs-Payload.",
        "tooltip.node.BuildRow.generic": "Sammelt eingehende Werte zu einer standardisierten Ausgabezeile.",
        "tooltip.node.OutputSheet.generic": "Schreibt die aufgebaute Zeile in ein standardisiertes Zielblatt.",
        "tooltip.node.InputColumn.generic": "Liest eine Spalte aus der Quell-Arbeitsmappe.",
        "tooltip.node.MapEnum.generic": "Mappt einen Quellwert auf ein kontrolliertes Vokabular oder eine Enumeration.",
        "tooltip.node.BoolMap.generic": "Konvertiert einen Eingangswert in eine True/False-artige Ausgabe.",
        "tooltip.node.PreferFirstNonEmpty.generic": "Wählt den ersten befüllten Eingang und fällt bei Bedarf zurück.",
        "tooltip.node.ConfidenceGate.generic": "Lässt einen Wert nur passieren, wenn seine Konfidenz den Schwellenwert erreicht.",
        "tooltip.node.OutputProperty.generic": "Schreibt einen gemappten Wert in eine AAS-Property.",
        "tooltip.node.OutputSubmodel.generic": "Gruppiert mehrere Properties zu einer AAS-Submodell-Ausgabe.",
        "tooltip.node.BuiltinContext.usage": "Verwenden Sie dies, wenn die Stufe vom eingebauten Inconsistence-Extract-Kontext statt von einer benutzerdefinierten Excel-Vorlage starten soll.",
        "tooltip.node.WorkbookSheet.usage": "Tragen Sie hier den Blattnamen ein, den Sie prüfen möchten, und fahren Sie dann mit Header- und Zeilenlogik fort.",
        "tooltip.node.HeaderMatch.usage": "Füllen Sie Pflicht- und optionale Header aus, um die erwartete Arbeitsmappen-Vorlage zu beschreiben.",
        "tooltip.node.RowIterator.usage": "Verbinden Sie ihn nach der Blatt- oder Header-Erkennung, damit jede Zeile feldweise gemappt werden kann.",
        "tooltip.node.CellValue.usage": "Legen Sie den Quellfeldnamen fest und verbinden Sie den Ausgang mit einer Transformation oder direkt mit BuildRow.",
        "tooltip.node.Constant.usage": "Verwenden Sie dies für feste Fallback-Texte, Statuswerte oder Spalten, die immer denselben Inhalt erhalten sollen.",
        "tooltip.node.NormalizeIdentifier.usage": "Platzieren Sie ihn vor Matching- oder Ausgabeknoten, wenn Tags zuerst normalisiert werden müssen.",
        "tooltip.node.RegexExtract.usage": "Geben Sie Muster und Gruppe an, wenn nur ein Teil des Eingabestrings weitergereicht werden soll.",
        "tooltip.node.Concat.usage": "Verbinden Sie mehrere Eingänge in der gewünschten Reihenfolge und passen Sie bei Bedarf das Trennzeichen an.",
        "tooltip.node.Condition.usage": "Führen Sie Subjekt- und Vergleichseingänge zu und setzen Sie dann Vergleichswert sowie Zweigausgaben.",
        "tooltip.node.LookupMap.usage": "Pflegen Sie die Mapping-Tabelle, wenn Quellwerte umbenannt oder standardisiert werden müssen.",
        "tooltip.node.StrictMatch.usage": "Lassen Sie diesen Knoten im Fluss, wenn exaktes quellenübergreifendes Matching Teil des Fachschritts ist.",
        "tooltip.node.ResolverMatch.usage": "Verwenden Sie ihn, wenn ein heuristischer Resolver-Schritt das direkte Matching anreichern soll.",
        "tooltip.node.MissingPlaceholder.usage": "Verbinden Sie ihn, wenn fehlende Gegenstücke explizit sichtbar gemacht werden sollen.",
        "tooltip.node.CompletionMerge.usage": "Platzieren Sie ihn nach dem Matching, damit Review-Status oder Completion-Vorschläge zurückgeführt werden.",
        "tooltip.node.RelationBuild.usage": "Verwenden Sie ihn, wenn die Stufe beziehungsartige Zeilen aus mehreren Treffern erzeugen soll.",
        "tooltip.node.BuildRow.usage": "Verbinden Sie pro Ziel-Port genau eine Quelle, um zu steuern, welcher Wert in welcher standardisierten Spalte landet.",
        "tooltip.node.OutputSheet.usage": "Halten Sie pro Zweig genau einen finalen OutputSheet-Knoten, wenn standardisierte Zeilen ausgegeben werden sollen.",
        "tooltip.node.InputColumn.usage": "Wählen Sie die Arbeitsmappen-Spalte und den Lesemodus und verbinden Sie sie dann mit Transformationen oder Ausgaben.",
        "tooltip.node.MapEnum.usage": "Bearbeiten Sie die Mapping-Tabelle so, dass Arbeitsmappenwerte auf das erwartete Zielvokabular landen.",
        "tooltip.node.BoolMap.usage": "Konfigurieren Sie die True- und False-Ausgaben, wenn nachgelagerte Logik normalisierte Bool-Texte erwartet.",
        "tooltip.node.PreferFirstNonEmpty.usage": "Verbinden Sie Kandidaten in Prioritätsreihenfolge, damit der erste gefüllte Wert verwendet wird.",
        "tooltip.node.ConfidenceGate.usage": "Führen Sie Wert und Konfidenz zu und setzen Sie dann Schwellenwert und Fallback.",
        "tooltip.node.OutputProperty.usage": "Mappen Sie einen vorbereiteten Wert auf die exakte AAS-Property, die befüllt werden soll.",
        "tooltip.node.OutputSubmodel.usage": "Verbinden Sie Property-Knoten, die unter demselben Submodell gruppiert werden sollen.",
        "tooltip.t1t5.t1.BuiltinContext.detail": "Im eingebauten T1-Fluss steht dies für den geparsten R&I-Quellkontext, den die Legacy-Pipeline vor der Graph-Ausführung vorbereitet hat.",
        "tooltip.t1t5.t1.StrictMatch.detail": "In T1 markiert dies die strikte quellenübergreifende Match-Phase, die prüft, ob ein R&I-Gerät auch in unterstützenden Quellen vorkommt.",
        "tooltip.t1t5.t1.CompletionMerge.detail": "In T1 markiert dies die Completion-Merge-Phase, in der Review-Flags, fehlende Ziele und empfohlene Aktionen in die Zeile gemischt werden.",
        "tooltip.t1t5.default.WorkbookSheet.detail": "Verwenden Sie dies in benutzerdefinierten Workbook-Profilen, um den Fluss vor Header-Prüfung und Zeileniteration an ein Blatt zu binden.",
        "tooltip.t1t5.default.HeaderMatch.detail": "Dieser Knoten beschreibt die Signatur, mit der automatisch erkannt wird, ob ein Workbook-Profil zur Beispiel-Excel-Vorlage passt.",
        "tooltip.t1t5.default.RowIterator.detail": "Nachdem ein Blatt passt, steht dieser Knoten für die zeilenweise Schleife, aus der nachfolgende CellValue-Knoten lesen.",
        "tooltip.t1t5.default.BuildRow.detail": "Dies ist der Schritt, der passende Werte in eine standardisierte Excel-Zeile für die aktuelle T-Stufe umwandelt.",
        "tooltip.t1t5.default.OutputSheet.detail": "Dies ist der finale Export-Schritt der Stufe. Die verbundene BuildRow-Ausgabe wird zu einer Zeile im gewählten standardisierten Blatt.",
        "tooltip.t1t5.label.ri_source_context.detail": "Diese Bezeichnung markiert den eingebauten R&I-Quellkontext, der den Standard-T1-Fluss startet.",
        "tooltip.t1t5.label.cross_document_strict_match.detail": "Diese Bezeichnung markiert den strikten quellenübergreifenden Vergleichsschritt im Standard-T1-Fluss.",
        "tooltip.t1t5.label.completion_candidate_merge.detail": "Diese Bezeichnung markiert den Completion-Merge-Schritt, der review-orientierte Felder in die Zeile zurückschreibt.",
        "tooltip.tx.default.InputColumn.detail": "In Tx ist dies die Brücke von der standardisierten Excel-Datei in den Regelgraphen. Sie liest die Quellspalte vor allen Transformationen.",
        "tooltip.tx.default.NormalizeIdentifier.detail": "In Tx wird dies typischerweise vor dem Matching mit AAS-Identifiern eingesetzt oder wenn ein Workbook-Tag bereinigt werden muss.",
        "tooltip.tx.default.RegexExtract.detail": "In Tx wird dies oft verwendet, um Präfixe, Einheiten oder Klammertexte zu entfernen, bevor in AAS-Properties gemappt wird.",
        "tooltip.tx.default.MapEnum.detail": "Verwenden Sie dies, wenn Workbook-Codes in kontrollierte Zielwerte des AAS-Modells übersetzt werden müssen.",
        "tooltip.tx.default.BoolMap.detail": "Hilfreich, wenn das Workbook Flags wie X, Yes oder 1 speichert und das Zielmodell normalisierte Bool-Texte erwartet.",
        "tooltip.tx.default.Concat.detail": "Dies ist hilfreich, wenn eine AAS-Property aus mehreren Workbook-Spalten zusammengesetzt werden soll.",
        "tooltip.tx.default.PreferFirstNonEmpty.detail": "Verwenden Sie dies, wenn mehrere Kandidatenspalten dieselbe AAS-Property liefern können und der erste befüllte Wert gewählt werden soll.",
        "tooltip.tx.default.Condition.detail": "Damit kann ein Zweig der Tx-Ausgabe vom Workbook-Inhalt abhängen, zum Beispiel zur Ableitung von Status- oder Fallback-Texten.",
        "tooltip.tx.default.ConfidenceGate.detail": "Verwenden Sie dies, wenn ein Wert nur dann in die AAS-Ausgabe fließen soll, wenn die Konfidenzspalte hoch genug ist.",
        "tooltip.tx.default.OutputProperty.detail": "Dies ist der finale Property-Mapping-Schritt. Jeder verbundene Wert wird zu einer Property im Ziel-Submodell.",
        "tooltip.tx.default.OutputSubmodel.detail": "Dies gruppiert mehrere OutputProperty-Knoten, damit das erzeugte AAS-Payload nach Submodellen organisiert ist.",
    }
)

TRANSLATIONS["zh"].update(
    {
        "tooltip.node.fallback.title": "规则节点",
        "tooltip.node.fallback.generic": "这个节点会参与规则流程，并在不同步骤之间传递数据。",
        "tooltip.node.fallback.usage": "把它连接到前后步骤上；如果需要更细的行为，再查看高级设置。",
        "tooltip.section.what": "作用：{text}",
        "tooltip.section.current": "当前配置：{text}",
        "tooltip.section.how": "怎么用：{text}",
        "tooltip.summary.none": "当前还没有额外配置。",
        "tooltip.summary.field": "字段：{value}",
        "tooltip.summary.mode": "模式：{value}",
        "tooltip.summary.separator": "分隔符：{value}",
        "tooltip.summary.constant": "常量：{value}",
        "tooltip.summary.pattern": "正则：{value}",
        "tooltip.summary.group": "分组：{value}",
        "tooltip.summary.default_value": "默认值：{value}",
        "tooltip.summary.connected_inputs": "已连接输入：{count}",
        "tooltip.summary.target_fields": "目标字段数：{count}",
        "tooltip.summary.flow_inputs": "流程输入数：{count}",
        "tooltip.summary.sheet": "工作表：{value}",
        "tooltip.summary.mapping_entries": "映射项数：{count}",
        "tooltip.summary.true_value": "为真时输出：{value}",
        "tooltip.summary.false_value": "为假时输出：{value}",
        "tooltip.summary.operator": "运算符：{value}",
        "tooltip.summary.compare_to": "比较值：{value}",
        "tooltip.summary.min_confidence": "最小置信度：{value}",
        "tooltip.summary.fallback": "回退值：{value}",
        "tooltip.summary.required_headers": "必需表头数：{count}",
        "tooltip.summary.optional_headers": "可选表头数：{count}",
        "tooltip.summary.property": "属性：{value}",
        "tooltip.summary.submodel": "子模型：{value}",
        "tooltip.summary.value": "值：{value}",
        "tooltip.inspector.none": "请选择一个节点后再查看上下文说明。",
        "tooltip.control.node_type": "这里显示当前选中的节点类型。不同节点类型会暴露不同的端口和配置项。",
        "tooltip.control.node_summary": "这里显示当前选中的节点 ID 和类型，方便确认你正在编辑哪一个图形节点。",
        "tooltip.control.label": "标签用于给节点起一个更业务化的名字，不会改变它的技术类型。",
        "tooltip.control.field": "设置这个节点要读取的源字段或工作簿列名。",
        "tooltip.control.mode": "选择同一列出现多个值时，这个节点应该如何组合后再输出。",
        "tooltip.control.property": "选择这个节点要写入的目标 AAS 属性。",
        "tooltip.control.submodel": "选择这些属性最终要归入的目标 AAS 子模型。",
        "tooltip.control.value": "输入这个节点要直接输出的固定值，或作为默认值使用的内容。",
        "tooltip.control.pattern": "输入用于从上游文本中抽取部分内容的正则表达式。",
        "tooltip.control.separator": "设置多个输入值拼接时插入的分隔符。",
        "tooltip.control.compare_to": "输入 Condition 节点用来比较的目标值。",
        "tooltip.control.true_value": "设置条件为真时输出什么。",
        "tooltip.control.false_value": "设置条件为假时输出什么。",
        "tooltip.control.sheet_name": "设置这个模板或节点要匹配的工作表名称。",
        "tooltip.control.field_names": "列出 BuildRow 应该暴露为输入端口的标准化输出字段。",
        "tooltip.control.mapping": "编辑把源值翻译成目标值时用到的键值映射表。",
        "tooltip.control.advanced": "高级 JSON 用于填写界面上没有单独暴露出来的额外节点配置。",
        "tooltip.control.apply": "把当前表单里的修改应用回选中的节点。",
        "tooltip.node.BuiltinContext.generic": "从系统内置的 Inconsistence Extract 上下文中读取记录，而不是从自定义工作簿读取。",
        "tooltip.node.WorkbookSheet.generic": "选择后续工作簿节点要检查的工作表。",
        "tooltip.node.HeaderMatch.generic": "检查当前工作表是否符合预期的表头模式。",
        "tooltip.node.RowIterator.generic": "遍历匹配到的工作表行，让下游节点可以逐行读取单元格值。",
        "tooltip.node.CellValue.generic": "从当前标准化行或工作簿行里读取一个字段。",
        "tooltip.node.Constant.generic": "输出一个固定常量值。",
        "tooltip.node.NormalizeIdentifier.generic": "把原始 tag 或 ID 规范化成更稳定的标识格式。",
        "tooltip.node.RegexExtract.generic": "用正则表达式从输入值里提取一部分内容。",
        "tooltip.node.Concat.generic": "把多个输入值拼接成一个字符串。",
        "tooltip.node.Condition.generic": "比较输入值，然后输出 true 分支或 false 分支对应的结果。",
        "tooltip.node.LookupMap.generic": "通过映射表把输入值翻译成另一组值。",
        "tooltip.node.StrictMatch.generic": "表示一个严格匹配步骤，用来在不同来源之间做精确对应。",
        "tooltip.node.ResolverMatch.generic": "表示一个解析器匹配步骤，用来补充直接匹配不够的情况。",
        "tooltip.node.MissingPlaceholder.generic": "为缺失或未解析的数据创建占位结果。",
        "tooltip.node.CompletionMerge.generic": "把补全或复核信息重新并回当前行。",
        "tooltip.node.RelationBuild.generic": "基于上游匹配结果构造关系行或关系载荷。",
        "tooltip.node.BuildRow.generic": "把多个输入值汇总成一条标准化输出行。",
        "tooltip.node.OutputSheet.generic": "把组装好的行写入目标 standardized sheet。",
        "tooltip.node.InputColumn.generic": "读取源工作簿中的一个列。",
        "tooltip.node.MapEnum.generic": "把源值映射成受控词汇或枚举值。",
        "tooltip.node.BoolMap.generic": "把输入值转换成 true/false 风格的标准输出。",
        "tooltip.node.PreferFirstNonEmpty.generic": "从多个候选输入里选第一个非空值，必要时再回退。",
        "tooltip.node.ConfidenceGate.generic": "只有当置信度达到阈值时才允许该值继续向下流动。",
        "tooltip.node.OutputProperty.generic": "把准备好的值写入一个 AAS 属性。",
        "tooltip.node.OutputSubmodel.generic": "把多个属性分组到同一个 AAS 子模型输出里。",
        "tooltip.node.BuiltinContext.usage": "当这个阶段需要从系统内置的 Inconsistence Extract 上下文开始，而不是从自定义 Excel 模板开始时，就用它。",
        "tooltip.node.WorkbookSheet.usage": "先指定你要检查的工作表名称，再继续连接表头判断和逐行处理节点。",
        "tooltip.node.HeaderMatch.usage": "填写必需表头和可选表头，用来描述你期望匹配的 Excel 模板。",
        "tooltip.node.RowIterator.usage": "把它接在工作表识别或表头判断之后，这样每一行都可以继续做字段映射。",
        "tooltip.node.CellValue.usage": "设置源字段名，然后把它的输出连接到转换节点或直接连接到 BuildRow。",
        "tooltip.node.Constant.usage": "适合放固定文案、固定状态值，或始终写同一内容的列。",
        "tooltip.node.NormalizeIdentifier.usage": "当 tag 或 ID 在匹配或输出前需要先做规范化时，把它放在前面。",
        "tooltip.node.RegexExtract.usage": "当你只想把输入字符串中的一部分继续往后传时，填写正则和分组即可。",
        "tooltip.node.Concat.usage": "按你想要的顺序连接多个输入，再根据需要调整分隔符。",
        "tooltip.node.Condition.usage": "给它接入被比较值和比较目标，再设置真分支和假分支输出。",
        "tooltip.node.LookupMap.usage": "当源值需要重命名或标准化时，在这里维护映射表。",
        "tooltip.node.StrictMatch.usage": "如果业务流程里需要精确的跨文档匹配步骤，就保留这个节点。",
        "tooltip.node.ResolverMatch.usage": "当直接匹配不够，需要额外的解析或启发式补充时使用它。",
        "tooltip.node.MissingPlaceholder.usage": "当你希望明确保留“缺失对应项”的结果时，把它接进流程里。",
        "tooltip.node.CompletionMerge.usage": "通常放在匹配步骤之后，用来把复核状态或补全建议并回当前行。",
        "tooltip.node.RelationBuild.usage": "当这个阶段需要根据多个匹配结果输出关系型行时使用它。",
        "tooltip.node.BuildRow.usage": "把不同来源的值分别连到目标端口上，就能控制每个标准化列最终写什么。",
        "tooltip.node.OutputSheet.usage": "每条最终要输出标准化行的分支，通常保留一个最终 OutputSheet 节点即可。",
        "tooltip.node.InputColumn.usage": "选择源列和读取模式，然后把它连接到转换节点或输出节点上。",
        "tooltip.node.MapEnum.usage": "维护映射表，让工作簿里的取值落到目标模型要求的受控词汇上。",
        "tooltip.node.BoolMap.usage": "当下游逻辑希望得到统一的布尔文本时，在这里配置真值和假值输出。",
        "tooltip.node.PreferFirstNonEmpty.usage": "按优先级连接多个候选值，节点会优先选择第一个非空输入。",
        "tooltip.node.ConfidenceGate.usage": "把值和置信度一起输入，再设置阈值和回退值。",
        "tooltip.node.OutputProperty.usage": "把一个已经准备好的值映射到你想填充的具体 AAS 属性。",
        "tooltip.node.OutputSubmodel.usage": "把属于同一个子模型的属性节点都连接到这里。",
        "tooltip.t1t5.t1.BuiltinContext.detail": "在内置 T1 流程里，它表示 legacy 流程在图执行前就已经准备好的 R&I 源上下文。",
        "tooltip.t1t5.t1.StrictMatch.detail": "在 T1 里，它表示严格的跨文档匹配阶段，用来检查某个 R&I 设备是否也出现在其他支撑来源中。",
        "tooltip.t1t5.t1.CompletionMerge.detail": "在 T1 里，它表示补全合并阶段，会把复核标记、缺失目标和建议动作并回当前行。",
        "tooltip.t1t5.default.WorkbookSheet.detail": "在自定义工作簿模板里，这个节点用来先把流程锚定到某个工作表，再继续做表头判断和逐行处理。",
        "tooltip.t1t5.default.HeaderMatch.detail": "这个节点描述了自动匹配 Excel 模板时使用的签名条件。",
        "tooltip.t1t5.default.RowIterator.detail": "当某个工作表匹配成功后，这个节点代表逐行遍历的过程，后面的 CellValue 节点都会从这里取值。",
        "tooltip.t1t5.default.BuildRow.detail": "这是把匹配结果真正组装成当前 T 阶段标准化 Excel 行的步骤。",
        "tooltip.t1t5.default.OutputSheet.detail": "这是当前阶段的最终导出步骤。连接过来的 BuildRow 输出会变成目标标准化工作表中的一行。",
        "tooltip.t1t5.label.ri_source_context.detail": "这个标签表示默认 T1 流程中的 R&I 源上下文入口。",
        "tooltip.t1t5.label.cross_document_strict_match.detail": "这个标签表示默认 T1 流程里的跨文档严格匹配步骤。",
        "tooltip.t1t5.label.completion_candidate_merge.detail": "这个标签表示把复核相关字段并回当前行的补全合并步骤。",
        "tooltip.tx.default.InputColumn.detail": "在 Tx 里，它是从 standardized Excel 进入规则图的入口，会先读取源工作簿列，再交给后续转换节点。",
        "tooltip.tx.default.NormalizeIdentifier.detail": "在 Tx 里，它通常用于在映射到 AAS 标识前先清洗和规范化 workbook 中的 tag。",
        "tooltip.tx.default.RegexExtract.detail": "在 Tx 里，它常用于去掉前缀、单位或括号内容后，再把结果映射到 AAS 属性。",
        "tooltip.tx.default.MapEnum.detail": "当工作簿里的编码需要翻译成 AAS 模型要求的受控值时，就使用它。",
        "tooltip.tx.default.BoolMap.detail": "当工作簿里用 X、Yes、1 之类表示布尔含义，而目标模型需要统一布尔文本时，它会很有用。",
        "tooltip.tx.default.Concat.detail": "当一个 AAS 属性需要由多个工作簿列拼接而成时，这个节点很适合。",
        "tooltip.tx.default.PreferFirstNonEmpty.detail": "当多个候选列都可能提供同一个 AAS 属性，而你只想取第一个非空值时，就用它。",
        "tooltip.tx.default.Condition.detail": "它可以让 Tx 输出是否走某个分支，取决于工作簿中的内容，例如派生状态值或回退标签。",
        "tooltip.tx.default.ConfidenceGate.detail": "当某个值只有在置信度足够高时才应该进入 AAS 输出时，就使用它。",
        "tooltip.tx.default.OutputProperty.detail": "这是最终的属性映射步骤。每个连接进来的值都会落到目标子模型中的一个属性上。",
        "tooltip.tx.default.OutputSubmodel.detail": "它会把多个 OutputProperty 节点归组，让最终生成的 AAS 载荷按子模型组织。",
    }
)
