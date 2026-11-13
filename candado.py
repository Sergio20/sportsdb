"""Candado de la web publicada: todas las páginas van cifradas con la contraseña del panel de control.

Es el mismo sistema que Portal deportivo (repositorio privado Sergio20/portal-deportivo, portal/cifrado.py):
AES-256-GCM con una clave derivada de la contraseña (PBKDF2, sal en clave.json). La clave nunca va en el código:
en GitHub es el secreto PORTAL_KEY de este repositorio y en el PC de Sergio el archivo ~/.portal_deportivo/clave.key.
Como la web de SportsDB y el panel están en la misma dirección (sergio20.github.io), el navegador que ya entró en el
panel entra aquí sin volver a pedir la contraseña.

Sin clave NUNCA se publica nada en abierto: cada página se sustituye por un aviso de que falta la clave.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOADER = ROOT / "panel" / "candado.html"
PARAMS = ROOT / "clave.json"  # sal e iteraciones: no son secretas (las mismas que el panel)
KEY_FILE = Path.home() / ".portal_deportivo" / "clave.key"

SIN_CLAVE = ('<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             '<meta name="robots" content="noindex,nofollow"><title>SportsDB</title></head>'
             '<body style="font:16px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px;max-width:520px">'
             '<h1 style="font-size:20px">Página privada</h1><p>Falta la clave del candado en la publicación, así que no se '
             'enseña nada. Pídele a Claude que revise el secreto PORTAL_KEY del repositorio sportsdb.</p></body></html>')


def clave() -> bytes | None:
    text = os.environ.get("PORTAL_KEY") or (KEY_FILE.read_text().strip() if KEY_FILE.exists() else "")
    return bytes.fromhex(text.strip()) if text.strip() else None


def cifrar(data: bytes, key: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = os.urandom(12)
    return base64.b64encode(iv + AESGCM(key).encrypt(iv, data, None)).decode("ascii")


def cerrar(out: Path) -> bool:
    """Cifra todas las páginas de out/ (y los datos del panel). Devuelve False si no había clave."""
    key = clave()
    pages = sorted(out.glob("*.html"))
    data = out / "data.json"
    if not key:
        for page in pages:
            page.write_text(SIN_CLAVE, encoding="utf-8")
        data.unlink(missing_ok=True)
        print("::warning::Sin la clave PORTAL_KEY: se publica la web cerrada (solo el aviso de que falta la clave).")
        return False
    params = json.loads(PARAMS.read_text(encoding="utf-8"))
    loader = LOADER.read_text(encoding="utf-8").replace("__SALT__", params["salt"]).replace(
        "__ITERATIONS__", str(params["iteraciones"]))
    for page in pages:
        page.write_text(loader.replace("__DATA__", cifrar(page.read_bytes(), key)), encoding="utf-8")
    if data.exists():
        (out / "data.enc").write_text(cifrar(data.read_bytes(), key), encoding="ascii")
        data.unlink()
    return True
