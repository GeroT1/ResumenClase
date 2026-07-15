"""Cola JSONL reanudable para procesar lotes de archivos.

Diseño:

  tasks.jsonl   ← INPUT. Una línea por tarea. Append-only desde fuera.
                  Formato: {"id": "<id único>", "source": "<path>",
                            "subject": "<key>", "name": "<stem opcional>"}
                  Se reescribe sólo si el usuario llama a `add()`.

  done.jsonl    ← STATE. Append-only. Una línea por tarea completada con éxito.
                  Formato: {"id": "...", "ok": true, "summary_path": "...",
                            "transcript_path": "...", "elapsed_seconds": ...,
                            "ts": "<isoformat>"}

  failed.jsonl  ← STATE. Append-only. Tareas que fallaron (con error).
                  Formato: {"id": "...", "ok": false, "error": "...",
                            "ts": "<isoformat>"}

Reanudación: al iniciar `process_queue()`, leemos `done.jsonl` y `failed.jsonl`
y filtramos esas IDs de `tasks.jsonl`. Cada tarea exitosa se appendea a
`done.jsonl` ANTES de pasar a la siguiente (atomicidad: si se cae a mitad,
sólo se pierde la tarea actual).

Por qué no "checkpoint cada 12": cada tarea ya es atómica y persistente al
instante. Un checkpoint cada 12 sería peor (perderías hasta 11 tareas si se
cae). Esto es estrictamente superior al benchmark.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)


@dataclass
class Task:
    id: str
    source: str           # path a wav/mp4/txt/etc
    subject: str = ""     # clave de materia (octave, redes, ...)
    name: str | None = None  # stem opcional para los outputs
    extra_files: list[str] = field(default_factory=list)


@dataclass
class TaskResult:
    id: str
    ok: bool
    summary_path: str | None = None
    transcript_path: str | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None
    ts: str = ""


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("línea inválida en %s: %s", path.name, e)
    return out


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Append atómico: una sola write+flush+fsync por línea.
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        try:
            import os
            os.fsync(f.fileno())
        except OSError:
            pass


class TaskQueue:
    """Wrapper sobre los tres JSONL. Idempotente y reanudable."""

    def __init__(self, queue_dir: Path) -> None:
        self.dir = Path(queue_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tasks_path  = self.dir / "tasks.jsonl"
        self.done_path   = self.dir / "done.jsonl"
        self.failed_path = self.dir / "failed.jsonl"

    # ── Construcción de la cola ────────────────────────────────────────────

    def add(self, task: Task) -> None:
        """Encola una tarea. Si la ID ya existe en tasks.jsonl, no la duplica."""
        existing = {t["id"] for t in _read_jsonl(self.tasks_path)}
        if task.id in existing:
            log.info("Task %s ya estaba encolada", task.id)
            return
        _append_jsonl(self.tasks_path, asdict(task))

    def add_files(self, files: Iterable[Path], subject: str = "") -> list[Task]:
        """Helper: encola una lista de archivos generando IDs estables a partir
        del path absoluto. Reencolar el mismo archivo es no-op."""
        added: list[Task] = []
        for p in files:
            p = Path(p).resolve()
            if not p.exists():
                log.warning("No existe, ignoro: %s", p)
                continue
            # ID estable: hash corto del path absoluto.
            import hashlib
            h = hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:10]
            task = Task(id=f"{p.stem}-{h}", source=str(p), subject=subject)
            self.add(task)
            added.append(task)
        return added

    # ── Lectura ────────────────────────────────────────────────────────────

    def all_tasks(self) -> list[Task]:
        return [Task(**t) for t in _read_jsonl(self.tasks_path)]

    def done_ids(self) -> set[str]:
        return {r["id"] for r in _read_jsonl(self.done_path) if r.get("ok")}

    def failed_ids(self) -> set[str]:
        return {r["id"] for r in _read_jsonl(self.failed_path)}

    def pending(self, retry_failed: bool = False) -> list[Task]:
        done = self.done_ids()
        skip = set(done)
        if not retry_failed:
            skip |= self.failed_ids()
        return [t for t in self.all_tasks() if t.id not in skip]

    def status(self) -> dict[str, int]:
        return {
            "total":   len(self.all_tasks()),
            "done":    len(self.done_ids()),
            "failed":  len(self.failed_ids()),
            "pending": len(self.pending()),
        }

    # ── Marcado ────────────────────────────────────────────────────────────

    def mark_done(self, result: TaskResult) -> None:
        result.ts = datetime.now().isoformat(timespec="seconds")
        result.ok = True
        _append_jsonl(self.done_path, asdict(result))

    def mark_failed(self, task_id: str, error: str) -> None:
        _append_jsonl(self.failed_path, {
            "id":    task_id,
            "ok":    False,
            "error": error,
            "ts":    datetime.now().isoformat(timespec="seconds"),
        })


# ─────────────────────────────────────────────────────────────────────────────
# Ejecutor
# ─────────────────────────────────────────────────────────────────────────────

def process_queue(
    queue_dir: Path,
    config_path: Path,
    progress_cb: Callable[[str], None] | None = None,
    retry_failed: bool = False,
    warmup_model: bool = True,
) -> dict[str, int]:
    """Procesa todas las tareas pendientes de la cola.

    - Reanuda automáticamente: salta lo que ya está en done.jsonl.
    - Cada tarea se persiste en done.jsonl ANTES de pasar a la siguiente.
    - Si una tarea falla, se loguea en failed.jsonl y se sigue con la próxima.
    - El warmup precarga el modelo para que la 1ra tarea no incluya tiempo de load.

    Devuelve {"ok": N, "failed": N, "skipped": N}.
    """
    from .config import Config
    from .service import process_file
    from .summarize import check_ollama, warmup

    q = TaskQueue(queue_dir)
    pending = q.pending(retry_failed=retry_failed)
    if not pending:
        if progress_cb:
            progress_cb("Cola vacía o todas las tareas ya completadas.")
        return {"ok": 0, "failed": 0, "skipped": 0}

    base_cfg = Config.load(config_path)
    if not check_ollama(base_cfg.llm):
        raise RuntimeError(f"Ollama no responde en {base_cfg.llm.host}")

    if warmup_model:
        if progress_cb:
            progress_cb(f"Precargando {base_cfg.llm.model}...")
        warmup(base_cfg.llm)

    counts = {"ok": 0, "failed": 0, "skipped": 0}
    for i, task in enumerate(pending, 1):
        prefix = f"[{i}/{len(pending)}] {task.id}"
        if progress_cb:
            progress_cb(f"{prefix}: arrancando...")
        try:
            cfg = Config.load(config_path, subject=task.subject)
            src = Path(task.source)
            if not src.exists():
                raise FileNotFoundError(f"source no existe: {src}")
            extras = [Path(p) for p in (task.extra_files or [])]
            t0 = time.time()
            result = process_file(
                cfg, src, stem=task.name, generate_summary=True,
                extra_files=extras,
                progress_cb=progress_cb,
            )
            elapsed = time.time() - t0
            q.mark_done(TaskResult(
                id=task.id, ok=True,
                summary_path=str(result.summary_path) if result.summary_path else None,
                transcript_path=str(result.transcript_path),
                elapsed_seconds=elapsed,
            ))
            counts["ok"] += 1
            if progress_cb:
                progress_cb(f"{prefix}: ✓ ({elapsed:.0f}s)")
        except Exception as e:
            log.exception("Falló task %s", task.id)
            q.mark_failed(task.id, f"{type(e).__name__}: {e}")
            counts["failed"] += 1
            if progress_cb:
                progress_cb(f"{prefix}: ✗ {e}")
    return counts
