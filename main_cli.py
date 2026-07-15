"""CLI: live (Meet loopback) + file (grabación prof)."""
from __future__ import annotations

import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel

# Agregar src al path para imports absolutos del paquete
ROOT = Path(__file__).resolve().parent
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from resumen_clase.audio import (
    LoopbackRecorder,
    extract_audio_from_file,
    list_devices,
    load_wav_mono,
)
from resumen_clase.config import Config, safe_path_component
from resumen_clase.summarize import check_ollama, summarize
from resumen_clase.transcribe import format_segments, load_whisper, transcribe_array, transcribe_file


def _setup_logging(verbose: bool = True) -> None:
    """Configura logging para que los log.info() de summarize.py (telemetría
    t/s, progreso de map/reduce) sean visibles en consola."""
    if logging.getLogger().handlers:
        return  # ya configurado
    handler = RichHandler(rich_tracebacks=True, show_path=False, show_time=False,
                          markup=False)
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[handler],
    )
    # Bajar ruido de libs
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


app = typer.Typer(help="Transcribe + resume clases virtuales con IA local.")
console = Console()


@app.callback()
def _global(
    quiet: bool = typer.Option(False, "--quiet", help="Silencia logs INFO."),
) -> None:
    """Setup global aplicado antes de cualquier subcomando."""
    _setup_logging(verbose=not quiet)


def _timestamp() -> str:
    return datetime.now().strftime("%m-%d")


def _write_outputs(
    cfg: Config,
    stem: str,
    transcript_plain: str,
    segs: list[dict],
    summary: str | None,
) -> tuple[Path, Path, Path | None]:
    t_path = cfg.artifact_dir("transcripts") / f"{stem}.txt"
    t_path.write_text(format_segments(segs) if segs else transcript_plain, encoding="utf-8")
    plain_path = cfg.artifact_dir("transcripts") / f"{stem}.plain.txt"
    plain_path.write_text(transcript_plain, encoding="utf-8")
    s_path: Path | None = None
    if summary:
        s_path = cfg.artifact_dir("summaries") / f"{stem}.md"
        s_path.write_text(summary, encoding="utf-8")
    return t_path, plain_path, s_path


def _pick_subject(cfg: Config, subject: str | None) -> str:
    """Si no se pasó subject, muestra menú interactivo y retorna la key elegida."""
    names = cfg.subject_names()
    if not names:
        return ""
    keys = list(names.keys())
    if subject:
        if subject not in names:
            console.print(f"[red]Materia '{subject}' no existe. Opciones: {', '.join(keys)}[/red]")
            raise typer.Exit(1)
        return subject
    if len(keys) == 1:
        return keys[0]
    console.print("\n[bold]¿Qué materia es?[/bold]")
    for i, k in enumerate(keys, 1):
        console.print(f"  [cyan]{i}[/cyan]. {names[k]}")
    while True:
        choice = input(f"Elegí [1-{len(keys)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            return keys[int(choice) - 1]
        console.print("[yellow]Opción inválida, intentá de nuevo.[/yellow]")


@app.command()
def devices() -> None:
    """Lista dispositivos audio."""
    list_devices()


@app.command()
def live(
    config: Path = typer.Option("config.yaml", "--config", "-c"),
    no_summary: bool = typer.Option(False, "--no-summary", help="Solo transcripción."),
    name: str | None = typer.Option(None, "--name", "-n", help="Nombre base output."),
    subject: str | None = typer.Option(None, "--subject", "-s", help="Clave de materia (ej: octave, redes)."),
) -> None:
    """Graba loopback Meet → transcribe en vivo → resume al parar (Ctrl+C)."""
    cfg = Config.load(config)
    chosen = _pick_subject(cfg, subject)
    cfg.active_subject = chosen
    stem = safe_path_component(name, "clase") if name else cfg.unique_stem(f"clase-{_timestamp()}")
    audio_path = cfg.artifact_dir("audio") / f"{stem}.wav"

    console.print(Panel.fit(
        f"[bold cyan]LIVE MODE[/bold cyan]\n"
        f"Out: {audio_path}\n"
        f"Ctrl+C para parar y generar resumen.",
        border_style="cyan",
    ))

    rec = LoopbackRecorder(cfg.audio, audio_path)
    all_text: list[str] = []
    all_segs: list[dict] = []
    offset = 0.0

    rec.start()
    console.print("[green]🎙  Grabando... (Ctrl+C para parar)[/green]")

    try:
        with load_whisper(cfg.whisper) as model:
            with Live(console=console, refresh_per_second=2) as live_view:
                while True:
                    try:
                        chunk = rec.chunk_queue.get(timeout=0.5)
                    except Exception:
                        continue
                    if chunk is None:
                        break
                    text, segs = transcribe_array(model, chunk, cfg.whisper)
                    if text:
                        all_text.append(text)
                        for s in segs:
                            s["start"] += offset
                            s["end"] += offset
                            all_segs.append(s)
                    else:
                        mm, ss = int(offset // 60), int(offset % 60)
                        all_text.append(f"[--- corte de audio {mm:02d}:{ss:02d} ---]")
                    offset += cfg.audio.chunk_seconds
                    tail = " ".join(all_text)[-800:]
                    live_view.update(Panel(tail, title=f"Transcript ({offset:.0f}s)", border_style="green"))
    except KeyboardInterrupt:
        console.print("\n[yellow]Parando grabación...[/yellow]")
        rec.stop()
        # drenar resto del buffer
        with load_whisper(cfg.whisper) as model:
            while True:
                try:
                    chunk = rec.chunk_queue.get(timeout=1.0)
                except Exception:
                    break
                if chunk is None:
                    break
                text, segs = transcribe_array(model, chunk, cfg.whisper)
                if text:
                    all_text.append(text)
                    for s in segs:
                        s["start"] += offset
                        s["end"] += offset
                        all_segs.append(s)
                else:
                    mm, ss = int(offset // 60), int(offset % 60)
                    all_text.append(f"[--- corte de audio {mm:02d}:{ss:02d} ---]")

    transcript = " ".join(all_text).strip()
    console.print(f"\n[bold]Transcript listo:[/bold] {len(transcript)} chars")

    summary = None
    if not no_summary and transcript:
        if not check_ollama(cfg.llm):
            console.print("[yellow]⚠ Ollama no responde. Saltando resumen.[/yellow]")
        else:
            console.print(f"[cyan]Resumiendo con {cfg.llm.model}...[/cyan]")
            t0 = time.time()
            summary = summarize(transcript, cfg.llm, cfg.summary_prompt())
            console.print(f"[green]Resumen listo en {time.time() - t0:.1f}s[/green]")

    t, p, s = _write_outputs(cfg, stem, transcript, all_segs, summary)
    console.print(f"\n[bold green]✓[/bold green] Transcript: {t}")
    console.print(f"[bold green]✓[/bold green] Plain:      {p}")
    if s:
        console.print(f"[bold green]✓[/bold green] Resumen:    {s}")
    if not cfg.output.save_audio:
        audio_path.unlink(missing_ok=True)


@app.command()
def file(
    source: Path = typer.Argument(..., exists=True, help="mp4/mkv/mp3/wav de la clase."),
    config: Path = typer.Option("config.yaml", "--config", "-c"),
    no_summary: bool = typer.Option(False, "--no-summary"),
    name: str | None = typer.Option(None, "--name", "-n"),
    subject: str | None = typer.Option(None, "--subject", "-s", help="Clave de materia (ej: octave, redes)."),
) -> None:
    """Procesa grabación existente del profesor."""
    cfg = Config.load(config)
    chosen = _pick_subject(cfg, subject)
    cfg.active_subject = chosen
    stem = safe_path_component(name, "clase") if name else cfg.unique_stem(f"clase-{_timestamp()}")
    out = cfg.output_dir()

    # convertir a wav si hace falta
    if source.suffix.lower() == ".wav":
        wav = source
        managed_wav = False
    else:
        wav = cfg.artifact_dir("audio") / f"{stem}.wav"
        console.print(f"[cyan]ffmpeg → {wav.name}[/cyan]")
        extract_audio_from_file(source, wav, cfg.audio.samplerate)
        managed_wav = True

    console.print(f"[cyan]Transcribiendo {wav.name} con {cfg.whisper.model}...[/cyan]")
    t0 = time.time()
    with load_whisper(cfg.whisper) as model:
        transcript, segs = transcribe_file(model, wav, cfg.whisper)
    console.print(f"[green]Transcript listo en {time.time() - t0:.1f}s ({len(transcript)} chars)[/green]")

    summary = None
    if not no_summary and transcript:
        if not check_ollama(cfg.llm):
            console.print("[yellow]⚠ Ollama no responde. Saltando resumen.[/yellow]")
        else:
            console.print(f"[cyan]Resumiendo con {cfg.llm.model}...[/cyan]")
            t0 = time.time()
            summary = summarize(transcript, cfg.llm, cfg.summary_prompt())
            console.print(f"[green]Resumen listo en {time.time() - t0:.1f}s[/green]")

    t, p, s = _write_outputs(cfg, stem, transcript, segs, summary)
    console.print(f"\n[bold green]✓[/bold green] Transcript: {t}")
    console.print(f"[bold green]✓[/bold green] Plain:      {p}")
    if s:
        console.print(f"[bold green]✓[/bold green] Resumen:    {s}")
    if cfg.output.save_audio and not managed_wav:
        audio_copy = cfg.artifact_dir("audio") / f"{stem}.wav"
        if source.resolve() != audio_copy.resolve():
            shutil.copy2(source, audio_copy)
    elif not cfg.output.save_audio and managed_wav:
        wav.unlink(missing_ok=True)


@app.command()
def combine(
    sources: list[Path] = typer.Argument(..., help="Archivos a combinar (wav/mp4/txt/etc)."),
    config: Path = typer.Option("config.yaml", "--config", "-c"),
    no_summary: bool = typer.Option(False, "--no-summary"),
    name: str | None = typer.Option(None, "--name", "-n"),
    subject: str | None = typer.Option(None, "--subject", "-s"),
) -> None:
    """Combina múltiples grabaciones/transcripts en un único resumen.

    Ejemplos:
        resumen combine parte1.wav parte2.wav -n clase-completa
        resumen combine clase1.plain.txt clase2.plain.txt -s octave
    """
    from resumen_clase.service import combine_sources
    cfg = Config.load(config)
    cfg.active_subject = _pick_subject(cfg, subject)
    for s in sources:
        if not s.exists():
            console.print(f"[red]No existe: {s}[/red]")
            raise typer.Exit(1)
    console.print(f"[cyan]Combinando {len(sources)} archivos...[/cyan]")
    result = combine_sources(
        cfg, sources, stem=name, generate_summary=not no_summary,
        progress_cb=lambda m: console.print(f"  [dim]{m}[/dim]"),
    )
    console.print(f"\n[bold green]✓[/bold green] Transcript: {result.transcript_path}")
    console.print(f"[bold green]✓[/bold green] Plain:      {result.plain_path}")
    if result.summary_path:
        console.print(f"[bold green]✓[/bold green] Resumen:    {result.summary_path}")
    from resumen_clase.utils import fmt_duration
    console.print(f"[dim]Tiempo total: {fmt_duration(result.elapsed_seconds)}[/dim]")


@app.command()
def summarize_only(
    transcript: Path = typer.Argument(..., exists=True),
    config: Path = typer.Option("config.yaml", "--config", "-c"),
    name: str | None = typer.Option(None, "--name", "-n"),
    subject: str | None = typer.Option(None, "--subject", "-s"),
) -> None:
    """Resume un .txt ya transcripto."""
    cfg = Config.load(config)
    cfg.active_subject = _pick_subject(cfg, subject)
    text = transcript.read_text(encoding="utf-8")
    if not check_ollama(cfg.llm):
        console.print("[red]Ollama no responde.[/red]")
        raise typer.Exit(1)
    console.print(f"[cyan]Resumiendo con {cfg.llm.model}...[/cyan] [dim]({len(text)} chars)[/dim]")
    t0 = time.time()
    s = summarize(text, cfg.llm, cfg.summary_prompt(),
                  progress_cb=lambda m: console.print(f"  [dim]{m}[/dim]"))
    console.print(f"[green]Resumen listo en {time.time() - t0:.1f}s[/green]")
    stem = safe_path_component(name or transcript.stem, "resumen")
    out = cfg.artifact_dir("summaries") / f"{stem}.md"
    out.write_text(s, encoding="utf-8")
    console.print(f"[bold green]✓[/bold green] {out}")


@app.command()
def diagnose(
    config: Path = typer.Option("config.yaml", "--config", "-c"),
) -> None:
    """Muestra estado de GPU/VRAM y modelos cargados en Ollama."""
    from resumen_clase.monitor import diagnose as _diag
    cfg = Config.load(config)
    console.print(Panel.fit(_diag(cfg.llm.host),
                            title="Diagnóstico", border_style="cyan"))


@app.command()
def batch(
    sources: list[Path] = typer.Argument(None, help="Archivos a encolar (opcional)."),
    config: Path = typer.Option("config.yaml", "--config", "-c"),
    queue_dir: Path = typer.Option(Path("./queue"), "--queue", "-q",
                                   help="Directorio de la cola JSONL."),
    subject: str | None = typer.Option(None, "--subject", "-s"),
    retry_failed: bool = typer.Option(False, "--retry-failed",
                                      help="Reintenta tareas en failed.jsonl."),
    list_only: bool = typer.Option(False, "--list",
                                   help="Solo muestra estado, no procesa."),
    no_warmup: bool = typer.Option(False, "--no-warmup",
                                   help="Salta el warmup del modelo."),
) -> None:
    """Procesa una cola JSONL reanudable.

    Encolá archivos y ejecutá. Si se interrumpe, volvé a correrlo: salta lo
    ya hecho. Cada tarea queda persistida en done.jsonl al instante.

    Ejemplos:
      resumen batch clase1.wav clase2.wav -s octave    # encola y procesa
      resumen batch --list                              # solo ver estado
      resumen batch                                     # procesa pendientes
      resumen batch --retry-failed                      # reintenta fallidos
    """
    from resumen_clase.queue import TaskQueue, process_queue
    from resumen_clase.monitor import GpuMonitor, diagnose as _diag

    cfg = Config.load(config)
    if subject:
        cfg.active_subject = _pick_subject(cfg, subject)

    q = TaskQueue(queue_dir)
    if sources:
        added = q.add_files(sources, subject=cfg.active_subject or "")
        console.print(f"[cyan]Encoladas {len(added)} tareas[/cyan]")

    st = q.status()
    console.print(
        f"[bold]Cola:[/bold] total={st['total']} · done={st['done']} "
        f"· failed={st['failed']} · pending={st['pending']}"
    )

    if list_only:
        return

    if st["pending"] == 0 and not retry_failed:
        console.print("[green]Nada por procesar.[/green]")
        return

    console.print(Panel.fit(_diag(cfg.llm.host),
                            title="Estado pre-batch", border_style="cyan"))

    with GpuMonitor(interval=2.0) as gm:
        counts = process_queue(
            queue_dir, config,
            progress_cb=lambda m: console.print(f"  [dim]{m}[/dim]"),
            retry_failed=retry_failed,
            warmup_model=not no_warmup,
        )

    console.print(
        f"\n[bold green]✓[/bold green] OK: {counts['ok']} · "
        f"[bold red]✗[/bold red] Failed: {counts['failed']}"
    )
    console.print(f"[dim]{gm.summary()}[/dim]")


if __name__ == "__main__":
    app()
