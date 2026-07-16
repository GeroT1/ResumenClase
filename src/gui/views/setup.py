from __future__ import annotations

import re
import subprocess
import webbrowser

import flet as ft

from gui.config_provider import get_config
from gui.config_writer import read_config_data, write_config_data
from gui.helpers import notify
from gui.theme import palette
from resumen_clase.secrets import delete_anthropic_api_key, save_anthropic_api_key
from resumen_clase.system_setup import SetupStatus, diagnose, find_executable


class SetupView:
    """Asistente revisitable. Sólo diagnostica hasta que el usuario pulsa una acción."""

    def __init__(self, page: ft.Page, app_layout):
        self.main_page = page
        self.app_layout = app_layout
        self.cfg = get_config()
        colors = palette(self.cfg.gui.theme)
        self.colors = colors
        self._busy = False

        self.whisper_icon, self.whisper_status = self._status("Comprobando el modelo...")
        self.gpu_icon, self.gpu_status = self._status("Detectando hardware...")
        self.ffmpeg_icon, self.ffmpeg_status = self._status("Buscando FFmpeg...")
        self.ollama_icon, self.ollama_status = self._status("Consultando Ollama...")
        self.claude_icon, self.claude_status = self._status("Revisando almacenamiento seguro...")
        self.progress = ft.ProgressBar(visible=False)
        self.refresh_button = ft.OutlinedButton(
            content=ft.Text("Volver a comprobar"), icon=ft.Icons.REFRESH,
            on_click=lambda _e: self.refresh(force=True),
        )
        self.download_whisper_button = ft.FilledButton(
            content=ft.Text("Descargar modelo"), icon=ft.Icons.DOWNLOAD,
            on_click=self._confirm_whisper_download,
        )
        self.cpu_button = ft.OutlinedButton(
            content=ft.Text("Usar CPU + int8"), icon=ft.Icons.MEMORY,
            on_click=self._configure_cpu,
        )
        self.gpu_button = ft.OutlinedButton(
            content=ft.Text("Usar GPU NVIDIA"), icon=ft.Icons.SPEED,
            on_click=self._configure_gpu,
        )
        self.start_ollama_button = ft.OutlinedButton(
            content=ft.Text("Iniciar Ollama"), icon=ft.Icons.PLAY_ARROW,
            on_click=self._start_ollama,
        )
        self.pull_model_button = ft.FilledButton(
            content=ft.Text("Descargar modelo de resumen"), icon=ft.Icons.DOWNLOAD,
            on_click=self._confirm_ollama_pull,
        )
        self.claude_key = ft.TextField(
            label="Nueva API key de Anthropic", password=True, can_reveal_password=True,
            width=410, hint_text="sk-ant-...",
        )

        whisper_card = self._card(
            "Transcripción", "Whisper se ejecuta localmente", ft.Icons.RECORD_VOICE_OVER,
            self.whisper_icon, self.whisper_status,
            [self.download_whisper_button, self.cpu_button], colors.accent,
        )
        ffmpeg_card = self._card(
            "Archivos multimedia", "FFmpeg permite importar MP3, MP4 y MKV", ft.Icons.MOVIE_FILTER,
            self.ffmpeg_icon, self.ffmpeg_status,
            [ft.OutlinedButton(content=ft.Text("Abrir descarga oficial"),
                               icon=ft.Icons.OPEN_IN_NEW,
                               on_click=lambda _e: self._open_url("https://ffmpeg.org/download.html#build-windows"))],
            colors.accent2,
        )
        ollama_card = self._card(
            "Resúmenes locales", f"Ollama · modelo configurado: {self.cfg.llm.model}", ft.Icons.AUTO_AWESOME,
            self.ollama_icon, self.ollama_status,
            [
                ft.OutlinedButton(content=ft.Text("Descargar Ollama"), icon=ft.Icons.OPEN_IN_NEW,
                                  on_click=lambda _e: self._open_url("https://ollama.com/download/windows")),
                self.start_ollama_button, self.pull_model_button,
            ], colors.accent3,
        )
        claude_card = self._card(
            "Claude (opcional)", "Sólo describe imágenes al convertir contexto fijo", ft.Icons.KEY,
            self.claude_icon, self.claude_status,
            [
                self.claude_key,
                ft.FilledButton(content=ft.Text("Guardar clave de forma segura"), icon=ft.Icons.LOCK,
                                on_click=self._save_claude_key),
                ft.OutlinedButton(content=ft.Text("Quitar clave guardada"), icon=ft.Icons.DELETE_OUTLINE,
                                  on_click=self._delete_claude_key),
            ], colors.warning,
        )
        gpu_card = self._card(
            "Aceleración", "La GPU NVIDIA es opcional", ft.Icons.SPEED,
            self.gpu_icon, self.gpu_status, [self.gpu_button], colors.success,
        )

        self.view = ft.Column(
            [
                ft.Row([
                    ft.Column([
                        ft.Text("Preparación del sistema", size=28, weight=ft.FontWeight.W_600),
                        ft.Text("Comprobá qué está listo y completá sólo lo que necesitás.", color=colors.muted),
                    ], expand=True, spacing=3),
                    self.refresh_button,
                ]),
                self.progress,
                ft.Container(
                    ft.ListView([
                        ft.ResponsiveRow([
                            ft.Container(ft.Column([whisper_card, gpu_card], spacing=14), col={"xs": 12, "lg": 6}),
                            ft.Container(ft.Column([ollama_card, ffmpeg_card, claude_card], spacing=14), col={"xs": 12, "lg": 6}),
                        ], columns=12, spacing=14, run_spacing=14),
                        ft.Container(
                            ft.Row([
                                ft.Icon(ft.Icons.PRIVACY_TIP_OUTLINED, color=colors.accent2),
                                ft.Text("Nada se instala ni se descarga automáticamente. Claude es opcional; grabar WAV no requiere FFmpeg.", expand=True),
                                ft.FilledButton(content=ft.Text("Continuar a Inicio"), icon=ft.Icons.ARROW_FORWARD,
                                                on_click=self._complete),
                            ], wrap=True),
                            bgcolor=colors.surface_high, border_radius=12, padding=16,
                        ),
                    ], expand=True, spacing=14, padding=ft.Padding.only(right=8, bottom=8)),
                    expand=True, bgcolor=colors.canvas, border_radius=12, padding=10,
                ),
            ], expand=True, spacing=12,
        )

    def _status(self, message: str):
        return ft.Icon(ft.Icons.HOURGLASS_TOP, color=self.colors.muted), ft.Text(message, expand=True)

    def _card(self, title, subtitle, icon, status_icon, status_text, actions, accent, include_actions=True):
        status_row = ft.Row([status_icon, status_text], vertical_alignment=ft.CrossAxisAlignment.START)
        rows = [
            ft.Row([ft.Icon(icon, color=accent), ft.Column([
                ft.Text(title, size=18, weight=ft.FontWeight.W_600),
                ft.Text(subtitle, size=12, color=self.colors.muted),
            ], expand=True, spacing=2)]),
            ft.Divider(), status_row,
        ]
        if include_actions:
            rows.append(ft.Row(actions, wrap=True, spacing=8))
        return ft.Card(elevation=0, bgcolor=self.colors.card, content=ft.Container(
            ft.Column(rows, spacing=12), padding=18,
            border=ft.Border(left=ft.BorderSide(4, accent)), border_radius=12,
        ))

    def refresh(self, force: bool = False) -> None:
        if self._busy:
            return
        self._set_busy(True, "Comprobando componentes locales...")
        self.main_page.run_thread(self._refresh_worker)

    def _refresh_worker(self) -> None:
        try:
            status = diagnose(get_config())
            self._apply_status(status)
        except Exception as exc:
            notify(self.main_page, f"No se pudo completar el diagnóstico: {exc}", error=True)
        finally:
            self._set_busy(False)

    def _apply_status(self, status: SetupStatus) -> None:
        self._set_status(self.whisper_icon, self.whisper_status, status.whisper_cached, status.whisper_detail)
        self._set_status(self.gpu_icon, self.gpu_status, status.cuda_runtime_ready, status.gpu_detail, optional=True)
        self._set_status(self.ffmpeg_icon, self.ffmpeg_status, bool(status.ffmpeg_path),
                         f"Disponible: {status.ffmpeg_path}" if status.ffmpeg_path else "No está instalado. WAV y grabación en vivo siguen funcionando.", optional=True)
        configured = self.cfg.llm.model
        model_ready = configured in status.ollama_models
        if status.ollama_running:
            ollama_text = (f"Ollama responde y {configured} está disponible." if model_ready else
                           f"Ollama responde, pero falta descargar {configured}.")
        elif status.ollama_path:
            ollama_text = "Ollama está instalado, pero su servidor no responde."
        else:
            ollama_text = "Ollama no está instalado."
        self._set_status(self.ollama_icon, self.ollama_status,
                         status.ollama_running and model_ready, ollama_text)
        self._set_status(self.claude_icon, self.claude_status, status.claude_ready,
                         status.claude_detail, optional=True)
        self.download_whisper_button.disabled = status.whisper_cached
        self.gpu_button.disabled = not status.cuda_runtime_ready
        self.start_ollama_button.disabled = not status.ollama_path or status.ollama_running
        self.pull_model_button.disabled = not status.ollama_running or model_ready
        self._safe_update()

    def _set_status(self, icon, text, ready: bool, message: str, optional: bool = False) -> None:
        icon.name = ft.Icons.CHECK_CIRCLE if ready else (ft.Icons.INFO if optional else ft.Icons.WARNING_AMBER)
        icon.color = self.colors.success if ready else (self.colors.warning if not optional else self.colors.muted)
        text.value = message

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.progress.visible = busy
        self.refresh_button.disabled = busy
        if message:
            self.progress.tooltip = message
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.view.update()
        except (RuntimeError, AssertionError):
            pass

    def _open_url(self, url: str) -> None:
        if not webbrowser.open(url):
            notify(self.main_page, f"No se pudo abrir el navegador. Visitá {url}", warning=True)

    def _configure_cpu(self, _e) -> None:
        try:
            data = read_config_data()
            data.setdefault("whisper", {}).update(device="cpu", compute_type="int8")
            self.cfg = write_config_data(data)
            notify(self.main_page, "Whisper quedó configurado para CPU + int8.")
            self.refresh()
        except Exception as exc:
            notify(self.main_page, f"No se pudo guardar la configuración: {exc}", error=True)

    def _configure_gpu(self, _e) -> None:
        try:
            data = read_config_data()
            data.setdefault("whisper", {}).update(device="cuda", compute_type="int8_float16")
            self.cfg = write_config_data(data)
            notify(self.main_page, "Whisper quedó configurado para GPU NVIDIA.")
            self.refresh()
        except Exception as exc:
            notify(self.main_page, f"No se pudo guardar la configuración: {exc}", error=True)

    def _confirm_whisper_download(self, _e) -> None:
        model = get_config().whisper.model
        self.main_page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Descargar modelo Whisper"),
            content=ft.Text(f"Se descargará '{model}' desde Hugging Face. Puede ocupar varios GB según el modelo."),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=lambda _e: self.main_page.pop_dialog()),
                ft.FilledButton(content=ft.Text("Descargar"), on_click=lambda _e: self._download_whisper()),
            ],
        ))

    def _download_whisper(self) -> None:
        self.main_page.pop_dialog()
        self._set_busy(True, "Descargando modelo Whisper...")
        self.main_page.run_thread(self._download_whisper_worker)

    def _download_whisper_worker(self) -> None:
        try:
            from faster_whisper.utils import download_model
            model = get_config().whisper.model
            path = download_model(model)
            notify(self.main_page, f"Modelo {model} descargado en {path}.")
        except Exception as exc:
            notify(self.main_page, f"Falló la descarga de Whisper: {exc}", error=True)
        finally:
            self._busy = False
            self._refresh_worker()

    def _start_ollama(self, _e) -> None:
        executable = find_executable("ollama")
        if not executable:
            notify(self.main_page, "Ollama no está instalado.", warning=True)
            return
        try:
            subprocess.Popen([executable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                                            getattr(subprocess, "DETACHED_PROCESS", 0)))
            notify(self.main_page, "Ollama se está iniciando. Volvé a comprobar en unos segundos.")
        except Exception as exc:
            notify(self.main_page, f"No se pudo iniciar Ollama: {exc}", error=True)

    def _confirm_ollama_pull(self, _e) -> None:
        model = get_config().llm.model
        self.main_page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("Descargar modelo de resumen"),
            content=ft.Text(f"Ollama descargará '{model}'. Los modelos suelen ocupar varios GB."),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=lambda _e: self.main_page.pop_dialog()),
                ft.FilledButton(content=ft.Text("Descargar"), on_click=lambda _e: self._pull_ollama()),
            ],
        ))

    def _pull_ollama(self) -> None:
        self.main_page.pop_dialog()
        model = get_config().llm.model
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", model):
            notify(self.main_page, "El nombre del modelo contiene caracteres no válidos.", error=True)
            return
        self._set_busy(True, f"Descargando {model} con Ollama...")
        self.main_page.run_thread(self._pull_ollama_worker, model)

    def _pull_ollama_worker(self, model: str) -> None:
        try:
            executable = find_executable("ollama")
            if not executable:
                raise RuntimeError("Ollama no está instalado")
            result = subprocess.run([executable, "pull", model], capture_output=True, text=True,
                                    timeout=7200, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout).strip()[-500:])
            notify(self.main_page, f"Modelo {model} descargado.")
        except Exception as exc:
            notify(self.main_page, f"Falló la descarga del modelo: {exc}", error=True)
        finally:
            self._busy = False
            self._refresh_worker()

    def _save_claude_key(self, _e) -> None:
        try:
            value = (self.claude_key.value or "").strip()
            if not value.startswith("sk-ant-"):
                raise ValueError("La clave de Anthropic debería comenzar con sk-ant-")
            save_anthropic_api_key(value)
            self.claude_key.value = ""
            from resumen_clase.references import reset_markitdown
            reset_markitdown()
            notify(self.main_page, "Clave guardada en el Administrador de credenciales de Windows.")
            self.refresh()
        except Exception as exc:
            notify(self.main_page, f"No se pudo guardar la clave: {exc}", error=True)

    def _delete_claude_key(self, _e) -> None:
        try:
            removed = delete_anthropic_api_key()
            from resumen_clase.references import reset_markitdown
            reset_markitdown()
            notify(self.main_page, "Clave guardada eliminada." if removed else "No había una clave guardada.")
            self.refresh()
        except Exception as exc:
            notify(self.main_page, f"No se pudo eliminar la clave: {exc}", error=True)

    def _complete(self, _e) -> None:
        try:
            data = read_config_data()
            data.setdefault("gui", {})["setup_completed"] = True
            write_config_data(data)
            self.app_layout.show_home()
        except Exception as exc:
            notify(self.main_page, f"No se pudo completar la preparación: {exc}", error=True)
