from __future__ import annotations

from pathlib import Path

import flet as ft

from gui.config_provider import get_config
from gui.helpers import class_stem, file_names, notify
from gui.theme import palette
from gui.views.recording import RecordingView
from resumen_clase.references import discover_subject_references


class HomeView:
    def __init__(self, page: ft.Page, app_layout):
        self.main_page = page
        self.app_layout = app_layout
        self.context_files: list[Path] = []
        cfg = get_config()
        colors = palette(cfg.gui.theme)

        self.subject_dropdown = ft.Dropdown(
            label="Materia",
            options=[ft.DropdownOption(key=k, text=v) for k, v in cfg.subject_names().items()],
            width=320,
            on_select=self.on_subject_selected,
        )
        self.name_field = ft.TextField(
            label="Nombre opcional",
            hint_text="Ej: Introducción a matrices",
            width=380,
        )
        self.context_status = ft.Text(
            "Seleccioná una materia para buscar contexto automático.",
            color=colors.muted,
            size=12,
        )
        self.recent_list = ft.Column(spacing=5)
        self._load_recent_summaries()

        hero = ft.Container(
            content=ft.Row([
                ft.Column([
                    ft.Text("Tu próxima clase, bien organizada", size=29, weight=ft.FontWeight.W_700),
                    ft.Text(
                        "Grabá, transcribí y generá apuntes con contexto automático.",
                        color=colors.text,
                        size=14,
                    ),
                ], expand=True, spacing=5),
                ft.Container(
                    ft.Icon(ft.Icons.AUTO_STORIES, size=44, color=colors.accent),
                    width=76,
                    height=76,
                    alignment=ft.Alignment.CENTER,
                    bgcolor=colors.card,
                    border_radius=22,
                    border=ft.Border.all(1, colors.accent),
                ),
            ]),
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_LEFT,
                end=ft.Alignment.BOTTOM_RIGHT,
                colors=[colors.accent_soft, colors.accent3_soft],
            ),
            border_radius=16,
            padding=24,
        )

        context_panel = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Container(
                        ft.Icon(ft.Icons.FOLDER_COPY_OUTLINED, color=colors.accent2),
                        bgcolor=colors.accent2_soft,
                        border_radius=10,
                        padding=10,
                    ),
                    ft.Text("Material de contexto", weight=ft.FontWeight.W_600),
                    ft.TextButton(
                        content=ft.Text("Agregar extra"),
                        icon=ft.Icons.ATTACH_FILE,
                        on_click=self.pick_context,
                    ),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self.context_status,
            ], spacing=7),
            bgcolor=colors.surface_high,
            border=ft.Border(left=ft.BorderSide(4, colors.accent2)),
            border_radius=10,
            padding=14,
        )

        new_class = ft.Card(
            bgcolor=colors.card,
            elevation=0,
            content=ft.Container(
                ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.MIC_NONE, color=colors.accent),
                        ft.Column([
                            ft.Text("Nueva grabación", size=20, weight=ft.FontWeight.W_600),
                            ft.Text("Elegí la materia y, si querés, un nombre fácil de reconocer.", color=colors.muted, size=12),
                        ], spacing=2),
                    ]),
                    ft.Divider(),
                    ft.Row([self.subject_dropdown, self.name_field], spacing=16, wrap=True),
                    context_panel,
                ], spacing=14),
                padding=20,
                border=ft.Border(top=ft.BorderSide(3, colors.accent)),
                border_radius=12,
            ),
        )

        dark_button_text = "#101318"
        light_button_text = "#FFFFFF"
        primary_text = dark_button_text if colors.mode == ft.ThemeMode.DARK else light_button_text
        actions = ft.Row([
            ft.FilledButton(
                content=ft.Text(
                    "Volver a la grabación" if app_layout.has_active_recording() else "Empezar a grabar"
                ),
                icon=ft.Icons.MIC,
                on_click=(
                    lambda _e: app_layout.show_active_recording()
                    if app_layout.has_active_recording()
                    else self.on_start(_e)
                ),
                style=ft.ButtonStyle(bgcolor=colors.accent, color=primary_text, padding=18),
            ),
            ft.FilledButton(
                content=ft.Text("Combinar archivos"),
                icon=ft.Icons.MERGE_TYPE,
                on_click=self.nav_to_combine,
                style=ft.ButtonStyle(bgcolor=colors.accent2, color=dark_button_text, padding=18),
            ),
            ft.FilledButton(
                content=ft.Text("Explorar resúmenes"),
                icon=ft.Icons.LIBRARY_BOOKS,
                on_click=self.nav_to_summaries,
                style=ft.ButtonStyle(bgcolor=colors.accent3, color=dark_button_text, padding=18),
            ),
        ], spacing=12, wrap=True)

        recent = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Container(
                        ft.Icon(ft.Icons.HISTORY, color=colors.accent3),
                        bgcolor=colors.accent3_soft,
                        border_radius=10,
                        padding=10,
                    ),
                    ft.Column([
                        ft.Text("Resúmenes recientes", size=20, weight=ft.FontWeight.W_600),
                        ft.Text("Tus últimos apuntes, separados por materia y año.", color=colors.muted, size=12),
                    ], spacing=2),
                ]),
                ft.Divider(),
                self.recent_list,
            ], spacing=10),
            bgcolor=colors.card,
            border=ft.Border(left=ft.BorderSide(4, colors.accent3)),
            border_radius=12,
            padding=18,
        )

        self.view = ft.ListView(
            [hero, new_class, actions, recent],
            expand=True,
            spacing=16,
            padding=ft.Padding.only(right=10, bottom=12),
        )

    async def pick_context(self, _e) -> None:
        files = await ft.FilePicker().pick_files(
            dialog_title="Material de apoyo adicional",
            allow_multiple=True,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=[
                "md", "txt",
            ],
        )
        self.context_files = [Path(f.path) for f in (files or []) if f.path]
        self._refresh_context_status()
        self.context_status.update()

    def on_subject_selected(self, _e) -> None:
        self._refresh_context_status()
        self.context_status.update()

    def _automatic_context(self) -> list[Path]:
        subject = self.subject_dropdown.value
        if not subject:
            return []
        return discover_subject_references(get_config().config_path, subject)

    def _refresh_context_status(self) -> None:
        automatic = self._automatic_context()
        parts = []
        if automatic:
            parts.append(f"{len(automatic)} automático(s) en referencias/{self.subject_dropdown.value}")
        if self.context_files:
            parts.append(f"extras: {file_names(self.context_files, '')}")
        self.context_status.value = " · ".join(parts) or "No se encontró contexto para esta materia."

    def on_start(self, _e) -> None:
        subject = self.subject_dropdown.value
        if not subject:
            notify(self.main_page, "Seleccioná una materia antes de grabar.", error=True)
            return
        recording = RecordingView(
            self.main_page,
            self.app_layout,
            subject,
            class_stem(subject, self.name_field.value or ""),
            extra_files=list(self.context_files),
        )
        self.app_layout.show_recording(recording)
        recording.start_session()

    def nav_to_combine(self, _e) -> None:
        from gui.views.combine import CombineView

        combine = CombineView(self.main_page, self.app_layout)
        self.app_layout.show_combine(combine.view)

    def nav_to_summaries(self, _e) -> None:
        self.app_layout.show_summaries()

    def _load_recent_summaries(self) -> None:
        root = get_config().output_dir()
        files = sorted(
            (path for path in root.rglob("*.md") if path.parent.name == "summaries"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:8]
        if not files:
            self.recent_list.controls.append(ft.Text("Todavía no hay resúmenes."))
            return
        for path in files:
            relative = path.relative_to(root)
            year = relative.parts[1] if len(relative.parts) >= 4 else ""
            self.recent_list.controls.append(
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.DESCRIPTION_OUTLINED),
                    title=ft.Text(path.stem),
                    subtitle=ft.Text(f"{relative.parts[0]} · {year}".strip(" ·")),
                    data=path,
                    on_click=lambda e: self.app_layout.show_summaries(e.control.data),
                    dense=True,
                )
            )
