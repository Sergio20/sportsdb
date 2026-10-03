#!/usr/bin/env python3
"""Asistente para activar los avisos por Telegram. Se ejecuta UNA vez, en tu propio terminal.

Te pide el token del bot (no se muestra al pegarlo ni se guarda en ningún fichero), averigua tu
número de chat a partir del último mensaje que le hayas escrito al bot, guarda las dos claves como
secretos del repositorio de GitHub y te manda un mensaje de prueba.

    python scripts/configurar_telegram.py
"""
import getpass
import subprocess
import sys

import requests

REPO = "Sergio20/sportsdb"


def main():
    print("Antes de seguir: abre tu bot en Telegram y escríbele cualquier cosa (por ejemplo «hola»).\n")
    token = getpass.getpass("Pega aquí el token de BotFather y pulsa Enter (no se verá al pegarlo): ").strip()
    if ":" not in token:
        print("Eso no parece un token de Telegram (tiene esta forma: 123456789:AAH...). Vuelve a intentarlo.")
        return 1
    api = f"https://api.telegram.org/bot{token}/"
    me = requests.get(api + "getMe", timeout=30).json()
    if not me.get("ok"):
        print("Telegram no reconoce ese token. Cópialo otra vez desde BotFather, entero.")
        return 1
    print(f"Bot encontrado: @{me['result']['username']}")
    ups = requests.get(api + "getUpdates", timeout=30).json().get("result", [])
    chats = [u["message"]["chat"] for u in ups if "message" in u]
    if not chats:
        print(f"El bot no ha recibido ningún mensaje tuyo. Abre @{me['result']['username']} en Telegram, "
              "escríbele «hola» y vuelve a ejecutar este asistente.")
        return 1
    chat = chats[-1]
    print(f"Chat encontrado: {chat.get('first_name', '')} (el último que ha escrito al bot)")
    for name, value in (("TELEGRAM_TOKEN", token), ("TELEGRAM_CHAT_ID", str(chat["id"]))):
        r = subprocess.run(["gh", "secret", "set", name, "--repo", REPO], input=value, text=True, capture_output=True)
        if r.returncode != 0:
            print(f"No se ha podido guardar {name} en GitHub: {r.stderr.strip()}")
            return 1
        print(f"Guardado en GitHub: {name}")
    ok = requests.post(api + "sendMessage", timeout=30, json={"chat_id": chat["id"], "text":
                       "✅ SportsDB: avisos por Telegram activados. Aquí llegarán los avisos de los partidos en directo."}).json().get("ok")
    print("Mensaje de prueba enviado: míralo en Telegram." if ok else "Las claves están guardadas, pero el mensaje de prueba no ha salido.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
