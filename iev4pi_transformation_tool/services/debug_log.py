from __future__ import annotations

from collections import OrderedDict, deque
from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any, Iterator

from PyQt6.QtCore import QObject, pyqtSignal


class DebugLogStore(QObject):
    entry_added = pyqtSignal(dict)
    cleared = pyqtSignal()

    def __init__(
        self,
        max_entries: int = 4000,
        *,
        persist_path: Path | None = None,
        cache_size: int = 512,
    ) -> None:
        super().__init__()
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._persist_path = Path(persist_path) if persist_path is not None else None
        self._cache_size = max(32, int(cache_size))
        self._entry_offsets: list[int] = []
        self._entry_cache: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        if self._persist_path is not None:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.touch(exist_ok=True)
            self._load_existing_entries()

    def _normalize_entry(
        self,
        entry: dict[str, Any] | None = None,
        *,
        source: str | None = None,
        action: str | None = None,
        message: str | None = None,
        level: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if entry is None:
            return {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": source or "",
                "action": action or "",
                "level": level.upper(),
                "message": message or "",
                "details": details or {},
            }
        return {
            "timestamp": str(entry.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "source": str(entry.get("source") or ""),
            "action": str(entry.get("action") or ""),
            "level": str(entry.get("level") or "INFO").upper(),
            "message": str(entry.get("message") or ""),
            "details": entry.get("details") if isinstance(entry.get("details"), dict) else {},
        }

    def _load_existing_entries(self) -> None:
        assert self._persist_path is not None
        recent_serialized: deque[bytes] = deque(maxlen=self._entries.maxlen or 0)
        with self._persist_path.open("rb") as handle:
            while True:
                offset = handle.tell()
                raw_line = handle.readline()
                if not raw_line:
                    break
                if not raw_line.strip():
                    continue
                self._entry_offsets.append(offset)
                if recent_serialized.maxlen:
                    recent_serialized.append(raw_line)
        for raw_line in recent_serialized:
            try:
                payload = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            self._entries.append(self._normalize_entry(payload))

    def _cache_entry(self, index: int, entry: dict[str, Any]) -> None:
        self._entry_cache[index] = entry
        self._entry_cache.move_to_end(index)
        while len(self._entry_cache) > self._cache_size:
            self._entry_cache.popitem(last=False)

    def _persist_entry_locked(self, entry: dict[str, Any]) -> int:
        if self._persist_path is None:
            return max(0, self.total_count() - 1)
        # Sanitise: replace newlines in message so each log entry is exactly
        # one line in the JSONL file.  Multi-line messages cause blank rows
        # when the file is re-read.
        msg = str(entry.get("message", ""))
        if "\n" in msg or "\r" in msg:
            entry = {**entry, "message": msg.replace("\r", " ").replace("\n", " | ")}
        payload = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        with self._persist_path.open("ab") as handle:
            offset = handle.tell()
            handle.write(payload)
        self._entry_offsets.append(offset)
        index = len(self._entry_offsets) - 1
        self._cache_entry(index, entry)
        return index

    def add(
        self,
        *,
        source: str,
        action: str,
        message: str,
        level: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = self._normalize_entry(
            source=source,
            action=action,
            message=message,
            level=level,
            details=details,
        )
        with self._lock:
            self._entries.append(entry)
            self._persist_entry_locked(entry)
        self.entry_added.emit(entry)
        return entry

    def append_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_entry(entry)
        with self._lock:
            self._entries.append(normalized)
            self._persist_entry_locked(normalized)
        self.entry_added.emit(normalized)
        return normalized

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries)

    def total_count(self) -> int:
        with self._lock:
            if self._persist_path is None:
                return len(self._entries)
            return len(self._entry_offsets)

    def entry_at(self, index: int) -> dict[str, Any] | None:
        with self._lock:
            if self._persist_path is None:
                if index < 0 or index >= len(self._entries):
                    return None
                return list(self._entries)[index]
            if index < 0 or index >= len(self._entry_offsets):
                return None
            cached = self._entry_cache.get(index)
            if cached is not None:
                self._entry_cache.move_to_end(index)
                return cached
            offset = self._entry_offsets[index]
            assert self._persist_path is not None
            try:
                with self._persist_path.open("rb") as handle:
                    handle.seek(offset)
                    raw_line = handle.readline()
            except OSError:
                return None
            if not raw_line.strip():
                return None
            try:
                payload = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
            normalized = self._normalize_entry(payload)
            self._cache_entry(index, normalized)
            return normalized

    def iter_entries(self) -> Iterator[tuple[int, dict[str, Any]]]:
        if self._persist_path is None:
            with self._lock:
                snapshot = list(self._entries)
            for index, entry in enumerate(snapshot):
                yield index, entry
            return
        assert self._persist_path is not None
        try:
            with self._persist_path.open("rb") as handle:
                for index, raw_line in enumerate(handle):
                    if not raw_line.strip():
                        continue
                    try:
                        payload = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    normalized = self._normalize_entry(payload)
                    with self._lock:
                        if index < len(self._entry_offsets):
                            self._cache_entry(index, normalized)
                    yield index, normalized
        except OSError:
            return

    def iter_recent_entries(self, limit: int) -> Iterator[tuple[int, dict[str, Any]]]:
        """Yield at most *limit* most recent entries from the persistent file."""
        if self._persist_path is None:
            with self._lock:
                snapshot = list(self._entries)
            start = max(0, len(snapshot) - limit)
            for index in range(start, len(snapshot)):
                yield index, snapshot[index]
            return
        with self._lock:
            total = len(self._entry_offsets)
        if total == 0:
            return
        start_index = max(0, total - limit)
        assert self._persist_path is not None
        try:
            with self._persist_path.open("rb") as handle:
                handle.seek(self._entry_offsets[start_index])
                for raw_line in handle:
                    if not raw_line.strip():
                        continue
                    try:
                        payload = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    normalized = self._normalize_entry(payload)
                    yield start_index, normalized
                    start_index += 1
        except OSError:
            return

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._entry_offsets.clear()
            self._entry_cache.clear()
            if self._persist_path is not None:
                self._persist_path.write_bytes(b"")
        self.cleared.emit()
