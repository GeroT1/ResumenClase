"""Monitoreo opcional de VRAM y telemetría del modelo cargado.

Uso típico (CLI/GUI):

    from .monitor import GpuMonitor
    with GpuMonitor(interval=2.0) as gm:
        # ... tu código de inferencia ...
        pass
    print(gm.summary())   # → "VRAM: avg=7321 MiB, peak=7892 MiB"

Si nvidia-smi no está disponible (no NVIDIA, o sin drivers en PATH),
GpuMonitor se desactiva silenciosamente — `summary()` devuelve "n/a".

Para introspección puntual: `gpu_snapshot()` y `ollama_running()`.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


def _have_nvidia_smi() -> bool:
    return shutil.which("nvidia-smi") is not None


def gpu_snapshot() -> dict | None:
    """Lee `nvidia-smi` UNA vez y devuelve memoria usada/total + utilización.
    None si no está disponible."""
    if not _have_nvidia_smi():
        return None
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,utilization.gpu,name",
                "--format=csv,noheader,nounits",
            ],
            text=True, timeout=3,
        )
        line = out.strip().splitlines()[0]
        used, total, util, name = [x.strip() for x in line.split(",")]
        return {
            "vram_used_mib":  int(used),
            "vram_total_mib": int(total),
            "gpu_util_pct":   int(util),
            "gpu_name":       name,
        }
    except Exception as e:
        log.debug("gpu_snapshot falló: %s", e)
        return None


@dataclass
class _Sample:
    t: float
    vram_used: int
    util: int


class GpuMonitor:
    """Sampler en background. Context manager. Stop al salir."""

    def __init__(self, interval: float = 2.0) -> None:
        self.interval = interval
        self.samples: list[_Sample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = _have_nvidia_smi()

    def __enter__(self) -> "GpuMonitor":
        if self._enabled:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 1)

    def _run(self) -> None:
        while not self._stop.is_set():
            snap = gpu_snapshot()
            if snap:
                self.samples.append(_Sample(
                    t=time.time(),
                    vram_used=snap["vram_used_mib"],
                    util=snap["gpu_util_pct"],
                ))
            self._stop.wait(self.interval)

    def summary(self) -> str:
        if not self._enabled:
            return "VRAM: n/a (nvidia-smi no encontrado)"
        if not self.samples:
            return "VRAM: sin muestras"
        vrams = [s.vram_used for s in self.samples]
        utils = [s.util for s in self.samples]
        return (
            f"VRAM: avg={sum(vrams)//len(vrams)} MiB, peak={max(vrams)} MiB · "
            f"GPU util: avg={sum(utils)//len(utils)}%, peak={max(utils)}%"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Estado del modelo en Ollama
# ─────────────────────────────────────────────────────────────────────────────

def ollama_running(host: str = "http://localhost:11434") -> list[dict]:
    """Devuelve los modelos actualmente cargados en VRAM por Ollama
    (vía /api/ps). Lista vacía si no hay nada cargado."""
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.get(f"{host}/api/ps")
            r.raise_for_status()
            return r.json().get("models", [])
    except Exception as e:
        log.debug("ollama_running falló: %s", e)
        return []


def diagnose(host: str = "http://localhost:11434") -> str:
    """Texto multi-línea con todo lo relevante para validar setup.
    Pensado para imprimirlo al arrancar el batch o al pedido del usuario."""
    lines: list[str] = []
    snap = gpu_snapshot()
    if snap:
        lines.append(
            f"GPU: {snap['gpu_name']} · "
            f"VRAM {snap['vram_used_mib']}/{snap['vram_total_mib']} MiB · "
            f"util {snap['gpu_util_pct']}%"
        )
    else:
        lines.append("GPU: nvidia-smi no disponible")

    running = ollama_running(host)
    if not running:
        lines.append("Ollama: ningún modelo cargado en VRAM")
    else:
        for m in running:
            name = m.get("name", "?")
            size_vram = m.get("size_vram", 0) / (1024**3)
            size_total = m.get("size", 0) / (1024**3)
            ratio = (size_vram / size_total * 100) if size_total else 0
            lines.append(
                f"Ollama: {name} · {size_vram:.1f}/{size_total:.1f} GiB en VRAM "
                f"({ratio:.0f}% on GPU)"
            )
    return "\n".join(lines)


if __name__ == "__main__":
    # Smoke test: `python -m resumen_clase.monitor`
    print(diagnose())
