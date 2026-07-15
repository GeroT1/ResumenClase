from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import flet as ft


def notify(
    page: ft.Page,
    message: str,
    *,
    error: bool = False,
    warning: bool = False,
) -> None:
    page.show_dialog(
        ft.SnackBar(
            content=ft.Text(message),
            bgcolor=ft.Colors.RED_800 if error else ft.Colors.AMBER_800 if warning else None,
            show_close_icon=True,
        )
    )


def class_stem(subject: str, optional_name: str = "", prefix: str = "clase") -> str:
    from gui.config_provider import get_config

    suffix = re.sub(r"[^\w.-]+", "-", optional_name.strip(), flags=re.UNICODE)
    suffix = suffix.strip("-._")
    base = f"{prefix}-{datetime.now().strftime('%m-%d')}"
    if suffix:
        base = f"{base}-{suffix}"
    return get_config().unique_stem(base, subject, datetime.now().year)


def file_names(paths: list[Path], empty: str) -> str:
    if not paths:
        return empty
    if len(paths) <= 2:
        return ", ".join(p.name for p in paths)
    return f"{paths[0].name}, {paths[1].name} y {len(paths) - 2} más"
