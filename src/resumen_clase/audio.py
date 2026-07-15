"""Captura audio loopback WASAPI (Win) — graba lo que suena en speakers/auriculares."""
from __future__ import annotations

import subprocess
import threading
import time
import logging
from pathlib import Path
from queue import Queue
from typing import Callable

import warnings

import numpy as np
import soundcard as sc
import soundfile as sf

warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)

from .config import AudioCfg

log = logging.getLogger(__name__)


def output_devices() -> tuple[list[str], str]:
    """Devuelve salidas de audio disponibles y el nombre de la predeterminada."""
    speakers = list(sc.all_speakers())
    names = list(dict.fromkeys(str(speaker.name) for speaker in speakers))
    default_name = str(sc.default_speaker().name)
    if default_name not in names:
        names.insert(0, default_name)
    return names, default_name


def list_devices() -> None:
    print("Speakers (loopback source):")
    for s in sc.all_speakers():
        print(f"  - {s.name}")
    print("\nMics:")
    for m in sc.all_microphones(include_loopback=True):
        tag = " [LOOPBACK]" if m.isloopback else ""
        print(f"  - {m.name}{tag}")


def pick_loopback(
    device_name: str = "",
    on_warning: Callable[[str], None] | None = None,
) -> sc.Microphone:
    """Devuelve loopback mic. Si device_name está seteado busca por substring,
    si no usa el default speaker del sistema."""
    if device_name:
        speakers = list(sc.all_speakers())
        exact = next(
            (speaker for speaker in speakers if device_name.casefold() == str(speaker.name).casefold()),
            None,
        )
        partial = next(
            (speaker for speaker in speakers if device_name.casefold() in str(speaker.name).casefold()),
            None,
        )
        selected = exact or partial
        if selected is not None:
            return sc.get_microphone(id=str(selected.name), include_loopback=True)
        message = (
            f"La salida '{device_name}' ya no existe. "
            "Se usará el dispositivo predeterminado de Windows."
        )
        if on_warning:
            on_warning(message)
        else:
            log.warning(message)
    spk = sc.default_speaker()
    return sc.get_microphone(id=str(spk.name), include_loopback=True)


def default_loopback() -> sc.Microphone:
    """Retrocompat."""
    return pick_loopback("")


class LoopbackRecorder:
    """Graba loopback a wav con auto-recovery si el device se invalida."""

    def __init__(self, cfg: AudioCfg, out_path: Path) -> None:
        self.cfg = cfg
        self.out_path = out_path
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self.chunk_queue: Queue[np.ndarray] = Queue()
        self.error: BaseException | None = None
        self.on_warning: Callable[[str], None] = lambda _message: None
        self._last_warning = ""
        self._last_warning_at = 0.0

    def _warn(self, message: str) -> None:
        log.warning(message)
        now = time.monotonic()
        if message == self._last_warning and now - self._last_warning_at < 10:
            return
        self._last_warning = message
        self._last_warning_at = now
        try:
            self.on_warning(message)
        except Exception:
            log.exception("No se pudo entregar una advertencia de audio a la interfaz")

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def _record_session(self, wav: sf.SoundFile, buf: list[np.ndarray], acc: int, frames_per_chunk: int) -> tuple[list[np.ndarray], int]:
        """Abre el device y graba hasta que se invalida o se pide stop.
        Devuelve (buf, acc) para que el caller pueda continuar si reintenta."""
        mic = pick_loopback(self.cfg.device_name, on_warning=self._warn)
        with mic.recorder(samplerate=self.cfg.samplerate, channels=self.cfg.channels) as rec:
            while not self._stop.is_set():
                data = rec.record(numframes=self.cfg.samplerate)
                if self._paused.is_set():
                    continue
                if self.cfg.channels == 1 and data.ndim > 1:
                    data = data.mean(axis=1, keepdims=True)
                wav.write(data)
                buf.append(data)
                acc += len(data)
                if acc >= frames_per_chunk:
                    chunk = np.concatenate(buf, axis=0).flatten().astype(np.float32)
                    self.chunk_queue.put(chunk)
                    buf = []
                    acc = 0
        return buf, acc

    def _record_loop(self) -> None:
        frames_per_chunk = self.cfg.samplerate * self.cfg.chunk_seconds
        buf: list[np.ndarray] = []
        acc = 0
        with sf.SoundFile(
            self.out_path,
            mode="w",
            samplerate=self.cfg.samplerate,
            channels=self.cfg.channels,
            subtype="PCM_16",
        ) as wav:
            while not self._stop.is_set():
                try:
                    buf, acc = self._record_session(wav, buf, acc, frames_per_chunk)
                    break  # salida limpia
                except RuntimeError as e:
                    if not self.cfg.auto_recover or self._stop.is_set():
                        raise
                    mm = int((wav.frames / self.cfg.samplerate) // 60)
                    ss = int((wav.frames / self.cfg.samplerate) % 60)
                    self._warn(
                        f"Se perdió la salida de audio cerca de {mm:02d}:{ss:02d}. "
                        "Se intentará reconectar en 2 segundos."
                    )
                    time.sleep(2)
                    continue
            # flush residual
            if buf:
                chunk = np.concatenate(buf, axis=0).flatten().astype(np.float32)
                self.chunk_queue.put(chunk)

    def _run(self) -> None:
        try:
            self._record_loop()
        except BaseException as exc:
            self.error = exc
        finally:
            self.chunk_queue.put(None)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def extract_audio_from_file(src: Path, dst: Path, samplerate: int = 16000) -> None:
    """ffmpeg: mp4/mkv/mp3/etc → wav 16k mono PCM16."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(samplerate),
        "-c:a", "pcm_s16le", str(dst),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{r.stderr}")


def load_wav_mono(path: Path, samplerate: int = 16000) -> np.ndarray:
    data, sr = sf.read(str(path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != samplerate:
        raise ValueError(f"sr mismatch: {sr} != {samplerate}. Pasa por ffmpeg primero.")
    return data
