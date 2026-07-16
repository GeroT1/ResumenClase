from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
import shutil
from typing import Any

import yaml


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_path_component(value: str, fallback: str = "archivo") -> str:
    """Normaliza un nombre para usarlo como un único componente de ruta."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(value).strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-_")
    cleaned = cleaned[:120].rstrip(" .") or fallback
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


@dataclass(slots=True)
class WhisperCfg:
    model: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "int8_float16"
    language: str | None = "es"
    vad_filter: bool = True
    beam_size: int = 5


@dataclass(slots=True)
class AudioCfg:
    samplerate: int = 16000
    chunk_seconds: int = 30
    channels: int = 1
    device_name: str = ""           # substring del nombre del speaker. "" = default
    auto_recover: bool = True       # reintenta si el device se invalida


@dataclass(slots=True)
class LLMCfg:
    backend: str = "ollama"
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct-q6_K"
    temperature: float = 0.3
    max_chunk_chars: int = 40000  # ~10k tokens. Con num_ctx=16384 en map entra justo.
    # ── Tuning de inferencia (calidad-first) ────────────────────────────────
    # Estos pasan a "options" del API de Ollama. Defaults pensados para
    # 8 GB VRAM + 32 GB RAM con qwen2.5:14b Q4_K_M.
    num_gpu: int = -1            # Capas a GPU. -1 = autodetect (Ollama elige max que entre).
                                  #            En 14B Q4_K_M con 8GB VRAM ronda 32-36/48.
    num_thread: int = 0          # Hilos CPU (0 = auto = todos los físicos).
    num_batch: int = 512         # Batch de prompt eval. 512 buen balance VRAM/velocidad.
    repeat_penalty: float = 1.05 # Penaliza repetición moderadamente sin distorsionar.
    top_p: float = 0.9           # Sampling: foco pero con algo de variación.
    top_k: int = 40
    chunk_overlap_chars: int = 6000  # ~15% solape entre chunks en map (mantiene contexto).
    log_telemetry: bool = True   # Loguear t/s y duración por llamada.


@dataclass(slots=True)
class OutputCfg:
    base_dir: str = "./output"
    save_audio: bool = True


@dataclass(slots=True)
class GuiCfg:
    theme: str = "midnight"
    setup_completed: bool = False


@dataclass(slots=True)
class Config:
    whisper: WhisperCfg = field(default_factory=WhisperCfg)
    audio: AudioCfg = field(default_factory=AudioCfg)
    llm: LLMCfg = field(default_factory=LLMCfg)
    output: OutputCfg = field(default_factory=OutputCfg)
    gui: GuiCfg = field(default_factory=GuiCfg)
    subjects: dict[str, dict] = field(default_factory=dict)
    active_subject: str = ""
    config_path: Path = field(default_factory=lambda: Path("config.yaml"))

    def summary_prompt(self) -> str:
        if self.active_subject and self.active_subject in self.subjects:
            return self.subjects[self.active_subject].get("summary_system", "")
        # fallback: primer subject disponible
        if self.subjects:
            return next(iter(self.subjects.values())).get("summary_system", "")
        return ""

    def subject_names(self) -> dict[str, str]:
        """Devuelve {key: name} de todos los subjects."""
        return {k: v.get("name", k) for k, v in self.subjects.items()}

    @classmethod
    def load(cls, path: str | Path = "config.yaml", subject: str = "") -> "Config":
        cfg_path = Path(path).resolve()
        data: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        return cls(
            whisper=WhisperCfg(**data.get("whisper", {})),
            audio=AudioCfg(**data.get("audio", {})),
            llm=LLMCfg(**data.get("llm", {})),
            output=OutputCfg(**data.get("output", {})),
            gui=GuiCfg(**data.get("gui", {})),
            subjects=data.get("subjects", {}),
            active_subject=subject,
            config_path=cfg_path,
        )

    def references_dir(self) -> Path:
        """Directorio raíz del material de apoyo: <config_dir>/referencias."""
        return self.config_path.parent / "referencias"

    def output_dir(self) -> Path:
        p = Path(self.output.base_dir)
        if not p.is_absolute():
            p = self.config_path.parent / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def subject_dir(self, subject: str | None = None) -> Path:
        key = safe_path_component(subject or self.active_subject or "sin_materia", "sin_materia")
        path = self.output_dir() / key
        path.mkdir(parents=True, exist_ok=True)
        return path

    def year_dir(self, subject: str | None = None, year: int | str | None = None) -> Path:
        value = str(year or datetime.now().year)
        path = self.subject_dir(subject) / value
        path.mkdir(parents=True, exist_ok=True)
        return path

    def artifact_dir(
        self, kind: str, subject: str | None = None, year: int | str | None = None
    ) -> Path:
        if kind not in {"audio", "transcripts", "summaries"}:
            raise ValueError(f"Tipo de salida inválido: {kind}")
        path = self.year_dir(subject, year) / kind
        path.mkdir(parents=True, exist_ok=True)
        return path

    def migrate_legacy_outputs(self) -> int:
        """Migra layouts anteriores a ``output/<materia>/<año>/<tipo>``."""
        base = self.output_dir()
        moved = 0
        subject_keys = set(self.subjects) | {"sin_materia"}
        for kind in ("audio", "transcripts", "summaries"):
            legacy = base / kind
            if not legacy.is_dir():
                continue
            for source in sorted(legacy.rglob("*")):
                if not source.is_file():
                    continue
                relative = source.relative_to(legacy)
                if len(relative.parts) > 1 and relative.parts[0] in subject_keys:
                    subject = relative.parts[0]
                    filename = Path(*relative.parts[1:]).name
                else:
                    filename = source.name
                    subject = next(
                        (key for key in self.subjects if filename.lower().startswith((f"{key}-", f"{key}_"))),
                        "sin_materia",
                    )
                file_year = datetime.fromtimestamp(source.stat().st_mtime).year
                destination = self.artifact_dir(kind, subject, file_year) / filename
                if destination.exists():
                    index = 1
                    while True:
                        candidate = destination.with_name(
                            f"{destination.stem}-legacy-{index}{destination.suffix}"
                        )
                        if not candidate.exists():
                            destination = candidate
                            break
                        index += 1
                shutil.move(str(source), str(destination))
                moved += 1
            for directory in sorted(
                (p for p in legacy.rglob("*") if p.is_dir()), reverse=True
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            try:
                legacy.rmdir()
            except OSError:
                pass
        # Segunda versión histórica: output/<materia>/<tipo>/archivo.
        for subject_path in [path for path in base.iterdir() if path.is_dir()]:
            for kind in ("audio", "transcripts", "summaries"):
                legacy = subject_path / kind
                if not legacy.is_dir():
                    continue
                for source in sorted(path for path in legacy.rglob("*") if path.is_file()):
                    file_year = datetime.fromtimestamp(source.stat().st_mtime).year
                    destination = self.artifact_dir(kind, subject_path.name, file_year) / source.name
                    if destination.exists():
                        index = 1
                        while True:
                            candidate = destination.with_name(
                                f"{destination.stem}-legacy-{index}{destination.suffix}"
                            )
                            if not candidate.exists():
                                destination = candidate
                                break
                            index += 1
                    shutil.move(str(source), str(destination))
                    moved += 1
                shutil.rmtree(legacy, ignore_errors=True)
        return moved

    def unique_stem(
        self, base: str, subject: str | None = None, year: int | str | None = None
    ) -> str:
        """Devuelve un nombre libre en los tres tipos de artefacto del año."""
        base = safe_path_component(base, "clase")
        candidate = base
        index = 2
        while any(
            any(directory.glob(f"{candidate}.*"))
            for directory in (
                self.artifact_dir("audio", subject, year),
                self.artifact_dir("transcripts", subject, year),
                self.artifact_dir("summaries", subject, year),
            )
        ):
            candidate = f"{base}-{index}"
            index += 1
        return candidate
