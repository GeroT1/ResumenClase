from __future__ import annotations

import os
from typing import Callable

import yaml

from gui.config_provider import config_path, reload_config
from resumen_clase.config import Config


def read_config_data() -> dict:
    return yaml.safe_load(config_path().read_text(encoding="utf-8")) or {}


def write_config_data(data: dict) -> Config:
    path = config_path()
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        Config.load(temporary)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return reload_config()
