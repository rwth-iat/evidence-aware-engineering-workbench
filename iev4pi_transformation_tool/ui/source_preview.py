from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QPointF, QRectF, Qt, QThread
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iev4pi_transformation_tool.ui.i18n import normalize_language, tr
from iev4pi_transformation_tool.ui.qfluent import BodyLabel, ComboBox, IndeterminateProgressRing, StrongBodyLabel
from iev4pi_transformation_tool.ui.tasking import TaskWorker


class PdfSourcePreviewView(QGraphicsView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._overlay_items: list[object] = []
        self._scene_rect = QRectF()
        self._focus_rect = QRectF()
        self._min_scale = 1.0
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setBackgroundBrush(QColor("#1f1f1f"))

    def clear_preview(self) -> None:
        self._scene.clear()
        self._overlay_items.clear()
        self._pixmap_item = None
        self._scene_rect = QRectF()
        self._focus_rect = QRectF()
        self._min_scale = 1.0
        self.resetTransform()

    def set_preview(
        self,
        pixmap: QPixmap,
        *,
        highlight_kind: str,
        highlight_geometry: dict[str, object],
        viewport_rect: tuple[float, float, float, float] | None,
    ) -> None:
        self.clear_preview()
        if pixmap.isNull():
            return
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene_rect = QRectF(0.0, 0.0, float(pixmap.width()), float(pixmap.height()))
        self._scene.setSceneRect(self._scene_rect)
        overlay_pen = QPen(QColor("#ff2b2b"))
        overlay_pen.setWidthF(6.0)
        overlay_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        overlay_pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        if highlight_kind == "rect":
            rect_data = highlight_geometry.get("rect")
            if isinstance(rect_data, list) and len(rect_data) == 4:
                rect = QRectF(
                    float(rect_data[0]),
                    float(rect_data[1]),
                    float(rect_data[2] - rect_data[0]),
                    float(rect_data[3] - rect_data[1]),
                )
                item = QGraphicsRectItem(rect)
                item.setPen(overlay_pen)
                self._scene.addItem(item)
                self._overlay_items.append(item)
        elif highlight_kind == "polyline":
            polyline = highlight_geometry.get("polyline")
            if isinstance(polyline, list) and polyline:
                path = QPainterPath()
                for index, point in enumerate(polyline):
                    if not isinstance(point, (list, tuple)) or len(point) != 2:
                        continue
                    current = QPointF(float(point[0]), float(point[1]))
                    if index == 0:
                        path.moveTo(current)
                    else:
                        path.lineTo(current)
                if not path.isEmpty():
                    item = QGraphicsPathItem(path)
                    item.setPen(overlay_pen)
                    self._scene.addItem(item)
                    self._overlay_items.append(item)

        if viewport_rect is not None:
            self._focus_rect = QRectF(
                float(viewport_rect[0]),
                float(viewport_rect[1]),
                float(viewport_rect[2] - viewport_rect[0]),
                float(viewport_rect[3] - viewport_rect[1]),
            )
        else:
            self._focus_rect = QRectF()
        self.fit_to_highlight()

    def zoom_in(self) -> None:
        self.scale(1.2, 1.2)

    def zoom_out(self) -> None:
        current_scale = self._current_scale()
        if current_scale <= self._min_scale + 1e-6:
            return
        target_scale = max(self._min_scale, current_scale / 1.2)
        self._set_scale(target_scale)

    def reset_zoom(self) -> None:
        self.fit_full_page()

    def fit_full_page(self) -> None:
        if self._scene_rect.isValid():
            self.fitInView(self._scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self._min_scale = max(1e-6, self._current_scale())

    def fit_to_highlight(self) -> None:
        target = self._focus_rect if self._focus_rect.isValid() and not self._focus_rect.isEmpty() else self._scene_rect
        if not target.isValid() or target.isEmpty():
            return
        padding = max(24.0, min(target.width(), target.height()) * 0.1)
        padded = target.adjusted(-padding, -padding, padding, padding)
        self.fitInView(padded, Qt.AspectRatioMode.KeepAspectRatio)
        self._update_min_scale()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_in()
            event.accept()
            return
        if delta < 0:
            self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:
        previous_scale = self._current_scale()
        previous_min_scale = self._min_scale
        super().resizeEvent(event)
        if not self._scene_rect.isValid() or self._scene_rect.isEmpty():
            return
        self._update_min_scale()
        if previous_scale <= previous_min_scale + 1e-6 or previous_scale < self._min_scale:
            self.fit_full_page()

    def _current_scale(self) -> float:
        return max(1e-6, abs(self.transform().m11()))

    def _set_scale(self, target_scale: float) -> None:
        current_scale = self._current_scale()
        clamped_scale = max(self._min_scale, float(target_scale))
        if abs(clamped_scale - current_scale) <= 1e-6:
            return
        self.scale(clamped_scale / current_scale, clamped_scale / current_scale)

    def _update_min_scale(self) -> None:
        if not self._scene_rect.isValid() or self._scene_rect.isEmpty():
            self._min_scale = 1.0
            return
        viewport_rect = self.viewport().rect()
        if viewport_rect.width() <= 0 or viewport_rect.height() <= 0:
            self._min_scale = max(1e-6, self._current_scale())
            return
        available_width = max(1.0, float(viewport_rect.width() - 2))
        available_height = max(1.0, float(viewport_rect.height() - 2))
        self._min_scale = max(
            1e-6,
            min(
                available_width / max(1.0, self._scene_rect.width()),
                available_height / max(1.0, self._scene_rect.height()),
            ),
        )


class SpreadsheetHighlightDelegate(QStyledItemDelegate):
    def __init__(self, table: "SpreadsheetSourcePreviewTable") -> None:
        super().__init__(table)
        self.table = table

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        super().paint(painter, option, index)
        highlight = self.table.highlight_range
        if highlight is None:
            return
        top, left, bottom, right = highlight
        row = index.row()
        column = index.column()
        if not (top <= row <= bottom and left <= column <= right):
            return
        painter.save()
        pen = QPen(QColor("#ff2b2b"))
        pen.setWidth(3)
        painter.setPen(pen)
        rect = option.rect.adjusted(1, 1, -1, -1)
        if row == top:
            painter.drawLine(rect.topLeft(), rect.topRight())
        if row == bottom:
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if column == left:
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
        if column == right:
            painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.restore()


class SpreadsheetSourcePreviewTable(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.highlight_range: tuple[int, int, int, int] | None = None
        self._base_font_size = max(9, self.font().pointSize())
        self._base_row_height = 28
        self._base_column_width = 120
        self._zoom_factor = 1.0
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setItemDelegate(SpreadsheetHighlightDelegate(self))

    def clear_preview(self) -> None:
        self.clearContents()
        self.setRowCount(0)
        self.setColumnCount(0)
        self.highlight_range = None

    def set_preview(self, rows: list[list[str]], highlight: dict[str, int]) -> None:
        self.clear_preview()
        row_count = len(rows)
        column_count = max((len(row) for row in rows), default=0)
        self.setRowCount(row_count)
        self.setColumnCount(column_count)
        self.setVerticalHeaderLabels([str(index + 1) for index in range(row_count)])
        self.setHorizontalHeaderLabels([self._column_label(index + 1) for index in range(column_count)])
        for row_index, row in enumerate(rows):
            for column_index in range(column_count):
                value = row[column_index] if column_index < len(row) else ""
                self.setItem(row_index, column_index, QTableWidgetItem(str(value)))
        if highlight:
            top = max(1, int(highlight.get("top", 1))) - 1
            left = max(1, int(highlight.get("left", 1))) - 1
            bottom = max(top, int(highlight.get("bottom", top + 1)) - 1)
            right = max(left, int(highlight.get("right", left + 1)) - 1)
            self.highlight_range = (top, left, bottom, right)
        else:
            self.highlight_range = None
        self._apply_zoom()
        self.focus_highlight()
        self.viewport().update()

    def zoom_in(self) -> None:
        self._zoom_factor = min(4.0, self._zoom_factor * 1.15)
        self._apply_zoom()

    def zoom_out(self) -> None:
        self._zoom_factor = max(0.5, self._zoom_factor / 1.15)
        self._apply_zoom()

    def reset_zoom(self) -> None:
        self._zoom_factor = 1.0
        self._apply_zoom()

    def focus_highlight(self) -> None:
        if self.highlight_range is None:
            return
        top, left, _bottom, _right = self.highlight_range
        item = self.item(top, left)
        if item is not None:
            self.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            self.setCurrentItem(item)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            elif event.angleDelta().y() < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def _apply_zoom(self) -> None:
        font = self.font()
        font.setPointSize(max(8, round(self._base_font_size * self._zoom_factor)))
        self.setFont(font)
        self.horizontalHeader().setFont(font)
        self.verticalHeader().setFont(font)
        target_row_height = max(20, round(self._base_row_height * self._zoom_factor))
        target_column_width = max(72, round(self._base_column_width * self._zoom_factor))
        self.verticalHeader().setDefaultSectionSize(target_row_height)
        for column in range(self.columnCount()):
            self.setColumnWidth(column, target_column_width)
        self.viewport().update()

    def _column_label(self, index: int) -> str:
        label = ""
        value = max(1, index)
        while value:
            value, remainder = divmod(value - 1, 26)
            label = chr(ord("A") + remainder) + label
        return label


class TextHighlightDelegate(QStyledItemDelegate):
    def __init__(self, table: "TextSourcePreviewTable") -> None:
        super().__init__(table)
        self.table = table

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        super().paint(painter, option, index)
        highlight = self.table.highlight_range
        if highlight is None:
            return
        top, bottom = highlight
        row = index.row()
        if not (top <= row <= bottom):
            return
        painter.save()
        pen = QPen(QColor("#ff2b2b"))
        pen.setWidth(3)
        painter.setPen(pen)
        rect = option.rect.adjusted(1, 1, -1, -1)
        if row == top:
            painter.drawLine(rect.topLeft(), rect.topRight())
        if row == bottom:
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.topLeft(), rect.bottomLeft())
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.restore()


class TextSourcePreviewTable(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.highlight_range: tuple[int, int] | None = None
        self._base_font_size = max(9, self.font().pointSize())
        self._base_row_height = 28
        self._base_column_width = 720
        self._zoom_factor = 1.0
        self._line_offset = 0
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setWordWrap(False)
        self.horizontalHeader().setVisible(False)
        self.setItemDelegate(TextHighlightDelegate(self))

    def clear_preview(self) -> None:
        self.clearContents()
        self.setRowCount(0)
        self.setColumnCount(1)
        self.highlight_range = None
        self._line_offset = 0

    def set_preview(
        self,
        lines: list[str],
        *,
        line_offset: int = 0,
        highlight: dict[str, int] | None = None,
    ) -> None:
        self.clear_preview()
        self._line_offset = max(0, int(line_offset))
        self.setColumnCount(1)
        self.setRowCount(len(lines))
        self.setVerticalHeaderLabels([str(self._line_offset + index + 1) for index in range(len(lines))])
        for row_index, line in enumerate(lines):
            self.setItem(row_index, 0, QTableWidgetItem(str(line)))
        if highlight:
            top = max(1, int(highlight.get("top", 1))) - 1
            bottom = max(top, int(highlight.get("bottom", top + 1)) - 1)
            self.highlight_range = (top, bottom)
        else:
            self.highlight_range = None
        self._apply_zoom()
        self.focus_highlight()
        self.viewport().update()

    def zoom_in(self) -> None:
        self._zoom_factor = min(4.0, self._zoom_factor * 1.15)
        self._apply_zoom()

    def zoom_out(self) -> None:
        self._zoom_factor = max(0.5, self._zoom_factor / 1.15)
        self._apply_zoom()

    def reset_zoom(self) -> None:
        self._zoom_factor = 1.0
        self._apply_zoom()

    def focus_highlight(self) -> None:
        if self.highlight_range is None:
            return
        top, _bottom = self.highlight_range
        item = self.item(top, 0)
        if item is not None:
            self.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            self.setCurrentItem(item)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            elif event.angleDelta().y() < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def _apply_zoom(self) -> None:
        font = self.font()
        font.setPointSize(max(8, round(self._base_font_size * self._zoom_factor)))
        self.setFont(font)
        self.verticalHeader().setFont(font)
        target_row_height = max(20, round(self._base_row_height * self._zoom_factor))
        self.verticalHeader().setDefaultSectionSize(target_row_height)
        metrics = self.fontMetrics()
        longest = max((metrics.horizontalAdvance(item.text()) for item in self.findItems("*", Qt.MatchFlag.MatchWildcard)), default=0)
        target_column_width = max(
            360,
            min(2200, max(longest + 48, round(self._base_column_width * self._zoom_factor))),
        )
        self.setColumnWidth(0, target_column_width)
        self.viewport().update()


class ValueSourcePreviewDialog(QDialog):
    def __init__(
        self,
        workspace_root: str,
        language_getter: Callable[[], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace_root = workspace_root
        self.language_getter = language_getter
        self._context: dict[str, object] = {}
        self._preview_cache: dict[int, dict[str, object]] = {}
        self._current_payload: dict[str, object] = {}
        self._current_source_type = ""
        self._task_thread: QThread | None = None
        self._task_worker: TaskWorker | None = None
        self._pending_index: int | None = None
        self._preview_generation = 0

        self.setModal(False)
        self.resize(1120, 820)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(14)

        self.title_label = StrongBodyLabel("")
        self.path_label = BodyLabel("")
        self.path_label.setWordWrap(True)
        self.location_label = BodyLabel("")
        self.location_label.setWordWrap(True)
        root_layout.addWidget(self.title_label)
        root_layout.addWidget(self.path_label)
        root_layout.addWidget(self.location_label)

        toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(10)
        self.evidence_label = BodyLabel("")
        self.evidence_combo = ComboBox(self)
        self.evidence_combo.currentIndexChanged.connect(self._handle_evidence_changed)
        self.zoom_out_button = QPushButton(self)
        self.zoom_out_button.clicked.connect(self._zoom_out)
        self.zoom_in_button = QPushButton(self)
        self.zoom_in_button.clicked.connect(self._zoom_in)
        self.reset_zoom_button = QPushButton(self)
        self.reset_zoom_button.clicked.connect(self._reset_zoom)
        self.fit_button = QPushButton(self)
        self.fit_button.clicked.connect(self._fit_to_highlight)
        toolbar_layout.addWidget(self.evidence_label)
        toolbar_layout.addWidget(self.evidence_combo, 1)
        toolbar_layout.addWidget(self.zoom_out_button)
        toolbar_layout.addWidget(self.zoom_in_button)
        toolbar_layout.addWidget(self.reset_zoom_button)
        toolbar_layout.addWidget(self.fit_button)
        root_layout.addWidget(toolbar)

        self.stack = QStackedWidget(self)
        self.loading_page = QWidget(self)
        loading_layout = QVBoxLayout(self.loading_page)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        loading_layout.setSpacing(12)
        self.loading_ring = IndeterminateProgressRing(self.loading_page)
        self.loading_ring.setFixedSize(30, 30)
        self.loading_label = BodyLabel("")
        self.loading_label.setWordWrap(True)
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.loading_label.setMinimumWidth(360)
        self._stabilize_status_label(self.loading_label)
        loading_layout.addStretch(1)
        loading_layout.addWidget(self.loading_ring, alignment=Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self.loading_label, alignment=Qt.AlignmentFlag.AlignCenter)
        loading_layout.addStretch(1)

        self.pdf_view = PdfSourcePreviewView(self)
        self.sheet_view = SpreadsheetSourcePreviewTable(self)
        self.text_view = TextSourcePreviewTable(self)
        self.message_page = QWidget(self)
        message_layout = QVBoxLayout(self.message_page)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(12)
        self.message_label = BodyLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.message_label.setMinimumWidth(360)
        self._stabilize_status_label(self.message_label)
        message_layout.addStretch(1)
        message_layout.addWidget(self.message_label, alignment=Qt.AlignmentFlag.AlignCenter)
        message_layout.addStretch(1)

        self.stack.addWidget(self.loading_page)
        self.stack.addWidget(self.pdf_view)
        self.stack.addWidget(self.sheet_view)
        self.stack.addWidget(self.text_view)
        self.stack.addWidget(self.message_page)
        root_layout.addWidget(self.stack, 1)

        self.footer_label = BodyLabel("")
        self.footer_label.setWordWrap(True)
        root_layout.addWidget(self.footer_label)
        self.apply_language()

    @property
    def language(self) -> str:
        return normalize_language(self.language_getter())

    def t(self, key: str, **kwargs) -> str:
        return tr(self.language, key, **kwargs)

    def apply_language(self) -> None:
        self.setWindowTitle(self.t("review.preview.title"))
        self.title_label.setText(self.t("review.preview.title"))
        self.evidence_label.setText(self.t("review.preview.evidence"))
        self.zoom_out_button.setText(self.t("review.preview.zoom_out"))
        self.zoom_in_button.setText(self.t("review.preview.zoom_in"))
        self.reset_zoom_button.setText(self.t("review.preview.zoom_reset"))
        self.fit_button.setText(self.t("review.preview.fit"))
        self.loading_label.setText(self.t("review.preview.loading"))
        self._stabilize_status_label(self.loading_label)
        self._stabilize_status_label(self.message_label)
        if self._current_payload:
            self._refresh_labels(self._current_payload)

    def open_preview(
        self,
        *,
        source_path: str,
        record_display_name: str,
        field_name: str,
        target_value: str,
        evidences: list[dict[str, object]],
        initial_index: int = 0,
    ) -> None:
        self._context = {
            "source_path": source_path,
            "record_display_name": record_display_name,
            "field_name": field_name,
            "target_value": target_value,
            "evidences": list(evidences),
        }
        self._preview_generation += 1
        self._preview_cache.clear()
        self._current_payload = {}
        self._current_source_type = ""
        self._pending_index = None
        self._reset_visible_state()
        self._show_loading()
        self.show()
        self.raise_()
        self.activateWindow()
        self._request_preview(initial_index)

    def closeEvent(self, event) -> None:
        self._cancel_worker()
        super().closeEvent(event)

    def _handle_evidence_changed(self, *_args) -> None:
        if not self._current_payload and not self._context:
            return
        requested_index = self.evidence_combo.currentData()
        if requested_index is None:
            requested_index = self.evidence_combo.currentIndex()
        requested_index = max(0, int(requested_index))
        current_index = int(self._current_payload.get("evidence_index", -1)) if self._current_payload else -1
        if requested_index == current_index:
            return
        self._request_preview(requested_index)

    def _request_preview(self, evidence_index: int) -> None:
        if not self._context:
            return
        if evidence_index in self._preview_cache:
            self._apply_preview(self._preview_cache[evidence_index])
            return
        if self._task_thread is not None and self._task_thread.isRunning():
            self._pending_index = evidence_index
            return
        request_generation = self._preview_generation
        payload = {
            "source_path": str(self._context.get("source_path", "")),
            "record_display_name": str(self._context.get("record_display_name", "")),
            "field_name": str(self._context.get("field_name", "")),
            "target_value": str(self._context.get("target_value", "")),
            "evidences": list(self._context.get("evidences", [])),
            "evidence_index": max(0, int(evidence_index)),
        }
        self._show_loading()
        self._set_controls_enabled(False)
        self._task_thread = QThread(self)
        self._task_worker = TaskWorker(self.workspace_root, "load_value_source_preview", payload)
        self._task_worker.moveToThread(self._task_thread)
        success_payload: dict[str, object] | None = None
        error_message: str | None = None

        def handle_success(result: object) -> None:
            nonlocal success_payload
            if isinstance(result, dict):
                success_payload = result

        def handle_error(message: str) -> None:
            nonlocal error_message
            error_message = message

        def cleanup() -> None:
            thread = self._task_thread
            if thread is not None:
                thread.quit()
                thread.wait(2000)
            self._task_thread = None
            self._task_worker = None
            self._consume_preview_result(request_generation, success_payload, error_message)

        self._task_thread.started.connect(self._task_worker.run)
        self._task_worker.succeeded.connect(handle_success)
        self._task_worker.failed.connect(handle_error)
        self._task_worker.finished.connect(cleanup)
        self._task_worker.finished.connect(self._task_worker.deleteLater)
        self._task_thread.finished.connect(self._task_thread.deleteLater)
        self._task_thread.start()

    def _cancel_worker(self) -> None:
        thread = self._task_thread
        if thread is None:
            return
        thread.quit()
        thread.wait(2000)
        self._task_thread = None
        self._task_worker = None

    def _apply_preview(self, payload: dict[str, object]) -> None:
        self._current_payload = payload
        self._current_source_type = str(payload.get("source_type", ""))
        self._refresh_labels(payload)
        self.evidence_combo.blockSignals(True)
        try:
            self.evidence_combo.clear()
            for option in payload.get("evidence_options", []):
                if not isinstance(option, dict):
                    continue
                self.evidence_combo.addItem(str(option.get("label", "")), userData=int(option.get("index", 0)))
            if self.evidence_combo.count() > 0:
                self.evidence_combo.setCurrentIndex(max(0, int(payload.get("evidence_index", 0))))
        finally:
            self.evidence_combo.blockSignals(False)
        self.evidence_combo.setDisabled(int(payload.get("evidence_count", 0)) <= 1)

        if self._current_source_type == "pdf":
            pixmap = QPixmap(str(payload.get("rendered_image_path", "")))
            viewport_hint = payload.get("viewport_hint", {})
            rect_data = viewport_hint.get("rect") if isinstance(viewport_hint, dict) else None
            viewport_rect = None
            if isinstance(rect_data, list) and len(rect_data) == 4:
                viewport_rect = tuple(float(value) for value in rect_data)
            self.pdf_view.set_preview(
                pixmap,
                highlight_kind=str(payload.get("highlight_kind", "none")),
                highlight_geometry=payload.get("highlight_geometry", {}) if isinstance(payload.get("highlight_geometry"), dict) else {},
                viewport_rect=viewport_rect,
            )
            self.stack.setCurrentWidget(self.pdf_view)
        elif self._current_source_type == "spreadsheet":
            rows = payload.get("sheet_rows", [])
            highlight = payload.get("sheet_highlight", {})
            self.sheet_view.set_preview(
                rows if isinstance(rows, list) else [],
                highlight if isinstance(highlight, dict) else {},
            )
            self.stack.setCurrentWidget(self.sheet_view)
        elif self._current_source_type == "text":
            lines = payload.get("text_lines", [])
            highlight = payload.get("text_highlight", {})
            line_offset = int(payload.get("text_line_offset", 0))
            self.text_view.set_preview(
                [str(line) for line in lines] if isinstance(lines, list) else [],
                line_offset=line_offset,
                highlight=highlight if isinstance(highlight, dict) else {},
            )
            self.stack.setCurrentWidget(self.text_view)
        else:
            self._show_message(str(payload.get("message", "")) or self.t("review.preview.unsupported"))

    def _consume_preview_result(
        self,
        request_generation: int,
        success_payload: dict[str, object] | None,
        error_message: str | None,
    ) -> None:
        self._set_controls_enabled(True)
        if success_payload is not None and request_generation == self._preview_generation:
            index = max(0, int(success_payload.get("evidence_index", 0)))
            self._preview_cache[index] = success_payload
            self._apply_preview(success_payload)
        elif error_message and request_generation == self._preview_generation:
            self._show_message(error_message)
        pending_index = self._pending_index
        self._pending_index = None
        if pending_index is not None:
            current_index = int(self._current_payload.get("evidence_index", -1)) if self._current_payload else -1
            if request_generation != self._preview_generation or pending_index != current_index or not self._current_payload:
                self._request_preview(pending_index)

    def _reset_visible_state(self) -> None:
        self.evidence_combo.blockSignals(True)
        try:
            self.evidence_combo.clear()
        finally:
            self.evidence_combo.blockSignals(False)
        self.path_label.setText("")
        self.location_label.setText("")
        self.footer_label.setText("")
        self.pdf_view.clear_preview()
        self.sheet_view.clear_preview()
        self.text_view.clear_preview()
        self.message_label.setText("")

    def _refresh_labels(self, payload: dict[str, object]) -> None:
        path_text = str(payload.get("resolved_source_path", "")) or str(payload.get("display_source_path", ""))
        self.path_label.setText(f"{self.t('review.preview.source')}: {path_text}")
        location_value = str(payload.get("page_label", "")) or str(payload.get("sheet_name", ""))
        locator_text = str(payload.get("locator_text", ""))
        if location_value and locator_text:
            location_text = f"{location_value} / {locator_text}"
        else:
            location_text = location_value or locator_text or self.t("review.preview.no_locator")
        self.location_label.setText(f"{self.t('review.preview.location')}: {location_text}")
        snippet = str(payload.get("highlight_text", "")).strip() or str(payload.get("snippet", "")).strip()
        message = str(payload.get("message", "")).strip()
        footer_parts = [part for part in [message, snippet] if part]
        self.footer_label.setText("\n".join(footer_parts) if footer_parts else "")

    def _show_loading(self) -> None:
        self.loading_label.setText(self.t("review.preview.loading"))
        self._stabilize_status_label(self.loading_label)
        self.stack.setCurrentWidget(self.loading_page)

    def _show_message(self, message: str) -> None:
        self.message_label.setText(message or self.t("review.preview.unsupported"))
        self._stabilize_status_label(self.message_label)
        self.stack.setCurrentWidget(self.message_page)

    def _stabilize_status_label(self, label: BodyLabel) -> None:
        metrics = label.fontMetrics()
        line_height = max(20, metrics.lineSpacing())
        label.setMinimumHeight(line_height * 3 + 12)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.zoom_out_button.setEnabled(enabled)
        self.zoom_in_button.setEnabled(enabled)
        self.reset_zoom_button.setEnabled(enabled)
        self.fit_button.setEnabled(enabled)
        if enabled and self.evidence_combo.count() > 1:
            self.evidence_combo.setEnabled(True)
        else:
            self.evidence_combo.setEnabled(False if not enabled else self.evidence_combo.count() > 1)

    def _zoom_in(self) -> None:
        if self._current_source_type == "pdf":
            self.pdf_view.zoom_in()
        elif self._current_source_type == "spreadsheet":
            self.sheet_view.zoom_in()
        elif self._current_source_type == "text":
            self.text_view.zoom_in()

    def _zoom_out(self) -> None:
        if self._current_source_type == "pdf":
            self.pdf_view.zoom_out()
        elif self._current_source_type == "spreadsheet":
            self.sheet_view.zoom_out()
        elif self._current_source_type == "text":
            self.text_view.zoom_out()

    def _reset_zoom(self) -> None:
        if self._current_source_type == "pdf":
            self.pdf_view.reset_zoom()
        elif self._current_source_type == "spreadsheet":
            self.sheet_view.reset_zoom()
        elif self._current_source_type == "text":
            self.text_view.reset_zoom()

    def _fit_to_highlight(self) -> None:
        if self._current_source_type == "pdf":
            self.pdf_view.fit_to_highlight()
        elif self._current_source_type == "spreadsheet":
            self.sheet_view.focus_highlight()
        elif self._current_source_type == "text":
            self.text_view.focus_highlight()
