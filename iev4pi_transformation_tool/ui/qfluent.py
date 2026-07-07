from __future__ import annotations

import contextlib
import importlib
import io
import os
import platform
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, pyqtProperty
from PyQt6.QtGui import QAction, QColor, QGuiApplication, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QAbstractButton, QApplication, QHBoxLayout, QTableWidgetItem, QWidget


def _load_qfluentwidgets():
    workspace_root = Path(__file__).resolve().parents[2]
    required_attrs = {
        "BodyLabel",
        "StrongBodyLabel",
        "CaptionLabel",
        "SubtitleLabel",
        "PrimaryPushButton",
        "SearchLineEdit",
        "TableWidget",
        "ComboBox",
        "FluentWindow",
    }

    def _looks_usable(module) -> bool:
        return all(hasattr(module, attr) for attr in required_attrs)

    original_module = sys.modules.pop("qfluentwidgets", None)
    original_sys_path = list(sys.path)
    try:
        filtered_sys_path: list[str] = []
        for entry in original_sys_path:
            resolved = Path(entry or ".").resolve()
            if resolved == workspace_root:
                continue
            filtered_sys_path.append(entry)
        sys.path = filtered_sys_path
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                importlib.invalidate_caches()
                module = importlib.import_module("qfluentwidgets")
            if _looks_usable(module):
                return module
        except Exception:
            pass
    finally:
        sys.path = original_sys_path
        if original_module is not None:
            sys.modules["qfluentwidgets"] = original_module
        else:
            sys.modules.pop("qfluentwidgets", None)

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        module = importlib.import_module("qfluentwidgets")
    if _looks_usable(module):
        return module
    raise ImportError(
        "Unable to load a compatible 'qfluentwidgets' package. "
        "A local workspace directory named 'qfluentwidgets' may be shadowing the installed dependency."
    )


_qfluentwidgets = _load_qfluentwidgets()

BodyLabel = _qfluentwidgets.BodyLabel
CaptionLabel = _qfluentwidgets.CaptionLabel
CardWidget = _qfluentwidgets.CardWidget
FluentIcon = _qfluentwidgets.FluentIcon
InfoBar = _qfluentwidgets.InfoBar
InfoBarPosition = _qfluentwidgets.InfoBarPosition
IndeterminateProgressRing = _qfluentwidgets.IndeterminateProgressRing
ProgressBar = _qfluentwidgets.ProgressBar
StrongBodyLabel = _qfluentwidgets.StrongBodyLabel
SubtitleLabel = _qfluentwidgets.SubtitleLabel
Theme = _qfluentwidgets.Theme
ToolButton = _qfluentwidgets.ToolButton
isDarkTheme = _qfluentwidgets.isDarkTheme
setTheme = _qfluentwidgets.setTheme
setThemeColor = _qfluentwidgets.setThemeColor
NavigationInterface = _qfluentwidgets.NavigationInterface
NavigationItemPosition = _qfluentwidgets.NavigationItemPosition
AnimatedStackedWidget = importlib.import_module("qfluentwidgets.window.fluent_window").StackedWidget


_SEARCH_LINE_EDIT_MACOS_DARK_CSS = """
LineEdit, TextEdit, PlainTextEdit, TextBrowser, SearchLineEdit {
    color: rgba(245, 246, 247, 0.96);
    background-color: rgba(17, 20, 24, 0.94);
    border: 1px solid rgba(77, 88, 102, 0.82);
    border-bottom: 1px solid rgba(109, 121, 136, 0.94);
    border-radius: 5px;
    padding: 0px 10px;
    selection-background-color: #4a9ef5;
}

TextEdit,
PlainTextEdit,
TextBrowser {
    padding: 2px 3px 2px 8px;
}

LineEdit:hover, TextEdit:hover, PlainTextEdit:hover, TextBrowser:hover, SearchLineEdit:hover {
    background-color: rgba(24, 29, 35, 0.96);
}

LineEdit:focus, TextEdit:focus, PlainTextEdit:focus, TextBrowser:focus, SearchLineEdit:focus {
    border-bottom: 1px solid #4a9ef5;
    background-color: rgba(21, 25, 31, 0.98);
}

LineEdit:disabled, TextEdit:disabled, PlainTextEdit:disabled, TextBrowser:disabled, SearchLineEdit:disabled {
    color: rgba(126, 135, 146, 0.95);
    background-color: rgba(20, 24, 29, 0.90);
    border: 1px solid rgba(57, 66, 77, 0.88);
    border-bottom: 1px solid rgba(57, 66, 77, 0.88);
}

#lineEditButton {
    background-color: transparent;
    border-radius: 4px;
    border: none;
}

#lineEditButton:hover {
    background-color: rgba(255, 255, 255, 0.08);
}

#lineEditButton:pressed {
    background-color: rgba(255, 255, 255, 0.12);
}
""".strip()

_COMBO_BOX_MACOS_DARK_CSS = """
ComboBox, ModelComboBox {
    border: 1px solid rgba(77, 88, 102, 0.82);
    border-bottom: 1px solid rgba(109, 121, 136, 0.94);
    border-radius: 5px;
    padding: 5px 31px 6px 11px;
    color: rgba(245, 246, 247, 0.96);
    background-color: rgba(17, 20, 24, 0.94);
    text-align: left;
    outline: none;
}

ComboBox:hover, ModelComboBox:hover {
    background-color: rgba(24, 29, 35, 0.96);
}

ComboBox:pressed, ModelComboBox:pressed {
    background-color: rgba(21, 25, 31, 0.98);
    border-bottom: 1px solid #4a9ef5;
    color: rgba(245, 246, 247, 0.96);
}

ComboBox:disabled, ModelComboBox:disabled {
    color: rgba(126, 135, 146, 0.95);
    background: rgba(20, 24, 29, 0.90);
    border: 1px solid rgba(57, 66, 77, 0.88);
    border-bottom: 1px solid rgba(57, 66, 77, 0.88);
}

ComboBox[isPlaceholderText=true], ModelComboBox[isPlaceholderText=true] {
    color: rgba(151, 161, 172, 0.96);
}
""".strip()

_NUMERIC_TEXT_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_TABLE_SORT_ROLE = Qt.ItemDataRole.UserRole + 1
_SAFE_FALLBACK_QPA_PLATFORMS = {"offscreen", "minimal", "headless"}


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _qt_platform_name() -> str:
    return os.environ.get("QT_QPA_PLATFORM", "").strip().lower()


def _running_under_pytest() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _use_safe_fluent_window() -> bool:
    if not _is_macos():
        return False
    if _truthy_env("IEVPI_USE_NATIVE_FLUENT_WINDOW"):
        return False
    return True


class _SafeFluentWindow(QWidget):
    """A QWidget-based fallback that avoids macOS frameless window crashes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mica_enabled = False
        self._light_background = QColor(240, 244, 249)
        self._dark_background = QColor(32, 32, 32)

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setSpacing(0)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)

        self.navigationInterface = NavigationInterface(self, showReturnButton=True)
        self.stackedWidget = AnimatedStackedWidget(self)
        self.widgetLayout = QHBoxLayout()
        self.widgetLayout.setContentsMargins(0, 24, 0, 0)
        self.widgetLayout.addWidget(self.stackedWidget)

        self.hBoxLayout.addWidget(self.navigationInterface)
        self.hBoxLayout.addLayout(self.widgetLayout, 1)

        self.stackedWidget.currentChanged.connect(self._on_current_interface_changed)
        self._update_background_color()

    def setMicaEffectEnabled(self, is_enabled: bool) -> None:
        self._mica_enabled = bool(is_enabled)

    def isMicaEffectEnabled(self) -> bool:
        return self._mica_enabled

    def setCustomBackgroundColor(self, light, dark) -> None:
        self._light_background = QColor(light)
        self._dark_background = QColor(dark)
        self._update_background_color()

    def _update_background_color(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, self._dark_background if isDarkTheme() else self._light_background)
        self.setPalette(palette)
        self.setAutoFillBackground(True)

    def addSubInterface(self, interface, icon, text: str, position=NavigationItemPosition.TOP, parent=None, isTransparent=False):
        if not interface.objectName():
            raise ValueError("The object name of `interface` can't be empty string.")

        parent_route_key = parent
        if parent is not None and isinstance(parent, QWidget):
            parent_route_key = parent.objectName()
            if not parent_route_key:
                raise ValueError("The object name of `parent` can't be empty string.")

        interface.setProperty("isStackedTransparent", isTransparent)
        if self.stackedWidget.indexOf(interface) < 0:
            self.stackedWidget.addWidget(interface)

        route_key = interface.objectName()
        item = self.navigationInterface.addItem(
            routeKey=route_key,
            icon=icon,
            text=text,
            onClick=lambda: self.switchTo(interface),
            position=position,
            tooltip=text,
            parentRouteKey=parent_route_key,
        )

        if self.stackedWidget.count() == 1:
            self.navigationInterface.setCurrentItem(route_key)
        self._update_stacked_background()
        return item

    def removeInterface(self, interface, isDelete=False) -> None:
        route_key = interface.objectName()
        self.navigationInterface.removeWidget(route_key)
        index = self.stackedWidget.indexOf(interface)
        if index >= 0:
            self.stackedWidget.removeWidget(interface)
        if isDelete:
            interface.deleteLater()

    def switchTo(self, interface) -> None:
        self.stackedWidget.setCurrentWidget(interface, popOut=False)
        self._update_stacked_background()

    def _on_current_interface_changed(self, index: int) -> None:
        widget = self.stackedWidget.widget(index)
        if widget is None:
            return
        route_key = widget.objectName()
        if route_key:
            self.navigationInterface.setCurrentItem(route_key)
        self._update_stacked_background()

    def _update_stacked_background(self) -> None:
        current_widget = self.stackedWidget.currentWidget()
        is_transparent = bool(current_widget.property("isStackedTransparent")) if current_widget is not None else False
        if bool(self.stackedWidget.property("isTransparent")) == is_transparent:
            return
        self.stackedWidget.setProperty("isTransparent", is_transparent)
        self.stackedWidget.setStyle(QApplication.style())


FluentWindow = _SafeFluentWindow if _use_safe_fluent_window() else _qfluentwidgets.FluentWindow


def _apply_macos_widget_compat(widget, css: str) -> None:
    if not _is_macos():
        return
    app = QApplication.instance()
    if app is not None:
        widget.setPalette(app.palette())
    widget.setStyleSheet(css)


def table_sort_key(value) -> tuple[int, object]:
    if value is None:
        return (2, "")
    if isinstance(value, bool):
        return (0, int(value))
    if isinstance(value, (int, float)):
        return (0, value)
    text = str(value).strip()
    if not text:
        return (2, "")
    if _NUMERIC_TEXT_RE.fullmatch(text):
        try:
            if "." in text:
                return (0, float(text))
            return (0, int(text))
        except ValueError:
            pass
    return (1, text.casefold())


class SortableTableWidgetItem(QTableWidgetItem):
    def __init__(self, text: str = "", sort_value=None):
        super().__init__(text)
        self.set_sort_value(text if sort_value is None else sort_value)

    def set_sort_value(self, value) -> None:
        self.setData(_TABLE_SORT_ROLE, table_sort_key(value))

    def __lt__(self, other) -> bool:
        left = self.data(_TABLE_SORT_ROLE) or table_sort_key(self.text())
        if isinstance(other, QTableWidgetItem):
            right = other.data(_TABLE_SORT_ROLE) or table_sort_key(other.text())
        else:
            right = table_sort_key(other)
        return left < right


class PrimaryPushButton(_qfluentwidgets.PrimaryPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if _is_macos():
            app = QApplication.instance()
            if app is not None:
                self.setPalette(app.palette())


class SearchLineEdit(_qfluentwidgets.SearchLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_macos_widget_compat(self, _SEARCH_LINE_EDIT_MACOS_DARK_CSS)


class TableWidget(_qfluentwidgets.TableWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if _is_macos():
            app = QApplication.instance()
            if app is not None:
                self.setPalette(app.palette())
        self._sorted_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        header = self.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._handle_header_click)

    def _handle_header_click(self, column: int) -> None:
        if column < 0 or column >= self.columnCount():
            return
        if self._sorted_column == column and self._sort_order == Qt.SortOrder.AscendingOrder:
            order = Qt.SortOrder.DescendingOrder
        else:
            order = Qt.SortOrder.AscendingOrder
        self.apply_sort(column, order)

    def apply_sort(self, column: int, order: Qt.SortOrder) -> None:
        if column < 0 or column >= self.columnCount():
            return
        self._sorted_column = column
        self._sort_order = order
        self.setSortingEnabled(True)
        self.sortItems(column, order)
        self.horizontalHeader().setSortIndicator(column, order)
        self.setSortingEnabled(False)

    def reapply_saved_sort(self) -> None:
        if self.rowCount() <= 0:
            return
        if self._sorted_column < 0 or self._sorted_column >= self.columnCount():
            return
        self.apply_sort(self._sorted_column, self._sort_order)


class ComboBox(_qfluentwidgets.ComboBox):
    """Combo box with a screen-safe popup position for long localized labels."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_macos_widget_compat(self, _COMBO_BOX_MACOS_DARK_CSS)

    def _showComboMenu(self):
        if not self.items:
            return

        menu = self._createComboMenu()
        for i, item in enumerate(self.items):
            action = QAction(item.icon, item.text, triggered=lambda c, x=i: self._onItemClicked(x), parent=menu)
            action.setEnabled(item.isEnabled)
            menu.addAction(action)

        metrics = self.fontMetrics()
        longest_text_width = max((metrics.horizontalAdvance(item.text) for item in self.items), default=0)
        minimum_width = max(self.width(), longest_text_width + 96)
        if menu.view.width() < minimum_width:
            menu.view.setMinimumWidth(minimum_width)
            menu.adjustSize()

        menu.setMaxVisibleItems(self.maxVisibleItems())
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.closedSignal.connect(self._onDropMenuClosed)
        self.dropMenu = menu

        if self.currentIndex() >= 0 and self.items:
            menu.setDefaultAction(menu.actions()[self.currentIndex()])

        anchor_down = self.mapToGlobal(QPoint(0, self.height()))
        anchor_up = self.mapToGlobal(QPoint(0, 0))
        screen = QGuiApplication.screenAt(anchor_down) or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            clamped_x = max(available.left(), min(anchor_down.x(), available.right() - menu.width() + 1))
            anchor_down.setX(clamped_x)
            anchor_up.setX(clamped_x)

        hd = menu.view.heightForAnimation(anchor_down, _qfluentwidgets.MenuAnimationType.DROP_DOWN)
        hu = menu.view.heightForAnimation(anchor_up, _qfluentwidgets.MenuAnimationType.PULL_UP)

        if hd >= hu:
            menu.view.adjustSize(anchor_down, _qfluentwidgets.MenuAnimationType.DROP_DOWN)
            menu.exec(anchor_down, aniType=_qfluentwidgets.MenuAnimationType.DROP_DOWN)
        else:
            menu.view.adjustSize(anchor_up, _qfluentwidgets.MenuAnimationType.PULL_UP)
            menu.exec(anchor_up, aniType=_qfluentwidgets.MenuAnimationType.PULL_UP)


class SwitchButton(QAbstractButton):
    """A lightweight Fluent-style switch without the upstream hover font warnings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset = 3.0
        self._hovered = False
        self._on_text = ""
        self._off_text = ""
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)

        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate_thumb)

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def getOffset(self) -> float:
        return self._offset

    def setOffset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = pyqtProperty(float, getOffset, setOffset)

    def setOnText(self, text: str) -> None:
        self._on_text = text

    def setOffText(self, text: str) -> None:
        self._off_text = text

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        self._offset = self._target_offset()
        super().resizeEvent(event)

    def _target_offset(self) -> float:
        return float(self.width() - 21 if self.isChecked() else 3)

    def _animate_thumb(self, _checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(self._target_offset())
        self._animation.start()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        dark = isDarkTheme()
        enabled = self.isEnabled()

        if self.isChecked():
            track = QColor(86, 174, 255) if not dark else QColor(74, 158, 245)
            if self._hovered:
                track = track.lighter(110)
            if not enabled:
                track.setAlpha(90)
            thumb = QColor(255, 255, 255)
        else:
            track = QColor(0, 0, 0, 36) if not dark else QColor(255, 255, 255, 36)
            border = QColor(0, 0, 0, 90) if not dark else QColor(255, 255, 255, 110)
            if self._hovered:
                track = QColor(0, 0, 0, 52) if not dark else QColor(255, 255, 255, 52)
            if not enabled:
                track = QColor(0, 0, 0, 18) if not dark else QColor(255, 255, 255, 18)
                border = QColor(0, 0, 0, 40) if not dark else QColor(255, 255, 255, 48)
            thumb = QColor(246, 248, 252) if not dark else QColor(238, 242, 247)

        rect = self.rect().adjusted(1, 1, -1, -1)
        radius = rect.height() / 2

        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.setPen(QPen(border, 1))
        painter.setBrush(track)
        painter.drawRoundedRect(rect, radius, radius)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(thumb)
        painter.drawEllipse(int(round(self._offset)), 3, 18, 18)
        painter.end()
