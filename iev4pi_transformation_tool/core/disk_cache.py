"""Simple disk-persisted dict cache for LLM/VLM/Embedding results.

Usage:
    cache = DiskDict("my_cache_name")
    if key in cache:
        return cache[key]
    result = expensive_api_call(key)
    cache[key] = result
    return result

Data is persisted to .iev4pi/cache/<name>.json on every write.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path


def _cache_dir() -> Path:
    repo = Path(__file__).resolve().parents[2]
    d = repo / ".iev4pi" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


class DiskDict:
    """A dict that persists to a JSON file on disk after every write.

    Thread-safe within the current process.
    Loads from disk on first access; subsequent reads are in-memory.
    """

    _locks: dict[Path, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, name: str) -> None:
        self._path = _cache_dir() / f"{name}.json"
        self._data: dict[str, object] | None = None
        with self._locks_guard:
            self._lock = self._locks.setdefault(self._path, threading.RLock())

    def _load(self) -> dict[str, object]:
        with self._lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> dict[str, object]:
        if self._data is not None:
            return self._data
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}
        return self._data

    def _save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        if self._data is None:
            return
        try:
            snapshot = dict(self._data)
            payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
            tmp_path = self._path.with_name(f"{self._path.name}.tmp")
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(self._path)
        except OSError:
            pass

    def get(self, key: str, default: object = None) -> object:
        with self._lock:
            return self._load_unlocked().get(key, default)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._load_unlocked()

    def __getitem__(self, key: str) -> object:
        with self._lock:
            return self._load_unlocked()[key]

    def __setitem__(self, key: str, value: object) -> None:
        with self._lock:
            self._load_unlocked()[key] = value
            self._save_unlocked()

    def __delitem__(self, key: str) -> None:
        with self._lock:
            d = self._load_unlocked()
            if key in d:
                del d[key]
                self._save_unlocked()

    def clear(self) -> None:
        with self._lock:
            self._data = {}
            self._save_unlocked()

    def __len__(self) -> int:
        with self._lock:
            return len(self._load_unlocked())

    def items(self):
        with self._lock:
            return list(self._load_unlocked().items())

    def keys(self):
        with self._lock:
            return list(self._load_unlocked().keys())

    def values(self):
        with self._lock:
            return list(self._load_unlocked().values())
