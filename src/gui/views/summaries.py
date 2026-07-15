from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

import flet as ft

from gui.config_provider import get_config
from gui.helpers import notify
from gui.theme import palette


def find_summary_files() -> list[Path]:
    root = get_config().output_dir()
    return sorted(
        (path for path in root.rglob("*.md") if path.parent.name == "summaries"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


class SummariesView:
    def __init__(self, page: ft.Page, app_layout, selected_file: Path | None = None):
        self.main_page = page
        self.app_layout = app_layout
        self.all_files = find_summary_files()
        self.selected_file = selected_file
        colors = palette(get_config().gui.theme)

        self.search = ft.TextField(
            hint_text="Buscar por título o contenido...",
            prefix_icon=ft.Icons.SEARCH,
            on_change=self.filter_files,
            dense=True,
        )
        self.result_count = ft.Text(color=colors.muted, size=12)
        self.files_list = ft.ListView(expand=True, spacing=5)
        self.title = ft.Text("Seleccioná un resumen", size=22, weight=ft.FontWeight.W_600)
        self.path_label = ft.Text("", color=colors.muted, size=12)
        self.markdown_view = ft.Markdown(
            "Elegí una materia y un archivo desde el explorador.",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        )
        self._rebuild_groups(self.all_files)

        explorer = ft.Container(
            ft.Column([
                ft.Row([
                    ft.Text("Biblioteca", size=22, weight=ft.FontWeight.W_600),
                    ft.IconButton(icon=ft.Icons.ADD, tooltip="Nueva grabación", on_click=self._go_home),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self.search,
                self.result_count,
                ft.Divider(),
                self.files_list,
            ]),
            width=340,
            bgcolor=colors.card,
            border_radius=12,
            padding=16,
        )
        reader = ft.Container(
            ft.Column([
                self.title,
                self.path_label,
                ft.Divider(),
                ft.ListView([self.markdown_view], expand=True),
            ]),
            expand=True,
            bgcolor=colors.surface_high,
            border_radius=12,
            padding=22,
        )
        self.view = ft.Row([explorer, ft.Container(reader, expand=True)], expand=True, spacing=18)
        if selected_file:
            self._read_file(selected_file)

    @staticmethod
    def _subject_for(path: Path) -> str:
        # output/<materia>/<año>/summaries/archivo.md
        return path.parent.parent.parent.name if path.parent.name == "summaries" else "sin_materia"

    @staticmethod
    def _year_for(path: Path) -> str:
        return path.parent.parent.name if path.parent.name == "summaries" else "sin_año"

    def _rebuild_groups(self, files: list[Path], query: str = "") -> None:
        grouped: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))
        for path in files:
            grouped[self._subject_for(path)][self._year_for(path)].append(path)
        names = get_config().subject_names()
        controls: list[ft.Control] = []
        for subject in sorted(grouped, key=lambda key: names.get(key, key).casefold()):
            year_tiles: list[ft.Control] = []
            subject_count = 0
            for year in sorted(grouped[subject], reverse=True):
                paths = grouped[subject][year]
                subject_count += len(paths)
                children = [
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=20),
                        title=ft.Text(path.stem, max_lines=2),
                        subtitle=ft.Text(datetime.fromtimestamp(path.stat().st_mtime).strftime("%d/%m/%Y")),
                        data=path,
                        selected=path == self.selected_file,
                        on_click=self.select_file,
                        dense=True,
                    )
                    for path in paths
                ]
                year_tiles.append(ft.ExpansionTile(
                    title=ft.Text(year),
                    leading=ft.Icons.CALENDAR_MONTH_OUTLINED,
                    controls=children,
                    expanded=bool(query) or (
                        self.selected_file is not None and year == self._year_for(self.selected_file)
                    ),
                    maintain_state=True,
                    dense=True,
                ))
            controls.append(ft.ExpansionTile(
                title=ft.Text(names.get(subject, subject)),
                subtitle=ft.Text(f"{subject_count} resumen(es)"),
                leading=ft.Icons.FOLDER_OUTLINED,
                controls=year_tiles,
                expanded=bool(query) or (
                    self.selected_file is not None and subject == self._subject_for(self.selected_file)
                ),
                maintain_state=True,
            ))
        self.files_list.controls = controls or [ft.Text("No se encontraron resúmenes.")]
        self.result_count.value = f"{len(files)} de {len(self.all_files)} archivos"

    def filter_files(self, e) -> None:
        query = (e.control.value or "").strip().casefold()
        filtered = self._matching_files(query)
        self._rebuild_groups(filtered, query)
        self.files_list.update()
        self.result_count.update()

    def _matching_files(self, query: str) -> list[Path]:
        if not query:
            return self.all_files
        filtered = []
        for path in self.all_files:
            if query in path.name.casefold():
                filtered.append(path)
                continue
            try:
                if query in path.read_text(encoding="utf-8", errors="ignore").casefold():
                    filtered.append(path)
            except OSError:
                pass
        return filtered

    def _go_home(self, _e) -> None:
        self.app_layout.show_home()

    def _read_file(self, path: Path) -> None:
        try:
            self.selected_file = path
            self.title.value = path.stem
            self.path_label.value = (
                f"{self._subject_for(path)} / {self._year_for(path)} / summaries / {path.name}"
            )
            self.markdown_view.value = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.markdown_view.value = "No se pudo abrir el resumen."
            notify(self.main_page, f"No se pudo leer {path.name}: {exc}", error=True)

    def select_file(self, e) -> None:
        self._read_file(e.control.data)
        query = (self.search.value or "").strip().casefold()
        self._rebuild_groups(self._matching_files(query), query)
        self.view.update()
