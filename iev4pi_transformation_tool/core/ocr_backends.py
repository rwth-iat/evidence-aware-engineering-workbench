from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from iev4pi_transformation_tool.core.utils import clean_cell
from iev4pi_transformation_tool.models import KeyValuePair, LayoutBlock, TableCellData, TableData, TextBlock


@dataclass
class OCRBackendResult:
    engine: str
    blocks: list[TextBlock] = field(default_factory=list)
    layout_blocks: list[LayoutBlock] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)
    kv_pairs: list[KeyValuePair] = field(default_factory=list)
    average_confidence: float = 0.0
    flags: list[str] = field(default_factory=list)
    device: str = "cpu"


class OCRBackend:
    name = "unavailable"

    def __init__(self, device: str = "cpu") -> None:
        self.device = device

    @classmethod
    def is_available(cls) -> bool:
        return False

    def begin_batch(self) -> None:
        return None

    def end_batch(self) -> None:
        return None

    def process_page(self, image: np.ndarray, page_number: int) -> OCRBackendResult:
        return OCRBackendResult(engine=self.name, device=self.device, flags=[f"{self.name}:unavailable"])


def easyocr_bridge_path() -> Path:
    return Path(__file__).resolve().parent.parent / "vendor" / "rustocr" / "easyocr_bridge.py"


class EasyOCRBackend(OCRBackend):
    name = "easyocr"
    bridge_languages = ("de", "en")

    def __init__(self, device: str = "auto") -> None:
        super().__init__(device=self._runtime_device())
        self._worker_process: subprocess.Popen[str] | None = None
        self._worker_lock = threading.Lock()

    @classmethod
    def is_available(cls) -> bool:
        details = cls.availability_details()
        return bool(details["bridge_exists"] and details["module_found"])

    @classmethod
    def availability_details(cls) -> dict[str, object]:
        bridge = easyocr_bridge_path()
        module_spec = importlib.util.find_spec("easyocr")
        return {
            "python_executable": sys.executable,
            "bridge_path": str(bridge),
            "bridge_exists": bridge.exists(),
            "module_found": module_spec is not None,
            "module_origin": getattr(module_spec, "origin", None) if module_spec is not None else None,
        }

    @classmethod
    def _runtime_device(cls) -> str:
        if importlib.util.find_spec("torch") is None:
            return "cpu"
        try:
            import torch

            if torch.backends.mps.is_available():
                return "mps"
            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            return "cpu"
        return "cpu"

    def runtime_device(self) -> str:
        self.device = self._runtime_device()
        return self.device

    def begin_batch(self) -> None:
        self._ensure_worker()

    def end_batch(self) -> None:
        self._shutdown_worker()

    def process_page(self, image: np.ndarray, page_number: int) -> OCRBackendResult:
        device = self.runtime_device()
        result = OCRBackendResult(engine=self.name, device=device)
        bridge = easyocr_bridge_path()
        if not bridge.exists():
            result.flags.append("easyocr:bridge_missing")
            return result
        if importlib.util.find_spec("easyocr") is None:
            result.flags.append("easyocr:module_missing")
            return result

        page_image = _numpy_to_pil_image(image)
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
                temp_path = handle.name
            page_image.save(temp_path)
            completed = self._invoke_bridge(temp_path)
        except Exception as exc:  # pragma: no cover - third-party runtime guard
            result.flags.append(f"easyocr:bridge_error:{exc.__class__.__name__}")
            return result
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        if completed.returncode != 0:
            stderr_text = (completed.stderr or "").strip()
            if stderr_text:
                result.flags.append("easyocr:bridge_failed")
            else:
                result.flags.append("easyocr:bridge_failed:unknown")
            return result

        try:
            payload = completed.stdout.strip() or "[]"
            raw_result = json.loads(payload)
        except Exception as exc:
            result.flags.append(f"easyocr:json_error:{exc.__class__.__name__}")
            return result
        if isinstance(raw_result, dict) and raw_result.get("error"):
            result.flags.append("easyocr:bridge_failed")
            return result

        for index, entry in enumerate(raw_result or []):
            bbox = entry.get("bbox")
            text = entry.get("text")
            score = entry.get("confidence")
            block = _quad_text_block(
                bbox,
                text,
                score,
                page_number,
                source="ocr_text",
                engine=self.name,
            )
            if block is None:
                continue
            block.reading_order = index
            result.blocks.append(block)
            result.layout_blocks.append(
                LayoutBlock(
                    page_number=page_number,
                    block_type="text",
                    text=block.text,
                    bbox=block.bbox,
                    confidence=block.confidence,
                    reading_order=index,
                    engine=self.name,
                )
            )

        result.kv_pairs = extract_key_value_pairs(result.blocks, page_number)
        result.average_confidence = _average_confidence(result.blocks)
        result.flags.append(f"easyocr:device:{device}")
        return result

    def _bridge_command(self, *, serve: bool) -> list[str]:
        command = [
            sys.executable,
            str(easyocr_bridge_path()),
            "--languages",
            ",".join(self.bridge_languages),
            "--gpu",
            "true",
            "--detail",
            "1",
        ]
        if serve:
            command.append("--serve")
        return command

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker_process is not None and self._worker_process.poll() is None:
                return
            process = subprocess.Popen(
                self._bridge_command(serve=True),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            ready_line = process.stdout.readline() if process.stdout is not None else ""
            if process.poll() is not None:
                stderr_output = process.stderr.read() if process.stderr is not None else ""
                raise RuntimeError(f"easyocr worker failed to start: {stderr_output.strip()}")
            try:
                ready_payload = json.loads(ready_line.strip() or "{}")
            except json.JSONDecodeError as exc:
                self._terminate_process(process)
                raise RuntimeError("easyocr worker returned invalid ready payload") from exc
            if ready_payload.get("status") != "ready":
                self._terminate_process(process)
                raise RuntimeError(f"easyocr worker not ready: {ready_payload}")
            self._worker_process = process

    def _shutdown_worker(self) -> None:
        with self._worker_lock:
            process = self._worker_process
            self._worker_process = None
            if process is None:
                return
            try:
                if process.poll() is None and process.stdin is not None:
                    process.stdin.write(json.dumps({"command": "quit"}) + "\n")
                    process.stdin.flush()
                    if process.stdout is not None:
                        process.stdout.readline()
                process.wait(timeout=2)
            except Exception:
                self._terminate_process(process)

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _invoke_bridge(self, image_path: str) -> subprocess.CompletedProcess[str]:
        process = self._worker_process
        if process is not None and process.poll() is None:
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("easyocr worker pipes unavailable")
            request = {
                "command": "ocr",
                "image": image_path,
                "detail": 1,
            }
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            stdout_line = process.stdout.readline()
            return subprocess.CompletedProcess(
                self._bridge_command(serve=True),
                0,
                stdout=stdout_line,
                stderr="",
            )
        return subprocess.run(
            [
                *self._bridge_command(serve=False),
                "--image",
                image_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )


class RapidOCRBackend(OCRBackend):
    name = "rapidocr"

    def __init__(self, device: str = "cpu") -> None:
        super().__init__(device="cpu")
        self._engine: Any | None = None

    @classmethod
    def is_available(cls) -> bool:
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None

    def process_page(self, image: np.ndarray, page_number: int) -> OCRBackendResult:
        result = OCRBackendResult(engine=self.name, device="cpu")
        if not self.is_available():
            result.flags.append("rapidocr:module_missing")
            return result
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR()
            except Exception as exc:  # pragma: no cover - third-party runtime guard
                result.flags.append(f"rapidocr:init_error:{exc.__class__.__name__}")
                return result
        try:
            raw_result, _ = self._engine(image)
        except Exception as exc:  # pragma: no cover - third-party runtime guard
            result.flags.append(f"rapidocr:error:{exc.__class__.__name__}")
            return result
        for index, entry in enumerate(raw_result or []):
            if len(entry) < 3:
                continue
            quad, text, score = entry[:3]
            block = _quad_text_block(quad, text, score, page_number, source="ocr_text", engine=self.name)
            if block is None:
                continue
            block.reading_order = index
            result.blocks.append(block)
            result.layout_blocks.append(
                LayoutBlock(
                    page_number=page_number,
                    block_type="text",
                    text=block.text,
                    bbox=block.bbox,
                    confidence=block.confidence,
                    reading_order=index,
                    engine=self.name,
                )
            )
        result.kv_pairs = extract_key_value_pairs(result.blocks, page_number)
        result.average_confidence = _average_confidence(result.blocks)
        return result


class AppleOCRBackend(OCRBackend):
    name = "apple"

    def __init__(
        self,
        device: str = "cpu",
        *,
        framework: str = "vision",
        recognition_level: str = "accurate",
    ) -> None:
        super().__init__(device="apple-vision")
        self.framework = framework if framework in {"vision", "livetext"} else "vision"
        self.recognition_level = recognition_level if recognition_level in {"fast", "accurate"} else "accurate"

    @classmethod
    def is_available(cls) -> bool:
        return platform.system() == "Darwin" and importlib.util.find_spec("ocrmac") is not None

    def process_page(self, image: np.ndarray, page_number: int) -> OCRBackendResult:
        result = OCRBackendResult(engine=self.name, device=self.device)
        if not self.is_available():
            result.flags.append("apple:module_missing")
            return result
        try:
            from ocrmac.ocrmac import OCR

            page_image = _numpy_to_pil_image(image)
            ocr_engine = OCR(
                page_image,
                framework=self.framework,
                recognition_level=self.recognition_level,
                confidence_threshold=0.0,
                detail=True,
                unit="line",
            )
            raw_result = ocr_engine.recognize(px=True)
        except Exception as exc:  # pragma: no cover - third-party runtime guard
            result.flags.append(f"apple:ocr_error:{exc.__class__.__name__}")
            return result

        for index, entry in enumerate(raw_result or []):
            if len(entry) < 3:
                continue
            text, score, bbox = entry[:3]
            cleaned = clean_cell(text)
            coerced_bbox = _coerce_bbox(bbox)
            if not cleaned or coerced_bbox is None:
                continue
            confidence = float(score or 0.0)
            block = TextBlock(
                page_number=page_number,
                text=cleaned,
                bbox=coerced_bbox,
                source="ocr_text",
                score=confidence,
                confidence=confidence,
                engine=self.name,
                block_type="text",
                reading_order=index,
                line_id=f"{self.name}:p{page_number}:line{index}",
            )
            result.blocks.append(block)
            result.layout_blocks.append(
                LayoutBlock(
                    page_number=page_number,
                    block_type="text",
                    text=block.text,
                    bbox=block.bbox,
                    confidence=block.confidence,
                    reading_order=index,
                    engine=self.name,
                )
            )
        result.kv_pairs = extract_key_value_pairs(result.blocks, page_number)
        result.average_confidence = _average_confidence(result.blocks)
        if self.framework == "vision":
            result.flags.append(f"apple:vision_{self.recognition_level}")
        else:
            result.flags.append("apple:livetext_line")
        return result


class PaddleOCRBackend(OCRBackend):
    name = "paddle"

    def __init__(self, device: str = "cpu") -> None:
        super().__init__(device=device)
        self._ocr_engine: Any | None = None
        self._structure_engine: Any | None = None
        self._structure_checked = False

    @classmethod
    def is_available(cls) -> bool:
        return importlib.util.find_spec("paddleocr") is not None

    def _get_ocr_engine(self) -> Any:
        if self._ocr_engine is None:
            from paddleocr import PaddleOCR

            kwargs = {
                "use_angle_cls": False,
                "lang": "en",
                "show_log": False,
            }
            normalized_device = self._paddle_device()
            if normalized_device.startswith("gpu"):
                kwargs["use_gpu"] = True
            self._ocr_engine = PaddleOCR(**kwargs)
        return self._ocr_engine

    def _get_structure_engine(self) -> Any | None:
        if self._structure_checked:
            return self._structure_engine
        self._structure_checked = True
        try:
            from paddleocr import PPStructureV3  # type: ignore[attr-defined]

            kwargs = {"show_log": False, "device": self._paddle_device()}
            self._structure_engine = PPStructureV3(**kwargs)
            return self._structure_engine
        except Exception:
            try:
                from paddleocr import PPStructure  # type: ignore[attr-defined]

                kwargs = {"show_log": False}
                if self._paddle_device().startswith("gpu"):
                    kwargs["use_gpu"] = True
                self._structure_engine = PPStructure(**kwargs)
                return self._structure_engine
            except Exception:
                self._structure_engine = None
                return None

    def _paddle_device(self) -> str:
        lowered = self.device.strip().lower()
        if lowered.startswith("gpu"):
            return lowered
        if lowered.startswith("cuda"):
            return "gpu" + lowered[4:]
        return "cpu"

    def process_page(self, image: np.ndarray, page_number: int) -> OCRBackendResult:
        result = OCRBackendResult(engine=self.name, device=self.device)
        if not self.is_available():
            result.flags.append("paddle:module_missing")
            return result
        try:
            ocr_engine = self._get_ocr_engine()
            ocr_output = ocr_engine.ocr(image, cls=False)
        except Exception as exc:  # pragma: no cover - third-party runtime guard
            result.flags.append(f"paddle:ocr_error:{exc.__class__.__name__}")
            return result

        lines = _flatten_paddle_ocr_lines(ocr_output)
        for index, line in enumerate(lines):
            block = _quad_text_block(
                line["quad"],
                line["text"],
                line["score"],
                page_number,
                source="ocr_text",
                engine=self.name,
            )
            if block is None:
                continue
            block.reading_order = index
            result.blocks.append(block)
            result.layout_blocks.append(
                LayoutBlock(
                    page_number=page_number,
                    block_type="text",
                    text=block.text,
                    bbox=block.bbox,
                    confidence=block.confidence,
                    reading_order=index,
                    engine=self.name,
                )
            )

        structure_engine = self._get_structure_engine()
        if structure_engine is not None:
            try:
                structure_output = structure_engine(image)
            except Exception as exc:  # pragma: no cover - third-party runtime guard
                result.flags.append(f"paddle:structure_error:{exc.__class__.__name__}")
            else:
                layout_blocks, tables = _parse_paddle_structure_output(structure_output, page_number, self.name)
                if layout_blocks:
                    result.layout_blocks = layout_blocks
                if tables:
                    result.tables = tables

        result.kv_pairs = extract_key_value_pairs(result.blocks, page_number)
        result.average_confidence = _average_confidence(result.blocks)
        if result.tables:
            result.flags.append(f"paddle:tables:{len(result.tables)}")
        return result


class SuryaBackend(OCRBackend):
    name = "surya"

    def __init__(self, device: str = "cpu") -> None:
        super().__init__(device=device)
        self._runtime_ready = False
        self._detection_predictor: Any | None = None
        self._recognition_predictor: Any | None = None
        self._layout_predictor: Any | None = None
        self._table_rec_predictor: Any | None = None

    @classmethod
    def is_available(cls) -> bool:
        return importlib.util.find_spec("surya") is not None

    def _surya_device(self) -> str:
        lowered = self.device.strip().lower()
        if lowered.startswith("cuda") or lowered.startswith("gpu"):
            return "cuda"
        if lowered.startswith("mps"):
            return "mps"
        if lowered.startswith("xla"):
            return "xla"
        return "cpu"

    def _prepare_runtime(self) -> str:
        normalized_device = self._surya_device()
        if self._runtime_ready:
            return normalized_device
        model_cache_dir = Path.cwd() / ".iev4pi" / "cache" / "surya_models"
        model_cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["TORCH_DEVICE"] = normalized_device
        os.environ["MODEL_CACHE_DIR"] = str(model_cache_dir)
        from surya.settings import settings

        settings.TORCH_DEVICE = normalized_device
        settings.DISABLE_TQDM = True
        settings.MODEL_CACHE_DIR = str(model_cache_dir)
        self._runtime_ready = True
        return normalized_device

    def _get_text_predictors(self) -> tuple[Any, Any]:
        if self._detection_predictor is not None and self._recognition_predictor is not None:
            return self._detection_predictor, self._recognition_predictor
        normalized_device = self._prepare_runtime()
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor

        foundation_predictor = FoundationPredictor(device=normalized_device)
        foundation_predictor.disable_tqdm = True
        detection_predictor = DetectionPredictor(device=normalized_device)
        detection_predictor.disable_tqdm = True
        recognition_predictor = RecognitionPredictor(foundation_predictor)
        recognition_predictor.disable_tqdm = True

        self._detection_predictor = detection_predictor
        self._recognition_predictor = recognition_predictor
        return detection_predictor, recognition_predictor

    def _get_layout_predictor(self) -> Any:
        if self._layout_predictor is not None:
            return self._layout_predictor
        normalized_device = self._prepare_runtime()
        from surya.foundation import FoundationPredictor
        from surya.layout import LayoutPredictor
        from surya.settings import settings

        foundation_predictor = FoundationPredictor(
            checkpoint=settings.LAYOUT_MODEL_CHECKPOINT,
            device=normalized_device,
        )
        foundation_predictor.disable_tqdm = True
        layout_predictor = LayoutPredictor(foundation_predictor)
        layout_predictor.disable_tqdm = True
        self._layout_predictor = layout_predictor
        return layout_predictor

    def _get_table_rec_predictor(self) -> Any:
        if self._table_rec_predictor is not None:
            return self._table_rec_predictor
        normalized_device = self._prepare_runtime()
        from surya.settings import settings
        from surya.table_rec import TableRecPredictor

        settings.TORCH_DEVICE = normalized_device
        settings.DISABLE_TQDM = True
        table_rec_predictor = TableRecPredictor()
        table_rec_predictor.disable_tqdm = True
        self._table_rec_predictor = table_rec_predictor
        return table_rec_predictor

    def process_page(self, image: np.ndarray, page_number: int) -> OCRBackendResult:
        result = OCRBackendResult(engine=self.name, device=self.device)
        page_image = _numpy_to_pil_image(image)
        try:
            detection_predictor, recognition_predictor = self._get_text_predictors()
        except ModuleNotFoundError:
            result.flags.append("surya:module_missing")
            return result
        except Exception as exc:  # pragma: no cover - third-party runtime guard
            result.flags.append(f"surya:init_error:{exc.__class__.__name__}")
            return result

        try:
            predictions = recognition_predictor(
                [page_image],
                det_predictor=detection_predictor,
                highres_images=[page_image],
                sort_lines=True,
                math_mode=False,
            )
        except Exception as exc:  # pragma: no cover - third-party runtime guard
            result.flags.append(f"surya:ocr_error:{exc.__class__.__name__}")
            return result

        if predictions:
            result.blocks = _parse_surya_ocr_result(predictions[0], page_number, self.name)
            result.kv_pairs = extract_key_value_pairs(result.blocks, page_number)
            result.average_confidence = _average_confidence(result.blocks)

        try:
            layout_predictor = self._get_layout_predictor()
            layout_predictions = layout_predictor([page_image])
        except Exception as exc:  # pragma: no cover - third-party runtime guard
            result.flags.append(f"surya:layout_error:{exc.__class__.__name__}")
        else:
            if layout_predictions:
                result.layout_blocks = _parse_surya_layout_result(
                    layout_predictions[0],
                    page_number,
                    self.name,
                )
        if not result.layout_blocks:
            result.layout_blocks = [
                LayoutBlock(
                    page_number=block.page_number,
                    block_type="text",
                    text=block.text,
                    bbox=block.bbox,
                    confidence=block.confidence,
                    reading_order=block.reading_order,
                    engine=self.name,
                )
                for block in result.blocks
            ]
        table_regions = [block for block in result.layout_blocks if "table" in block.block_type.lower()]
        if table_regions:
            result.flags.append("surya:table_regions_detected")
            try:
                table_predictor = self._get_table_rec_predictor()
                result.tables = _recognize_surya_tables(
                    page_image,
                    table_regions,
                    table_predictor,
                    result.blocks,
                    page_number,
                    self.name,
                )
            except Exception as exc:  # pragma: no cover - third-party runtime guard
                result.flags.append(f"surya:table_rec_error:{exc.__class__.__name__}")
            else:
                if result.tables:
                    result.flags.append(f"surya:tables:{len(result.tables)}")
        return result


def extract_key_value_pairs(blocks: list[TextBlock], page_number: int) -> list[KeyValuePair]:
    pairs: list[KeyValuePair] = []
    pattern = re.compile(r"^\s*([^:]{2,80})\s*:\s*(.+?)\s*$")
    key_only_pattern = re.compile(r"^\s*([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\s./()-]{1,40})\s*:\s*$")
    seen: set[tuple[str, str]] = set()

    # Pass 1: multi-line title-block style (key-only line followed by value line)
    _KEY_LABELS = {
        "projekt", "projektnr", "project number", "projekt-nr",
        "kunde", "customer", "auftrag", "order",
        "position", "plant", "anlage", "facility",
        "dokument", "document", "erstellt", "created",
        "bearb", "edited", "gepr", "geprüft", "reviewed",
        "norm", "standard", "software", "datum", "date",
        "name", "rev", "revision",
    }
    for i, block in enumerate(blocks):
        km = key_only_pattern.match(block.text)
        if not km:
            continue
        key_candidate = clean_cell(km.group(1)).lower().rstrip(".")
        if key_candidate not in _KEY_LABELS:
            continue
        # Look ahead up to 3 blocks for a non-key value
        for j in range(i + 1, min(i + 4, len(blocks))):
            val_text = blocks[j].text.strip()
            if not val_text or key_only_pattern.match(val_text):
                continue
            val = clean_cell(val_text)
            if not val:
                continue
            dedupe_key = (key_candidate, val.lower())
            if dedupe_key in seen:
                break
            seen.add(dedupe_key)
            pairs.append(
                KeyValuePair(
                    page_number=page_number,
                    key=key_candidate,
                    value=val,
                    key_bbox=block.bbox,
                    value_bbox=blocks[j].bbox,
                    confidence=blocks[j].confidence,
                    source=blocks[j].source,
                    engine=blocks[j].engine,
                )
            )
            break  # Only take the first non-key block after the key

    # Pass 2: standard single-line Key: Value pattern
    for block in blocks:
        match = pattern.match(block.text)
        if not match:
            continue
        key = clean_cell(match.group(1))
        value = clean_cell(match.group(2))
        if not key or not value:
            continue
        dedupe_key = (key.lower(), value.lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        pairs.append(
            KeyValuePair(
                page_number=page_number,
                key=key,
                value=value,
                key_bbox=block.bbox,
                value_bbox=block.bbox,
                confidence=block.confidence,
                source=block.source,
                engine=block.engine,
            )
        )
    return pairs


def _quad_text_block(
    quad: Any,
    text: Any,
    score: Any,
    page_number: int,
    *,
    source: str,
    engine: str,
) -> TextBlock | None:
    cleaned = clean_cell(text)
    if not cleaned:
        return None
    try:
        xs = [float(point[0]) for point in quad]
        ys = [float(point[1]) for point in quad]
    except Exception:
        return None
    confidence = float(score or 0.0)
    return TextBlock(
        page_number=page_number,
        text=cleaned,
        bbox=(float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))),
        source=source,
        score=confidence,
        confidence=confidence,
        engine=engine,
        block_type="text",
    )


def _average_confidence(blocks: list[TextBlock]) -> float:
    if not blocks:
        return 0.0
    return sum(block.confidence for block in blocks) / len(blocks)


def _flatten_paddle_ocr_lines(ocr_output: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    if not isinstance(ocr_output, list):
        return lines
    stack = list(ocr_output)
    while stack:
        item = stack.pop(0)
        if not item:
            continue
        if isinstance(item, list) and len(item) == 2 and isinstance(item[1], (list, tuple)) and len(item[1]) >= 2:
            lines.append({"quad": item[0], "text": item[1][0], "score": item[1][1]})
            continue
        if isinstance(item, list):
            stack = list(item) + stack
    return lines


def _parse_paddle_structure_output(
    structure_output: Any,
    page_number: int,
    engine: str,
) -> tuple[list[LayoutBlock], list[TableData]]:
    layout_blocks: list[LayoutBlock] = []
    tables: list[TableData] = []
    if not isinstance(structure_output, list):
        return layout_blocks, tables
    table_index = 0
    for index, item in enumerate(structure_output):
        if not isinstance(item, dict):
            continue
        block_type = str(item.get("type") or item.get("label") or "text")
        bbox = _coerce_bbox(item.get("bbox"))
        text = clean_cell(item.get("text") or item.get("res") or "")
        if bbox is not None:
            layout_blocks.append(
                LayoutBlock(
                    page_number=page_number,
                    block_type=block_type,
                    text=text,
                    bbox=bbox,
                    confidence=float(item.get("score") or 1.0),
                    reading_order=index,
                    engine=engine,
                )
            )
        if "table" not in block_type.lower():
            continue
        table_id = f"p{page_number}_t{table_index}"
        table_index += 1
        table = TableData(
            table_id=table_id,
            page_number=page_number,
            bbox=bbox or (0.0, 0.0, 0.0, 0.0),
            engine=engine,
        )
        html = str(item.get("res") or item.get("html") or "")
        rows = _parse_html_table_rows(html)
        for row_id, row in enumerate(rows, start=1):
            for col_id, cell_text in enumerate(row, start=1):
                table.cells.append(
                    TableCellData(
                        table_id=table_id,
                        page_number=page_number,
                        row_id=row_id,
                        col_id=col_id,
                        text=cell_text,
                        bbox=table.bbox,
                        confidence=float(item.get("score") or 1.0),
                        engine=engine,
                        is_header=row_id == 1,
                    )
                )
        tables.append(table)
    return layout_blocks, tables


def _parse_html_table_rows(html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    if not html:
        return rows
    row_matches = re.findall(r"<tr.*?>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)
    for row_html in row_matches:
        cells = re.findall(r"<t[dh].*?>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
        cleaned = [clean_cell(re.sub(r"<[^>]+>", " ", cell)) for cell in cells]
        if cleaned:
            rows.append(cleaned)
    return rows


def _coerce_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except Exception:
        return None


def _numpy_to_pil_image(image: np.ndarray) -> Image.Image:
    array = np.asarray(image)
    if array.ndim == 2:
        array = np.stack([array] * 3, axis=-1)
    if array.ndim == 3 and array.shape[2] == 1:
        array = np.repeat(array, 3, axis=2)
    if array.ndim == 3 and array.shape[2] > 3:
        array = array[:, :, :3]
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    return Image.fromarray(array).convert("RGB")


def _parse_surya_ocr_result(ocr_result: Any, page_number: int, engine: str) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    text_lines = getattr(ocr_result, "text_lines", []) or []
    for index, line in enumerate(text_lines):
        text = clean_cell(getattr(line, "text", ""))
        bbox = _polygon_to_bbox(getattr(line, "polygon", None))
        if not text or bbox is None:
            continue
        confidence = float(getattr(line, "confidence", 0.0) or 0.0)
        blocks.append(
            TextBlock(
                page_number=page_number,
                text=text,
                bbox=bbox,
                source="ocr_text",
                score=confidence,
                confidence=confidence,
                engine=engine,
                block_type="text",
                reading_order=index,
                line_id=f"{engine}:p{page_number}:line{index}",
            )
        )
    return blocks


def _parse_surya_layout_result(layout_result: Any, page_number: int, engine: str) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
    layout_boxes = getattr(layout_result, "bboxes", []) or []
    for index, box in enumerate(layout_boxes):
        label = clean_cell(getattr(box, "label", "")) or "layout"
        bbox = _polygon_to_bbox(getattr(box, "polygon", None))
        if bbox is None:
            continue
        confidence = float(getattr(box, "confidence", 0.0) or 0.0)
        reading_order = getattr(box, "position", index)
        blocks.append(
            LayoutBlock(
                page_number=page_number,
                block_type=label,
                text="",
                bbox=bbox,
                confidence=confidence,
                reading_order=int(reading_order),
                engine=engine,
            )
        )
    return blocks


def _recognize_surya_tables(
    page_image: Image.Image,
    table_regions: list[LayoutBlock],
    predictor: Any,
    ocr_blocks: list[TextBlock],
    page_number: int,
    engine: str,
) -> list[TableData]:
    width, height = page_image.size
    cropped_images: list[Image.Image] = []
    crop_regions: list[tuple[int, tuple[int, int, int, int]]] = []
    for region_index, region in enumerate(table_regions):
        crop_bbox = _expand_and_clip_bbox(region.bbox, width, height)
        if crop_bbox is None:
            continue
        left, top, right, bottom = crop_bbox
        if right - left < 4 or bottom - top < 4:
            continue
        cropped_images.append(page_image.crop(crop_bbox))
        crop_regions.append((region_index, crop_bbox))
    if not cropped_images:
        return []
    predictions = predictor(cropped_images)
    tables: list[TableData] = []
    for prediction, (region_index, crop_bbox) in zip(predictions, crop_regions):
        table = _parse_surya_table_result(
            prediction,
            page_number=page_number,
            table_id=f"p{page_number}_t{region_index}",
            crop_bbox=crop_bbox,
            ocr_blocks=ocr_blocks,
            engine=engine,
        )
        if table is not None:
            tables.append(table)
    return tables


def _parse_surya_table_result(
    table_result: Any,
    *,
    page_number: int,
    table_id: str,
    crop_bbox: tuple[int, int, int, int],
    ocr_blocks: list[TextBlock],
    engine: str,
) -> TableData | None:
    cells = getattr(table_result, "cells", []) or []
    table = TableData(
        table_id=table_id,
        page_number=page_number,
        bbox=tuple(float(value) for value in crop_bbox),
        engine=engine,
    )
    left, top, _, _ = crop_bbox
    for cell in cells:
        local_bbox = _coerce_bbox(getattr(cell, "bbox", None))
        if local_bbox is None:
            continue
        global_bbox = (
            float(local_bbox[0] + left),
            float(local_bbox[1] + top),
            float(local_bbox[2] + left),
            float(local_bbox[3] + top),
        )
        row_id = int(getattr(cell, "row_id", 0) or 0) + 1
        raw_col_id = getattr(cell, "col_id", None)
        if raw_col_id is None:
            raw_col_id = getattr(cell, "within_row_id", 0)
        col_id = int(raw_col_id or 0) + 1
        confidence = float(getattr(cell, "confidence", 1.0) or 1.0)
        text = _surya_cell_text(getattr(cell, "text_lines", None))
        if not text:
            text = _collect_cell_text_from_ocr(global_bbox, ocr_blocks)
        table.cells.append(
            TableCellData(
                table_id=table_id,
                page_number=page_number,
                row_id=row_id,
                col_id=col_id,
                text=text,
                bbox=global_bbox,
                confidence=confidence,
                engine=engine,
                is_header=bool(getattr(cell, "is_header", False)),
            )
        )
    if not table.cells:
        return None
    return table


def _surya_cell_text(text_lines: Any) -> str:
    if not isinstance(text_lines, list):
        return ""
    parts: list[str] = []
    for line in text_lines:
        text = ""
        if isinstance(line, dict):
            text = clean_cell(line.get("text", ""))
        else:
            text = clean_cell(getattr(line, "text", ""))
        if text and text not in parts:
            parts.append(text)
    return " ".join(parts)


def _collect_cell_text_from_ocr(
    cell_bbox: tuple[float, float, float, float],
    ocr_blocks: list[TextBlock],
) -> str:
    matches: list[TextBlock] = []
    for block in ocr_blocks:
        overlap_area = _bbox_intersection_area(cell_bbox, block.bbox)
        if overlap_area <= 0:
            continue
        block_area = max(1.0, (block.bbox[2] - block.bbox[0]) * (block.bbox[3] - block.bbox[1]))
        if _bbox_contains_point(cell_bbox, _bbox_center(block.bbox)) or (overlap_area / block_area) >= 0.35:
            matches.append(block)
    matches.sort(key=lambda item: (item.reading_order if item.reading_order is not None else 10**9, item.bbox[1], item.bbox[0]))
    texts: list[str] = []
    for block in matches:
        text = clean_cell(block.text)
        if text and text not in texts:
            texts.append(text)
    return " ".join(texts)


def _expand_and_clip_bbox(
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
    padding: int = 8,
) -> tuple[int, int, int, int] | None:
    left = max(0, int(round(bbox[0])) - padding)
    top = max(0, int(round(bbox[1])) - padding)
    right = min(width, int(round(bbox[2])) + padding)
    bottom = min(height, int(round(bbox[3])) + padding)
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _bbox_contains_point(bbox: tuple[float, float, float, float], point: tuple[float, float]) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def _bbox_intersection_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    inter_left = max(left[0], right[0])
    inter_top = max(left[1], right[1])
    inter_right = min(left[2], right[2])
    inter_bottom = min(left[3], right[3])
    if inter_right <= inter_left or inter_bottom <= inter_top:
        return 0.0
    return float((inter_right - inter_left) * (inter_bottom - inter_top))


def _polygon_to_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
        except Exception:
            continue
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))
