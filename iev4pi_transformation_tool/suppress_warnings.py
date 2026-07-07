"""Suppress known-harmless third-party and OS-level warnings at startup.

Import this module before any third-party library that produces the noise
(requests, PyQt6) to prevent cosmetic warnings on stderr.
"""

import os
import sys
import warnings

# urllib3 version too new for requests' compatibility table; purely cosmetic.
warnings.filterwarnings("ignore", message=r".*urllib3.*doesn't match.*")

# Qt font alias population warning (Segoe UI doesn't exist on macOS).
os.environ.setdefault("QT_LOGGING_RULES",
                      "qt.qpa.fonts.warning=false")

# macOS InputMethodKit mach-port error — logged to fd 2 outside Python's
# control, so we filter stderr via a pipe.
if sys.platform == "darwin":
    _orig_fd2 = os.dup(2)
    _r, _w = os.pipe()
    os.dup2(_w, 2)
    os.close(_w)

    import threading

    def _filter_stderr():
        with os.fdopen(_r, errors="replace") as _src:
            for _line in _src:
                if "IMKCFRunLoopWakeUpReliable" not in _line:
                    os.write(_orig_fd2, _line.encode(errors="replace"))

    threading.Thread(target=_filter_stderr, daemon=True, name="stderr-filter").start()
