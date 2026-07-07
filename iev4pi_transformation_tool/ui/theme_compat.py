from __future__ import annotations

import os

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


_COMPAT_STYLESHEET_BEGIN = "/* IEVPI_MACOS_DARK_COMPAT_BEGIN */"
_COMPAT_STYLESHEET_END = "/* IEVPI_MACOS_DARK_COMPAT_END */"
_VIRTUAL_STYLESHEET_PLATFORMS = {"offscreen", "minimal", "headless"}


def _current_app_stylesheet(app: QApplication) -> str:
    cached = getattr(app, "_ievpi_virtual_stylesheet", None)
    if isinstance(cached, str):
        return cached
    return QApplication.styleSheet(app) or ""


def _use_virtual_app_stylesheet() -> bool:
    platform_name = os.environ.get("QT_QPA_PLATFORM", "").strip().lower()
    return platform_name in _VIRTUAL_STYLESHEET_PLATFORMS


def _apply_app_stylesheet(app: QApplication, stylesheet: str) -> None:
    if _use_virtual_app_stylesheet():
        app._ievpi_virtual_stylesheet = stylesheet  # type: ignore[attr-defined]
        app.styleSheet = lambda css=stylesheet: css  # type: ignore[method-assign]
        return
    app.setStyleSheet(stylesheet)


def apply_macos_dark_palette_compat(app: QApplication) -> None:
    """Force standard Qt widgets to honor a stable dark palette on macOS."""

    if not _use_virtual_app_stylesheet():
        app.setStyle("Fusion")

    palette = QPalette()
    window = QColor("#181818")
    window_text = QColor("#f5f6f7")
    base = QColor("#111418")
    alternate_base = QColor("#1a1f26")
    button = QColor("#252b33")
    button_hover = QColor("#2f3640")
    highlight = QColor("#4a9ef5")
    highlight_text = QColor("#ffffff")
    disabled_text = QColor("#7e8792")
    border = QColor("#3b4450")
    placeholder = QColor("#97a1ac")

    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, window_text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, alternate_base)
    palette.setColor(QPalette.ColorRole.ToolTipBase, alternate_base)
    palette.setColor(QPalette.ColorRole.ToolTipText, window_text)
    palette.setColor(QPalette.ColorRole.Text, window_text)
    palette.setColor(QPalette.ColorRole.Button, button)
    palette.setColor(QPalette.ColorRole.ButtonText, window_text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff6b6b"))
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, highlight_text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, placeholder)
    palette.setColor(QPalette.ColorRole.Light, button_hover)
    palette.setColor(QPalette.ColorRole.Midlight, border)
    palette.setColor(QPalette.ColorRole.Dark, QColor("#101317"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#2d3640"))
    palette.setColor(QPalette.ColorRole.Shadow, QColor("#000000"))

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.HighlightedText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled_text)

    for role, color in (
        (QPalette.ColorRole.Button, QColor("#1f252c")),
        (QPalette.ColorRole.Base, base),
        (QPalette.ColorRole.Window, window),
        (QPalette.ColorRole.Highlight, QColor("#305d8f")),
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, color)

    app.setPalette(palette)
    _apply_macos_dark_stylesheet_compat(app)


def _apply_macos_dark_stylesheet_compat(app: QApplication) -> None:
    compat_css = f"""
{_COMPAT_STYLESHEET_BEGIN}
QLineEdit,
QTextEdit,
QPlainTextEdit,
QAbstractSpinBox,
QListView,
QListWidget,
QTreeView,
QTreeWidget,
QTableView,
QTableWidget,
QAbstractItemView,
QAbstractScrollArea {{
    background-color: #111418;
    color: #f5f6f7;
    selection-background-color: #4a9ef5;
    selection-color: #ffffff;
    border: 1px solid #3b4450;
    border-radius: 8px;
}}

QHeaderView::section,
QTableCornerButton::section {{
    background-color: #1a1f26;
    color: #f5f6f7;
    border: none;
    border-right: 1px solid #2d3640;
    border-bottom: 1px solid #2d3640;
    padding: 6px 8px;
}}

QPushButton,
QToolButton {{
    background-color: #252b33;
    color: #f5f6f7;
    border: 1px solid #3b4450;
    border-radius: 8px;
    padding: 6px 12px;
}}

QPushButton:hover,
QToolButton:hover {{
    background-color: #2f3640;
}}

QPushButton:pressed,
QToolButton:pressed {{
    background-color: #20262d;
}}

QPushButton:disabled,
QToolButton:disabled {{
    background-color: #1f252c;
    color: #7e8792;
    border-color: #313946;
}}

QComboBox,
QComboBox QAbstractItemView,
QMenu {{
    background-color: #1a1f26;
    color: #f5f6f7;
    border: 1px solid #3b4450;
}}

QComboBox QAbstractItemView::item:selected,
QMenu::item:selected {{
    background-color: #4a9ef5;
    color: #ffffff;
}}

QLabel {{
    color: #f5f6f7;
    background: transparent;
}}
{_COMPAT_STYLESHEET_END}
""".strip()
    existing = _current_app_stylesheet(app)
    if _COMPAT_STYLESHEET_BEGIN in existing and _COMPAT_STYLESHEET_END in existing:
        start = existing.index(_COMPAT_STYLESHEET_BEGIN)
        end = existing.index(_COMPAT_STYLESHEET_END) + len(_COMPAT_STYLESHEET_END)
        merged = (existing[:start].rstrip() + "\n\n" + compat_css + "\n" + existing[end:].lstrip()).strip()
    elif existing.strip():
        merged = f"{existing.rstrip()}\n\n{compat_css}\n"
    else:
        merged = compat_css
    _apply_app_stylesheet(app, merged)
