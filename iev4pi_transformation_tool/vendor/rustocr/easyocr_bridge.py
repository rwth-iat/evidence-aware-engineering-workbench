#!/usr/bin/env python3
"""EasyOCR Python bridge adapted from cyberiums/RustOCR.

This script is intentionally vendored so the desktop app can call a stable,
repo-controlled bridge path instead of relying on an external checkout.
"""

from __future__ import annotations

import argparse
import json
import sys

import easyocr


def _build_payload(results, *, detail: int) -> list[dict[str, object]]:
    if detail == 0:
        return [{"text": text} for text in results]
    return [
        {
            "bbox": [[int(x), int(y)] for x, y in bbox],
            "text": text,
            "confidence": float(confidence),
        }
        for bbox, text, confidence in results
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="EasyOCR Python bridge")
    parser.add_argument("--languages", required=True, help="Comma-separated language codes")
    parser.add_argument("--image", help="Path to image file")
    parser.add_argument(
        "--gpu",
        type=lambda value: str(value).lower() == "true",
        default=True,
        help="Use GPU acceleration",
    )
    parser.add_argument("--detail", type=int, default=1, choices=[0, 1], help="Detail level")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Keep the EasyOCR reader alive and process JSON requests from stdin",
    )
    args = parser.parse_args()

    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    if not languages:
        print(json.dumps({"error": "No OCR languages supplied"}), file=sys.stderr)
        return 1
    if not args.serve and not args.image:
        print(json.dumps({"error": "--image is required unless --serve is enabled"}), file=sys.stderr)
        return 1

    try:
        reader = easyocr.Reader(languages, gpu=args.gpu, verbose=False)
        if args.serve:
            print(json.dumps({"status": "ready"}, ensure_ascii=False), flush=True)
            for line in sys.stdin:
                raw = line.strip()
                if not raw:
                    continue
                request = json.loads(raw)
                command = str(request.get("command", "ocr")).strip().lower()
                if command == "quit":
                    print(json.dumps({"status": "bye"}, ensure_ascii=False), flush=True)
                    return 0
                image_path = request.get("image")
                if not image_path:
                    print(json.dumps({"error": "Missing image path"}, ensure_ascii=False), flush=True)
                    continue
                detail = int(request.get("detail", args.detail))
                results = reader.readtext(str(image_path), detail=detail)
                print(json.dumps(_build_payload(results, detail=detail), ensure_ascii=False), flush=True)
            return 0

        results = reader.readtext(args.image, detail=args.detail)
        print(json.dumps(_build_payload(results, detail=args.detail), ensure_ascii=False))
        return 0
    except Exception as exc:  # pragma: no cover - third-party runtime guard
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
