from __future__ import annotations

from collections import OrderedDict

from PyQt6.QtWidgets import QTabWidget

from iev4pi_transformation_tool.services.workbench import Workbench
from iev4pi_transformation_tool.ui.pages import BasePage
from iev4pi_transformation_tool.ui.t1t5_editor import T1T5RuleEditorWidget
from iev4pi_transformation_tool.ui.tx_editor import TxRuleEditorWidget


class TransformationRuleEditorPage(BasePage):
    TAB_SPECS: OrderedDict[str, dict[str, str | None]] = OrderedDict(
        [
            ("t1", {"label_key": "tx.tab.t1", "stage_id": "t1"}),
            ("t2", {"label_key": "tx.tab.t2", "stage_id": "t2"}),
            ("t3", {"label_key": "tx.tab.t3", "stage_id": "t3"}),
            ("t4", {"label_key": "tx.tab.t4", "stage_id": "t4"}),
            ("t5", {"label_key": "tx.tab.t5", "stage_id": "t5"}),
            ("tx", {"label_key": "tx.tab.tx", "stage_id": None}),
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
        self._editor_widgets: dict[str, object] = {}

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

    def _create_editor(self, tab_id: str):
        spec = self.TAB_SPECS[tab_id]
        if tab_id == "tx":
            editor = TxRuleEditorWidget(
                self.workbench,
                self.refresh_all,
                fixed_source_type=None,
                show_source_type_picker=True,
                prefer_saved_rules=True,
                title_key="page.tx_editor.title",
                subtitle_key="page.tx_editor.subtitle",
            )
        else:
            editor = T1T5RuleEditorWidget(
                self.workbench,
                self.refresh_all,
                stage_id=str(spec["stage_id"]),
                title_key=str(spec["label_key"]),
                subtitle_key=self.subtitle_key,
            )
        editor.setObjectName(f"{tab_id}_rule_editor")
        setattr(self, f"{tab_id}_editor", editor)
        return editor

    def _ensure_editor(self, tab_id: str):
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
