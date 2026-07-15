from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import flet as ft

from gui.config_provider import get_config
from gui.helpers import notify
from resumen_clase.config import Config
from resumen_clase.service import LiveSession


class RecordingView:
    def __init__(
        self,
        page: ft.Page,
        app_layout,
        subject_key: str,
        stem: str,
        extra_files: list[Path] | None = None,
    ):
        self.main_page = page
        self.app_layout = app_layout
        self.subject_key = subject_key
        self.stem = stem
        self._paused = False
        self._chunks: list[str] = []
        self._search_query = ""
        self._processing_done = threading.Event()
        self._timer_stop = threading.Event()
        self._session_error: Exception | None = None
        self._start_time: float | None = None
        self._finishing = False

        current_cfg = get_config()
        # La sesión conserva una instantánea propia: navegar, combinar o guardar
        # ajustes no puede cambiarle materia, dispositivo, modelos ni rutas.
        cfg = Config.load(current_cfg.config_path, subject=subject_key)
        self._session = LiveSession(cfg, stem, extra_files=extra_files or [])
        self._session.on_chunk = self._on_chunk
        self._session.on_status = self._on_status
        self._session.on_warning = self._on_warning

        self.status_indicator = ft.Container(
            width=14, height=14, bgcolor=ft.Colors.ORANGE, border_radius=7
        )
        self.status_text = ft.Text("Preparando grabación...", size=15)
        self.elapsed_text = ft.Text("00:00", color=ft.Colors.GREY_400)
        self.last_chunk_text = ft.Text(
            "Esperando audio...", size=16, italic=True, color=ft.Colors.GREY_400
        )
        self.search_field = ft.TextField(
            label="Buscar en transcripción",
            prefix_icon=ft.Icons.SEARCH,
            on_change=self.on_search,
            width=320,
        )
        self.history_list = ft.ListView(expand=True, spacing=6, auto_scroll=True)
        self.btn_pause = ft.OutlinedButton(content=ft.Text("⏸ Pausar"), on_click=self.toggle_pause)
        self.btn_stop = ft.FilledButton(
            content=ft.Text("⏹ Detener y resumir"), on_click=self.stop_and_summarize
        )
        self.btn_save_only = ft.TextButton(
            content=ft.Text("💾 Guardar sin resumir"), on_click=self.save_without_summary
        )
        self.btn_cancel = ft.TextButton(content=ft.Text("Cancelar"), on_click=self.cancel)
        self.btn_row = ft.Row(
            [self.btn_pause, self.btn_stop, self.btn_save_only, self.btn_cancel],
            spacing=10,
            wrap=True,
        )

        self.view = ft.Column(
            [
                ft.Row(
                    [
                        self.status_indicator,
                        self.status_text,
                        ft.Container(width=20),
                        self.elapsed_text,
                    ],
                    spacing=8,
                ),
                ft.Text(f"Sesión: {stem}", size=12, color=ft.Colors.GREY_500),
                ft.Divider(),
                ft.Text("Último fragmento", size=16),
                ft.Container(
                    self.last_chunk_text,
                    border=ft.Border.all(1, ft.Colors.GREY_700),
                    border_radius=8,
                    padding=12,
                ),
                ft.Row(
                    [ft.Text("Transcripción", size=18), self.search_field],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                ),
                ft.Container(
                    self.history_list,
                    expand=True,
                    border=ft.Border.all(1, ft.Colors.GREY_700),
                    border_radius=8,
                    padding=10,
                ),
                self.btn_row,
            ],
            expand=True,
            spacing=12,
        )

    def start_session(self) -> None:
        self.main_page.run_thread(self._recording_worker)

    def _recording_worker(self) -> None:
        try:
            self._session.start()
            self._start_time = time.time()
            self.main_page.run_task(self._elapsed_loop)
            self._session.process_chunks_until_stop()
        except Exception as exc:
            self._session_error = exc
            self._on_status("error")
            notify(self.main_page, f"La grabación falló: {exc}", error=True)
        finally:
            self._processing_done.set()

    async def _elapsed_loop(self) -> None:
        while not self._timer_stop.is_set() and self._start_time is not None:
            elapsed = int(time.time() - self._start_time)
            mm, ss = divmod(elapsed, 60)
            self.elapsed_text.value = f"{mm:02d}:{ss:02d}"
            self.app_layout.update_recording_banner(elapsed=self.elapsed_text.value)
            if self.elapsed_text.page:
                self.elapsed_text.update()
            await asyncio.sleep(1)

    def _on_chunk(self, text: str, _offset: float) -> None:
        if not text:
            return
        self._chunks.append(text)
        self.last_chunk_text.value = text
        self.last_chunk_text.italic = False
        self.last_chunk_text.color = None
        self._rebuild_history()
        self._safe_update()

    def _on_status(self, status: str) -> None:
        if status.startswith("⚠"):
            self._on_warning(status.removeprefix("⚠").strip())
            return
        labels = {
            "recording": ("Grabando...", ft.Colors.RED),
            "paused": ("Pausado", ft.Colors.ORANGE),
            "stopping": ("Procesando últimos fragmentos...", ft.Colors.YELLOW),
            "summarizing": ("Generando resumen...", ft.Colors.BLUE),
            "done": ("Listo", ft.Colors.GREEN),
            "cancelling": ("Cancelando...", ft.Colors.GREY_400),
            "ollama_unavailable": ("Ollama no está disponible; se guardó el transcript.", ft.Colors.ORANGE),
            "error": ("Error", ft.Colors.RED),
        }
        label, color = labels.get(status, (status, ft.Colors.BLUE_GREY_300))
        self.status_text.value = label
        self.status_indicator.bgcolor = color
        self.app_layout.update_recording_banner(status=label)
        self._safe_update()

    def _on_warning(self, message: str) -> None:
        self.status_text.value = message
        self.status_indicator.bgcolor = ft.Colors.AMBER
        self.app_layout.update_recording_banner(status=message)
        self._safe_update()
        notify(self.main_page, message, warning=True)

    def _safe_update(self) -> None:
        try:
            if self.view.page:
                self.view.update()
        except (RuntimeError, AssertionError):
            pass

    def _rebuild_history(self) -> None:
        query = self._search_query.casefold()
        self.history_list.controls = [
            ft.Container(
                ft.Text(chunk, color=ft.Colors.AMBER_300 if query else None),
                padding=6,
                border_radius=4,
            )
            for chunk in self._chunks
            if not query or query in chunk.casefold()
        ]

    def on_search(self, e) -> None:
        self._search_query = e.control.value or ""
        self._rebuild_history()
        self.history_list.update()

    def toggle_pause(self, _e) -> None:
        if self._finishing:
            return
        self._paused = not self._paused
        self._session.set_paused(self._paused)
        self.btn_pause.content = ft.Text("▶ Reanudar" if self._paused else "⏸ Pausar")
        self.btn_pause.update()

    def stop_and_summarize(self, _e) -> None:
        self._begin_finalize(True)

    def save_without_summary(self, _e) -> None:
        self._begin_finalize(False)

    def _begin_finalize(self, generate_summary: bool) -> None:
        if self._finishing:
            return
        self._finishing = True
        self._disable_buttons()
        self.main_page.run_thread(self._finalize_worker, generate_summary)

    def _finalize_worker(self, generate_summary: bool) -> None:
        try:
            self._session.request_stop()
        except Exception as exc:
            self._session_error = exc
            self._processing_done.set()
        self._processing_done.wait()
        self._timer_stop.set()
        try:
            if self._session_error:
                raise self._session_error
            result = self._session.finalize(generate_summary=generate_summary)
            self.app_layout.finish_recording()
            if generate_summary and result.summary_path is None:
                notify(
                    self.main_page,
                    "Se guardó la transcripción, pero no se generó resumen. Verificá Ollama.",
                    error=True,
                )
                self.app_layout.show_home()
            elif result.summary_path:
                self.app_layout.show_summaries(result.summary_path)
            else:
                notify(self.main_page, f"Transcripción guardada en {result.plain_path}")
                self.app_layout.show_home()
        except Exception as exc:
            self._on_status("error")
            notify(self.main_page, f"No se pudo finalizar la sesión: {exc}", error=True)
            self._finishing = False
            self.btn_cancel.disabled = False
            try:
                if self.btn_cancel.page:
                    self.btn_cancel.update()
            except (RuntimeError, AssertionError):
                pass

    def cancel(self, _e) -> None:
        if self._finishing:
            return
        self._finishing = True
        self._timer_stop.set()
        self._start_time = None
        self._disable_buttons()
        self.main_page.run_thread(self._cancel_worker)

    def _cancel_worker(self) -> None:
        self._session.request_cancel()
        self._processing_done.wait()
        self._timer_stop.set()
        self._session.discard()
        self.app_layout.finish_recording()
        self.app_layout.show_home()

    def _disable_buttons(self) -> None:
        for button in [self.btn_pause, self.btn_stop, self.btn_save_only, self.btn_cancel]:
            button.disabled = True
        self.btn_row.update()
