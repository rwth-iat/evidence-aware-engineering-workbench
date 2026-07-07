#!/usr/bin/env python3
"""Pipeline test script — exactly mirrors what the GUI's task_runner does.

Usage: python scripts/test_pipeline.py

This mimics the GUI's ``run_workbench_task_process`` for each pipeline phase,
calling the same workbench methods with the same parameters.  If this passes,
the GUI will pass.
"""

import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent  # project root


def emit(kind: str, payload=None):
    """Simulate the GUI's progress/log emitter."""
    if kind == "progress":
        pct = payload.get("value", 0) if isinstance(payload, dict) else 0
        msg = payload.get("message", "") if isinstance(payload, dict) else str(payload)
        print(f"  [{pct}%] {msg}", flush=True)
    elif kind == "result":
        print(f"  Result: {payload}", flush=True)
    elif kind == "log":
        entry = payload.get("entry", {}) if isinstance(payload, dict) else {}
        level = entry.get("level", "INFO")
        if level in ("ERROR", "WARNING"):
            print(f"  [{level}] {entry.get('message', '')}", flush=True)


def run_phase(task_name, payload=None):
    """Mimics run_workbench_task_process for a single phase."""
    print(f"\n{'='*60}")
    print(f"  Phase: {task_name}")
    print(f"{'='*60}")

    def progress(value, message):
        emit("progress", {"value": value, "message": message})

    # External log sink — same as GUI passes
    def log_entry(entry):
        emit("log", {"entry": entry})

    # Same Workbench constructor as GUI line 59
    from iev4pi_transformation_tool.services.workbench import Workbench
    wb = Workbench(WORKSPACE, external_log_sink=log_entry)

    if task_name == "run_extraction":
        use_ocr = payload.get("use_ocr") if payload else None
        summary = wb.run_extraction(progress, use_ocr=bool(use_ocr) if use_ocr is not None else None)
        emit("result", summary.model_dump(mode="json"))
        return summary

    elif task_name == "fill_standardized_templates":
        use_ocr = payload.get("use_ocr") if payload else None
        summary = wb.fill_standardized_templates(progress, use_ocr=bool(use_ocr) if use_ocr is not None else None)
        emit("result", summary.model_dump(mode="json"))
        return summary

    elif task_name == "save_extraction_results":
        result = wb.save_extraction_results(progress)
        emit("result", result)
        return result

    else:
        raise ValueError(f"Unknown task: {task_name}")


if __name__ == "__main__":
    # Clean start
    db_path = WORKSPACE / ".iev4pi" / "state.sqlite"
    cache_path = WORKSPACE / ".iev4pi" / "llm_cache.json"
    db_path.unlink(missing_ok=True)
    # Keep cache if it exists (for testing cache persistence)

    import subprocess
    subprocess.run(["find", str(WORKSPACE), "-type", "d", "-name", "__pycache__",
                    "-path", "*/iev4pi*", "-exec", "rm", "-rf", "{}", "+"],
                   capture_output=True)

    try:
        run_phase("run_extraction")
        run_phase("fill_standardized_templates")
        run_phase("save_extraction_results")
        print("\n=== ALL PHASES PASSED - GUI will work ===")
    except Exception as e:
        print(f"\n=== FAILED: {e} ===")
        import traceback
        traceback.print_exc()
        sys.exit(1)
