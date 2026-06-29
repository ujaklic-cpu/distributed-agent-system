from __future__ import annotations

import os
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from threading import Lock

try:
    import docker
    from docker.errors import DockerException
except Exception:  # pragma: no cover - optional runtime dependency in unit tests
    docker = None

    class DockerException(Exception):
        pass


from node_agent.config import settings
from shared.policy import DEFAULT_TOOL_POLICY
from shared.schemas import ToolContainerResult, ToolJobRequest, ToolJobResult, ToolType
from shared.tool_metadata import default_tool_metadata


def _parse_uid_gid(user: str) -> tuple[int, int] | None:
    if not user:
        return None
    uid_text, _, gid_text = user.partition(":")
    if not uid_text.isdigit():
        return None
    uid = int(uid_text)
    gid = int(gid_text) if gid_text.isdigit() else uid
    return uid, gid


def _prepare_writable_path(path: Path, uid_gid: tuple[int, int] | None, mode: int = 0o777) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        path.chmod(mode)
    if uid_gid is not None and hasattr(os, "chown"):
        with suppress(OSError):
            os.chown(path, uid_gid[0], uid_gid[1])


def _resolve_host_workspace(workspace_id: str) -> Path:
    root = Path(settings.host_workspace_root).resolve()
    workspace = (root / workspace_id).resolve()
    if not workspace.is_relative_to(root):
        raise ValueError("resolved host workspace escapes host_workspace_root")
    return workspace


class DockerToolRunner:
    def __init__(self) -> None:
        self.client = None
        self._containers: dict[str, object] = {}
        self._canceled: set[str] = set()
        self._lock = Lock()
        try:
            if docker is None:
                raise DockerException("docker SDK is not installed")
            self.client = docker.from_env()
            self.client.ping()
        except DockerException:
            self.client = None

    def image_map(self) -> dict[ToolType, str]:
        return {
            ToolType.shell: settings.tool_image_shell,
            ToolType.test: settings.tool_image_test,
            ToolType.internet: settings.tool_image_internet,
            ToolType.filesystem: settings.tool_image_filesystem,
            ToolType.build: settings.tool_image_build,
            ToolType.analysis: settings.tool_image_analysis,
        }

    def metadata(self) -> dict[ToolType, object]:
        return default_tool_metadata(self.image_map())

    def image_for_tool(self, tool: ToolType) -> str:
        return self.metadata()[tool].image

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            self._canceled.add(job_id)
            container = self._containers.get(job_id)
        if container is None:
            return False
        with suppress(Exception):
            container.kill()
        with suppress(Exception):
            container.remove(force=True)
        return True

    def is_canceled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._canceled

    def _canceled_result(self, job: ToolJobRequest, start: float) -> ToolJobResult:
        return ToolJobResult(
            job_id=job.job_id,
            attempt_id=job.attempt_id,
            generation=job.generation,
            node_id=settings.node_id,
            status="canceled",
            error="Tool job canceled",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def run(self, job: ToolJobRequest) -> ToolJobResult:
        start = time.monotonic()
        try:
            DEFAULT_TOOL_POLICY.validate(job)
        except Exception as exc:
            return ToolJobResult(
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                generation=job.generation,
                node_id=settings.node_id,
                status="blocked",
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        uid_gid = _parse_uid_gid(settings.tool_container_user)
        try:
            host_workspace = str(_resolve_host_workspace(job.workspace_id))
        except ValueError as exc:
            return ToolJobResult(
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                generation=job.generation,
                node_id=settings.node_id,
                status="blocked",
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        node_workspace = Path(settings.node_workspace_root) / job.workspace_id
        _prepare_writable_path(node_workspace, uid_gid)

        if self.client is None:
            if settings.allow_host_tool_fallback:
                return self._run_host_fallback(job, node_workspace, start)
            return ToolJobResult(
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                generation=job.generation,
                node_id=settings.node_id,
                status="error",
                error="Docker is unavailable and host fallback is disabled",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        result_dir = node_workspace / ".tool-results"
        _prepare_writable_path(result_dir, uid_gid, mode=0o777)
        result_path = result_dir / f"{job.job_id}-{job.attempt_id}.json"
        if result_path.exists():
            result_path.unlink()
        if self.is_canceled(job.job_id):
            return self._canceled_result(job, start)
        container = None
        try:
            image = self.image_for_tool(job.tool)
            run_options = self._container_run_options(job, image, host_workspace, result_path)
            container = self.client.containers.run(**run_options)
            with self._lock:
                self._containers[job.job_id] = container
            try:
                wait_result = container.wait(timeout=job.limits.timeout_seconds + 5)
                if self.is_canceled(job.job_id):
                    return self._canceled_result(job, start)
                status_code = int(wait_result.get("StatusCode", 1))
                if result_path.exists():
                    try:
                        parsed = ToolContainerResult.model_validate_json(result_path.read_text(encoding="utf-8"))
                    except Exception as exc:
                        return ToolJobResult(
                            job_id=job.job_id,
                            attempt_id=job.attempt_id,
                            generation=job.generation,
                            node_id=settings.node_id,
                            status="error",
                            exit_code=status_code,
                            error=f"tool output schema validation failed: {exc}",
                            duration_ms=int((time.monotonic() - start) * 1000),
                        )
                    return self._result_from_container(job, parsed, start)
                try:
                    logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
                except Exception:
                    logs = ""
                return ToolJobResult(
                    job_id=job.job_id,
                    attempt_id=job.attempt_id,
                    generation=job.generation,
                    node_id=settings.node_id,
                    status="ok" if status_code == 0 else "error",
                    exit_code=status_code,
                    stdout=logs[-job.limits.output_limit_mb * 1024 * 1024 :],
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            except Exception as exc:
                with suppress(Exception):
                    container.kill()
                return ToolJobResult(
                    job_id=job.job_id,
                    attempt_id=job.attempt_id,
                    generation=job.generation,
                    node_id=settings.node_id,
                    status="timeout",
                    error=str(exc),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
        except Exception as exc:
            return ToolJobResult(
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                generation=job.generation,
                node_id=settings.node_id,
                status="error",
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        finally:
            with self._lock:
                self._containers.pop(job.job_id, None)
            if container is not None:
                with suppress(Exception):
                    container.remove(force=True)
            with suppress(OSError):
                result_path.unlink()

    def _result_from_container(self, job: ToolJobRequest, parsed: ToolContainerResult, start: float) -> ToolJobResult:
        limit = job.limits.output_limit_mb * 1024 * 1024
        err = None
        if parsed.error is not None:
            err = f"{parsed.error.code}: {parsed.error.message}"
        return ToolJobResult(
            job_id=job.job_id,
            attempt_id=job.attempt_id,
            generation=job.generation,
            node_id=settings.node_id,
            status="ok" if parsed.exit_code == 0 else "error",
            exit_code=parsed.exit_code,
            stdout=parsed.stdout[-limit:],
            stderr=parsed.stderr[-limit:],
            artifacts=[artifact.model_dump(mode="json") for artifact in parsed.artifacts],
            duration_ms=parsed.duration_ms or int((time.monotonic() - start) * 1000),
            error=err,
        )

    def _container_run_options(self, job: ToolJobRequest, image: str, host_workspace: str, result_path: Path) -> dict:
        result_in_workspace = Path("/workspace") / result_path.relative_to(
            Path(settings.node_workspace_root) / job.workspace_id
        )
        run_options = {
            "image": image,
            "detach": True,
            "network_disabled": not job.network,
            "working_dir": "/workspace",
            "environment": {
                "TOOL_COMMAND": job.command,
                "TOOL_TIMEOUT_SECONDS": str(job.limits.timeout_seconds),
                "TOOL_RESULT_PATH": result_in_workspace.as_posix(),
                "TOOL_NAME": job.tool.value,
                "TOOL_INLINE_OUTPUT_BYTES": str(min(job.limits.output_limit_mb * 1024 * 1024, 65536)),
                "HOME": "/tmp",
                "TMPDIR": "/tmp",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PIP_CACHE_DIR": "/tmp/pip-cache",
                "XDG_CACHE_HOME": "/tmp/.cache",
            },
            "volumes": {
                host_workspace: {"bind": "/workspace", "mode": "rw"},
            },
            "mem_limit": job.limits.memory,
            "nano_cpus": int(job.limits.cpus * 1_000_000_000),
            "privileged": False,
            "pid_mode": None,
            "ipc_mode": None,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 512,
            "remove": settings.tool_container_auto_remove,
            "read_only": settings.tool_container_read_only_root,
        }
        if settings.tool_container_tmpfs:
            run_options["tmpfs"] = {"/tmp": "rw,nosuid,nodev,noexec,size=512m"}
        if settings.tool_container_user:
            run_options["user"] = settings.tool_container_user
        return run_options

    def _run_host_fallback(self, job: ToolJobRequest, workspace: Path, start: float) -> ToolJobResult:
        try:
            completed = subprocess.run(
                ["bash", "-lc", job.command],
                cwd=str(workspace),
                text=True,
                capture_output=True,
                timeout=job.limits.timeout_seconds,
            )
            return ToolJobResult(
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                generation=job.generation,
                node_id=settings.node_id,
                status="ok" if completed.returncode == 0 else "error",
                exit_code=completed.returncode,
                stdout=completed.stdout[-job.limits.output_limit_mb * 1024 * 1024 :],
                stderr=completed.stderr[-job.limits.output_limit_mb * 1024 * 1024 :],
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return ToolJobResult(
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                generation=job.generation,
                node_id=settings.node_id,
                status="timeout",
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                error="tool command timed out",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
