#!/usr/bin/env python3
"""Vigila los partidos en directo de Euroliga y EuroCup y envía los avisos por Telegram.

Lo lanza cada tarde .github/workflows/vigilante.yml. Mientras haya partidos en juego lee las jugadas
oficiales cada 45 segundos, aplica las reglas de avisos.py y manda un mensaje por cada aviso nuevo,
con las líneas de seguridad (80, 90 y 95 %). Al acabar cada partido manda cómo quedó cada aviso.
Todo queda anotado en un fichero (rama `avisos` de GitHub) para medir después cuánto aciertan.

    python vigilante.py [--db data/deportes.db] [--log avisos.json] [--horas 4.5]
    python vigilante.py --prueba                    # solo manda un mensaje de prueba
    python vigilante.py --repetir E2026 26 18       # ensayo: el partido 26, parado en el minuto 18 (no anota)

Telegram: variables de entorno TELEGRAM_TOKEN y TELEGRAM_CHAT_ID. Sin ellas, los mensajes solo se escriben en pantalla.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

import avisos
import export_live
from build_timeline import PERIODS, minute
from sportsdb.common import DEFAULT_DB, UA, current_season_start

LIVE = "https://live.euroleague.net/api/"
GAMES = "https://api-live.euroleague.net/v2/competitions/{c}/seasons/{c}{y}/games?limit=1000"
COMP = {"E": "Euroliga", "U": "EuroCup"}
WEB = "https://sergio20.github.io/sportsdb/en-vivo.html"
EVERY, GAP = 45, 1.2          # segundos entre vueltas y entre peticiones (la fuente corta si se abusa)
ses = requests.Session()
ses.headers["User-Agent"] = UA
now = lambda: dt.datetime.now(dt.timezone.utc)  # noqa: E731


def get(url, **params):
    """JSON de la fuente, o None si falla o está cortando (se reintenta en la siguiente vuelta)."""
    time.sleep(GAP)
    try:
        r = ses.get(url, params=params or None, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code == 429:
        print("  la fuente corta el acceso: pausa de 60 s", flush=True)
        time.sleep(60)
        return None
    return r.json() if r.status_code == 200 and r.text.strip() else None


def send(text):
    """Manda un mensaje por Telegram. El token nunca se escribe en pantalla."""
    print("\n" + text + "\n", flush=True)
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("  (Telegram sin configurar: el mensaje no se ha enviado)", flush=True)
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
        if r.status_code != 200:
            print(f"  Telegram ha rechazado el mensaje (código {r.status_code})", flush=True)
        return r.status_code == 200
    except requests.RequestException:
        print("  no se ha podido contactar con Telegram", flush=True)
        return False


def elapsed(h):
    q = int(h.get("Quarter") or 0)
    if not 1 <= q <= 4:
        return None
    m, s = (str(h.get("RemainingPartialTime") or "0:0").strip().split(":") + ["0"])[:2]
    return (q - 1) * 10 + 10 - (int(m or 0) + int(s or 0) / 60)


def read_plays(comp, year, code):
    d = get(LIVE + "PlaybyPlay", gamecode=code, seasoncode=f"{comp}{year}")
    if not d:
        return None
    return [dict(t=minute(p), team=(p.get("CODETEAM") or "").strip(), type=(p.get("PLAYTYPE") or "").strip())
            for k in PERIODS[:4] for p in (d.get(k) or [])]


def message(al, g, el, score):
    q = min(4, int(el // 10) + 1)
    return (f"🔔 {avisos.NAMES[al['type']]} ({al['level']})\n{COMP[g['comp']]} · {g['home']} {score} {g['away']} · minuto {el:.0f} ({q}.º cuarto)\n\n"
            + avisos.describe(al, g["home"], g["away"])
            + f"\n\nSolo compensa si la cuota que te dan supera la mínima. Ninguna línea acierta siempre.\n{WEB}")


def result_message(g, entries, hs, as_):
    lines = [f"🏁 Final: {g['home']} {hs}-{as_} {g['away']}"]
    for e in entries:
        res = avisos.settle(e["alert"], hs, as_)
        what = "total" if e["alert"]["type"] == "total" else (g["home"] if e["alert"]["side"] > 0 else g["away"])
        lines.append(f"{avisos.NAMES[e['alert']['type']]} (min {e['el']:.0f}, {what}): " + " · ".join(f"{p} % {'✅' if ok else '❌'}" for p, ok in res.items()))
    return "\n".join(lines)


def todays_games(year):
    out = []
    for comp in COMP:
        d = get(GAMES.format(c=comp, y=year))
        for g in (d or {}).get("data", []):
            if not g.get("utcDate") or g["local"]["club"].get("isVirtual") or g["road"]["club"].get("isVirtual"):
                continue
            start = dt.datetime.fromisoformat(g["utcDate"].replace("Z", "+00:00"))
            if abs((start - now()).total_seconds()) < 8 * 3600:
                out.append(dict(comp=comp, year=year, code=g["gameCode"], start=start, home=g["local"]["club"]["name"], away=g["road"]["club"]["name"],
                                hc=g["local"]["club"]["code"], ac=g["road"]["club"]["code"], done=bool(g.get("played")), sent={}))
    return out


def watch(base, log_path, hours):
    year = current_season_start()
    deadline = now() + dt.timedelta(hours=hours)
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    games = todays_games(year)
    for g in games:   # lo ya avisado en una tanda anterior del mismo día no se repite
        g["sent"] = {e["key"]: e for e in log if (e["comp"], e["year"], e["code"]) == (g["comp"], g["year"], g["code"])}
    print(f"{len(games)} partidos en las próximas horas; vigilando hasta las {deadline:%H:%M} UTC como muy tarde", flush=True)
    while now() < deadline:
        pending = [g for g in games if not g["done"]]
        if not pending:
            break
        soon = [g for g in pending if g["start"] <= now() + dt.timedelta(minutes=3)]
        if not soon:
            time.sleep(min(300, max(30, (min(g["start"] for g in pending) - now()).total_seconds() - 120)))
            continue
        t0 = time.time()
        for g in soon:
            h = get(LIVE + "Header", gamecode=g["code"], seasoncode=f"{g['comp']}{year}")
            if not h:
                continue
            hs, as_ = int(h.get("ScoreA") or 0), int(h.get("ScoreB") or 0)
            if not h.get("Live"):
                if hs + as_ > 0 and now() > g["start"] + dt.timedelta(minutes=75):   # terminado
                    g["done"] = True
                    mine = [e for e in g["sent"].values()]
                    if mine and not any(e.get("closed") for e in mine):
                        send(result_message(g, mine, hs, as_))
                        for e in mine:
                            e["closed"] = True
                        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
                continue
            el, bA, bB = elapsed(h), base.get(g["hc"]), base.get(g["ac"])
            if el is None or not bA or not bB:
                continue
            plays = read_plays(g["comp"], year, g["code"])
            if not plays:
                continue
            A, B = avisos.totals(plays, g["hc"])
            for al in avisos.evaluate(el, A, B, bA, bB):
                k, old = avisos.key(al), g["sent"].get(avisos.key(al))
                if old and not (old["alert"]["level"] == "moderado" and al["level"] == "fuerte" and not old.get("upgraded")):
                    continue
                score = f"{A['pts']}-{B['pts']}"
                send(message(al, g, el, score))
                if old:                 # pasa de moderado a fuerte: se avisa otra vez, pero cuenta el primero
                    old["upgraded"] = True
                    continue
                entry = dict(ts=now().isoformat(timespec="seconds"), comp=g["comp"], year=year, code=g["code"], home=g["home"], away=g["away"],
                             key=k, el=round(el, 1), score=score, alert=al)
                g["sent"][k] = entry
                log.append(entry)
                log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(max(5, EVERY - (time.time() - t0)))
    print(f"Fin de la vigilancia: {sum(len(g['sent']) for g in games)} avisos en total hoy", flush=True)


def replay(base, season, code, el):
    """Ensayo con un partido ya jugado, parado en un minuto: manda los avisos que habría en ese momento."""
    comp, year = season[0], int(season[1:])
    h = get(LIVE + "Header", gamecode=code, seasoncode=season)
    plays = read_plays(comp, year, code)
    if not h or not plays:
        print("No se ha podido leer el partido.")
        return 1
    hc, ac = h["CodeTeamA"].strip(), h["CodeTeamB"].strip()
    g = dict(comp=comp, home=h["TeamA"].strip().title(), away=h["TeamB"].strip().title())
    A, B = avisos.totals(plays, hc, el)
    als = avisos.evaluate(el, A, B, base[hc], base[ac])
    if not als:
        print(f"En el minuto {el} no había ningún aviso ({A['pts']}-{B['pts']}).")
    for al in als:
        send("ENSAYO con un partido ya jugado\n" + message(al, g, el, f"{A['pts']}-{B['pts']}"))
    if als:
        send("ENSAYO\n" + result_message(g, [dict(alert=al, el=el) for al in als], int(h["ScoreA"]), int(h["ScoreB"])))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--log", default="avisos.json")
    ap.add_argument("--horas", type=float, default=4.5)
    ap.add_argument("--prueba", action="store_true")
    ap.add_argument("--repetir", nargs=3, metavar=("TEMPORADA", "PARTIDO", "MINUTO"))
    a = ap.parse_args()
    if a.prueba:
        ok = send("✅ SportsDB: los avisos por Telegram funcionan. Aquí llegarán los desfases, ritmos insostenibles, triples y totales de los partidos en directo.")
        return 0 if ok else 1
    con = sqlite3.connect(a.db)
    base = export_live.baseline(con)
    con.close()
    if a.repetir:
        return replay(base, a.repetir[0], int(a.repetir[1]), int(a.repetir[2]))
    watch(base, Path(a.log), a.horas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
