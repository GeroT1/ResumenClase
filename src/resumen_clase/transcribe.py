"""Transcripción con faster-whisper. VRAM-consciente: context manager libera GPU."""
from __future__ import annotations

import gc
import os
import site
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Callable


def _setup_cuda_dlls() -> None:
    """Agrega los directorios de DLLs de nvidia-*-cu12 al PATH antes de que
    ctranslate2 intente cargarlas. Debe correr a nivel de módulo."""
    if sys.platform != "win32":
        return
    search = list(sys.path)
    if hasattr(sys, "_MEIPASS"):
        search.append(sys._MEIPASS)
        search.append(str(Path(sys._MEIPASS) / "_internal"))
    if sys.executable:
        exe_parent = Path(sys.executable).parent
        search.append(str(exe_parent))
        search.append(str(exe_parent / "_internal"))
    try:
        search += site.getsitepackages()
    except Exception:
        pass
    dirs: list[str] = []
    for sp in search:
        if not sp:
            continue
        p = Path(sp)
        if p.is_dir():
            d = str(p)
            if d not in dirs:
                dirs.append(d)
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
        nvidia_dir = p / "nvidia"
        if nvidia_dir.is_dir():
            for pkg in nvidia_dir.iterdir():
                bin_dir = pkg / "bin"
                if bin_dir.is_dir():
                    d = str(bin_dir)
                    if d not in dirs:
                        dirs.append(d)
                        try:
                            os.add_dll_directory(d)
                        except Exception:
                            pass
    if dirs:
        os.environ["PATH"] = ";".join(dirs) + ";" + os.environ.get("PATH", "")


# Correr ANTES de importar faster_whisper / ctranslate2
_setup_cuda_dlls()

import warnings  # noqa: E402
warnings.filterwarnings("ignore", message=".*symlinks.*")
warnings.filterwarnings("ignore", message=".*HF_TOKEN.*")
warnings.filterwarnings("ignore", message=".*unauthenticated.*")

import numpy as np  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

from .config import WhisperCfg  # noqa: E402


@contextmanager
def load_whisper(cfg: WhisperCfg) -> Iterator[WhisperModel]:
    model = None
    try:
        model = WhisperModel(
            cfg.model,
            device=cfg.device,
            compute_type=cfg.compute_type,
        )
    except Exception as exc:
        if cfg.device == "cuda":
            import logging
            logging.getLogger(__name__).warning(
                "Falló la inicialización de Whisper en CUDA (%s). Cambiando automáticamente a CPU (int8)...", exc
            )
            model = WhisperModel(
                cfg.model,
                device="cpu",
                compute_type="int8",
            )
        else:
            raise
    try:
        yield model
    finally:
        if model is not None:
            del model
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def transcribe_array(
    model: WhisperModel,
    audio: np.ndarray,
    cfg: WhisperCfg,
) -> tuple[str, list[dict]]:
    """Transcribe numpy array float32 16kHz mono → (texto, segmentos)."""
    segments, _info = model.transcribe(
        audio,
        language=cfg.language,
        beam_size=cfg.beam_size,
        vad_filter=cfg.vad_filter,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    text_parts: list[str] = []
    seg_list: list[dict] = []
    for s in segments:
        text_parts.append(s.text.strip())
        seg_list.append({"start": s.start, "end": s.end, "text": s.text.strip()})
    return " ".join(text_parts), seg_list


def transcribe_file(
    model: WhisperModel,
    wav: Path,
    cfg: WhisperCfg,
    progress_cb: Callable[[str], None] | None = None,
    base_msg: str = "Transcribiendo",
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[str, list[dict]]:
    segments, _info = model.transcribe(
        str(wav),
        language=cfg.language,
        beam_size=cfg.beam_size,
        vad_filter=cfg.vad_filter,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    t0 = time.time()
    last_update = t0
    total_duration = _info.duration

    text_parts: list[str] = []
    seg_list: list[dict] = []
    for s in segments:
        if is_cancelled and is_cancelled():
            raise InterruptedError("Cancelado por el usuario")

        text_parts.append(s.text.strip())
        seg_list.append({"start": s.start, "end": s.end, "text": s.text.strip()})

        if progress_cb and total_duration > 0:
            now = time.time()
            if now - last_update > 2.0:
                elapsed = now - t0
                speed = s.end / elapsed if elapsed > 0 else 0
                remaining_audio = max(0.0, total_duration - s.end)
                eta = remaining_audio / speed if speed > 0 else 0

                pct = min(100, int((s.end / total_duration) * 100))
                mm = int(eta // 60)
                ss = int(eta % 60)
                progress_cb(f"{base_msg} ({pct}% - ETA: {mm:02d}:{ss:02d})")
                last_update = now

    return " ".join(text_parts), seg_list


def format_segments(segs: list[dict]) -> str:
    """Transcript con timestamps [mm:ss]."""
    lines: list[str] = []
    for s in segs:
        mm = int(s["start"] // 60)
        ss = int(s["start"] % 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {s['text']}")
    return "\n".join(lines)
