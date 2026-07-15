from __future__ import annotations

from pathlib import Path
from typing import Callable

import flet as ft

from gui.config_provider import get_config
from gui.theme import palette
from gui.views.home import HomeView
from gui.views.settings import SettingsView
from gui.views.summaries import SummariesView


class AppLayout(ft.Row):
    def __init__(self, page: ft.Page):
        super().__init__(expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        self.main_page = page
        self.active_recording = None
        self._queued_after_recording: list[Callable[[], None]] = []
        colors = palette(get_config().gui.theme)
        self.bgcolor = colors.canvas
        self.rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=112,
            group_alignment=-0.8,
            bgcolor=colors.rail,
            indicator_color=colors.accent_soft,
            selected_label_text_style=ft.TextStyle(color=colors.rail_text, weight=ft.FontWeight.W_600),
            unselected_label_text_style=ft.TextStyle(color=colors.rail_text),
            leading=ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.AUTO_STORIES, color=colors.accent, size=30),
                        ft.Text("ResumenClase", size=11, color=colors.rail_text),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                ),
                padding=ft.Padding.only(top=18, bottom=22),
            ),
            destinations=[
                ft.NavigationRailDestination(icon=ft.Icons.HOME_OUTLINED, selected_icon=ft.Icons.HOME, label="Inicio"),
                ft.NavigationRailDestination(icon=ft.Icons.LIBRARY_BOOKS_OUTLINED, selected_icon=ft.Icons.LIBRARY_BOOKS, label="Resúmenes"),
                ft.NavigationRailDestination(icon=ft.Icons.SCHOOL_OUTLINED, selected_icon=ft.Icons.SCHOOL, label="Materias"),
                ft.NavigationRailDestination(icon=ft.Icons.FOLDER_COPY_OUTLINED, selected_icon=ft.Icons.FOLDER_COPY, label="Contexto"),
                ft.NavigationRailDestination(icon=ft.Icons.TUNE_OUTLINED, selected_icon=ft.Icons.TUNE, label="Ajustes"),
            ],
            on_change=self.rail_change,
        )
        self.rail_panel = ft.Container(
            content=self.rail,
            bgcolor=colors.rail,
            border=ft.Border(right=ft.BorderSide(3, colors.accent)),
        )
        self.recording_banner_icon = ft.Icon(ft.Icons.MIC, color=colors.accent)
        self.recording_banner_text = ft.Text("Grabación en curso", weight=ft.FontWeight.W_600)
        self.recording_banner_status = ft.Text("Preparando...", color=colors.muted, size=12)
        self.recording_banner_elapsed = ft.Text("00:00", weight=ft.FontWeight.W_600)
        self.recording_banner = ft.Container(
            content=ft.Row([
                self.recording_banner_icon,
                ft.Column([
                    self.recording_banner_text,
                    self.recording_banner_status,
                ], expand=True, spacing=1),
                self.recording_banner_elapsed,
                ft.OutlinedButton(
                    content=ft.Text("Volver a la grabación"),
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=lambda _e: self.show_active_recording(),
                ),
            ], spacing=12),
            bgcolor=colors.accent_soft,
            border=ft.Border(bottom=ft.BorderSide(2, colors.accent)),
            padding=ft.Padding.symmetric(horizontal=22, vertical=10),
            visible=False,
        )
        self.content_area = ft.Container(expand=True, padding=30, bgcolor=colors.surface)
        self.workspace = ft.Column(
            [self.recording_banner, self.content_area],
            expand=True,
            spacing=0,
        )
        self.controls = [self.rail_panel, self.workspace]
        self.show_home(update=False)

    def apply_palette(self) -> None:
        colors = palette(get_config().gui.theme)
        self.bgcolor = colors.canvas
        self.rail.bgcolor = colors.rail
        self.rail.indicator_color = colors.accent_soft
        self.rail.selected_label_text_style = ft.TextStyle(color=colors.rail_text, weight=ft.FontWeight.W_600)
        self.rail.unselected_label_text_style = ft.TextStyle(color=colors.rail_text)
        self.rail.leading.content.controls[0].color = colors.accent
        self.rail.leading.content.controls[1].color = colors.rail_text
        self.rail_panel.bgcolor = colors.rail
        self.rail_panel.border = ft.Border(right=ft.BorderSide(3, colors.accent))
        self.content_area.bgcolor = colors.surface
        self.recording_banner.bgcolor = colors.accent_soft
        self.recording_banner.border = ft.Border(bottom=ft.BorderSide(2, colors.accent))
        self.recording_banner_icon.color = colors.accent
        self.recording_banner_status.color = colors.muted
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.update()
        except (RuntimeError, AssertionError):
            pass

    def _show(self, control: ft.Control, *, index: int) -> None:
        self.rail.disabled = False
        self.rail.selected_index = index
        self.content_area.content = control
        self._safe_update()

    def show_home(self, *, update: bool = True) -> None:
        self.rail.disabled = False
        self.rail.selected_index = 0
        self.content_area.content = HomeView(self.main_page, self).view
        if update:
            self._safe_update()

    def show_summaries(self, selected_file: Path | None = None) -> None:
        self._show(SummariesView(self.main_page, self, selected_file).view, index=1)

    def show_subjects(self) -> None:
        from gui.views.subjects import SubjectsView

        self._show(SubjectsView(self.main_page, self).view, index=2)

    def show_context(self) -> None:
        from gui.views.context import ContextView

        self._show(ContextView(self.main_page, self).view, index=3)

    def show_settings(self) -> None:
        settings = SettingsView(self.main_page, self)
        self._show(settings.view, index=4)
        settings.load_audio_devices()
        settings.load_models()

    def show_recording(self, recording) -> None:
        self.active_recording = recording
        self.recording_banner.visible = True
        self.recording_banner_text.value = f"Grabando: {recording.stem}"
        self.recording_banner_status.value = "Preparando captura..."
        self.recording_banner_elapsed.value = "00:00"
        self.rail.disabled = False
        self.rail.selected_index = 0
        self.content_area.content = recording.view
        self._safe_update()

    def show_active_recording(self) -> None:
        if self.active_recording is None:
            return
        self.rail.selected_index = 0
        self.content_area.content = self.active_recording.view
        self._safe_update()

    def update_recording_banner(
        self,
        *,
        status: str | None = None,
        elapsed: str | None = None,
    ) -> None:
        if status is not None:
            self.recording_banner_status.value = status
        if elapsed is not None:
            self.recording_banner_elapsed.value = elapsed
        try:
            if self.recording_banner.page:
                self.recording_banner.update()
        except (RuntimeError, AssertionError):
            pass

    def has_active_recording(self) -> bool:
        return self.active_recording is not None

    def queue_after_recording(self, callback: Callable[[], None]) -> bool:
        if self._queued_after_recording:
            return False
        self._queued_after_recording.append(callback)
        return True

    def cancel_after_recording(self, callback: Callable[[], None]) -> None:
        try:
            self._queued_after_recording.remove(callback)
        except ValueError:
            pass

    def finish_recording(self) -> None:
        self.active_recording = None
        self.recording_banner.visible = False
        queued = list(self._queued_after_recording)
        self._queued_after_recording.clear()
        try:
            if self.recording_banner.page:
                self.recording_banner.update()
        except (RuntimeError, AssertionError):
            pass
        for callback in queued:
            callback()

    def show_combine(self, control: ft.Control) -> None:
        self._show(control, index=0)

    def set_view_by_index(self, index: int) -> None:
        if index == 0:
            self.show_home()
        elif index == 1:
            self.show_summaries()
        elif index == 2:
            self.show_subjects()
        elif index == 3:
            self.show_context()
        elif index == 4:
            self.show_settings()

    def rail_change(self, e) -> None:
        self.set_view_by_index(e.control.selected_index or 0)
