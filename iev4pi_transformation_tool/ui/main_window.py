from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QCursor, QColor, QGuiApplication

from iev4pi_transformation_tool.services.workbench import Workbench
from iev4pi_transformation_tool.ui.i18n import tr
from iev4pi_transformation_tool.ui.pages import (
    ExtractionReviewPage,
    LogPage,
    ModelSettingsPage,
    PidInconsistencyPage,
    ProjectPage,
    QuickStartPage,
    SchemaDiscoveryPage,
)
from iev4pi_transformation_tool.ui.rule_editor import TransformationRuleEditorPage
from iev4pi_transformation_tool.ui.qfluent import FluentIcon as FIF
from iev4pi_transformation_tool.ui.qfluent import FluentWindow


class MainWindow(FluentWindow):
    def __init__(self, workbench: Workbench, direct_extract: bool = False) -> None:
        super().__init__()
        self._direct_extract = direct_extract
        # Force a stable dark window background and disable Win11 Mica tinting,
        # which can otherwise make the app appear blue despite dark mode.
        self.setMicaEffectEnabled(False)
        self.setCustomBackgroundColor(QColor(244, 246, 248), QColor(24, 24, 24))
        self.setMinimumSize(860, 640)
        self.workbench = workbench
        self.resize(1480, 920)

        self.quick_start_page = QuickStartPage(workbench, self.refresh_pages)
        self.quick_start_page.setObjectName("quick_start")

        self.project_page = ProjectPage(workbench, self.refresh_pages)
        self.project_page.setObjectName("project")

        self.schema_page = SchemaDiscoveryPage(workbench, self.refresh_pages)
        self.schema_page.setObjectName("schema_discovery")

        self.extraction_review_page = ExtractionReviewPage(workbench, self.refresh_pages)
        self.extraction_review_page.setObjectName("extraction_review")

        self.t1_t5_editor_page = TransformationRuleEditorPage(workbench, self.refresh_pages)
        self.t1_t5_editor_page.setObjectName("t1_t5_editor")
        self.tx_editor_page = self.t1_t5_editor_page

        self.pid_inconsistency_page = PidInconsistencyPage(
            workbench,
            self.refresh_pages,
            self.open_review_from_pid,
        )
        self.pid_inconsistency_page.setObjectName("pid_inconsistency")

        self.settings_page = ModelSettingsPage(workbench, self.refresh_pages)
        self.settings_page.setObjectName("model_settings")

        self.log_page = LogPage(workbench, self.refresh_pages)
        self.log_page.setObjectName("log")

        self.page_specs = [
            ("quick_start", self.quick_start_page, FIF.HOME, "nav.quick_start"),
            ("extraction_review", self.extraction_review_page, FIF.PLAY, "nav.extraction_review"),
            ("t1_t5_editor", self.t1_t5_editor_page, FIF.CONNECT, "nav.t1_t5_editor"),
            ("pid_inconsistency", self.pid_inconsistency_page, FIF.SEARCH, "nav.pid_inconsistency"),
            ("log", self.log_page, FIF.SYNC, "nav.log"),
            ("model_settings", self.settings_page, FIF.SETTING, "nav.model_settings"),
        ]
        self.page_spec_by_route = {route_key: (page, icon, title_key) for route_key, page, icon, title_key in self.page_specs}
        self.nav_items = {}
        for route_key, page, icon, title_key in self.page_specs:
            if route_key == "t1_t5_editor" and not self._rule_editor_enabled():
                self._ensure_page_in_stack(page)
                continue
            self.nav_items[route_key] = self.addSubInterface(page, icon, title_key)

        self.switchTo(self.extraction_review_page if self._direct_extract else self.quick_start_page)
        if not self._direct_extract:
            self.refresh_pages()

    def _rule_editor_enabled(self) -> bool:
        return bool(
            getattr(self.workbench.settings, "use_custom_t1_t5_rules", False)
            or getattr(self.workbench.settings, "use_custom_tx_rules", False)
        )

    def _ensure_page_in_stack(self, page) -> None:
        if self.stackedWidget.indexOf(page) >= 0:
            return
        page.setProperty("isStackedTransparent", False)
        self.stackedWidget.addWidget(page)

    def _nav_insert_index(self, route_key: str) -> int:
        ordered_routes = [spec_route for spec_route, _page, _icon, _title_key in self.page_specs]
        visible_before = 0
        for candidate in ordered_routes:
            if candidate == route_key:
                break
            if candidate in self.nav_items:
                visible_before += 1
        return 2 + visible_before

    def _sync_rule_editor_nav_item(self, language: str) -> None:
        route_key = "t1_t5_editor"
        page, icon, title_key = self.page_spec_by_route[route_key]
        should_show = self._rule_editor_enabled()

        if should_show and route_key not in self.nav_items:
            self._ensure_page_in_stack(page)
            title = tr(language, title_key)
            self.nav_items[route_key] = self.navigationInterface.insertItem(
                self._nav_insert_index(route_key),
                route_key,
                icon,
                title,
                onClick=lambda: self.switchTo(page),
                tooltip=title,
            )
            return

        if not should_show and route_key in self.nav_items:
            if self.stackedWidget.currentWidget() == page:
                self.switchTo(self.quick_start_page)
            self.navigationInterface.removeWidget(route_key)
            self.nav_items.pop(route_key, None)

    def refresh_pages(self) -> None:
        language = self.workbench.settings.ui_language
        self._sync_rule_editor_nav_item(language)
        self.setWindowTitle(tr(language, "app.title"))
        for route_key, page, _icon, title_key in self.page_specs:
            if route_key in self.nav_items:
                self.nav_items[route_key].setText(tr(language, title_key))
                self.nav_items[route_key].setToolTip(tr(language, title_key))
            page.apply_language()
            if route_key != "log":
                page.refresh()
        self.schema_page.apply_language()
        self.schema_page.refresh()

    def open_review_from_pid(self, payload: dict[str, object]) -> None:
        self.switchTo(self.extraction_review_page)
        self.extraction_review_page.open_component_review(payload)

    def show_default(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            geometry = self.frameGeometry()
            geometry.moveCenter(available.center())
            self.move(geometry.topLeft())
        self.showMaximized()

    def minimumSizeHint(self) -> QSize:
        return QSize(860, 640)
