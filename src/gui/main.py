from __future__ import annotations

import sys
from pathlib import Path

import flet as ft

from gui.app import AppLayout
from gui.config_provider import get_config
from gui.theme import apply_theme
from resumen_clase.config import safe_path_component


def main(page: ft.Page) -> None:
    page.title = "ResumenClase"
    page.window.width = 1100
    page.window.height = 720
    page.window.min_width = 820
    page.window.min_height = 560
    page.window.icon = "icon_windows.ico"
    page.padding = 0
    cfg = get_config()
    cfg.migrate_legacy_outputs()
    for subject_key in cfg.subjects:
        cfg.references_dir().joinpath(
            safe_path_component(subject_key, "sin_materia")
        ).mkdir(parents=True, exist_ok=True)
    apply_theme(page, cfg.gui.theme)
    layout = AppLayout(page)
    page.add(layout)
    layout.refresh_initial_view()


def run() -> None:
    project_root = Path(__file__).resolve().parents[2]
    resource_root = Path(getattr(sys, "_MEIPASS", project_root))
    ft.run(main, assets_dir=str(resource_root / "assets"))


if __name__ == "__main__":
    run()
