"""OCR Ensemble Fusion — merge results from multiple OCR backends.

When ``ocr_pipeline_mode`` is set to ``"ensemble"``, every available OCR
backend is executed in parallel on the same page image.  This module
then fuses the individual ``OCRBackendResult`` objects into a single,
high-confidence merged result using IoU-based spatial clustering and
confidence-weighted voting.
"""

from __future__ import annotations

from dataclasses import field
from typing import Sequence

from iev4pi_transformation_tool.core.ocr_backends import OCRBackendResult, extract_key_value_pairs
from iev4pi_transformation_tool.core.utils import clean_cell, normalize_label
from iev4pi_transformation_tool.models import LayoutBlock, TextBlock

# ---------------------------------------------------------------------------
# IoU helpers
# ---------------------------------------------------------------------------

IOU_THRESHOLD = 0.45


def _iou(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Intersection-over-Union for two axis-aligned bounding boxes."""
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


# ---------------------------------------------------------------------------
# Spatial clustering
# ---------------------------------------------------------------------------


def _cluster_blocks(all_blocks: list[TextBlock]) -> list[list[TextBlock]]:
    """Group blocks from different engines by spatial overlap (IoU ≥ threshold).

    Uses greedy single-linkage: iterate blocks in order; for each unassigned
    block, start a new cluster and pull in every other unassigned block whose
    bbox overlaps with *any* member already in the cluster.
    """
    assigned: set[int] = set()
    clusters: list[list[TextBlock]] = []

    for i, block_i in enumerate(all_blocks):
        if i in assigned:
            continue
        cluster = [block_i]
        assigned.add(i)
        # Expand cluster greedily
        queue = [i]
        while queue:
            ref_idx = queue.pop(0)
            ref_bbox = all_blocks[ref_idx].bbox
            for j, block_j in enumerate(all_blocks):
                if j in assigned:
                    continue
                if _iou(ref_bbox, block_j.bbox) >= IOU_THRESHOLD:
                    cluster.append(block_j)
                    assigned.add(j)
                    queue.append(j)
        clusters.append(cluster)
    return clusters


# ---------------------------------------------------------------------------
# Confidence-weighted voting
# ---------------------------------------------------------------------------


def _vote_cluster(cluster: list[TextBlock]) -> TextBlock:
    """Merge a cluster of spatially overlapping blocks into one ``TextBlock``.

    * **Text** is taken from the block with the highest individual confidence.
    * **Confidence** is boosted via the formula:
      ``ensemble_confidence = 1 - ∏(1 - ci)``
      so agreement from multiple engines significantly boosts the score.
    * **BBox** is taken from the highest-confidence contributor.
    """
    best = max(cluster, key=lambda b: b.confidence)

    # Ensemble confidence: 1 - product(1 - ci)
    product = 1.0
    for block in cluster:
        product *= max(0.0, 1.0 - block.confidence)
    ensemble_confidence = min(1.0, 1.0 - product)

    engines_used = sorted({b.engine for b in cluster})
    engine_label = "+".join(engines_used) if len(engines_used) > 1 else engines_used[0]

    return TextBlock(
        page_number=best.page_number,
        text=best.text,
        bbox=best.bbox,
        source="ocr_text",
        score=ensemble_confidence,
        confidence=ensemble_confidence,
        engine=f"ensemble({engine_label})",
        block_type=best.block_type,
        reading_order=best.reading_order,
        line_id=best.line_id,
        table_id=best.table_id,
        row_id=best.row_id,
        col_id=best.col_id,
    )


# ---------------------------------------------------------------------------
# Layout / table / kv-pair union helpers
# ---------------------------------------------------------------------------


def _merge_all_layout_blocks(results: Sequence[OCRBackendResult]) -> list[LayoutBlock]:
    """Deduplicated union of layout blocks from all backends."""
    merged: list[LayoutBlock] = []
    seen: set[tuple[str, tuple[int, int, int, int], int]] = set()
    for result in results:
        for block in result.layout_blocks:
            bbox_key = tuple(round(v) for v in block.bbox)
            key = (block.block_type.lower(), bbox_key, block.page_number)
            if key in seen:
                continue
            seen.add(key)
            merged.append(block)
    return sorted(
        merged,
        key=lambda b: (b.page_number, b.reading_order or 0, b.bbox[1], b.bbox[0]),
    )


def _merge_all_tables(results: Sequence[OCRBackendResult]) -> list:
    """Union of tables from all backends; keep the richer one on overlap."""
    by_key: dict[tuple[int, tuple[int, int, int, int]], object] = {}
    for result in results:
        for table in result.tables:
            bbox_key = tuple(round(v) for v in table.bbox)
            key = (table.page_number, bbox_key)
            existing = by_key.get(key)
            if existing is None or len(table.cells) > len(existing.cells):
                by_key[key] = table
    return list(by_key.values())


def _merge_all_kv_pairs(results: Sequence[OCRBackendResult]) -> list:
    """Deduplicated union of key-value pairs from all backends."""
    merged = []
    seen: set[tuple[str, str, int]] = set()
    for result in results:
        for pair in result.kv_pairs:
            key = (normalize_label(pair.key), normalize_label(pair.value), pair.page_number)
            if key in seen:
                continue
            seen.add(key)
            merged.append(pair)
    return merged


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def ensemble_results(results: list[OCRBackendResult]) -> OCRBackendResult:
    """Fuse multiple ``OCRBackendResult`` objects into a single merged result.

    Steps:
    1. Collect all text blocks from every backend.
    2. Cluster them spatially using IoU.
    3. Vote within each cluster to produce one merged block.
    4. Union layout blocks, tables, and kv-pairs.

    Returns a new ``OCRBackendResult`` with ``engine="ensemble"``.
    """
    if not results:
        return OCRBackendResult(engine="ensemble")

    # If only one backend produced results, short-circuit
    non_empty = [r for r in results if r.blocks]
    if len(non_empty) <= 1:
        base = non_empty[0] if non_empty else results[0]
        # Still merge layout/tables from all backends
        base.layout_blocks = _merge_all_layout_blocks(results) or base.layout_blocks
        base.tables = _merge_all_tables(results) or base.tables
        base.kv_pairs = _merge_all_kv_pairs(results) or base.kv_pairs
        base.flags.append("ensemble:single_source")
        return base

    # Collect all text blocks
    all_blocks: list[TextBlock] = []
    all_flags: list[str] = []
    for result in results:
        all_blocks.extend(result.blocks)
        all_flags.extend(result.flags)

    # Cluster & vote
    clusters = _cluster_blocks(all_blocks)
    merged_blocks: list[TextBlock] = []
    for idx, cluster in enumerate(clusters):
        voted = _vote_cluster(cluster)
        voted.reading_order = idx
        merged_blocks.append(voted)

    # Sort by reading order
    merged_blocks.sort(
        key=lambda b: (b.page_number, b.reading_order or 0, b.bbox[1], b.bbox[0])
    )

    # Recompute kv-pairs from merged blocks
    page_numbers = {b.page_number for b in merged_blocks}
    ensemble_kv_pairs = []
    for page_number in sorted(page_numbers):
        page_blocks = [b for b in merged_blocks if b.page_number == page_number]
        ensemble_kv_pairs.extend(extract_key_value_pairs(page_blocks, page_number))

    # Also include kv-pairs from individual backends
    all_kv = _merge_all_kv_pairs(results)
    seen_kv: set[tuple[str, str, int]] = set()
    final_kv = []
    for pair in [*ensemble_kv_pairs, *all_kv]:
        key = (normalize_label(pair.key), normalize_label(pair.value), pair.page_number)
        if key not in seen_kv:
            seen_kv.add(key)
            final_kv.append(pair)

    avg_conf = (
        sum(b.confidence for b in merged_blocks) / len(merged_blocks)
        if merged_blocks
        else 0.0
    )

    engines_used = sorted({r.engine for r in results if r.blocks})
    all_flags.append(f"ensemble:fused:{'+'.join(engines_used)}")

    return OCRBackendResult(
        engine="ensemble",
        blocks=merged_blocks,
        layout_blocks=_merge_all_layout_blocks(results),
        tables=_merge_all_tables(results),
        kv_pairs=final_kv,
        average_confidence=avg_conf,
        flags=list(dict.fromkeys(all_flags)),  # deduplicate preserving order
    )
