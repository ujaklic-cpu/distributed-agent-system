from __future__ import annotations

import platform
import shutil
import subprocess

import psutil

from shared.schemas import NodeGpu


def cpu_cores_total() -> int:
    return psutil.cpu_count(logical=True) or 1


def cpu_cores_free() -> int:
    percent = psutil.cpu_percent(interval=0.1)
    total = cpu_cores_total()
    return max(1, round(total * (100 - percent) / 100))


def ram_gb_total() -> float:
    return round(psutil.virtual_memory().total / (1024**3), 2)


def ram_gb_free() -> float:
    return round(psutil.virtual_memory().available / (1024**3), 2)


def probe_gpus() -> list[NodeGpu]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, text=True, timeout=3)
        gpus: list[NodeGpu] = []
        for line in out.strip().splitlines():
            idx, name, total_mb, free_mb = [part.strip() for part in line.split(",", 3)]
            gpus.append(
                NodeGpu(
                    id=f"gpu{idx}",
                    name=name,
                    vram_gb_total=round(float(total_mb) / 1024, 2),
                    vram_gb_free=round(float(free_mb) / 1024, 2),
                )
            )
        return gpus
    except Exception:
        return []


def docker_available() -> bool:
    try:
        import docker

        client = docker.from_env()
        return bool(client.ping())
    except Exception:
        return False


def platform_arch() -> str:
    return platform.machine() or "unknown"


def resource_snapshot() -> dict:
    return {
        "cpu_cores_total": cpu_cores_total(),
        "cpu_cores_free": cpu_cores_free(),
        "ram_gb_total": ram_gb_total(),
        "ram_gb_free": ram_gb_free(),
        "gpus": [gpu.model_dump() for gpu in probe_gpus()],
        "docker_available": docker_available(),
        "platform_arch": platform_arch(),
    }
