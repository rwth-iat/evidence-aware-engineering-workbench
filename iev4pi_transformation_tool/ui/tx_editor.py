from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path

from PyQt6.QtCore import QMimeData, QPointF, Qt
from PyQt6.QtGui import QAction, QDrag, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from iev4pi_transformation_tool.core.utils import clean_cell
from iev4pi_transformation_tool.services.workbench import Workbench
from iev4pi_transformation_tool.tx import TxRuleSet, build_default_uc1_rule_set
from iev4pi_transformation_tool.ui.i18n import normalize_language
from iev4pi_transformation_tool.ui.node_tooltips import NodeTooltipContext, build_inspector_tooltip, build_palette_node_tooltip
from iev4pi_transformation_tool.ui.pages import BasePage
from iev4pi_transformation_tool.ui.qfluent import BodyLabel, CaptionLabel, CardWidget, ComboBox, PrimaryPushButton, SearchLineEdit, StrongBodyLabel
from iev4pi_transformation_tool.ui.tx_graph import PALETTE_MIME_TYPE, NODE_GROUPS, TxFlowScene, TxFlowView, TxGraphAdapter, source_type_targets


class TxPaletteList(QListWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(self.SelectionMode.SingleSelection)

    def startDrag(self, supported_actions) -> None:
        item = self.currentItem()
        if item is None:
            return
        node_type = clean_cell(item.data(Qt.ItemDataRole.UserRole))
        if not node_type:
            return
        mime = QMimeData()
        mime.setData(PALETTE_MIME_TYPE, node_type.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec()


class TxRuleEditorWidget(BasePage):
    def __init__(
        self,
        workbench: Workbench,
        refresh_all,
        *,
        fixed_source_type: str | None = None,
        show_source_type_picker: bool = True,
        prefer_saved_rules: bool = True,
        title_key: str = "page.tx_editor.title",
        subtitle_key: str = "page.tx_editor.subtitle",
    ) -> None:
        super().__init__(
            workbench,
            refresh_all,
            title_key,
            subtitle_key,
        )
        self.root_layout.removeWidget(self.title_label)
        self.root_layout.removeWidget(self.subtitle_label)
        self.title_label.hide()
        self.subtitle_label.hide()

        self.fixed_source_type = clean_cell(fixed_source_type)
        self.show_source_type_picker = bool(show_source_type_picker and not self.fixed_source_type)
        self.prefer_saved_rules = prefer_saved_rules
        initial_source_type = self.fixed_source_type or "instrument_list"

        self.rule_set = build_default_uc1_rule_set(initial_source_type)
        self.workbook_path: Path | None = None
        self.workbook_columns: list[str] = []
        self.identity_keys: list[str] = []
        self.target_catalog = source_type_targets(self.rule_set.source_type)
        self._rule_origin = "default"

        self.scene = TxFlowScene(self)
        self.scene.tooltip_language = self.language
        self.scene.tooltip_source_type = self.rule_set.source_type
        self.graph_adapter = TxGraphAdapter(self.scene)
        self._updating_inspector = False
        self._blocking_scene_events = False
        self._pending_drag_sync = False
        self._undo_stack: list[str] = []
        self._redo_stack: list[str] = []
        self._last_snapshot = ""
        self._selection_clipboard: dict[str, object] = {}

        self._build_toolbar()
        self._build_layout()
        self._install_actions()
        self._connect_scene_signals()

        self._populate_palette()
        self._load_preferred_rule_set(reset_history=True)
        self._reload_workbook_context()
        self.apply_language()

    @property
    def language(self) -> str:
        return normalize_language(self.workbench.settings.ui_language)

    def _selected_node_tooltip_context(self) -> NodeTooltipContext | None:
        node = self._selected_scene_node()
        if node is None:
            return None
        model = node.model
        return NodeTooltipContext(
            language=self.language,
            editor_kind="tx",
            node_type=clean_cell(getattr(model, "node_type", "")),
            label=clean_cell(getattr(model, "label_text", "")),
            config=dict(getattr(model, "config", {}) or {}),
            source_type=self._current_source_type(),
            connected_input_count=len(getattr(node.state, "input_connections", [])),
            connected_output_count=len(getattr(node.state, "output_connections", [])),
        )

    def _refresh_palette_tooltips(self) -> None:
        for index in range(self.node_palette_list.count()):
            item = self.node_palette_list.item(index)
            node_type = clean_cell(item.data(Qt.ItemDataRole.UserRole))
            if not node_type:
                item.setToolTip("")
                continue
            item.setToolTip(
                build_palette_node_tooltip(
                    language=self.language,
                    editor_kind="tx",
                    node_type=node_type,
                    source_type=self._current_source_type(),
                )
            )

    def _refresh_canvas_tooltips(self) -> None:
        self.scene.tooltip_language = self.language
        self.scene.tooltip_source_type = self._current_source_type()
        self.scene.refresh_node_tooltips()

    def _set_tooltip_pair(self, label_widget: QWidget, input_widget: QWidget, text: str) -> None:
        label_widget.setToolTip(text)
        input_widget.setToolTip(text)

    def _refresh_inspector_tooltips(self) -> None:
        context = self._selected_node_tooltip_context()
        summary_tooltip = build_inspector_tooltip(language=self.language, control_key="node_summary", context=context)
        self.node_label.setToolTip(summary_tooltip)
        self.node_summary_label.setToolTip(summary_tooltip)
        self._set_tooltip_pair(
            self.label_label,
            self.label_input,
            build_inspector_tooltip(language=self.language, control_key="label", context=context),
        )
        self._set_tooltip_pair(
            self.field_label,
            self.field_combo,
            build_inspector_tooltip(language=self.language, control_key="field", context=context),
        )
        self._set_tooltip_pair(
            self.mode_label,
            self.mode_combo,
            build_inspector_tooltip(language=self.language, control_key="mode", context=context),
        )
        self._set_tooltip_pair(
            self.property_label,
            self.property_combo,
            build_inspector_tooltip(language=self.language, control_key="property", context=context),
        )
        self._set_tooltip_pair(
            self.submodel_label,
            self.submodel_combo,
            build_inspector_tooltip(language=self.language, control_key="submodel", context=context),
        )
        self._set_tooltip_pair(
            self.value_label,
            self.value_input,
            build_inspector_tooltip(language=self.language, control_key="value", context=context),
        )
        self._set_tooltip_pair(
            self.separator_label,
            self.separator_input,
            build_inspector_tooltip(language=self.language, control_key="separator", context=context),
        )
        advanced_tooltip = build_inspector_tooltip(language=self.language, control_key="advanced", context=context)
        self.advanced_label.setToolTip(advanced_tooltip)
        self.advanced_config_edit.setToolTip(advanced_tooltip)
        self.apply_node_button.setToolTip(build_inspector_tooltip(language=self.language, control_key="apply", context=context))

    def _build_toolbar(self) -> None:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.source_type_label = StrongBodyLabel("")
        toolbar.addWidget(self.source_type_label)

        self.source_type_combo = ComboBox(self)
        for source_type in ("pid", "instrument_list", "wiring", "datasheet", "piping"):
            self.source_type_combo.addItem(source_type)
        self.source_type_combo.setCurrentText(self.rule_set.source_type)
        self.source_type_combo.currentTextChanged.connect(self._handle_source_type_changed)
        toolbar.addWidget(self.source_type_combo, 0)

        self.load_default_button = QPushButton(self)
        self.load_default_button.clicked.connect(self._load_default_rule_set)
        toolbar.addWidget(self.load_default_button)

        self.load_workbook_button = QPushButton(self)
        self.load_workbook_button.clicked.connect(self._choose_workbook)
        toolbar.addWidget(self.load_workbook_button)

        self.undo_button = QPushButton(self)
        self.undo_button.clicked.connect(self._undo)
        toolbar.addWidget(self.undo_button)

        self.redo_button = QPushButton(self)
        self.redo_button.clicked.connect(self._redo)
        toolbar.addWidget(self.redo_button)

        self.copy_button = QPushButton(self)
        self.copy_button.clicked.connect(self._copy_selection)
        toolbar.addWidget(self.copy_button)

        self.paste_button = QPushButton(self)
        self.paste_button.clicked.connect(self._paste_selection)
        toolbar.addWidget(self.paste_button)

        self.delete_selection_button = QPushButton(self)
        self.delete_selection_button.clicked.connect(self._delete_selection)
        toolbar.addWidget(self.delete_selection_button)

        self.rule_origin_label = CaptionLabel("")
        self.rule_origin_label.setWordWrap(True)
        toolbar.addWidget(self.rule_origin_label, 0)

        self.workbook_label = BodyLabel("")
        self.workbook_label.setWordWrap(True)
        toolbar.addWidget(self.workbook_label, 1)

        self.source_type_label.setVisible(self.show_source_type_picker)
        self.source_type_combo.setVisible(self.show_source_type_picker)

        self.root_layout.addLayout(toolbar)

    def _build_layout(self) -> None:
        self.main_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.root_layout.addWidget(self.main_splitter, 1)

        top_splitter = QSplitter(Qt.Orientation.Horizontal, self.main_splitter)
        self.main_splitter.addWidget(top_splitter)

        self.palette_card = CardWidget(self)
        palette_layout = QVBoxLayout(self.palette_card)
        palette_layout.setContentsMargins(16, 16, 16, 16)
        palette_layout.setSpacing(10)

        self.palette_title = StrongBodyLabel("")
        palette_layout.addWidget(self.palette_title)

        self.palette_search = SearchLineEdit(self)
        self.palette_search.textChanged.connect(self._filter_palette)
        palette_layout.addWidget(self.palette_search)

        self.node_palette_list = TxPaletteList(self)
        self.node_palette_list.itemDoubleClicked.connect(self._handle_palette_item_activated)
        palette_layout.addWidget(self.node_palette_list, 1)

        self.add_node_button = PrimaryPushButton("", self)
        self.add_node_button.clicked.connect(self._add_selected_palette_node)
        palette_layout.addWidget(self.add_node_button)

        self.columns_label = StrongBodyLabel("")
        palette_layout.addWidget(self.columns_label)

        self.column_list = QListWidget(self)
        self.column_list.itemDoubleClicked.connect(self._apply_selected_column)
        palette_layout.addWidget(self.column_list, 1)

        self.targets_label = StrongBodyLabel("")
        palette_layout.addWidget(self.targets_label)

        self.target_list = QListWidget(self)
        self.target_list.itemDoubleClicked.connect(self._apply_selected_target)
        palette_layout.addWidget(self.target_list, 1)

        top_splitter.addWidget(self.palette_card)

        self.canvas_card = CardWidget(self)
        canvas_layout = QVBoxLayout(self.canvas_card)
        canvas_layout.setContentsMargins(16, 16, 16, 16)
        canvas_layout.setSpacing(10)

        self.canvas_title = StrongBodyLabel("")
        canvas_layout.addWidget(self.canvas_title)

        canvas_toolbar = QHBoxLayout()
        canvas_toolbar.setSpacing(8)

        self.arrange_button = QPushButton(self)
        self.arrange_button.clicked.connect(self._auto_arrange)
        canvas_toolbar.addWidget(self.arrange_button)

        self.zoom_in_button = QPushButton(self)
        self.zoom_in_button.clicked.connect(self._zoom_in)
        canvas_toolbar.addWidget(self.zoom_in_button)

        self.zoom_out_button = QPushButton(self)
        self.zoom_out_button.clicked.connect(self._zoom_out)
        canvas_toolbar.addWidget(self.zoom_out_button)

        self.reset_zoom_button = QPushButton(self)
        self.reset_zoom_button.clicked.connect(self._reset_zoom)
        canvas_toolbar.addWidget(self.reset_zoom_button)

        self.canvas_hint = BodyLabel("")
        self.canvas_hint.setWordWrap(True)
        canvas_toolbar.addWidget(self.canvas_hint, 1)
        canvas_layout.addLayout(canvas_toolbar)

        self.canvas_view = TxFlowView(self.scene, self)
        self.canvas_view.setMinimumHeight(460)
        self.canvas_view.node_create_requested.connect(self._create_node_at)
        canvas_layout.addWidget(self.canvas_view, 1)
        top_splitter.addWidget(self.canvas_card)

        self.inspector_card = CardWidget(self)
        inspector_layout = QVBoxLayout(self.inspector_card)
        inspector_layout.setContentsMargins(16, 16, 16, 16)
        inspector_layout.setSpacing(10)

        self.inspector_title = StrongBodyLabel("")
        inspector_layout.addWidget(self.inspector_title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.node_label = QLabel("")
        self.node_summary_label = QLabel("")
        self.node_summary_label.setWordWrap(True)
        form.addRow(self.node_label, self.node_summary_label)

        self.label_label = QLabel("")
        self.label_input = QLineEdit(self)
        form.addRow(self.label_label, self.label_input)

        self.field_label = QLabel("")
        self.field_combo = QComboBox(self)
        self.field_combo.setEditable(True)
        self.field_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        form.addRow(self.field_label, self.field_combo)

        self.mode_label = QLabel("")
        self.mode_combo = QComboBox(self)
        self.mode_combo.addItems(["join", "first", "first_non_empty", "count_present", "bool_any"])
        form.addRow(self.mode_label, self.mode_combo)

        self.property_label = QLabel("")
        self.property_combo = QComboBox(self)
        self.property_combo.setEditable(True)
        self.property_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        form.addRow(self.property_label, self.property_combo)

        self.submodel_label = QLabel("")
        self.submodel_combo = QComboBox(self)
        self.submodel_combo.setEditable(True)
        self.submodel_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        form.addRow(self.submodel_label, self.submodel_combo)

        self.value_label = QLabel("")
        self.value_input = QLineEdit(self)
        form.addRow(self.value_label, self.value_input)

        self.separator_label = QLabel("")
        self.separator_input = QLineEdit(" | ", self)
        form.addRow(self.separator_label, self.separator_input)
        inspector_layout.addLayout(form)

        self.advanced_label = StrongBodyLabel("")
        inspector_layout.addWidget(self.advanced_label)

        self.advanced_config_edit = QPlainTextEdit(self)
        self.advanced_config_edit.setMinimumHeight(180)
        inspector_layout.addWidget(self.advanced_config_edit, 1)

        self.apply_node_button = PrimaryPushButton("", self)
        self.apply_node_button.clicked.connect(self._apply_node_edits)
        inspector_layout.addWidget(self.apply_node_button)
        top_splitter.addWidget(self.inspector_card)

        self.preview_card = CardWidget(self)
        preview_layout = QVBoxLayout(self.preview_card)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        preview_layout.setSpacing(10)

        self.preview_title = StrongBodyLabel("")
        preview_layout.addWidget(self.preview_title)

        preview_toolbar = QHBoxLayout()
        preview_toolbar.setSpacing(8)

        self.identity_label = StrongBodyLabel("")
        self.identity_combo = ComboBox(self)
        self.identity_combo.setMaximumWidth(360)
        preview_toolbar.addWidget(self.identity_label, 0)
        preview_toolbar.addWidget(self.identity_combo, 0)

        self.validate_button = QPushButton(self)
        self.validate_button.clicked.connect(self._validate_rule_set)
        self._configure_preview_action_button(self.validate_button)
        preview_toolbar.addWidget(self.validate_button, 0)

        self.save_button = QPushButton(self)
        self.save_button.clicked.connect(self._save_rule_set)
        self._configure_preview_action_button(self.save_button)
        preview_toolbar.addWidget(self.save_button, 0)

        self.import_button = QPushButton(self)
        self.import_button.clicked.connect(self._import_rule_set)
        self._configure_preview_action_button(self.import_button)
        preview_toolbar.addWidget(self.import_button, 0)

        self.export_button = QPushButton(self)
        self.export_button.clicked.connect(self._export_rule_set)
        self._configure_preview_action_button(self.export_button)
        preview_toolbar.addWidget(self.export_button, 0)

        self.preview_button = PrimaryPushButton("", self)
        self.preview_button.clicked.connect(self._preview_rule_set)
        self._configure_preview_action_button(self.preview_button)
        preview_toolbar.addWidget(self.preview_button, 0)

        self.generate_button = QPushButton(self)
        self.generate_button.clicked.connect(self._generate_aas_from_tx)
        self._configure_preview_action_button(self.generate_button, max_width=140)
        preview_toolbar.addWidget(self.generate_button, 0)
        preview_toolbar.addStretch(1)
        preview_layout.addLayout(preview_toolbar)

        text_splitter = QSplitter(Qt.Orientation.Horizontal, self.preview_card)

        payload_card = CardWidget(self.preview_card)
        payload_layout = QVBoxLayout(payload_card)
        payload_layout.setContentsMargins(12, 12, 12, 12)
        payload_layout.setSpacing(8)
        self.payload_title = StrongBodyLabel("")
        payload_layout.addWidget(self.payload_title)
        self.preview_payload = QPlainTextEdit(self.preview_card)
        self.preview_payload.setReadOnly(True)
        payload_layout.addWidget(self.preview_payload, 1)
        text_splitter.addWidget(payload_card)

        trace_card = CardWidget(self.preview_card)
        trace_layout = QVBoxLayout(trace_card)
        trace_layout.setContentsMargins(12, 12, 12, 12)
        trace_layout.setSpacing(8)
        self.trace_title = StrongBodyLabel("")
        trace_layout.addWidget(self.trace_title)
        self.preview_trace = QPlainTextEdit(self.preview_card)
        self.preview_trace.setReadOnly(True)
        trace_layout.addWidget(self.preview_trace, 1)
        text_splitter.addWidget(trace_card)

        issues_card = CardWidget(self.preview_card)
        issues_layout = QVBoxLayout(issues_card)
        issues_layout.setContentsMargins(12, 12, 12, 12)
        issues_layout.setSpacing(8)
        self.issues_title = StrongBodyLabel("")
        issues_layout.addWidget(self.issues_title)
        self.preview_issues = QPlainTextEdit(self.preview_card)
        self.preview_issues.setReadOnly(True)
        issues_layout.addWidget(self.preview_issues, 1)
        text_splitter.addWidget(issues_card)

        preview_layout.addWidget(text_splitter, 1)

        self.main_splitter.addWidget(self.preview_card)
        self.main_splitter.setSizes([720, 280])
        top_splitter.setSizes([280, 880, 340])

    def _install_actions(self) -> None:
        self.undo_action = QAction(self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.triggered.connect(self._undo)
        self.addAction(self.undo_action)

        self.redo_action = QAction(self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.triggered.connect(self._redo)
        self.addAction(self.redo_action)

        self.copy_action = QAction(self)
        self.copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_action.triggered.connect(self._copy_selection)
        self.addAction(self.copy_action)

        self.paste_action = QAction(self)
        self.paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self.paste_action.triggered.connect(self._paste_selection)
        self.addAction(self.paste_action)

        self.delete_action = QAction(self)
        self.delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.delete_action.triggered.connect(self._delete_selection)
        self.addAction(self.delete_action)

    def _configure_preview_action_button(self, button: QPushButton, *, max_width: int = 132) -> None:
        button.setMinimumHeight(34)
        button.setMaximumWidth(max_width)

    def _connect_scene_signals(self) -> None:
        self.scene.selectionChanged.connect(self._handle_scene_selection_changed)
        self.scene.connection_created.connect(self._handle_scene_structure_changed)
        self.scene.connection_deleted.connect(self._handle_scene_structure_changed)
        self.scene.node_deleted.connect(self._handle_scene_structure_changed)
        self.scene.node_moved.connect(self._handle_scene_node_moved)
        self.scene.node_dragging.connect(self._handle_scene_dragging)

    def apply_language(self) -> None:
        super().apply_language()
        self.scene.tooltip_language = self.language
        self.source_type_label.setText(self.t("tx.source_type"))
        self.load_default_button.setText(self.t("tx.load_default"))
        self.load_workbook_button.setText(self.t("tx.load_workbook"))
        self.undo_button.setText(self.t("tx.undo"))
        self.redo_button.setText(self.t("tx.redo"))
        self.copy_button.setText(self.t("tx.copy"))
        self.paste_button.setText(self.t("tx.paste"))
        self.delete_selection_button.setText(self.t("tx.delete_selection"))
        self.palette_title.setText(self.t("tx.palette"))
        self.palette_search.setPlaceholderText(self.t("tx.search_nodes"))
        self.add_node_button.setText(self.t("tx.add_node"))
        self.columns_label.setText(self.t("tx.columns"))
        self.targets_label.setText(self.t("tx.targets"))
        self.canvas_title.setText(self.t("tx.canvas"))
        self.arrange_button.setText(self.t("tx.arrange"))
        self.zoom_in_button.setText(self.t("tx.zoom_in"))
        self.zoom_out_button.setText(self.t("tx.zoom_out"))
        self.reset_zoom_button.setText(self.t("tx.reset_zoom"))
        self.canvas_hint.setText(self.t("tx.canvas_hint"))
        self.inspector_title.setText(self.t("tx.inspector"))
        self.node_label.setText(self.t("tx.node"))
        self.label_label.setText(self.t("tx.label"))
        self.field_label.setText(self.t("tx.field"))
        self.mode_label.setText(self.t("tx.mode"))
        self.property_label.setText(self.t("tx.property"))
        self.submodel_label.setText(self.t("tx.submodel"))
        self.value_label.setText(self.t("tx.value"))
        self.separator_label.setText(self.t("tx.separator"))
        self.advanced_label.setText(self.t("tx.advanced_config"))
        self.apply_node_button.setText(self.t("tx.apply_node"))
        self.preview_title.setText(self.t("tx.preview"))
        self.identity_label.setText(self.t("tx.identity"))
        self.validate_button.setText(self.t("tx.validate"))
        self.save_button.setText(self.t("tx.save"))
        self.import_button.setText(self.t("tx.import"))
        self.export_button.setText(self.t("tx.export"))
        self.preview_button.setText(self.t("tx.preview_action"))
        self.generate_button.setText(self.t("tx.generate_aas"))
        self.payload_title.setText(self.t("tx.preview_payload"))
        self.trace_title.setText(self.t("tx.preview_trace"))
        self.issues_title.setText(self.t("tx.validation_issues"))
        self._refresh_palette_tooltips()
        self._refresh_canvas_tooltips()
        self._refresh_inspector_tooltips()
        self._update_rule_origin_label()
        self._update_workbook_label()
        self._update_history_buttons()
        self._populate_inspector()

    def refresh(self, *_args) -> None:
        self._update_rule_origin_label()
        self._update_workbook_label()

    def _populate_palette(self) -> None:
        self.node_palette_list.clear()
        for category, node_types in NODE_GROUPS.items():
            for node_type in node_types:
                item = QListWidgetItem(f"{category} · {node_type}")
                item.setData(Qt.ItemDataRole.UserRole, node_type)
                self.node_palette_list.addItem(item)
        self._refresh_palette_tooltips()

    def _filter_palette(self, text: str) -> None:
        needle = clean_cell(text).lower()
        for index in range(self.node_palette_list.count()):
            item = self.node_palette_list.item(index)
            node_type = clean_cell(item.data(Qt.ItemDataRole.UserRole)).lower()
            item.setHidden(bool(needle) and needle not in node_type and needle not in item.text().lower())

    def _build_view_state(self) -> dict[str, object]:
        return self.graph_adapter.collect_view_state(self.canvas_view, last_selected_node=self._selected_tx_node_id())

    def _serialize_rule_set(self, rule_set: TxRuleSet) -> str:
        return json.dumps(rule_set.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)

    def _current_source_type(self) -> str:
        return clean_cell(self.fixed_source_type or self.source_type_combo.currentText()) or "instrument_list"

    def _set_source_type_selection(self, source_type: str) -> None:
        target = clean_cell(self.fixed_source_type or source_type)
        if not target:
            return
        self.source_type_combo.blockSignals(True)
        self.source_type_combo.setCurrentText(target)
        self.source_type_combo.blockSignals(False)

    def _set_rule_origin(self, origin: str) -> None:
        self._rule_origin = origin if origin in {"saved", "default"} else "default"
        self._update_rule_origin_label()

    def _update_rule_origin_label(self) -> None:
        self.rule_origin_label.setText(self.t(f"tx.rule_origin.{self._rule_origin}"))

    def _accepts_rule_set(self, rule_set: TxRuleSet) -> bool:
        if not self.fixed_source_type:
            return True
        if clean_cell(rule_set.source_type) == self.fixed_source_type:
            return True
        self.show_warning_banner(self.t("tx.fixed_source_type_mismatch", source_type=self.fixed_source_type))
        return False

    def _load_preferred_rule_set(self, *, reset_history: bool = False) -> None:
        source_type = self._current_source_type()
        allow_saved_rules = self.prefer_saved_rules
        rule_set = self.workbench.load_tx_rule_set(
            source_type,
            allow_saved_rules=allow_saved_rules,
        )
        has_saved_rule = allow_saved_rules and self.workbench.tx_rule_store.exists(source_type=source_type)
        self._load_rule_set_into_scene(rule_set, reset_history=reset_history)
        self._set_rule_origin("saved" if has_saved_rule else "default")

    def _load_rule_set_into_scene(self, rule_set: TxRuleSet, *, reset_history: bool = False) -> None:
        self._blocking_scene_events = True
        try:
            arranged_rule_set = rule_set
            self._set_source_type_selection(rule_set.source_type)
            self.scene.tooltip_source_type = rule_set.source_type
            self.scene.tooltip_language = self.language
            self.target_catalog = source_type_targets(rule_set.source_type)
            self._refresh_palette_tooltips()
            self._refresh_target_catalog()
            self._refresh_schema_aware_inputs()
            self.canvas_view.resetTransform()
            self.graph_adapter.load_rule_set(rule_set)
            if self._rule_set_needs_auto_layout(rule_set):
                self.graph_adapter.arrange_scene()
                arranged_rule_set = self.graph_adapter.to_rule_set(
                    source_type=rule_set.source_type,
                    view_state=self._build_view_state(),
                )
            self.graph_adapter.apply_view_state(self.canvas_view, arranged_rule_set)
            self._refresh_canvas_tooltips()
            self.canvas_view.refresh_scene_bounds(center_on_contents=True)
            self.scene.clearSelection()
            self.rule_set = arranged_rule_set
            self._last_snapshot = self._serialize_rule_set(arranged_rule_set)
            if reset_history:
                self._undo_stack.clear()
                self._redo_stack.clear()
        finally:
            self._blocking_scene_events = False
        self._update_history_buttons()
        self._populate_inspector()

    def _sync_rule_set_from_scene(self, *, push_history: bool = False) -> None:
        updated = self.graph_adapter.to_rule_set(
            source_type=self._current_source_type(),
            view_state=self._build_view_state(),
        )
        snapshot = self._serialize_rule_set(updated)
        if push_history and snapshot != self._last_snapshot:
            self._undo_stack.append(self._last_snapshot)
            if len(self._undo_stack) > 60:
                self._undo_stack = self._undo_stack[-60:]
            self._redo_stack.clear()
        self.rule_set = updated
        self._last_snapshot = snapshot
        self._update_history_buttons()

    def _rule_set_needs_auto_layout(self, rule_set: TxRuleSet) -> bool:
        ui = rule_set.metadata.get("ui", {}) if isinstance(rule_set.metadata, dict) else {}
        try:
            layout_version = int(ui.get("layout_version", 0) or 0)
        except (TypeError, ValueError):
            layout_version = 0
        return layout_version < self.graph_adapter.layout_version or self._scene_has_overlapping_nodes()

    def _update_history_buttons(self) -> None:
        self.undo_button.setEnabled(bool(self._undo_stack))
        self.redo_button.setEnabled(bool(self._redo_stack))

    def _selected_scene_nodes(self) -> list[object]:
        return list(self.scene.selected_nodes())

    def _scene_has_overlapping_nodes(self) -> bool:
        nodes = list(self.scene.nodes.values())
        if len(nodes) < 2:
            return False
        for index, first in enumerate(nodes):
            first_rect = first.graphics_object.sceneBoundingRect().adjusted(-12.0, -12.0, 12.0, 12.0)
            for second in nodes[index + 1 :]:
                second_rect = second.graphics_object.sceneBoundingRect().adjusted(-12.0, -12.0, 12.0, 12.0)
                if first_rect.intersects(second_rect):
                    return True
        return False

    def _selected_scene_node(self) -> object | None:
        selected = self._selected_scene_nodes()
        return selected[0] if len(selected) == 1 else None

    def _selected_tx_node_id(self) -> str:
        node = self._selected_scene_node()
        if node is None:
            return ""
        return clean_cell(getattr(node.model, "tx_node_id", node.id)) or node.id

    def _selected_connection_item(self):
        selected = self.scene.selected_connection_items()
        return selected[0] if len(selected) == 1 else None

    def _clear_preview_panels(self) -> None:
        self.preview_payload.setPlainText("")
        self.preview_trace.setPlainText("")
        self.preview_issues.setPlainText("")

    def _set_preview_issues(self, issues: object) -> None:
        if isinstance(issues, list) and issues:
            self.preview_issues.setPlainText(json.dumps(issues, ensure_ascii=False, indent=2))
            return
        self.preview_issues.setPlainText("")

    _SOURCE_TYPE_TO_TEMPLATE: dict[str, str] = {
        "pid": "PID_template.xlsx",
        "instrument_list": "Stellenplan_template.xlsx",
        "wiring": "Klemmenplan_template.xlsx",
        "datasheet": "Datasheet_template.xlsx",
        "piping": "PID_template.xlsx",
    }

    def _default_workbook_path(self) -> Path | None:
        source_type = self._current_source_type()
        template_name = self._SOURCE_TYPE_TO_TEMPLATE.get(source_type)
        if template_name:
            from iev4pi_transformation_tool.core.standardized_templates import STANDARDIZED_TEMPLATE_DIR
            candidate = STANDARDIZED_TEMPLATE_DIR / template_name
            if candidate.exists():
                return candidate
        # Fallback: search Exports/Excel/ directory
        from iev4pi_transformation_tool.core.standardized_templates import TEMPLATE_TO_EXPORT_CATEGORY
        category = TEMPLATE_TO_EXPORT_CATEGORY.get(template_name or "", "")
        if category:
            candidate = self.workbench.resolve_results_export_dir() / "Excel" / category / template_name
            if candidate.exists():
                return candidate
        return None

    def _handle_source_type_changed(self, source_type: str) -> None:
        if clean_cell(source_type) and not self.fixed_source_type:
            self._load_preferred_rule_set(reset_history=True)
            self._reload_workbook_context()
            self._clear_preview_panels()

    def _load_default_rule_set(self) -> None:
        rule_set = build_default_uc1_rule_set(self._current_source_type())
        self._load_rule_set_into_scene(rule_set, reset_history=True)
        self._set_rule_origin("default")
        self._reload_workbook_context()
        self._clear_preview_panels()

    def _choose_workbook(self) -> None:
        start_dir = str(self._default_workbook_path().parent) if self._default_workbook_path() else str(self.workbench.workspace_root)
        chosen, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.t("tx.load_workbook"),
            start_dir,
            "Excel (*.xlsx *.xlsm *.xls);;All Files (*)",
        )
        if chosen:
            self.workbook_path = Path(chosen)
            self._reload_workbook_context()

    def _reload_workbook_context(self) -> None:
        if self.workbook_path is None:
            self.workbook_path = self._default_workbook_path()
        self.workbook_columns = []
        self.identity_keys = []
        self.column_list.clear()
        self.identity_combo.clear()
        self._refresh_schema_aware_inputs()
        if self.workbook_path is None or not self.workbook_path.exists():
            self._update_workbook_label()
            return
        try:
            sheets = self.workbench._uc1_load_excel_rows(self.workbook_path)
            primary_sheet = clean_cell(self.rule_set.primary_sheet_name) or self.workbench._uc1_primary_sheet_name(self.rule_set.source_type)
            primary_rows = sheets.get(primary_sheet, [])
            self.workbook_columns = list(primary_rows[0].keys()) if primary_rows else []
            self.column_list.addItems(self.workbook_columns)
            grouped = self.workbench._uc1_group_rows_by_identity(primary_rows)
            self.identity_keys = sorted(grouped.keys())
            if self.identity_keys:
                self.identity_combo.addItems(self.identity_keys)
        except Exception as exc:
            self.preview_issues.setPlainText(str(exc))
        self._refresh_schema_aware_inputs()
        self._update_workbook_label()

    def _update_workbook_label(self) -> None:
        if self.workbook_path is None:
            self.workbook_label.setText(self.t("tx.no_workbook"))
        else:
            self.workbook_label.setText(str(self.workbook_path))

    def _refresh_target_catalog(self) -> None:
        self.target_list.clear()
        for submodel_name in self.target_catalog.get("submodels", []):
            for property_name in self.target_catalog.get("properties_by_submodel", {}).get(submodel_name, []):
                item = QListWidgetItem(f"{submodel_name} / {property_name}")
                item.setData(Qt.ItemDataRole.UserRole, {"submodel": submodel_name, "property_name": property_name})
                self.target_list.addItem(item)

    def _refresh_schema_aware_inputs(self) -> None:
        self._set_combo_values(self.field_combo, self.workbook_columns, self.field_combo.currentText())
        self._set_combo_values(self.property_combo, self.target_catalog.get("properties", []), self.property_combo.currentText())
        self._set_combo_values(self.submodel_combo, self.target_catalog.get("submodels", []), self.submodel_combo.currentText())

    def _set_combo_values(self, combo: QComboBox, values: list[str], current: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        for value in values:
            combo.addItem(value)
        if clean_cell(current):
            combo.setCurrentText(current)
        combo.blockSignals(False)

    def _handle_palette_item_activated(self, item: QListWidgetItem) -> None:
        self._create_palette_node(clean_cell(item.data(Qt.ItemDataRole.UserRole)))

    def _add_selected_palette_node(self) -> None:
        item = self.node_palette_list.currentItem()
        if item is None:
            return
        self._create_palette_node(clean_cell(item.data(Qt.ItemDataRole.UserRole)))

    def _canvas_center_scene_pos(self) -> QPointF:
        return self.canvas_view.mapToScene(self.canvas_view.viewport().rect().center())

    def _create_palette_node(self, node_type: str) -> None:
        if not clean_cell(node_type):
            return
        self._blocking_scene_events = True
        try:
            node = self.graph_adapter.create_node(node_type, self._canvas_center_scene_pos())
            self.scene.clearSelection()
            node.graphics_object.setSelected(True)
        finally:
            self._blocking_scene_events = False
        self._refresh_canvas_tooltips()
        self.canvas_view.refresh_scene_bounds()
        self._sync_rule_set_from_scene(push_history=True)
        self._populate_inspector()

    def _create_node_at(self, node_type: str, position: QPointF) -> None:
        if not clean_cell(node_type):
            return
        self._blocking_scene_events = True
        try:
            node = self.graph_adapter.create_node(node_type, position)
            self.scene.clearSelection()
            node.graphics_object.setSelected(True)
        finally:
            self._blocking_scene_events = False
        self._refresh_canvas_tooltips()
        self.canvas_view.refresh_scene_bounds()
        self._sync_rule_set_from_scene(push_history=True)
        self._populate_inspector()

    def _handle_scene_selection_changed(self) -> None:
        if self.scene.selectedItems():
            self.canvas_view.setFocus(Qt.FocusReason.OtherFocusReason)
        self._populate_inspector()

    def _handle_scene_structure_changed(self, *_args) -> None:
        if self._blocking_scene_events:
            return
        self._sync_rule_set_from_scene(push_history=True)
        self._populate_inspector()

    def _handle_scene_node_moved(self, *_args) -> None:
        if self._blocking_scene_events:
            return
        self._pending_drag_sync = True

    def _handle_scene_dragging(self, dragging: bool) -> None:
        if dragging or self._blocking_scene_events or not self._pending_drag_sync:
            return
        self._pending_drag_sync = False
        self._sync_rule_set_from_scene(push_history=True)

    def _populate_inspector(self) -> None:
        self._updating_inspector = True
        try:
            scene_node = self._selected_scene_node()
            connection_item = self._selected_connection_item()
            selected_count = len(self._selected_scene_nodes())
            if scene_node is None:
                if selected_count > 1:
                    self.node_summary_label.setText(self.t("tx.selection_multiple", count=selected_count))
                elif connection_item is not None:
                    connection = connection_item.connection
                    self.node_summary_label.setText(
                        self.t(
                            "tx.edge_selected",
                            source=clean_cell(getattr(connection.output_node.model, "tx_node_id", connection.output_node.id)),
                            target=clean_cell(getattr(connection.input_node.model, "tx_node_id", connection.input_node.id)),
                        )
                    )
                else:
                    self.node_summary_label.setText(self.t("tx.no_node_selected"))
                self.label_input.setText("")
                self.field_combo.setCurrentText("")
                self.mode_combo.setCurrentText("join")
                self.property_combo.setCurrentText("")
                self.submodel_combo.setCurrentText("")
                self.value_input.setText("")
                self.separator_input.setText(" | ")
                self.advanced_config_edit.setPlainText("{}")
                self._set_inspector_enabled(False)
                self._refresh_inspector_tooltips()
                return

            model = scene_node.model
            self.node_summary_label.setText(
                f"{clean_cell(getattr(model, 'tx_node_id', scene_node.id))}\n{clean_cell(getattr(model, 'node_type', ''))}"
            )
            self.label_input.setText(clean_cell(getattr(model, "label_text", "")))
            config = dict(getattr(model, "config", {}))
            self.field_combo.setCurrentText(clean_cell(config.get("field", "")))
            self.mode_combo.setCurrentText(clean_cell(config.get("mode", "")) or "join")
            self.property_combo.setCurrentText(clean_cell(config.get("property_name", "")))
            self.submodel_combo.setCurrentText(clean_cell(config.get("id_short", "")))
            self.value_input.setText(clean_cell(config.get("value", "")))
            self.separator_input.setText(clean_cell(config.get("separator", "")) or " | ")
            self.advanced_config_edit.setPlainText(json.dumps(config, ensure_ascii=False, indent=2))
            self._set_inspector_enabled(True)
        finally:
            self._updating_inspector = False
        self._refresh_inspector_tooltips()

    def _set_inspector_enabled(self, enabled: bool) -> None:
        for widget in (
            self.label_input,
            self.field_combo,
            self.mode_combo,
            self.property_combo,
            self.submodel_combo,
            self.value_input,
            self.separator_input,
            self.advanced_config_edit,
            self.apply_node_button,
        ):
            widget.setEnabled(enabled)

    def _apply_node_edits(self) -> None:
        scene_node = self._selected_scene_node()
        if scene_node is None:
            self.show_warning_banner(self.t("tx.no_node_selected"))
            return
        try:
            config = json.loads(self.advanced_config_edit.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            self.show_warning_banner(self.t("tx.invalid_json", message=str(exc)))
            return
        if not isinstance(config, dict):
            self.show_warning_banner(self.t("tx.invalid_json", message="Expected an object."))
            return

        config = dict(config)
        node_type = clean_cell(getattr(scene_node.model, "node_type", ""))
        label = clean_cell(self.label_input.text())
        if node_type == "InputColumn":
            config["field"] = clean_cell(self.field_combo.currentText())
            config["mode"] = clean_cell(self.mode_combo.currentText()) or "join"
            config["separator"] = clean_cell(self.separator_input.text()) or " | "
        elif node_type == "OutputProperty":
            config["property_name"] = clean_cell(self.property_combo.currentText())
        elif node_type == "OutputSubmodel":
            config["id_short"] = clean_cell(self.submodel_combo.currentText())
        elif node_type == "Constant":
            config["value"] = clean_cell(self.value_input.text())
        else:
            if clean_cell(self.field_combo.currentText()):
                config["field"] = clean_cell(self.field_combo.currentText())
            if clean_cell(self.separator_input.text()):
                config["separator"] = clean_cell(self.separator_input.text())

        self._blocking_scene_events = True
        try:
            scene_node.model.update_label(label)
            scene_node.model.update_config(config)
            scene_node.geometry.recalculate_size()
            scene_node.graphics_object.update()
        finally:
            self._blocking_scene_events = False
        self._refresh_canvas_tooltips()
        self._sync_rule_set_from_scene(push_history=True)
        self._populate_inspector()

    def _apply_selected_column(self, _item: QListWidgetItem | None = None) -> None:
        item = self.column_list.currentItem()
        scene_node = self._selected_scene_node()
        if item is None:
            return
        column_name = item.text()
        if scene_node is not None and clean_cell(getattr(scene_node.model, "node_type", "")) == "InputColumn":
            self.field_combo.setCurrentText(column_name)
            self._apply_node_edits()
            return
        self._create_palette_node("InputColumn")
        scene_node = self._selected_scene_node()
        if scene_node is None:
            return
        self.field_combo.setCurrentText(column_name)
        self.label_input.setText(column_name)
        self._apply_node_edits()

    def _apply_selected_target(self, _item: QListWidgetItem | None = None) -> None:
        item = self.target_list.currentItem()
        if item is None:
            return
        payload = item.data(Qt.ItemDataRole.UserRole) or {}
        property_name = clean_cell(payload.get("property_name", ""))
        submodel_name = clean_cell(payload.get("submodel", ""))
        scene_node = self._selected_scene_node()
        if scene_node is not None:
            node_type = clean_cell(getattr(scene_node.model, "node_type", ""))
            if node_type == "OutputProperty":
                self.property_combo.setCurrentText(property_name)
                if not clean_cell(self.label_input.text()):
                    self.label_input.setText(property_name)
                self._apply_node_edits()
                return
            if node_type == "OutputSubmodel":
                self.submodel_combo.setCurrentText(submodel_name)
                if not clean_cell(self.label_input.text()):
                    self.label_input.setText(submodel_name)
                self._apply_node_edits()
                return
        self._create_palette_node("OutputProperty")
        scene_node = self._selected_scene_node()
        if scene_node is None:
            return
        self.property_combo.setCurrentText(property_name)
        self.label_input.setText(property_name)
        self._apply_node_edits()

    def _auto_arrange(self) -> None:
        self._blocking_scene_events = True
        try:
            self.graph_adapter.arrange_scene()
        except Exception as exc:
            self.preview_issues.setPlainText(str(exc))
            self.show_warning_banner(str(exc))
        finally:
            self._blocking_scene_events = False
        self._refresh_canvas_tooltips()
        self.canvas_view.refresh_scene_bounds(center_on_contents=True)
        self._sync_rule_set_from_scene(push_history=True)

    def _zoom_in(self) -> None:
        self.canvas_view.scale_up()
        self.canvas_view.refresh_scene_bounds()
        self._sync_rule_set_from_scene(push_history=False)

    def _zoom_out(self) -> None:
        self.canvas_view.scale_down()
        self.canvas_view.refresh_scene_bounds()
        self._sync_rule_set_from_scene(push_history=False)

    def _reset_zoom(self) -> None:
        self.canvas_view.resetTransform()
        self.canvas_view.refresh_scene_bounds(center_on_contents=True)
        self._sync_rule_set_from_scene(push_history=False)

    def _copy_selection(self) -> None:
        fragment = self.graph_adapter.export_selection()
        if not fragment:
            self.show_info_banner(self.t("tx.no_node_selected"))
            return
        self._selection_clipboard = fragment
        QApplication.clipboard().setText(json.dumps(fragment, ensure_ascii=False, indent=2))
        self.show_info_banner(self.t("tx.copied_selection"))

    def _paste_selection(self) -> None:
        fragment = self._selection_clipboard
        if not fragment:
            clipboard_text = clean_cell(QApplication.clipboard().text())
            if clipboard_text:
                try:
                    parsed = json.loads(clipboard_text)
                    if isinstance(parsed, dict) and isinstance(parsed.get("nodes"), list):
                        fragment = parsed
                except json.JSONDecodeError:
                    fragment = {}
        if not fragment:
            self.show_warning_banner(self.t("tx.nothing_to_paste"))
            return
        self._blocking_scene_events = True
        try:
            created_ids = self.graph_adapter.paste_selection(fragment, self._canvas_center_scene_pos())
            self.scene.clearSelection()
            for node in self.scene.nodes.values():
                node_id = clean_cell(getattr(node.model, "tx_node_id", node.id)) or node.id
                if node_id in created_ids:
                    node.graphics_object.setSelected(True)
        finally:
            self._blocking_scene_events = False
        if created_ids:
            self._refresh_canvas_tooltips()
            self.canvas_view.refresh_scene_bounds()
            self._sync_rule_set_from_scene(push_history=True)
            self.show_success_banner(self.t("tx.pasted_selection"))

    def _delete_selection(self) -> None:
        if not self.scene.selectedItems():
            return
        self._blocking_scene_events = True
        try:
            self.canvas_view.delete_selected()
        finally:
            self._blocking_scene_events = False
        self._refresh_canvas_tooltips()
        self.canvas_view.refresh_scene_bounds()
        self._sync_rule_set_from_scene(push_history=True)
        self._populate_inspector()

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        current = self._serialize_rule_set(self.rule_set)
        payload = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._load_rule_set_into_scene(TxRuleSet.model_validate(json.loads(payload)), reset_history=False)

    def _redo(self) -> None:
        if not self._redo_stack:
            return
        current = self._serialize_rule_set(self.rule_set)
        payload = self._redo_stack.pop()
        self._undo_stack.append(current)
        self._load_rule_set_into_scene(TxRuleSet.model_validate(json.loads(payload)), reset_history=False)

    def _validate_rule_set(self) -> None:
        self.run_background_task(
            "validate_tx_rules",
            self.title_key,
            "busy.tx_validate.body",
            "common.export_failed",
            self._handle_validate_result,
            payload={"rule_set": self._current_rule_payload()},
        )

    def _handle_validate_result(self, payload: object) -> None:
        result = payload if isinstance(payload, dict) else {}
        issues = result.get("issues", [])
        self._set_preview_issues(issues)
        if result.get("valid"):
            self.show_success_banner(self.t("tx.validation_ok"))
        else:
            self.show_warning_banner(self.t("tx.validation_has_issues"))

    def _save_rule_set(self) -> None:
        self.run_background_task(
            "save_tx_rules",
            self.title_key,
            "busy.tx_save.body",
            "common.export_failed",
            self._handle_save_result,
            payload={
                "rule_set": self._current_rule_payload(),
                "tx_rule_set_id": "",
            },
        )

    def _handle_save_result(self, payload: object) -> None:
        result = payload if isinstance(payload, dict) else {}
        self._set_preview_issues(result.get("issues", []))
        if result.get("saved"):
            self._set_rule_origin("saved")
            self.show_success_banner(self.t("tx.saved_to", path=result.get("rule_path", "")))
        else:
            self.show_warning_banner(self.t("tx.validation_has_issues"))

    def _preview_rule_set(self) -> None:
        if self.workbook_path is None or not self.workbook_path.exists():
            self.show_warning_banner(self.t("tx.no_workbook"))
            return
        self.run_background_task(
            "preview_tx_rules",
            self.title_key,
            "busy.tx_preview.body",
            "common.export_failed",
            self._handle_preview_result,
            payload={
                "source_type": self.rule_set.source_type,
                "workbook_path": str(self.workbook_path),
                "identity_key": self.identity_combo.currentText(),
                "rule_set": self._current_rule_payload(),
            },
        )

    def _handle_preview_result(self, payload: object) -> None:
        result = payload if isinstance(payload, dict) else {}
        self.preview_payload.setPlainText(json.dumps(result.get("payload", {}), ensure_ascii=False, indent=2))
        self.preview_trace.setPlainText(json.dumps(result.get("traces", []), ensure_ascii=False, indent=2))
        self._set_preview_issues(result.get("issues", []))
        if result.get("issues"):
            self.show_info_banner(self.t("tx.preview_with_issues"))

    def _suggest_rule_set(self) -> None:
        if self.workbook_path is None or not self.workbook_path.exists():
            self.show_warning_banner(self.t("tx.no_workbook"))
            return
        self.run_background_task(
            "suggest_tx_rules",
            self.title_key,
            "busy.tx_suggest.body",
            "common.export_failed",
            self._handle_suggest_result,
            payload={
                "source_type": self.rule_set.source_type,
                "workbook_path": str(self.workbook_path),
                "target_properties": self._target_properties(),
            },
        )

    def _handle_suggest_result(self, payload: object) -> None:
        result = payload if isinstance(payload, dict) else {}
        suggested = result.get("suggested_rule_set", {})
        if not suggested:
            self.show_warning_banner(self.t("tx.suggestion_failed"))
            return
        imported_rule_set = TxRuleSet.model_validate(suggested)
        if not self._accepts_rule_set(imported_rule_set):
            return
        self._undo_stack.append(self._serialize_rule_set(self.rule_set))
        self._redo_stack.clear()
        self._load_rule_set_into_scene(imported_rule_set, reset_history=False)
        self._reload_workbook_context()
        self._set_preview_issues(result.get("issues", []))
        if result.get("fallback_used"):
            self.show_info_banner(self.t("tx.suggestion_fallback"))
        else:
            self.show_success_banner(self.t("tx.suggestion_applied"))

    def _generate_aas_from_tx(self) -> None:
        if self.workbook_path is None or not self.workbook_path.exists():
            self.show_warning_banner(self.t("tx.no_workbook"))
            return
        self.run_background_task(
            "generate_uc1_aas_from_tx",
            self.title_key,
            "busy.tx_generate.body",
            "common.export_failed",
            self._handle_generate_result,
            payload={
                "source_type": self.rule_set.source_type,
                "workbook_path": str(self.workbook_path),
                "target_formats": ["json"],
                "rule_set": self._current_rule_payload(),
            },
        )

    def _handle_generate_result(self, payload: object) -> None:
        result = payload if isinstance(payload, dict) else {}
        generated_paths = result.get("generated_paths", [])
        self._set_preview_issues(result.get("issues", []))
        self.show_success_banner(self.t("tx.generated_count", count=len(generated_paths)))


    def _import_rule_set(self) -> None:
        chosen, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.t("tx.import"),
            str(self.workbench.workspace_root),
            "JSON (*.json);;All Files (*)",
        )
        if not chosen:
            return
        try:
            payload = json.loads(Path(chosen).read_text(encoding="utf-8"))
            imported_rule_set = TxRuleSet.model_validate(payload)
            if not self._accepts_rule_set(imported_rule_set):
                return
            self._undo_stack.append(self._serialize_rule_set(self.rule_set))
            self._redo_stack.clear()
            self._load_rule_set_into_scene(imported_rule_set, reset_history=False)
            self._reload_workbook_context()
            self.show_success_banner(self.t("tx.imported"))
        except Exception as exc:
            self.show_error("common.export_failed", str(exc))

    def _export_rule_set(self) -> None:
        chosen, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.t("tx.export"),
            str(self.workbench.workspace_root / f"{self.rule_set.source_type}_tx_rule.json"),
            "JSON (*.json);;All Files (*)",
        )
        if not chosen:
            return
        try:
            Path(chosen).write_text(json.dumps(self._current_rule_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.show_success_banner(self.t("tx.exported"))
        except Exception as exc:
            self.show_error("common.export_failed", str(exc))

    def _current_rule_payload(self) -> dict[str, object]:
        self._sync_rule_set_from_scene(push_history=False)
        return self.rule_set.model_dump(mode="json")

    def _target_properties(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for node in self.rule_set.nodes:
            if node.node_type != "OutputProperty":
                continue
            grouped.setdefault("properties", []).append(clean_cell(node.config.get("property_name", "")) or node.label or node.id)
        return grouped


class TxEditorPage(BasePage):
    TAB_SPECS: OrderedDict[str, dict[str, object]] = OrderedDict(
        [
            ("t1", {"label_key": "tx.tab.t1", "fixed_source_type": "pid", "attr_name": "t1_editor"}),
            ("t2", {"label_key": "tx.tab.t2", "fixed_source_type": "instrument_list", "attr_name": "t2_editor"}),
            ("t3", {"label_key": "tx.tab.t3", "fixed_source_type": "wiring", "attr_name": "t3_editor"}),
            ("t4", {"label_key": "tx.tab.t4", "fixed_source_type": "datasheet", "attr_name": "t4_editor"}),
            ("t5", {"label_key": "tx.tab.t5", "fixed_source_type": "piping", "attr_name": "t5_editor"}),
            ("tx", {"label_key": "tx.tab.tx", "fixed_source_type": None, "attr_name": "tx_editor"}),
        ]
    )

    def __init__(self, workbench: Workbench, refresh_all) -> None:
        super().__init__(
            workbench,
            refresh_all,
            "page.t1_t5_editor.title",
            "page.t1_t5_editor.subtitle",
        )
        self.root_layout.removeWidget(self.progress_card)
        self.progress_card.hide()

        self.editor_tabs = QTabWidget(self)
        self.root_layout.addWidget(self.editor_tabs, 1)
        self._editor_widgets: dict[str, TxRuleEditorWidget] = {}

        self.apply_language()
        self.refresh()

    def _enabled_tab_ids(self) -> list[str]:
        enabled: list[str] = []
        if self.workbench.settings.use_custom_t1_t5_rules:
            enabled.extend(["t1", "t2", "t3", "t4", "t5"])
        if self.workbench.settings.use_custom_tx_rules:
            enabled.append("tx")
        return enabled

    def _current_tab_id(self) -> str:
        current_widget = self.editor_tabs.currentWidget()
        for tab_id, widget in self._editor_widgets.items():
            if widget is current_widget:
                return tab_id
        return ""

    def _create_editor(self, tab_id: str) -> TxRuleEditorWidget:
        spec = self.TAB_SPECS[tab_id]
        fixed_source_type = spec["fixed_source_type"]
        editor = TxRuleEditorWidget(
            self.workbench,
            self.refresh_all,
            fixed_source_type=str(fixed_source_type) if fixed_source_type else None,
            show_source_type_picker=tab_id == "tx",
            prefer_saved_rules=True,
            title_key=str(spec["label_key"]),
            subtitle_key=self.subtitle_key,
        )
        editor.setObjectName(f"{tab_id}_rule_editor")
        setattr(self, str(spec["attr_name"]), editor)
        return editor

    def _ensure_editor(self, tab_id: str) -> TxRuleEditorWidget:
        editor = self._editor_widgets.get(tab_id)
        if editor is None:
            editor = self._create_editor(tab_id)
            self._editor_widgets[tab_id] = editor
        return editor

    def _sync_tabs(self) -> None:
        desired_tabs = self._enabled_tab_ids()
        current_tab_id = self._current_tab_id()

        self.editor_tabs.blockSignals(True)
        self.editor_tabs.clear()
        for tab_id in desired_tabs:
            spec = self.TAB_SPECS[tab_id]
            editor = self._ensure_editor(tab_id)
            editor.apply_language()
            editor.refresh()
            self.editor_tabs.addTab(editor, self.t(str(spec["label_key"])))
        self.editor_tabs.blockSignals(False)

        self.editor_tabs.setVisible(bool(desired_tabs))
        if not desired_tabs:
            return

        if current_tab_id in desired_tabs:
            self.editor_tabs.setCurrentIndex(desired_tabs.index(current_tab_id))
        else:
            self.editor_tabs.setCurrentIndex(0)

    def apply_language(self) -> None:
        super().apply_language()
        self._sync_tabs()

    def refresh(self, *_args) -> None:
        self._sync_tabs()
