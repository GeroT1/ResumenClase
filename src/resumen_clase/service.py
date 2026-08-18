"""Capa de servicio: lógica reusable entre CLI y GUI."""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

from .audio import LoopbackRecorder, extract_audio_from_file
from .config import Config, safe_path_component
from .references import build_reference_block, resolve_all_references
from .summarize import check_ollama, summarize
from .transcribe import format_segments, load_whisper, transcribe_array, transcribe_file


@dataclass
class SessionResult:
    transcript_path: Path
    plain_path: Path
    summary_path: Path | None
    audio_path: Path | None
    transcript: str
    summary: str | None
    elapsed_seconds: float


def timestamp_stem() -> str:
    return f"clase-{datetime.now().strftime('%m-%d')}"


def _cleanup_stem_files(cfg: Config, stem: str) -> None:
    for folder in ["audio", "transcripts", "summaries"]:
        d = cfg.artifact_dir(folder)
        if d.exists():
            for f in d.glob(f"*{stem}*"):
                try:
                    f.unlink()
                except Exception:
                    pass


def _write_outputs(
    cfg: Config, stem: str, transcript: str, segs: list[dict], summary: str | None
) -> tuple[Path, Path, Path | None]:
    stem = safe_path_component(stem, timestamp_stem())
    t_dir = cfg.artifact_dir("transcripts")
    t_path = t_dir / f"{stem}.txt"
    t_path.write_text(format_segments(segs) if segs else transcript, encoding="utf-8")

    plain_path = t_dir / f"{stem}.plain.txt"
    plain_path.write_text(transcript, encoding="utf-8")

    s_path: Path | None = None
    if summary:
        s_dir = cfg.artifact_dir("summaries")
        s_path = s_dir / f"{stem}.md"
        s_path.write_text(summary, encoding="utf-8")
    return t_path, plain_path, s_path


class LiveSession:
    """Sesión de grabación en vivo con callbacks para la UI.

    Uso:
        s = LiveSession(cfg, "mi-clase")
        s.on_chunk = lambda text, offset: ...
        s.start()
        # ... tiempo pasa ...
        result = s.stop_and_finalize(generate_summary=True)
    """

    def __init__(self, cfg: Config, stem: str,
                 extra_files: list[Path] | None = None) -> None:
        self.cfg = cfg
        self.stem = safe_path_component(stem, timestamp_stem())
        self.extra_files = extra_files or []
        audio_dir = cfg.artifact_dir("audio")
        self.audio_path = audio_dir / f"{self.stem}.wav"
        self.recorder = LoopbackRecorder(cfg.audio, self.audio_path)
        self.all_text: list[str] = []
        self.all_segs: list[dict] = []
        self.offset = 0.0
        self._stopping = False
        self._cancelled = False
        self._started_at = 0.0
        self.debug_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        log.info("live[%s]: preparada materia=%s stem=%s", self.debug_id, cfg.active_subject, stem)

        # Callbacks (la UI los reemplaza)
        self.on_chunk: Callable[[str, float], None] = lambda t, o: None
        self.on_status: Callable[[str], None] = lambda s: None
        self.on_summary_progress: Callable[[str], None] = lambda s: None
        self.on_warning: Callable[[str], None] = lambda message: None
        self.recorder.on_warning = lambda message: self.on_warning(message)

    def elapsed(self) -> float:
        return time.time() - self._started_at if self._started_at else 0.0

    def start(self) -> None:
        self._started_at = time.time()
        log.info("live[%s]: inicio", self.debug_id)
        self.recorder.start()
        self.on_status("recording")

    def set_paused(self, paused: bool) -> None:
        self.recorder.set_paused(paused)
        self.on_status("paused" if paused else "recording")

    def process_chunks_until_stop(self) -> None:
        """Loop bloqueante: transcribe chunks hasta que se llame stop().
        Correr en un thread separado."""
        from .transcribe import WhisperModel  # noqa: F401

        with load_whisper(self.cfg.whisper) as model:
            while not self._stopping:
                try:
                    chunk = self.recorder.chunk_queue.get(timeout=0.5)
                except Exception:
                    continue
                if chunk is None:
                    break
                self._transcribe_chunk(model, chunk)
            # cancelado: no drenar nada, salir ya
            if self._cancelled:
                return
            # drenar lo que quede para no perder los últimos segundos
            while True:
                try:
                    chunk = self.recorder.chunk_queue.get(timeout=1.0)
                except Exception:
                    break
                if chunk is None:
                    break
                self._transcribe_chunk(model, chunk)
            if self.recorder.error is not None:
                raise RuntimeError(f"Falló la captura de audio: {self.recorder.error}") from self.recorder.error

    def _transcribe_chunk(self, model, chunk) -> None:
        max_amp = float(np.max(np.abs(chunk))) if len(chunk) > 0 else 0.0
        if max_amp == 0.0:
            self._consecutive_silent_chunks = getattr(self, "_consecutive_silent_chunks", 0) + 1
            if self._consecutive_silent_chunks == 2:
                dev = self.cfg.audio.device_name or "Predeterminado de Windows"
                self.on_warning(
                    f"No se detecta señal de audio en '{dev}'. "
                    "Verificá que el sonido esté saliendo por ese dispositivo o elegí 'Predeterminado de Windows' en Ajustes."
                )
        else:
            self._consecutive_silent_chunks = 0

        text, segs = transcribe_array(model, chunk, self.cfg.whisper)
        if text:
            self.all_text.append(text)
            for s in segs:
                s["start"] += self.offset
                s["end"] += self.offset
                self.all_segs.append(s)
        self.offset += self.cfg.audio.chunk_seconds
        try:
            current_transcript = " ".join(self.all_text).strip()
            _write_outputs(self.cfg, self.stem, current_transcript, self.all_segs, summary=None)
        except Exception as exc:
            log.warning("live[%s]: no se pudo auto-guardar transcripción parcial: %s", self.debug_id, exc)
        self.on_chunk(text, self.offset)

    def request_stop(self) -> None:
        """Marca la sesión para parar. La llamás desde la UI cuando el usuario
        apreta el botón de stop."""
        self._stopping = True
        log.info("live[%s]: parada solicitada tras %.1fs", self.debug_id, self.elapsed())
        self.recorder.stop()
        self.on_status("stopping")

    def request_cancel(self) -> None:
        """Cancela la sesión: corta la grabación y descarta TODO sin transcribir
        ni resumir. El loop de transcripción saldrá inmediatamente."""
        self._cancelled = True
        log.info("live[%s]: cancelada tras %.1fs", self.debug_id, self.elapsed())
        self._stopping = True
        try:
            self.recorder.stop()
        except Exception:
            pass
        # vaciar la cola para que el loop salga rápido
        try:
            while True:
                self.recorder.chunk_queue.get_nowait()
        except Exception:
            pass
        self.on_status("cancelling")

    def discard(self) -> None:
        """Borra los archivos y limpia el estado tras una cancelación."""
        _cleanup_stem_files(self.cfg, self.stem)
        self.all_text.clear()
        self.all_segs.clear()

    def finalize(self, generate_summary: bool = True) -> SessionResult:
        """Llamar DESPUÉS de que process_chunks_until_stop() termine."""
        transcript = " ".join(self.all_text).strip()

        # Guardar transcripción temprana por si el resumen crashea
        _write_outputs(self.cfg, self.stem, transcript, self.all_segs, summary=None)

        summary = None
        if generate_summary and transcript:
            if check_ollama(self.cfg.llm):
                self.on_status("summarizing")
                ref_paths = resolve_all_references(
                    self.cfg.config_path, self.cfg.active_subject, self.extra_files,
                    progress_cb=self.on_status,
                )
                if ref_paths:
                    self.on_status(f"Cargando material de apoyo ({len(ref_paths)} archivo/s)...")
                ref_block = build_reference_block(ref_paths, progress_cb=self.on_status)
                summary = summarize(transcript, self.cfg.llm, self.cfg.summary_prompt(),
                                    progress_cb=self.on_status,
                                    extra_context=ref_block)
            else:
                self.on_status("ollama_unavailable")
        t_path, p_path, s_path = _write_outputs(
            self.cfg, self.stem, transcript, self.all_segs, summary
        )
        audio_path = self.audio_path if self.cfg.output.save_audio else None
        if not self.cfg.output.save_audio:
            self.audio_path.unlink(missing_ok=True)
        self.on_status("done")
        return SessionResult(
            transcript_path=t_path,
            plain_path=p_path,
            summary_path=s_path,
            audio_path=audio_path,
            transcript=transcript,
            summary=summary,
            elapsed_seconds=self.elapsed(),
        )


def combine_sources(
    cfg: Config,
    sources: list[Path],
    stem: str | None = None,
    generate_summary: bool = True,
    progress_cb: Callable[[str], None] | None = None,
    extra_files: list[Path] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> SessionResult:
    """Combina múltiples fuentes (audios, transcripts o resúmenes previos) en
    un único resumen consolidado. El LLM reconcilia duplicados y rellena gaps
    usando el contexto completo de todas las partes."""
    AUDIO_EXTS = {".wav", ".mp3", ".mp4", ".mkv", ".m4a", ".ogg", ".flac", ".webm"}
    TRANSCRIPT_EXTS = {".txt"}
    SUMMARY_EXTS = {".md"}

    debug_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = safe_path_component(
        stem or cfg.unique_stem(f"combinado-{datetime.now().strftime('%m-%d')}"),
        "combinado",
    )
    log.info("combine[%s]: materia=%s stem=%s fuentes=%d", debug_id, cfg.active_subject, stem, len(sources))
    out = cfg.output_dir()
    t0 = time.time()
    all_segs: list[dict] = []
    all_text_parts: list[str] = []
    offset = 0.0

    def _check_cancel():
        if is_cancelled and is_cancelled():
            raise InterruptedError("Cancelado por el usuario")

    try:

        # Clasificar: si todas las fuentes son resúmenes (.md), usamos prompt especial
        only_summaries = all(s.suffix.lower() in SUMMARY_EXTS for s in sources)

        # Cargar modelo una sola vez si hay audios para transcribir
        audios = [s for s in sources if s.suffix.lower() in AUDIO_EXTS]

        def _notify(msg: str) -> None:
            if progress_cb:
                progress_cb(msg)

        def _label(idx: int, src: Path) -> str:
            ext = src.suffix.lower()
            kind = "transcript" if ext in TRANSCRIPT_EXTS else "resumen previo" if ext in SUMMARY_EXTS else "grabación"
            return f"\n\n===== PARTE {idx}: {src.name} ({kind}) =====\n\n"

        if audios:
            _notify(f"Cargando Whisper ({cfg.whisper.model})...")
            with load_whisper(cfg.whisper) as model:
                for idx, src in enumerate(sources, 1):
                    ext = src.suffix.lower()
                    if ext in TRANSCRIPT_EXTS or ext in SUMMARY_EXTS:
                        _notify(f"[{idx}/{len(sources)}] Leyendo: {src.name}")
                        all_text_parts.append(_label(idx, src))
                        all_text_parts.append(src.read_text(encoding="utf-8"))
                        continue
                    if ext not in AUDIO_EXTS:
                        _notify(f"⚠ Ignorando {src.name} (extensión no soportada)")
                        continue
                    # convertir a wav si hace falta
                    if ext == ".wav":
                        wav = src
                    else:
                        audio_dir = cfg.artifact_dir("audio")
                        wav = audio_dir / f"{stem}__part{idx}.wav"
                        _notify(f"[{idx}/{len(sources)}] ffmpeg: {src.name}")
                        extract_audio_from_file(src, wav, cfg.audio.samplerate)
                    _notify(f"[{idx}/{len(sources)}] Transcribiendo: {src.name}")
                    _check_cancel()
                    text, segs = transcribe_file(
                        model, wav, cfg.whisper,
                        progress_cb=_notify,
                        base_msg=f"[{idx}/{len(sources)}] Transcribiendo: {src.name}",
                        is_cancelled=is_cancelled,
                    )
                    all_text_parts.append(_label(idx, src))
                    if text:
                        all_text_parts.append(text)
                        for s in segs:
                            s["start"] += offset
                            s["end"] += offset
                            all_segs.append(dict(s))
                        import soundfile as sf
                        try:
                            info = sf.info(str(wav))
                            offset += info.duration
                        except Exception:
                            offset += segs[-1]["end"] if segs else 0
        else:
            for idx, src in enumerate(sources, 1):
                _notify(f"[{idx}/{len(sources)}] Leyendo: {src.name}")
                all_text_parts.append(_label(idx, src))
                all_text_parts.append(src.read_text(encoding="utf-8"))

        transcript = " ".join(all_text_parts).strip()

        # Guardar transcripción temprana
        _write_outputs(cfg, stem, transcript, all_segs, summary=None)

        summary = None
        if generate_summary and transcript:
            if check_ollama(cfg.llm):
                _notify(f"Resumiendo con {cfg.llm.model}...")
                base_prompt = cfg.summary_prompt()
                if only_summaries:
                    combine_hint = (
                        "\n\n--- CONTEXTO DE COMBINACIÓN ---\n"
                        "Los datos que recibís NO son una transcripción cruda: son varios "
                        "resúmenes previos de partes distintas de la MISMA clase (posiblemente "
                        "cortada en trozos por problemas técnicos). Tu tarea es generar UN único "
                        "resumen consolidado y coherente, aplicando estas reglas:\n"
                        "- Reconciliá información duplicada entre partes — no la repitas.\n"
                        "- Si dos partes tienen la misma idea con distinto nivel de detalle, "
                        "usá la versión más completa.\n"
                        "- Si una parte aclara o corrige algo de otra, priorizá lo más reciente "
                        "o lo más detallado.\n"
                        "- Detectá gaps (temas que se interrumpen en una parte y continúan en otra) "
                        "y unificalos en una explicación fluida.\n"
                        "- No inventes contenido que no esté en ninguna parte.\n"
                        "- El resultado debe leerse como si fuera el resumen de una clase única y continua."
                    )
                else:
                    combine_hint = (
                        "\n\n--- CONTEXTO DE COMBINACIÓN ---\n"
                        "Los datos incluyen múltiples partes de la MISMA clase (posiblemente "
                        "cortada por problemas técnicos). Cada parte está marcada con "
                        "===== PARTE N ===== . Generá UN único resumen consolidado, reconciliando "
                        "información repetida y uniendo temas que se cortan entre partes. "
                        "No inventes contenido."
                    ) if len(sources) > 1 else ""
                full_prompt = base_prompt + combine_hint
                ref_paths = resolve_all_references(
                    cfg.config_path, cfg.active_subject, extra_files,
                    progress_cb=_notify,
                )
                if ref_paths:
                    _notify(f"Cargando material de apoyo ({len(ref_paths)} archivo/s)...")
                ref_block = build_reference_block(ref_paths, progress_cb=_notify)
                log.info("combine: llamando a summarize() — transcript=%d chars, ref=%d chars",
                         len(transcript), len(ref_block))
                _check_cancel()
                summary = summarize(transcript, cfg.llm, full_prompt,
                                    progress_cb=_notify,
                                    extra_context=ref_block,
                                    is_cancelled=is_cancelled)
                log.info("combine: summarize() retornó (%d chars)", len(summary or ""))
            else:
                _notify("⚠ Ollama no responde — sin resumen")

        _check_cancel()
        log.info("combine: escribiendo outputs...")
        t_path, p_path, s_path = _write_outputs(cfg, stem, transcript, all_segs, summary)
        log.info("combine: outputs escritos OK")
        return SessionResult(
            transcript_path=t_path,
            plain_path=p_path,
            summary_path=s_path,
            audio_path=None,
            transcript=transcript,
            summary=summary,
            elapsed_seconds=time.time() - t0,
        )

    except InterruptedError:
        _cleanup_stem_files(cfg, stem)
        raise


def process_file(
    cfg: Config, source: Path, stem: str | None = None, generate_summary: bool = True,
    extra_files: list[Path] | None = None,
    progress_cb: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> SessionResult:
    """Procesa archivo de audio/video existente."""
    stem = safe_path_component(stem or cfg.unique_stem(timestamp_stem()), timestamp_stem())
    log.info("file[%s]: materia=%s stem=%s origen=%s",
             datetime.now().strftime("%Y%m%d-%H%M%S"), cfg.active_subject, stem, source.name)
    out = cfg.output_dir()
    t0 = time.time()

    def _check_cancel():
        if is_cancelled and is_cancelled():
            raise InterruptedError("Cancelado por el usuario")

    try:
        if source.suffix.lower() == ".wav":
            wav = source
            managed_wav = False
        else:
            audio_dir = cfg.artifact_dir("audio")
            wav = audio_dir / f"{stem}.wav"
            extract_audio_from_file(source, wav, cfg.audio.samplerate)
            managed_wav = True
        with load_whisper(cfg.whisper) as model:
            _check_cancel()
            transcript, segs = transcribe_file(
                model, wav, cfg.whisper,
                progress_cb=progress_cb,
                base_msg=f"Transcribiendo: {source.name}",
                is_cancelled=is_cancelled,
            )

        # Guardar transcripción temprana
        _write_outputs(cfg, stem, transcript, segs, summary=None)

        summary = None
        if generate_summary and transcript and check_ollama(cfg.llm):
            _check_cancel()
            ref_paths = resolve_all_references(
                cfg.config_path, cfg.active_subject, extra_files, progress_cb=progress_cb
            )
            ref_block = build_reference_block(ref_paths)
            summary = summarize(transcript, cfg.llm, cfg.summary_prompt(),
                                progress_cb=None, extra_context=ref_block,
                                is_cancelled=is_cancelled)

        _check_cancel()
        t_path, p_path, s_path = _write_outputs(cfg, stem, transcript, segs, summary)
        audio_path: Path | None = None
        if cfg.output.save_audio:
            if managed_wav:
                audio_path = wav
            else:
                audio_path = cfg.artifact_dir("audio") / f"{stem}.wav"
                if source.resolve() != audio_path.resolve():
                    shutil.copy2(source, audio_path)
        elif managed_wav:
            wav.unlink(missing_ok=True)
        return SessionResult(
            transcript_path=t_path,
            plain_path=p_path,
            summary_path=s_path,
            audio_path=audio_path,
            transcript=transcript,
            summary=summary,
            elapsed_seconds=time.time() - t0,
        )

    except InterruptedError:
        _cleanup_stem_files(cfg, stem)
        raise
