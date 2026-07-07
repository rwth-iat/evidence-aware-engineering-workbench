"""Semantic ID (IRDI / ECLASS) lookup for AAS Property semanticId injection.

Loads a YAML mapping from ``assets/semantic_ids/uc1_field_to_irdi.yaml`` and
provides a fast lookup function to retrieve the IRDI for any
(submodel_name, property_name) pair.

Usage::

    from iev4pi_transformation_tool.core.semantic_ids import get_irdi

    irdi = get_irdi("SM_CoreIdentity", "canonicalTag")
    # → "0173-1#02-TBD-CANONICAL-TAG#001"
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml


_MAP_CACHE: dict[str, dict[str, str]] | None = None


def _map_path() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "semantic_ids" / "uc1_field_to_irdi.yaml"


@functools.lru_cache(maxsize=1)
def load_semantic_id_map() -> dict[str, dict[str, str]]:
    """Load and cache the IRDI mapping.

    Returns a nested dict: ``{submodel_name: {property_name: irdi, ...}, ...}``.
    """
    global _MAP_CACHE
    if _MAP_CACHE is not None:
        return _MAP_CACHE

    path = _map_path()
    if not path.is_file():
        _MAP_CACHE = {}
        return _MAP_CACHE

    raw: dict[str, dict[str, dict[str, str]]] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    flattened: dict[str, dict[str, str]] = {}
    for submodel_name, properties in raw.items():
        flattened[submodel_name] = {}
        for property_name, info in properties.items():
            if isinstance(info, dict):
                flattened[submodel_name][property_name] = info.get("irdi", "")
            else:
                flattened[submodel_name][property_name] = str(info)
    _MAP_CACHE = flattened
    return _MAP_CACHE


def get_irdi(submodel_name: str, property_name: str) -> str | None:
    """Get the IRDI for a submodel property.

    Args:
        submodel_name: e.g. ``"SM_CoreIdentity"``.
        property_name: e.g. ``"canonicalTag"``.

    Returns:
        IRDI string, or ``None`` if not found.
    """
    mapping = load_semantic_id_map()
    submodel = mapping.get(submodel_name, {})
    irdi = submodel.get(property_name)
    return irdi or None


def reload_semantic_id_map() -> None:
    """Force reload of the IRDI map (useful for testing)."""
    global _MAP_CACHE
    _MAP_CACHE = None
    load_semantic_id_map.cache_clear()
