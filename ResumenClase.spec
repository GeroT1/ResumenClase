# -*- mode: python ; coding: utf-8 -*-
"""Empaquetado reproducible de ResumenClase para Windows con PyInstaller."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPEC).resolve().parent
datas = [
    (str(ROOT / "config.example.yaml"), "."),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "build" / "support" / "flet-windows.zip"), "flet_desktop/app"),
]
binaries = []
hiddenimports = []

# Estos paquetes cargan módulos, datos o DLL de forma dinámica.
for package in (
    "av",
    "charset_normalizer",
    "ctranslate2",
    "faster_whisper",
    "flet_desktop",
    "magika",
    "mammoth",
    "markdownify",
    "markitdown",
    "nvidia.cublas",
    "nvidia.cudnn",
    "nvidia.cuda_nvrtc",
    "onnxruntime",
    "pdfminer",
    "pdfplumber",
    "pptx",
    "pypdfium2",
    "soundcard",
    "soundfile",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    [str(ROOT / "main_gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ResumenClase",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon_windows.ico"),
    version=str(ROOT / "assets" / "version_info.txt"),
)

bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ResumenClase",
)
