from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class OCRPlatformDefaults:
    ocr_backend: str
    ocr_fallback_backend: str
    ocr_device: str
    apple_ocr_framework: str = "vision"
    apple_ocr_recognition_level: str = "accurate"


def get_ocr_platform_defaults(system: str | None = None) -> OCRPlatformDefaults:
    current_system = system or platform.system()
    if current_system == "Darwin":
        return OCRPlatformDefaults(
            ocr_backend="apple",
            ocr_fallback_backend="rapidocr",
            ocr_device="cpu",
            apple_ocr_framework="vision",
            apple_ocr_recognition_level="accurate",
        )
    return OCRPlatformDefaults(
        ocr_backend="paddle",
        ocr_fallback_backend="surya",
        ocr_device="cuda:0",
    )
