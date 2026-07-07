from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QMimeData, QPointF, Qt
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from iev4pi_transformation_tool.core.utils import clean_cell, normalize_identifier
from iev4pi_transformation_tool.services.workbench import Workbench
from iev4pi_transformation_tool.t1t5 import (
    STAGE_IDS,
    T1T5RuleBundle,
    T1T5RuleProfile,
    WorkbookSignature,
    build_custom_workbook_profile,
    build_default_t1_t5_bundle,
    stage_output_fields,
)
from iev4pi_transformation_tool.ui.node_tooltips import NodeTooltipContext, build_inspector_tooltip, build_palette_node_tooltip
from iev4pi_transformation_tool.ui.pages import BasePage
from iev4pi_transformation_tool.ui.qfluent import BodyLabel, CaptionLabel, CardWidget, ComboBox, PrimaryPushButton, SearchLineEdit, StrongBodyLabel
from iev4pi_transformation_tool.ui.t1t5_graph import PALETTE_MIME_TYPE, NODE_GROUPS, T1T5FlowScene, T1T5FlowView, T1T5GraphAdapter, stage_output_targets


class T1T5PaletteList(QListWidget):
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


class T1T5RuleEditorWidget(BasePage):
    def __init__(
        self,
        workbench: Workbench,
        refresh_all,
        *,
        stage_id: str,
        title_key: str = "page.t1_t5_editor.title",
        subtitle_key: str = "page.t1_t5_editor.subtitle",
    ) -> None:
        super().__init__(workbench, refresh_all, title_key, subtitle_key)
        self.root_layout.removeWidget(self.title_label)
        self.root_layout.removeWidget(self.subtitle_label)
        self.title_label.hide()
        self.subtitle_label.hide()

        self.stage_id = normalize_identifier(stage_id) or "t1"
        if self.stage_id not in STAGE_IDS:
            raise ValueError(f"Unsupported T1-T5 stage: {stage_id}")

        self.bundle = build_default_t1_t5_bundle(self.stage_id)
        self.current_profile_id = self.bundle.default_profile_id
        self.workbook_path: Path | None = None
        self.workbook_columns: list[str] = []
        self.target_catalog = stage_output_targets(self.stage_id)
        self._updating_profile_controls = False
        self._updating_inspector = False
        self._rule_origin = "default"

        self.scene = T1T5FlowScene(self)
        self.scene.tooltip_stage_id = self.stage_id
        self.scene.tooltip_language = self.language
        self.graph_adapter = T1T5GraphAdapter(self.scene, stage_id=self.stage_id)

        self._build_toolbar()
        self._build_layout()
        self._connect_signals()

        self._populate_palette()
        self._load_preferred_bundle()
        self._reload_workbook_context()
        self.apply_language()

    @property
    def profile_map(self) -> dict[str, T1T5RuleProfile]:
        return {profile.profile_id: profile for profile in self.bundle.profiles}

    @property
    def current_profile(self) -> T1T5RuleProfile:
        profile = self.profile_map.get(self.current_profile_id)
        if profile is None:
            profile = self.bundle.profiles[0]
            self.current_profile_id = profile.profile_id
        return profile

    def _selected_node_tooltip_context(self) -> NodeTooltipContext | None:
        node = self._selected_graph_node()
        if node is None:
            return None
        model = node.model
        return NodeTooltipContext(
            language=self.language,
            editor_kind="t1t5",
            node_type=clean_cell(getattr(model, "node_type", "")),
            label=clean_cell(getattr(model, "label_text", "")),
            config=dict(getattr(model, "config", {}) or {}),
            stage_id=self.stage_id,
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
                    editor_kind="t1t5",
                    node_type=node_type,
                    stage_id=self.stage_id,
                )
            )

    def _refresh_canvas_tooltips(self) -> None:
        self.scene.tooltip_stage_id = self.stage_id
        self.scene.tooltip_language = self.language
        self.scene.refresh_node_tooltips()

    def _set_tooltip_pair(self, label_widget: QWidget, input_widget: QWidget, text: str) -> None:
        label_widget.setToolTip(text)
        input_widget.setToolTip(text)

    def _refresh_inspector_tooltips(self) -> None:
        context = self._selected_node_tooltip_context()
        node_summary_tooltip = build_inspector_tooltip(language=self.language, control_key="node_type", context=context)
        self.node_type_label.setToolTip(node_summary_tooltip)
        self.node_type_value.setToolTip(node_summary_tooltip)
        self._set_tooltip_pair(
            self.node_label,
            self.node_label_input,
            build_inspector_tooltip(language=self.language, control_key="label", context=context),
        )
        self._set_tooltip_pair(
            self.field_label,
            self.field_combo,
            build_inspector_tooltip(language=self.language, control_key="field", context=context),
        )
        self._set_tooltip_pair(
            self.value_label,
            self.value_input,
            build_inspector_tooltip(language=self.language, control_key="value", context=context),
        )
        self._set_tooltip_pair(
            self.pattern_label,
            self.pattern_input,
            build_inspector_tooltip(language=self.language, control_key="pattern", context=context),
        )
        self._set_tooltip_pair(
            self.separator_label,
            self.separator_input,
            build_inspector_tooltip(language=self.language, control_key="separator", context=context),
        )
        self._set_tooltip_pair(
            self.compare_label,
            self.compare_input,
            build_inspector_tooltip(language=self.language, control_key="compare_to", context=context),
        )
        self._set_tooltip_pair(
            self.true_label,
            self.true_input,
            build_inspector_tooltip(language=self.language, control_key="true_value", context=context),
        )
        self._set_tooltip_pair(
            self.false_label,
            self.false_input,
            build_inspector_tooltip(language=self.language, control_key="false_value", context=context),
        )
        self._set_tooltip_pair(
            self.sheet_config_label,
            self.sheet_config_input,
            build_inspector_tooltip(language=self.language, control_key="sheet_name", context=context),
        )
        self._set_tooltip_pair(
            self.field_names_label,
            self.field_names_input,
            build_inspector_tooltip(language=self.language, control_key="field_names", context=context),
        )
        mapping_tooltip = build_inspector_tooltip(language=self.language, control_key="mapping", context=context)
        self.mapping_label.setToolTip(mapping_tooltip)
        self.mapping_edit.setToolTip(mapping_tooltip)
        advanced_tooltip = build_inspector_tooltip(language=self.language, control_key="advanced", context=context)
        self.advanced_label.setToolTip(advanced_tooltip)
        self.advanced_config_edit.setToolTip(advanced_tooltip)
        self.apply_node_button.setToolTip(build_inspector_tooltip(language=self.language, control_key="apply", context=context))

    def _build_toolbar(self) -> None:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.profile_label = StrongBodyLabel("")
        toolbar.addWidget(self.profile_label)

        self.profile_combo = ComboBox(self)
        self.profile_combo.currentIndexChanged.connect(self._handle_profile_changed)
        toolbar.addWidget(self.profile_combo, 0)

        self.new_profile_button = QPushButton(self)
        self.new_profile_button.clicked.connect(self._add_profile)
        toolbar.addWidget(self.new_profile_button)

        self.duplicate_profile_button = QPushButton(self)
        self.duplicate_profile_button.clicked.connect(self._duplicate_profile)
        toolbar.addWidget(self.duplicate_profile_button)

        self.delete_profile_button = QPushButton(self)
        self.delete_profile_button.clicked.connect(self._delete_profile)
        toolbar.addWidget(self.delete_profile_button)

        self.load_default_button = QPushButton(self)
        self.load_default_button.clicked.connect(self._load_default_bundle)
        toolbar.addWidget(self.load_default_button)

        self.load_workbook_button = QPushButton(self)
        self.load_workbook_button.clicked.connect(self._choose_workbook)
        toolbar.addWidget(self.load_workbook_button)

        self.rule_origin_label = CaptionLabel("")
        self.rule_origin_label.setWordWrap(True)
        toolbar.addWidget(self.rule_origin_label)

        self.workbook_label = BodyLabel("")
        self.workbook_label.setWordWrap(True)
        toolbar.addWidget(self.workbook_label, 1)

        self.root_layout.addLayout(toolbar)

    def _build_layout(self) -> None:
        self.main_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.root_layout.addWidget(self.main_splitter, 1)

        top_splitter = QSplitter(Qt.Orientation.Horizontal, self.main_splitter)
        self.main_splitter.addWidget(top_splitter)

        self.profile_card = CardWidget(self)
        profile_layout = QVBoxLayout(self.profile_card)
        profile_layout.setContentsMargins(16, 16, 16, 16)
        profile_layout.setSpacing(10)

        self.profile_settings_title = StrongBodyLabel("")
        profile_layout.addWidget(self.profile_settings_title)

        signature_form = QFormLayout()
        signature_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.input_mode_label = QLabel("")
        self.input_mode_combo = QComboBox(self)
        self.input_mode_combo.addItem("", userData="builtin_context")
        self.input_mode_combo.addItem("", userData="custom_workbook")
        signature_form.addRow(self.input_mode_label, self.input_mode_combo)

        self.profile_title_label = QLabel("")
        self.profile_title_edit = QLineEdit(self)
        signature_form.addRow(self.profile_title_label, self.profile_title_edit)

        self.profile_desc_label = QLabel("")
        self.profile_desc_edit = QLineEdit(self)
        signature_form.addRow(self.profile_desc_label, self.profile_desc_edit)

        self.priority_label = QLabel("")
        self.priority_edit = QLineEdit(self)
        signature_form.addRow(self.priority_label, self.priority_edit)

        self.sheet_name_label = QLabel("")
        self.sheet_name_edit = QLineEdit(self)
        signature_form.addRow(self.sheet_name_label, self.sheet_name_edit)

        self.required_headers_label = QLabel("")
        self.required_headers_edit = QLineEdit(self)
        signature_form.addRow(self.required_headers_label, self.required_headers_edit)

        self.optional_headers_label = QLabel("")
        self.optional_headers_edit = QLineEdit(self)
        signature_form.addRow(self.optional_headers_label, self.optional_headers_edit)

        profile_layout.addLayout(signature_form)

        self.match_status_label = CaptionLabel("")
        self.match_status_label.setWordWrap(True)
        profile_layout.addWidget(self.match_status_label)

        self.palette_title = StrongBodyLabel("")
        profile_layout.addWidget(self.palette_title)

        self.palette_search = SearchLineEdit(self)
        self.palette_search.textChanged.connect(self._filter_palette)
        profile_layout.addWidget(self.palette_search)

        self.node_palette_list = T1T5PaletteList(self)
        self.node_palette_list.itemDoubleClicked.connect(self._handle_palette_item_activated)
        profile_layout.addWidget(self.node_palette_list, 1)

        self.add_node_button = PrimaryPushButton("", self)
        self.add_node_button.clicked.connect(self._add_selected_palette_node)
        profile_layout.addWidget(self.add_node_button)

        self.columns_label = StrongBodyLabel("")
        profile_layout.addWidget(self.columns_label)

        self.column_list = QListWidget(self)
        self.column_list.itemDoubleClicked.connect(self._apply_selected_column)
        profile_layout.addWidget(self.column_list, 1)

        self.output_fields_label = StrongBodyLabel("")
        profile_layout.addWidget(self.output_fields_label)

        self.output_fields_box = QPlainTextEdit(self)
        self.output_fields_box.setReadOnly(True)
        self.output_fields_box.setMaximumHeight(140)
        profile_layout.addWidget(self.output_fields_box)
        top_splitter.addWidget(self.profile_card)

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

        self.delete_selection_button = QPushButton(self)
        self.delete_selection_button.clicked.connect(self._delete_selection)
        canvas_toolbar.addWidget(self.delete_selection_button)

        self.canvas_hint = BodyLabel("")
        self.canvas_hint.setWordWrap(True)
        canvas_toolbar.addWidget(self.canvas_hint, 1)
        canvas_layout.addLayout(canvas_toolbar)

        self.canvas_view = T1T5FlowView(self.scene, self)
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

        self.node_type_label = QLabel("")
        self.node_type_value = QLabel("")
        self.node_type_value.setWordWrap(True)
        form.addRow(self.node_type_label, self.node_type_value)

        self.node_label = QLabel("")
        self.node_label_input = QLineEdit(self)
        form.addRow(self.node_label, self.node_label_input)

        self.field_label = QLabel("")
        self.field_combo = QComboBox(self)
        self.field_combo.setEditable(True)
        self.field_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        form.addRow(self.field_label, self.field_combo)

        self.value_label = QLabel("")
        self.value_input = QLineEdit(self)
        form.addRow(self.value_label, self.value_input)

        self.pattern_label = QLabel("")
        self.pattern_input = QLineEdit(self)
        form.addRow(self.pattern_label, self.pattern_input)

        self.separator_label = QLabel("")
        self.separator_input = QLineEdit(self)
        form.addRow(self.separator_label, self.separator_input)

        self.compare_label = QLabel("")
        self.compare_input = QLineEdit(self)
        form.addRow(self.compare_label, self.compare_input)

        self.true_label = QLabel("")
        self.true_input = QLineEdit(self)
        form.addRow(self.true_label, self.true_input)

        self.false_label = QLabel("")
        self.false_input = QLineEdit(self)
        form.addRow(self.false_label, self.false_input)

        self.sheet_config_label = QLabel("")
        self.sheet_config_input = QLineEdit(self)
        form.addRow(self.sheet_config_label, self.sheet_config_input)

        self.field_names_label = QLabel("")
        self.field_names_input = QLineEdit(self)
        form.addRow(self.field_names_label, self.field_names_input)
        inspector_layout.addLayout(form)

        self.mapping_label = StrongBodyLabel("")
        inspector_layout.addWidget(self.mapping_label)

        self.mapping_edit = QPlainTextEdit(self)
        self.mapping_edit.setMinimumHeight(120)
        inspector_layout.addWidget(self.mapping_edit)

        self.advanced_label = StrongBodyLabel("")
        inspector_layout.addWidget(self.advanced_label)

        self.advanced_config_edit = QPlainTextEdit(self)
        self.advanced_config_edit.setMinimumHeight(150)
        inspector_layout.addWidget(self.advanced_config_edit)

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

        self.validate_button = QPushButton(self)
        self.validate_button.clicked.connect(self._validate_bundle)
        preview_toolbar.addWidget(self.validate_button, 0)

        self.save_button = QPushButton(self)
        self.save_button.clicked.connect(self._save_bundle)
        preview_toolbar.addWidget(self.save_button, 0)

        self.import_button = QPushButton(self)
        self.import_button.clicked.connect(self._import_bundle)
        preview_toolbar.addWidget(self.import_button, 0)

        self.export_button = QPushButton(self)
        self.export_button.clicked.connect(self._export_bundle)
        preview_toolbar.addWidget(self.export_button, 0)

        self.preview_button = PrimaryPushButton("", self)
        self.preview_button.clicked.connect(self._preview_bundle)
        preview_toolbar.addWidget(self.preview_button, 0)
        preview_toolbar.addStretch(1)
        preview_layout.addLayout(preview_toolbar)

        preview_splitter = QSplitter(Qt.Orientation.Horizontal, self.preview_card)

        rows_card = CardWidget(self.preview_card)
        rows_layout = QVBoxLayout(rows_card)
        rows_layout.setContentsMargins(12, 12, 12, 12)
        rows_layout.setSpacing(8)
        self.rows_title = StrongBodyLabel("")
        rows_layout.addWidget(self.rows_title)
        self.preview_rows = QPlainTextEdit(self.preview_card)
        self.preview_rows.setReadOnly(True)
        rows_layout.addWidget(self.preview_rows, 1)
        preview_splitter.addWidget(rows_card)

        match_card = CardWidget(self.preview_card)
        match_layout = QVBoxLayout(match_card)
        match_layout.setContentsMargins(12, 12, 12, 12)
        match_layout.setSpacing(8)
        self.match_title = StrongBodyLabel("")
        match_layout.addWidget(self.match_title)
        self.preview_match = QPlainTextEdit(self.preview_card)
        self.preview_match.setReadOnly(True)
        match_layout.addWidget(self.preview_match, 1)
        preview_splitter.addWidget(match_card)

        issues_card = CardWidget(self.preview_card)
        issues_layout = QVBoxLayout(issues_card)
        issues_layout.setContentsMargins(12, 12, 12, 12)
        issues_layout.setSpacing(8)
        self.issues_title = StrongBodyLabel("")
        issues_layout.addWidget(self.issues_title)
        self.preview_issues = QPlainTextEdit(self.preview_card)
        self.preview_issues.setReadOnly(True)
        issues_layout.addWidget(self.preview_issues, 1)
        preview_splitter.addWidget(issues_card)

        preview_layout.addWidget(preview_splitter, 1)

        self.main_splitter.addWidget(self.preview_card)
        self.main_splitter.setSizes([720, 300])
        top_splitter.setSizes([340, 820, 360])

    def _connect_signals(self) -> None:
        self.scene.selectionChanged.connect(self._handle_scene_selection_changed)
        self.profile_title_edit.editingFinished.connect(self._apply_profile_metadata)
        self.profile_desc_edit.editingFinished.connect(self._apply_profile_metadata)
        self.priority_edit.editingFinished.connect(self._apply_profile_metadata)
        self.sheet_name_edit.editingFinished.connect(self._apply_profile_signature)
        self.required_headers_edit.editingFinished.connect(self._apply_profile_signature)
        self.optional_headers_edit.editingFinished.connect(self._apply_profile_signature)
        self.input_mode_combo.currentIndexChanged.connect(self._apply_profile_signature)

    def _populate_palette(self) -> None:
        self.node_palette_list.clear()
        for group_name, node_types in NODE_GROUPS.items():
            group_item = QListWidgetItem(f"[{group_name}]")
            group_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.node_palette_list.addItem(group_item)
            for node_type in node_types:
                item = QListWidgetItem(node_type)
                item.setData(Qt.ItemDataRole.UserRole, node_type)
                self.node_palette_list.addItem(item)
        self._refresh_palette_tooltips()

    def _filter_palette(self, text: str) -> None:
        needle = clean_cell(text).lower()
        for index in range(self.node_palette_list.count()):
            item = self.node_palette_list.item(index)
            node_type = clean_cell(item.data(Qt.ItemDataRole.UserRole))
            if not node_type:
                item.setHidden(False)
                continue
            item.setHidden(bool(needle) and needle not in node_type.lower())

    def _load_preferred_bundle(self) -> None:
        self.bundle = self.workbench.load_t1_t5_rule_bundle(
            self.stage_id,
            allow_saved_rules=True,
        )
        self.current_profile_id = clean_cell(self.bundle.default_profile_id) or self.bundle.profiles[0].profile_id
        self._rule_origin = "saved" if self.workbench.t1_t5_rule_store.exists(stage_id=self.stage_id) else "default"
        self._refresh_profile_combo()
        self._load_profile_into_scene(self.current_profile)
        self._set_rule_origin(self._rule_origin)

    def _set_rule_origin(self, origin: str) -> None:
        self._rule_origin = origin
        key = "t1t5.rule_origin.saved" if origin == "saved" else "t1t5.rule_origin.default"
        self.rule_origin_label.setText(self.t(key))

    def _refresh_profile_combo(self) -> None:
        current_id = self.current_profile_id
        self.profile_combo.blockSignals(True)
        try:
            self.profile_combo.clear()
            for profile in self.bundle.profiles:
                self.profile_combo.addItem(profile.title or profile.profile_id, userData=profile.profile_id)
            for index in range(self.profile_combo.count()):
                if clean_cell(self.profile_combo.itemData(index)) == current_id:
                    self.profile_combo.setCurrentIndex(index)
                    break
        finally:
            self.profile_combo.blockSignals(False)
        self.output_fields_box.setPlainText("\n".join(self.current_profile.output_fields))

    def _handle_profile_changed(self, *_args) -> None:
        profile_id = clean_cell(self.profile_combo.currentData() or "")
        if not profile_id or profile_id == self.current_profile_id:
            return
        self._sync_current_profile_from_scene()
        self.current_profile_id = profile_id
        self._load_profile_into_scene(self.current_profile)

    def _load_profile_into_scene(self, profile: T1T5RuleProfile) -> None:
        self.current_profile_id = profile.profile_id
        self.scene.tooltip_stage_id = self.stage_id
        self.scene.tooltip_language = self.language
        self.graph_adapter.load_profile(profile)
        if self._profile_needs_auto_layout(profile):
            self.graph_adapter.arrange_scene()
            self._sync_current_profile_from_scene()
            profile = self.current_profile
        self._populate_profile_controls(profile)
        self._populate_column_list()
        self._populate_inspector()
        self.graph_adapter.apply_view_state(self.canvas_view, self.current_profile)
        self._refresh_canvas_tooltips()
        self.canvas_view.refresh_scene_bounds(center_on_contents=True)
        self.output_fields_box.setPlainText("\n".join(profile.output_fields))

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

    def _profile_needs_auto_layout(self, profile: T1T5RuleProfile) -> bool:
        ui = profile.metadata.get("ui", {}) if isinstance(profile.metadata, dict) else {}
        try:
            layout_version = int(ui.get("layout_version", 0) or 0)
        except (TypeError, ValueError):
            layout_version = 0
        return layout_version < self.graph_adapter.layout_version or self._scene_has_overlapping_nodes()

    def _populate_profile_controls(self, profile: T1T5RuleProfile) -> None:
        self._updating_profile_controls = True
        try:
            self.profile_title_edit.setText(profile.title)
            self.profile_desc_edit.setText(profile.description)
            self.priority_edit.setText(str(profile.priority))
            for index in range(self.input_mode_combo.count()):
                if clean_cell(self.input_mode_combo.itemData(index)) == profile.input_mode:
                    self.input_mode_combo.setCurrentIndex(index)
                    break
            self.sheet_name_edit.setText(profile.workbook_signature.sheet_name)
            self.required_headers_edit.setText(", ".join(profile.workbook_signature.required_headers))
            self.optional_headers_edit.setText(", ".join(profile.workbook_signature.optional_headers))
        finally:
            self._updating_profile_controls = False
        self._update_match_status()

    def _apply_profile_metadata(self) -> None:
        if self._updating_profile_controls:
            return
        self._sync_current_profile_from_scene()
        profile = self.current_profile.model_copy(deep=True)
        profile.title = self.profile_title_edit.text().strip()
        profile.description = self.profile_desc_edit.text().strip()
        profile.priority = int(self.priority_edit.text().strip() or "100")
        self._replace_current_profile(profile)

    def _apply_profile_signature(self) -> None:
        if self._updating_profile_controls:
            return
        self._sync_current_profile_from_scene()
        profile = self.current_profile.model_copy(deep=True)
        profile.input_mode = str(self.input_mode_combo.currentData() or "builtin_context")
        profile.workbook_signature = WorkbookSignature(
            workbook_kind=profile.workbook_signature.workbook_kind,
            sheet_name=self.sheet_name_edit.text().strip(),
            required_headers=[item.strip() for item in self.required_headers_edit.text().split(",") if item.strip()],
            optional_headers=[item.strip() for item in self.optional_headers_edit.text().split(",") if item.strip()],
            header_fingerprint=profile.workbook_signature.header_fingerprint,
            source_root=profile.workbook_signature.source_root,
        )
        self._replace_current_profile(profile)
        self._update_match_status()

    def _replace_current_profile(self, profile: T1T5RuleProfile) -> None:
        updated = []
        for existing in self.bundle.profiles:
            updated.append(profile if existing.profile_id == profile.profile_id else existing)
        self.bundle = self.bundle.model_copy(update={"profiles": updated})
        self.current_profile_id = profile.profile_id
        if not clean_cell(self.bundle.default_profile_id):
            self.bundle.default_profile_id = profile.profile_id
        self._refresh_profile_combo()

    def _sync_current_profile_from_scene(self) -> None:
        current = self.current_profile.model_copy(deep=True)
        scene_profile = self.graph_adapter.to_profile(
            view_state=self.graph_adapter.collect_view_state(
                self.canvas_view,
                last_selected_node=self._selected_node_id(),
            )
        )
        current.nodes = scene_profile.nodes
        current.edges = scene_profile.edges
        current.metadata = scene_profile.metadata
        self._replace_current_profile(current)

    def _add_profile(self) -> None:
        self._sync_current_profile_from_scene()
        base_id = normalize_identifier(f"{self.stage_id}_custom_{len(self.bundle.profiles) + 1}") or "custom"
        existing_ids = set(self.profile_map)
        candidate = base_id
        index = 2
        while candidate in existing_ids:
            candidate = f"{base_id}_{index}"
            index += 1
        profile = build_custom_workbook_profile(self.stage_id, profile_id=candidate, title=f"{self.stage_id.upper()} custom {len(self.bundle.profiles) + 1}")
        self.bundle = self.bundle.model_copy(update={"profiles": [*self.bundle.profiles, profile]})
        self.current_profile_id = profile.profile_id
        self._refresh_profile_combo()
        self._load_profile_into_scene(profile)

    def _duplicate_profile(self) -> None:
        self._sync_current_profile_from_scene()
        source = self.current_profile.model_copy(deep=True)
        base_id = normalize_identifier(f"{source.profile_id}_copy") or "copy"
        existing_ids = set(self.profile_map)
        candidate = base_id
        index = 2
        while candidate in existing_ids:
            candidate = f"{base_id}_{index}"
            index += 1
        source.profile_id = candidate
        source.title = f"{source.title or source.profile_id} Copy"
        self.bundle = self.bundle.model_copy(update={"profiles": [*self.bundle.profiles, source]})
        self.current_profile_id = source.profile_id
        self._refresh_profile_combo()
        self._load_profile_into_scene(source)

    def _delete_profile(self) -> None:
        if len(self.bundle.profiles) <= 1:
            self.show_warning_banner(self.t("t1t5.minimum_one_profile"))
            return
        remaining = [profile for profile in self.bundle.profiles if profile.profile_id != self.current_profile_id]
        self.bundle = self.bundle.model_copy(update={"profiles": remaining})
        self.current_profile_id = remaining[0].profile_id
        if self.bundle.default_profile_id not in {profile.profile_id for profile in remaining}:
            self.bundle.default_profile_id = remaining[0].profile_id
        self._refresh_profile_combo()
        self._load_profile_into_scene(self.current_profile)

    def _load_default_bundle(self) -> None:
        self.bundle = build_default_t1_t5_bundle(self.stage_id)
        self.current_profile_id = self.bundle.default_profile_id
        self._rule_origin = "default"
        self._refresh_profile_combo()
        self._load_profile_into_scene(self.current_profile)
        self._set_rule_origin("default")
        self.preview_rows.clear()
        self.preview_match.clear()
        self.preview_issues.clear()

    def _choose_workbook(self) -> None:
        chosen, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.t("t1t5.load_workbook"),
            str(self.workbench.workspace_root),
            "Excel (*.xlsx *.xls *.xlsm);;All Files (*)",
        )
        if not chosen:
            return
        self.workbook_path = Path(chosen)
        self._reload_workbook_context()
        self._update_match_status()

    def _reload_workbook_context(self) -> None:
        self.workbook_columns = []
        if self.workbook_path is not None and self.workbook_path.exists():
            try:
                sheets = self.workbench._uc1_load_excel_rows(self.workbook_path)
            except Exception as exc:
                self.workbook_label.setText(str(exc))
                self._populate_column_list()
                return
            seen: list[str] = []
            for rows in sheets.values():
                if not rows:
                    continue
                for key in rows[0].keys():
                    cleaned = clean_cell(key)
                    if cleaned and cleaned not in seen:
                        seen.append(cleaned)
            self.workbook_columns = seen
            self.workbook_label.setText(str(self.workbook_path.name))
        else:
            self.workbook_label.setText(self.t("t1t5.no_workbook_loaded"))
        self._populate_column_list()

    def _populate_column_list(self) -> None:
        self.column_list.clear()
        values = self.workbook_columns if self.workbook_columns else self.current_profile.output_fields
        for field_name in values:
            self.column_list.addItem(field_name)
        self.field_combo.clear()
        for field_name in values:
            self.field_combo.addItem(field_name)

    def _update_match_status(self) -> None:
        if self.workbook_path is None or not self.workbook_path.exists():
            self.match_status_label.setText(self.t("t1t5.match_status.no_workbook"))
            return
        try:
            result = self.workbench.resolve_t1_t5_profile(
                self.stage_id,
                workbook_path=self.workbook_path,
                bundle_payload=self._current_bundle_payload(),
                profile_id=self.current_profile_id,
            )
        except Exception as exc:
            self.match_status_label.setText(str(exc))
            return
        score = float(result.get("score", 0.0) or 0.0)
        sheet = clean_cell(result.get("matched_sheet_name", ""))
        self.match_status_label.setText(self.t("t1t5.match_status.summary", score=f"{score:.2f}", sheet=sheet or "-"))

    def _handle_palette_item_activated(self, item: QListWidgetItem) -> None:
        node_type = clean_cell(item.data(Qt.ItemDataRole.UserRole))
        if node_type:
            self._create_node_at(node_type, self._canvas_center_scene_pos())

    def _add_selected_palette_node(self) -> None:
        item = self.node_palette_list.currentItem()
        if item is None:
            return
        node_type = clean_cell(item.data(Qt.ItemDataRole.UserRole))
        if node_type:
            self._create_node_at(node_type, self._canvas_center_scene_pos())

    def _canvas_center_scene_pos(self) -> QPointF:
        viewport_rect = self.canvas_view.viewport().rect()
        return self.canvas_view.mapToScene(viewport_rect.center())

    def _create_node_at(self, node_type: str, position: QPointF) -> None:
        self.graph_adapter.create_node(node_type, position)
        self._refresh_canvas_tooltips()
        self.canvas_view.refresh_scene_bounds()
        self._sync_current_profile_from_scene()
        self._populate_inspector()

    def _auto_arrange(self) -> None:
        self.graph_adapter.arrange_scene()
        self._refresh_canvas_tooltips()
        self.canvas_view.refresh_scene_bounds(center_on_contents=True)
        self._sync_current_profile_from_scene()

    def _zoom_in(self) -> None:
        self.canvas_view.scale(1.15, 1.15)
        self.canvas_view.refresh_scene_bounds()

    def _zoom_out(self) -> None:
        self.canvas_view.scale(1 / 1.15, 1 / 1.15)
        self.canvas_view.refresh_scene_bounds()

    def _reset_zoom(self) -> None:
        self.canvas_view.resetTransform()
        self.canvas_view.refresh_scene_bounds(center_on_contents=True)

    def _delete_selection(self) -> None:
        if not self.scene.selectedItems():
            return
        self.canvas_view.delete_selected()
        self._refresh_canvas_tooltips()
        self.canvas_view.refresh_scene_bounds()
        self._sync_current_profile_from_scene()
        self._populate_inspector()

    def _selected_graph_node(self):
        selected = self.scene.selected_nodes()
        return selected[0] if selected else None

    def _selected_node_id(self) -> str:
        node = self._selected_graph_node()
        if node is None:
            return ""
        return clean_cell(getattr(node.model, "t1t5_node_id", node.id)) or node.id

    def _handle_scene_selection_changed(self) -> None:
        self._populate_inspector()

    def _populate_inspector(self) -> None:
        node = self._selected_graph_node()
        if node is None:
            self.node_type_value.setText(self.t("t1t5.no_node_selected"))
            self.node_label_input.clear()
            self.value_input.clear()
            self.pattern_input.clear()
            self.separator_input.clear()
            self.compare_input.clear()
            self.true_input.clear()
            self.false_input.clear()
            self.sheet_config_input.clear()
            self.field_names_input.clear()
            self.mapping_edit.clear()
            self.advanced_config_edit.clear()
            self._refresh_inspector_tooltips()
            return
        model = node.model
        config = dict(getattr(model, "config", {}))
        self._updating_inspector = True
        try:
            self.node_type_value.setText(getattr(model, "node_type", ""))
            self.node_label_input.setText(getattr(model, "label_text", ""))
            self.field_combo.setCurrentText(clean_cell(config.get("field", "")))
            self.value_input.setText(clean_cell(config.get("value", "")))
            self.pattern_input.setText(clean_cell(config.get("pattern", "")))
            self.separator_input.setText(clean_cell(config.get("separator", "")))
            self.compare_input.setText(clean_cell(config.get("compare_to", "")))
            self.true_input.setText(clean_cell(config.get("true_value", "")))
            self.false_input.setText(clean_cell(config.get("false_value", "")))
            self.sheet_config_input.setText(clean_cell(config.get("sheet_name", "")))
            self.field_names_input.setText(", ".join(config.get("field_names", [])) if isinstance(config.get("field_names", []), list) else "")
            mapping = config.get("mapping", {})
            self.mapping_edit.setPlainText(json.dumps(mapping if isinstance(mapping, dict) else {}, ensure_ascii=False, indent=2))
            self.advanced_config_edit.setPlainText(json.dumps(config, ensure_ascii=False, indent=2))
        finally:
            self._updating_inspector = False
        self._refresh_inspector_tooltips()

    def _apply_node_edits(self) -> None:
        if self._updating_inspector:
            return
        node = self._selected_graph_node()
        if node is None:
            return
        model = node.model
        config = dict(getattr(model, "config", {}))
        config["field"] = self.field_combo.currentText().strip()
        config["value"] = self.value_input.text().strip()
        config["pattern"] = self.pattern_input.text().strip()
        config["separator"] = self.separator_input.text().strip()
        config["compare_to"] = self.compare_input.text().strip()
        config["true_value"] = self.true_input.text().strip()
        config["false_value"] = self.false_input.text().strip()
        config["sheet_name"] = self.sheet_config_input.text().strip()
        field_names = [item.strip() for item in self.field_names_input.text().split(",") if item.strip()]
        if field_names:
            config["field_names"] = field_names
        try:
            mapping = json.loads(self.mapping_edit.toPlainText().strip() or "{}")
            if isinstance(mapping, dict):
                config["mapping"] = mapping
        except json.JSONDecodeError:
            pass
        try:
            advanced = json.loads(self.advanced_config_edit.toPlainText().strip() or "{}")
            if isinstance(advanced, dict):
                config.update(advanced)
        except json.JSONDecodeError:
            pass
        model.update_label(self.node_label_input.text().strip())
        model.update_config(config)
        self._sync_current_profile_from_scene()
        self._load_profile_into_scene(self.current_profile)

    def _apply_selected_column(self, item: QListWidgetItem) -> None:
        if self._selected_graph_node() is None:
            return
        self.field_combo.setCurrentText(item.text())
        self._apply_node_edits()

    def _set_preview_issues(self, issues: list[dict[str, object]]) -> None:
        self.preview_issues.setPlainText(json.dumps(issues or [], ensure_ascii=False, indent=2))

    def _validate_bundle(self) -> None:
        self._sync_current_profile_from_scene()
        result = self.workbench.validate_t1_t5_rules(self.stage_id, self._current_bundle_payload())
        self._set_preview_issues(result.get("issues", []))
        if result.get("valid"):
            self.show_success_banner(self.t("t1t5.validation_ok"))
        else:
            self.show_warning_banner(self.t("t1t5.validation_has_issues"))

    def _save_bundle(self) -> None:
        self._sync_current_profile_from_scene()
        result = self.workbench.save_t1_t5_rules(self.stage_id, self._current_bundle_payload())
        self._set_preview_issues(result.get("issues", []))
        if result.get("saved"):
            self._set_rule_origin("saved")
            self.show_success_banner(self.t("t1t5.saved_to", path=result.get("rule_path", "")))
        else:
            self.show_warning_banner(self.t("t1t5.validation_has_issues"))

    def _preview_bundle(self) -> None:
        self._sync_current_profile_from_scene()
        workbook_path = self.workbook_path if self.workbook_path and self.workbook_path.exists() else None
        result = self.workbench.preview_t1_t5_rules(
            self.stage_id,
            workbook_path=workbook_path,
            profile_id=self.current_profile_id,
            bundle_payload=self._current_bundle_payload(),
        )
        self.preview_rows.setPlainText(json.dumps(result.get("output_rows", []), ensure_ascii=False, indent=2))
        self.preview_match.setPlainText(json.dumps(result.get("profile_match", {}), ensure_ascii=False, indent=2))
        self._set_preview_issues(result.get("issues", []))
        if result.get("issues"):
            self.show_info_banner(self.t("t1t5.preview_with_issues"))

    def _import_bundle(self) -> None:
        chosen, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.t("t1t5.import"),
            str(self.workbench.workspace_root),
            "JSON (*.json);;All Files (*)",
        )
        if not chosen:
            return
        try:
            payload = json.loads(Path(chosen).read_text(encoding="utf-8"))
            bundle = T1T5RuleBundle.model_validate(payload)
        except Exception as exc:
            self.show_error("common.export_failed", str(exc))
            return
        self.bundle = bundle
        self.current_profile_id = clean_cell(bundle.default_profile_id) or bundle.profiles[0].profile_id
        self._refresh_profile_combo()
        self._load_profile_into_scene(self.current_profile)
        self.show_success_banner(self.t("t1t5.imported"))

    def _export_bundle(self) -> None:
        self._sync_current_profile_from_scene()
        chosen, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.t("t1t5.export"),
            str(self.workbench.workspace_root / f"{self.stage_id}_rules.json"),
            "JSON (*.json);;All Files (*)",
        )
        if not chosen:
            return
        try:
            Path(chosen).write_text(json.dumps(self._current_bundle_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
            self.show_success_banner(self.t("t1t5.exported"))
        except Exception as exc:
            self.show_error("common.export_failed", str(exc))

    def _current_bundle_payload(self) -> dict[str, object]:
        return self.bundle.model_dump(mode="json")

    def apply_language(self) -> None:
        super().apply_language()
        self.scene.tooltip_language = self.language
        self.profile_label.setText(self.t("t1t5.profile"))
        self.new_profile_button.setText(self.t("t1t5.new_profile"))
        self.duplicate_profile_button.setText(self.t("t1t5.duplicate_profile"))
        self.delete_profile_button.setText(self.t("t1t5.delete_profile"))
        self.load_default_button.setText(self.t("t1t5.load_builtin"))
        self.load_workbook_button.setText(self.t("t1t5.load_workbook"))
        self.profile_settings_title.setText(self.t("t1t5.profile_settings"))
        self.input_mode_label.setText(self.t("t1t5.input_mode"))
        self.input_mode_combo.setItemText(0, self.t("t1t5.input_mode_builtin"))
        self.input_mode_combo.setItemText(1, self.t("t1t5.input_mode_custom_workbook"))
        self.profile_title_label.setText(self.t("t1t5.profile_title"))
        self.profile_desc_label.setText(self.t("t1t5.profile_description"))
        self.priority_label.setText(self.t("t1t5.priority"))
        self.sheet_name_label.setText(self.t("t1t5.sheet_name"))
        self.required_headers_label.setText(self.t("t1t5.required_headers"))
        self.optional_headers_label.setText(self.t("t1t5.optional_headers"))
        self.palette_title.setText(self.t("t1t5.palette"))
        self.palette_search.setPlaceholderText(self.t("t1t5.search_nodes"))
        self.add_node_button.setText(self.t("t1t5.add_node"))
        self.columns_label.setText(self.t("t1t5.columns"))
        self.output_fields_label.setText(self.t("t1t5.output_fields"))
        self.canvas_title.setText(self.t("t1t5.canvas"))
        self.arrange_button.setText(self.t("t1t5.arrange"))
        self.zoom_in_button.setText(self.t("t1t5.zoom_in"))
        self.zoom_out_button.setText(self.t("t1t5.zoom_out"))
        self.reset_zoom_button.setText(self.t("t1t5.reset_zoom"))
        self.delete_selection_button.setText(self.t("t1t5.delete_selection"))
        self.canvas_hint.setText(self.t("t1t5.canvas_hint"))
        self.inspector_title.setText(self.t("t1t5.inspector"))
        self.node_type_label.setText(self.t("t1t5.node_type"))
        self.node_label.setText(self.t("t1t5.node_label"))
        self.field_label.setText(self.t("t1t5.field"))
        self.value_label.setText(self.t("t1t5.value"))
        self.pattern_label.setText(self.t("t1t5.pattern"))
        self.separator_label.setText(self.t("t1t5.separator"))
        self.compare_label.setText(self.t("t1t5.compare_to"))
        self.true_label.setText(self.t("t1t5.true_value"))
        self.false_label.setText(self.t("t1t5.false_value"))
        self.sheet_config_label.setText(self.t("t1t5.sheet_name"))
        self.field_names_label.setText(self.t("t1t5.field_names"))
        self.mapping_label.setText(self.t("t1t5.mapping"))
        self.advanced_label.setText(self.t("t1t5.advanced"))
        self.apply_node_button.setText(self.t("t1t5.apply_node"))
        self.preview_title.setText(self.t("t1t5.preview"))
        self.validate_button.setText(self.t("t1t5.validate"))
        self.save_button.setText(self.t("t1t5.save"))
        self.import_button.setText(self.t("t1t5.import"))
        self.export_button.setText(self.t("t1t5.export"))
        self.preview_button.setText(self.t("t1t5.preview_button"))
        self.rows_title.setText(self.t("t1t5.preview_rows"))
        self.match_title.setText(self.t("t1t5.match_title"))
        self.issues_title.setText(self.t("t1t5.issues"))
        self._refresh_palette_tooltips()
        self._refresh_canvas_tooltips()
        self._refresh_inspector_tooltips()
        self._set_rule_origin(self._rule_origin)
        self._update_match_status()

    def refresh(self, *_args) -> None:
        self._refresh_profile_combo()
        self._update_match_status()
