from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from resumen_clase.config import Config

_config: Config | None = None


def config_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    resource_root = Path(getattr(sys, "_MEIPASS", project_root))
    bundled = project_root / "config.yaml"
    if getattr(sys, "frozen", False):
        storage = os.environ.get("FLET_APP_STORAGE_DATA")
        if storage:
            data_root = Path(storage)
        else:
            local_app_data = os.environ.get("LOCALAPPDATA")
            data_root = (
                Path(local_app_data) / "ResumenClase"
                if local_app_data
                else Path(sys.executable).resolve().parent / "data"
            )
        bundled = data_root / "config.yaml"
    if not bundled.exists():
        example = (
            resource_root / "config.example.yaml"
            if getattr(sys, "frozen", False)
            else bundled.with_name("config.example.yaml")
        )
        if not example.exists():
            raise FileNotFoundError(
                "Falta config.yaml y no existe config.example.yaml para crearlo"
            )
        bundled.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(example, bundled)
    storage = os.environ.get("FLET_APP_STORAGE_DATA")
    if not storage:
        return bundled
    # En desarrollo Flet 0.86 cambia el cwd a .flet/storage/data. Conservamos
    # el config/output/referencias del proyecto para no crear una copia paralela.
    if ".flet" in Path(storage).parts and bundled.exists():
        return bundled
    target = Path(storage) / "config.yaml"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, target)
    return target

def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.load(config_path())
    return _config


def reload_config() -> Config:
    global _config
    _config = Config.load(config_path())
    return _config
