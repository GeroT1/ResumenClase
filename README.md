# ResumenClase

Transcribe + resume clases virtuales (Meet) con IA 100% local. RTX 3070+ recomendado.

## Stack
- **faster-whisper** (CTranslate2) — transcripción GPU, int8_float16 ~2GB VRAM con `large-v3`
- **soundcard** — loopback WASAPI nativo Windows (captura lo que sale por speakers, sin VB-Cable)
- **Ollama** — LLM local para resumen (Llama 3.1 8B Q4 ~5GB VRAM)
- **ffmpeg** — extrae audio de mp4/mkv del profe

VRAM total 3070 (8GB) no alcanza para Whisper + LLM simultáneos. El programa **libera Whisper antes de cargar LLM** vía context manager + `torch.cuda.empty_cache()`.

## Setup

### 1. Deps sistema
- **Python 3.13**
- **ffmpeg** en PATH → `winget install ffmpeg` o `choco install ffmpeg`
- **CUDA 12.x** drivers NVIDIA
- **cuDNN 8.x** para CTranslate2 (faster-whisper lo pide). Descargá de NVIDIA o instalá `nvidia-cudnn-cu12` via pip
- **Ollama** → https://ollama.com/download

### 2. Ollama models
```bash
ollama pull llama3.1:8b
# alternativas mejores si tenés VRAM libre:
# ollama pull qwen2.5:7b      (bueno en español)
# ollama pull gemma2:9b
```

### 3. Python env (usando uv — recomendado)
```bash
# instalar uv si no tenés: https://docs.astral.sh/uv/
uv venv
uv pip install -e .
# cuDNN si hace falta:
uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

O con pip clásico:
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Creá tu configuración local antes de iniciar. Este archivo no se versiona:

```powershell
Copy-Item config.example.yaml config.yaml
```

### 4. Verificar audio devices
```bash
resumen devices
```

## Uso

### Modo LIVE (durante la clase en Meet)
1. Unite a Meet normal. Audio sale por tus speakers/auriculares.
2. En otra terminal:
```bash
resumen live
# o con nombre:
resumen live -n clase-semana-5
```
3. Cuando termine la clase → `Ctrl+C`. Genera transcript + resumen.

En la GUI podés seguir navegando por resúmenes, materias y ajustes mientras se graba. Una barra fija conserva el estado y permite volver a los controles. Las combinaciones iniciadas durante una captura se encolan hasta que termine para no competir por GPU con Whisper/Ollama.

### Material de contexto

La vista **Contexto** administra el material fijo de cada materia. Al importar un
PDF, documento, presentación, planilla, página HTML o imagen, MarkItDown lo
convierte una sola vez y guarda únicamente el resultado `.md` dentro de
`referencias/<materia>/`. El archivo externo elegido se conserva. Si ya había
originales dentro de esa carpeta administrada, **Convertir archivos existentes**
los elimina sólo después de comprobar que el Markdown generado no está vacío.

Las grabaciones y combinaciones encuentran automáticamente esos `.md`. El material
extra elegido para una clase puntual sólo admite `.md` y `.txt`: se lee directamente
y no ejecuta MarkItDown en el camino crítico de la grabación. Para evitar competir
por recursos, la vista no permite comenzar una conversión mientras se está grabando.

### Modo FILE (grabación del profe)
```bash
resumen file "C:\path\clase.mp4"
resumen file clase.mp4 -n clase-semana-5
resumen file clase.mp4 --no-summary
```

### Solo resumen de un transcript existente
```bash
resumen summarize-only output/<materia>/<año>/transcripts/clase-04-17.plain.txt
```

## Output
```
output/
└── <materia>/
    └── <año>/
        ├── audio/clase-MM-DD.wav
        ├── transcripts/clase-MM-DD.txt        (con timestamps [mm:ss])
        ├── transcripts/clase-MM-DD.plain.txt  (sin timestamps)
        └── summaries/clase-MM-DD.md
```

Los nombres generados no repiten la materia, el año ni la hora: esa información
está en la ruta y la hora exacta queda disponible en los metadatos y logs de diagnóstico.
Si hay más de una clase el mismo día se agregan sufijos (`-2`, `-3`, etc.).

## Config

Copiá `config.example.yaml` como `config.yaml` y ajustá modelo Whisper, LLM,
VAD, materias, etc. `config.yaml`, `output/` y `referencias/` están ignorados
para evitar publicar datos académicos o configuración personal.

La aplicación usa Ollama en `localhost` de manera predeterminada. Configurar un
host remoto envía allí las transcripciones. MarkItDown convierte documentos al
importarlos desde la vista Contexto; para agregar descripciones de imágenes con Claude definí
`RESUMEN_CLASE_ENABLE_CLAUDE=1` y `ANTHROPIC_API_KEY`. Podés cambiar el modelo
con `RESUMEN_CLASE_CLAUDE_MODEL` (por defecto `claude-sonnet-4-6`). Al habilitarlo,
las imágenes procesadas pueden enviarse a Anthropic; consultá [SECURITY.md](SECURITY.md).

Tips de eficiencia:
- `whisper.model`: `medium` es ~2x más rápido que `large-v3` con calidad aceptable en español
- `whisper.compute_type`: `int8_float16` (default) vs `float16` (más rápido, +1GB VRAM) vs `int8` (CPU fallback)
- `whisper.vad_filter: true` skipea silencios → 30-40% menos tiempo
- `llm.model`: modelos chicos tipo `phi3:mini` resumen rápido si no necesitás calidad máxima

## Troubleshooting

**"Could not load libcudnn_ops_infer.so"** → instalá `nvidia-cudnn-cu12` vía pip.

**Audio loopback vacío** → chequeá que el default speaker de Windows es el mismo al que sale Meet. `resumen devices` para listar.

En Ajustes podés elegir una salida detectada o **Predeterminado de Windows**. Esta última es la opción recomendada si alternás entre auriculares USB y parlantes, porque toma la salida predeterminada que esté activa al comenzar cada grabación.

**OOM GPU** → bajá a `whisper.model: medium` o `compute_type: int8`.

**Ollama no responde** → `ollama serve` en otra terminal.
