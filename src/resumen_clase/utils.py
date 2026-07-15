"""Utilidades compartidas entre CLI y GUI (sin dependencias pesadas)."""
from __future__ import annotations


def fmt_duration(seconds: float) -> str:
    """Formato MM:SS, HH:MM:SS si pasa la hora, o '<N>s' si es < 1 min."""
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
