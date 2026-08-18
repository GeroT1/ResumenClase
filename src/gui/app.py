from __future__ import annotations

from pathlib import Path
from typing import Callable

import flet as ft

from gui.config_provider import get_config
from gui.theme import palette
from gui.views.home import HomeView
from gui.views.settings import SettingsView
from gui.views.summaries import SummariesView
from resumen_clase.system_setup import SetupStatus, is_setup_needed

DESTINATIONS: list[tuple[str, str, str, str]] = [
    ("home", ft.Icons.HOME_OUTLINED, ft.Icons.HOME, "Inicio"),
    ("summaries", ft.Icons.LIBRARY_BOOKS_OUTLINED, ft.Icons.LIBRARY_BOOKS, "Resúmenes"),
    ("subjects", ft.Icons.SCHOOL_OUTLINED, ft.Icons.SCHOOL, "Materias"),
    ("context", ft.Icons.FOLDER_COPY_OUTLINED, ft.Icons.FOLDER_COPY, "Contexto"),
    ("setup", ft.Icons.HEALTH_AND_SAFETY_OUTLINED, ft.Icons.HEALTH_AND_SAFETY, "Preparación"),
    ("settings", ft.Icons.TUNE_OUTLINED, ft.Icons.TUNE, "Ajustes"),
]


class AppLayout(ft.Row):
    def __init__(self, page: ft.Page):
        super().__init__(expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        self.main_page = page
        self.active_recording = None
        self._queued_after_recording: list[Callable[[], None]] = []
        cfg = get_config()
        colors = palette(cfg.gui.theme)
        self.bgcolor = colors.canvas

        self.show_setup_in_rail = is_setup_needed(cfg)
        self._current_view_key: str = "setup" if self.show_setup_in_rail else "home"
        self._rail_keys: list[str] = []

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
            destinations=[],
            on_change=self.rail_change,
        )
        self.rebuild_rail()

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
        self._setup_view = None

        if self.show_setup_in_rail:
            self.show_setup(update=False, refresh=False)
        else:
            self.show_home(update=False)

    def _active_destinations(self) -> list[tuple[str, str, str, str]]:
        return [
            item for item in DESTINATIONS
            if item[0] != "setup" or self.show_setup_in_rail
        ]

    def rebuild_rail(self) -> None:
        dest_data = self._active_destinations()
        self._rail_keys = [item[0] for item in dest_data]
        self.rail.destinations = [
            ft.NavigationRailDestination(icon=icon, selected_icon=sel_icon, label=label)
            for _, icon, sel_icon, label in dest_data
        ]
        if self._current_view_key in self._rail_keys:
            self.rail.selected_index = self._rail_keys.index(self._current_view_key)
        else:
            self.rail.selected_index = None

    def update_setup_rail_visibility(self, status: SetupStatus | None = None) -> None:
        needed = is_setup_needed(get_config(), status)
        if needed != self.show_setup_in_rail:
            self.show_setup_in_rail = needed
            self.rebuild_rail()
            self._safe_update()

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

    def _show_by_key(self, key: str, control: ft.Control, *, update: bool = True) -> None:
        self.rail.disabled = False
        self._current_view_key = key
        if key in self._rail_keys:
            self.rail.selected_index = self._rail_keys.index(key)
        else:
            self.rail.selected_index = None
        self.content_area.content = control
        if update:
            self._safe_update()

    def show_home(self, *, update: bool = True) -> None:
        self._show_by_key("home", HomeView(self.main_page, self).view, update=update)

    def show_summaries(self, selected_file: Path | None = None) -> None:
        self._show_by_key("summaries", SummariesView(self.main_page, self, selected_file).view)

    def show_subjects(self) -> None:
        from gui.views.subjects import SubjectsView

        self._show_by_key("subjects", SubjectsView(self.main_page, self).view)

    def show_context(self) -> None:
        from gui.views.context import ContextView

        self._show_by_key("context", ContextView(self.main_page, self).view)

    def show_settings(self) -> None:
        settings = SettingsView(self.main_page, self)
        self._show_by_key("settings", settings.view)
        settings.load_audio_devices()
        settings.load_models()

    def show_setup(
        self,
        *,
        update: bool = True,
        refresh: bool = True,
        from_settings: bool = False,
    ) -> None:
        from gui.views.setup import SetupView

        self._setup_view = SetupView(self.main_page, self, from_settings=from_settings)
        self._show_by_key("setup", self._setup_view.view, update=update)
        if refresh:
            self._setup_view.refresh()

    def refresh_initial_view(self) -> None:
        if self._setup_view is not None:
            self._setup_view.refresh()

    def show_recording(self, recording) -> None:
        self.active_recording = recording
        self.recording_banner.visible = True
        self.recording_banner_text.value = f"Grabando: {recording.stem}"
        self.recording_banner_status.value = "Preparando captura..."
        self.recording_banner_elapsed.value = "00:00"
        self._show_by_key("home", recording.view)

    def show_active_recording(self) -> None:
        if self.active_recording is None:
            return
        self._show_by_key("home", self.active_recording.view)

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
        self._show_by_key("home", control)

    def rail_change(self, e) -> None:
        idx = e.control.selected_index
        if idx is not None and 0 <= idx < len(self._rail_keys):
            key = self._rail_keys[idx]
            if key == "home":
                self.show_home()
            elif key == "summaries":
                self.show_summaries()
            elif key == "subjects":
                self.show_subjects()
            elif key == "context":
                self.show_context()
            elif key == "setup":
                self.show_setup()
            elif key == "settings":
                self.show_settings()

