"""Cifratura dei segreti salvati in config.json (token del bot Telegram).

Su Windows si usa DPAPI (la stessa protezione delle password salvate di Windows): il valore cifrato
si può leggere solo dallo stesso utente sullo stesso PC, quindi copiare config.json non basta per
rubare il token. Su altri sistemi (es. GitHub Actions, dove il token arriva dai secret) il valore
resta com'è.
"""

from __future__ import annotations

import base64
import os

PREFIX = "dpapi:"


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = Blob(len(data), buf), Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    # flag 0x1 = CRYPTPROTECT_UI_FORBIDDEN: mai finestre di dialogo
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
        raise OSError("DPAPI non disponibile")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def available() -> bool:
    return os.name == "nt"


def protect(value: str) -> str:
    """Valore da salvare su disco (cifrato se possibile)."""
    if not value or value.startswith(PREFIX) or not available():
        return value
    try:
        return PREFIX + base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii")
    except OSError:
        return value


def unprotect(value: str) -> str:
    """Valore in chiaro da un valore salvato (cifrato o no). Stringa vuota se non decifrabile
    (es. config.json copiato da un altro PC): basta reinserire il token nelle Impostazioni."""
    if not value or not value.startswith(PREFIX):
        return value
    if not available():
        return ""
    try:
        return _dpapi(base64.b64decode(value[len(PREFIX):]), False).decode("utf-8")
    except (OSError, ValueError):
        return ""
