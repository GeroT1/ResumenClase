from __future__ import annotations

from dataclasses import dataclass

import flet as ft


@dataclass(frozen=True)
class Palette:
    mode: ft.ThemeMode
    canvas: str
    surface: str
    surface_high: str
    rail: str
    card: str
    accent: str
    accent_soft: str
    accent2: str
    accent2_soft: str
    accent3: str
    accent3_soft: str
    success: str
    warning: str
    text: str
    muted: str
    outline: str
    rail_text: str


THEMES: dict[str, Palette] = {
    "midnight": Palette(
        mode=ft.ThemeMode.DARK,
        canvas="#070C18", surface="#0E1728", surface_high="#17233A",
        rail="#171A38", card="#182943",
        accent="#7C9CFF", accent_soft="#293B70",
        accent2="#45D0C1", accent2_soft="#174A4B",
        accent3="#C58CFF", accent3_soft="#442D61",
        success="#59D98E", warning="#F6C85F",
        text="#F1F5FF", muted="#9DAFC8", outline="#314764", rail_text="#F3F0FF",
    ),
    "aurora": Palette(
        mode=ft.ThemeMode.DARK,
        canvas="#061416", surface="#0B2224", surface_high="#143335",
        rail="#112C31", card="#173A3B",
        accent="#55D6BE", accent_soft="#1C554F",
        accent2="#72A7FF", accent2_soft="#254568",
        accent3="#F29CC2", accent3_soft="#5A3045",
        success="#8BE28B", warning="#F1C75B",
        text="#EBFFFB", muted="#99C8C1", outline="#2C5A58", rail_text="#E8FFFB",
    ),
    "graphite": Palette(
        mode=ft.ThemeMode.DARK,
        canvas="#0E1014", surface="#171A20", surface_high="#222731",
        rail="#2A1C16", card="#252B35",
        accent="#F2A65A", accent_soft="#5A3822",
        accent2="#4FC6B4", accent2_soft="#1C4C48",
        accent3="#A993FF", accent3_soft="#40355E",
        success="#75D69C", warning="#FFD166",
        text="#F7F7F8", muted="#AEB3BD", outline="#414957", rail_text="#FFF3E8",
    ),
    "linen": Palette(
        mode=ft.ThemeMode.LIGHT,
        canvas="#EDE9E1", surface="#FAF7F1", surface_high="#F0EADF",
        rail="#DCE9E6", card="#FFFFFF",
        accent="#32727A", accent_soft="#C5E2E0",
        accent2="#D96C5F", accent2_soft="#F6D8D2",
        accent3="#9A7426", accent3_soft="#F1E2B8",
        success="#4F8B62", warning="#B7791F",
        text="#202B32", muted="#66747C", outline="#D2CBC0", rail_text="#23383B",
    ),
}


def palette(name: str) -> Palette:
    return THEMES.get(name, THEMES["midnight"])


def apply_theme(page: ft.Page, name: str) -> None:
    colors = palette(name)
    scheme = ft.ColorScheme(
        primary=colors.accent,
        on_primary="#111319" if colors.mode == ft.ThemeMode.DARK else "#FFFFFF",
        primary_container=colors.accent_soft,
        on_primary_container=colors.text,
        secondary=colors.accent2,
        secondary_container=colors.accent2_soft,
        on_secondary_container=colors.text,
        tertiary=colors.accent3,
        tertiary_container=colors.accent3_soft,
        on_tertiary_container=colors.text,
        surface=colors.surface,
        surface_container=colors.card,
        surface_container_high=colors.surface_high,
        on_surface=colors.text,
        on_surface_variant=colors.muted,
        outline=colors.outline,
        outline_variant=colors.outline,
    )
    theme = ft.Theme(
        color_scheme=scheme,
        use_material3=True,
        canvas_color=colors.canvas,
        scaffold_bgcolor=colors.canvas,
        card_bgcolor=colors.card,
        divider_color=colors.outline,
        navigation_rail_theme=ft.NavigationRailTheme(
            bgcolor=colors.rail,
            indicator_color=colors.accent_soft,
            selected_label_text_style=ft.TextStyle(color=colors.rail_text, weight=ft.FontWeight.W_600),
            unselected_label_text_style=ft.TextStyle(color=colors.rail_text),
        ),
        scrollbar_theme=ft.ScrollbarTheme(
            thumb_color=colors.muted,
            track_color=colors.surface_high,
            thickness=7,
            radius=8,
        ),
    )
    page.theme_mode = colors.mode
    page.theme = theme
    page.dark_theme = theme
    page.bgcolor = colors.canvas
