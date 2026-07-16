"""Almacenamiento local de secretos de ResumenClase."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys

_TARGET = "ResumenClase/AnthropicApiKey"
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _advapi32():
    if sys.platform != "win32":
        raise RuntimeError("El almacenamiento seguro integrado sólo está disponible en Windows")
    dll = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    dll.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    dll.CredWriteW.restype = wintypes.BOOL
    dll.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    dll.CredReadW.restype = wintypes.BOOL
    dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    dll.CredDeleteW.restype = wintypes.BOOL
    dll.CredFree.argtypes = [ctypes.c_void_p]
    dll.CredFree.restype = None
    return dll


def stored_anthropic_api_key() -> str | None:
    """Lee sólo Credential Manager, sin considerar variables de entorno."""
    if sys.platform != "win32":
        return None
    dll = _advapi32()
    pointer = ctypes.POINTER(_CREDENTIALW)()
    if not dll.CredReadW(_TARGET, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        item = pointer.contents
        raw = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
        return raw.decode("utf-8") or None
    finally:
        dll.CredFree(pointer)


def get_anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or stored_anthropic_api_key()


def save_anthropic_api_key(value: str) -> None:
    value = value.strip()
    if not value:
        raise ValueError("La clave no puede estar vacía")
    raw = value.encode("utf-8")
    if len(raw) > 512:
        raise ValueError("La clave es demasiado larga")
    dll = _advapi32()
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = _CREDENTIALW(
        Type=_CRED_TYPE_GENERIC, TargetName=_TARGET,
        CredentialBlobSize=len(raw),
        CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
        Persist=_CRED_PERSIST_LOCAL_MACHINE, UserName="Anthropic API",
        Comment="Clave de Claude para ResumenClase",
    )
    if not dll.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def delete_anthropic_api_key() -> bool:
    if sys.platform != "win32":
        return False
    dll = _advapi32()
    if dll.CredDeleteW(_TARGET, _CRED_TYPE_GENERIC, 0):
        return True
    if ctypes.get_last_error() == 1168:
        return False
    raise ctypes.WinError(ctypes.get_last_error())
