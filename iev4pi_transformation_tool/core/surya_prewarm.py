from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[int, str], None]


def _required_models() -> list[dict[str, str]]:
    try:
        from surya.settings import settings
    except Exception:
        return []

    checkpoints = [
        ("text_detection", "Text Detection", settings.DETECTOR_MODEL_CHECKPOINT),
        ("text_recognition", "Text Recognition", settings.FOUNDATION_MODEL_CHECKPOINT),
        ("layout", "Layout", settings.LAYOUT_MODEL_CHECKPOINT),
        ("table_recognition", "Table Recognition", settings.TABLE_REC_MODEL_CHECKPOINT),
    ]
    seen: set[str] = set()
    models: list[dict[str, str]] = []
    for key, label, checkpoint in checkpoints:
        if checkpoint in seen:
            continue
        seen.add(checkpoint)
        models.append({"key": key, "label": label, "checkpoint": checkpoint})
    return models


def _cache_dir(workspace_root: Path) -> Path:
    return workspace_root / ".iev4pi" / "cache" / "surya_models"


def _checkpoint_relative_path(checkpoint: str) -> str:
    return checkpoint.replace("s3://", "", 1)


def _local_model_dir(workspace_root: Path, checkpoint: str) -> Path:
    return _cache_dir(workspace_root) / _checkpoint_relative_path(checkpoint)


def _load_manifest(path: Path) -> dict | None:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _model_ready(path: Path) -> tuple[bool, int, int]:
    manifest = _load_manifest(path)
    if not isinstance(manifest, dict):
        return False, 0, 0
    files = manifest.get("files", [])
    if not isinstance(files, list):
        return False, 0, 0
    total = len(files)
    ready = 0
    for file_name in files:
        if (path / str(file_name)).exists():
            ready += 1
    return ready == total and total > 0, ready, total


def surya_prewarm_status(workspace_root: Path) -> dict[str, object]:
    workspace_root = workspace_root.resolve()
    models = []
    ready_count = 0
    for spec in _required_models():
        local_dir = _local_model_dir(workspace_root, spec["checkpoint"])
        is_ready, ready_files, total_files = _model_ready(local_dir)
        if is_ready:
            ready_count += 1
        models.append(
            {
                **spec,
                "local_dir": str(local_dir),
                "is_ready": is_ready,
                "ready_files": ready_files,
                "total_files": total_files,
            }
        )
    return {
        "available": bool(models),
        "ready": ready_count == len(models),
        "ready_count": ready_count,
        "model_count": len(models),
        "models": models,
        "cache_dir": str(_cache_dir(workspace_root)),
    }


def _join_url(base: str, suffix: str) -> str:
    return f"{base.rstrip('/')}/{suffix.lstrip('/')}"


def _emit_status(
    progress: ProgressCallback | None,
    statuses: dict[str, float],
    labels: dict[str, str],
    *,
    current_key: str | None = None,
    current_file: str | None = None,
) -> None:
    if progress is None:
        return
    model_count = max(1, len(statuses))
    overall = round(sum(statuses.values()) / model_count)
    ready_models = sum(1 for value in statuses.values() if value >= 100.0)
    lines = [f"Cached models {ready_models}/{model_count}"]
    for key, label in labels.items():
        percent = max(0, min(100, round(statuses.get(key, 0.0))))
        line = f"{label}: {percent}%"
        if key == current_key and current_file:
            line += f" ({current_file})"
        lines.append(line)
    progress(overall, "\n".join(lines))


def _download_with_progress(
    session,
    url: str,
    local_path: Path,
    *,
    on_chunk: Callable[[float], None],
    chunk_size: int = 1024 * 1024,
) -> None:
    response = session.get(url, stream=True, allow_redirects=True, timeout=60)
    response.raise_for_status()
    total_size = int(response.headers.get("content-length", 0))
    downloaded = 0
    part_path = local_path.with_suffix(local_path.suffix + ".part")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(part_path, "wb") as handle:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            handle.write(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                on_chunk(downloaded / total_size)
    part_path.replace(local_path)
    on_chunk(1.0)


def prewarm_surya_models(workspace_root: Path, progress: ProgressCallback | None = None) -> dict[str, object]:
    workspace_root = workspace_root.resolve()
    cache_dir = _cache_dir(workspace_root)
    cache_dir.mkdir(parents=True, exist_ok=True)
    import requests

    try:
        from surya.settings import settings
    except Exception as exc:
        raise RuntimeError("Surya is not installed") from exc

    models = _required_models()
    if not models:
        raise RuntimeError("Surya is not installed")
    statuses: dict[str, float] = {}
    labels = {spec["key"]: spec["label"] for spec in models}
    for spec in models:
        local_dir = _local_model_dir(workspace_root, spec["checkpoint"])
        is_ready, _ready_files, _total_files = _model_ready(local_dir)
        statuses[spec["key"]] = 100.0 if is_ready else 0.0
    _emit_status(progress, statuses, labels)

    session = requests.Session()
    try:
        for spec in models:
            key = spec["key"]
            checkpoint = spec["checkpoint"]
            local_dir = _local_model_dir(workspace_root, checkpoint)
            is_ready, _ready_files, _total_files = _model_ready(local_dir)
            if is_ready:
                continue

            remote_root = _join_url(settings.S3_BASE_URL, _checkpoint_relative_path(checkpoint))
            manifest_url = _join_url(remote_root, "manifest.json")
            manifest_response = session.get(manifest_url, timeout=60)
            manifest_response.raise_for_status()
            manifest = manifest_response.json()
            files = [str(file_name) for file_name in manifest.get("files", [])]
            if not files:
                raise RuntimeError(f"Missing manifest entries for {checkpoint}")

            temp_parent = cache_dir / ".tmp"
            temp_parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=temp_parent) as temp_dir_name:
                temp_dir = Path(temp_dir_name)
                (temp_dir / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                total_files = len(files)
                completed_files = 0
                for file_name in files:
                    remote_url = _join_url(remote_root, file_name)
                    local_path = temp_dir / file_name

                    def on_chunk(file_fraction: float, *, _key: str = key, _file_name: str = file_name) -> None:
                        statuses[_key] = ((completed_files + max(0.0, min(1.0, file_fraction))) / total_files) * 100.0
                        _emit_status(progress, statuses, labels, current_key=_key, current_file=Path(_file_name).name)

                    _download_with_progress(session, remote_url, local_path, on_chunk=on_chunk)
                    completed_files += 1
                    statuses[key] = (completed_files / total_files) * 100.0
                    _emit_status(progress, statuses, labels, current_key=key, current_file=Path(file_name).name)

                if local_dir.exists():
                    shutil.rmtree(local_dir)
                local_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(temp_dir), str(local_dir))
                statuses[key] = 100.0
                _emit_status(progress, statuses, labels)
    finally:
        session.close()

    return surya_prewarm_status(workspace_root)
