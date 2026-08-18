from __future__ import annotations

from pathlib import Path

import flet as ft

from gui.config_provider import config_path, get_config
from gui.helpers import notify
from gui.theme import palette
from resumen_clase.references import (
    convertible_extensions,
    delete_fixed_reference,
    discover_subject_references,
    import_fixed_reference,
    normalize_existing_references,
    subject_reference_dir,
)


class ContextView:
    """Administra el material fijo ya normalizado de cada materia."""

    def __init__(self, page: ft.Page, app_layout):
        self.main_page = page
        self.app_layout = app_layout
        self._running = False
        cfg = get_config()
        colors = palette(cfg.gui.theme)

        self.subject = ft.Dropdown(
            label="Materia",
            options=[
                ft.DropdownOption(key=key, text=name)
                for key, name in cfg.subject_names().items()
            ],
            width=360,
            on_select=self._subject_changed,
        )
        self.import_button = ft.FilledButton(
            content=ft.Text("Importar y convertir"),
            icon=ft.Icons.UPLOAD_FILE,
            on_click=self.pick_files,
        )
        self.normalize_button = ft.OutlinedButton(
            content=ft.Text("Convertir archivos existentes"),
            icon=ft.Icons.AUTORENEW,
            on_click=self.normalize_existing,
        )
        self.progress = ft.ProgressRing(width=22, height=22, visible=False)
        self.status = ft.Text(
            "Elegí una materia para ver su material fijo.",
            color=colors.muted,
            size=12,
            selectable=True,
            expand=True,
        )
        self.copy_status_button = ft.IconButton(
            icon=ft.Icons.COPY_ALL,
            icon_size=16,
            tooltip="Copiar mensaje al portapapeles",
            visible=False,
            on_click=lambda _e: self._copy_status(),
        )
        self.file_list = ft.ListView(expand=True, spacing=6)

        info = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.INFO_OUTLINE, color=colors.accent2),
                            ft.Text(
                                "Cómo se usa este material",
                                weight=ft.FontWeight.W_600,
                            ),
                        ]
                    ),
                    ft.Text(
                        "Los archivos se convierten una sola vez a Markdown y se usan "
                        "automáticamente en las clases de esa materia. El archivo externo "
                        "que elijas se conserva; sólo se eliminan originales que ya estén "
                        "dentro de la carpeta administrada, después de verificar la conversión.",
                        color=colors.text,
                    ),
                    ft.Text(
                        "El contexto extra de una clase admite únicamente .md o .txt y no "
                        "ejecuta MarkItDown durante la grabación.",
                        color=colors.muted,
                        size=12,
                    ),
                ],
                spacing=8,
            ),
            bgcolor=colors.accent2_soft,
            border=ft.Border(left=ft.BorderSide(4, colors.accent2)),
            border_radius=12,
            padding=16,
        )

        files_panel = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(
                                        "Contexto disponible",
                                        size=20,
                                        weight=ft.FontWeight.W_600,
                                    ),
                                    ft.Row(
                                        [self.status, self.copy_status_button],
                                        alignment=ft.MainAxisAlignment.START,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            self.progress,
                        ]
                    ),
                    ft.Divider(),
                    self.file_list,
                ],
                expand=True,
            ),
            bgcolor=colors.card,
            border=ft.Border(top=ft.BorderSide(3, colors.accent3)),
            border_radius=12,
            padding=18,
            expand=True,
        )

        self.view = ft.Column(
            [
                ft.Text("Material de contexto", size=28, weight=ft.FontWeight.W_600),
                ft.Text(
                    "Prepará el material fijo antes de la clase y mantenelo separado por materia.",
                    color=colors.muted,
                ),
                info,
                ft.Row(
                    [self.subject, self.import_button, self.normalize_button],
                    wrap=True,
                    spacing=12,
                ),
                files_panel,
            ],
            expand=True,
            spacing=14,
        )

        if cfg.subjects:
            self.subject.value = next(iter(cfg.subjects))
            self._refresh_files()
        else:
            self._set_buttons_disabled(True)

    def _selected_subject(self) -> str | None:
        return self.subject.value or None

    def _subject_changed(self, _event) -> None:
        if self._running:
            return
        self._refresh_files()
        self._safe_update()

    async def pick_files(self, _event) -> None:
        subject = self._selected_subject()
        if not subject:
            notify(self.main_page, "Elegí una materia.", error=True)
            return
        if self.app_layout.has_active_recording():
            notify(
                self.main_page,
                "Terminá la grabación antes de convertir material de contexto.",
                warning=True,
            )
            return
        selected = await ft.FilePicker().pick_files(
            dialog_title="Material fijo de la materia",
            allow_multiple=True,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=sorted(ext.removeprefix(".") for ext in convertible_extensions()),
        )
        sources = [Path(item.path) for item in (selected or []) if item.path]
        if not sources:
            return
        if not self._begin("Preparando la conversión..."):
            return
        self.main_page.run_thread(self._import_worker, subject, sources)

    def _import_worker(self, subject: str, sources: list[Path]) -> None:
        converted: list[Path] = []
        failed: list[tuple[Path, Exception]] = []
        for source in sources:
            self._set_status(f"Convirtiendo {source.name}...")
            try:
                managed_dir = subject_reference_dir(config_path(), subject).resolve()
                converted.append(
                    import_fixed_reference(
                        source,
                        config_path(),
                        subject,
                        remove_managed_source=source.resolve().is_relative_to(managed_dir),
                    )
                )
            except Exception as exc:
                failed.append((source, exc))
        self._finish_conversion(converted, failed)

    def normalize_existing(self, _event) -> None:
        subject = self._selected_subject()
        if not subject:
            notify(self.main_page, "Elegí una materia.", error=True)
            return
        if self.app_layout.has_active_recording():
            notify(
                self.main_page,
                "Terminá la grabación antes de convertir material de contexto.",
                warning=True,
            )
            return
        if not self._begin("Buscando archivos sin convertir..."):
            return
        self.main_page.run_thread(self._normalize_worker, subject)

    def _normalize_worker(self, subject: str) -> None:
        converted, failed = normalize_existing_references(
            config_path(), subject, self._set_status
        )
        self._finish_conversion(converted, failed)

    def _copy_status(self) -> None:
        try:
            if self.status.value:
                self.main_page.set_clipboard(self.status.value)
                notify(self.main_page, "Mensaje copiado al portapapeles.")
        except Exception:
            pass

    def _finish_conversion(
        self,
        converted: list[Path],
        failed: list[tuple[Path, Exception]],
    ) -> None:
        self._running = False
        self.progress.visible = False
        self.subject.disabled = False
        self._set_buttons_disabled(False)
        self._refresh_files()
        if failed:
            first_path, first_error = failed[0]
            self.status.value = (
                f"Se convirtieron {len(converted)}; fallaron {len(failed)}. "
                f"{first_path.name}: {first_error}"
            )
            self.copy_status_button.visible = True
            notify(self.main_page, self.status.value, error=True)
        elif converted:
            self.status.value = f"Se convirtieron {len(converted)} archivo(s) correctamente."
            self.copy_status_button.visible = False
            notify(self.main_page, self.status.value)
        else:
            self.status.value = "No había archivos pendientes de conversión."
            self.copy_status_button.visible = False
            notify(self.main_page, self.status.value)
        self._safe_update()

    def _begin(self, message: str) -> bool:
        if self._running:
            return False
        self._running = True
        self.progress.visible = True
        self.subject.disabled = True
        self._set_buttons_disabled(True)
        self.copy_status_button.visible = False
        self.status.value = message
        self._safe_update()
        return True

    def _set_buttons_disabled(self, disabled: bool) -> None:
        self.import_button.disabled = disabled
        self.normalize_button.disabled = disabled

    def _set_status(self, message: str) -> None:
        self.status.value = message
        self._safe_update()

    def _refresh_files(self) -> None:
        subject = self._selected_subject()
        files = discover_subject_references(config_path(), subject or "")
        if not files:
            self.file_list.controls = [
                ft.Container(
                    ft.Text("Todavía no hay material fijo convertido."),
                    padding=16,
                )
            ]
            if subject:
                self.status.value = "0 archivos Markdown disponibles."
            return
        self.file_list.controls = [self._file_tile(path) for path in files]
        self.status.value = f"{len(files)} archivo(s) Markdown disponibles."

    def _file_tile(self, path: Path) -> ft.Control:
        size_kb = max(1, round(path.stat().st_size / 1024))
        return ft.ListTile(
            leading=ft.Icon(ft.Icons.DESCRIPTION_OUTLINED),
            title=ft.Text(path.name),
            subtitle=ft.Text(f"{size_kb} KB · listo para usar"),
            trailing=ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                tooltip="Quitar contexto",
                data=path,
                on_click=self.confirm_delete,
            ),
        )

    def confirm_delete(self, event) -> None:
        if self.app_layout.has_active_recording():
            notify(
                self.main_page,
                "Terminá la grabación antes de quitar material de contexto.",
                warning=True,
            )
            return
        path = Path(event.control.data)

        def delete(_event) -> None:
            self.main_page.pop_dialog()
            try:
                subject = self._selected_subject()
                if not subject:
                    raise ValueError("No hay una materia seleccionada")
                delete_fixed_reference(path, config_path(), subject)
                self._refresh_files()
                self._safe_update()
                notify(self.main_page, f"Se quitó {path.name} del contexto.")
            except (OSError, ValueError) as exc:
                notify(self.main_page, f"No se pudo quitar {path.name}: {exc}", error=True)

        self.main_page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Quitar material de contexto"),
                content=ft.Text(
                    f"Se eliminará {path.name} de la carpeta administrada de la materia."
                ),
                actions=[
                    ft.TextButton(
                        content=ft.Text("Cancelar"),
                        on_click=lambda _e: self.main_page.pop_dialog(),
                    ),
                    ft.FilledButton(content=ft.Text("Quitar"), on_click=delete),
                ],
            )
        )

    def _safe_update(self) -> None:
        try:
            if self.view.page:
                self.view.update()
        except (RuntimeError, AssertionError):
            pass
