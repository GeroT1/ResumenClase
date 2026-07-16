"""Diagnóstico no destructivo de dependencias opcionales y modelos locales."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import sys

import httpx

from .config import Config
from .secrets import get_anthropic_api_key, stored_anthropic_api_key


@dataclass(frozen=True, slots=True)
class SetupStatus:
    whisper_cached: bool
    whisper_detail: str
    gpu_available: bool
    gpu_detail: str
    cuda_runtime_ready: bool
    ffmpeg_path: str | None
    ollama_path: str | None
    ollama_running: bool
    ollama_models: tuple[str, ...]
    claude_ready: bool
    claude_detail: str


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt" and name.lower() == "ollama":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidate = Path(local) / "Programs" / "Ollama" / "ollama.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def _gpu_status() -> tuple[bool, str]:
    executable = find_executable("nvidia-smi")
    if not executable:
        return False, "No se detectó una GPU NVIDIA; se recomienda CPU + int8."
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True, result.stdout.strip().splitlines()[0] or "GPU NVIDIA detectada"
    except Exception as exc:
        return False, f"NVIDIA está instalada, pero no respondió: {exc}"


def _cuda_runtime_ready() -> bool:
    """CTranslate2 necesita al menos cuBLAS y cuDNN además del driver NVIDIA."""
    roots = [Path(item) for item in sys.path if item]
    roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    patterns = (
        "nvidia/cublas/bin/cublas64_*.dll",
        "nvidia/cudnn/bin/cudnn64_*.dll",
        "cublas64_*.dll",
        "cudnn64_*.dll",
    )
    has_cublas = any(any(root.glob(patterns[index])) for root in roots for index in (0, 2))
    has_cudnn = any(any(root.glob(patterns[index])) for root in roots for index in (1, 3))
    return has_cublas and has_cudnn


def _whisper_status(model: str) -> tuple[bool, str]:
    try:
        from faster_whisper.utils import download_model
        path = download_model(model, local_files_only=True)
        return True, f"{model} disponible en {path}"
    except Exception:
        return False, f"El modelo {model} todavía no está descargado."


def _ollama_status(host: str) -> tuple[bool, tuple[str, ...]]:
    try:
        response = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=1.5)
        response.raise_for_status()
        models = tuple(item["name"] for item in response.json().get("models", [])
                       if item.get("name"))
        return True, models
    except Exception:
        return False, ()


def diagnose(cfg: Config) -> SetupStatus:
    whisper_cached, whisper_detail = _whisper_status(cfg.whisper.model)
    gpu_available, gpu_detail = _gpu_status()
    cuda_runtime_ready = gpu_available and _cuda_runtime_ready()
    if gpu_available and not cuda_runtime_ready:
        gpu_detail += " · faltan bibliotecas CUDA/cuBLAS para usarla con Whisper."
    ollama_running, ollama_models = _ollama_status(cfg.llm.host)
    env_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    stored_key = bool(stored_anthropic_api_key())
    claude_ready = bool(get_anthropic_api_key())
    claude_detail = (
        "Clave disponible mediante ANTHROPIC_API_KEY." if env_key else
        "Clave guardada en el Administrador de credenciales de Windows." if stored_key else
        "Opcional: no hay una clave guardada."
    )
    return SetupStatus(
        whisper_cached, whisper_detail, gpu_available, gpu_detail, cuda_runtime_ready,
        find_executable("ffmpeg"), find_executable("ollama"),
        ollama_running, ollama_models, claude_ready, claude_detail,
    )
