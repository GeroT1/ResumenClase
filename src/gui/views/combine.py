from __future__ import annotations

import threading
from pathlib import Path

import flet as ft

from gui.config_provider import get_config
from gui.helpers import class_stem, file_names, notify
from resumen_clase.config import Config
from resumen_clase.service import combine_sources


class CombineView:
    SOURCE_EXTENSIONS = ["wav", "mp3", "mp4", "mkv", "m4a", "ogg", "flac", "webm", "txt", "md"]
    CONTEXT_EXTENSIONS = ["md", "txt"]

    def __init__(self, page: ft.Page, app_layout):
        self.main_page = page
        self.app_layout = app_layout
        self.sources: list[Path] = []
        self.context_files: list[Path] = []
        self._cancelled = threading.Event()
        self._running = False
        self._queued = False
        cfg = get_config()

        self.subject = ft.Dropdown(
            label="Materia",
            options=[ft.DropdownOption(key=k, text=v) for k, v in cfg.subject_names().items()],
            width=300,
        )
        self.name = ft.TextField(label="Nombre opcional", width=300)
        self.sources_text = ft.Text("No hay archivos seleccionados.", color=ft.Colors.GREY_500)
        self.context_text = ft.Text("Sin contexto adicional.", color=ft.Colors.GREY_500)
        self.status = ft.Text("Listo para seleccionar archivos.")
        self.progress = ft.ProgressRing(visible=False, width=24, height=24)
        self.btn_process = ft.FilledButton(
            content=ft.Text("Combinar y resumir"), on_click=self.start_combine, disabled=True
        )
        self.btn_cancel = ft.OutlinedButton(
            content=ft.Text("Cancelar proceso"), on_click=self.cancel_process, visible=False
        )

        self.view = ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("Combinar audios y resúmenes", size=28),
                        ft.TextButton(content=ft.Text("Volver"), on_click=lambda _e: app_layout.show_home()),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row([self.subject, self.name], wrap=True),
                ft.OutlinedButton(content=ft.Text("Agregar archivos"), on_click=self.pick_sources),
                self.sources_text,
                ft.TextButton(content=ft.Text("Agregar contexto de apoyo"), on_click=self.pick_context),
                self.context_text,
                ft.Divider(),
                ft.Row([self.progress, self.status], wrap=True),
                ft.Row([self.btn_process, self.btn_cancel], wrap=True),
            ],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=16,
        )

    async def pick_sources(self, _e) -> None:
        files = await ft.FilePicker().pick_files(
            dialog_title="Archivos para combinar",
            allow_multiple=True,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=self.SOURCE_EXTENSIONS,
        )
        self.sources = [Path(f.path) for f in files if f.path]
        self.sources_text.value = file_names(self.sources, "No hay archivos seleccionados.")
        self.sources_text.color = None if self.sources else ft.Colors.GREY_500
        self.btn_process.disabled = not self.sources
        self.view.update()

    async def pick_context(self, _e) -> None:
        files = await ft.FilePicker().pick_files(
            dialog_title="Contexto de apoyo",
            allow_multiple=True,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=self.CONTEXT_EXTENSIONS,
        )
        self.context_files = [Path(f.path) for f in (files or []) if f.path]
        self.context_text.value = file_names(self.context_files, "Sin contexto adicional.")
        self.context_text.color = None if self.context_files else ft.Colors.GREY_500
        self.context_text.update()

    def start_combine(self, _e) -> None:
        if self._running:
            return
        if not self.subject.value:
            notify(self.main_page, "Seleccioná una materia.", error=True)
            return
        missing = [p for p in self.sources if not p.is_file()]
        if missing:
            notify(self.main_page, f"Ya no existe: {missing[0]}", error=True)
            return
        self._running = True
        self._cancelled.clear()
        self.btn_process.disabled = True
        self.btn_cancel.visible = True
        self.progress.visible = True
        if self.app_layout.has_active_recording():
            self._queued = True
            if not self.app_layout.queue_after_recording(self._start_combine_now):
                self._queued = False
                self._running = False
                notify(
                    self.main_page,
                    "Ya hay otra combinación esperando que termine la grabación.",
                    error=True,
                )
                self._reset_controls()
                return
            self.status.value = (
                "En cola: comenzará automáticamente cuando termine la grabación, "
                "para no competir por Whisper/Ollama."
            )
            notify(
                self.main_page,
                "La combinación quedó en cola y comenzará al terminar la grabación.",
                warning=True,
            )
        else:
            self.status.value = "Preparando archivos..."
        self.view.update()
        if not self._queued:
            self._start_combine_now()

    def _start_combine_now(self) -> None:
        if self._cancelled.is_set():
            return
        self._queued = False
        self.status.value = "Preparando archivos..."
        try:
            if self.view.page:
                self.view.update()
        except (RuntimeError, AssertionError):
            pass
        self.main_page.run_thread(self._combine_worker)

    def _combine_worker(self) -> None:
        current_cfg = get_config()
        cfg = Config.load(current_cfg.config_path, subject=self.subject.value or "")
        try:
            result = combine_sources(
                cfg,
                list(self.sources),
                stem=class_stem(cfg.active_subject, self.name.value or "", prefix="combinado"),
                generate_summary=True,
                progress_cb=self._set_status,
                extra_files=list(self.context_files),
                is_cancelled=self._cancelled.is_set,
            )
            if result.summary_path:
                self.app_layout.show_summaries(result.summary_path)
            else:
                notify(self.main_page, "Se generó el transcript, pero Ollama no produjo un resumen.", error=True)
                self._reset_controls()
        except InterruptedError:
            self._set_status("Proceso cancelado.")
            self._reset_controls()
        except Exception as exc:
            notify(self.main_page, f"Falló la combinación: {exc}", error=True)
            self._set_status("Error durante el procesamiento.")
            self._reset_controls()

    def _set_status(self, message: str) -> None:
        self.status.value = message
        if message.startswith("⚠"):
            notify(self.main_page, message.removeprefix("⚠").strip(), warning=True)
        try:
            if self.status.page:
                self.status.update()
        except RuntimeError:
            pass

    def cancel_process(self, _e) -> None:
        self._cancelled.set()
        if self._queued:
            self.app_layout.cancel_after_recording(self._start_combine_now)
            self._queued = False
            self.status.value = "Combinación en cola cancelada."
            self._reset_controls()
            return
        self.status.value = "Cancelando al terminar el paso actual..."
        self.btn_cancel.disabled = True
        self.view.update()

    def _reset_controls(self) -> None:
        self._running = False
        self.progress.visible = False
        self.btn_cancel.visible = False
        self.btn_cancel.disabled = False
        self.btn_process.disabled = not self.sources
        try:
            if self.view.page:
                self.view.update()
        except RuntimeError:
            pass
