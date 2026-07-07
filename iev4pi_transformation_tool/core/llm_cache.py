"""Disk-persistent cache for LLM/VLM API calls.

All caches are saved to ``.iev4pi/llm_cache.json`` after each pipeline run
and reloaded on the next run.  This eliminates redundant API calls across
pipeline invocations.
"""

from __future__ import annotations

import json
import atexit
from pathlib import Path
from typing import Any

_CACHE_DIR: Path | None = None
_CACHE_FILE: Path | None = None
_registry: dict[str, dict] = {}


def init(cache_dir: str | Path) -> None:
    """Set the cache directory, register all caches, and load from disk."""
    global _CACHE_DIR, _CACHE_FILE
    _CACHE_DIR = Path(cache_dir)
    _CACHE_FILE = _CACHE_DIR / "llm_cache.json"

    # Register all known LLM/VLM caches
    try:
        from iev4pi_transformation_tool.core.standardized_export import _title_block_cache
        register("title_block", _title_block_cache)
        from iev4pi_transformation_tool.core.standardized_export import _projekt_split_cache, _pce_cache, _order_code_clean_cache
        register("projekt_split", _projekt_split_cache)
        register("pce", _pce_cache)
        register("order_code_clean_export", _order_code_clean_cache)
    except ImportError: pass
    try:
        from iev4pi_transformation_tool.core.datasheet_parser import _vlm_datasheet_cache, _llm_datasheet_cache
        register("vlm_datasheet", _vlm_datasheet_cache)
        register("llm_datasheet", _llm_datasheet_cache)
    except ImportError: pass
    try:
        from iev4pi_transformation_tool.core.extractor import Extractor
        register("batch_llm", Extractor._batch_llm_cache)
        register("vlm_connection", Extractor._vlm_connection_cache)
        register("vlm_ordering", Extractor._vlm_ordering_cache)
        register("placeholder", Extractor._placeholder_cache)
        register("name_clean", Extractor._name_clean_cache)
        register("classification", Extractor._classification_cache)
    except ImportError: pass
    try:
        from iev4pi_transformation_tool.core.component_classification import _token_classification_cache, _component_classification_cache
        register("token_classification", _token_classification_cache)
        register("component_classification", _component_classification_cache)
    except ImportError: pass
    try:
        from iev4pi_transformation_tool.core.klemmenplan_source_parser import _column_semantic_cache
        register("column_semantic", _column_semantic_cache)
    except ImportError: pass
    try:
        from iev4pi_transformation_tool.core.standardized_export import _plant_from_filename_cache
        register("plant_from_filename", _plant_from_filename_cache)
    except ImportError: pass
    try:
        from iev4pi_transformation_tool.core.standardized_export import _marketing_desc_cache
        register("marketing_desc", _marketing_desc_cache)
    except ImportError: pass
    try:
        from iev4pi_transformation_tool.core.aio_exporter import _get_cache
        register("aio_exporter", _get_cache())
    except ImportError: pass

    _load()


def register(name: str, cache_dict: dict) -> None:
    """Register an in-memory cache dict for persistence.

    The dict is loaded from disk (if available) and will be saved on exit.
    """
    _registry[name] = cache_dict
    if _CACHE_FILE and _CACHE_FILE.exists():
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            saved = data.get(name, {})
            if isinstance(saved, dict) and saved:
                for k, v in saved.items():
                    if isinstance(cache_dict.get(k), set) and isinstance(v, list):
                        saved[k] = set(v)
                cache_dict.update(saved)
        except Exception:
            pass


def _load() -> None:
    """Reload all registered caches from disk."""
    if not _CACHE_FILE or not _CACHE_FILE.exists():
        return
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        for name, cache_dict in _registry.items():
            saved = data.get(name, {})
            if isinstance(saved, dict) and saved:
                cache_dict.update(saved)
    except Exception:
        pass


def save() -> None:
    """Persist all registered caches to disk.  Called at pipeline end."""
    import sys
    if not _CACHE_FILE:
        print("[llm_cache] No cache file path set — init() not called?", file=sys.stderr)
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        data = {}
        populated = 0
        for name, cache_dict in _registry.items():
            if cache_dict:
                clean = {}
                for k, v in cache_dict.items():
                    if isinstance(v, set):
                        clean[str(k)] = list(v)
                    elif isinstance(v, (str, int, float, bool, list, dict, type(None))):
                        clean[str(k)] = v
                    else:
                        clean[str(k)] = str(v)
                data[name] = clean
                populated += 1
        if data:
            _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[llm_cache] Saved {populated} caches ({sum(len(v) for v in data.values())} entries)", file=sys.stderr)
    except Exception as e:
        print(f"[llm_cache] Save error: {e}", file=sys.stderr)


# Auto-save on normal exit as safety net
atexit.register(save)
