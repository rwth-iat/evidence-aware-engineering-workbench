from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import sys
import traceback
from typing import Any

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from iev4pi_transformation_tool.ui.qfluent import (
    BodyLabel,
    CardWidget,
    IndeterminateProgressRing,
    ProgressBar,
    StrongBodyLabel,
)

class TaskWorker(QObject):
    progress_changed = pyqtSignal(int, str)
    log_received = pyqtSignal(object)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, workspace_root: str, task_name: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.workspace_root = workspace_root
        self.task_name = task_name
        self.payload = payload or {}
        self._process: subprocess.Popen[str] | None = None
        self._cancel_requested = False

    def _python_executable(self) -> str:
        workspace = os.path.abspath(self.workspace_root)
        if platform.system() == "Windows":
            candidate = os.path.join(workspace, ".venv", "Scripts", "python.exe")
        else:
            candidate = os.path.join(workspace, ".venv", "bin", "python")
        return candidate if os.path.exists(candidate) else sys.executable

    @pyqtSlot()
    def cancel(self) -> None:
        self._cancel_requested = True
        self._terminate_process()

    def _terminate_process(self, *, force: bool = False) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            if platform.system() == "Windows":
                if force:
                    process.kill()
                else:
                    process.terminate()
                return
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGKILL if force else signal.SIGTERM)
        except Exception:
            try:
                if force:
                    process.kill()
                else:
                    process.terminate()
            except Exception:
                pass

    @pyqtSlot()
    def run(self) -> None:
        from iev4pi_transformation_tool.core.qos_helpers import pcore_worker_count, subprocess_qos_preexec

        child_env = os.environ.copy()
        child_env.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.15")
        child_env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        child_env.setdefault("OMP_NUM_THREADS", str(pcore_worker_count()))
        popen_kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "env": child_env,
        }
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        if platform.system() == "Darwin":
            popen_kwargs["preexec_fn"] = subprocess_qos_preexec
        process = subprocess.Popen(
            [
                self._python_executable(),
                "-m",
                "iev4pi_transformation_tool.services.task_runner",
                self.workspace_root,
                self.task_name,
                json.dumps(self.payload, ensure_ascii=False),
            ],
            **popen_kwargs,
        )
        self._process = process
        if self._cancel_requested and process.poll() is None:
            self._terminate_process()
        result_or_error_emitted = False
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                if self._cancel_requested:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                message = json.loads(line)
                kind = message.get("kind")
                if kind == "progress":
                    self.progress_changed.emit(int(message.get("value", 0)), str(message.get("message", "")))
                    continue
                if kind == "log":
                    self.log_received.emit(message.get("entry", {}))
                    continue
                if kind == "result":
                    self.succeeded.emit(message.get("payload"))
                    result_or_error_emitted = True
                    break
                if kind == "error":
                    self.failed.emit(str(message.get("message", "Unknown background task error")))
                    result_or_error_emitted = True
                    break
        except Exception as exc:  # pragma: no cover - UI safety net
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")
            result_or_error_emitted = True
        finally:
            try:
                stderr_output = process.stderr.read() if process.stderr is not None else ""
            except Exception:
                stderr_output = ""
            try:
                return_code = process.wait(timeout=2 if self._cancel_requested else 5)
            except subprocess.TimeoutExpired:
                self._terminate_process(force=True)
                return_code = process.wait(timeout=5)
            self._process = None
            if self._cancel_requested and not result_or_error_emitted:
                self.cancelled.emit("Task stopped.")
            elif return_code != 0 and stderr_output and not result_or_error_emitted:
                self.failed.emit(stderr_output)
            self.finished.emit()


class InlineProgressCard(CardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inlineTaskCard")
        self.setVisible(False)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_card)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(16)

        self.ring = IndeterminateProgressRing(self)
        self.ring.setFixedSize(22, 22)
        header.addWidget(self.ring, alignment=Qt.AlignmentFlag.AlignVCenter)

        text_box = QWidget(self)
        text_layout = QVBoxLayout(text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)
        self.title_label = StrongBodyLabel("")
        self.message_label = BodyLabel("")
        self.message_label.setWordWrap(True)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.message_label)
        header.addWidget(text_box, 1)

        self.percent_label = StrongBodyLabel("0%")
        header.addWidget(self.percent_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        self.progress_bar = ProgressBar(self)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

    def start(self, title: str, message: str) -> None:
        self._hide_timer.stop()
        self._reset_progress_state()
        self.setVisible(True)
        self.ring.setVisible(True)
        self.update_progress(0, title, message)

    def update_progress(self, value: int, title: str, message: str) -> None:
        current_value = self.progress_bar.value()
        bounded = current_value if int(value) < 0 else max(current_value, max(0, min(100, int(value))))
        self.title_label.setText(title)
        self.message_label.setText(message)
        self.percent_label.setText(f"{bounded}%")
        self.progress_bar.setValue(bounded)

    def complete(self, title: str, message: str) -> None:
        self.update_progress(100, title, message)
        self.ring.setVisible(False)
        self.schedule_hide(250)

    def cancel(self, title: str, message: str) -> None:
        self.title_label.setText(title)
        self.message_label.setText(message)
        self.ring.setVisible(False)
        self.schedule_hide(250)

    def schedule_hide(self, delay_ms: int = 450) -> None:
        self._hide_timer.stop()
        self._hide_timer.start(max(0, int(delay_ms)))

    def hide_now(self) -> None:
        self._hide_timer.stop()
        self._hide_card()

    def _reset_progress_state(self) -> None:
        self.progress_bar.setValue(0)
        self.percent_label.setText("0%")

    def _hide_card(self) -> None:
        self.setVisible(False)
        self._reset_progress_state()
        self.title_label.clear()
        self.message_label.clear()
        parent = self.parentWidget()
        if parent is not None:
            layout = parent.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            parent.updateGeometry()
