# Seguridad y privacidad

ResumenClase procesa audio, transcripciones y material académico local. No adjuntes
estos datos a reportes públicos de errores.

## Datos locales

- `config.yaml`, `output/`, `referencias/` y `queue/` están excluidos de Git.
- Ollama apunta de forma predeterminada a `localhost`. Si configurás otro host, las
  transcripciones se enviarán a ese servidor.
- La integración opcional con Claude se activa al guardar una clave desde la vista
  **Preparación** (Administrador de credenciales de Windows), o al definir conjuntamente
  `RESUMEN_CLASE_ENABLE_CLAUDE=1` y `ANTHROPIC_API_KEY`. La clave no se escribe en
  `config.yaml`. Al activarla, las imágenes procesadas por MarkItDown pueden enviarse
  a Anthropic.
- Procesá únicamente archivos de origen confiable: la conversión depende de ffmpeg,
  MarkItDown y sus parsers de documentos.

## Reportar una vulnerabilidad

Usá el reporte privado de vulnerabilidades de GitHub cuando esté disponible. No
incluyas grabaciones, transcripciones, claves, rutas personales ni documentos reales.
