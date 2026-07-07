"""macOS QoS helpers and CPU-count-based worker scaling.

Threads and subprocesses on Apple Silicon default to QOS_CLASS_DEFAULT (21)
which macOS schedules on efficiency cores. This module provides utilities to
promote worker threads/subprocesses to QOS_CLASS_USER_INITIATED (25) so they
run on performance cores, plus cpu_count-based worker scaling.
"""

from __future__ import annotations

import os
import platform
from concurrent.futures import ThreadPoolExecutor

_DARWIN = platform.system() == "Darwin"


def _set_qos(qos_class: int) -> None:
    import ctypes

    _libc = ctypes.CDLL("libc.dylib")
    _libc.pthread_set_qos_class_self_np(ctypes.c_int(qos_class), ctypes.c_int(0))


def ensure_pcore_qos() -> None:
    """Promote the calling thread to USER_INITIATED QoS (P-cores)."""
    if _DARWIN:
        _set_qos(0x19)  # QOS_CLASS_USER_INITIATED


def subprocess_qos_preexec() -> None:
    """For use as ``preexec_fn`` in :class:`subprocess.Popen`.

    Runs in the child process after fork, before exec — no shared state.
    """
    if _DARWIN:
        _set_qos(0x19)


# ---------------------------------------------------------------------------
# CPU-count-based worker scaling
# ---------------------------------------------------------------------------

def _logical_cores() -> int:
    return max(1, os.cpu_count() or 4)


def pcore_worker_count(*, cap: int = 0) -> int:
    """Worker count for CPU-bound work (OCR, numpy, openpyxl).

    Assumes ~80% of logical cores are P-cores.
    M1 Max (10 cores) → 8.
    """
    cores = _logical_cores()
    p_cores = max(2, round(cores * 0.8))
    return min(p_cores, cap) if cap > 0 else p_cores


def io_worker_count(*, cap: int = 0) -> int:
    """Worker count for I/O-bound work (LLM API calls, file I/O).

    Can use all logical cores since threads spend most time waiting.
    """
    cores = _logical_cores()
    return min(cores, cap) if cap > 0 else cores


# ---------------------------------------------------------------------------
# QoS-aware ThreadPoolExecutor
# ---------------------------------------------------------------------------

class QoSAwareThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that sets USER_INITIATED QoS on each worker task.

    On non-macOS this behaves identically to ThreadPoolExecutor.
    """

    if _DARWIN:

        def submit(self, fn, /, *args, **kwargs):
            def _qos_wrapper(*a, **kw):
                _set_qos(0x19)  # QOS_CLASS_USER_INITIATED
                return fn(*a, **kw)

            return super().submit(_qos_wrapper, *args, **kwargs)
