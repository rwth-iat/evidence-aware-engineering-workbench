from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path
import platform
import re
import shutil
import time
import requests
from typing import Callable

from PyQt6.QtCore import (
    QAbstractTableModel,
    QItemSelectionModel,
    QModelIndex,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QGuiApplication, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from iev4pi_transformation_tool.ui.qfluent import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    IndeterminateProgressRing,
    isDarkTheme,
    PrimaryPushButton,
    ProgressBar,
    SearchLineEdit,
    SortableTableWidgetItem,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TableWidget,
    ToolButton,
    table_sort_key,
    FluentIcon,
)
from iev4pi_transformation_tool.ui.source_preview import ValueSourcePreviewDialog

from iev4pi_transformation_tool.models import (
    DocumentFamily,
    ExcelCellProvenance,
    ExcelCellTooltipContext,
    ExcelWorkbookPreview,
    ExtractedRecord,
    PidInconsistencyRow,
    PidInconsistencySummary,
    ProjectSettings,
    SchemaFamily,
    SchemaField,
)
from iev4pi_transformation_tool.core.ocr_defaults import get_ocr_platform_defaults
from iev4pi_transformation_tool.services.workbench import Workbench
from iev4pi_transformation_tool.ui.i18n import (
    normalize_language,
    tr,
    translate_family,
    translate_source_kind,
    translate_status,
)
from iev4pi_transformation_tool.ui.tasking import InlineProgressCard, TaskWorker


SETTINGS_LANGUAGE_OPTIONS = [
    ("en", "English"),
    ("de", "German"),
    ("zh", "Chinese"),
]


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _ocr_defaults():
    return get_ocr_platform_defaults()


def _backend_uses_configurable_device(backend: str) -> bool:
    return backend in {"paddle", "surya"}


def _ocr_device_options(current_value: str = "") -> list[tuple[str, str]]:
    items = [("cpu", "CPU")]
    if _is_windows():
        items.extend(
            [
                ("cuda:0", "GPU 0 (CUDA)"),
                ("cuda:1", "GPU 1 (CUDA)"),
            ]
        )
    elif _is_macos():
        items.append(("mps", "Apple GPU (MPS)"))
    elif not _is_macos():
        items.append(("cuda:0", "CUDA GPU"))
    values = {value for value, _label in items}
    normalized_current = current_value.strip()
    if normalized_current and normalized_current not in values:
        items.append((normalized_current, f"Custom ({normalized_current})"))
    return items


def _primary_ocr_options() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if _is_macos():
        items.append(("apple", "Apple Vision"))
    items.extend(
        [
            ("paddle", "PaddleOCR"),
            ("rapidocr", "RapidOCR"),
            ("surya", "Surya"),
            ("easyocr", "EasyOCR (RustOCR bridge)"),
        ]
    )
    return items


def _fallback_ocr_options(none_label: str) -> list[tuple[str, str]]:
    if _is_macos():
        return [
            ("rapidocr", "RapidOCR"),
            ("apple", "Apple Vision"),
            ("surya", "Surya"),
            ("easyocr", "EasyOCR (RustOCR bridge)"),
            ("none", none_label),
        ]
    return [
        ("surya", "Surya"),
        ("rapidocr", "RapidOCR"),
        ("easyocr", "EasyOCR (RustOCR bridge)"),
        ("none", none_label),
    ]


def _apple_ocr_framework_options() -> list[tuple[str, str]]:
    return [
        ("vision", "Vision"),
        ("livetext", "LiveText"),
    ]


def _apple_ocr_recognition_level_options() -> list[tuple[str, str]]:
    return [
        ("fast", "Fast"),
        ("accurate", "Accurate"),
    ]


def _ocr_backend_display_name(value: str, none_label: str) -> str:
    return {
        "apple": "Apple Vision",
        "paddle": "PaddleOCR",
        "rapidocr": "RapidOCR",
        "surya": "Surya",
        "easyocr": "EasyOCR (RustOCR bridge)",
        "none": none_label,
    }.get((value or "").strip().lower(), value)


def _set_table_headers(table: TableWidget, headers: list[str]) -> None:
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setWordWrap(True)


class SuryaWarmupInline(QWidget):
    requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self.button = PrimaryPushButton("")
        self.button.clicked.connect(self.requested.emit)
        header.addWidget(self.button)

        self.spinner = IndeterminateProgressRing(self)
        self.spinner.setFixedSize(16, 16)
        self.spinner.setVisible(False)
        header.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.summary_label = CaptionLabel("")
        self.summary_label.setWordWrap(True)
        header.addWidget(self.summary_label, 1)
        layout.addLayout(header)

        self.progress_bar = ProgressBar(self)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

    def apply_language(self, language: str) -> None:
        self.button.setText(tr(language, "settings.surya_prewarm_button"))

    def set_ready_state(self, summary: str) -> None:
        self.spinner.setVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.summary_label.setText(summary)
        self.button.setVisible(False)
        self.setVisible(True)

    def set_idle_state(self, summary: str) -> None:
        self.spinner.setVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.summary_label.setText(summary)
        self.button.setVisible(True)
        self.button.setEnabled(True)
        self.setVisible(True)

    def set_running_state(self, value: int, summary: str) -> None:
        self.spinner.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(max(0, min(100, int(value))))
        self.summary_label.setText(summary)
        self.button.setVisible(True)
        self.button.setEnabled(False)
        self.setVisible(True)


class ScaledLogoWidget(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        preferred_width: int = 720,
        minimum_width: int = 140,
    ) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._preferred_width = preferred_width
        self._minimum_width = minimum_width
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setMaximumWidth(preferred_width)

    def setPixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.updateGeometry()
        self.update()

    def clear(self) -> None:
        self._pixmap = QPixmap()
        self.updateGeometry()
        self.update()

    def pixmap(self) -> QPixmap | None:
        return None if self._pixmap.isNull() else self._pixmap

    def sizeHint(self) -> QSize:
        if self._pixmap.isNull():
            return QSize(self._preferred_width, 120)
        scaled_height = round(self._preferred_width * self._pixmap.height() / max(1, self._pixmap.width()))
        return QSize(self._preferred_width, scaled_height)

    def minimumSizeHint(self) -> QSize:
        if self._pixmap.isNull():
            return QSize(self._minimum_width, 48)
        scaled_height = round(self._minimum_width * self._pixmap.height() / max(1, self._pixmap.width()))
        return QSize(self._minimum_width, scaled_height)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        scaled = self._pixmap.scaled(
            self.contentsRect().size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)


class ReviewTableView(QTableView):
    """Review table with stable horizontal scrolling support."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sorted_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._enter_starts_editing = False
        self._delete_clears_selected_cells = False
        self._modifier_drag_adds_selection = False
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.horizontalHeader().setSectionsClickable(True)
        self.horizontalHeader().setSortIndicatorShown(True)
        self.horizontalHeader().sectionClicked.connect(self._handle_header_click)

    def set_enter_starts_editing(self, enabled: bool) -> None:
        self._enter_starts_editing = bool(enabled)

    def set_delete_clears_selected_cells(self, enabled: bool) -> None:
        self._delete_clears_selected_cells = bool(enabled)

    def set_modifier_drag_adds_selection(self, enabled: bool) -> None:
        self._modifier_drag_adds_selection = bool(enabled)

    def selectionCommand(self, index: QModelIndex, event=None):
        if (
            self._modifier_drag_adds_selection
            and index.isValid()
            and event is not None
            and event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        ):
            return QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Current
        return super().selectionCommand(index, event)

    def keyPressEvent(self, event) -> None:
        if self._enter_starts_editing and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            index = self.currentIndex()
            if index.isValid():
                self.edit(index)
                event.accept()
                return
        if self._delete_clears_selected_cells and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            model = self.model()
            indexes = self.selectedIndexes()
            if indexes and hasattr(model, "clear_indexes"):
                cleared = model.clear_indexes(indexes)
                if cleared:
                    event.accept()
                    return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            delta = event.angleDelta().y() or event.angleDelta().x()
            if delta:
                step = 60
                bar.setValue(bar.value() - int((delta / 120) * step))
                event.accept()
                return
        super().wheelEvent(event)

    def _handle_header_click(self, column: int) -> None:
        if column < 0:
            return
        if self._sorted_column == column and self._sort_order == Qt.SortOrder.AscendingOrder:
            order = Qt.SortOrder.DescendingOrder
        else:
            order = Qt.SortOrder.AscendingOrder
        self.sortByColumn(column, order)

    def sortByColumn(self, column: int, order: Qt.SortOrder) -> None:
        self._sorted_column = column
        self._sort_order = order
        self.horizontalHeader().setSortIndicator(column, order)
        model = self.model()
        if model is not None:
            model.sort(column, order)

    def reapply_saved_sort(self) -> None:
        if self._sorted_column < 0:
            return
        self.sortByColumn(self._sorted_column, self._sort_order)


class ReviewTableModel(QAbstractTableModel):
    page_requested = pyqtSignal(int)

    def __init__(self, workbench: Workbench, language: str, page_size: int = 80) -> None:
        super().__init__()
        self.workbench = workbench
        self.language = language
        self.page_size = page_size
        self.family_filter: str | None = None
        self.keyword_filter = ""
        self.total_count = 0
        self.fixed_headers: list[str] = []
        self.headers: list[str] = []
        self.field_names: list[str] = []
        self._page_cache: OrderedDict[int, list[dict[str, object]]] = OrderedDict()
        self._max_cached_pages = 12
        self._pending_pages: set[int] = set()
        self._highlighted_record_keys: set[str] = set()
        self._all_rows: list[dict[str, object]] = []
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

    def set_fixed_headers(self, headers: list[str]) -> None:
        self.fixed_headers = headers
        self._refresh_headers()

    def set_language(self, language: str) -> None:
        self.language = language
        self._refresh_headers()
        self.layoutChanged.emit()

    def set_filters(self, family_filter: str | None, keyword_filter: str = "") -> None:
        self.beginResetModel()
        self.family_filter = family_filter
        self.keyword_filter = keyword_filter.strip()
        self.field_names = self._resolve_field_names()
        self._refresh_headers()
        self.total_count = 0
        self._page_cache.clear()
        self._pending_pages.clear()
        self._highlighted_record_keys.clear()
        self._all_rows.clear()
        self.endResetModel()

    def apply_review_payload(self, payload: dict[str, object]) -> None:
        family_filter = payload.get("family")
        keyword_filter = str(payload.get("keyword", "")).strip()
        offset = max(0, int(payload.get("offset", 0)))
        rows = payload.get("rows", [])
        self.beginResetModel()
        self.family_filter = family_filter if isinstance(family_filter, str) and family_filter else None
        self.keyword_filter = keyword_filter
        self.field_names = self._resolve_field_names()
        self._refresh_headers()
        self.total_count = max(0, int(payload.get("total_count", 0)))
        self._page_cache.clear()
        self._pending_pages.clear()
        self._all_rows = self._deserialize_rows(rows)
        self._sort_loaded_rows()
        if not self._all_rows or len(self._all_rows) < self.total_count:
            self._page_cache[offset] = self._all_rows
        else:
            self._page_cache.clear()
        self.endResetModel()

    def apply_page_payload(self, payload: dict[str, object]) -> None:
        page_start = max(0, int(payload.get("offset", 0)))
        rows = self._deserialize_rows(payload.get("rows", []))
        self.total_count = max(self.total_count, int(payload.get("total_count", self.total_count)))
        self._all_rows.clear()
        self._pending_pages.discard(page_start)
        self._page_cache[page_start] = rows
        self._page_cache.move_to_end(page_start)
        while len(self._page_cache) > self._max_cached_pages:
            self._page_cache.popitem(last=False)
        if not self.headers:
            self._refresh_headers()
        if rows and self.columnCount() > 0:
            start_row = page_start
            end_row = min(self.total_count, page_start + len(rows)) - 1
            if end_row >= start_row:
                self.dataChanged.emit(
                    self.index(start_row, 0),
                    self.index(end_row, self.columnCount() - 1),
                    [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole],
                )

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self.total_count

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.headers):
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.BackgroundRole,
        }:
            return None
        review_record = self._row_at(index.row())
        if review_record is None:
            return ""
        record = review_record["record"]
        results_by_name = review_record["results_by_name"]
        if role == Qt.ItemDataRole.BackgroundRole and record.record_key in self._highlighted_record_keys:
            return QBrush(QColor("#274255" if isDarkTheme() else "#fff4ce"))
        if index.column() == 0:
            source_root = record.source_root or self.workbench.family_source_root(record.family)
            if record.scope_id:
                bundle_name = self.workbench.bundle_name_for_scope(record.scope_id)
                return f"{source_root} / {bundle_name} / {translate_family(self.language, record.family)}"
            return f"{source_root} / {translate_family(self.language, record.family)}"
        if index.column() == 1:
            return record.display_name
        if index.column() == 2:
            return record.source_path
        field_index = index.column() - 3
        if field_index < 0 or field_index >= len(self.field_names):
            return ""
        result = results_by_name.get(self.field_names[field_index])
        if result is None or not result.value:
            if role == Qt.ItemDataRole.ToolTipRole:
                return self._result_tooltip(record, result)
            return ""
        if role == Qt.ItemDataRole.ForegroundRole:
            conf = self._result_review_confidence(result)
            need_review = self.workbench.settings.review_need_review_threshold
            low = self.workbench.settings.review_low_confidence_threshold
            if conf < need_review:
                return QBrush(QColor("#ff8080" if isDarkTheme() else "#c42b1c"))
            if conf < low:
                return QBrush(QColor("#d29922" if isDarkTheme() else "#a15c00"))
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._result_tooltip(record, result)
        return result.value

    @staticmethod
    def _result_review_confidence(result: object | None) -> float:
        if result is None:
            return 0.0
        values: list[float] = []
        for attr in ("confidence", "decision_confidence"):
            raw = getattr(result, attr, None)
            if raw is None or raw == "":
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
        return min(values) if values else 0.0

    def _result_tooltip(self, record: ExtractedRecord, result: object | None) -> str:
        if result is None:
            return translate_status(self.language, "blank_no_evidence")
        status_value = getattr(result, "status", None)
        status_key = status_value.value if status_value is not None else "blank_no_evidence"
        confidence = float(getattr(result, "confidence", 0.0) or 0.0)
        lines = [
            f"Status: {translate_status(self.language, status_key)}",
            f"Confidence: {confidence:.2f}",
            f"Source: {record.source_path}",
        ]
        decision_confidence = getattr(result, "decision_confidence", None)
        if decision_confidence is not None:
            try:
                lines.append(f"Decision confidence: {float(decision_confidence):.2f}")
            except (TypeError, ValueError):
                pass
        review_confidence = self._result_review_confidence(result)
        if review_confidence != confidence:
            lines.append(f"Review confidence: {review_confidence:.2f}")
        evidence_bundle_id = str(getattr(result, "evidence_bundle_id", "") or "").strip()
        if evidence_bundle_id:
            lines.append(f"Evidence bundle: {evidence_bundle_id}")
        llm_status = str(getattr(result, "llm_verification_status", "") or "").strip()
        if llm_status:
            lines.append(f"LLM/VLM: {llm_status}")
        uncertainty_reason = str(getattr(result, "uncertainty_reason", "") or "").strip()
        if uncertainty_reason:
            lines.append(f"Uncertainty: {uncertainty_reason}")
        rule_support = [str(item).strip() for item in getattr(result, "rule_support", []) if str(item).strip()]
        if rule_support:
            lines.append(f"Rules: {' | '.join(rule_support)}")
        review_feedback_status = str(getattr(result, "review_feedback_status", "") or "").strip()
        if review_feedback_status:
            lines.append(f"Review feedback: {review_feedback_status}")

        first_evidence = result.evidence_refs[0] if getattr(result, "evidence_refs", None) else None
        if first_evidence is None:
            return "\n".join(lines)
        location_bits = []
        if first_evidence.page_or_sheet:
            location_bits.append(first_evidence.page_or_sheet)
        if first_evidence.cell_range_or_bbox:
            location_bits.append(first_evidence.cell_range_or_bbox)
        location = " / ".join(location_bits)
        snippet = first_evidence.snippet or ""
        evidence_type = first_evidence.evidence_type or "blank"
        engine = first_evidence.engine or ""
        evidence_line = evidence_type if not engine else f"{evidence_type} ({engine})"
        lines.extend(
            [
                f"Evidence: {evidence_line}",
                f"Location: {location}",
                f"Snippet: {snippet}",
            ]
        )
        return "\n".join(lines)

    def _row_at(self, row_index: int) -> dict[str, object] | None:
        if row_index < 0 or row_index >= self.total_count:
            return None
        if self._all_rows:
            return self._all_rows[row_index] if row_index < len(self._all_rows) else None
        page_start = (row_index // self.page_size) * self.page_size
        page_rows = self._page_cache.get(page_start)
        if page_rows is None:
            self._request_page(page_start)
            return None
        else:
            self._page_cache.move_to_end(page_start)
            self._maybe_prefetch_next_page(row_index, page_start)
        offset = row_index - page_start
        return page_rows[offset] if 0 <= offset < len(page_rows) else None

    def source_path_at(self, row_index: int) -> str:
        row = self._row_at(row_index)
        if row is None:
            return ""
        record = row["record"]
        return str(record.source_path)

    def value_context_at(self, row_index: int, column_index: int) -> dict[str, object] | None:
        row = self._row_at(row_index)
        if row is None or column_index < 3:
            return None
        field_index = column_index - 3
        if field_index < 0 or field_index >= len(self.field_names):
            return None
        field_name = self.field_names[field_index]
        result = row["results_by_name"].get(field_name)
        if result is None:
            return None
        return {
            "record": row["record"],
            "field_name": field_name,
            "result": result,
        }

    def prefetch_first_page(self) -> None:
        if self.total_count > 0 and not self._all_rows:
            self._request_page(0)

    def loaded_count(self) -> int:
        if self._all_rows:
            return len(self._all_rows)
        return sum(len(rows) for rows in self._page_cache.values())

    def _deserialize_rows(self, rows: object) -> list[dict[str, object]]:
        page_rows: list[dict[str, object]] = []
        if not isinstance(rows, list):
            return page_rows
        for item in rows:
            record = ExtractedRecord.model_validate(item)
            page_rows.append(
                {
                    "record": record,
                    "results_by_name": {result.field_name: result for result in record.results},
                }
            )
        return page_rows

    def _resolve_field_names(self) -> list[str]:
        return self.workbench.schema_field_names_for_selection(self.family_filter)

    def _refresh_headers(self) -> None:
        self.headers = [*self.fixed_headers, *self.field_names]
        if self.headers:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.headers) - 1)

    def _request_page(self, page_start: int) -> None:
        if page_start < 0 or page_start >= self.total_count:
            return
        if page_start in self._page_cache or page_start in self._pending_pages:
            return
        self._pending_pages.add(page_start)
        self.page_requested.emit(page_start)

    def _maybe_prefetch_next_page(self, row_index: int, page_start: int) -> None:
        next_page = page_start + self.page_size
        if next_page >= self.total_count:
            return
        if row_index - page_start >= max(1, self.page_size - 15):
            self._request_page(next_page)

    def set_highlighted_record_keys(self, record_keys: set[str]) -> None:
        self._highlighted_record_keys = set(record_keys)
        if self.total_count > 0 and self.columnCount() > 0:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.total_count - 1, self.columnCount() - 1),
                [Qt.ItemDataRole.BackgroundRole],
            )

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self._sort_column = column
        self._sort_order = order
        if not self._all_rows:
            return
        self.layoutAboutToBeChanged.emit()
        self._sort_loaded_rows()
        self.layoutChanged.emit()

    def _sort_loaded_rows(self) -> None:
        if not self._all_rows:
            return
        if self._sort_column < 0 or self._sort_column >= len(self.headers):
            return
        self._all_rows.sort(
            key=lambda row: self._sort_key_for_row(row, self._sort_column),
            reverse=self._sort_order == Qt.SortOrder.DescendingOrder,
        )

    def _sort_key_for_row(self, review_row: dict[str, object], column: int) -> tuple[int, object]:
        record = review_row["record"]
        results_by_name = review_row["results_by_name"]
        if column == 0:
            source_root = record.source_root or self.workbench.family_source_root(record.family)
            if record.scope_id:
                bundle_name = self.workbench.bundle_name_for_scope(record.scope_id)
                return table_sort_key(f"{source_root} / {bundle_name} / {translate_family(self.language, record.family)}")
            return table_sort_key(f"{source_root} / {translate_family(self.language, record.family)}")
        if column == 1:
            return table_sort_key(record.display_name)
        if column == 2:
            return table_sort_key(record.source_path)
        field_index = column - 3
        if field_index < 0 or field_index >= len(self.field_names):
            return table_sort_key("")
        result = results_by_name.get(self.field_names[field_index])
        if result is None:
            return table_sort_key("")
        return table_sort_key(result.value)


class ExcelPreviewTableModel(QAbstractTableModel):
    cell_edited = pyqtSignal(str, str, str)
    OCR_ENGINE_MARKERS = {"ocr", "rapidocr", "easyocr", "paddle", "paddleocr", "surya", "apple"}

    def __init__(self, workbench: Workbench, language: str) -> None:
        super().__init__()
        self.workbench = workbench
        self.language = language
        self.workbook: ExcelWorkbookPreview | None = None
        self.sheet_index = 0
        self._rows: list[list[str]] = []
        self._provenance: dict[str, ExcelCellProvenance] = {}
        self._tooltip_contexts: dict[str, ExcelCellTooltipContext] = {}
        self._column_count = 0
        self._highlighted_coord = ""

    def set_language(self, language: str) -> None:
        self.language = language
        self.layoutChanged.emit()

    def set_workbook(self, workbook: ExcelWorkbookPreview | None) -> None:
        self.beginResetModel()
        self.workbook = workbook
        self.sheet_index = 0
        self._highlighted_coord = ""
        self._load_active_sheet()
        self.endResetModel()

    def set_sheet(self, sheet_name: str) -> None:
        if self.workbook is None:
            return
        next_index = next(
            (index for index, sheet in enumerate(self.workbook.sheets) if sheet.name == sheet_name),
            self.sheet_index,
        )
        if next_index == self.sheet_index:
            return
        self.beginResetModel()
        self.sheet_index = next_index
        self._highlighted_coord = ""
        self._load_active_sheet()
        self.endResetModel()

    def sheet_names(self) -> list[str]:
        if self.workbook is None:
            return []
        return [sheet.name for sheet in self.workbook.sheets]

    def active_sheet_name(self) -> str:
        if self.workbook is None or not self.workbook.sheets:
            return ""
        if 0 <= self.sheet_index < len(self.workbook.sheets):
            return self.workbook.sheets[self.sheet_index].name
        return ""

    def workbook_name(self) -> str:
        return self.workbook.workbook_name if self.workbook is not None else ""

    def workbook_path(self) -> str:
        return self.workbook.path if self.workbook is not None else ""

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self._column_count

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._excel_column_letter(section + 1)
        if orientation == Qt.Orientation.Vertical:
            return str(section + 1)
        return super().headerData(section, orientation, role)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role not in {
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.EditRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.ForegroundRole,
        }:
            return None
        value = self._value_at(index.row(), index.column())
        provenance = self.provenance_at(index.row(), index.column())
        if role == Qt.ItemDataRole.ForegroundRole:
            if provenance is not None:
                conf = self.provenance_review_confidence(provenance)
                need_review = self.workbench.settings.review_need_review_threshold
                low = self.workbench.settings.review_low_confidence_threshold
                if conf < need_review:
                    return QBrush(QColor("#ff8080" if isDarkTheme() else "#c42b1c"))
                if conf < low:
                    return QBrush(QColor("#d29922" if isDarkTheme() else "#a15c00"))
                return None
            context = self.tooltip_context_at(index.row(), index.column())
            if self.is_review_cell_metadata(None, context):
                conf = self.tooltip_context_review_confidence(context) or 0.0
                need_review = self.workbench.settings.review_need_review_threshold
                if conf < need_review:
                    return QBrush(QColor("#ff8080" if isDarkTheme() else "#c42b1c"))
                return QBrush(QColor("#d29922" if isDarkTheme() else "#a15c00"))
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            if not value:
                return ""
            if provenance is not None:
                return self._provenance_tooltip(provenance, value, self._coord(index.row(), index.column()))
            context = self.tooltip_context_at(index.row(), index.column())
            if context is not None:
                return self._tooltip_context_text(context)
            return self._no_direct_source_text(index.row(), index.column(), value)
        return value

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return super().flags(index)
        return (
            Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsEditable
        )

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or self.workbook is None:
            return False
        text = "" if value is None else str(value)
        provenance_before = self.provenance_at(index.row(), index.column())
        if text == self._value_at(index.row(), index.column()) and not self.is_review_provenance(provenance_before):
            return False
        row_number = index.row() + 1
        column_number = index.column() + 1
        sheet_name = self.active_sheet_name()
        workbook_name = self.workbook_name()
        try:
            updated_provenance = self.workbench.update_filled_excel_cell(
                workbook_name,
                sheet_name,
                row_number,
                column_number,
                text,
            )
        except Exception:
            return False
        self._set_value_at(index.row(), index.column(), text)
        coord = self._coord(index.row(), index.column())
        if updated_provenance is not None:
            self._provenance[coord] = updated_provenance
        self.dataChanged.emit(
            index,
            index,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.EditRole,
                Qt.ItemDataRole.ToolTipRole,
                Qt.ItemDataRole.ForegroundRole,
            ],
        )
        self.cell_edited.emit(workbook_name, sheet_name, coord)
        return True

    def clear_indexes(self, indexes: list[QModelIndex]) -> int:
        if self.workbook is None:
            return 0
        selected: dict[tuple[int, int], QModelIndex] = {}
        for index in indexes:
            if index.isValid() and index.model() is self:
                selected[(index.row(), index.column())] = index
        edits: dict[tuple[int, int], str] = {}
        for (row_index, column_index), index in selected.items():
            provenance = self.provenance_at(row_index, column_index)
            if self._value_at(row_index, column_index) or self.is_review_provenance(provenance):
                edits[(row_index + 1, column_index + 1)] = ""
        if not edits:
            return 0
        workbook_name = self.workbook_name()
        sheet_name = self.active_sheet_name()
        try:
            updated_provenance = self.workbench.update_filled_excel_cells(workbook_name, sheet_name, edits)
        except Exception:
            return 0
        changed_indexes: list[QModelIndex] = []
        first_coord = ""
        for row_number, column_number in sorted(edits):
            row_index = row_number - 1
            column_index = column_number - 1
            self._set_value_at(row_index, column_index, "")
            coord = self._coord(row_index, column_index)
            if not first_coord:
                first_coord = coord
            if coord in updated_provenance and updated_provenance[coord] is not None:
                self._provenance[coord] = updated_provenance[coord]
            changed_indexes.append(self.index(row_index, column_index))
        roles = [
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.EditRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.ForegroundRole,
        ]
        for index in changed_indexes:
            if index.isValid():
                self.dataChanged.emit(index, index, roles)
        if first_coord:
            self.cell_edited.emit(workbook_name, sheet_name, first_coord)
        return len(edits)

    def provenance_at(self, row_index: int, column_index: int) -> ExcelCellProvenance | None:
        coord = self._coord(row_index, column_index)
        return self._provenance.get(coord)

    def tooltip_context_at(self, row_index: int, column_index: int) -> ExcelCellTooltipContext | None:
        coord = self._coord(row_index, column_index)
        return self._tooltip_contexts.get(coord)

    def index_for_coord(self, coord: str) -> QModelIndex:
        row_index, column_index = self._coord_to_indexes(coord)
        if row_index < 0 or column_index < 0:
            return QModelIndex()
        if row_index >= self.rowCount() or column_index >= self.columnCount():
            return QModelIndex()
        return self.index(row_index, column_index)

    def is_ocr_source_cell(self, row_index: int, column_index: int) -> bool:
        provenance = self.provenance_at(row_index, column_index)
        return self.provenance_has_ocr_source(provenance)

    def is_highlighted_cell(self, row_index: int, column_index: int) -> bool:
        return bool(self._highlighted_coord and self._coord(row_index, column_index) == self._highlighted_coord)

    def set_highlighted_coord(self, coord: str) -> None:
        previous = self._highlighted_coord
        self._highlighted_coord = coord
        for candidate in {previous, coord}:
            index = self.index_for_coord(candidate)
            if index.isValid():
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.BackgroundRole])

    def value_context_at(self, row_index: int, column_index: int) -> dict[str, object] | None:
        provenance = self.provenance_at(row_index, column_index)
        if provenance is None:
            return None
        return {"provenance": provenance}

    def is_review_provenance(self, provenance: ExcelCellProvenance | None) -> bool:
        if provenance is None:
            return False
        status_value = provenance.status.value if hasattr(provenance.status, "value") else str(provenance.status)
        return (
            status_value == "needs_review"
            or self.provenance_review_confidence(provenance) < self.workbench.settings.review_low_confidence_threshold
        )

    def is_review_cell_metadata(
        self,
        provenance: ExcelCellProvenance | None,
        context: ExcelCellTooltipContext | None,
    ) -> bool:
        if provenance is not None:
            return self.is_review_provenance(provenance)
        if not self.is_reviewable_tooltip_context(context):
            return False
        context_confidence = self.tooltip_context_review_confidence(context)
        return (
            context_confidence is not None
            and context_confidence < self.workbench.settings.review_low_confidence_threshold
        )

    @classmethod
    def is_reviewable_tooltip_context(cls, context: ExcelCellTooltipContext | None) -> bool:
        if context is None or context.source_type != "row_metadata":
            return False
        field_key = cls._review_field_key(context.field_name)
        if not field_key:
            return False
        if field_key in cls._NON_REVIEW_TOOLTIP_FIELDS:
            return False
        if field_key.endswith("_id") or field_key.startswith("id_"):
            return False
        if not cls._uses_strict_aio_review_fields(context):
            return True
        if cls._is_reviewable_aio_source_value_context(context, field_key):
            return True
        return False

    @classmethod
    def _is_reviewable_aio_source_value_context(
        cls,
        context: ExcelCellTooltipContext,
        field_key: str,
    ) -> bool:
        sheet_name = str(context.sheet_name or "")
        if sheet_name == "Object":
            return field_key == "content_text"
        if sheet_name in cls._AIO_SOURCE_VALUE_REVIEW_SHEETS:
            return field_key in {"attribute_value", "raw_value"}
        return False

    @classmethod
    def _uses_strict_aio_review_fields(cls, context: ExcelCellTooltipContext) -> bool:
        workbook_name = str(context.workbook_name or "").lower()
        sheet_name = str(context.sheet_name or "")
        return workbook_name.endswith("_aio.xlsx") or sheet_name in cls._AIO_REVIEW_FILTER_SHEETS

    _NON_REVIEW_TOOLTIP_FIELDS = {
        "index",
        "attribute_name",
        "confidence",
        "decision_confidence",
        "review_status",
        "needs_review",
        "needs_review_reason",
        "parsing_status",
        "extraction_method",
        "match_status",
        "match_method",
        "match_rule",
        "source_operation",
        "source_file",
        "source_row",
        "source_locator",
        "source_object_id",
        "evidence_summary",
        "page_number",
        "bbox_x1",
        "bbox_y1",
        "bbox_x2",
        "bbox_y2",
        "pos_x_mm",
        "pos_y_mm",
        "pos_z_mm",
        "object_type",
        "object_role",
    }
    _AIO_REVIEW_FILTER_SHEETS = {
        "Object",
        "Object_Cluster",
        "Document_Data",
        "Document_Data_Source",
        "Element_Data",
        "Element_Data_Source",
        "Elements_TopDown",
        "Elements_from_Cluster",
        "Element_ID",
        "Match_Result",
        "Connection_Data",
        "Connection_Data_Source",
        "RepresentedItem_Data",
        "RepresentedItem_Data_Source",
        "Classification",
        "Attribute_Lookup",
    }
    _AIO_SOURCE_VALUE_REVIEW_SHEETS = {
        "Document_Data",
        "Element_Data",
        "Connection_Data",
        "RepresentedItem_Data",
    }

    @staticmethod
    def _review_field_key(field_name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(field_name or "").strip().lower()).strip("_")

    @staticmethod
    def provenance_review_confidence(provenance: ExcelCellProvenance | None) -> float:
        if provenance is None:
            return 0.0
        values: list[float] = []
        for raw in (provenance.confidence, provenance.decision_confidence):
            if raw is None or raw == "":
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
        return min(values) if values else 0.0

    @staticmethod
    def tooltip_context_review_confidence(context: ExcelCellTooltipContext | None) -> float | None:
        if context is None:
            return None
        values: list[float] = []
        for raw in (context.confidence, context.decision_confidence):
            if raw is None or raw == "":
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
        return min(values) if values else None

    @classmethod
    def provenance_has_ocr_source(cls, provenance: ExcelCellProvenance | None) -> bool:
        if provenance is None:
            return False
        for evidence in provenance.evidence_refs:
            evidence_type = str(evidence.evidence_type or "").casefold()
            engine = str(evidence.engine or "").casefold()
            if "ocr" in evidence_type or "ocr" in engine:
                return True
            if any(marker in engine for marker in cls.OCR_ENGINE_MARKERS):
                return True
        return False

    def _load_active_sheet(self) -> None:
        self._rows = []
        self._provenance = {}
        self._tooltip_contexts = {}
        self._column_count = 0
        if self.workbook is None or not self.workbook.sheets:
            return
        self.sheet_index = max(0, min(self.sheet_index, len(self.workbook.sheets) - 1))
        sheet = self.workbook.sheets[self.sheet_index]
        self._rows = sheet.rows
        self._provenance = sheet.cell_provenance
        self._tooltip_contexts = sheet.tooltip_contexts
        self._column_count = max((len(row) for row in self._rows), default=0)

    def _value_at(self, row_index: int, column_index: int) -> str:
        if row_index < 0 or row_index >= len(self._rows):
            return ""
        row = self._rows[row_index]
        if column_index < 0 or column_index >= len(row):
            return ""
        return row[column_index]

    def _set_value_at(self, row_index: int, column_index: int, value: str) -> None:
        if row_index < 0 or column_index < 0:
            return
        while row_index >= len(self._rows):
            self._rows.append([])
        row = self._rows[row_index]
        while column_index >= len(row):
            row.append("")
        row[column_index] = value
        self._column_count = max(self._column_count, column_index + 1)

    def _coord(self, row_index: int, column_index: int) -> str:
        return f"{self._excel_column_letter(column_index + 1)}{row_index + 1}"

    def _coord_to_indexes(self, coord: str) -> tuple[int, int]:
        text = str(coord or "").strip().upper()
        letters = ""
        digits = ""
        for char in text:
            if char.isalpha() and not digits:
                letters += char
            elif char.isdigit():
                digits += char
            else:
                return -1, -1
        if not letters or not digits:
            return -1, -1
        column_number = 0
        for char in letters:
            column_number = column_number * 26 + (ord(char) - 64)
        return int(digits) - 1, column_number - 1

    def _provenance_tooltip(self, provenance: ExcelCellProvenance, value: str = "", coord: str = "") -> str:
        status_value = provenance.status.value if hasattr(provenance.status, "value") else str(provenance.status)
        method = self._provenance_method(provenance)
        location = self._provenance_location(provenance) or provenance.coord
        lines = [
            f"Cell: {provenance.sheet_name}!{provenance.coord or coord}",
            "Source type: direct extraction",
            f"Field: {provenance.field_name}",
            f"Status: {translate_status(self.language, status_value)}",
            f"Confidence: {float(provenance.confidence or 0.0):.2f}",
            f"Evidence support: {self._evidence_support_text(provenance)}",
            f"Extraction method: {method or 'n/a'}",
            f"Source file: {provenance.source_path or self._missing_source_text()}",
            f"Location: {location or provenance.coord or coord}",
        ]
        if provenance.record_display_name:
            lines.append(f"Record: {provenance.record_display_name}")
        if provenance.decision_confidence is not None:
            try:
                lines.append(f"Decision confidence: {float(provenance.decision_confidence):.2f}")
            except (TypeError, ValueError):
                pass
        if provenance.llm_verification_status:
            lines.append(f"LLM/VLM: {provenance.llm_verification_status}")
        if provenance.uncertainty_reason:
            lines.append(f"Uncertainty: {provenance.uncertainty_reason}")
        if provenance.rule_support:
            lines.append(f"Rules: {' | '.join(provenance.rule_support)}")
        if provenance.review_feedback_status:
            lines.append(f"Review feedback: {provenance.review_feedback_status}")
        if provenance.notes:
            lines.append(f"Notes: {provenance.notes}")
        if not provenance.evidence_refs:
            return "\n".join(lines)
        first_evidence = provenance.evidence_refs[0]
        lines.extend(
            [
                f"Evidence: {method or 'evidence'}",
                f"Snippet: {first_evidence.snippet}",
            ]
        )
        if len(provenance.evidence_refs) > 1:
            lines.append(f"More evidence: {len(provenance.evidence_refs) - 1}")
        return "\n".join(lines)

    def _tooltip_context_text(self, context: ExcelCellTooltipContext) -> str:
        method = context.extraction_method
        source_path = context.source_path
        location = context.location
        confidence = context.confidence
        note = context.note
        if context.source_type == "template":
            method = method or "Template_Static"
            source_path = source_path or context.workbook_name or self.workbook_name() or self.workbook_path()
            location = location or context.current_location or f"{context.sheet_name}!{context.coord}"
            confidence = 1.0 if confidence is None else confidence
            note = note or "Static template/header/rule cell from the workbook template."
        elif context.source_type == "exporter_generated":
            method = method or "Deterministic_Exporter"
            source_path = source_path or context.workbook_name or self.workbook_name() or self.workbook_path()
            location = location or context.current_location or f"{context.sheet_name}!{context.coord}"
            note = note or (
                "Generated by the deterministic exporter. "
                "No direct parser/OCR/LLM source object is available for this specific cell; "
                "source file and location refer to the exported workbook cell, not an original source document."
            )
        lines = [
            f"Cell: {context.current_location or f'{context.sheet_name}!{context.coord}'}",
            f"Source type: {self._source_type_label(context.source_type)}",
            f"Field: {context.field_name or 'n/a'}",
            f"Confidence: {self._confidence_text(confidence)}",
            f"Evidence support: {self._context_evidence_support_text(context)}",
            f"Extraction method: {method or 'n/a'}",
            f"Source file: {source_path or self._missing_source_text()}",
            f"Location: {self._source_location_text(location)}",
        ]
        if context.decision_confidence is not None:
            lines.append(f"Decision confidence: {self._confidence_text(context.decision_confidence)}")
        if note:
            lines.append(f"Note: {note}")
        return "\n".join(lines)

    def _no_direct_source_text(self, row_index: int, column_index: int, value: str) -> str:
        coord = self._coord(row_index, column_index)
        sheet_name = self.active_sheet_name()
        field_name = ""
        if self._rows and self._rows[0] and 0 <= column_index < len(self._rows[0]):
            field_name = self._rows[0][column_index]
        is_template = row_index <= 1 or sheet_name in {"Rules", "Schema_Metadata"}
        method = "Template_Static" if is_template else "Workbook_Structure_Fallback"
        confidence: float | None = 1.0 if is_template else None
        source_type = "template/header/rule" if is_template else "generated or derived export cell"
        support = (
            "static template cell; no extraction evidence refs"
            if is_template
            else "workbook structure fallback; no source-object refs available for this cell"
        )
        source_file = self.workbook_name() or self.workbook_path() or "current workbook"
        lines = [
            f"Cell: {sheet_name}!{coord}",
            f"Source type: {source_type}",
            f"Field: {field_name or 'n/a'}",
            f"Confidence: {self._confidence_text(confidence)}",
            f"Evidence support: {support}",
            f"Extraction method: {method}",
            f"Source file: {source_file}",
            f"Location: {sheet_name}!{coord}",
            "Note: Fallback tooltip generated from workbook structure; source file and location refer to the exported workbook cell, not an original source document.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _is_structural_field(field_name: str) -> bool:
        field_key = str(field_name or "").strip().lower()
        return (
            field_key == "index"
            or field_key.endswith("_id")
            or field_key
            in {
                "document_id",
                "semanticid",
                "source",
                "review_status",
                "resolution_status",
                "match_status",
                "match_rule",
                "relationship_type",
                "caex_type",
                "caex_roleclass_path",
                "caex_systemunitclass_path",
                "caex_interfaceclass_path",
                "parsing_status",
            }
        )

    def _missing_source_text(self) -> str:
        if self.language == "de":
            return "keine direkte Extraktionsquelle"
        if self.language == "zh":
            return "无直接抽取来源"
        return "no direct extraction source"

    def _source_location_text(self, location: str) -> str:
        if location:
            return location
        if self.language == "de":
            return "keine ursprüngliche Quellposition verfügbar"
        if self.language == "zh":
            return "无原始文件位置"
        return "original source location unavailable"

    def _source_type_label(self, source_type: str) -> str:
        labels = {
            "direct_extraction": "direct extraction",
            "row_metadata": "row metadata",
            "template": "template/header/rule",
            "exporter_generated": "generated or derived export cell",
            "no_direct_source": "no direct extraction source",
        }
        return labels.get(source_type, source_type or "no direct extraction source")

    @staticmethod
    def _confidence_text(value: float | None) -> str:
        if value is None:
            return "n/a"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return "n/a"

    @classmethod
    def _evidence_support_text(cls, provenance: ExcelCellProvenance) -> str:
        count = len(provenance.evidence_refs)
        bundle = str(provenance.evidence_bundle_id or "").strip()
        if count <= 0:
            suffix = f", bundle {bundle}" if bundle else ""
            return f"0 refs{suffix}; confidence has no direct evidence refs"
        scores: list[float] = []
        for evidence in provenance.evidence_refs:
            try:
                scores.append(float(evidence.score))
            except (TypeError, ValueError):
                continue
        top_score = max(scores) if scores else None
        ref_label = "ref" if count == 1 else "refs"
        parts = [f"{count} {ref_label}"]
        if top_score is not None:
            parts.append(f"top score {cls._confidence_text(top_score)}")
        if bundle:
            parts.append(f"bundle {bundle}")
        return ", ".join(parts)

    def _context_evidence_support_text(self, context: ExcelCellTooltipContext) -> str:
        if context.source_type == "row_metadata":
            if context.confidence is None:
                return "no direct evidence refs; row has source metadata but no confidence column"
            return "no direct evidence refs; confidence read from row metadata"
        if context.source_type == "exporter_generated" and context.extraction_method:
            return "deterministic exporter rule support; no direct source-object refs for this derived cell"
        if context.source_type == "template" and context.extraction_method:
            return "static template cell; no extraction evidence refs"
        if context.source_type in {"template", "exporter_generated", "no_direct_source"}:
            return "n/a"
        return "no direct evidence refs"

    @staticmethod
    def _provenance_method(provenance: ExcelCellProvenance) -> str:
        if provenance.evidence_refs:
            evidence = provenance.evidence_refs[0]
            evidence_type = str(evidence.evidence_type or "").strip()
            engine = str(evidence.engine or "").strip()
            if evidence_type and engine:
                return f"{evidence_type} ({engine})"
            return evidence_type or engine
        if provenance.rule_support:
            return " | ".join(str(rule).strip() for rule in provenance.rule_support if str(rule).strip())
        return str(provenance.llm_verification_status or "").strip()

    @staticmethod
    def _provenance_location(provenance: ExcelCellProvenance) -> str:
        if not provenance.evidence_refs:
            return ""
        evidence = provenance.evidence_refs[0]
        return " / ".join(
            item
            for item in [str(evidence.page_or_sheet or "").strip(), str(evidence.cell_range_or_bbox or "").strip()]
            if item
        )

    @staticmethod
    def _excel_column_letter(column_number: int) -> str:
        result = ""
        number = max(1, int(column_number))
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(65 + remainder) + result
        return result


class ExcelEvidenceCellDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        super().paint(painter, option, index)
        model = index.model()
        if not isinstance(model, ExcelPreviewTableModel):
            return
        draw_ocr_border = model.is_ocr_source_cell(index.row(), index.column())
        draw_highlight = model.is_highlighted_cell(index.row(), index.column())
        if not draw_ocr_border and not draw_highlight:
            return
        painter.save()
        rect = option.rect.adjusted(2, 2, -2, -2)
        if draw_highlight:
            highlight_color = QColor("#ffcc66" if isDarkTheme() else "#d18f00")
            painter.setPen(QPen(highlight_color, 3))
            painter.drawRect(rect)
            rect = rect.adjusted(2, 2, -2, -2)
        if draw_ocr_border:
            border_color = QColor("#ffffff" if isDarkTheme() else "#f8f8f8")
            painter.setPen(QPen(border_color, 2))
            painter.drawRect(rect)
        painter.restore()


class BasePage(QWidget):
    def __init__(
        self,
        workbench: Workbench,
        refresh_all: Callable[[], None],
        title_key: str,
        subtitle_key: str,
    ) -> None:
        super().__init__()
        self.workbench = workbench
        self.refresh_all = refresh_all
        self.title_key = title_key
        self.subtitle_key = subtitle_key
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(20, 12, 20, 12)
        self.root_layout.setSpacing(8)
        self.title_label = SubtitleLabel("")
        self.subtitle_label = BodyLabel("")
        self.subtitle_label.setWordWrap(True)
        self.root_layout.addWidget(self.title_label)
        self.root_layout.addWidget(self.subtitle_label)
        self.progress_card = InlineProgressCard(self)
        self.root_layout.addWidget(self.progress_card)
        self._task_thread: QThread | None = None
        self._task_worker: TaskWorker | None = None
        self._task_title = ""
        self._last_task_message = ""

    def log_debug(
        self,
        action: str,
        message: str,
        *,
        level: str = "INFO",
        details: dict | None = None,
    ) -> None:
        self.workbench.log_debug(
            source=self.objectName() or self.__class__.__name__,
            action=action,
            message=message,
            level=level,
            details=details,
        )

    @property
    def language(self) -> str:
        return normalize_language(self.workbench.settings.ui_language)

    def t(self, key: str, **kwargs) -> str:
        return tr(self.language, key, **kwargs)

    def resolve_text(self, key_or_text: str) -> str:
        if key_or_text.startswith("literal:"):
            return key_or_text.split(":", 1)[1]
        return self.t(key_or_text)

    def apply_language(self) -> None:
        self.title_label.setText(self.t(self.title_key))
        self.subtitle_label.setText(self.t(self.subtitle_key))

    def _extraction_ocr_label_text(self) -> str:
        if self.language == "de":
            return "OCR verwenden"
        if self.language == "zh":
            return "使用OCR"
        return "Use OCR"

    def _toggle_on_text(self) -> str:
        if self.language == "de":
            return "Ein"
        if self.language == "zh":
            return "开"
        return "On"

    def _toggle_off_text(self) -> str:
        if self.language == "de":
            return "Aus"
        if self.language == "zh":
            return "关"
        return "Off"

    def refresh(self, *_args) -> None:
        return None

    def _banner_parent(self) -> QWidget:
        parent = self.window()
        return parent if isinstance(parent, QWidget) else self

    def _banner_title(self, title_key: str | None = None) -> str:
        if title_key:
            title = self.resolve_text(title_key).strip()
            if title:
                return title
        title_label = getattr(self, "title_label", None)
        if title_label is not None:
            title = str(title_label.text() or "").strip()
            if title:
                return title
        window_title = str(self.windowTitle() or "").strip()
        if window_title:
            return window_title
        return self.t("app.title")

    def _banner_content(self, message: str) -> str:
        return " ".join(part.strip() for part in str(message).splitlines() if part.strip())

    def _show_banner(
        self,
        level: str,
        message: str,
        title_key: str | None = None,
        *,
        duration: int,
    ) -> None:
        content = self._banner_content(message)
        if not content:
            return
        factory = {
            "success": InfoBar.success,
            "info": InfoBar.info,
            "warning": InfoBar.warning,
            "error": InfoBar.error,
        }[level]
        factory(
            title=self._banner_title(title_key),
            content=content,
            isClosable=True,
            duration=duration,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self._banner_parent(),
        )

    def show_success_banner(self, message: str, title_key: str | None = None, *, duration: int = 3200) -> None:
        self._show_banner("success", message, title_key, duration=duration)

    def show_info_banner(self, message: str, title_key: str | None = None, *, duration: int = 3600) -> None:
        self._show_banner("info", message, title_key, duration=duration)

    def show_warning_banner(self, message: str, title_key: str | None = None, *, duration: int = 4400) -> None:
        self._show_banner("warning", message, title_key, duration=duration)

    def show_error(self, title_key: str, message: str) -> None:
        self.log_debug(
            "error",
            f"{self.resolve_text(title_key)}: {message}",
            level="ERROR",
        )
        self._show_banner("error", message, title_key, duration=5200)

    def cancel_background_task(self, message: str | None = None) -> None:
        if self._task_thread is None or self._task_worker is None or not self._task_thread.isRunning():
            return
        self.log_debug(source="task_cancel_requested", message=f"Cancel requested for {self._task_worker.task_name}")
        if message:
            self._last_task_message = message
            self.progress_card.update_progress(
                self.progress_card.progress_bar.value(),
                self._task_title,
                message,
            )
        self._task_worker.cancel()

    def run_background_task(
        self,
        task_name: str,
        title_key: str,
        body_key: str,
        error_title_key: str,
        on_success: Callable[[object], None],
        payload: dict | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        on_complete: Callable[[bool], None] | None = None,
        deliver_success_immediately: bool = False,
    ) -> None:
        if self._task_thread is not None and self._task_thread.isRunning():
            return
        title = self.resolve_text(title_key)
        body = self.resolve_text(body_key)
        self._task_title = title
        self._last_task_message = body
        self.progress_card.start(title, body)
        self.set_task_running(True)
        task_started_at = time.perf_counter()
        self.log_debug(
            "task_started",
            f"Started task {task_name}: {body}",
            details={"task_name": task_name, "payload": payload or {}},
        )

        self._task_thread = QThread(self)
        self._task_worker = TaskWorker(str(self.workbench.workspace_root), task_name, payload)
        self._task_worker.moveToThread(self._task_thread)
        success_result: object | None = None
        success_delivered = False
        error_message: str | None = None
        cancel_message: str | None = None

        def handle_progress(value: int, message: str) -> None:
            display_value = (
                self.progress_card.progress_bar.value()
                if int(value) < 0
                else max(0, min(100, int(value)))
            )
            self._last_task_message = message
            self.progress_card.update_progress(value, title, message)
            self.log_debug(
                "task_progress",
                f"{task_name} [{display_value}%] {message}",
                details={
                    "task_name": task_name,
                    "progress": int(display_value),
                    "raw_progress": int(value),
                },
            )
            if on_progress is not None:
                on_progress(value, message)

        def handle_log(entry: object) -> None:
            if isinstance(entry, dict):
                details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
                self.workbench.debug_log.append_entry(
                    {
                        **entry,
                        "details": {
                            **details,
                            "task_name": str(details.get("task_name") or task_name),
                        },
                    }
                )

        def handle_success(result: object) -> None:
            nonlocal success_delivered
            nonlocal success_result
            success_result = result
            self.progress_card.complete(title, self._last_task_message)
            duration_ms = round((time.perf_counter() - task_started_at) * 1000, 1)
            self.log_debug(
                "task_succeeded",
                f"Completed task {task_name} in {duration_ms} ms",
                details={"task_name": task_name, "duration_ms": duration_ms},
            )
            if deliver_success_immediately:
                on_success(result)
                success_delivered = True

        def handle_error(message: str) -> None:
            nonlocal error_message
            error_message = message
            self.progress_card.update_progress(self.progress_card.progress_bar.value(), title, self._last_task_message)
            duration_ms = round((time.perf_counter() - task_started_at) * 1000, 1)
            self.log_debug(
                "task_failed",
                f"Task {task_name} failed after {duration_ms} ms: {message}",
                level="ERROR",
                details={"task_name": task_name, "duration_ms": duration_ms},
            )

        def handle_cancelled(message: str) -> None:
            nonlocal cancel_message
            cancel_message = message
            self._last_task_message = message
            self.progress_card.cancel(title, message)
            duration_ms = round((time.perf_counter() - task_started_at) * 1000, 1)
            self.log_debug(
                "task_cancelled",
                f"Task {task_name} cancelled after {duration_ms} ms: {message}",
                level="WARNING",
                details={"task_name": task_name, "duration_ms": duration_ms},
            )

        def cleanup() -> None:
            self.set_task_running(False)
            thread = self._task_thread
            if thread is not None:
                thread.quit()
                thread.wait(2000)
            self._task_thread = None
            self._task_worker = None
            if cancel_message:
                if on_complete is not None:
                    on_complete(False)
                self.progress_card.schedule_hide()
                return
            if error_message:
                if on_complete is not None:
                    on_complete(False)
                self.show_error(error_title_key, error_message)
                self.progress_card.schedule_hide()
                return
            if success_result is not None:
                if not success_delivered:
                    on_success(success_result)
                if on_complete is not None:
                    on_complete(True)
                self.progress_card.schedule_hide()

        self._task_thread.started.connect(self._task_worker.run)
        self._task_worker.progress_changed.connect(handle_progress)
        self._task_worker.log_received.connect(handle_log)
        self._task_worker.succeeded.connect(handle_success)
        self._task_worker.failed.connect(handle_error)
        self._task_worker.cancelled.connect(handle_cancelled)
        self._task_worker.finished.connect(cleanup)
        self._task_worker.finished.connect(self._task_worker.deleteLater)
        self._task_thread.finished.connect(self._task_thread.deleteLater)
        self._task_thread.start()

    def choose_directory(self, title_key: str, current_path: str) -> Path | None:
        chosen = QFileDialog.getExistingDirectory(self, self.t(title_key), current_path)
        if chosen:
            self.log_debug(source="choose_directory", message=f"Selected directory {chosen}")
        return Path(chosen) if chosen else None

    def set_task_running(self, running: bool) -> None:
        return None


class QuickStartPage(BasePage):
    def __init__(self, workbench: Workbench, refresh_all: Callable[[], None]) -> None:
        super().__init__(
            workbench,
            refresh_all,
            "page.quick_start.title",
            "page.quick_start.subtitle",
        )
        self.hero_card = CardWidget(self)
        self.hero_card.setObjectName("quickStartHero")
        hero_layout = QHBoxLayout(self.hero_card)
        hero_layout.setContentsMargins(24, 24, 24, 24)
        hero_layout.setSpacing(20)

        hero_content = QWidget(self.hero_card)
        hero_content_layout = QVBoxLayout(hero_content)
        hero_content_layout.setContentsMargins(0, 0, 0, 0)
        hero_content_layout.setSpacing(12)
        self.hero_title_label = StrongBodyLabel("")
        self.hero_title_label.setObjectName("quickStartHeroTitle")
        self.hero_body_label = BodyLabel("")
        self.hero_body_label.setObjectName("quickStartHeroBody")
        self.hero_body_label.setWordWrap(True)
        hero_content_layout.addWidget(self.hero_title_label)
        hero_content_layout.addWidget(self.hero_body_label)

        badge_container = QWidget(hero_content)
        badge_layout = QHBoxLayout(badge_container)
        badge_layout.setContentsMargins(0, 8, 0, 0)
        badge_layout.setSpacing(12)
        self.badge_documents = self._create_badge_label(badge_container)
        self.badge_review = self._create_badge_label(badge_container)
        self.badge_export = self._create_badge_label(badge_container)
        badge_layout.addWidget(self.badge_documents)
        badge_layout.addWidget(self.badge_review)
        badge_layout.addWidget(self.badge_export)
        badge_layout.addStretch(1)
        hero_content_layout.addWidget(badge_container)
        hero_content_layout.addStretch(1)
        hero_layout.addWidget(hero_content, 1)

        self.logo_label = ScaledLogoWidget(self.hero_card, preferred_width=720, minimum_width=160)
        self.logo_label.setObjectName("quickStartLogo")
        hero_layout.addWidget(self.logo_label, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.root_layout.addWidget(self.hero_card)

        self.steps_container = QWidget(self)
        steps_layout = QGridLayout(self.steps_container)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setHorizontalSpacing(16)
        steps_layout.setVerticalSpacing(16)

        self.scan_card, self.scan_title, self.scan_body = self._create_step_card()
        self.schema_card, self.schema_title, self.schema_body = self._create_step_card()
        self.run_card, self.run_title, self.run_body = self._create_step_card()
        steps_layout.addWidget(self.scan_card, 0, 0)
        steps_layout.addWidget(self.schema_card, 0, 1)
        steps_layout.addWidget(self.run_card, 0, 2)
        self.root_layout.addWidget(self.steps_container)

        self.guide_card = CardWidget(self)
        self.guide_card.setObjectName("quickStartGuide")
        guide_layout = QVBoxLayout(self.guide_card)
        guide_layout.setContentsMargins(20, 20, 20, 20)
        guide_layout.setSpacing(12)
        self.guide_title = StrongBodyLabel("")
        self.guide_title.setObjectName("quickStartGuideTitle")
        guide_layout.addWidget(self.guide_title)

        self.markdown_box = QTextEdit(self.guide_card)
        self.markdown_box.setObjectName("quickStartGuideText")
        self.markdown_box.setReadOnly(True)
        self.markdown_box.setFrameStyle(QFrame.Shape.NoFrame)
        self.markdown_box.setMinimumHeight(280)
        guide_layout.addWidget(self.markdown_box)
        self.root_layout.addWidget(self.guide_card, 1)
        self._update_logo()
        self.apply_language()

    def apply_language(self) -> None:
        super().apply_language()
        self.hero_title_label.setText(self.t("quickstart.hero_title"))
        self.hero_body_label.setText(self.t("quickstart.hero_body"))
        self.badge_documents.setText(self.t("quickstart.badge.documents"))
        self.badge_review.setText(self.t("quickstart.badge.review"))
        self.badge_export.setText(self.t("quickstart.badge.export"))
        self.scan_title.setText(self.t("quickstart.card.scan.title"))
        self.scan_body.setText(self.t("quickstart.card.scan.body"))
        self.schema_title.setText(self.t("quickstart.card.schema.title"))
        self.schema_body.setText(self.t("quickstart.card.schema.body"))
        self.run_title.setText(self.t("quickstart.card.run.title"))
        self.run_body.setText(self.t("quickstart.card.run.body"))
        self.guide_title.setText(self.t("quickstart.guide_title"))
        self.markdown_box.setMarkdown(self.t("quickstart.markdown"))
        self._apply_theme_styles()

    def refresh(self, *_args) -> None:
        self._update_logo()
        self._apply_theme_styles()

    def _create_badge_label(self, parent: QWidget) -> QLabel:
        label = QLabel(parent)
        label.setObjectName("quickStartBadge")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _create_step_card(self) -> tuple[CardWidget, StrongBodyLabel, CaptionLabel]:
        card = CardWidget(self.steps_container)
        card.setObjectName("quickStartStepCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        title = StrongBodyLabel("")
        title.setObjectName("quickStartStepTitle")
        body = CaptionLabel("")
        body.setObjectName("quickStartStepBody")
        body.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addStretch(1)
        return card, title, body

    def _update_logo(self) -> None:
        logo_path = self.workbench.workspace_root / "assets" / "rwth_logo.png"
        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            self.logo_label.clear()
            self.logo_label.setVisible(False)
            return
        self.logo_label.setPixmap(pixmap)
        self.logo_label.setVisible(True)

    def _apply_theme_styles(self) -> None:
        if isDarkTheme():
            hero_border = "rgba(110, 168, 255, 0.30)"
            hero_start = "rgba(29, 43, 58, 0.98)"
            hero_end = "rgba(18, 28, 38, 0.95)"
            card_border = "rgba(119, 148, 180, 0.24)"
            card_bg = "rgba(28, 34, 42, 0.92)"
            guide_bg = "rgba(20, 25, 32, 0.92)"
            badge_bg = "rgba(96, 165, 250, 0.20)"
            badge_fg = "rgb(234, 243, 255)"
            title_fg = "rgb(248, 250, 252)"
            body_fg = "rgba(232, 238, 244, 0.92)"
            subtle_fg = "rgba(214, 223, 233, 0.88)"
        else:
            hero_border = "rgba(0, 120, 212, 0.20)"
            hero_start = "rgba(245, 250, 255, 0.98)"
            hero_end = "rgba(230, 241, 255, 0.92)"
            card_border = "rgba(125, 140, 158, 0.22)"
            card_bg = "rgba(255, 255, 255, 0.88)"
            guide_bg = "rgba(255, 255, 255, 0.92)"
            badge_bg = "rgba(0, 120, 212, 0.10)"
            badge_fg = "rgb(0, 82, 140)"
            title_fg = "rgb(24, 34, 44)"
            body_fg = "rgba(47, 62, 78, 0.92)"
            subtle_fg = "rgba(64, 80, 96, 0.86)"

        self.setStyleSheet(
            f"""
            QWidget#quickStartHero {{
                border: 1px solid {hero_border};
                border-radius: 24px;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 {hero_start},
                    stop: 1 {hero_end}
                );
            }}
            QWidget#quickStartGuide {{
                border: 1px solid {card_border};
                border-radius: 22px;
                background: {guide_bg};
            }}
            QWidget#quickStartStepCard {{
                border: 1px solid {card_border};
                border-radius: 20px;
                background: {card_bg};
            }}
            QLabel#quickStartBadge {{
                border-radius: 14px;
                padding: 6px 12px;
                background: {badge_bg};
                color: {badge_fg};
                font-weight: 600;
            }}
            QLabel#quickStartLogo {{
                background: transparent;
            }}
            QTextEdit#quickStartGuideText {{
                background: transparent;
                border: none;
                color: {body_fg};
            }}
            """
        )
        self.hero_title_label.setStyleSheet(f"color: {title_fg}; font-size: 26px; font-weight: 700;")
        self.hero_body_label.setStyleSheet(f"color: {body_fg}; font-size: 14px;")
        self.guide_title.setStyleSheet(f"color: {title_fg}; font-size: 18px; font-weight: 700;")

        for label in (self.scan_title, self.schema_title, self.run_title):
            label.setStyleSheet(f"color: {title_fg}; font-size: 16px; font-weight: 700;")
        for label in (self.scan_body, self.schema_body, self.run_body):
            label.setStyleSheet(f"color: {subtle_fg}; font-size: 13px;")


class ProjectPage(BasePage):
    def __init__(self, workbench: Workbench, refresh_all: Callable[[], None]) -> None:
        super().__init__(
            workbench,
            refresh_all,
            "page.project.title",
            "page.project.subtitle",
        )
        self.path_label = BodyLabel("")
        self.path_edit = QLineEdit(self)
        self.path_edit.setReadOnly(True)
        self.browse_button = PrimaryPushButton("")
        self.browse_button.clicked.connect(self.select_scan_root)
        path_row = QWidget(self)
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(12)
        path_layout.addWidget(self.path_label)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(self.browse_button)

        self.scan_button = PrimaryPushButton("")
        self.scan_button.clicked.connect(self.scan_workspace)
        self.summary_label = BodyLabel("")
        self.summary_label.setWordWrap(True)
        self.root_layout.addWidget(path_row)
        self.root_layout.addWidget(self.scan_button)
        self.root_layout.addWidget(self.summary_label)

        self.docs_table = TableWidget(self)
        self.root_layout.addWidget(self.docs_table, 1)
        self.apply_language()
        self.refresh()

    def apply_language(self) -> None:
        super().apply_language()
        self.path_label.setText(self.t("common.scan_root"))
        self.browse_button.setText(self.t("common.browse"))
        self.scan_button.setText(self.t("common.scan_workspace"))
        _set_table_headers(
            self.docs_table,
            [
                self.t("project.header.relative_path"),
                self.t("project.header.source_kind"),
                self.t("project.header.output_families"),
                self.t("project.header.size"),
            ],
        )
        if not self.workbench.documents:
            self.summary_label.setText(self.t("project.no_scan"))

    def scan_workspace(self) -> None:
        self.log_debug(source="scan_clicked", message=f"Scan workspace clicked for {self.workbench.resolve_scan_root()}")
        self.run_background_task(
            "scan",
            "busy.scan.title",
            "busy.scan.body",
            "common.scan_failed",
            self._handle_scan_complete,
        )

    def refresh(self, *_args) -> None:
        self.path_edit.setText(str(self.workbench.resolve_scan_root()))
        snapshot = self.workbench.current_snapshot()
        if not snapshot.documents:
            self.docs_table.setRowCount(0)
            self.summary_label.setText(self.t("project.no_scan"))
            return
        self.populate(snapshot)

    def select_scan_root(self) -> None:
        self.log_debug("browse_scan_root_clicked", "Browse scan root clicked")
        chosen = self.choose_directory("dialog.select_scan_root", str(self.workbench.resolve_scan_root()))
        if chosen is None:
            return
        self.workbench.update_scan_root(chosen)
        self.path_edit.setText(str(self.workbench.resolve_scan_root()))
        self.refresh()

    def _handle_scan_complete(self, result: object) -> None:
        snapshot = self.workbench.apply_snapshot_payload(result)
        self.populate(snapshot)

    def set_task_running(self, running: bool) -> None:
        self.scan_button.setDisabled(running)
        self.browse_button.setDisabled(running)

    def populate(self, snapshot) -> None:
        family_summary = self._format_count_mapping(snapshot.family_counts, is_family=True)
        source_summary = self._format_count_mapping(snapshot.source_kind_counts, is_family=False)
        summary_text = self.t(
            "project.summary",
            count=len(snapshot.documents),
            families=family_summary,
            sources=source_summary,
        )
        if snapshot.ri_bundles:
            bundle_bits = []
            for bundle in snapshot.ri_bundles:
                bundle_bits.append(
                    f"{bundle.display_name} (PDF: {self._bool_text(bool(bundle.pdf_path))}, "
                    f"XML: {self._bool_text(bool(bundle.xml_path))}, "
                    f"XSD: {self._bool_text(bool(bundle.xsd_path))}, "
                    f"{self._bundle_status_label()}: {bundle.pairing_status})"
                )
            summary_text += f"\n{self._bundle_summary_label()}: " + "; ".join(bundle_bits)
        self.summary_label.setText(summary_text)
        self.docs_table.setRowCount(len(snapshot.documents))
        for row_index, document in enumerate(snapshot.documents):
            values = [
                document.relative_path,
                translate_source_kind(self.language, document.source_kind.value),
                ", ".join(translate_family(self.language, family) for family in document.output_families),
                str(document.size_bytes),
            ]
            for col_index, value in enumerate(values):
                sort_value = document.size_bytes if col_index == 3 else value
                self.docs_table.setItem(row_index, col_index, SortableTableWidgetItem(value, sort_value))
        self.docs_table.reapply_saved_sort()

    def _format_count_mapping(self, items: dict[str, int], *, is_family: bool) -> str:
        if not items:
            return self.t("common.none")
        parts = []
        for name, count in items.items():
            label = translate_family(self.language, name) if is_family else translate_source_kind(self.language, name)
            parts.append(f"{label}: {count}")
        return ", ".join(parts)

    def _bundle_summary_label(self) -> str:
        if self.language == "de":
            return "R&I-Buendel"
        if self.language == "zh":
            return "R&I 组合包"
        return "R&I bundles"

    def _bundle_status_label(self) -> str:
        if self.language == "de":
            return "Status"
        if self.language == "zh":
            return "状态"
        return "Status"

    def _bool_text(self, value: bool) -> str:
        if self.language == "de":
            return "ja" if value else "nein"
        if self.language == "zh":
            return "是" if value else "否"
        return "yes" if value else "no"


class SchemaDiscoveryPage(BasePage):
    def __init__(self, workbench: Workbench, refresh_all: Callable[[], None]) -> None:
        super().__init__(
            workbench,
            refresh_all,
            "page.schema.title",
            "page.schema.subtitle",
        )
        self._pending_family_value: str | None = None
        self.generate_button = PrimaryPushButton("")
        self.generate_button.clicked.connect(self.generate_schemas)
        self.abort_button = PrimaryPushButton("")
        self.abort_button.clicked.connect(self.abort_current_task)
        self.abort_button.setDisabled(True)
        self.family_label = BodyLabel("")
        self.family_combo = ComboBox(self)
        self.family_combo.currentIndexChanged.connect(self.load_selected_schema)
        self.family_combo.currentIndexChanged.connect(self._log_schema_family_changed)
        toolbar = QWidget(self)
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        action_row = QWidget(toolbar)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(12)
        action_layout.addWidget(self.generate_button)
        action_layout.addWidget(self.abort_button)
        action_layout.addStretch(1)
        toolbar_layout.addWidget(action_row)
        toolbar_layout.addWidget(self.family_label)
        toolbar_layout.addWidget(self.family_combo)
        self.root_layout.addWidget(toolbar)

        self.schema_table = TableWidget(self)
        self.root_layout.addWidget(self.schema_table, 1)
        self.apply_language()
        self.refresh()

    def apply_language(self) -> None:
        super().apply_language()
        self.generate_button.setText(self.t("common.generate_schemas"))
        self.abort_button.setText(self._abort_button_text())
        self.family_label.setText(self.t("common.family"))
        _set_table_headers(
            self.schema_table,
            [
                self.t("schema.header.name"),
                self.t("schema.header.aliases"),
                self.t("schema.header.type"),
                self.t("schema.header.repeatable"),
                self.t("schema.header.hint"),
            ],
        )

    def abort_current_task(self) -> None:
        self.abort_button.setDisabled(True)
        self.cancel_background_task(self._schema_abort_requested_text())

    def generate_schemas(self) -> None:
        self.log_debug(
            "generate_schemas_clicked",
            "Generate schemas clicked",
            details={
                "use_ocr": self.workbench.settings.schema_generation_use_ocr,
                "ocr_runtime": self.workbench.ocr_runtime_status(),
            },
        )
        self.run_background_task(
            "generate_schemas",
            "busy.schema.title",
            "busy.schema.body",
            "common.schema_failed",
            self._handle_schema_generation_complete,
            payload={"use_ocr": self.workbench.settings.schema_generation_use_ocr},
        )

    def refresh(self, *_args) -> None:
        previous_value = self._pending_family_value or self.family_combo.currentData()
        self._pending_family_value = None
        self.family_combo.blockSignals(True)
        try:
            self.family_combo.clear()
            for selection, _label in self.workbench.schema_selection_items():
                schema = self.workbench.schema_by_selection(selection)
                if schema is None:
                    continue
                self.family_combo.addItem(self._schema_selection_label(schema), userData=selection)

            if self.family_combo.count() == 0:
                self.schema_table.setRowCount(0)
                return

            index = 0
            if previous_value:
                for row in range(self.family_combo.count()):
                    if self.family_combo.itemData(row) == previous_value:
                        index = row
                        break
            self.family_combo.setCurrentIndex(index)
        finally:
            self.family_combo.blockSignals(False)
        self.load_selected_schema()

    def _handle_schema_generation_complete(self, _result: object) -> None:
        self.workbench.reload_schemas()
        self.refresh()

    def load_selected_schema(self, *_args) -> None:
        selection = self.family_combo.currentData()
        schema = self.workbench.schema_by_selection(str(selection or ""))
        if schema is None:
            self.schema_table.setRowCount(0)
            return
        self.schema_table.setRowCount(len(schema.fields))
        for row_index, field in enumerate(schema.fields):
            values = [
                field.name,
                " | ".join(field.aliases),
                field.value_type,
                "true" if field.repeatable else "false",
                field.extraction_hint,
            ]
            for col_index, value in enumerate(values):
                sort_value = field.repeatable if col_index == 3 else value
                self.schema_table.setItem(row_index, col_index, SortableTableWidgetItem(value, sort_value))
        self.schema_table.reapply_saved_sort()

    def _log_schema_family_changed(self, *_args) -> None:
        selection = str(self.family_combo.currentData() or "")
        if selection:
            self.log_debug("schema_family_changed", f"Schema family changed to {selection}")

    def _text(self, row: int, column: int) -> str:
        item = self.schema_table.item(row, column)
        return item.text() if item else ""

    def _schema_selection_label(self, schema: SchemaFamily) -> str:
        source_root = schema.source_root or self.workbench.family_source_root(schema.family)
        family_label = translate_family(self.language, schema.family)
        if schema.scope_id:
            bundle_name = schema.bundle_name or self.workbench.bundle_name_for_scope(schema.scope_id)
            return f"{source_root} / {bundle_name} / {family_label}"
        return f"{source_root} / {family_label}"

    def _abort_button_text(self) -> str:
        if self.language == "de":
            return "Abbrechen"
        if self.language == "zh":
            return "停止"
        return "Stop"

    def _schema_abort_requested_text(self) -> str:
        if self.language == "de":
            return "Schema-Erzeugung wird abgebrochen..."
        if self.language == "zh":
            return "正在停止模板生成..."
        return "Stopping schema generation..."

    def _schema_ocr_label_text(self) -> str:
        if self.language == "de":
            return "OCR verwenden"
        if self.language == "zh":
            return "使用OCR"
        return "Use OCR"

    def _toggle_on_text(self) -> str:
        if self.language == "de":
            return "Ein"
        if self.language == "zh":
            return "开"
        return "On"

    def _toggle_off_text(self) -> str:
        if self.language == "de":
            return "Aus"
        if self.language == "zh":
            return "关"
        return "Off"

    def _schema_ocr_label_text(self) -> str:
        if self.language == "de":
            return "OCR verwenden"
        if self.language == "zh":
            return "\u4f7f\u7528OCR"
        return "Use OCR"

    def _toggle_on_text(self) -> str:
        if self.language == "de":
            return "Ein"
        if self.language == "zh":
            return "\u5f00"
        return "On"

    def _toggle_off_text(self) -> str:
        if self.language == "de":
            return "Aus"
        if self.language == "zh":
            return "\u5173"
        return "Off"

    def set_task_running(self, running: bool) -> None:
        self.generate_button.setDisabled(running)
        self.load_button.setDisabled(running)
        self.save_button.setDisabled(running)
        self.browse_button.setDisabled(running)
        self.family_combo.setDisabled(running)
        self.abort_button.setDisabled(not running)


class PidInconsistencyPage(BasePage):
    ROW_ID_ROLE = Qt.ItemDataRole.UserRole + 2
    STATUS_COLUMNS = {
        2: "pdf",
        3: "xml",
        4: "stellenplaene",
        5: "verschaltungslisten",
        6: "ifc",
    }

    def __init__(
        self,
        workbench: Workbench,
        refresh_all: Callable[[], None],
        open_review_callback: Callable[[dict[str, object]], None],
    ) -> None:
        super().__init__(
            workbench,
            refresh_all,
            "page.pid_inconsistency.title",
            "page.pid_inconsistency.subtitle",
        )
        self.open_review_callback = open_review_callback
        self._report: PidInconsistencySummary | None = None
        self._loaded_signature: tuple[object, ...] | None = None
        self._dirty = True
        self._visible_rows: list[PidInconsistencyRow] = []
        self._visible_row_map: dict[str, PidInconsistencyRow] = {}
        self._source_preview_dialog: ValueSourcePreviewDialog | None = None
        self._pending_click_payload: dict[str, object] | None = None
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._commit_pending_item_click)

        action_bar = QWidget(self)
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(12)
        self.run_button = PrimaryPushButton("")
        self.run_button.clicked.connect(self.start_detection)
        self.export_button = PrimaryPushButton("")
        self.export_button.clicked.connect(self.export_uc1_workbook)
        self.status_label = BodyLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        action_layout.addWidget(self.run_button)
        action_layout.addWidget(self.export_button)
        action_layout.addWidget(self.status_label, 1)
        action_layout.addStretch(1)
        self.root_layout.addWidget(action_bar)

        summary_bar = QWidget(self)
        summary_layout = QHBoxLayout(summary_bar)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(16)
        self.total_components_label = StrongBodyLabel("")
        self.problem_components_label = StrongBodyLabel("")
        self.problem_items_label = StrongBodyLabel("")
        summary_layout.addWidget(self.total_components_label)
        summary_layout.addWidget(self.problem_components_label)
        summary_layout.addWidget(self.problem_items_label)
        summary_layout.addStretch(1)
        self.root_layout.addWidget(summary_bar)

        self.empty_label = BodyLabel("")
        self.empty_label.setWordWrap(True)
        self.root_layout.addWidget(self.empty_label)

        self.table = TableWidget(self)
        self.table.setWordWrap(True)
        self.table.itemClicked.connect(self._handle_item_clicked)
        self.table.itemDoubleClicked.connect(self._handle_item_double_clicked)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.root_layout.addWidget(self.table, 1)

        self.apply_language()
        self.refresh()

    def apply_language(self) -> None:
        super().apply_language()
        self.run_button.setText(self._run_button_text())
        self.export_button.setText(self._export_button_text())
        if self._source_preview_dialog is not None:
            self._source_preview_dialog.apply_language()
        _set_table_headers(
            self.table,
            [
                self.t("pid.header.component"),
                self.t("pid.header.canonical_tag"),
                self.t("pid.header.pdf"),
                self.t("pid.header.xml"),
                self.t("pid.header.stellenplaene"),
                self.t("pid.header.verschaltungslisten"),
                self.t("pid.header.ifc"),
                self.t("pid.header.issues"),
            ],
        )
        self._configure_table_behavior()
        self._render_report()

    def refresh(self, *_args) -> None:
        signature = self._current_signature()
        self._dirty = self._loaded_signature != signature or self._report is None
        self._render_report()

    def start_detection(self) -> None:
        self.log_debug("pid_detection_clicked", "Exitenz Konfidenz detection clicked")
        self.run_background_task(
            "load_pid_inconsistency_report",
            "busy.pid.title",
            "busy.pid.body",
            "common.extraction_failed",
            self._handle_report_complete,
        )

    def _current_signature(self) -> tuple[object, ...]:
        latest_run = self.workbench.latest_run_summary()
        return (
            latest_run.run_id if latest_run else 0,
            len(self.workbench.documents),
            len(self.workbench.ri_bundles),
            len(self.workbench.records),
            len(self.workbench.ri_bundle_schemas),
        )

    def _handle_report_complete(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        report_payload = result
        snapshot_payload = result.get("snapshot")
        nested_report_payload = result.get("report")
        if isinstance(snapshot_payload, dict):
            self.workbench.apply_snapshot_payload(snapshot_payload)
            self.workbench.reload_schemas()
            self.workbench.reload_records()
        if isinstance(nested_report_payload, dict):
            report_payload = nested_report_payload
        self._report = PidInconsistencySummary.model_validate(report_payload)
        self._loaded_signature = self._current_signature()
        self._dirty = False
        self._render_report()
        self.show_success_banner(self._report_complete_text(self._report))

    def _render_report(self) -> None:
        report = self._report or PidInconsistencySummary(empty_reason="no_ri_data")
        self.status_label.clear()
        self.status_label.setVisible(False)
        self.total_components_label.setText(
            self.t("pid.summary.total", count=report.total_components)
        )
        self.problem_components_label.setText(
            self.t("pid.summary.problem_components", count=report.problem_component_count)
        )
        self.problem_items_label.setText(
            self.t("pid.summary.problem_items", count=report.problem_item_count)
        )
        rows = report.rows
        self._visible_rows = rows
        self._visible_row_map = {self._row_identifier(row): row for row in rows}
        self.table.setRowCount(len(rows))
        if not rows:
            self.empty_label.setVisible(True)
            self.empty_label.setText(self._empty_text())
            self.table.reapply_saved_sort()
            self._wrap_long_headers()
            return
        self.empty_label.setVisible(False)
        for row_index, row in enumerate(rows):
            self._set_plain_item(row_index, 0, row.display_name, row_data=row)
            self._set_plain_item(row_index, 1, row.canonical_tag, row_data=row)
            self._set_status_item(row_index, 2, "pdf", row.pdf_status, row)
            self._set_status_item(row_index, 3, "xml", row.xml_status, row)
            self._set_status_item(row_index, 4, "stellenplaene", row.stellenplaene_status, row)
            self._set_status_item(row_index, 5, "verschaltungslisten", row.verschaltungslisten_status, row)
            self._set_status_item(row_index, 6, "ifc", row.ifc_match_status, row)
            issues_text = self._issues_text(row.issues)
            self._set_plain_item(
                row_index,
                7,
                str(row.issue_count),
                sort_value=row.issue_count,
                tooltip=issues_text if issues_text else self.t("pid.issue.none"),
                row_data=row,
            )
        self.table.reapply_saved_sort()
        self._wrap_long_headers()

    def _row_identifier(self, row: PidInconsistencyRow) -> str:
        return "::".join(
            [
                row.scope_id or "",
                row.component_key or "",
                row.normalized_key or "",
                row.display_name or "",
            ]
        )

    def _set_plain_item(
        self,
        row: int,
        column: int,
        value: str,
        sort_value=None,
        tooltip: str = "",
        row_data: PidInconsistencyRow | None = None,
    ) -> None:
        display_text = self._single_line_text(value)
        item = SortableTableWidgetItem(display_text, display_text if sort_value is None else sort_value)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setToolTip(tooltip or value)
        if row_data is not None:
            item.setData(self.ROW_ID_ROLE, self._row_identifier(row_data))
        self.table.setItem(row, column, item)

    def _set_status_item(self, row_index: int, column: int, source_key: str, status: str, row: PidInconsistencyRow) -> None:
        symbol = self._status_symbol(status)
        item = SortableTableWidgetItem(symbol, self._status_sort_value(status))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QBrush(self._status_color(status)))
        item.setToolTip(self._status_tooltip(source_key, status, row))
        item.setData(self.ROW_ID_ROLE, self._row_identifier(row))
        jump_target = row.jump_targets.get(source_key)
        if status == "present" and jump_target is not None and jump_target.matching_record_keys:
            item.setData(Qt.ItemDataRole.UserRole, jump_target.model_dump(mode="json"))
        self.table.setItem(row_index, column, item)

    def _status_symbol(self, status: str) -> str:
        if status == "present":
            return "✓"
        if status == "conflict":
            return "!"
        if status == "deferred":
            return "…"
        if status == "not_required":
            return "—"
        return "✗"

    def _status_color(self, status: str) -> QColor:
        if status == "present":
            return QColor("#7ee787" if isDarkTheme() else "#0f7b0f")
        if status == "deferred":
            return QColor("#d29922" if isDarkTheme() else "#a15c00")
        if status == "not_required":
            return QColor("#8b949e" if isDarkTheme() else "#6e7681")
        return QColor("#ff6b6b" if isDarkTheme() else "#c42b1c")

    def _status_sort_value(self, status: str) -> int:
        return {
            "missing": 0,
            "conflict": 1,
            "deferred": 2,
            "not_required": 3,
            "present": 4,
        }.get(status, -1)

    def _status_tooltip(self, source_key: str, status: str, row: PidInconsistencyRow) -> str:
        if source_key == "ifc":
            return self._ifc_status_text(status)
        if status == "present":
            return self.t(f"pid.status.{source_key}.present")
        if status == "conflict":
            return self.t(f"pid.status.{source_key}.conflict")
        return self.t(f"pid.status.{source_key}.missing")

    def _issues_text(self, issues: list[str]) -> str:
        if not issues:
            return ""
        return "\n".join(self._issue_text(issue) for issue in issues)

    def _handle_item_clicked(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        payload = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(payload, dict) and payload.get("matching_record_keys"):
            self._pending_click_payload = payload
            self._click_timer.start(220)

    def _handle_item_double_clicked(self, item: QTableWidgetItem) -> None:
        self._click_timer.stop()
        self._pending_click_payload = None
        if item is None:
            return
        row = self._row_for_table_index(item.row())
        if row is None:
            return
        if item.column() == 0:
            self.open_review_callback(self._component_review_payload(row, item.text()))
            return
        source_key = self.STATUS_COLUMNS.get(item.column())
        if source_key not in {"pdf", "xml", "stellenplaene", "verschaltungslisten"}:
            return
        if self._source_status_for_row(row, source_key) != "present":
            return
        context = self.workbench.pid_source_preview_context(row, source_key)
        if context is None:
            self.show_info_banner(self._preview_unavailable_text())
            return
        self._open_source_preview_payload(context)

    def _row_for_table_index(self, row_index: int) -> PidInconsistencyRow | None:
        if 0 <= row_index < self.table.rowCount():
            item = self.table.item(row_index, 0)
            if item is not None:
                row_id = item.data(self.ROW_ID_ROLE)
                if isinstance(row_id, str) and row_id:
                    mapped_row = self._visible_row_map.get(row_id)
                    if mapped_row is not None:
                        return mapped_row
        if 0 <= row_index < len(self._visible_rows):
            return self._visible_rows[row_index]
        return None

    def _component_review_payload(self, row: PidInconsistencyRow, fallback_keyword: str) -> dict[str, object]:
        matching_record_keys: list[str] = []
        for source_key in ("pdf", "xml", "stellenplaene", "verschaltungslisten"):
            target = row.jump_targets.get(source_key)
            if target is None:
                continue
            for record_key in target.matching_record_keys:
                if record_key and record_key not in matching_record_keys:
                    matching_record_keys.append(record_key)
        preferred_target = row.jump_targets.get("xml") or row.jump_targets.get("pdf")
        return {
            "keyword": row.canonical_tag or fallback_keyword,
            "matching_record_keys": matching_record_keys,
            "preferred_record_key": preferred_target.preferred_record_key if preferred_target is not None else "",
            "preferred_source_root": preferred_target.preferred_source_root if preferred_target is not None else "",
            "preferred_scope_id": row.scope_id,
        }

    def _source_status_for_row(self, row: PidInconsistencyRow, source_key: str) -> str:
        return {
            "pdf": row.pdf_status,
            "xml": row.xml_status,
            "stellenplaene": row.stellenplaene_status,
            "verschaltungslisten": row.verschaltungslisten_status,
            "ifc": row.ifc_match_status,
        }.get(source_key, "")

    def _open_source_preview_payload(self, payload: dict[str, object]) -> None:
        evidences = payload.get("evidences", [])
        if not isinstance(evidences, list) or not evidences:
            self.show_info_banner(self._preview_unavailable_text())
            return
        if self._source_preview_dialog is None:
            self._source_preview_dialog = ValueSourcePreviewDialog(
                str(self.workbench.workspace_root),
                lambda: self.language,
                self,
            )
        self._source_preview_dialog.open_preview(
            source_path=str(payload.get("source_path", "")),
            record_display_name=str(payload.get("record_display_name", "")),
            field_name=str(payload.get("field_name", "")),
            target_value=str(payload.get("target_value", "")),
            evidences=evidences,
            initial_index=0,
        )

    def _commit_pending_item_click(self) -> None:
        payload = dict(self._pending_click_payload or {})
        self._pending_click_payload = None
        if not payload.get("matching_record_keys"):
            return
        self.log_debug(
            "pid_row_clicked",
            f"Exitenz Konfidenz row clicked for keyword {payload.get('keyword', '')}",
            details={"payload": payload},
        )
        self.open_review_callback(payload)

    def _configure_table_behavior(self) -> None:
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        header.setTextElideMode(Qt.TextElideMode.ElideNone)
        line_spacing = header.fontMetrics().lineSpacing()
        header.setMinimumHeight(line_spacing * 2 + 10)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setMinimumSectionSize(46)

    def _wrap_long_headers(self) -> None:
        header = self.table.horizontalHeader()
        col_count = self.table.columnCount()
        if col_count <= 0:
            return
        available = self.table.viewport().width()
        if available <= 0:
            return
        col_width = available // col_count
        if col_width < 40:
            return
        fm = header.fontMetrics()
        padding = 16
        for col in range(col_count):
            item = self.table.horizontalHeaderItem(col)
            if item is None:
                continue
            text = item.text().replace("\n", " ")
            if fm.horizontalAdvance(text) + padding <= col_width:
                item.setText(text)
                continue
            words = text.split(" ")
            if len(words) <= 1:
                continue
            best = len(words) // 2
            wrapped = " ".join(words[:best]) + "\n" + " ".join(words[best:])
            item.setText(wrapped)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._wrap_long_headers()

    def _single_line_text(self, text: str) -> str:
        return " ".join((text or "").splitlines())

    def _display_text_width(self, metrics, text: str) -> int:
        return metrics.horizontalAdvance(self._single_line_text(text))

    def _apply_default_column_widths(self) -> None:
        header = self.table.horizontalHeader()
        header_metrics = header.fontMetrics()
        header_padding = 28
        cell_padding = 28
        for column_index in range(self.table.columnCount()):
            header_item = self.table.horizontalHeaderItem(column_index)
            header_text = header_item.text() if header_item else ""
            header_width = self._display_text_width(header_metrics, header_text) + header_padding
            content_width = max(0, self.table.sizeHintForColumn(column_index)) + cell_padding
            self.table.setColumnWidth(column_index, max(header_width, content_width))

    def export_uc1_workbook(self) -> None:
        self.run_background_task(
            "export_use_case_1_workbook",
            f"literal:{self._export_busy_title_text()}",
            f"literal:{self._export_busy_body_text()}",
            "common.extraction_failed",
            self._handle_uc1_export_complete,
        )

    def _handle_uc1_export_complete(self, result: object) -> None:
        if not isinstance(result, str):
            return
        self.show_success_banner(self._export_success_text(result))

    def _run_button_text(self) -> str:
        if self.language == "de":
            return "Prüfung starten"
        if self.language == "zh":
            return "开始检测"
        return "Start Detection"

    def _export_button_text(self) -> str:
        if self.language == "de":
            return "Konfidenz-Arbeitsmappe exportieren"
        if self.language == "zh":
            return "导出置信检测工作簿"
        return "Export Confidence Workbook"

    def _export_busy_title_text(self) -> str:
        if self.language == "de":
            return "Konfidenz-Arbeitsmappe wird exportiert"
        if self.language == "zh":
            return "正在导出置信检测工作簿"
        return "Exporting confidence workbook"

    def _export_busy_body_text(self) -> str:
        if self.language == "de":
            return "Die Exitenz Konfidenz Ergebnisse werden als Arbeitsmappe geschrieben."
        if self.language == "zh":
            return "正在写入 Exitenz Konfidenz 检测结果工作簿。"
        return "Writing the Exitenz Konfidenz results workbook."

    def _ifc_status_text(self, status: str) -> str:
        labels = {
            "present": {
                "de": "IFC-Match vorhanden",
                "zh": "已找到 IFC 精确匹配",
                "en": "Exact IFC match found",
            },
            "missing": {
                "de": "Kein IFC-Match gefunden",
                "zh": "未找到 IFC 精确匹配",
                "en": "No exact IFC match found",
            },
            "deferred": {
                "de": "Kein IFC geladen",
                "zh": "当前未加载 IFC",
                "en": "No IFC document loaded",
            },
            "not_required": {
                "de": "IFC-Prüfung nicht erforderlich",
                "zh": "无需 IFC 检查",
                "en": "IFC check not required",
            },
        }
        bundle = labels.get(status, labels["missing"])
        return bundle.get(self.language, bundle["en"])

    def _issue_text(self, issue: str) -> str:
        custom = {
            "missing_ifc": {
                "de": "Fehlender IFC-Eintrag",
                "zh": "缺少 IFC 对象",
                "en": "Missing IFC object",
            },
            "missing_flange": {
                "de": "Fehlende Flansch-Information",
                "zh": "缺少法兰信息",
                "en": "Missing flange information",
            },
        }
        if issue in custom:
            bundle = custom[issue]
            return bundle.get(self.language, bundle["en"])
        return self.t(f"pid.issue.{issue}")

    def _export_success_text(self, path: str) -> str:
        file_name = Path(path).name or path
        if self.language == "de":
            return f"Konfidenz-Arbeitsmappe exportiert: {file_name}"
        if self.language == "zh":
            return f"已导出置信检测工作簿：{file_name}"
        return f"Confidence workbook exported: {file_name}"

    def _preview_unavailable_text(self) -> str:
        if self.language == "de":
            return "Für diese Zelle ist derzeit keine präzise Vorschau verfügbar."
        if self.language == "zh":
            return "当前这个单元格还没有可用的精确证据预览。"
        return "No precise source preview is available for this cell yet."

    def _status_text(self, report: PidInconsistencySummary) -> str:
        if self._dirty and self._report is not None:
            if self.language == "de":
                return "Die Ergebnisse sind veraltet. Klicken Sie auf Prüfung starten."
            if self.language == "zh":
                return "当前结果已过期。点击开始检测更新。"
            return "The result is outdated. Click Start Detection."
        if report.rows:
            if self.language == "de":
                return "Ergebnis bereit. Grün markierte Quellen öffnen die Evidenz."
            if self.language == "zh":
                return "结果已生成。点击绿色对勾查看证据。"
            return "Result ready. Click a green check to inspect evidence."
        if self.language == "de":
            return "Klicken Sie auf Prüfung starten."
        if self.language == "zh":
            return "点击开始检测后生成结果。"
        return "Click Start Detection to build the result."

    def _report_complete_text(self, report: PidInconsistencySummary) -> str:
        if self.language == "de":
            return (
                f"Exitenz Konfidenz abgeschlossen: "
                f"{report.problem_component_count} Komponenten, "
                f"{report.problem_item_count} Eintraege."
            )
        if self.language == "zh":
            return (
                f"Exitenz Konfidenz 检测完成："
                f"{report.problem_component_count} 个组件，{report.problem_item_count} 个问题项。"
            )
        return (
            f"Exitenz Konfidenz complete: "
            f"{report.problem_component_count} components, "
            f"{report.problem_item_count} items."
        )

    def _empty_text(self) -> str:
        if self._report is None:
            if self.language == "de":
                return "Noch kein Ergebnis. Klicken Sie auf Prüfung starten."
            if self.language == "zh":
                return "当前还没有结果。点击开始检测。"
            return "No result yet. Click Start Detection."
        return self.t("pid.empty.no_data")

    def set_task_running(self, running: bool) -> None:
        self.run_button.setDisabled(running)
        self.export_button.setDisabled(running)
        self.table.setDisabled(running)


class ExtractionReviewPage(BasePage):
    def __init__(self, workbench: Workbench, refresh_all: Callable[[], None]) -> None:
        super().__init__(
            workbench,
            refresh_all,
            "page.extraction_review.title",
            "page.extraction_review.subtitle",
        )
        self._review_thread: QThread | None = None
        self._review_worker: TaskWorker | None = None
        self._review_request_mode = "search"
        self._review_request_offset = 0
        self._review_request_reset_page = False
        self._review_generation = 0
        self._active_review_generation = 0
        self._pending_page_offsets: list[int] = []
        self._pending_review_jump: dict[str, object] | None = None
        self._show_run_summary = False
        self._extraction_task_running = False
        self._source_preview_dialog: ValueSourcePreviewDialog | None = None
        self._last_standardized_workbook = ""
        self._last_aas_bundle: dict[str, list[str]] = {}
        self._last_ontology_path = ""
        self._content_mode = "documents"
        self._excel_workbook_items: list[dict[str, str]] = []
        self._loaded_excel_mode = ""
        self._global_review_targets: list[dict[str, object]] = []
        self._current_global_review_target_index = -1
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.timeout.connect(lambda: self.review_model.set_highlighted_record_keys(set()))
        self._excel_highlight_timer = QTimer(self)
        self._excel_highlight_timer.setSingleShot(True)
        self._excel_highlight_timer.timeout.connect(lambda: self.excel_model.set_highlighted_coord(""))

        toolbar = QWidget(self)
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(12)

        scan_toolbar = QWidget(toolbar)
        scan_layout = QHBoxLayout(scan_toolbar)
        scan_layout.setContentsMargins(0, 0, 0, 0)
        scan_layout.setSpacing(12)
        self.scan_root_label = BodyLabel("")
        self.scan_root_edit = QLineEdit(self)
        self.scan_root_edit.setReadOnly(True)
        self.scan_root_browse_button = PrimaryPushButton("")
        self.scan_root_browse_button.clicked.connect(self.select_scan_root)
        scan_layout.addWidget(self.scan_root_label)
        scan_layout.addWidget(self.scan_root_edit, 1)
        scan_layout.addWidget(self.scan_root_browse_button)
        toolbar_layout.addWidget(scan_toolbar)

        export_toolbar = QWidget(toolbar)
        export_layout = QHBoxLayout(export_toolbar)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.setSpacing(12)
        self.export_path_label = BodyLabel("")
        self.export_path_edit = QLineEdit(self)
        self.export_path_edit.setReadOnly(True)
        self.export_browse_button = PrimaryPushButton("")
        self.export_browse_button.clicked.connect(self.select_export_dir)
        self.aas_button = PrimaryPushButton("")
        self.aas_button.clicked.connect(self.generate_uc1_aas_models)
        self.ontology_button = PrimaryPushButton("")
        self.ontology_button.clicked.connect(self.export_uc1_source_ontologies)
        export_layout.addWidget(self.export_path_label)
        export_layout.addWidget(self.export_path_edit, 1)
        export_layout.addWidget(self.export_browse_button)
        toolbar_layout.addWidget(export_toolbar)

        actions_row = QWidget(toolbar)
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        self.scan_button = PrimaryPushButton("")
        self.scan_button.clicked.connect(self.scan_workspace)
        self.start_extraction_button = PrimaryPushButton("")
        self.start_extraction_button.clicked.connect(self.start_extraction)
        self.save_results_button = PrimaryPushButton("")
        self.save_results_button.clicked.connect(self.save_results)
        self.stop_button = PrimaryPushButton("")
        self.stop_button.clicked.connect(self.stop_extraction)
        self.stop_button.setDisabled(True)
        actions_layout.addWidget(self.scan_button)
        actions_layout.addWidget(self.start_extraction_button)
        actions_layout.addWidget(self.save_results_button)
        actions_layout.addWidget(self.stop_button)
        actions_layout.addSpacing(8)
        actions_layout.addWidget(self.aas_button)
        actions_layout.addWidget(self.ontology_button)
        actions_layout.addStretch(1)
        toolbar_layout.addWidget(actions_row)

        self.status_label = BodyLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        toolbar_layout.addWidget(self.status_label)

        self.export_status_label = BodyLabel("")
        self.export_status_label.setWordWrap(True)
        self.export_status_label.setVisible(False)
        toolbar_layout.addWidget(self.export_status_label)
        self.root_layout.addWidget(toolbar)

        content_toolbar = QWidget(self)
        content_layout = QHBoxLayout(content_toolbar)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        self.content_mode_label = BodyLabel("")
        self.content_mode_combo = ComboBox(self)
        self.content_mode_combo.currentIndexChanged.connect(self._handle_content_mode_changed)
        self.content_mode_combo.setMinimumWidth(120)
        self.content_mode_combo.setMaximumWidth(150)
        self.schema_family_label = BodyLabel("")
        self.schema_family_combo = ComboBox(self)
        self.schema_family_combo.currentIndexChanged.connect(self._load_selected_pipeline_schema)
        self.schema_family_combo.setMinimumWidth(220)
        self.schema_family_combo.setMaximumWidth(320)
        content_layout.addWidget(self.content_mode_label)
        content_layout.addWidget(self.content_mode_combo)
        content_layout.addWidget(self.schema_family_label)
        content_layout.addWidget(self.schema_family_combo)
        content_layout.addSpacing(12)

        self.review_controls = QWidget(content_toolbar)
        self.review_controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        review_layout = QHBoxLayout(self.review_controls)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.setSpacing(12)
        self.family_label = BodyLabel("")
        self.family_combo = ComboBox(self)
        self.family_combo.currentIndexChanged.connect(self._handle_family_changed)
        self.family_combo.setMinimumWidth(140)
        self.family_combo.setMaximumWidth(320)
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setMinimumWidth(220)
        self.search_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search_edit.returnPressed.connect(self.search_edit.search)
        self.search_edit.searchSignal.connect(lambda *_args: self.apply_keyword_filter())
        self.search_edit.clearSignal.connect(lambda *_args: self.clear_keyword_filter())
        self.search_loading_ring = IndeterminateProgressRing(self)
        self.search_loading_ring.setFixedSize(18, 18)
        self.search_loading_ring.setVisible(False)
        self.page_info_label = BodyLabel("")
        self.page_info_label.setWordWrap(True)
        self.page_info_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        review_layout.addWidget(self.family_label)
        review_layout.addWidget(self.family_combo)
        review_layout.addWidget(self.search_edit)
        review_layout.addWidget(self.search_loading_ring)
        review_layout.addWidget(self.page_info_label, 1)
        content_layout.addWidget(self.review_controls)

        self.excel_controls = QWidget(content_toolbar)
        self.excel_controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        excel_layout = QHBoxLayout(self.excel_controls)
        excel_layout.setContentsMargins(0, 0, 0, 0)
        excel_layout.setSpacing(12)
        self.excel_sheet_label = BodyLabel("")
        self.excel_sheet_combo = ComboBox(self)
        self.excel_sheet_combo.currentIndexChanged.connect(self._handle_excel_sheet_changed)
        self.excel_sheet_combo.setMinimumWidth(180)
        self.excel_sheet_combo.setMaximumWidth(320)
        self.excel_info_label = BodyLabel("")
        self.excel_info_label.setWordWrap(True)
        self.excel_info_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.prev_review_cell_button = PrimaryPushButton("")
        self.prev_review_cell_button.setFixedWidth(36)
        self.prev_review_cell_button.clicked.connect(lambda: self._navigate_global_review_target(-1))
        self.review_cell_counter_label = StrongBodyLabel("0 / 0")
        self.review_cell_counter_label.setMinimumWidth(72)
        self.review_cell_counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_review_cell_button = PrimaryPushButton("")
        self.next_review_cell_button.setFixedWidth(36)
        self.next_review_cell_button.clicked.connect(lambda: self._navigate_global_review_target(1))
        excel_layout.addWidget(self.excel_sheet_label)
        excel_layout.addWidget(self.excel_sheet_combo)
        excel_layout.addWidget(self.excel_info_label, 1)
        excel_layout.addWidget(self.prev_review_cell_button)
        excel_layout.addWidget(self.review_cell_counter_label)
        excel_layout.addWidget(self.next_review_cell_button)
        content_layout.addWidget(self.excel_controls)
        content_layout.addStretch(1)
        self.root_layout.addWidget(content_toolbar)

        self.docs_table = TableWidget(self)
        self.root_layout.addWidget(self.docs_table, 1)

        self.schema_table = TableWidget(self)
        self.root_layout.addWidget(self.schema_table, 1)

        self.search_progress_bar = ProgressBar(self)
        self.search_progress_bar.setValue(0)
        self.search_progress_bar.setTextVisible(True)
        self.search_progress_bar.setVisible(False)
        self.root_layout.addWidget(self.search_progress_bar)

        self.review_model = ReviewTableModel(self.workbench, self.language)
        self.review_table = ReviewTableView(self)
        self.review_table.setModel(self.review_model)
        self.review_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.review_table.horizontalHeader().setDefaultSectionSize(240)
        self.review_table.verticalHeader().setDefaultSectionSize(48)
        self.review_table.setWordWrap(True)
        self.review_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.review_table.customContextMenuRequested.connect(self._show_review_context_menu)
        self.review_table.doubleClicked.connect(self._handle_review_table_double_clicked)
        self.root_layout.addWidget(self.review_table, 1)
        self.review_model.page_requested.connect(
            self._queue_review_page_request,
            type=Qt.ConnectionType.QueuedConnection,
        )

        self.excel_model = ExcelPreviewTableModel(self.workbench, self.language)
        self.excel_table = ReviewTableView(self)
        self.excel_table.setModel(self.excel_model)
        self.excel_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.excel_table.horizontalHeader().setDefaultSectionSize(160)
        self.excel_table.verticalHeader().setDefaultSectionSize(34)
        self.excel_table.setWordWrap(True)
        self.excel_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.excel_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.excel_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.excel_table.set_enter_starts_editing(True)
        self.excel_table.set_delete_clears_selected_cells(True)
        self.excel_table.set_modifier_drag_adds_selection(True)
        self.excel_table.setItemDelegate(ExcelEvidenceCellDelegate(self.excel_table))
        self.excel_table.doubleClicked.connect(self._handle_excel_table_double_clicked)
        self.root_layout.addWidget(self.excel_table, 1)
        self.excel_model.cell_edited.connect(self._handle_excel_cell_edited)
        self.apply_language()
        self.refresh()

    def apply_language(self) -> None:
        super().apply_language()
        self.scan_button.setText(self.t("common.scan_workspace"))
        self.start_extraction_button.setText(self.t("common.start_extraction"))
        self.save_results_button.setText(self.t("common.save_extraction_results"))
        self.stop_button.setText(self._stop_button_text())
        self.scan_root_label.setText(self.t("common.scan_root"))
        self.scan_root_browse_button.setText(self.t("common.browse"))
        self.export_path_label.setText(self.t("common.export_dir"))
        self.export_browse_button.setText(self.t("common.browse"))
        self.aas_button.setText(self._aas_button_text())
        self.ontology_button.setText(self._ontology_button_text())
        for button in (
            self.scan_button,
            self.start_extraction_button,
            self.save_results_button,
            self.stop_button,
            self.aas_button,
            self.ontology_button,
        ):
            button.setToolTip(button.text())
        self._apply_button_widths()
        self.content_mode_label.setText(self._content_mode_label_text())
        self.schema_family_label.setText(self.t("common.family"))
        self.excel_sheet_label.setText(self._excel_sheet_label_text())
        self.prev_review_cell_button.setText("‹")
        self.next_review_cell_button.setText("›")
        self.prev_review_cell_button.setToolTip(self._previous_review_cell_text())
        self.next_review_cell_button.setToolTip(self._next_review_cell_text())
        self.excel_model.set_language(self.language)
        self._refresh_content_mode_combo()
        _set_table_headers(
            self.docs_table,
            [
                self.t("project.header.relative_path"),
                self.t("project.header.source_kind"),
                self.t("project.header.output_families"),
                self.t("project.header.size"),
            ],
        )
        _set_table_headers(
            self.schema_table,
            [
                self.t("schema.header.name"),
                self.t("schema.header.aliases"),
                self.t("schema.header.type"),
                self.t("schema.header.repeatable"),
                self.t("schema.header.hint"),
            ],
        )
        self.family_label.setText(self.t("common.family"))
        self.search_edit.setPlaceholderText(self._review_search_placeholder())
        self.review_model.set_fixed_headers(
            [
                self.t("review.header.family"),
                self.t("review.header.record"),
                self.t("review.header.source"),
            ]
        )
        self.review_model.set_language(self.language)
        self.review_table.reapply_saved_sort()
        self.excel_table.reapply_saved_sort()
        if self._source_preview_dialog is not None:
            self._source_preview_dialog.apply_language()
        self._refresh_pipeline_schema_combo()
        self._apply_content_mode()
        self._update_page_info()
        self._refresh_global_review_navigation()

    def refresh(self, *_args) -> None:
        self.scan_root_edit.setText(str(self.workbench.resolve_scan_root()))
        self.export_path_edit.setText(str(self.workbench.resolve_results_export_dir()))
        self._clear_export_status()
        self._refresh_status_label()
        self._populate_scan_results()
        self._refresh_pipeline_schema_combo()
        self._load_selected_pipeline_schema()
        self._refresh_family_combo()
        self._refresh_excel_workbook_items()
        self._refresh_global_review_targets()
        if self.review_model.total_count <= 0:
            family_filter, keyword = self._current_review_filters()
            self.review_model.set_filters(family_filter, keyword)
        latest_run = self.workbench.latest_run_summary()
        has_extracted_records = bool(latest_run and latest_run.record_count > 0)
        if (self._content_mode == "extracted" or self._is_excel_mode(self._content_mode)) and not has_extracted_records:
            self._content_mode = "schemas" if (self.workbench.schemas or self.workbench.ri_bundle_schemas) else "documents"
        elif self._content_mode == "schemas" and not (self.workbench.schemas or self.workbench.ri_bundle_schemas):
            self._content_mode = "documents"
        if has_extracted_records and self._excel_workbook_items:
            valid_modes = {self._excel_mode_for_item(item) for item in self._excel_workbook_items}
            if self._content_mode == "extracted" or self._content_mode not in valid_modes | {"documents", "schemas"}:
                self._content_mode = self._excel_mode_for_item(self._excel_workbook_items[0])
        self._refresh_content_mode_combo()
        self._apply_content_mode()
        self._update_page_info()
        self._refresh_global_review_navigation()

    def _refresh_content_mode_combo(self) -> None:
        current_mode = str(self.content_mode_combo.currentData() or self._content_mode)
        self.content_mode_combo.blockSignals(True)
        try:
            self.content_mode_combo.clear()
            self.content_mode_combo.addItem(self._documents_mode_text(), userData="documents")
            self.content_mode_combo.addItem(self._schemas_mode_text(), userData="schemas")
            if self._excel_items_visible():
                for item in self._excel_workbook_items:
                    self.content_mode_combo.addItem(
                        self._excel_workbook_label(item),
                        userData=self._excel_mode_for_item(item),
                    )
            else:
                self.content_mode_combo.addItem(self._extracted_mode_text(), userData="extracted")
            target_index = 0
            for index in range(self.content_mode_combo.count()):
                if self.content_mode_combo.itemData(index) == current_mode:
                    target_index = index
                    break
            self.content_mode_combo.setCurrentIndex(target_index)
        finally:
            self.content_mode_combo.blockSignals(False)

    def _handle_content_mode_changed(self, *_args) -> None:
        self._content_mode = str(self.content_mode_combo.currentData() or "documents")
        self._apply_content_mode()

    def _apply_content_mode(self) -> None:
        mode = self._content_mode
        show_documents = mode == "documents"
        show_schemas = mode == "schemas"
        show_review = mode == "extracted"
        show_excel = self._is_excel_mode(mode)
        self.docs_table.setVisible(show_documents)
        self.schema_family_label.setVisible(show_schemas)
        self.schema_family_combo.setVisible(show_schemas)
        self.schema_table.setVisible(show_schemas)
        self.family_label.setVisible(show_review)
        self.family_combo.setVisible(show_review)
        self.search_edit.setVisible(show_review)
        self.search_loading_ring.setVisible(show_review and self.search_loading_ring.isVisible())
        self.page_info_label.setVisible(show_review)
        self.review_table.setVisible(show_review)
        self.review_controls.setVisible(show_review)
        self.excel_controls.setVisible(show_excel)
        self.excel_table.setVisible(show_excel)
        self.search_progress_bar.setVisible(show_review and self.search_progress_bar.isVisible())
        if show_excel:
            self._load_selected_excel_workbook()

    def _refresh_excel_workbook_items(self) -> None:
        self._excel_workbook_items = self.workbench.filled_excel_workbook_items()
        valid_modes = {self._excel_mode_for_item(item) for item in self._excel_workbook_items}
        if self._loaded_excel_mode and self._loaded_excel_mode not in valid_modes:
            self._loaded_excel_mode = ""
            self.excel_model.set_workbook(None)

    def _refresh_global_review_targets(self) -> None:
        previous_key = self._global_review_target_key(
            self._global_review_targets[self._current_global_review_target_index]
        ) if 0 <= self._current_global_review_target_index < len(self._global_review_targets) else ""
        targets: list[dict[str, object]] = []
        if self._excel_items_visible():
            for workbook_order, item in enumerate(self._excel_workbook_items):
                workbook_name = str(item.get("workbook_name", ""))
                workbook = self.workbench.load_filled_excel_preview(workbook_name)
                if workbook is None:
                    continue
                for sheet_order, sheet in enumerate(workbook.sheets):
                    review_coords = set(sheet.cell_provenance) | set(sheet.tooltip_contexts)
                    for coord in sorted(review_coords, key=self._excel_coord_sort_key):
                        provenance = sheet.cell_provenance.get(coord)
                        context = sheet.tooltip_contexts.get(coord)
                        if not self.excel_model.is_review_cell_metadata(provenance, context):
                            continue
                        row_index, column_index = self.excel_model._coord_to_indexes(coord)
                        targets.append(
                            {
                                "workbook_name": workbook_name,
                                "sheet_name": sheet.name,
                                "coord": coord,
                                "row_index": row_index,
                                "column_index": column_index,
                                "provenance": provenance,
                                "sort_key": (workbook_order, sheet_order, row_index, column_index),
                            }
                        )
        targets.sort(key=lambda target: target.get("sort_key", (0, 0, 0, 0)))
        self._global_review_targets = targets
        self._current_global_review_target_index = -1
        if previous_key:
            for index, target in enumerate(targets):
                if self._global_review_target_key(target) == previous_key:
                    self._current_global_review_target_index = index
                    break
        self._refresh_global_review_navigation()

    def _global_review_target_key(self, target: dict[str, object]) -> str:
        return "::".join(
            [
                str(target.get("workbook_name", "")),
                str(target.get("sheet_name", "")),
                str(target.get("coord", "")),
            ]
        )

    def _excel_coord_sort_key(self, coord: str) -> tuple[int, int]:
        row_index, column_index = self.excel_model._coord_to_indexes(coord)
        return row_index, column_index

    def _refresh_global_review_navigation(self) -> None:
        total = len(self._global_review_targets)
        current = self._current_global_review_target_index + 1 if self._current_global_review_target_index >= 0 else 0
        if total <= 0:
            current = 0
        self.review_cell_counter_label.setText(f"{current} / {total}")
        self.prev_review_cell_button.setEnabled(total > 0)
        self.next_review_cell_button.setEnabled(total > 0)

    def _handle_excel_cell_edited(self, workbook_name: str, sheet_name: str, coord: str) -> None:
        previous_key = "::".join([workbook_name, sheet_name, coord])
        self._refresh_global_review_targets()
        self._current_global_review_target_index = -1
        for index, target in enumerate(self._global_review_targets):
            if self._global_review_target_key(target) == previous_key:
                self._current_global_review_target_index = index
                break
        self._refresh_global_review_navigation()

    def _navigate_global_review_target(self, delta: int) -> None:
        if not self._global_review_targets:
            return
        if self._current_global_review_target_index < 0:
            next_index = 0 if delta >= 0 else len(self._global_review_targets) - 1
        else:
            next_index = (self._current_global_review_target_index + delta) % len(self._global_review_targets)
        self._jump_to_global_review_target(next_index)

    def _jump_to_global_review_target(self, target_index: int) -> None:
        if not (0 <= target_index < len(self._global_review_targets)):
            return
        target = self._global_review_targets[target_index]
        workbook_name = str(target.get("workbook_name", ""))
        sheet_name = str(target.get("sheet_name", ""))
        coord = str(target.get("coord", ""))
        target_mode = f"excel:{workbook_name}"

        self.content_mode_combo.blockSignals(True)
        try:
            for index in range(self.content_mode_combo.count()):
                if self.content_mode_combo.itemData(index) == target_mode:
                    self.content_mode_combo.setCurrentIndex(index)
                    break
        finally:
            self.content_mode_combo.blockSignals(False)
        self._content_mode = target_mode
        self._apply_content_mode()

        self.excel_sheet_combo.blockSignals(True)
        try:
            for index in range(self.excel_sheet_combo.count()):
                if self.excel_sheet_combo.itemData(index) == sheet_name:
                    self.excel_sheet_combo.setCurrentIndex(index)
                    break
        finally:
            self.excel_sheet_combo.blockSignals(False)
        self.excel_model.set_sheet(sheet_name)

        model_index = self.excel_model.index_for_coord(coord)
        if not model_index.isValid():
            self._refresh_global_review_targets()
            return
        self._current_global_review_target_index = target_index
        self._refresh_global_review_navigation()
        self.excel_table.setCurrentIndex(model_index)
        self.excel_table.scrollTo(model_index, QTableView.ScrollHint.PositionAtCenter)
        self.excel_model.set_highlighted_coord(coord)
        self._excel_highlight_timer.start(1800)

    def _excel_items_visible(self) -> bool:
        latest_run = self.workbench.latest_run_summary()
        return bool(latest_run and latest_run.record_count > 0 and self._excel_workbook_items)

    def _is_excel_mode(self, mode: str) -> bool:
        return mode.startswith("excel:")

    def _excel_mode_for_item(self, item: dict[str, str]) -> str:
        return f"excel:{item.get('workbook_name', '')}"

    def _excel_workbook_name_from_mode(self, mode: str) -> str:
        return mode.split(":", 1)[1] if self._is_excel_mode(mode) else ""

    def _excel_workbook_label(self, item: dict[str, str]) -> str:
        name = item.get("workbook_name", "")
        category = item.get("category", "")
        return f"Excel: {category}/{name}" if category else f"Excel: {name}"

    def _load_selected_excel_workbook(self) -> None:
        workbook_name = self._excel_workbook_name_from_mode(self._content_mode)
        if not workbook_name:
            self.excel_model.set_workbook(None)
            self._refresh_excel_sheet_combo()
            return
        if self._loaded_excel_mode != self._content_mode:
            workbook = self.workbench.load_filled_excel_preview(workbook_name)
            self.excel_model.set_workbook(workbook)
            self._loaded_excel_mode = self._content_mode
            self._refresh_excel_sheet_combo()
        path = self.excel_model.workbook_path()
        if path:
            self.excel_info_label.setText(path)
        else:
            self.excel_info_label.setText(self._excel_missing_text())

    def _refresh_excel_sheet_combo(self) -> None:
        current = str(self.excel_sheet_combo.currentData() or "")
        sheet_names = self.excel_model.sheet_names()
        self.excel_sheet_combo.blockSignals(True)
        try:
            self.excel_sheet_combo.clear()
            for sheet_name in sheet_names:
                self.excel_sheet_combo.addItem(sheet_name, userData=sheet_name)
            if sheet_names:
                target_index = 0
                for index in range(self.excel_sheet_combo.count()):
                    if self.excel_sheet_combo.itemData(index) == current:
                        target_index = index
                        break
                self.excel_sheet_combo.setCurrentIndex(target_index)
        finally:
            self.excel_sheet_combo.blockSignals(False)

    def _handle_excel_sheet_changed(self, *_args) -> None:
        sheet_name = str(self.excel_sheet_combo.currentData() or "")
        if sheet_name:
            self.excel_model.set_sheet(sheet_name)
            self.excel_model.set_highlighted_coord("")

    def _populate_scan_results(self) -> None:
        snapshot = self.workbench.current_snapshot()
        self.docs_table.setRowCount(len(snapshot.documents))
        for row_index, document in enumerate(snapshot.documents):
            values = [
                document.relative_path,
                translate_source_kind(self.language, document.source_kind.value),
                ", ".join(translate_family(self.language, family) for family in document.output_families),
                str(document.size_bytes),
            ]
            for col_index, value in enumerate(values):
                sort_value = document.size_bytes if col_index == 3 else value
                self.docs_table.setItem(row_index, col_index, SortableTableWidgetItem(value, sort_value))
        self.docs_table.reapply_saved_sort()

    def _refresh_pipeline_schema_combo(self) -> None:
        previous_value = self.schema_family_combo.currentData()
        self.schema_family_combo.blockSignals(True)
        try:
            self.schema_family_combo.clear()
            for selection, _label in self.workbench.schema_selection_items():
                schema = self.workbench.schema_by_selection(selection)
                if schema is None:
                    continue
                self.schema_family_combo.addItem(self._pipeline_schema_selection_label(schema), userData=selection)
            if self.schema_family_combo.count() > 0:
                target_index = 0
                for row in range(self.schema_family_combo.count()):
                    if self.schema_family_combo.itemData(row) == previous_value:
                        target_index = row
                        break
                self.schema_family_combo.setCurrentIndex(target_index)
        finally:
            self.schema_family_combo.blockSignals(False)

    def _load_selected_pipeline_schema(self, *_args) -> None:
        selection = str(self.schema_family_combo.currentData() or "")
        schema = self.workbench.schema_by_selection(selection)
        if schema is None:
            self.schema_table.setRowCount(0)
            return
        self.schema_table.setRowCount(len(schema.fields))
        for row_index, field in enumerate(schema.fields):
            values = [
                field.name,
                " | ".join(field.aliases),
                field.value_type,
                "true" if field.repeatable else "false",
                field.extraction_hint,
            ]
            for col_index, value in enumerate(values):
                sort_value = field.repeatable if col_index == 3 else value
                self.schema_table.setItem(row_index, col_index, SortableTableWidgetItem(value, sort_value))
        self.schema_table.reapply_saved_sort()

    def _pipeline_schema_selection_label(self, schema: SchemaFamily) -> str:
        source_root = schema.source_root or self.workbench.family_source_root(schema.family)
        family_label = translate_family(self.language, schema.family)
        if schema.scope_id:
            bundle_name = schema.bundle_name or self.workbench.bundle_name_for_scope(schema.scope_id)
            return f"{source_root} / {bundle_name} / {family_label}"
        return f"{source_root} / {family_label}"

    def scan_workspace(self) -> None:
        self.log_debug(
            "inconsistence_extract_scan_clicked",
            f"Inconsistence Extract scan clicked for {self.workbench.resolve_scan_root()}",
        )
        self.run_background_task(
            "scan",
            "busy.scan.title",
            "busy.scan.body",
            "common.scan_failed",
            self._handle_pipeline_scan_complete,
        )

    def select_scan_root(self) -> None:
        self.log_debug("browse_scan_root_clicked", "Browse scan root clicked")
        chosen = self.choose_directory("dialog.select_scan_root", str(self.workbench.resolve_scan_root()))
        if chosen is None:
            return
        self.workbench.update_scan_root(chosen)
        self.scan_root_edit.setText(str(self.workbench.resolve_scan_root()))
        self._content_mode = "documents"
        self.refresh()

    def _handle_pipeline_scan_complete(self, result: object) -> None:
        snapshot = self.workbench.apply_snapshot_payload(result)
        self._content_mode = "documents"
        self._populate_scan_results()
        self._apply_content_mode()
        self._clear_export_status()
        self.show_success_banner(self._scan_summary_text(len(snapshot.documents)))
        self.refresh()

    def generate_schemas(self) -> None:
        self.log_debug(
            "inconsistence_extract_generate_schemas_clicked",
            "Inconsistence Extract generate schemas clicked",
            details={
                "use_ocr": self.workbench.settings.schema_generation_use_ocr,
                "ocr_runtime": self.workbench.ocr_runtime_status(),
            },
        )
        self.run_background_task(
            "generate_schemas",
            "busy.schema.title",
            "busy.schema.body",
            "common.schema_failed",
            self._handle_pipeline_schema_complete,
            payload={"use_ocr": self.workbench.settings.schema_generation_use_ocr},
        )

    def _handle_pipeline_schema_complete(self, _result: object) -> None:
        self.workbench.reload_schemas()
        self._content_mode = "schemas"
        self._refresh_pipeline_schema_combo()
        self._load_selected_pipeline_schema()
        self._apply_content_mode()
        self._clear_export_status()
        self.show_success_banner(self._schema_summary_text())
        self.refresh()

    def run_extraction(self) -> None:
        if self._task_thread is not None and self._task_thread.isRunning():
            return
        self._extraction_task_running = True
        self._show_run_summary = False
        self._clear_export_status()
        self._refresh_status_label()
        self.log_debug(
            "run_extraction_clicked",
            "Run extraction clicked",
            details={
                "use_ocr": self.workbench.settings.extraction_use_ocr,
                "retrieval_top_k": self.workbench.settings.retrieval_top_k,
                "ocr_runtime": self.workbench.ocr_runtime_status(),
            },
        )
        self.run_background_task(
            "run_extraction",
            "busy.extraction.title",
            "busy.extraction.body",
            "common.extraction_failed",
            self._handle_extraction_complete,
            payload={
                "retrieval_top_k": self.workbench.settings.retrieval_top_k,
                "use_ocr": self.workbench.settings.extraction_use_ocr,
            },
            on_complete=self._handle_extraction_task_complete,
            deliver_success_immediately=True,
        )

    def stop_extraction(self) -> None:
        if self._task_thread is None or self._task_worker is None or not self._task_thread.isRunning():
            return
        self.stop_button.setDisabled(True)
        self.cancel_background_task(self._current_task_abort_requested_text())

    def _handle_extraction_task_complete(self, _success: bool) -> None:
        self._extraction_task_running = False
        self.stop_button.setDisabled(True)

    def _handle_extraction_complete(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        summary = self.workbench.apply_run_summary_payload(result)
        self.workbench.reload_schemas()
        self._show_run_summary = True
        self._clear_export_status()
        self._refresh_excel_workbook_items()
        self._refresh_global_review_targets()
        self._loaded_excel_mode = ""
        self._content_mode = (
            self._excel_mode_for_item(self._excel_workbook_items[0])
            if self._excel_workbook_items
            else "extracted"
        )
        self._refresh_content_mode_combo()
        self._refresh_family_combo()
        self._apply_content_mode()
        self.show_success_banner(
            self.t(
                "extraction.completed",
                run_id=summary.run_id,
                record_count=summary.record_count,
                family_count=len(summary.family_counts),
            )
        )
        self.start_review_search(reset_page=True, initial_limit=0)

    def _clear_export_status(self) -> None:
        self.export_status_label.clear()
        self.export_status_label.setVisible(False)

    def start_extraction(self) -> None:
        self.log_debug("start_extraction_clicked", "Start extraction clicked")
        self.run_background_task(
            "fill_standardized_templates",
            "busy.extraction_fill.title",
            "busy.extraction_fill.body",
            "common.extraction_failed",
            self._handle_start_extraction_complete,
            payload={"use_ocr": self.workbench.settings.extraction_use_ocr},
        )

    def _handle_start_extraction_complete(self, result: object) -> None:
        if isinstance(result, dict):
            self.workbench.apply_run_summary_payload(result)
        self.workbench.reload_schemas()
        self._refresh_excel_workbook_items()
        self._refresh_global_review_targets()
        self._loaded_excel_mode = ""
        self._content_mode = (
            self._excel_mode_for_item(self._excel_workbook_items[0])
            if self._excel_workbook_items
            else "extracted"
        )
        self._refresh_content_mode_combo()
        self._refresh_family_combo()
        self._apply_content_mode()
        self._clear_export_status()
        record_count = result.get("record_count", 0) if isinstance(result, dict) else 0
        self.show_success_banner(self._start_extraction_summary_text(record_count))
        self.start_review_search(reset_page=True, initial_limit=0)
        self.refresh()

    def _start_extraction_summary_text(self, count: int) -> str:
        if self.language == "de":
            return f"Extraktion abgeschlossen. {count} Datensätze extrahiert und standardisierte Vorlagen befüllt."
        if self.language == "zh":
            return f"抽取完成，已抽取 {count} 条记录并填写标准化模板。"
        return f"Extraction complete. {count} records extracted and standardized templates filled."

    def save_results(self) -> None:
        self.log_debug("save_results_clicked", "Save extraction results clicked")
        self.run_background_task(
            "save_extraction_results",
            "busy.save_results.title",
            "busy.save_results.body",
            "common.export_failed",
            self._handle_save_results_complete,
        )

    def _handle_save_results_complete(self, result: object) -> None:
        count = len(result) if isinstance(result, dict) else 0
        self._clear_export_status()
        self.show_success_banner(self._save_results_summary_text(count))

    def _save_results_summary_text(self, count: int) -> str:
        if self.language == "de":
            return f"{count} Ergebnisdateien nach Kategorie gespeichert."
        if self.language == "zh":
            return f"已按种类保存 {count} 个结果文件。"
        return f"Saved {count} result files by category."

    def _handle_family_changed(self, *_args) -> None:
        family_value = self.family_combo.currentData()
        self.log_debug("review_family_changed", f"Review family changed to {family_value or '__all__'}")
        self.start_review_search(reset_page=True)

    def apply_keyword_filter(self) -> None:
        self.log_debug("review_search", f"Search requested: {self.search_edit.text().strip()}")
        self.start_review_search(reset_page=True)

    def clear_keyword_filter(self) -> None:
        self.log_debug("review_search_clear", "Cleared review search")
        self.start_review_search(reset_page=True)

    def open_component_review(self, payload: dict[str, object]) -> None:
        self._pending_review_jump = payload
        self.family_combo.blockSignals(True)
        try:
            self.family_combo.setCurrentIndex(0)
        finally:
            self.family_combo.blockSignals(False)
        self.search_edit.setText(str(payload.get("keyword", "")).strip())
        self.start_review_search(reset_page=True, initial_limit=0)

    def start_review_search(self, reset_page: bool = False, initial_limit: int | None = None) -> None:
        if self._review_thread is not None and self._review_thread.isRunning():
            return
        self._review_generation += 1
        self._pending_page_offsets.clear()
        family_filter, keyword = self._current_review_filters()
        self.review_model.set_filters(family_filter, keyword)
        self._update_page_info()
        self.log_debug(
            "review_query_started",
            f"Loading review rows for family={family_filter or 'all'} keyword={keyword or '<empty>'}",
            details={"family": family_filter, "keyword": keyword, "reset_page": reset_page},
        )
        self._launch_review_request(
            mode="search",
            offset=0,
            reset_page=reset_page,
            generation=self._review_generation,
            limit=self.review_model.page_size if initial_limit is None else initial_limit,
        )

    def _queue_review_page_request(self, offset: int) -> None:
        if offset in self._pending_page_offsets:
            return
        if self._review_thread is not None and self._review_thread.isRunning():
            self._pending_page_offsets.append(offset)
            return
        self._launch_review_request(
            mode="page",
            offset=offset,
            reset_page=False,
            generation=self._review_generation,
            limit=self.review_model.page_size,
        )

    def _current_review_filters(self) -> tuple[str | None, str]:
        family_value = self.family_combo.currentData()
        family_filter = None if family_value in {None, "__all__"} else family_value
        keyword = self.search_edit.text().strip()
        return family_filter, keyword

    def _refresh_family_combo(self) -> None:
        previous_value = self.family_combo.currentData()
        self.family_combo.blockSignals(True)
        try:
            self.family_combo.clear()
            self.family_combo.addItem(self.t("common.all"), userData="__all__")
            for selection, _label in self.workbench.review_selection_items():
                self.family_combo.addItem(self._review_selection_label(selection), userData=selection)
            selected_index = 0
            if previous_value:
                for row in range(self.family_combo.count()):
                    if self.family_combo.itemData(row) == previous_value:
                        selected_index = row
                        break
            self.family_combo.setCurrentIndex(selected_index)
        finally:
            self.family_combo.blockSignals(False)

    def _refresh_status_label(self) -> None:
        self.status_label.clear()
        self.status_label.setVisible(False)

    def _stop_button_text(self) -> str:
        if self.language == "de":
            return "Abbrechen"
        if self.language == "zh":
            return "停止"
        return "Stop"

    def _scan_abort_requested_text(self) -> str:
        if self.language == "de":
            return "Scan wird abgebrochen..."
        if self.language == "zh":
            return "正在停止扫描..."
        return "Stopping scan..."

    def _schema_abort_requested_text(self) -> str:
        if self.language == "de":
            return "Template-Erzeugung wird abgebrochen..."
        if self.language == "zh":
            return "正在停止模板生成..."
        return "Stopping schema generation..."

    def _extraction_abort_requested_text(self) -> str:
        if self.language == "de":
            return "Extraktion wird abgebrochen..."
        if self.language == "zh":
            return "正在停止抽取..."
        return "Stopping extraction..."

    def _generic_abort_requested_text(self) -> str:
        if self.language == "de":
            return "Vorgang wird abgebrochen..."
        if self.language == "zh":
            return "正在停止当前操作..."
        return "Stopping task..."

    def _current_task_abort_requested_text(self) -> str:
        task_name = self._task_worker.task_name if self._task_worker is not None else ""
        if task_name == "scan":
            return self._scan_abort_requested_text()
        if task_name == "generate_schemas":
            return self._schema_abort_requested_text()
        if task_name == "run_extraction":
            return self._extraction_abort_requested_text()
        if task_name == "fill_standardized_templates":
            return self._extraction_abort_requested_text()
        if task_name == "save_extraction_results":
            return self._generic_abort_requested_text()
        return self._generic_abort_requested_text()

    def _apply_button_widths(self) -> None:
        for button in (
            self.scan_button,
            self.start_extraction_button,
            self.save_results_button,
            self.stop_button,
            self.aas_button,
            self.ontology_button,
        ):
            target_width = max(button.sizeHint().width(), 88)
            button.setMinimumWidth(target_width)
            button.setMaximumWidth(16777215)
        self.scan_root_browse_button.setMinimumWidth(max(self.scan_root_browse_button.sizeHint().width(), 88))
        self.export_browse_button.setMinimumWidth(max(self.export_browse_button.sizeHint().width(), 88))

    def _review_selection_label(self, selection: str) -> str:
        family, scope_id = self.workbench.parse_family_selection(selection)
        if family is None:
            return self.t("common.all")
        source_root = self.workbench.family_source_root(family)
        family_label = translate_family(self.language, family)
        if scope_id:
            return f"{source_root} / {self.workbench.bundle_name_for_scope(scope_id)} / {family_label}"
        return f"{source_root} / {family_label}"

    def _launch_review_request(
        self,
        *,
        mode: str,
        offset: int,
        reset_page: bool,
        generation: int,
        limit: int,
    ) -> None:
        family_filter, keyword = self._current_review_filters()
        self._review_request_mode = mode
        self._review_request_offset = offset
        self._review_request_reset_page = reset_page
        self._active_review_generation = generation
        self._set_search_running(True)
        request_started_at = time.perf_counter()

        self._review_thread = QThread(self)
        self._review_worker = TaskWorker(
            str(self.workbench.workspace_root),
            "load_review_record_page",
            {
                "family": family_filter,
                "keyword": keyword,
                "offset": offset,
                "limit": limit,
            },
        )
        self._review_worker.moveToThread(self._review_thread)
        success_result: object | None = None
        error_message: str | None = None

        def handle_progress(value: int, _message: str) -> None:
            self.search_progress_bar.setValue(max(0, min(100, int(value))))
            self.log_debug(
                "review_query_progress",
                f"Review request {mode} [{value}%]",
                details={"mode": mode, "offset": offset, "progress": int(value)},
            )

        def handle_success(result: object) -> None:
            nonlocal success_result
            success_result = result

        def handle_error(message: str) -> None:
            nonlocal error_message
            error_message = message
            duration_ms = round((time.perf_counter() - request_started_at) * 1000, 1)
            self.log_debug(
                "review_query_failed",
                f"Review request failed after {duration_ms} ms: {message}",
                level="ERROR",
                details={"mode": mode, "offset": offset, "duration_ms": duration_ms},
            )

        def cleanup() -> None:
            thread = self._review_thread
            if thread is not None:
                thread.quit()
                thread.wait(2000)
            self._review_thread = None
            self._review_worker = None
            self._set_search_running(False)
            if error_message:
                self.show_error("common.extraction_failed", error_message)
                return
            if isinstance(success_result, dict) and self._active_review_generation == self._review_generation:
                duration_ms = round((time.perf_counter() - request_started_at) * 1000, 1)
                self.log_debug(
                    "review_query_succeeded",
                    f"Review request {mode} completed in {duration_ms} ms",
                    details={"mode": mode, "offset": offset, "duration_ms": duration_ms},
                )
                if self._review_request_mode == "search":
                    self.review_model.apply_review_payload(success_result)
                else:
                    self.review_model.apply_page_payload(success_result)
                self._update_page_info()
                if self._pending_review_jump and self._review_request_mode == "search":
                    self._apply_pending_review_jump(success_result)
                elif self._review_request_reset_page:
                    self.review_table.scrollToTop()
            if self._pending_page_offsets and self._active_review_generation == self._review_generation:
                next_offset = self._pending_page_offsets.pop(0)
                self._launch_review_request(
                    mode="page",
                    offset=next_offset,
                    reset_page=False,
                    generation=self._review_generation,
                    limit=self.review_model.page_size,
                )

        self._review_thread.started.connect(self._review_worker.run)
        self._review_worker.progress_changed.connect(handle_progress)
        self._review_worker.succeeded.connect(handle_success)
        self._review_worker.failed.connect(handle_error)
        self._review_worker.finished.connect(cleanup)
        self._review_worker.finished.connect(self._review_worker.deleteLater)
        self._review_thread.finished.connect(self._review_thread.deleteLater)
        self._review_thread.start()

    def _set_search_running(self, running: bool) -> None:
        self.search_loading_ring.setVisible(running)
        self.search_progress_bar.setVisible(running)
        self.scan_button.setDisabled(running)
        self.start_extraction_button.setDisabled(running)
        self.save_results_button.setDisabled(running)
        self.aas_button.setDisabled(running)
        self.ontology_button.setDisabled(running)
        self.scan_root_browse_button.setDisabled(running)
        self.export_browse_button.setDisabled(running)
        self.family_combo.setDisabled(running)
        self.search_edit.setDisabled(running)
        self.excel_sheet_combo.setDisabled(running)
        self.excel_table.setDisabled(running)
        self.prev_review_cell_button.setDisabled(running or not self._global_review_targets)
        self.next_review_cell_button.setDisabled(running or not self._global_review_targets)
        if running:
            self.search_progress_bar.setValue(0)
            if self.review_model.total_count <= 0:
                self.page_info_label.setText(self._review_loading_text())
        else:
            self._update_page_info()
            self._refresh_global_review_navigation()

    def _apply_pending_review_jump(self, payload: dict[str, object]) -> None:
        jump = dict(self._pending_review_jump or {})
        self._pending_review_jump = None
        rows_payload = payload.get("rows", [])
        if not isinstance(rows_payload, list) or not rows_payload:
            self.review_model.set_highlighted_record_keys(set())
            return
        records = [ExtractedRecord.model_validate(item) for item in rows_payload]
        matching_record_keys = [str(item) for item in jump.get("matching_record_keys", []) if str(item)]
        preferred_record_key = str(jump.get("preferred_record_key", "") or "")
        preferred_source_root = str(jump.get("preferred_source_root", "") or "")
        preferred_scope_id = str(jump.get("preferred_scope_id", "") or "")
        if matching_record_keys:
            candidates = [
                (index, record)
                for index, record in enumerate(records)
                if record.record_key in matching_record_keys
            ]
        else:
            candidates = list(enumerate(records))
        if not candidates:
            self.review_model.set_highlighted_record_keys(set())
            return
        highlight_keys = {record.record_key for _index, record in candidates}
        preferred_index = candidates[0][0]
        if preferred_record_key:
            for index, record in candidates:
                if record.record_key == preferred_record_key:
                    preferred_index = index
                    break
        else:
            for index, record in candidates:
                if preferred_source_root and record.source_root != preferred_source_root:
                    continue
                if preferred_scope_id and record.scope_id != preferred_scope_id:
                    continue
                preferred_index = index
                break
        self.review_model.set_highlighted_record_keys(highlight_keys)
        self.review_table.scrollTo(
            self.review_model.index(preferred_index, 0),
            QTableView.ScrollHint.PositionAtCenter,
        )
        self._highlight_timer.start(1800)

    def export_all(self) -> None:
        self.log_debug("export_raw_results_clicked", "Export raw extracted results clicked")
        self.run_background_task(
            "export_results",
            "busy.export.title",
            "busy.export.body",
            "common.export_failed",
            self._handle_export_complete,
        )

    def select_export_dir(self) -> None:
        self.log_debug("browse_results_export_dir_clicked", "Browse results export directory clicked")
        chosen = self.choose_directory("dialog.select_export_dir", str(self.workbench.resolve_results_export_dir()))
        if chosen is None:
            return
        self.workbench.update_results_export_dir(chosen)
        self.export_path_edit.setText(str(self.workbench.resolve_results_export_dir()))

    def _handle_export_complete(self, result: object) -> None:
        paths = result if isinstance(result, list) else [result]
        self._clear_export_status()
        self.show_success_banner(self._export_summary_text(len(paths)))

    def export_standardized_uc1_workbooks(self) -> None:
        self.log_debug(
            "export_standardized_excel_clicked",
            "Export transformed standardized Excel sheets clicked",
        )
        self.run_background_task(
            "export_use_case_1_standardized_workbooks",
            "literal:Standardized Excel Export",
            "literal:Building the transformed standardized Excel sheets.",
            "common.export_failed",
            self._handle_standardized_uc1_export_complete,
        )

    def _handle_standardized_uc1_export_complete(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        self._last_standardized_workbook = str(result.get("aggregated_workbook_path", "") or "")
        self._clear_export_status()
        self.show_success_banner(self._standardized_uc1_summary_text(result))

    def generate_uc1_aas_models(self) -> None:
        self.log_debug("generate_aas_models_clicked", "Generate AAS models clicked")
        self.run_background_task(
            "generate_use_case_1_aas_models",
            "literal:AAS Model Export",
            "literal:Generating the AAS models from the transformed sources.",
            "common.export_failed",
            self._handle_uc1_aas_complete,
        )

    def _handle_uc1_aas_complete(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        self._last_aas_bundle = {key: value for key, value in result.items() if isinstance(value, list)}
        self._clear_export_status()
        self.show_success_banner(self._uc1_aas_summary_text(self._last_aas_bundle))

    def export_uc1_source_ontologies(self) -> None:
        self.log_debug("export_owl_clicked", "Export OWL files clicked")
        self.run_background_task(
            "export_use_case_1_source_ontologies",
            "literal:OWL Export",
            "literal:Generating the Protégé-readable OWL files.",
            "common.export_failed",
            self._handle_uc1_source_ontology_complete,
        )

    def _handle_uc1_source_ontology_complete(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        aas_bundle = result.get("aas_paths_by_source", {})
        if isinstance(aas_bundle, dict):
            self._last_aas_bundle = {key: value for key, value in aas_bundle.items() if isinstance(value, list)}
        ontology_paths = result.get("ontology_paths", {})
        if isinstance(ontology_paths, dict):
            self._last_ontology_path = next((str(path) for path in ontology_paths.values() if str(path)), "")
        self._clear_export_status()
        self.show_success_banner(self._uc1_ontology_summary_text(result))

    def _export_summary_text(self, count: int) -> str:
        if self.language == "de":
            return f"{count} Roh-Extraktionsergebnisse exportiert."
        if self.language == "zh":
            return f"已导出 {count} 个原始抽取结果。"
        return f"Exported {count} raw extracted result files."

    def _scan_summary_text(self, count: int) -> str:
        if self.language == "de":
            return f"Scan abgeschlossen. {count} Dateien sind jetzt im Projekt inventarisiert."
        if self.language == "zh":
            return f"扫描完成，当前项目中已记录 {count} 个文件。"
        return f"Scan complete. {count} files are now indexed in the project."

    def _schema_summary_text(self) -> str:
        if self.language == "de":
            return "Schemas wurden aktualisiert und stehen in Inconsistence Extract bereit."
        if self.language == "zh":
            return "模板已更新，可继续运行 Inconsistence Extract。"
        return "Schemas are updated and ready for Inconsistence Extract."

    def _standardized_uc1_button_text(self) -> str:
        if self.language == "de":
            return "Standardisierte Excel-Sheets exportieren"
        if self.language == "zh":
            return "导出转换后的 Standardized Excel Sheet"
        return "Export Standardized Excel Sheet"

    def _aas_button_text(self) -> str:
        if self.language == "de":
            return "AAS-Modell erzeugen"
        if self.language == "zh":
            return "生成 AAS Model"
        return "Generate AAS Model"

    def _ontology_button_text(self) -> str:
        if self.language == "de":
            return "OWL exportieren"
        if self.language == "zh":
            return "导出 OWL"
        return "Export OWL"

    def _standardized_uc1_summary_text(self, result: dict[str, object]) -> str:
        workbook_count = len(
            {
                key: str(value)
                for key, value in result.items()
                if key in {"instrument_list", "wiring", "datasheet", "piping"} and str(value)
            }
        )
        if self.language == "de":
            return f"{workbook_count} transformierte Standardized Excel Sheets exportiert."
        if self.language == "zh":
            return f"已导出 {workbook_count} 份转换后的 Standardized Excel Sheet。"
        return f"Exported {workbook_count} transformed standardized Excel sheets."

    def _uc1_aas_summary_text(self, aas_bundle: dict[str, list[str]]) -> str:
        _KNOWN_KEYS = {"pid", "instrument_list", "wiring", "datasheet", "piping", "stromlaufplan"}
        aas_count = sum(len(aas_bundle.get(k, [])) for k in _KNOWN_KEYS)
        source_count = sum(1 for k in _KNOWN_KEYS if aas_bundle.get(k))
        if self.language == "de":
            return f"{aas_count} AAS-Modelle aus {source_count} Quellketten erzeugt."
        if self.language == "zh":
            return f"已生成 {aas_count} 个 AAS Model，覆盖 {source_count} 条源转换链。"
        return f"Generated {aas_count} AAS models across {source_count} source pipelines."

    def _uc1_ontology_summary_text(self, result: dict[str, object]) -> str:
        ontology_paths = result.get("ontology_paths", {})
        if not isinstance(ontology_paths, dict):
            ontology_paths = {}
        ontology_count = sum(1 for path in ontology_paths.values() if str(path))
        if self.language == "de":
            return f"{ontology_count} OWL-Dateien fuer Protege exportiert."
        if self.language == "zh":
            return f"已导出 {ontology_count} 个 Protege 可读取的 OWL 文件。"
        return f"Exported {ontology_count} Protege-readable OWL files."

    def _content_mode_label_text(self) -> str:
        if self.language == "de":
            return "Ansicht"
        if self.language == "zh":
            return "视图"
        return "View"

    def _documents_mode_text(self) -> str:
        if self.language == "de":
            return "Scan-Ergebnisse"
        if self.language == "zh":
            return "扫描结果"
        return "Scanned Files"

    def _schemas_mode_text(self) -> str:
        if self.language == "de":
            return "Schema-Ansicht"
        if self.language == "zh":
            return "模板信息"
        return "Schema Fields"

    def _extracted_mode_text(self) -> str:
        if self.language == "de":
            return "Extraktionsdaten"
        if self.language == "zh":
            return "抽取信息"
        return "Extracted Data"

    def _excel_sheet_label_text(self) -> str:
        if self.language == "de":
            return "Sheet"
        if self.language == "zh":
            return "工作表"
        return "Sheet"

    def _excel_missing_text(self) -> str:
        if self.language == "de":
            return "Die vorbereitete Excel-Arbeitsmappe konnte nicht geladen werden."
        if self.language == "zh":
            return "无法加载准备导出的 Excel。"
        return "The prepared Excel workbook could not be loaded."

    def _previous_review_cell_text(self) -> str:
        if self.language == "de":
            return "Vorherige Review-Zelle"
        if self.language == "zh":
            return "上一个需要 review 的单元格"
        return "Previous review cell"

    def _next_review_cell_text(self) -> str:
        if self.language == "de":
            return "Nächste Review-Zelle"
        if self.language == "zh":
            return "下一个需要 review 的单元格"
        return "Next review cell"

    def _update_page_info(self) -> None:
        total = self.review_model.total_count
        if total <= 0:
            self.page_info_label.setText(self._review_empty_text())
            return
        self.page_info_label.setText(self._review_summary_text(total, self.review_model.loaded_count()))

    def _review_empty_text(self) -> str:
        if self.language == "de":
            return "Keine Prüfergebnisse vorhanden."
        if self.language == "zh":
            return "当前没有可显示的复核结果。"
        return "No review rows available."

    def _review_loading_text(self) -> str:
        if self.language == "de":
            return "Prüfergebnisse werden geladen..."
        if self.language == "zh":
            return "正在加载抽取结果..."
        return "Loading extracted rows..."

    def _review_summary_text(self, total: int, loaded: int) -> str:
        keyword = self.search_edit.text().strip()
        fully_loaded = loaded >= total
        if self.language == "de":
            if not fully_loaded:
                if keyword:
                    return f"{loaded}/{total} Treffer für „{keyword}“ geladen. Weitere Zeilen werden nachgeladen."
                return f"{loaded}/{total} Prüfzeilen geladen. Weitere Zeilen werden nachgeladen."
            if keyword:
                return f"{total} Treffer für „{keyword}“ wurden vollständig geladen."
            return f"Alle {total} Prüfzeilen wurden vollständig geladen."
        if self.language == "zh":
            if not fully_loaded:
                if keyword:
                    return f"关键词“{keyword}”共找到 {total} 条结果，当前已加载 {loaded} 条，剩余结果会继续加载。"
                return f"正在显示 {total} 条复核结果中的 {loaded} 条，其余结果会继续加载。"
            if keyword:
                return f"关键词“{keyword}”共找到 {total} 条结果，已全部加载完成。"
            return f"正在显示全部 {total} 条复核结果，已全部加载完成。"
        if not fully_loaded:
            if keyword:
                return f"Loaded {loaded}/{total} matches for \"{keyword}\". More rows are still loading."
            return f"Loaded {loaded}/{total} review rows. More rows are still loading."
        if keyword:
            return f"{total} matches for \"{keyword}\" loaded completely."
        return f"Showing all {total} review rows. Everything is loaded."

    def _review_search_placeholder(self) -> str:
        if self.language == "de":
            return "Stichwort in Prüfdaten suchen"
        if self.language == "zh":
            return "在复核结果中搜索关键词"
        return "Search review rows"

    def _open_document_action_text(self) -> str:
        if self.language == "de":
            return "Dieses Dokument öffnen"
        if self.language == "zh":
            return "打开这个文档"
        return "Open this document"

    def _view_value_source_action_text(self) -> str:
        return self.t("review.action.view_source_position")

    def _value_preview_context(self, index: QModelIndex) -> dict[str, object] | None:
        if not index.isValid() or index.column() < 3:
            return None
        context = self.review_model.value_context_at(index.row(), index.column())
        if context is None:
            return None
        record = context["record"]
        result = context["result"]
        evidence_refs = list(result.evidence_refs)
        if not result.value or (not evidence_refs and not record.source_path):
            return None
        if self.review_model._result_review_confidence(result) <= 0.0:
            return None
        return context

    def _review_context_spec(self, index: QModelIndex) -> list[dict[str, object]]:
        if not index.isValid():
            return []
        spec: list[dict[str, object]] = []
        if index.column() == 2:
            source_path = self.review_model.source_path_at(index.row())
            if source_path:
                spec.append(
                    {
                        "kind": "open_document",
                        "label": self._open_document_action_text(),
                        "enabled": True,
                        "source_path": source_path,
                    }
                )
        elif index.column() >= 3:
            context = self.review_model.value_context_at(index.row(), index.column())
            if context is None:
                return []
            preview_context = self._value_preview_context(index)
            if preview_context is not None:
                spec.append(
                    {
                        "kind": "view_source_position",
                        "label": self._view_value_source_action_text(),
                        "enabled": True,
                        "context": preview_context,
                    }
                )
            current_feedback = str(getattr(context.get("result"), "review_feedback_status", "") or "").strip()
            for feedback_status in ("confirmed", "rejected", "corrected"):
                spec.append(
                    {
                        "kind": "mark_feedback",
                        "label": self._review_feedback_action_text(feedback_status),
                        "enabled": current_feedback != feedback_status,
                        "context": context,
                        "feedback_status": feedback_status,
                        "row": index.row(),
                        "column": index.column(),
                    }
                )
        return spec

    def _show_review_context_menu(self, position) -> None:
        index = self.review_table.indexAt(position)
        spec = self._review_context_spec(index)
        if not spec:
            return
        menu = QMenu(self.review_table)
        actions: dict[object, dict[str, object]] = {}
        for item in spec:
            action = menu.addAction(str(item.get("label", "")))
            action.setEnabled(bool(item.get("enabled", False)))
            actions[action] = item
        chosen = menu.exec(self.review_table.viewport().mapToGlobal(position))
        selected = actions.get(chosen)
        if not selected or not selected.get("enabled", False):
            return
        if selected.get("kind") == "open_document":
            self._open_review_document(str(selected.get("source_path", "")))
            return
        if selected.get("kind") == "view_source_position":
            context = selected.get("context")
            if isinstance(context, dict):
                self._open_value_source_preview(context)
            return
        if selected.get("kind") == "mark_feedback":
            context = selected.get("context")
            if isinstance(context, dict):
                self._save_review_feedback(
                    context,
                    str(selected.get("feedback_status", "")),
                    int(selected.get("row", -1)),
                    int(selected.get("column", -1)),
                )
            return

    def _handle_review_table_double_clicked(self, index: QModelIndex) -> None:
        context = self._value_preview_context(index)
        if context is None:
            return
        self._open_value_source_preview(context)

    def _handle_excel_table_double_clicked(self, index: QModelIndex) -> None:
        context = self.excel_model.value_context_at(index.row(), index.column())
        if context is None:
            return
        provenance = context.get("provenance")
        if not isinstance(provenance, ExcelCellProvenance):
            return
        if not provenance.value or (not provenance.evidence_refs and not provenance.source_path):
            return
        if self.excel_model.provenance_review_confidence(provenance) <= 0.0:
            return
        self._open_excel_value_source_preview(provenance)

    def _open_review_document(self, source_path: str) -> None:
        candidate = self.workbench.resolve_source_path(source_path)
        if candidate is None:
            self.show_error("common.extraction_failed", f"File not found: {source_path}")
            return
        self.log_debug("open_document", f"Opened source document {candidate}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate)))

    def _open_value_source_preview(self, context: dict[str, object]) -> None:
        record = context.get("record")
        result = context.get("result")
        if record is None or result is None:
            return
        if self._source_preview_dialog is None:
            self._source_preview_dialog = ValueSourcePreviewDialog(
                str(self.workbench.workspace_root),
                lambda: self.language,
                self,
            )
        self.log_debug(
            "open_source_preview",
            f"Opened source preview for {context.get('field_name', '')}",
            details={"source_path": str(record.source_path), "field_name": str(context.get('field_name', ''))},
        )
        self._source_preview_dialog.open_preview(
            source_path=str(record.source_path),
            record_display_name=str(record.display_name),
            field_name=str(context.get("field_name", "")),
            target_value=str(result.value),
            evidences=[evidence.model_dump(mode="json") for evidence in result.evidence_refs],
            initial_index=0,
        )

    def _open_excel_value_source_preview(self, provenance: ExcelCellProvenance) -> None:
        if self._source_preview_dialog is None:
            self._source_preview_dialog = ValueSourcePreviewDialog(
                str(self.workbench.workspace_root),
                lambda: self.language,
                self,
            )
        self.log_debug(
            "open_excel_source_preview",
            f"Opened Excel source preview for {provenance.workbook_name}/{provenance.sheet_name}!{provenance.coord}",
            details={
                "source_path": provenance.source_path,
                "field_name": provenance.field_name,
                "workbook_name": provenance.workbook_name,
                "sheet_name": provenance.sheet_name,
                "coord": provenance.coord,
            },
        )
        self._source_preview_dialog.open_preview(
            source_path=provenance.source_path,
            record_display_name=provenance.record_display_name,
            field_name=provenance.field_name,
            target_value=provenance.value,
            evidences=[evidence.model_dump(mode="json") for evidence in provenance.evidence_refs],
            initial_index=0,
        )

    def _review_feedback_action_text(self, feedback_status: str) -> str:
        if self.language == "de":
            return {
                "confirmed": "Als bestätigt markieren",
                "rejected": "Als abgelehnt markieren",
                "corrected": "Als korrigiert markieren",
            }.get(feedback_status, feedback_status)
        if self.language == "zh":
            return {
                "confirmed": "标记为已确认",
                "rejected": "标记为已否决",
                "corrected": "标记为已修正",
            }.get(feedback_status, feedback_status)
        return {
            "confirmed": "Mark as confirmed",
            "rejected": "Mark as rejected",
            "corrected": "Mark as corrected",
        }.get(feedback_status, feedback_status)

    def _review_feedback_saved_text(self, feedback_status: str, field_name: str) -> str:
        if self.language == "de":
            return f"Review-Feedback für {field_name} gespeichert: {feedback_status}."
        if self.language == "zh":
            return f"字段 {field_name} 的复核反馈已保存为 {feedback_status}。"
        return f"Saved review feedback for {field_name}: {feedback_status}."

    def _save_review_feedback(
        self,
        context: dict[str, object],
        feedback_status: str,
        row_index: int,
        column_index: int,
    ) -> None:
        record = context.get("record")
        result = context.get("result")
        field_name = str(context.get("field_name", "") or "")
        if record is None or result is None or not field_name:
            return
        if not self.workbench.save_review_feedback(str(record.record_key), field_name, feedback_status):
            self.show_error("common.extraction_failed", f"Unable to save review feedback for {field_name}.")
            return
        result.review_feedback_status = feedback_status
        if row_index >= 0 and column_index >= 0:
            model_index = self.review_model.index(row_index, column_index)
            self.review_model.dataChanged.emit(
                model_index,
                model_index,
                [Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.DisplayRole],
            )
        self.show_success_banner(self._review_feedback_saved_text(feedback_status, field_name))

    def set_task_running(self, running: bool) -> None:
        self.scan_button.setDisabled(running)
        self.start_extraction_button.setDisabled(running)
        self.save_results_button.setDisabled(running)
        self.stop_button.setDisabled(not running)
        self.aas_button.setDisabled(running)
        self.ontology_button.setDisabled(running)
        self.scan_root_browse_button.setDisabled(running)
        self.export_browse_button.setDisabled(running)
        self.family_combo.setDisabled(running)
        self.search_edit.setDisabled(running)
        self.excel_sheet_combo.setDisabled(running)
        self.excel_table.setDisabled(running)
        self.prev_review_cell_button.setDisabled(running or not self._global_review_targets)
        self.next_review_cell_button.setDisabled(running or not self._global_review_targets)


class ExportsPage(BasePage):
    def __init__(self, workbench: Workbench, refresh_all: Callable[[], None]) -> None:
        super().__init__(
            workbench,
            refresh_all,
            "page.exports.title",
            "page.exports.subtitle",
        )
        self.path_label = BodyLabel("")
        self.path_edit = QLineEdit(self)
        self.path_edit.setReadOnly(True)
        self.browse_button = PrimaryPushButton("")
        self.browse_button.clicked.connect(self.select_export_dir)
        path_row = QWidget(self)
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(12)
        path_layout.addWidget(self.path_label)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(self.browse_button)
        self.root_layout.addWidget(path_row)
        self.export_button = PrimaryPushButton("")
        self.export_button.clicked.connect(self.export_all)
        self.root_layout.addWidget(self.export_button)

        self.export_box = QTextEdit(self)
        self.export_box.setReadOnly(True)
        self.root_layout.addWidget(self.export_box, 1)
        self.apply_language()

    def apply_language(self) -> None:
        super().apply_language()
        self.path_label.setText(self.t("common.export_dir"))
        self.browse_button.setText(self.t("common.browse"))
        self.export_button.setText(self.t("common.export_all"))
        if not self.export_box.toPlainText():
            self.export_box.setPlaceholderText(self.t("exports.placeholder"))

    def refresh(self, *_args) -> None:
        self.path_edit.setText(self.workbench.display_export_dir())

    def export_all(self) -> None:
        self.log_debug("export_all_clicked", "Export all clicked")
        self.run_background_task(
            "export_all",
            "busy.export.title",
            "busy.export.body",
            "common.export_failed",
            self._handle_export_complete,
        )

    def select_export_dir(self) -> None:
        self.log_debug("browse_export_dir_clicked", "Browse export directory clicked")
        chosen = self.choose_directory("dialog.select_export_dir", str(self.workbench.resolve_export_dir()))
        if chosen is None:
            return
        self.workbench.update_export_dir(chosen)
        self.path_edit.setText(self.workbench.display_export_dir())

    def _handle_export_complete(self, result: object) -> None:
        paths = result
        self.export_box.setPlainText("\n".join(str(path) for path in paths))

    def set_task_running(self, running: bool) -> None:
        self.export_button.setDisabled(running)
        self.browse_button.setDisabled(running)


class LogTableModel(QAbstractTableModel):
    def __init__(self, page: "LogPage") -> None:
        super().__init__(page)
        self._page = page
        self._source_indices: list[int] = []
        self._formatted_cache: dict[int, str] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._source_indices)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or index.column() != 0:
            return None
        row = index.row()
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            cached = self._formatted_cache.get(row)
            if cached is not None:
                return cached
            entry = self._page.entry_for_row(row)
            if entry is not None:
                formatted = self._page.format_entry(entry)
                self._formatted_cache[row] = formatted
                return formatted
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if (
            orientation == Qt.Orientation.Horizontal
            and section == 0
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return "Log"
        return None

    def replace_rows(self, source_indices: list[int]) -> None:
        self.beginResetModel()
        # Filter out indices whose entries cannot be loaded (corrupted / blank
        # lines in the persisted log).  Without this, those rows appear as
        # empty lines in the QTableView.
        valid_indices = []
        valid_cache = {}
        for src_idx in source_indices:
            entry = self._page.workbench.debug_log_entry_at(src_idx)
            if entry is not None:
                valid_indices.append(src_idx)
                valid_cache[len(valid_indices) - 1] = self._page.format_entry(entry)
        self._source_indices = valid_indices
        self._formatted_cache = valid_cache
        self.endResetModel()

    def clear_rows(self) -> None:
        self.replace_rows([])

    def append_source_index(self, source_index: int) -> None:
        # Verify the entry exists before adding a row — avoids blank
        # lines when the persisted log index points to a corrupted/empty entry.
        entry = self._page.entry_for_row(len(self._source_indices))
        if entry is None:
            # Re-fetch: entry_for_row uses source_indices which may not
            # have this index yet.  Check directly.
            entry = self._page.workbench.debug_log_entry_at(source_index)
        if entry is None:
            return  # skip blank entries
        insert_at = len(self._source_indices)
        self.beginInsertRows(QModelIndex(), insert_at, insert_at)
        self._source_indices.append(source_index)
        self._formatted_cache[insert_at] = self._page.format_entry(entry)
        self.endInsertRows()

    def source_index_for_row(self, row: int) -> int | None:
        if row < 0 or row >= len(self._source_indices):
            return None
        return self._source_indices[row]


class LogPage(BasePage):
    def __init__(self, workbench: Workbench, refresh_all: Callable[[], None]) -> None:
        super().__init__(
            workbench,
            refresh_all,
            "page.log.title",
            "page.log.subtitle",
        )
        toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(12)
        self.filter_label = BodyLabel("")
        self.filter_combo = ComboBox(self)
        self.clear_button = PrimaryPushButton("")
        self.copy_button = PrimaryPushButton("")
        self.status_label = BodyLabel("")
        self.status_label.setWordWrap(True)
        self.filter_combo.currentIndexChanged.connect(self._handle_filter_changed)
        self.clear_button.clicked.connect(self.clear_log)
        self.copy_button.clicked.connect(self.copy_log)
        toolbar_layout.addWidget(self.filter_label)
        toolbar_layout.addWidget(self.filter_combo)
        toolbar_layout.addWidget(self.clear_button)
        toolbar_layout.addWidget(self.copy_button)
        toolbar_layout.addWidget(self.status_label, 1)
        self.root_layout.addWidget(toolbar)

        self.log_model = LogTableModel(self)
        self.log_view = QTableView(self)
        self.log_view.setModel(self.log_model)
        self.log_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.log_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.log_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.log_view.setWordWrap(False)
        self.log_view.setShowGrid(False)
        self.log_view.setAlternatingRowColors(False)
        self.log_view.setCornerButtonEnabled(False)
        self.log_view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.log_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.log_view.verticalHeader().setVisible(False)
        self.log_view.verticalHeader().setDefaultSectionSize(24)
        self.log_view.horizontalHeader().setStretchLastSection(False)
        self.log_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.log_view.setColumnWidth(0, 2200)
        self.root_layout.addWidget(self.log_view, 1)

        self.workbench.debug_log.entry_added.connect(self._handle_log_entry)
        self.workbench.debug_log.cleared.connect(self._handle_log_cleared)
        self._log_dirty = False
        self._active_task_name = ""
        self.apply_language()
        self.refresh()

    def apply_language(self) -> None:
        super().apply_language()
        self.filter_label.setText(self.t("log.filter.label"))
        self._populate_filter_combo()
        self.clear_button.setText(self.t("common.clear_log"))
        self.copy_button.setText(self.t("common.copy_log"))
        self._update_status()

    MAX_DISPLAY_ENTRIES = 5000

    def refresh(self, *_args) -> None:
        filter_value = self._current_filter()
        self._active_task_name = self._resolve_current_task_name() if filter_value == "current_task" else ""
        filtered_rows: list[int] = []
        for source_index, entry in self.workbench.iter_debug_log_recent(limit=self.MAX_DISPLAY_ENTRIES):
            if self._entry_matches_filter(entry, task_name=self._active_task_name):
                filtered_rows.append(source_index)
        self.log_model.replace_rows(filtered_rows)
        self.move_cursor_to_end()
        self._update_status()
        self._log_dirty = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._log_dirty:
            self.refresh()

    def clear_log(self) -> None:
        self.workbench.clear_debug_log()
        self.workbench.log_debug(source="log", action="clear", message="Debug log cleared")

    def copy_log(self) -> None:
        lines = []
        for row in range(self.log_model.rowCount()):
            entry = self.entry_for_row(row)
            if entry is not None:
                lines.append(self.format_entry(entry))
        text = "\n".join(lines)
        QGuiApplication.clipboard().setText(text)
        self.workbench.log_debug(
            source="log",
            action="copy",
            message=f"Copied {len(text.splitlines())} log lines to clipboard",
        )
        self._update_status()

    def _handle_log_entry(self, entry: dict) -> None:
        if not self.isVisible():
            self._log_dirty = True
            return
        filter_value = self._current_filter()
        if filter_value == "current_task":
            next_task_name = str((entry.get("details") or {}).get("task_name", "")).strip()
            if next_task_name and next_task_name != self._active_task_name:
                self._active_task_name = next_task_name
                self.log_model.clear_rows()
                self._update_status()
                return
        should_stick = self._is_scrolled_to_bottom()
        if self._entry_matches_filter(entry, task_name=self._active_task_name):
            self.log_model.append_source_index(self.workbench.debug_log_total_count() - 1)
            if should_stick:
                self.move_cursor_to_end()
        self._update_status()

    def _handle_log_cleared(self) -> None:
        self._active_task_name = ""
        self.log_model.clear_rows()
        self._update_status()

    def entry_for_row(self, row: int) -> dict | None:
        source_index = self.log_model.source_index_for_row(row)
        if source_index is None:
            return None
        return self.workbench.debug_log_entry_at(source_index)

    def format_entry(self, entry: dict) -> str:
        details = entry.get("details") or {}
        detail_text = ""
        if details:
            detail_bits = []
            for key, value in details.items():
                if isinstance(value, (dict, list, tuple)):
                    try:
                        rendered = json.dumps(value, ensure_ascii=False)
                    except TypeError:
                        rendered = str(value)
                else:
                    rendered = str(value)
                detail_bits.append(f"{key}={rendered}")
            detail_text = " | " + ", ".join(detail_bits)
        return (
            f"[{entry.get('timestamp', '')}] "
            f"[{entry.get('level', 'INFO')}] "
            f"[{entry.get('source', '')}/{entry.get('action', '')}] "
            f"{entry.get('message', '')}{detail_text}"
        )

    def _update_status(self) -> None:
        total_count = self.workbench.debug_log_total_count()
        filtered_count = self.log_model.rowCount()
        if self.language == "de":
            self.status_label.setText(f"{filtered_count}/{total_count} Log-Eintraege")
        elif self.language == "zh":
            self.status_label.setText(f"{filtered_count}/{total_count} 条日志记录")
        else:
            self.status_label.setText(f"{filtered_count}/{total_count} log entries")

    def move_cursor_to_end(self) -> None:
        self.log_view.scrollToBottom()
        self.log_view.horizontalScrollBar().setValue(0)

    def _is_scrolled_to_bottom(self) -> bool:
        scrollbar = self.log_view.verticalScrollBar()
        return scrollbar.value() >= max(0, scrollbar.maximum() - 2)

    def _populate_filter_combo(self) -> None:
        current_value = self.filter_combo.currentData() or "all"
        self.filter_combo.blockSignals(True)
        try:
            self.filter_combo.clear()
            self.filter_combo.addItem(self.t("common.all"), userData="all")
            self.filter_combo.addItem(self.t("log.filter.ocr"), userData="ocr")
            self.filter_combo.addItem(self.t("log.filter.rag"), userData="rag")
            self.filter_combo.addItem(self.t("log.filter.llm"), userData="llm")
            self.filter_combo.addItem(self.t("log.filter.embedding"), userData="embedding")
            self.filter_combo.addItem(self.t("log.filter.vlm"), userData="vlm")
            self.filter_combo.addItem(self.t("log.filter.errors"), userData="errors")
            self.filter_combo.addItem(self.t("log.filter.current_task"), userData="current_task")
            for row in range(self.filter_combo.count()):
                if self.filter_combo.itemData(row) == current_value:
                    self.filter_combo.setCurrentIndex(row)
                    break
        finally:
            self.filter_combo.blockSignals(False)

    def _handle_filter_changed(self, *_args) -> None:
        self.workbench.log_debug(
            source="log",
            action="filter",
            message=f"Log filter changed to {self._current_filter()}",
        )
        self.refresh()

    def _current_filter(self) -> str:
        return str(self.filter_combo.currentData() or "all")

    def _entry_matches_filter(self, entry: dict, *, task_name: str = "") -> bool:
        filter_value = self._current_filter()
        if filter_value == "all":
            return True
        if filter_value == "ocr":
            return self._is_ocr_entry(entry)
        if filter_value in {"rag", "llm", "embedding", "vlm"}:
            return str(entry.get("source", "")).strip().lower() == filter_value
        if filter_value == "errors":
            return str(entry.get("level", "INFO")).upper() in {"WARNING", "ERROR"}
        if filter_value == "current_task":
            if not task_name:
                return False
            details = entry.get("details") or {}
            return str(details.get("task_name", "")) == task_name
        return True

    def _is_ocr_entry(self, entry: dict) -> bool:
        text = " ".join(
            [
                str(entry.get("source", "")),
                str(entry.get("action", "")),
                str(entry.get("message", "")),
                str(entry.get("details", {})),
            ]
        ).lower()
        markers = ("ocr", "easyocr", "rapidocr", "paddle", "surya", "apple-vision", "apple vision", "mps")
        return any(marker in text for marker in markers)

    def _resolve_current_task_name(self) -> str:
        for entry in reversed(self.workbench.debug_log_entries()):
            details = entry.get("details") or {}
            task_name = str(details.get("task_name", "")).strip()
            if task_name:
                return task_name
        candidates: list[dict[str, object]] = []
        for _, entry in self.workbench.iter_debug_log_recent(limit=2000):
            candidates.append(entry)
        for entry in reversed(candidates):
            details = entry.get("details") or {}
            task_name = str(details.get("task_name", "")).strip()
            if task_name:
                return task_name
        return ""


class ModelSettingsPage(BasePage):
    _model_catalog_fetched = pyqtSignal(object, object, bool)

    def __init__(self, workbench: Workbench, refresh_all: Callable[[], None]) -> None:
        super().__init__(
            workbench,
            refresh_all,
            "page.settings.title",
            "page.settings.subtitle",
        )
        self.root_layout.removeWidget(self.title_label)
        self.root_layout.removeWidget(self.subtitle_label)
        self.root_layout.removeWidget(self.progress_card)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_content = QWidget(self.scroll_area)
        self.scroll_content_layout = QVBoxLayout(self.scroll_content)
        self.scroll_content_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_content_layout.setSpacing(16)
        self.scroll_area.setWidget(self.scroll_content)
        self.scroll_content_layout.addWidget(self.title_label)
        self.scroll_content_layout.addWidget(self.subtitle_label)
        self.scroll_content_layout.addWidget(self.progress_card)

        self.form_layout = QFormLayout()
        self.form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.language_label = QLabel("")
        self.language_combo = ComboBox(self)

        self.enable_llm = QCheckBox("")
        self._llm_form_rows: list[tuple[QWidget, QWidget]] = []
        self.base_url_label = QLabel("")
        self.base_url_edit = QLineEdit(self.workbench.settings.llm.base_url)
        self.base_url_edit.setMinimumWidth(420)
        self.base_url_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.chat_model_label = QLabel("")
        self.chat_model_edit = ComboBox(self)
        self.embedding_model_label = QLabel("")
        self.embedding_model_edit = ComboBox(self)
        self.api_key_label = QLabel("")
        self.api_key_edit = QLineEdit(self.workbench.settings.llm.api_key or "")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Use IEVPI_LLM_API_KEY environment variable")
        self.api_key_row = QWidget(self)
        self.api_key_row_layout = QHBoxLayout(self.api_key_row)
        self.api_key_row_layout.setContentsMargins(0, 0, 0, 0)
        self.api_key_row_layout.setSpacing(10)
        self.timeout_label = QLabel("")
        self.timeout_edit = QLineEdit(str(self.workbench.settings.llm.timeout))
        self.retry_label = QLabel("")
        self.retry_edit = QLineEdit(str(self.workbench.settings.llm.max_retries))
        self.retrieval_top_k_label = QLabel("")
        self.retrieval_top_k_edit = QLineEdit(str(self.workbench.settings.retrieval_top_k))
        self.aio_ml_evidence_linking_checkbox = QCheckBox("")
        self.ocr_checkbox = QCheckBox("")
        self.schema_ocr_checkbox = QCheckBox("")
        self.extraction_ocr_checkbox = QCheckBox("")
        self.custom_t1_t5_rules_checkbox = QCheckBox("")
        self.custom_tx_rules_checkbox = QCheckBox("")
        self.ocr_backend_label = QLabel("")
        self.ocr_backend_combo = ComboBox(self)
        self.ocr_backend_row = QWidget(self)
        self.ocr_backend_row_layout = QHBoxLayout(self.ocr_backend_row)
        self.primary_surya_warmup = SuryaWarmupInline(self)
        self.clear_ocr_cache_button = PrimaryPushButton("")
        self.ocr_fallback_label = QLabel("")
        self.ocr_fallback_combo = ComboBox(self)
        self.ocr_fallback_row = QWidget(self)
        self.ocr_fallback_row_layout = QHBoxLayout(self.ocr_fallback_row)
        self.fallback_surya_warmup = SuryaWarmupInline(self)
        self.apple_ocr_framework_label = QLabel("")
        self.apple_ocr_framework_combo = ComboBox(self)
        self.apple_ocr_recognition_label = QLabel("")
        self.apple_ocr_recognition_combo = ComboBox(self)
        for widget in (
            self.apple_ocr_framework_label,
            self.apple_ocr_framework_combo,
            self.apple_ocr_recognition_label,
            self.apple_ocr_recognition_combo,
        ):
            widget.setVisible(False)
            widget.setEnabled(False)
        self.ocr_device_label = QLabel("")
        self.ocr_device_combo = ComboBox(self)
        self.ocr_dpi_label = QLabel("")
        self.ocr_dpi_edit = QLineEdit(str(self.workbench.settings.ocr_dpi))
        self.diagram_dpi_label = QLabel("")
        self.diagram_dpi_edit = QLineEdit(str(self.workbench.settings.diagram_dpi))
        self.ocr_confidence_label = QLabel("")
        self.ocr_confidence_edit = QLineEdit(str(self.workbench.settings.ocr_min_confidence))
        self.review_confidence_label = QLabel("")
        self.review_confidence_edit = QLineEdit(str(self.workbench.settings.review_low_confidence_threshold))
        self.review_need_review_label = QLabel("")
        self.review_need_review_edit = QLineEdit(str(self.workbench.settings.review_need_review_threshold))
        self.ocr_pipeline_mode_label = QLabel("")
        self.ocr_pipeline_mode_row = QWidget(self)
        self.ocr_pipeline_mode_layout = QHBoxLayout(self.ocr_pipeline_mode_row)
        self.ocr_pipeline_mode_layout.setContentsMargins(0, 0, 0, 0)
        self.ocr_pipeline_mode_layout.setSpacing(10)
        
        self.ocr_pipeline_mode_combo = ComboBox(self)
        self.ocr_pipeline_mode_combo.currentIndexChanged.connect(self._toggle_ensemble_checkboxes)
        
        self.ensemble_apple_cb = QCheckBox("Apple Vision")
        self.ensemble_paddle_cb = QCheckBox("PaddleOCR")
        self.ensemble_surya_cb = QCheckBox("Surya")
        self.ensemble_rapidocr_cb = QCheckBox("RapidOCR")
        self.ensemble_easyocr_cb = QCheckBox("EasyOCR")
        for checkbox in (
            self.ensemble_apple_cb,
            self.ensemble_paddle_cb,
            self.ensemble_surya_cb,
            self.ensemble_rapidocr_cb,
            self.ensemble_easyocr_cb,
        ):
            checkbox.toggled.connect(self._sync_ocr_device_controls)
            checkbox.toggled.connect(self._refresh_runtime_status_label)
        
        self.ocr_pipeline_mode_layout.addWidget(self.ocr_pipeline_mode_combo)
        self.ocr_pipeline_mode_layout.addWidget(self.ensemble_apple_cb)
        self.ocr_pipeline_mode_layout.addWidget(self.ensemble_paddle_cb)
        self.ocr_pipeline_mode_layout.addWidget(self.ensemble_surya_cb)
        self.ocr_pipeline_mode_layout.addWidget(self.ensemble_rapidocr_cb)
        self.ocr_pipeline_mode_layout.addWidget(self.ensemble_easyocr_cb)
        self.ocr_pipeline_mode_layout.addStretch(1)

        self.diagram_checkbox = QCheckBox("")
        self.diagram_backend_label = QLabel("")
        self.diagram_backend_row = QWidget(self)
        self.diagram_backend_row_layout = QHBoxLayout(self.diagram_backend_row)
        self.diagram_backend_row_layout.setContentsMargins(0, 0, 0, 0)
        self.diagram_backend_row_layout.setSpacing(10)
        
        self.diagram_backend_combo = ComboBox(self)
        self.refresh_models_button = ToolButton(FluentIcon.SYNC, self)
        self.refresh_models_button.setToolTip(
            self.t(
                "settings.refresh_models_tooltip",
                default="Fetch chat, embedding, and VLM models from /v1/models API",
            )
        )
        self.refresh_models_button.setFixedWidth(36)
        self.api_key_row_layout.addWidget(self.api_key_edit)
        self.api_key_row_layout.addWidget(self.refresh_models_button)
        self.diagram_backend_row_layout.addWidget(self.diagram_backend_combo)
        self.diagram_backend_row_layout.addStretch(1)

        self.hard_fallback_checkbox = QCheckBox("")
        self.clear_db_checkbox = QCheckBox("")
        self.runtime_status_label = CaptionLabel("")

        self.ocr_backend_row_layout.setContentsMargins(0, 0, 0, 0)
        self.ocr_backend_row_layout.setSpacing(10)
        self.ocr_backend_row_layout.addWidget(self.ocr_backend_combo)
        self.ocr_backend_row_layout.addWidget(self.clear_ocr_cache_button)
        self.ocr_backend_row_layout.addWidget(self.primary_surya_warmup)
        self.ocr_backend_row_layout.addStretch(1)

        self.ocr_fallback_row_layout.setContentsMargins(0, 0, 0, 0)
        self.ocr_fallback_row_layout.setSpacing(10)
        self.ocr_fallback_row_layout.addWidget(self.ocr_fallback_combo)
        self.ocr_fallback_row_layout.addWidget(self.fallback_surya_warmup)
        self.ocr_fallback_row_layout.addStretch(1)

        self.form_layout.addRow(self.language_label, self.language_combo)
        self.form_layout.addRow(self.enable_llm)
        self.form_layout.addRow(self.base_url_label, self.base_url_edit)
        self._llm_form_rows.append((self.base_url_label, self.base_url_edit))
        self.form_layout.addRow(self.chat_model_label, self.chat_model_edit)
        self._llm_form_rows.append((self.chat_model_label, self.chat_model_edit))
        self.form_layout.addRow(self.embedding_model_label, self.embedding_model_edit)
        self._llm_form_rows.append((self.embedding_model_label, self.embedding_model_edit))
        self.form_layout.addRow(self.api_key_label, self.api_key_row)
        self._llm_form_rows.append((self.api_key_label, self.api_key_row))
        self.form_layout.addRow(self.timeout_label, self.timeout_edit)
        self._llm_form_rows.append((self.timeout_label, self.timeout_edit))
        self.form_layout.addRow(self.retry_label, self.retry_edit)
        self._llm_form_rows.append((self.retry_label, self.retry_edit))
        self.form_layout.addRow(self.retrieval_top_k_label, self.retrieval_top_k_edit)
        self._llm_form_rows.append((self.retrieval_top_k_label, self.retrieval_top_k_edit))
        self.form_layout.addRow(self.aio_ml_evidence_linking_checkbox)
        self._llm_form_rows.append((self.aio_ml_evidence_linking_checkbox, self.aio_ml_evidence_linking_checkbox))
        self.form_layout.addRow(self.schema_ocr_checkbox)
        self.form_layout.addRow(self.extraction_ocr_checkbox)
        self.form_layout.addRow(self.custom_t1_t5_rules_checkbox)
        self.form_layout.addRow(self.custom_tx_rules_checkbox)
        self.form_layout.addRow(self.ocr_backend_label, self.ocr_backend_row)
        self.form_layout.addRow(self.ocr_fallback_label, self.ocr_fallback_row)
        self.form_layout.addRow(self.ocr_device_label, self.ocr_device_combo)
        self.form_layout.addRow(self.ocr_dpi_label, self.ocr_dpi_edit)
        self.form_layout.addRow(self.diagram_dpi_label, self.diagram_dpi_edit)
        self.form_layout.addRow(self.ocr_confidence_label, self.ocr_confidence_edit)
        self.form_layout.addRow(self.review_confidence_label, self.review_confidence_edit)
        self.form_layout.addRow(self.review_need_review_label, self.review_need_review_edit)
        self.form_layout.addRow(self.ocr_pipeline_mode_label, self.ocr_pipeline_mode_row)
        self.form_layout.addRow(self.diagram_checkbox)
        self.form_layout.addRow(self.diagram_backend_label, self.diagram_backend_row)
        self.form_layout.addRow(self.hard_fallback_checkbox)
        self.form_layout.addRow(self.clear_db_checkbox)
        self.scroll_content_layout.addLayout(self.form_layout)
        self.scroll_content_layout.addWidget(self.runtime_status_label)

        self.save_button = PrimaryPushButton("")
        self.save_button.clicked.connect(self.save_settings)
        self.clear_ocr_cache_button.clicked.connect(self._confirm_clear_cache)
        self.primary_surya_warmup.requested.connect(self._prewarm_surya_models)
        self.fallback_surya_warmup.requested.connect(self._prewarm_surya_models)
        self.ocr_backend_combo.currentIndexChanged.connect(self._sync_apple_ocr_controls)
        self.ocr_fallback_combo.currentIndexChanged.connect(self._sync_apple_ocr_controls)
        self.ocr_backend_combo.currentIndexChanged.connect(self._sync_ocr_device_controls)
        self.ocr_fallback_combo.currentIndexChanged.connect(self._sync_ocr_device_controls)
        self.ocr_backend_combo.currentIndexChanged.connect(self._sync_surya_warmup_controls)
        self.ocr_fallback_combo.currentIndexChanged.connect(self._sync_surya_warmup_controls)
        self.language_combo.currentIndexChanged.connect(self._log_language_changed)
        self.ocr_backend_combo.currentIndexChanged.connect(self._log_ocr_backend_changed)
        self.ocr_fallback_combo.currentIndexChanged.connect(self._log_ocr_fallback_changed)
        self.ocr_pipeline_mode_combo.currentIndexChanged.connect(self._log_ocr_pipeline_mode_changed)
        self.diagram_backend_combo.currentIndexChanged.connect(self._log_diagram_backend_changed)
        self.ocr_device_combo.currentIndexChanged.connect(self._log_ocr_device_changed)
        self.refresh_models_button.clicked.connect(self._on_refresh_vlm_models_clicked)
        self.enable_llm.toggled.connect(self._sync_llm_controls)
        self.base_url_edit.textChanged.connect(self._sync_llm_controls)
        self.api_key_edit.textChanged.connect(self._sync_llm_controls)
        self.scroll_content_layout.addStretch(1)
        self.root_layout.addWidget(self.scroll_area, 1)
        self.footer_row = QWidget(self)
        self.footer_row_layout = QHBoxLayout(self.footer_row)
        self.footer_row_layout.setContentsMargins(0, 0, 0, 0)
        self.footer_row_layout.setSpacing(12)
        self.footer_row_layout.addWidget(self.save_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.footer_row_layout.addStretch(1)
        self.root_layout.addWidget(self.footer_row, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self._fetched_model_catalog: list[str] = []
        self._model_catalog_fetched.connect(self._apply_model_catalog)
        self.apply_language()
        self.refresh()

    def _on_refresh_vlm_models_clicked(self) -> None:
        self._fetch_model_catalog(show_errors=True)

    def _confirm_clear_cache(self) -> None:
        if self._task_thread is not None and self._task_thread.isRunning():
            return
        cache_path = self.workbench.display_cache_dir()
        self.log_debug("clear_cache_requested", f"Requested cache clear for {cache_path}")
        confirmed = QMessageBox.warning(
            self,
            self.t("settings.clear_ocr_cache_confirm_title"),
            self.t(
                "settings.clear_ocr_cache_confirm_body",
                path=cache_path,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            self.log_debug("clear_cache_cancelled", "Cache clear cancelled")
            return
        try:
            removed_entries = self.workbench.clear_cache()
        except (OSError, shutil.Error) as exc:
            self.show_error("settings.clear_ocr_cache_failed_title", str(exc))
            return
        self.log_debug(
            "clear_cache_succeeded",
            f"Cleared caches at {cache_path}",
            details={"removed_entries": removed_entries},
        )
        QMessageBox.information(
            self,
            self.t("settings.clear_ocr_cache_success_title"),
            self.t(
                "settings.clear_ocr_cache_success_body",
                count=removed_entries,
                path=cache_path,
            ),
        )

    def _normalized_model_api_url(self, base_url: str) -> str:
        api_url = base_url.split("/chat/completions")[0].rstrip("/")
        if not api_url.endswith("/v1"):
            api_url = api_url + "/v1"
        return api_url

    def _format_model_fetch_error(self, api_url: str, error: Exception) -> dict[str, str]:
        details = repr(error)
        if isinstance(error, requests.exceptions.ConnectionError):
            return {
                "summary": (
                    f"Could not reach the model server at {api_url}. "
                    "Start your OpenAI-compatible backend, or update the Base URL."
                ),
                "details": details,
            }
        if isinstance(error, requests.exceptions.Timeout):
            return {
                "summary": (
                    f"The model server at {api_url} did not respond in time. "
                    "Make sure it is running and reachable."
                ),
                "details": details,
            }
        if isinstance(error, requests.exceptions.HTTPError):
            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", "unknown")
            return {
                "summary": f"The model server at {api_url} returned HTTP {status_code} for /models.",
                "details": details,
            }
        return {
            "summary": f"Failed to fetch models from {api_url}: {error}",
            "details": details,
        }

    def _show_model_fetch_error(self, error: object) -> None:
        summary = "Failed to fetch models."
        details = ""
        if isinstance(error, dict):
            summary = str(error.get("summary", summary))
            details = str(error.get("details", "")).strip()
        elif error:
            summary = str(error)
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle("Error")
        dialog.setText("Failed to fetch models.")
        dialog.setInformativeText(summary)
        if details:
            dialog.setDetailedText(details)
        dialog.exec()

    def _sync_llm_controls(self, *_args) -> None:
        llm_enabled = self.enable_llm.isChecked()
        for label, field in self._llm_form_rows:
            self.form_layout.setRowVisible(label, llm_enabled)
            label.setVisible(llm_enabled)
            label.setEnabled(llm_enabled)
            field.setVisible(llm_enabled)
            field.setEnabled(llm_enabled)
        has_base_url = bool(self.base_url_edit.text().strip())
        if not has_base_url:
            tooltip = "Enter a Base URL to fetch models."
        else:
            tooltip = self.t(
                "settings.refresh_models_tooltip",
                default="Fetch chat, embedding, and VLM models from /v1/models API",
            )
        self.refresh_models_button.setVisible(llm_enabled)
        self.refresh_models_button.setEnabled(llm_enabled and has_base_url)
        self.refresh_models_button.setToolTip(tooltip)

    def _resolved_api_key_for_settings(self) -> str:
        return (
            self.api_key_edit.text().strip()
            or self.workbench.settings.llm.api_key
            or self.workbench.llm_client.resolved_api_key()
        )

    def _fetch_model_catalog(self, show_errors: bool = True) -> None:
        base_url = self.base_url_edit.text().strip()
        if not base_url:
            if show_errors:
                QMessageBox.warning(self, "Error", "Base URL is empty.")
            return

        api_url = self._normalized_model_api_url(base_url)
        url = f"{api_url}/models"
        api_key = self._resolved_api_key_for_settings()
        headers = {}
        if api_key and api_key != "not-needed":
            headers["Authorization"] = f"Bearer {api_key}"

        self.refresh_models_button.setEnabled(False)
        self.refresh_models_button.setText("...")

        def fetch_task():
            try:
                response = requests.get(url, headers=headers, timeout=5.0)
                response.raise_for_status()
                data = response.json()
                models = [str(m["id"]) for m in data.get("data", []) if str(m.get("id", "")).strip()]
                self._model_catalog_fetched.emit(models, None, show_errors)
            except Exception as e:
                self._model_catalog_fetched.emit(None, self._format_model_fetch_error(api_url, e), show_errors)

        import threading
        threading.Thread(target=fetch_task, daemon=True).start()

    def _toggle_ensemble_checkboxes(self) -> None:
        is_ensemble = str(self.ocr_pipeline_mode_combo.currentData() or "fallback") == "ensemble"
        self.ensemble_apple_cb.setVisible(is_ensemble)
        self.ensemble_paddle_cb.setVisible(is_ensemble)
        self.ensemble_surya_cb.setVisible(is_ensemble)
        self.ensemble_rapidocr_cb.setVisible(is_ensemble)
        self.ensemble_easyocr_cb.setVisible(is_ensemble)
        for widget in (
            self.ocr_backend_label,
            self.ocr_backend_row,
            self.ocr_fallback_label,
            self.ocr_fallback_row,
        ):
            widget.setVisible(not is_ensemble)
            widget.setEnabled(not is_ensemble)
        self._sync_ocr_device_controls()
        self._sync_surya_warmup_controls()
        self._refresh_runtime_status_label()

    def _model_options(
        self,
        *,
        current_value: str,
        include_none: bool = False,
        embedding_only: bool = False,
        exclude_embedding: bool = False,
        local_embedding: bool = False,
        prefix: str = "",
        label_prefix: str = "",
    ) -> list[tuple[str, str]]:
        options: list[tuple[str, str]] = []

        def _label_for(model_id: str) -> str:
            if not label_prefix:
                return model_id
            if label_prefix.endswith("("):
                return f"{label_prefix}{model_id})"
            return f"{label_prefix}{model_id}"

        if include_none:
            options.append(("none", self.t("settings.diagram_backend.none")))
        if local_embedding:
            options.append(("local-hash-768", "Local hash fallback (no API embedding)"))
        for model_id in self._fetched_model_catalog:
            lowered = model_id.lower()
            if embedding_only and "embedding" not in lowered:
                continue
            if exclude_embedding and "embedding" in lowered:
                continue
            value = f"{prefix}{model_id}" if prefix else model_id
            label = _label_for(model_id)
            if value not in [item[0] for item in options]:
                options.append((value, label))
        normalized_current = str(current_value or "").strip()
        if normalized_current and normalized_current not in [item[0] for item in options]:
            label_value = normalized_current.split(":", 1)[1] if prefix and normalized_current.startswith(prefix) else normalized_current
            label = _label_for(label_value)
            options.append((normalized_current, label))
        return options

    def _selected_combo_value(self, combo: ComboBox, fallback: str = "") -> str:
        value = str(combo.currentData() or "").strip()
        return value or fallback

    def _apply_model_catalog(self, models: list[str] | None, error: object | None, show_errors: bool) -> None:
        self._sync_llm_controls()
        self.refresh_models_button.setText("")

        if error:
            if show_errors:
                self._show_model_fetch_error(error)
            return

        if not models:
            if show_errors:
                QMessageBox.warning(self, "No Models", "Received empty model list from server.")
            return

        self._fetched_model_catalog = list(models)
        self._refresh_model_selectors()

    def _apply_scroll_area_style(self) -> None:
        if isDarkTheme():
            handle = "#8d939c"
            handle_hover = "#aab1bb"
        else:
            handle = "#b0b7c3"
            handle_hover = "#8f98a6"
        self.scroll_area.setStyleSheet(
            f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 12px;
                margin: 6px 2px 6px 2px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {handle};
                min-height: 48px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {handle_hover};
            }}
            QScrollBar::handle:vertical:pressed {{
                background: {handle_hover};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
                background: transparent;
                border: none;
            }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            """
        )

    def apply_language(self) -> None:
        super().apply_language()
        self._apply_scroll_area_style()
        self.language_label.setText("Interface language")
        self.enable_llm.setText(self.t("common.enable_http_llm"))
        self.base_url_label.setText(self.t("common.base_url"))
        self.chat_model_label.setText(self.t("common.chat_model"))
        self.embedding_model_label.setText(self.t("common.embedding_model"))
        self.api_key_label.setText(self.t("common.api_key"))
        self.timeout_label.setText(self.t("common.timeout"))
        self.retry_label.setText(self.t("common.max_retries"))
        self.retrieval_top_k_label.setText(self.t("common.top_k"))
        self.retrieval_top_k_edit.setToolTip(self._retrieval_top_k_hint_text())
        self.retrieval_top_k_edit.setPlaceholderText(self._retrieval_top_k_hint_text())
        self.aio_ml_evidence_linking_checkbox.setText(
            self.t("settings.aio_ml_evidence_linking_checkbox", default="Enable AIO ML evidence linking")
        )
        self.aio_ml_evidence_linking_checkbox.setToolTip(
            self.t(
                "settings.aio_ml_evidence_linking_tooltip",
                default="Use the configured chat and embedding models to link AIO values to source artifacts.",
            )
        )
        self.schema_ocr_checkbox.setText(self.t("settings.schema_ocr_checkbox"))
        self.extraction_ocr_checkbox.setText(self.t("settings.extraction_ocr_checkbox"))
        self.custom_t1_t5_rules_checkbox.setText(self.t("settings.custom_t1_t5_rules_checkbox"))
        self.custom_tx_rules_checkbox.setText(self.t("settings.custom_tx_rules_checkbox"))
        self.ocr_backend_label.setText(self.t("settings.ocr_backend_label"))
        self.ocr_fallback_label.setText(self.t("settings.ocr_fallback_label"))
        self.ocr_device_label.setText(self.t("settings.ocr_device_label"))
        self.ocr_dpi_label.setText(self.t("settings.ocr_dpi_label"))
        self.diagram_dpi_label.setText(self.t("settings.diagram_dpi_label"))
        self.ocr_confidence_label.setText(self.t("settings.ocr_confidence_label"))
        self.review_confidence_label.setText(self._review_confidence_label_text())
        self.review_need_review_label.setText(self._review_need_review_label_text())
        self.ocr_pipeline_mode_label.setText(self.t("settings.ocr_pipeline_mode_label", default="OCR Pipeline Mode"))
        self._populate_ocr_combo(
            self.ocr_pipeline_mode_combo,
            self.workbench.settings.ocr_pipeline_mode,
            [
                ("fallback", self.t("settings.ocr_pipeline_mode.fallback", default="Fallback (Serial)")),
                ("ensemble", self.t("settings.ocr_pipeline_mode.ensemble", default="Ensemble (Parallel Fusion)")),
            ],
        )
        self.diagram_checkbox.setText(self.t("settings.diagram_checkbox"))
        self.diagram_backend_label.setText(self.t("settings.diagram_backend_label"))
        self.clear_ocr_cache_button.setText(self.t("settings.clear_ocr_cache_button"))
        self.clear_ocr_cache_button.setToolTip(
            self.t(
                "settings.clear_ocr_cache_tooltip",
                path=self.workbench.display_cache_dir(),
            )
        )
        self._refresh_model_selectors()
        self.hard_fallback_checkbox.setText(self.t("settings.hard_fallback_checkbox"))
        self.clear_db_checkbox.setText(self.t("common.clear_db_before_extraction"))
        self.save_button.setText(self.t("common.save_settings"))
        self.primary_surya_warmup.apply_language(self.language)
        self.fallback_surya_warmup.apply_language(self.language)
        self._populate_language_combo()
        self._populate_ocr_combo(
            self.ocr_backend_combo,
            self.workbench.settings.ocr_backend,
            _primary_ocr_options(),
        )
        self._populate_ocr_combo(
            self.ocr_fallback_combo,
            self.workbench.settings.ocr_fallback_backend,
            _fallback_ocr_options(self.t("common.none")),
        )
        self._sync_ocr_device_controls(current_value=self.workbench.settings.ocr_device)
        self._sync_apple_ocr_controls()
        self._sync_surya_warmup_controls()
        self._sync_llm_controls()

    def _populate_language_combo(self) -> None:
        current_value = self.language_combo.currentData() or self.workbench.settings.ui_language
        self.language_combo.blockSignals(True)
        try:
            self.language_combo.clear()
            for code, label in SETTINGS_LANGUAGE_OPTIONS:
                self.language_combo.addItem(label, userData=code)
            for row in range(self.language_combo.count()):
                if self.language_combo.itemData(row) == current_value:
                    self.language_combo.setCurrentIndex(row)
                    return
        finally:
            self.language_combo.blockSignals(False)

    def _populate_ocr_combo(self, combo: ComboBox, current_value: str, items: list[tuple[str, str]]) -> None:
        combo.blockSignals(True)
        try:
            combo.clear()
            for value, label in items:
                combo.addItem(label, userData=value)
            for row in range(combo.count()):
                if combo.itemData(row) == current_value:
                    combo.setCurrentIndex(row)
                    return
        finally:
            combo.blockSignals(False)

    def _sync_apple_ocr_controls(self, *_args) -> None:
        for widget in (
            self.apple_ocr_framework_label,
            self.apple_ocr_framework_combo,
            self.apple_ocr_recognition_label,
            self.apple_ocr_recognition_combo,
        ):
            widget.setVisible(False)
            widget.setEnabled(False)

    def _sync_ocr_device_controls(self, *_args, current_value: str | None = None) -> None:
        if current_value is None:
            current_value = str(self.ocr_device_combo.currentData() or self.workbench.settings.ocr_device)
        self._populate_ocr_combo(
            self.ocr_device_combo,
            current_value,
            _ocr_device_options(current_value),
        )
        if self._current_ocr_pipeline_mode() == "ensemble":
            backend_values = self._selected_ensemble_backend_values()
        else:
            backend_values = [
                self._selected_backend_value(self.ocr_backend_combo),
                self._selected_backend_value(self.ocr_fallback_combo),
            ]
        device_relevant = any(_backend_uses_configurable_device(value) for value in backend_values)
        self.ocr_device_combo.setEnabled(device_relevant and self.ocr_device_combo.count() > 1)

    def _selected_backend_value(self, combo: ComboBox) -> str:
        return str(combo.currentData() or "")

    def _current_ocr_pipeline_mode(self) -> str:
        return str(self.ocr_pipeline_mode_combo.currentData() or self.workbench.settings.ocr_pipeline_mode or "fallback")

    def _selected_ensemble_backend_values(self) -> list[str]:
        values: list[str] = []
        if self.ensemble_apple_cb.isChecked():
            values.append("apple")
        if self.ensemble_paddle_cb.isChecked():
            values.append("paddle")
        if self.ensemble_surya_cb.isChecked():
            values.append("surya")
        if self.ensemble_rapidocr_cb.isChecked():
            values.append("rapidocr")
        if self.ensemble_easyocr_cb.isChecked():
            values.append("easyocr")
        return values

    def _refresh_runtime_status_label(self, *_args) -> None:
        if hasattr(self, "runtime_status_label"):
            self.runtime_status_label.setText(self._runtime_summary())

    def _log_language_changed(self, *_args) -> None:
        value = str(self.language_combo.currentData() or "")
        if value:
            self.log_debug("language_changed", f"Interface language changed to {value}")

    def _log_ocr_backend_changed(self, *_args) -> None:
        value = self._selected_backend_value(self.ocr_backend_combo)
        if value:
            self.log_debug("ocr_backend_changed", f"Primary OCR backend changed to {value}")

    def _log_ocr_fallback_changed(self, *_args) -> None:
        value = self._selected_backend_value(self.ocr_fallback_combo)
        if value:
            self.log_debug("ocr_fallback_changed", f"OCR fallback backend changed to {value}")

    def _log_ocr_pipeline_mode_changed(self, *_args) -> None:
        value = str(self.ocr_pipeline_mode_combo.currentData() or "")
        if value:
            self.log_debug("ocr_pipeline_mode_changed", f"OCR pipeline mode changed to {value}")

    def _log_diagram_backend_changed(self, *_args) -> None:
        value = self._diagram_backend_combo_value()
        if value:
            self.log_debug("diagram_backend_changed", f"Diagram backend changed to {value}")

    def _log_ocr_device_changed(self, *_args) -> None:
        value = str(self.ocr_device_combo.currentData() or "")
        if value:
            self.log_debug("ocr_device_changed", f"OCR device selection changed to {value}")

    def _diagram_backend_combo_value(self) -> str:
        """Build the combo-box data value from current settings."""
        s = self.workbench.settings
        if s.diagram_extraction_backend == "vlm" and s.llm.vlm_model:
            return f"vlm:{s.llm.vlm_model}"
        return "none"

    def _refresh_model_selectors(self) -> None:
        current_chat = self.workbench.settings.llm.chat_model or "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
        current_embedding = self.workbench.settings.llm.embedding_model or "local-hash-768"
        current_vlm = self._diagram_backend_combo_value()
        self._populate_ocr_combo(
            self.chat_model_edit,
            current_chat,
            self._model_options(current_value=current_chat, exclude_embedding=True),
        )
        self._populate_ocr_combo(
            self.embedding_model_edit,
            current_embedding,
            self._model_options(current_value=current_embedding, embedding_only=True, local_embedding=True),
        )
        self._populate_ocr_combo(
            self.diagram_backend_combo,
            current_vlm,
            self._model_options(
                current_value=current_vlm,
                include_none=True,
                exclude_embedding=True,
                prefix="vlm:",
                label_prefix="VLM (",
            ),
        )

    def _surya_summary_text(self, status: dict[str, object]) -> str:
        if not bool(status.get("available", True)):
            return self.t("settings.runtime_state.unavailable")
        ready_count = int(status.get("ready_count", 0))
        model_count = int(status.get("model_count", 0))
        if bool(status.get("ready")):
            return self.t("settings.surya_prewarm_ready", ready=ready_count, total=model_count)
        return self.t("settings.surya_prewarm_missing", ready=ready_count, total=model_count)

    def _sync_surya_warmup_controls(self, *_args) -> None:
        status = self.workbench.surya_model_cache_status()
        summary = self._surya_summary_text(status)
        primary_selected = self._selected_backend_value(self.ocr_backend_combo) == "surya"
        fallback_selected = self._selected_backend_value(self.ocr_fallback_combo) == "surya"
        for selected, widget in (
            (primary_selected, self.primary_surya_warmup),
            (fallback_selected, self.fallback_surya_warmup),
        ):
            if not selected:
                widget.setVisible(False)
                continue
            if not bool(status.get("available", True)):
                widget.set_ready_state(summary)
                continue
            if bool(status.get("ready")):
                widget.set_ready_state(summary)
            else:
                widget.set_idle_state(summary)

    def _set_surya_warmup_running(self, value: int, message: str) -> None:
        for widget in (self.primary_surya_warmup, self.fallback_surya_warmup):
            if widget.isVisible():
                widget.set_running_state(value, message)

    def _prewarm_surya_models(self) -> None:
        self.run_background_task(
            "prewarm_surya_models",
            "settings.surya_prewarm_title",
            "settings.surya_prewarm_body",
            "settings.surya_prewarm_failed",
            self._handle_surya_prewarm_complete,
            on_progress=self._set_surya_warmup_running,
            on_complete=lambda _success: self._sync_surya_warmup_controls(),
        )

    def _handle_surya_prewarm_complete(self, _result: object) -> None:
        self._sync_surya_warmup_controls()

    def _review_confidence_label_text(self) -> str:
        if self.language == "de":
            return "Review: niedrige Konfidenz"
        if self.language == "zh":
            return "复核低置信度阈值"
        return "Review low-confidence threshold"

    def _review_need_review_label_text(self) -> str:
        if self.language == "de":
            return "Review: Prüfbedarf Konfidenz"
        if self.language == "zh":
            return "复核待审置信度阈值"
        return "Review need-review threshold"

    def refresh(self, *_args) -> None:
        settings = self.workbench.settings
        settings.apple_ocr_framework = "vision"
        settings.apple_ocr_recognition_level = "accurate"
        self._apply_scroll_area_style()
        self._populate_language_combo()
        self._populate_ocr_combo(
            self.ocr_backend_combo,
            settings.ocr_backend,
            _primary_ocr_options(),
        )
        self._populate_ocr_combo(
            self.ocr_fallback_combo,
            settings.ocr_fallback_backend,
            _fallback_ocr_options(self.t("common.none")),
        )
        self._sync_ocr_device_controls(current_value=settings.ocr_device)
        self.enable_llm.setChecked(settings.llm.enabled)
        self.base_url_edit.setText(settings.llm.base_url)
        self._refresh_model_selectors()
        self.api_key_edit.setText(settings.llm.api_key or "")
        self.timeout_edit.setText(str(settings.llm.timeout))
        self.retry_edit.setText(str(settings.llm.max_retries))
        self.retrieval_top_k_edit.setText(str(settings.retrieval_top_k))
        self.aio_ml_evidence_linking_checkbox.setChecked(
            bool(settings.aio_ml_evidence_linking or settings.aio_ml_evidence_linking_enabled)
        )
        self.schema_ocr_checkbox.setChecked(settings.schema_generation_use_ocr)
        self.extraction_ocr_checkbox.setChecked(settings.extraction_use_ocr)
        self.custom_t1_t5_rules_checkbox.setChecked(settings.use_custom_t1_t5_rules)
        self.custom_tx_rules_checkbox.setChecked(settings.use_custom_tx_rules)
        self.ocr_dpi_edit.setText(str(settings.ocr_dpi))
        self.diagram_dpi_edit.setText(str(settings.diagram_dpi))
        self.ocr_confidence_edit.setText(str(settings.ocr_min_confidence))
        self.review_confidence_edit.setText(str(settings.review_low_confidence_threshold))
        self.review_need_review_edit.setText(str(settings.review_need_review_threshold))
        self._populate_ocr_combo(
            self.ocr_pipeline_mode_combo,
            settings.ocr_pipeline_mode,
            [
                ("fallback", self.t("settings.ocr_pipeline_mode.fallback", default="Fallback (Serial)")),
                ("ensemble", self.t("settings.ocr_pipeline_mode.ensemble", default="Ensemble (Parallel Fusion)")),
            ],
        )

        # Set check state for ensemble engines
        backends = settings.ocr_ensemble_backends
        self.ensemble_apple_cb.setChecked("apple" in backends)
        self.ensemble_paddle_cb.setChecked("paddle" in backends)
        self.ensemble_surya_cb.setChecked("surya" in backends)
        self.ensemble_rapidocr_cb.setChecked("rapidocr" in backends)
        self.ensemble_easyocr_cb.setChecked("easyocr" in backends)
        self._toggle_ensemble_checkboxes()

        self.diagram_checkbox.setChecked(settings.enable_diagram_relation_extraction)
        self._refresh_model_selectors()
        self.hard_fallback_checkbox.setChecked(settings.enable_hard_page_fallback)
        self.clear_db_checkbox.setChecked(settings.clear_database_before_extraction)
        self._sync_apple_ocr_controls()
        self._sync_surya_warmup_controls()
        self._sync_llm_controls()
        self.runtime_status_label.setText(self._runtime_summary())

        if settings.llm.base_url.strip():
            self._fetch_model_catalog(show_errors=False)

    def save_settings(self) -> None:
        ocr_defaults = _ocr_defaults()
        settings = ProjectSettings.model_validate(self.workbench.settings.model_dump())
        settings.ui_language = normalize_language(str(self.language_combo.currentData() or "en"))
        settings.llm.enabled = self.enable_llm.isChecked()
        settings.llm.base_url = self.base_url_edit.text().strip()
        settings.llm.chat_model = self._selected_combo_value(
            self.chat_model_edit,
            self.workbench.settings.llm.chat_model,
        )
        settings.llm.embedding_model = self._selected_combo_value(
            self.embedding_model_edit,
            self.workbench.settings.llm.embedding_model,
        )
        settings.llm.api_key = self.api_key_edit.text().strip() or None
        settings.llm.timeout = float(self.timeout_edit.text().strip() or "60")
        settings.llm.max_retries = int(self.retry_edit.text().strip() or "1")
        settings.retrieval_top_k = max(1, int(self.retrieval_top_k_edit.text().strip() or "6"))
        settings.aio_ml_evidence_linking = self.aio_ml_evidence_linking_checkbox.isChecked()
        settings.aio_ml_evidence_linking_enabled = settings.aio_ml_evidence_linking
        settings.schema_generation_use_ocr = self.schema_ocr_checkbox.isChecked()
        settings.extraction_use_ocr = self.extraction_ocr_checkbox.isChecked()
        settings.use_custom_t1_t5_rules = self.custom_t1_t5_rules_checkbox.isChecked()
        settings.use_custom_tx_rules = self.custom_tx_rules_checkbox.isChecked()
        settings.ocr_enabled = settings.schema_generation_use_ocr or settings.extraction_use_ocr
        settings.ocr_backend = str(self.ocr_backend_combo.currentData() or ocr_defaults.ocr_backend)
        settings.ocr_fallback_backend = str(
            self.ocr_fallback_combo.currentData() or ocr_defaults.ocr_fallback_backend
        )
        settings.apple_ocr_framework = "vision"
        settings.apple_ocr_recognition_level = "accurate"
        settings.ocr_device = str(self.ocr_device_combo.currentData() or ocr_defaults.ocr_device)
        settings.ocr_dpi = int(self.ocr_dpi_edit.text().strip() or "300")
        settings.diagram_dpi = int(self.diagram_dpi_edit.text().strip() or "400")
        settings.ocr_min_confidence = float(self.ocr_confidence_edit.text().strip() or "0.82")
        settings.review_low_confidence_threshold = float(self.review_confidence_edit.text().strip() or "0.8")
        settings.review_need_review_threshold = float(self.review_need_review_edit.text().strip() or "0.5")
        settings.ocr_pipeline_mode = str(self.ocr_pipeline_mode_combo.currentData() or "fallback")
        
        # Save ensemble backends
        ensemble_backends = []
        if self.ensemble_apple_cb.isChecked(): ensemble_backends.append("apple")
        if self.ensemble_paddle_cb.isChecked(): ensemble_backends.append("paddle")
        if self.ensemble_surya_cb.isChecked(): ensemble_backends.append("surya")
        if self.ensemble_rapidocr_cb.isChecked(): ensemble_backends.append("rapidocr")
        if self.ensemble_easyocr_cb.isChecked(): ensemble_backends.append("easyocr")
        settings.ocr_ensemble_backends = ensemble_backends

        settings.enable_diagram_relation_extraction = self.diagram_checkbox.isChecked()
        _raw_backend = str(self.diagram_backend_combo.currentData() or "none")
        if _raw_backend.startswith("vlm:"):
            settings.diagram_extraction_backend = "vlm"
            settings.llm.vlm_model = _raw_backend.split(":", 1)[1]
        else:
            settings.diagram_extraction_backend = "none"
        settings.enable_hard_page_fallback = self.hard_fallback_checkbox.isChecked()
        settings.clear_database_before_extraction = self.clear_db_checkbox.isChecked()
        self.workbench.save_settings(settings)
        self.api_key_edit.clear()
        self.log_debug(
            "save_settings",
            "Saved model settings",
            details={
                "ocr_backend": settings.ocr_backend,
                "ocr_fallback_backend": settings.ocr_fallback_backend,
                "ocr_pipeline_mode": settings.ocr_pipeline_mode,
                "ocr_ensemble_backends": settings.ocr_ensemble_backends,
                "ocr_device": settings.ocr_device,
                "llm_enabled": settings.llm.enabled,
                "chat_model": settings.llm.chat_model,
                "embedding_model": settings.llm.embedding_model,
                "vlm_model": settings.llm.vlm_model,
                "aio_ml_evidence_linking": settings.aio_ml_evidence_linking,
            },
        )
        self.refresh_all()

    def _retrieval_top_k_hint_text(self) -> str:
        if self.language == "de":
            return "Anzahl gefundener Evidenzblöcke pro Feld"
        if self.language == "zh":
            return "每个字段检索的证据片段数量"
        return "number of evidence chunks retrieved per field"

    def _runtime_summary(self) -> str:
        status = self.workbench.ocr_runtime_status()
        if self._current_ocr_pipeline_mode() == "ensemble":
            selected_backends = self._selected_ensemble_backend_values()
            backend_labels = []
            for backend in selected_backends:
                state = self.t(
                    "settings.runtime_state.available"
                    if status.get(f"{backend}_available")
                    else "settings.runtime_state.unavailable"
                )
                backend_labels.append(
                    f"{_ocr_backend_display_name(backend, self.t('common.none'))} ({state})"
                )
            return self.t(
                "settings.runtime_summary_ensemble",
                ensemble_backends=", ".join(backend_labels) or self.t("common.none"),
            )
        primary = _ocr_backend_display_name(str(status.get("primary_backend", "paddle")), self.t("common.none"))
        primary_state = self.t(
            "settings.runtime_state.available" if status.get("primary_available") else "settings.runtime_state.unavailable"
        )
        fallback = _ocr_backend_display_name(str(status.get("fallback_backend", "surya")), self.t("common.none"))
        fallback_state = self.t(
            "settings.runtime_state.available" if status.get("fallback_available") else "settings.runtime_state.unavailable"
        )
        rapidocr_state = self.t(
            "settings.runtime_state.available" if status.get("rapidocr_available") else "settings.runtime_state.unavailable"
        )
        easyocr_state = self.t(
            "settings.runtime_state.available" if status.get("easyocr_available") else "settings.runtime_state.unavailable"
        )
        easyocr_device = str(status.get("easyocr_device", "cpu"))
        if not _is_macos():
            paddle_state = self.t(
                "settings.runtime_state.available" if status.get("paddle_available") else "settings.runtime_state.unavailable"
            )
            surya_cache = self.workbench.surya_model_cache_status()
            if not status.get("surya_available"):
                surya_state = self.t("settings.runtime_state.unavailable")
            elif bool(surya_cache.get("ready")):
                surya_state = self.t("settings.runtime_state.available")
            else:
                surya_state = self.t("settings.runtime_state.download_required")
            return self.t(
                "settings.runtime_summary_non_macos",
                paddle_state=paddle_state,
                rapidocr_state=rapidocr_state,
                surya_state=surya_state,
                easyocr_state=easyocr_state,
                easyocr_device=easyocr_device,
            )
        active_device = status.get("active_device", "cpu")
        apple_framework = status.get("apple_framework", "vision")
        apple_recognition_level = status.get("apple_recognition_level", "fast")
        return self.t(
            "settings.runtime_summary",
            primary=primary,
            primary_state=primary_state,
            fallback=fallback,
            fallback_state=fallback_state,
            rapidocr_state=rapidocr_state,
            easyocr_state=easyocr_state,
            easyocr_device=easyocr_device,
            active_device=active_device,
            apple_framework=apple_framework,
            apple_recognition_level=apple_recognition_level,
        )

    def set_task_running(self, running: bool) -> None:
        self.save_button.setDisabled(running)
        self.clear_ocr_cache_button.setDisabled(running)
