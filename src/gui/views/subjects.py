from __future__ import annotations

import re

import flet as ft
import yaml

from gui.config_provider import get_config
from gui.config_writer import read_config_data, write_config_data
from gui.helpers import notify
from gui.theme import palette


class SubjectsView:
    def __init__(self, page: ft.Page, app_layout):
        self.main_page = page
        self.app_layout = app_layout
        self.selected_key: str | None = None
        self.subjects = dict(get_config().subjects)
        colors = palette(get_config().gui.theme)

        self.subject_list = ft.ListView(expand=True, spacing=5)
        self.key_field = ft.TextField(label="Identificador", hint_text="ej: sistemas_operativos")
        self.name_field = ft.TextField(label="Nombre visible")
        self.prompt_field = ft.TextField(
            label="Prompt del resumen",
            multiline=True,
            min_lines=14,
            max_lines=28,
            expand=True,
            hint_text="Indicaciones para generar el resumen de esta materia...",
        )
        self.delete_button = ft.TextButton(
            content=ft.Text("Eliminar"), icon=ft.Icons.DELETE_OUTLINE, on_click=self.confirm_delete, disabled=True
        )
        self.save_button = ft.FilledButton(
            content=ft.Text("Guardar materia"), icon=ft.Icons.SAVE, on_click=self.save_subject
        )
        self._rebuild_list()

        left = ft.Container(
            ft.Column([
                ft.Row([
                    ft.Text("Materias", size=20, weight=ft.FontWeight.W_600),
                    ft.IconButton(icon=ft.Icons.ADD, tooltip="Nueva materia", on_click=self.new_subject),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Text("Cada materia tiene su contexto y carpetas de salida.", size=12, color=colors.muted),
                ft.Divider(),
                self.subject_list,
            ]),
            width=290,
            padding=16,
            bgcolor=colors.card,
            border_radius=12,
        )
        form = ft.Column([
            ft.Text("Editar materia", size=24, weight=ft.FontWeight.W_600),
            ft.Row([self.key_field, self.name_field], wrap=True),
            ft.Text(
                "El contexto automático se lee desde referencias/<identificador>/.",
                size=12,
                color=colors.muted,
            ),
            ft.Text(
                "Los resultados se ordenan en output/<identificador>/<año>/.",
                size=12,
                color=colors.muted,
            ),
            self.prompt_field,
            ft.Container(
                ft.Row([self.delete_button, self.save_button], alignment=ft.MainAxisAlignment.END),
                bgcolor=colors.surface_high,
                border=ft.Border(top=ft.BorderSide(1, colors.outline)),
                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            ),
        ], expand=True)
        self.view = ft.Column([
            ft.Text("Administración de materias", size=28, weight=ft.FontWeight.W_600),
            ft.Row([left, ft.Container(form, expand=True, padding=ft.Padding.only(left=18))], expand=True),
        ], expand=True)

        if self.subjects:
            self._load_subject(next(iter(self.subjects)))
        else:
            self.new_subject(None)

    def _rebuild_list(self) -> None:
        self.subject_list.controls = [
            ft.ListTile(
                leading=ft.Icon(ft.Icons.FOLDER_OUTLINED),
                title=ft.Text(data.get("name", key)),
                subtitle=ft.Text(key),
                data=key,
                selected=key == self.selected_key,
                on_click=lambda e: self._load_subject(e.control.data),
            )
            for key, data in self.subjects.items()
        ]

    def _load_subject(self, key: str) -> None:
        self.selected_key = key
        data = self.subjects[key]
        self.key_field.value = key
        self.key_field.disabled = True
        self.name_field.value = data.get("name", key)
        self.prompt_field.value = data.get("summary_system", "")
        self.delete_button.disabled = False
        self._rebuild_list()
        try:
            self.view.update()
        except RuntimeError:
            pass

    def new_subject(self, _e) -> None:
        self.selected_key = None
        self.key_field.value = ""
        self.key_field.disabled = False
        self.name_field.value = ""
        self.prompt_field.value = ""
        self.delete_button.disabled = True
        self._rebuild_list()
        try:
            self.view.update()
        except RuntimeError:
            pass

    def save_subject(self, _e) -> None:
        key = (self.key_field.value or "").strip().lower()
        name = (self.name_field.value or "").strip()
        prompt = (self.prompt_field.value or "").strip()
        if not re.fullmatch(r"[a-z0-9_-]+", key):
            notify(self.main_page, "El identificador solo admite letras minúsculas, números, _ y -.", error=True)
            return
        if not name or not prompt:
            notify(self.main_page, "Completá el nombre y el prompt.", error=True)
            return
        if self.selected_key is None and key in self.subjects:
            notify(self.main_page, "Ya existe una materia con ese identificador.", error=True)
            return
        try:
            data = read_config_data()
            subjects = data.setdefault("subjects", {})
            subjects[key] = {"name": name, "summary_system": prompt}
            cfg = write_config_data(data)
            cfg.references_dir().joinpath(key).mkdir(parents=True, exist_ok=True)
            self.subjects = dict(cfg.subjects)
            self._load_subject(key)
            notify(self.main_page, "Materia guardada.")
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            notify(self.main_page, f"No se pudo guardar la materia: {exc}", error=True)

    def confirm_delete(self, _e) -> None:
        if not self.selected_key:
            return
        key = self.selected_key

        def delete(_event) -> None:
            self.main_page.pop_dialog()
            try:
                data = read_config_data()
                data.get("subjects", {}).pop(key, None)
                cfg = write_config_data(data)
                self.subjects = dict(cfg.subjects)
                self.new_subject(None)
                notify(self.main_page, "Materia eliminada. Sus archivos y referencias se conservaron.")
            except Exception as exc:
                notify(self.main_page, f"No se pudo eliminar: {exc}", error=True)

        self.main_page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Eliminar materia"),
            content=ft.Text("Se quitará la configuración, pero no sus audios, resúmenes ni referencias."),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=lambda _e: self.main_page.pop_dialog()),
                ft.FilledButton(content=ft.Text("Eliminar"), on_click=delete),
            ],
        ))
