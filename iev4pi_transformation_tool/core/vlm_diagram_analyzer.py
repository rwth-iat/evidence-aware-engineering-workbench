"""VLM Diagram Analyzer (Hybrid Version).

This module provides a Hybrid Diagram Analyzer that uses a Vision-Language
Model (VLM) via an OpenAI-compatible endpoint as a fallback for the 
deterministic heuristic DiagramAnalyzer.
"""

from __future__ import annotations

import base64
from concurrent.futures import as_completed

from iev4pi_transformation_tool.core.qos_helpers import QoSAwareThreadPoolExecutor
import hashlib
import io
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

from iev4pi_transformation_tool.core.diagram_analyzer import DiagramAnalyzer, DiagramAnalysisResult
from iev4pi_transformation_tool.models import (
    DiagramEdge,
    LLMBackendConfig,
    SourceDocumentKind,
    TextBlock,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CROP_SIZE = 400
CROP_PADDING = 50

_SYSTEM_PROMPT = """\
You are an expert electrical / P&ID engineering diagram analyst.
You will receive a small cropped region of a circuit diagram (Stromlaufplan) 
or P&ID. This crop centers around specific "unresolved terminals".

Your task is to trace the wires starting from these specific terminals and 
tell me where they connect to in the visible image snippet.

Return ONLY a JSON object with a single `connections` array:
{
  "connections": [
    {
      "from_component": "<source component label, e.g. -A1>",
      "from_terminal": "<source terminal id, e.g. 12>",
      "to_component": "<target component label, e.g. -X1>",
      "to_terminal": "<target terminal id, e.g. 5>",
      "wire_tag": "<wire tag if visible, else empty string>"
    }
  ]
}

Rules:
- Read the text from the image carefully.
- Only return connections that you can actually trace in the cropped image.
- If the wire simply goes off the edge of the crop and connects to nothing visible, do not guess. Return an empty connections array.
- Do not output Markdown or any explanations.
"""

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _encode_image_to_base64(image: np.ndarray, *, use_jpeg: bool = True) -> tuple[str, str]:
    """Convert an OpenCV BGR *np.ndarray* to a base64 data-URI string."""
    if PILImage is None:
        raise ImportError("Pillow is required for VLM diagram analysis.")
    if image.ndim == 3 and image.shape[2] == 3:
        # Crucial .copy() to prevent fatal memory Bus Error when Pillow reads negative strides in C!
        rgb = image[:, :, ::-1].copy()
    else:
        rgb = image.copy()
    pil_img = PILImage.fromarray(rgb)
    buf = io.BytesIO()
    if use_jpeg:
        pil_img.save(buf, format="JPEG", quality=85)
        mime = "image/jpeg"
    else:
        pil_img.save(buf, format="PNG")
        mime = "image/png"
    return base64.b64encode(buf.getvalue()).decode("ascii"), mime


def _call_vlm(
    config: LLMBackendConfig,
    image_b64: str,
    mime_type: str,
    target_prompt: str,
    *,
    cache_path: Path | None = None,
    logger_callback: Callable[..., Any] | None = None,
    trace_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a small cropped image to the VLM and parse the JSON."""
    request_details = {
        **(trace_context or {}),
        "model": config.vlm_model,
        "system_prompt": _SYSTEM_PROMPT,
        "user_prompt": target_prompt,
        "mime_type": mime_type,
        "image_input_omitted": True,
    }
    if cache_path is not None and cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if logger_callback is not None:
                logger_callback(
                    source="vlm",
                    action="cache_hit",
                    message=f"VLM cache hit for {config.vlm_model}",
                    details={
                        **request_details,
                        "output": payload,
                        "cache_path": str(cache_path),
                    },
                )
            return payload
        except Exception:
            logger.warning("Ignoring invalid VLM cache file %s", cache_path)
    disk_cache_key = hashlib.sha256(json.dumps(
        {
            "kind": "diagram_vlm",
            "base_url": config.base_url,
            "model": config.vlm_model,
            "temperature": config.temperature,
            "system_prompt": _SYSTEM_PROMPT,
            "target_prompt": target_prompt,
            "mime_type": mime_type,
            "image_sha256": hashlib.sha256(image_b64.encode("ascii")).hexdigest(),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")).hexdigest()
    try:
        from iev4pi_transformation_tool.core.disk_cache import DiskDict
        disk_cache = DiskDict("vlm_diagram_api")
        cached = disk_cache.get(disk_cache_key)
        if isinstance(cached, dict):
            if logger_callback is not None:
                logger_callback(
                    source="vlm",
                    action="cache_hit",
                    message=f"VLM disk cache hit for {config.vlm_model}",
                    details={
                        **request_details,
                        "output": cached,
                        "cache_key": disk_cache_key,
                    },
                )
            return cached
    except Exception:
        disk_cache = None
    if OpenAI is None:
        logger.warning("openai package not installed – VLM analysis unavailable.")
        return {"connections": []}

    client = OpenAI(
        base_url=config.base_url,
        api_key=os.getenv("IEVPI_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or config.api_key or "not-needed",
        timeout=config.timeout,
        max_retries=config.max_retries,
    )

    user_content: list[dict[str, Any]] = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
        },
        {
            "type": "text",
            "text": target_prompt,
        }
    ]

    max_attempts = max(2, config.max_retries + 1)
    for attempt in range(max_attempts):
        try:
            if logger_callback is not None:
                logger_callback(
                    source="vlm",
                    action="request",
                    message=f"VLM request sent to {config.vlm_model}",
                    details={
                        **request_details,
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                    },
                )
            response = client.chat.completions.create(
                model=config.vlm_model,
                temperature=config.temperature,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            parsed = _parse_json_response(raw)
            if logger_callback is not None:
                logger_callback(
                    source="vlm",
                    action="response",
                    message=f"VLM response received from {config.vlm_model}",
                    details={
                        **request_details,
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "raw_response": raw,
                        "parsed_response": parsed,
                    },
                )
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                if disk_cache is not None:
                    disk_cache[disk_cache_key] = parsed
            except Exception:
                pass
            return parsed
        except Exception as e:
            if logger_callback is not None:
                logger_callback(
                    source="vlm",
                    action="response_error",
                    message=f"VLM request failed: {e}",
                    level="ERROR",
                    details={
                        **request_details,
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                    },
                )
            logger.warning("VLM call failed (attempt %d/%d): %s", attempt + 1, max_attempts, str(e))
            if attempt < max_attempts - 1:
                time.sleep(1.5)
            else:
                logger.exception("VLM call failed after all retries")
                return {"connections": []}

    return {"connections": []}


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Robustly extract JSON from VLM output (may contain markdown fences)."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("VLM returned non-JSON output: %s", raw[:200])
        return {"connections": []}
    if not isinstance(parsed, dict):
        return {"connections": []}
    parsed.setdefault("connections", [])
    return parsed


def _find_terminal_center(
    term_text: str, comp_bbox: tuple[float, float, float, float], blocks: list[TextBlock]
) -> tuple[float, float] | None:
    """Find the approximate location of a terminal text near its component."""
    cx, cy = (comp_bbox[0] + comp_bbox[2]) / 2, (comp_bbox[1] + comp_bbox[3]) / 2
    best_dist = float("inf")
    best_center = None
    for b in blocks:
        if b.text.strip() == term_text:
            bx1, by1, bx2, by2 = b.bbox
            bcx, bcy = (bx1 + bx2) / 2, (by1 + by2) / 2
            # Bounding box expansion test to see if terminal is "near"
            dist = math.hypot(bcx - cx, bcy - cy)
            if dist < best_dist and dist < 250:  # arbitrary proximity
                best_dist = dist
                best_center = (bcx, bcy)
    return best_center


class HybridDiagramAnalyzer:
    """Combines heuristic analysis with localized VLM fallback tracking."""

    def __init__(
        self,
        config: LLMBackendConfig,
        heuristic_analyzer: DiagramAnalyzer,
        cache_dir: Path | None = None,
        logger: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.heuristic_analyzer = heuristic_analyzer
        self.cache_dir = cache_dir
        self._logger = logger
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        return bool(
            self.config.base_url
            and self.config.vlm_model
            and OpenAI is not None
        )

    def analyze(
        self,
        *,
        image: np.ndarray,
        blocks: list[TextBlock],
        page_number: int,
        source_path: str,
        source_kind: SourceDocumentKind,
        vector_segments: list[tuple[float, float, float, float]] | None = None,
        analysis_mode: str = "hybrid",
        on_progress: Callable[[int, str], None] | None = None,
    ) -> DiagramAnalysisResult:
        # 1. Run Baseline Heuristic
        baseline_result = self.heuristic_analyzer.analyze(
            image=image,
            blocks=blocks,
            page_number=page_number,
            source_path=source_path,
            source_kind=source_kind,
            vector_segments=vector_segments,
            analysis_mode=analysis_mode,
        )

        if not self.available() or not baseline_result.graph.nodes:
            return baseline_result

        # 2. Extract unresolved terminals by counting connections per component part
        sp = baseline_result.structured_page
        if not sp:
            return baseline_result

        edge_counts: dict[str, int] = defaultdict(int)
        for trace in sp.traces:
            if trace.from_component_id:
                edge_counts[trace.from_component_id] += 1
            if trace.to_component_id:
                edge_counts[trace.to_component_id] += 1

        unresolved_points: list[tuple[float, float, str, str, str]] = []
        label_to_id: dict[str, str] = {}
        
        for part in sp.parts:
            if part.display_label:
                # Store lowercased mapping to make VLM output matching robust
                label_to_id[part.display_label.lower()] = part.id
            if edge_counts[part.id] < len(part.terminal_labels):
                # This component has fewer edges than terminals, meaning some are unresolved.
                for term in part.terminal_labels:
                    if term:
                        center = _find_terminal_center(term, part.bbox, blocks)
                        if center:
                            unresolved_points.append((center[0], center[1], part.id, part.display_label, term))

        if not unresolved_points:
            return baseline_result

        # 3. Group unresolved terminals by proximity to minimize API calls
        h, w = image.shape[:2]
        clusters: list[list[tuple[float, float, str, str, str]]] = []
        for p in unresolved_points:
            added = False
            for cluster in clusters:
                cx = sum(pt[0] for pt in cluster) / len(cluster)
                cy = sum(pt[1] for pt in cluster) / len(cluster)
                if math.hypot(p[0] - cx, p[1] - cy) < (CROP_SIZE / 2 - CROP_PADDING):
                    cluster.append(p)
                    added = True
                    break
            if not added:
                clusters.append([p])

        logger.info("[VLM Hybrid] Detected %d unresolved terminals in %d regions.", len(unresolved_points), len(clusters))
        if on_progress:
            on_progress(-1, f"VLM Analysis [Page {page_number}]: Processing {len(clusters)} regions...")

        # 4. Crop and Send to VLM
        new_edges = []
        from iev4pi_transformation_tool.core.qos_helpers import io_worker_count

        configured = int(getattr(self.config, "parallel_workers", 0) or 0)
        default_count = io_worker_count(cap=min(4, len(clusters)))
        worker_count = max(1, min(len(clusters), configured)) if configured > 0 else default_count
        if on_progress and worker_count > 1:
            on_progress(-1, f"VLM Analysis [Page {page_number}]: Dispatching {len(clusters)} regions with {worker_count} workers...")

        def process_cluster(i: int, cluster: list[tuple[float, float, str, str, str]]) -> tuple[int, dict[str, Any]]:
            # Calculate crop boundaries
            min_x = min(pt[0] for pt in cluster)
            max_x = max(pt[0] for pt in cluster)
            min_y = min(pt[1] for pt in cluster)
            max_y = max(pt[1] for pt in cluster)

            cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
            half_c = CROP_SIZE // 2
            
            x1 = max(0, int(cx - half_c))
            y1 = max(0, int(cy - half_c))
            x2 = min(w, x1 + CROP_SIZE)
            y2 = min(h, y1 + CROP_SIZE)

            crop = image[y1:y2, x1:x2]
            b64, mime = _encode_image_to_base64(crop, use_jpeg=True)

            target_terms = ", ".join([f"{pt[3]}:{pt[4]}" if pt[3] else pt[4] for pt in cluster])
            prompt = f"Please resolve the missing connection paths for the following terminals: {target_terms}"

            if on_progress:
                on_progress(-1, f"VLM Analysis [Page {page_number}]: Sending crop {i+1}/{len(clusters)} to AI...")

            logger.info("[VLM Hybrid]   Sending crop %d/%d (%dx%d) to %s", i+1, len(clusters), crop.shape[1], crop.shape[0], self.config.vlm_model)
            cache_path = None
            if self.cache_dir is not None:
                cache_key = hashlib.sha1(
                    f"{self.config.vlm_model}\n{page_number}\n{prompt}\n{b64}".encode("utf-8")
                ).hexdigest()
                cache_path = self.cache_dir / f"{cache_key}.json"
            vlm_response = _call_vlm(
                self.config,
                b64,
                mime,
                prompt,
                cache_path=cache_path,
                logger_callback=self._logger,
                trace_context={
                    "workflow": "vlm_diagram_analysis",
                    "source_path": source_path,
                    "page_number": page_number,
                    "crop_index": i + 1,
                    "crop_total": len(clusters),
                    "crop_bounds": [x1, y1, x2, y2],
                    "target_prompt": prompt,
                },
            )
            return i, vlm_response

        cluster_responses: dict[int, dict[str, Any]] = {}
        indexed_clusters = list(enumerate(clusters))
        if worker_count <= 1:
            for index, cluster in indexed_clusters:
                cluster_index, vlm_response = process_cluster(index, cluster)
                cluster_responses[cluster_index] = vlm_response
                if on_progress:
                    on_progress(-1, f"VLM Analysis [Page {page_number}]: Received crop {cluster_index+1}/{len(clusters)} reply")
        else:
            with QoSAwareThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(process_cluster, index, cluster): index
                    for index, cluster in indexed_clusters
                }
                for future in as_completed(futures):
                    cluster_index, vlm_response = future.result()
                    cluster_responses[cluster_index] = vlm_response
                    if on_progress:
                        on_progress(-1, f"VLM Analysis [Page {page_number}]: Received crop {cluster_index+1}/{len(clusters)} reply")

        for i, _cluster in indexed_clusters:
            vlm_response = cluster_responses.get(i, {"connections": []})
            for conn in vlm_response.get("connections", []):
                from_c = str(conn.get("from_component", "")).strip()
                from_t = str(conn.get("from_terminal", "")).strip()
                to_c = str(conn.get("to_component", "")).strip()
                to_t = str(conn.get("to_terminal", "")).strip()
                tag = str(conn.get("wire_tag", "")).strip()

                if from_c and from_t and to_c and to_t:
                    # Resolve labels to internal UUIDs if possible
                    resolved_from_c = label_to_id.get(from_c.lower(), from_c)
                    resolved_to_c = label_to_id.get(to_c.lower(), to_c)
                    
                    new_edges.append(
                        DiagramEdge(
                            id=f"p{page_number}:vlm_edge:{len(baseline_result.graph.edges)+len(new_edges)}",
                            from_node=resolved_from_c,
                            to_node=resolved_to_c,
                            edge_type="wired_to",
                            label=tag,
                            polyline=[], # VLM doesn't know exact path pixels
                            confidence=0.75,
                            evidence_refs=[],
                        )
                    )

        if new_edges:
            logger.info("[VLM Hybrid] Acquired %d new connections from VLM.", len(new_edges))
            baseline_result.graph.edges.extend(new_edges)
            baseline_result.flags.append(f"vlm_resolved_edges:{len(new_edges)}")

        return baseline_result
