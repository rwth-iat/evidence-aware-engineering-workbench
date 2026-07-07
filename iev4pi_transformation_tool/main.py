from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path


def _prime_surya_runtime() -> None:
    """Load Surya/Torch before Qt on Windows to avoid DLL init failures."""
    if platform.system() != "Windows":
        return
    if importlib.util.find_spec("surya") is None:
        return
    try:
        import surya.settings  # noqa: F401
    except Exception:
        # Keep startup resilient when Surya is optional or partially installed.
        pass


_prime_surya_runtime()

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from iev4pi_transformation_tool.services.workbench import Workbench
from iev4pi_transformation_tool.ui.qfluent import Theme, setTheme, setThemeColor
from iev4pi_transformation_tool.ui.main_window import MainWindow
from iev4pi_transformation_tool.ui.theme_compat import apply_macos_dark_palette_compat


class _DirectExtract:
    """Test-mode flag: open directly to Extraction Review page."""
    value: bool = False


DIRECT_EXTRACT = _DirectExtract()


def main() -> int:
    app = QApplication(sys.argv)
    setTheme(Theme.DARK)
    setThemeColor(QColor("#30343b"), save=False, lazy=False)
    if platform.system() == "Darwin":
        apply_macos_dark_palette_compat(app)
    workbench = Workbench(Path.cwd())
    workbench.clear_debug_log()
    window = MainWindow(workbench, direct_extract=DIRECT_EXTRACT.value)
    window.show_default()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
