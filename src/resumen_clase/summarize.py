"""Resumen vía Ollama HTTP API.

Estrategia adaptativa según tamaño del transcript:

  CORTO (< 60 k chars ≈ 15 k tokens)
  └─ Sección por sección directamente sobre el transcript completo.
     El modelo ve TODO sin ninguna compresión previa.

  LARGO (60 k – 200 k chars)
  └─ Map: extraer notas densas por chunk con SOLAPE 15% (num_ctx=16384).
     Reduce sección por sección sobre las notas combinadas (num_ctx=32768).

  GIGANTE (≥ 200 k chars, raro)
  └─ Map jerárquico: chunks → notas → consolidación por grupos → reduce.
     Evita que las notas combinadas excedan el num_ctx del reduce.

En todos los casos el reduce es sección por sección: cada llamada LLM tiene
foco en UNA sola sección → respuestas más detalladas que pedir todo junto.

Telemetría: si cfg.log_telemetry, cada llamada loguea t/s y duración
extraídos de eval_count/eval_duration que devuelve Ollama.
"""
from __future__ import annotations

import logging
import re
import time

import httpx

from typing import Callable
from .config import LLMCfg

log = logging.getLogger(__name__)

_NUM_CTX_MAP        = 16384  # input = un chunk ~10k tokens (40k chars) + solape
_NUM_CTX_REDUCE_L   = 32768  # reduce con notas de map + material (~25k in + 4k out)
_NUM_CTX_REDUCE_S   = 32768  # reduce sin map, transcript completo (~17k in + 4k out)

# Si el transcript cabe aquí, lo mandamos entero (sin map)
_DIRECT_THRESHOLD_CHARS = 60_000  # ≈ 15 k tokens

# Si el transcript supera esto, activar reduce jerárquico (map de mapas)
_HIERARCHICAL_THRESHOLD_CHARS = 200_000  # ≈ 50 k tokens de input crudo


# ─────────────────────────────────────────────────────────────────────────────
# Telemetría
# ─────────────────────────────────────────────────────────────────────────────

def _log_telemetry(label: str, resp_json: dict, wall_seconds: float) -> None:
    """Extrae métricas de la respuesta de Ollama y loguea t/s reales."""
    try:
        eval_count = resp_json.get("eval_count", 0)
        eval_dur_ns = resp_json.get("eval_duration", 0)
        prompt_count = resp_json.get("prompt_eval_count", 0)
        prompt_dur_ns = resp_json.get("prompt_eval_duration", 0)
        gen_tps = (eval_count / (eval_dur_ns / 1e9)) if eval_dur_ns else 0
        prompt_tps = (prompt_count / (prompt_dur_ns / 1e9)) if prompt_dur_ns else 0
        log.info(
            "[%s] gen=%d tok @ %.1f t/s · prompt=%d tok @ %.1f t/s · wall=%.1fs",
            label, eval_count, gen_tps, prompt_count, prompt_tps, wall_seconds,
        )
    except Exception as e:
        log.debug("telemetría falló: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _chunk_text(text: str, max_chars: int, overlap_chars: int = 0) -> list[str]:
    """Chunking con solape opcional. El solape mantiene continuidad de contexto
    entre fragmentos: cada chunk salvo el primero arranca `overlap_chars` antes
    del corte natural anterior. Crítico para transcripciones de Whisper donde
    los cortes a mitad de explicación son comunes.

    Trata de cortar en ". " cuando es posible para no partir oraciones, pero
    si no hay punto cerca corta crudo.
    """
    if len(text) <= max_chars:
        return [text]

    overlap_chars = max(0, min(overlap_chars, max_chars // 3))
    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + max_chars, text_len)
        if end < text_len:
            # Buscar el último ". " dentro de los últimos 2000 chars del chunk
            # para cortar en límite de oración cuando sea posible.
            search_from = max(end - 2000, start + max_chars // 2)
            last_period = text.rfind(". ", search_from, end)
            if last_period > 0:
                end = last_period + 2  # incluir ". "
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_len:
            break
        # Próximo arranque: end - overlap (pero no antes del start actual + 1)
        start = max(end - overlap_chars, start + 1)

    return chunks


def _ollama_chat(cfg: LLMCfg, system: str, user: str,
                 num_predict: int = 2048, num_ctx: int = 8192,
                 label: str = "chat") -> str:
    options: dict = {
        "temperature": cfg.temperature,
        "num_predict": num_predict,
        "num_ctx":     num_ctx,
        "num_batch":   cfg.num_batch,
        "repeat_penalty": cfg.repeat_penalty,
        "top_p":       cfg.top_p,
        "top_k":       cfg.top_k,
    }
    # Solo enviamos num_gpu/num_thread si están seteados (≠ default neutro).
    if cfg.num_gpu >= 0:
        options["num_gpu"] = cfg.num_gpu
    if cfg.num_thread > 0:
        options["num_thread"] = cfg.num_thread

    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "stream": False,
        "options": options,
    }
    t0 = time.time()
    timeout = httpx.Timeout(connect=10.0, read=1800.0, write=60.0, pool=10.0)
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{cfg.host}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    wall = time.time() - t0
    if cfg.log_telemetry:
        _log_telemetry(label, data, wall)
    return data["message"]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# MAP — extracción de notas por chunk
# ─────────────────────────────────────────────────────────────────────────────

_MAP_SYSTEM = (
    "Sos un extractor de información de transcripciones de clases universitarias.\n"
    "Tu única tarea: reproducir FIELMENTE el contenido de este fragmento de transcripción.\n\n"
    "Extraé TODO lo que puede ser útil:\n"
    "• Conceptos con su explicación completa tal como la dio el profesor\n"
    "• Definiciones y analogías usadas\n"
    "• Ejemplos desarrollados paso a paso\n"
    "• Fórmulas, ecuaciones, valores numéricos mencionados\n"
    "• Código, comandos, herramientas nombradas\n"
    "• Tareas, fechas límite, tips y advertencias del profesor\n"
    "• Preguntas de alumnos y respuestas del profesor\n\n"
    "⚠ Si el fragmento empieza o termina mitad-explicación (corte de chunk), igual extraé\n"
    "lo que se entiende del fragmento. El sistema usa solape entre fragmentos para\n"
    "que la idea quede capturada en algún chunk.\n\n"
    "⚠ PROHIBIDO: agregar información que no esté en el texto. "
    "No uses tu conocimiento previo. Si algo no está en el fragmento, no lo pongas.\n"
    "Español. Bullets con contexto completo — mejor exceso de información que omitir algo."
)


def _map_chunk(cfg: LLMCfg, chunk: str, idx: int, total: int) -> str:
    user = (
        f"Fragmento {idx}/{total} de la transcripción:\n\n{chunk}\n\n"
        "Extraé toda la información útil en bullets detallados."
    )
    return _ollama_chat(cfg, _MAP_SYSTEM, user,
                        num_predict=2000, num_ctx=_NUM_CTX_MAP,
                        label=f"map {idx}/{total}")


# Map de segundo nivel: consolida grupos de notas en notas más densas pero sin
# perder información. Solo se usa si el transcript es gigante.
_CONSOLIDATE_SYSTEM = (
    "Sos un consolidador de notas. Recibís varias tandas de bullets extraídos\n"
    "de partes contiguas de una misma clase. Tu tarea: unirlas en UNA tanda\n"
    "consolidada, eliminando duplicados literales pero PRESERVANDO TODA la\n"
    "información — explicaciones detalladas, ejemplos, fórmulas, código,\n"
    "tareas, tips. NO resumas: deduplicá. Mantené el nivel de detalle.\n"
    "Español. Bullets."
)


def _consolidate_notes(cfg: LLMCfg, notes_group: str, idx: int, total: int) -> str:
    return _ollama_chat(
        cfg, _CONSOLIDATE_SYSTEM,
        f"Tanda de notas a consolidar (grupo {idx}/{total}):\n\n{notes_group}\n\n"
        "Devolvé una tanda consolidada sin perder información.",
        num_predict=3000, num_ctx=_NUM_CTX_REDUCE_L,
        label=f"consolidate {idx}/{total}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# REDUCE — sección por sección
# ─────────────────────────────────────────────────────────────────────────────

def _extract_section_names(system_prompt: str) -> list[str]:
    return re.findall(r'^\s*-\s+\*\*([^*\n]+)\*\*', system_prompt, re.MULTILINE)


def _reduce_one_section(cfg: LLMCfg, system_prompt: str,
                        source_text: str, section: str,
                        num_ctx: int, extra_context: str = "") -> str:
    """Genera UNA sección del resumen a partir del texto fuente (notas o transcript)."""
    ctx_block = f"\n\n{extra_context}\n" if extra_context else ""
    sec_system = (
        f"{system_prompt}{ctx_block}\n\n"
        "━━━ INSTRUCCIONES PARA ESTA LLAMADA ━━━\n"
        f"Generá ÚNICAMENTE la sección '**{section}**' del resumen.\n\n"
        "Reglas de calidad — SÉ MUY DETALLADO:\n"
        "• Para cada concepto explicá: qué es, cómo lo explicó el profesor "
        "(con sus palabras y analogías), los ejemplos concretos que usó, "
        "y su relación con otros temas de la clase.\n"
        "• Si hay procesos o algoritmos, describí cada paso en detalle.\n"
        "• Si hay ejemplos, desarrollalos completos, no solo el título.\n"
        "• Usá párrafos explicativos para conceptos complejos; "
        "los bullets de una línea son insuficientes.\n"
        "• Mínimo 200 palabras para esta sección si hay contenido real.\n\n"
        "⚠ ANTI-ALUCINACIÓN:\n"
        "• Basate EXCLUSIVAMENTE en el texto provisto.\n"
        "• NO agregues conocimiento propio aunque 'encaje' con la materia.\n"
        "• Si no hay contenido para esta sección, escribí exactamente:\n"
        "  _No mencionado en esta clase._\n\n"
        f"Salida: solo el contenido de la sección (empezando con '**{section}**'), en Markdown."
    )
    user = (
        f"Texto fuente:\n\n{source_text}\n\n"
        f"Generá la sección '**{section}**' con el máximo detalle posible."
    )
    content = _ollama_chat(cfg, sec_system, user,
                           num_predict=4096, num_ctx=num_ctx,
                           label=f"reduce '{section}'")
    stripped = content.strip()
    if not stripped.startswith(f"**{section}**"):
        stripped = f"**{section}**\n\n{stripped}"
    return stripped


def _reduce_section_by_section(cfg: LLMCfg, system_prompt: str,
                                source_text: str, num_ctx: int,
                                progress_cb=None, extra_context: str = "",
                                is_cancelled: Callable[[], bool] | None = None) -> str:
    sections = _extract_section_names(system_prompt)
    ctx_block = f"\n\n{extra_context}\n" if extra_context else ""
    if not sections:
        # Fallback: prompt sin secciones parseables → una sola llamada
        if progress_cb:
            progress_cb("Generando resumen...")
        return _ollama_chat(
            cfg, system_prompt + ctx_block,
            f"Texto fuente:\n\n{source_text}\n\nGenerá el resumen completo y detallado.",
            num_predict=4096, num_ctx=num_ctx,
            label="reduce monolítico",
        )

    parts: list[str] = []
    t0 = time.time()
    for i, section in enumerate(sections, 1):
        if is_cancelled and is_cancelled():
            raise InterruptedError("Cancelado por el usuario")

        if progress_cb:
            if i > 1:
                elapsed = time.time() - t0
                avg_time = elapsed / (i - 1)
                remaining = len(sections) - i + 1
                eta = avg_time * remaining
                mm = int(eta // 60)
                ss = int(eta % 60)
                progress_cb(f"Sección {i}/{len(sections)}: {section}... (ETA: {mm:02d}:{ss:02d})")
            else:
                progress_cb(f"Sección {i}/{len(sections)}: {section}...")
        content = _reduce_one_section(cfg, system_prompt, source_text,
                                      section, num_ctx,
                                      extra_context=extra_context)
        log.info("reduce: sección %d/%d OK (%d chars)", i, len(sections), len(content))
        parts.append(content)

    log.info("reduce: todas las secciones OK, joining (%d partes)", len(parts))
    joined = "\n\n---\n\n".join(parts)
    log.info("reduce: joined OK (%d chars total)", len(joined))
    return joined


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def summarize(transcript: str, cfg: LLMCfg, system_prompt: str,
              progress_cb=None, extra_context: str | None = None,
              is_cancelled: Callable[[], bool] | None = None) -> str:

    extra_context = extra_context or ""

    if len(transcript) <= _DIRECT_THRESHOLD_CHARS:
        # ── Estrategia DIRECTA: sin map, sección por sección sobre transcript completo ──
        if progress_cb:
            progress_cb("Transcript completo en contexto (modo directo)...")
        return _reduce_section_by_section(
            cfg, system_prompt,
            source_text=f"TRANSCRIPCIÓN COMPLETA DE LA CLASE:\n\n{transcript}",
            num_ctx=_NUM_CTX_REDUCE_S,
            progress_cb=progress_cb,
            extra_context=extra_context,
            is_cancelled=is_cancelled,
        )

    # ── Estrategia MAP-REDUCE ──
    overlap = max(0, min(getattr(cfg, "chunk_overlap_chars", 0),
                         cfg.max_chunk_chars // 3))
    chunks = _chunk_text(transcript, cfg.max_chunk_chars, overlap_chars=overlap)
    log.info("map: %d chunks (overlap=%d chars)", len(chunks), overlap)
    map_notes: list[str] = []
    t0_map = time.time()
    for i, chunk in enumerate(chunks, 1):
        if is_cancelled and is_cancelled():
            raise InterruptedError("Cancelado por el usuario")

        if progress_cb:
            if i > 1:
                elapsed = time.time() - t0_map
                avg_time = elapsed / (i - 1)
                remaining = len(chunks) - i + 1
                eta = avg_time * remaining
                mm = int(eta // 60)
                ss = int(eta % 60)
                progress_cb(f"Extrayendo notas parte {i}/{len(chunks)}... (ETA: {mm:02d}:{ss:02d})")
            else:
                progress_cb(f"Extrayendo notas parte {i}/{len(chunks)}...")
        notes = _map_chunk(cfg, chunk, i, len(chunks))
        map_notes.append(f"═══ NOTAS PARTE {i}/{len(chunks)} ═══\n{notes}")

    combined_notes = "\n\n".join(map_notes)

    # ── Reduce JERÁRQUICO si las notas combinadas son enormes ──
    # Si combined_notes excede ~70% del num_ctx del reduce, consolidamos primero
    # en grupos para no desbordar el contexto y perder información del final.
    safe_input_chars = int(_NUM_CTX_REDUCE_L * 4 * 0.7)  # 4 chars/tok aprox * 70%
    if (len(transcript) >= _HIERARCHICAL_THRESHOLD_CHARS
            or len(combined_notes) > safe_input_chars):
        if progress_cb:
            progress_cb("Consolidando notas (reduce jerárquico)...")
        log.info("reduce jerárquico activo: combined_notes=%d chars", len(combined_notes))
        # Agrupar de a 3-4 partes y consolidar cada grupo
        group_size = max(2, len(map_notes) // 4)
        groups = [
            "\n\n".join(map_notes[i:i + group_size])
            for i in range(0, len(map_notes), group_size)
        ]
        consolidated: list[str] = []
        t0_cons = time.time()
        for i, g in enumerate(groups, 1):
            if is_cancelled and is_cancelled():
                raise InterruptedError("Cancelado por el usuario")

            if progress_cb:
                if i > 1:
                    elapsed = time.time() - t0_cons
                    avg_time = elapsed / (i - 1)
                    remaining = len(groups) - i + 1
                    eta = avg_time * remaining
                    mm = int(eta // 60)
                    ss = int(eta % 60)
                    progress_cb(f"Consolidando grupo {i}/{len(groups)}... (ETA: {mm:02d}:{ss:02d})")
                else:
                    progress_cb(f"Consolidando grupo {i}/{len(groups)}...")
            consolidated.append(_consolidate_notes(cfg, g, i, len(groups)))
        combined_notes = "\n\n═══ BLOQUE CONSOLIDADO ═══\n\n".join(consolidated)
        log.info("reduce jerárquico: notas finales = %d chars", len(combined_notes))

    if progress_cb:
        progress_cb("Generando resumen por secciones...")
    return _reduce_section_by_section(
        cfg, system_prompt,
        source_text=combined_notes,
        num_ctx=_NUM_CTX_REDUCE_L,
        progress_cb=progress_cb,
        extra_context=extra_context,
        is_cancelled=is_cancelled,
    )


def check_ollama(cfg: LLMCfg) -> bool:
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(f"{cfg.host}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


def warmup(cfg: LLMCfg) -> bool:
    """Pre-carga el modelo en VRAM con un ping mínimo. Útil al iniciar batch
    para no incluir el load-time en la primera tarea (sesgo en t/s)."""
    try:
        _ollama_chat(cfg, "ping", "ping", num_predict=1, num_ctx=512, label="warmup")
        return True
    except Exception as e:
        log.warning("warmup falló: %s", e)
        return False
