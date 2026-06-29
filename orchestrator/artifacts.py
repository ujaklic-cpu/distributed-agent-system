from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from orchestrator.config import settings
from shared.schemas import new_id

DEFAULT_ARTIFACT_DIRS = ("output", "artifacts")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_path(workspace_id: str) -> Path:
    return Path(settings.orchestrator_workspace_root) / workspace_id


def collect_workspace_artifacts(task_id: str, workspace_id: str) -> list[dict[str, object]]:
    """Copy stable task artifacts into the orchestrator artifact store and return metadata."""
    workspace = workspace_path(workspace_id)
    if not workspace.exists():
        return []

    destination_root = Path(settings.artifact_storage_root) / task_id
    destination_root.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    total_files = 0
    for candidate in workspace.rglob("*"):
        if candidate.is_file():
            total_files += 1
            total_bytes += candidate.stat().st_size
            if total_files > settings.max_workspace_files:
                raise RuntimeError("workspace file-count limit exceeded")
            if total_bytes > settings.max_workspace_bytes:
                raise RuntimeError("workspace byte-size limit exceeded")

    records: list[dict[str, object]] = []
    seen: set[Path] = set()

    for dirname in DEFAULT_ARTIFACT_DIRS:
        source_dir = workspace / dirname
        if not source_dir.exists():
            continue
        for path in source_dir.rglob("*"):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            if path.stat().st_size > settings.max_artifact_bytes:
                raise RuntimeError("artifact byte-size limit exceeded")
            relative = path.relative_to(workspace)
            safe_name = str(relative).replace("\\", "/")
            stored = destination_root / relative
            stored.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, stored)
            digest = _sha256(stored)
            artifact_id = new_id("artifact")
            records.append(
                {
                    "id": artifact_id,
                    "task_id": task_id,
                    "workspace_id": workspace_id,
                    "name": safe_name,
                    "source_path": str(path),
                    "stored_path": str(stored),
                    "uri": f"{settings.artifact_uri_prefix}{task_id}/{safe_name}",
                    "size_bytes": stored.stat().st_size,
                    "sha256": digest,
                }
            )
    return records
