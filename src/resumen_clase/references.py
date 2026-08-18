"""Material de apoyo para enriquecer resúmenes.

Dos fuentes de material:

  1. Carpeta convencional `referencias/<subject_key>/` (relativa al config.yaml):
     contiene Markdown ya normalizado y se carga AUTOMÁTICAMENTE para todas las
     clases de esa materia.

  2. Archivos one-off pasados desde la GUI o CLI: solo .md/.txt para esa corrida,
     sin conversión costosa en el flujo de grabación.

Formatos soportados:
  • .md / .txt  → lectura directa
  • .pdf / .docx / .pptx / .xlsx / .html / .csv → markitdown
    (si markitdown no está disponible solo aceptamos .md y .txt)

LLM opcional para mejorar conversiones (descripciones de imágenes en slides
y archivos de imagen): se usa Claude mediante la API compatible con OpenAI
de Anthropic. Requiere opt-in y clave; sin ambos, MarkItDown trabaja localmente.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from .config import safe_path_component

log = logging.getLogger(__name__)

_CLAUDE_BASE_URL = "https://api.anthropic.com/v1/"
_CLAUDE_MODEL = "claude-sonnet-4-6"

_PLAIN_EXTS = {".md", ".txt"}
_MARKITDOWN_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".csv",
    ".jpg", ".jpeg", ".png",
}

# Caps generosos: prioridad calidad sobre velocidad. Con num_ctx=32768 en
# reduce y Flash Attention activo, hay budget para ~15k tokens de material
# (~60k chars) sin desplazar notas ni output.
_MAX_PER_FILE = 30_000
_MAX_TOTAL    = 60_000

_md_instance = None
_md_load_attempted = False


def reset_markitdown() -> None:
    """Fuerza a aplicar cambios de credenciales en la próxima conversión."""
    global _md_instance, _md_load_attempted
    _md_instance = None
    _md_load_attempted = False


def _build_claude_client():
    """Crea Claude con opt-in por entorno o una clave guardada por la GUI."""
    from .secrets import get_anthropic_api_key, stored_anthropic_api_key

    stored_key = stored_anthropic_api_key()
    if (
        os.environ.get("RESUMEN_CLASE_ENABLE_CLAUDE", "").strip() != "1"
        and not stored_key
    ):
        return None, None
    api_key = get_anthropic_api_key()
    if not api_key:
        log.warning(
            "Claude habilitado pero falta ANTHROPIC_API_KEY — MarkItDown seguirá local"
        )
        return None, None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=_CLAUDE_BASE_URL)
        model = os.environ.get("RESUMEN_CLASE_CLAUDE_MODEL", _CLAUDE_MODEL).strip()
        return client, model or _CLAUDE_MODEL
    except Exception as e:
        log.warning("No pude crear cliente Claude (%s) — MarkItDown seguirá local", e)
        return None, None


def _markitdown():
    """Carga markitdown perezosamente. Si falla, devuelve None.
    Si Claude fue habilitado, lo usa para describir imágenes compatibles."""
    global _md_instance, _md_load_attempted
    if _md_load_attempted:
        return _md_instance
    _md_load_attempted = True
    try:
        from markitdown import MarkItDown
        client, model = _build_claude_client()
        if client is not None:
            _md_instance = MarkItDown(llm_client=client, llm_model=model)
            log.info("MarkItDown cargado con Claude (%s)", model)
        else:
            _md_instance = MarkItDown()
            log.info(
                "MarkItDown cargado localmente, sin API de visión "
                "(RESUMEN_CLASE_ENABLE_CLAUDE=1 para habilitar Claude)"
            )
    except Exception as e:
        log.warning("markitdown no disponible (%s: %s) — usando extractores directos de respaldo", type(e).__name__, e)
        _md_instance = None
    return _md_instance


def _extract_pdf_fallback(path: Path) -> str:
    """Extrae texto de un archivo PDF si MarkItDown no está disponible o falla."""
    errors: list[str] = []
    # 1. Intentar con pdfminer
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        text = pdfminer_extract(str(path))
        if text is not None:
            return text.strip()
    except Exception as e:
        errors.append(f"pdfminer: {e}")
        log.warning("pdfminer falló al leer %s: %s", path.name, e)

    # 2. Intentar con pypdfium2
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        pages_text = []
        for page in pdf:
            text_page = page.get_textpage()
            pages_text.append(text_page.get_text_range())
        full_text = "\n\n".join(t for t in pages_text if t.strip())
        return full_text.strip()
    except Exception as e:
        errors.append(f"pypdfium2: {e}")
        log.warning("pypdfium2 falló al leer %s: %s", path.name, e)

    raise RuntimeError(f"No se pudo extraer texto del PDF '{path.name}'. Errores: {'; '.join(errors)}")


def supported_extensions() -> set[str]:
    exts = set(_PLAIN_EXTS)
    if _markitdown() is not None:
        exts |= _MARKITDOWN_EXTS
    else:
        # Aunque MarkItDown no esté, soportamos PDF vía fallback directo
        exts.add(".pdf")
    return exts


def convertible_extensions() -> set[str]:
    """Formatos aceptados por la vista de importación de contexto fijo."""
    return _PLAIN_EXTS | _MARKITDOWN_EXTS


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in supported_extensions()


def extract_text(path: Path) -> str:
    """Convierte el archivo a texto plano (markdown si aplica)."""
    ext = path.suffix.lower()
    if ext in _PLAIN_EXTS:
        return path.read_text(encoding="utf-8", errors="replace")
    if ext not in _MARKITDOWN_EXTS:
        raise RuntimeError(f"Extensión no soportada: {ext}")

    md = _markitdown()
    if md is not None:
        try:
            result = md.convert(str(path))
            if result.text_content and result.text_content.strip():
                return result.text_content
        except Exception as exc:
            log.warning("MarkItDown falló convirtiendo %s (%s). Intentando extractor de respaldo...", path.name, exc)

    if ext == ".pdf":
        return _extract_pdf_fallback(path)

    if md is None:
        raise RuntimeError(
            f"No puedo leer {path.name}: instalá markitdown o convertí a .md/.txt"
        )
    raise RuntimeError(f"No se pudo convertir '{path.name}' a texto.")


def subject_reference_dir(config_path: Path, subject_key: str) -> Path:
    directory = (
        config_path.parent
        / "referencias"
        / safe_path_component(subject_key, "sin_materia")
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def delete_fixed_reference(path: Path, config_path: Path, subject_key: str) -> None:
    """Elimina sólo un Markdown ubicado dentro de la carpeta administrada."""
    directory = subject_reference_dir(config_path, subject_key).resolve()
    target = path.resolve()
    if target.suffix.lower() != ".md" or not target.is_relative_to(directory):
        raise ValueError("La ruta no pertenece al contexto administrado de la materia")
    if not target.is_file():
        raise FileNotFoundError(f"No existe: {target}")
    target.unlink()


def import_fixed_reference(
    source: Path,
    config_path: Path,
    subject_key: str,
    *,
    remove_managed_source: bool = False,
) -> Path:
    """Convierte una referencia a Markdown y guarda sólo el resultado gestionado.

    ``remove_managed_source`` sólo elimina el origen cuando ya estaba dentro de
    ``referencias/<materia>``. Nunca borra el archivo externo elegido por el usuario.
    """
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"No existe: {source}")
    if source.suffix.lower() not in convertible_extensions():
        raise ValueError(f"Formato no soportado: {source.suffix or '(sin extensión)'}")

    directory = subject_reference_dir(config_path, subject_key).resolve()
    if source.suffix.lower() == ".md" and source.parent == directory:
        return source

    text = extract_text(source).strip()
    if not text:
        raise ValueError(f"La conversión de {source.name} no produjo texto")

    base = safe_path_component(source.stem, "contexto")
    destination = directory / f"{base}.md"
    index = 2
    while destination.exists():
        destination = directory / f"{base}-{index}.md"
        index += 1

    temporary = destination.with_suffix(".md.tmp")
    try:
        temporary.write_text(text + "\n", encoding="utf-8")
        if not temporary.read_text(encoding="utf-8").strip():
            raise ValueError("El Markdown temporal quedó vacío")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    if (
        remove_managed_source
        and source != destination
        and source.is_relative_to(directory)
    ):
        source.unlink()
    return destination


def normalize_existing_references(
    config_path: Path,
    subject_key: str,
    progress_cb: Callable[[str], None] | None = None,
) -> tuple[list[Path], list[tuple[Path, Exception]]]:
    """Convierte archivos históricos dentro de referencias y elimina el original."""
    directory = subject_reference_dir(config_path, subject_key)
    converted: list[Path] = []
    failed: list[tuple[Path, Exception]] = []
    sources = [
        path for path in sorted(directory.rglob("*"))
        if path.is_file() and path.suffix.lower() != ".md"
    ]
    for source in sources:
        if progress_cb:
            progress_cb(f"Convirtiendo {source.name}...")
        try:
            converted.append(
                import_fixed_reference(
                    source,
                    config_path,
                    subject_key,
                    remove_managed_source=True,
                )
            )
        except Exception as exc:
            failed.append((source, exc))
    return converted, failed


def discover_subject_references(config_path: Path, subject_key: str) -> list[Path]:
    """Encuentra archivos en `<config_dir>/referencias/<subject_key>/` (recursivo)."""
    if not subject_key:
        return []
    base = subject_reference_dir(config_path, subject_key)
    if not base.is_dir():
        return []
    found: list[Path] = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower() == ".md":
            found.append(p)
    return found


def build_reference_block(
    paths: list[Path],
    progress_cb: Callable[[str], None] | None = None,
) -> str:
    """Convierte una lista de archivos a un bloque único listo para inyectar al LLM.
    Aplica caps por archivo y total para no desbordar num_ctx."""
    if not paths:
        return ""

    pieces: list[str] = []
    total_chars = 0
    for p in paths:
        if total_chars >= _MAX_TOTAL:
            log.info("Cap total de material alcanzado, ignorando resto desde %s", p.name)
            break
        if progress_cb:
            progress_cb(f"Leyendo material: {p.name}...")
        try:
            text = extract_text(p).strip()
        except Exception as e:
            log.warning("No se pudo leer %s: %s", p, e)
            if progress_cb:
                progress_cb(f"⚠ No se pudo leer el material {p.name}: {e}")
            pieces.append(f"=== {p.name} ===\n[ERROR: {e}]")
            continue
        if not text:
            continue
        if len(text) > _MAX_PER_FILE:
            text = text[:_MAX_PER_FILE].rstrip() + "\n\n[... truncado por tamaño ...]"
        remaining = _MAX_TOTAL - total_chars
        if len(text) > remaining:
            text = text[:remaining].rstrip() + "\n\n[... truncado por cap total ...]"
        pieces.append(f"=== {p.name} ===\n{text}")
        total_chars += len(text)

    if not pieces:
        return ""

    header = (
        "--- MATERIAL DE APOYO ---\n"
        "Lo siguiente es material de referencia (programa, slides, apuntes) que cubre\n"
        "los temas que el profesor explica. Usalo SOLO para:\n"
        "  • Corregir terminología técnica que Whisper pudo haber transcripto mal.\n"
        "  • Estructurar las secciones siguiendo los temas reales.\n"
        "  • Aclarar contexto cuando algo se menciona al pasar.\n"
        "NUNCA agregues contenido del material que NO se haya mencionado en clase.\n"
        "El material es contexto, NO es la fuente principal — la fuente es la transcripción.\n"
    )
    return header + "\n\n" + "\n\n".join(pieces)


def resolve_all_references(
    config_path: Path,
    subject_key: str,
    extra_files: list[Path] | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> list[Path]:
    """Combina material auto-descubierto + adjuntos one-off, sin duplicados."""
    seen: set[Path] = set()
    out: list[Path] = []
    for p in discover_subject_references(config_path, subject_key):
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    for p in (extra_files or []):
        if not p.is_file():
            log.warning("Material extra no existe: %s", p)
            if progress_cb:
                progress_cb(f"⚠ El material extra ya no existe: {p.name}")
            continue
        if p.suffix.lower() not in _PLAIN_EXTS:
            log.warning("Material extra debe ser .md o .txt: %s", p)
            if progress_cb:
                progress_cb(f"⚠ {p.name}: el contexto extra sólo admite .md o .txt")
            continue
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out
