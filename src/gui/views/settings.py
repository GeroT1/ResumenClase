from __future__ import annotations

import flet as ft
import httpx
import yaml

from gui.config_provider import get_config
from gui.config_writer import read_config_data, write_config_data
from gui.helpers import notify
from gui.theme import THEMES, apply_theme, palette
from resumen_clase.audio import output_devices


WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "turbo"]
COMPUTE_TYPES = ["int8", "int8_float16", "float16", "float32"]
LANGUAGES = [("es", "Español"), ("en", "Inglés"), ("auto", "Detección automática")]
COMMON_LLM_MODELS = [
    "qwen2.5:7b-instruct-q6_K",
    "qwen2.5:14b",
    "llama3.1:8b",
    "gemma2:9b",
    "phi3:mini",
]
_OLLAMA_MODELS_CACHE: dict[str, list[str]] = {}
_AUDIO_DEVICES_CACHE: tuple[list[str], str] | None = None
SYSTEM_DEFAULT_DEVICE = "__windows_default__"


class SettingsView:
    def __init__(self, page: ft.Page, app_layout):
        cfg = get_config()
        self.main_page = page
        self.app_layout = app_layout
        colors = palette(cfg.gui.theme)

        self.whisper_model = self._dropdown("Modelo Whisper", WHISPER_MODELS, cfg.whisper.model, 230)
        self.whisper_device = self._dropdown("Dispositivo", ["cuda", "cpu"], cfg.whisper.device, 180)
        self.compute_type = self._dropdown("Precisión", COMPUTE_TYPES, cfg.whisper.compute_type, 210)
        self.language = ft.Dropdown(
            label="Idioma",
            options=[ft.DropdownOption(key=key, text=label) for key, label in LANGUAGES],
            value=cfg.whisper.language or "auto",
            width=200,
        )
        self.beam_size = ft.TextField(label="Beam size", value=str(cfg.whisper.beam_size), width=140)
        self.vad_filter = ft.Switch(label="Eliminar silencios (VAD)", value=cfg.whisper.vad_filter)

        self.samplerate = self._dropdown(
            "Sample rate", ["16000", "22050", "44100", "48000"], str(cfg.audio.samplerate), 190
        )
        self.chunk_seconds = self._dropdown(
            "Fragmento", ["15", "30", "45", "60", "90"], str(cfg.audio.chunk_seconds), 180,
            suffix=" segundos",
        )
        cached_audio = _AUDIO_DEVICES_CACHE or ([], "")
        self._available_audio_devices = set(cached_audio[0])
        selected_audio = cfg.audio.device_name or SYSTEM_DEFAULT_DEVICE
        self.device_name = ft.Dropdown(
            label="Dispositivo de salida",
            options=self._audio_options(cached_audio[0], cached_audio[1], selected_audio),
            value=selected_audio,
            width=450,
            enable_filter=True,
        )
        self.audio_device_status = ft.Text(
            "Detectando salidas de audio en segundo plano. Usá 'Predeterminado de Windows' para seguir tus cambios de salida automáticamente.",
            size=12,
            color=colors.muted,
        )
        self.refresh_audio_button = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip="Volver a detectar salidas de audio",
            on_click=lambda _e: self.load_audio_devices(force=True),
        )
        self.auto_recover = ft.Switch(label="Recuperar dispositivo automáticamente", value=cfg.audio.auto_recover)

        llm_models = _OLLAMA_MODELS_CACHE.get(cfg.llm.host, [])
        model_options = list(dict.fromkeys([cfg.llm.model, *llm_models, *COMMON_LLM_MODELS]))
        self.llm_host = ft.TextField(label="Servidor Ollama", value=cfg.llm.host, width=330)
        self.llm_model = ft.Dropdown(
            label="Modelo de resumen",
            options=[ft.DropdownOption(model) for model in model_options],
            value=cfg.llm.model,
            editable=True,
            enable_filter=True,
            width=340,
        )
        self.temperature = self._dropdown(
            "Temperatura", ["0.0", "0.1", "0.2", "0.3", "0.5", "0.7", "1.0"],
            str(cfg.llm.temperature), 180,
        )
        self.max_chunk_chars = ft.TextField(
            label="Caracteres por fragmento", value=str(cfg.llm.max_chunk_chars), width=230
        )
        self.model_status = ft.Text("Modelos locales cargándose en segundo plano...", size=12, color=colors.muted)
        self.refresh_models_button = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip="Actualizar modelos de Ollama",
            on_click=lambda _e: self.load_models(force=True),
        )

        self.output_dir = ft.TextField(label="Carpeta de resultados", value=cfg.output.base_dir, width=370)
        self.save_audio = ft.Switch(label="Conservar audio original", value=cfg.output.save_audio)
        self.theme = ft.Dropdown(
            label="Paleta visual",
            options=[
                ft.DropdownOption("midnight", "Midnight azul"),
                ft.DropdownOption("aurora", "Aurora verde"),
                ft.DropdownOption("graphite", "Graphite naranja"),
                ft.DropdownOption("linen", "Linen claro"),
            ],
            value=cfg.gui.theme if cfg.gui.theme in THEMES else "midnight",
            width=240,
        )

        scroll_content = ft.ListView(
            [
                self._card("Transcripción", "Modelo y calidad de reconocimiento", ft.Icons.GRAPHIC_EQ, [
                    ft.Row([self.whisper_model, self.whisper_device, self.compute_type, self.language], wrap=True),
                    ft.Row([self.beam_size, self.vad_filter], wrap=True),
                ], colors.card, colors.accent),
                self._card("Captura de audio", "Origen y tamaño de los fragmentos", ft.Icons.HEADPHONES, [
                    ft.Row([self.samplerate, self.chunk_seconds, self.device_name, self.refresh_audio_button], wrap=True),
                    self.audio_device_status,
                    self.auto_recover,
                ], colors.card, colors.accent2),
                self._card("Resumen con IA", "Conexión y modelo de Ollama", ft.Icons.AUTO_AWESOME, [
                    ft.Row([self.llm_host, self.llm_model, self.refresh_models_button, self.temperature, self.max_chunk_chars], wrap=True),
                    self.model_status,
                ], colors.card, colors.accent3),
                self._card("Archivos e interfaz", "Ubicación de datos y apariencia", ft.Icons.PALETTE_OUTLINED, [
                    ft.Row([self.output_dir, self.save_audio, self.theme], wrap=True),
                    ft.Text(
                        "Los resultados se guardan como output/<materia>/<año>/audio, transcripts y summaries.",
                        size=12,
                        color=colors.muted,
                    ),
                ], colors.card, colors.warning),
            ],
            expand=True,
            spacing=14,
            padding=ft.Padding.only(right=12, bottom=12),
        )
        footer = ft.Container(
            content=ft.Row(
                [ft.FilledButton(content=ft.Text("Guardar cambios"), icon=ft.Icons.SAVE, on_click=self.save_settings)],
                alignment=ft.MainAxisAlignment.END,
            ),
            bgcolor=colors.surface_high,
            border=ft.Border(top=ft.BorderSide(1, colors.outline)),
            padding=ft.Padding.symmetric(horizontal=18, vertical=12),
        )
        recording_notice = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.INFO_OUTLINE, color=colors.warning),
                ft.Text(
                    "Hay una grabación activa. El tema cambia ahora; los ajustes técnicos se aplicarán recién a la próxima grabación.",
                    expand=True,
                ),
            ]),
            bgcolor=colors.accent3_soft,
            border=ft.Border(left=ft.BorderSide(4, colors.warning)),
            border_radius=10,
            padding=12,
            visible=app_layout.has_active_recording(),
        )
        self.view = ft.Column(
            [
                ft.Container(
                    ft.Column([
                        ft.Text("Ajustes", size=28, weight=ft.FontWeight.W_600),
                        ft.Text("Configuración técnica de grabación, transcripción y resumen.", color=colors.muted),
                    ], spacing=3),
                    padding=ft.Padding.only(bottom=16),
                ),
                recording_notice,
                ft.Container(scroll_content, expand=True),
                footer,
            ],
            expand=True,
            spacing=0,
        )

    @staticmethod
    def _dropdown(label, values, value, width, suffix=""):
        values = list(values)
        if value not in values:
            values.insert(0, value)
        return ft.Dropdown(
            label=label,
            options=[ft.DropdownOption(key=v, text=f"{v}{suffix}") for v in values],
            value=value,
            width=width,
        )

    @staticmethod
    def _card(title, subtitle, icon, controls, bgcolor, accent):
        return ft.Card(
            bgcolor=bgcolor,
            elevation=0,
            content=ft.Container(
                ft.Column([
                    ft.Row([ft.Icon(icon), ft.Column([ft.Text(title, size=18, weight=ft.FontWeight.W_600), ft.Text(subtitle, size=12)])]),
                    ft.Divider(),
                    *controls,
                ], spacing=12),
                padding=18,
                border=ft.Border(left=ft.BorderSide(4, accent)),
                border_radius=12,
            ),
        )

    def load_models(self, force: bool = False) -> None:
        host = (self.llm_host.value or "").strip().rstrip("/")
        if not force and host in _OLLAMA_MODELS_CACHE:
            self._apply_models(_OLLAMA_MODELS_CACHE[host])
            return
        self.main_page.run_thread(self._load_models_worker, host)

    def load_audio_devices(self, force: bool = False) -> None:
        global _AUDIO_DEVICES_CACHE
        if not force and _AUDIO_DEVICES_CACHE is not None:
            self._apply_audio_devices(*_AUDIO_DEVICES_CACHE)
            return
        self.main_page.run_thread(self._load_audio_devices_worker)

    def _load_audio_devices_worker(self) -> None:
        global _AUDIO_DEVICES_CACHE
        self.audio_device_status.value = "Consultando salidas de audio de Windows..."
        self.refresh_audio_button.disabled = True
        self._safe_audio_update()
        try:
            devices, default_name = output_devices()
            _AUDIO_DEVICES_CACHE = (devices, default_name)
            self._apply_audio_devices(devices, default_name)
        except Exception as exc:
            self.audio_device_status.value = f"No se pudieron detectar las salidas: {exc}"
            notify(self.main_page, self.audio_device_status.value, warning=True)
        finally:
            self.refresh_audio_button.disabled = False
            self._safe_audio_update()

    def _apply_audio_devices(self, devices: list[str], default_name: str) -> None:
        current = self.device_name.value or SYSTEM_DEFAULT_DEVICE
        if current != SYSTEM_DEFAULT_DEVICE and current not in devices:
            matches = [name for name in devices if current.casefold() in name.casefold()]
            if len(matches) == 1:
                current = matches[0]
        self._available_audio_devices = set(devices)
        self.device_name.options = self._audio_options(devices, default_name, current)
        self.device_name.value = current
        if current != SYSTEM_DEFAULT_DEVICE and current not in self._available_audio_devices:
            self.audio_device_status.value = (
                f"La salida guardada '{current}' no está disponible. Elegí otra o usá la predeterminada."
            )
            notify(self.main_page, self.audio_device_status.value, warning=True)
        else:
            self.audio_device_status.value = (
                f"{len(devices)} salida(s) detectada(s). Predeterminada: {default_name}. "
                "La opción predeterminada sigue los cambios que hagas en Windows."
            )
        self._safe_audio_update()

    @staticmethod
    def _audio_options(devices: list[str], default_name: str, current: str) -> list[ft.DropdownOption]:
        default_label = "Predeterminado de Windows"
        if default_name:
            default_label += f" — {default_name}"
        options = [ft.DropdownOption(key=SYSTEM_DEFAULT_DEVICE, text=default_label)]
        options.extend(ft.DropdownOption(key=name, text=name) for name in devices)
        if current != SYSTEM_DEFAULT_DEVICE and current not in devices:
            options.append(ft.DropdownOption(key=current, text=f"No disponible — {current}"))
        return options

    def _safe_audio_update(self) -> None:
        try:
            self.device_name.update()
            self.audio_device_status.update()
            self.refresh_audio_button.update()
        except (RuntimeError, AssertionError):
            pass

    def _load_models_worker(self, host: str) -> None:
        self.model_status.value = "Consultando Ollama..."
        self.refresh_models_button.disabled = True
        self._safe_model_update()
        models = self._ollama_models(host)
        if models:
            _OLLAMA_MODELS_CACHE[host] = models
            self._apply_models(models)
            self.model_status.value = f"{len(models)} modelo(s) local(es) encontrado(s)."
        else:
            self.model_status.value = "Ollama no respondió; podés usar o escribir cualquier modelo."
        self.refresh_models_button.disabled = False
        self._safe_model_update()

    def _apply_models(self, models: list[str]) -> None:
        current = self.llm_model.value or ""
        values = list(dict.fromkeys([current, *models, *COMMON_LLM_MODELS]))
        self.llm_model.options = [ft.DropdownOption(model) for model in values if model]
        self._safe_model_update()

    def _safe_model_update(self) -> None:
        try:
            self.llm_model.update()
            self.model_status.update()
            self.refresh_models_button.update()
        except RuntimeError:
            pass

    @staticmethod
    def _ollama_models(host: str) -> list[str]:
        try:
            response = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=1.2)
            response.raise_for_status()
            return [model["name"] for model in response.json().get("models", []) if model.get("name")]
        except Exception:
            return []

    @staticmethod
    def _integer(field: ft.TextField, minimum: int = 1) -> int:
        value = int(field.value or "")
        if value < minimum:
            raise ValueError(f"{field.label} debe ser al menos {minimum}")
        return value

    def save_settings(self, _e) -> None:
        try:
            temperature = float(self.temperature.value or "")
            if not 0 <= temperature <= 2:
                raise ValueError("Temperatura debe estar entre 0 y 2")
            selected_audio = self.device_name.value or SYSTEM_DEFAULT_DEVICE
            if (
                selected_audio != SYSTEM_DEFAULT_DEVICE
                and self._available_audio_devices
                and selected_audio not in self._available_audio_devices
            ):
                raise ValueError("La salida de audio seleccionada ya no está disponible")
            data = read_config_data()
            data.setdefault("whisper", {}).update(
                model=self.whisper_model.value,
                device=self.whisper_device.value,
                compute_type=self.compute_type.value,
                language=None if self.language.value == "auto" else self.language.value,
                beam_size=self._integer(self.beam_size),
                vad_filter=bool(self.vad_filter.value),
            )
            data.setdefault("audio", {}).update(
                samplerate=int(self.samplerate.value),
                chunk_seconds=int(self.chunk_seconds.value),
                device_name="" if selected_audio == SYSTEM_DEFAULT_DEVICE else selected_audio,
                auto_recover=bool(self.auto_recover.value),
            )
            data.setdefault("llm", {}).update(
                host=(self.llm_host.value or "").strip().rstrip("/"),
                model=(self.llm_model.value or "").strip(),
                temperature=temperature,
                max_chunk_chars=self._integer(self.max_chunk_chars, 1000),
            )
            data.setdefault("output", {}).update(
                base_dir=(self.output_dir.value or "./output").strip(),
                save_audio=bool(self.save_audio.value),
            )
            data.setdefault("gui", {})["theme"] = self.theme.value or "midnight"
            cfg = write_config_data(data)
            apply_theme(self.main_page, cfg.gui.theme)
            self.app_layout.apply_palette()
            self.app_layout.show_settings()
            self.main_page.update()
            notify(self.main_page, "Ajustes guardados.")
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            notify(self.main_page, f"Configuración inválida: {exc}", error=True)
