#!/usr/bin/env python3
"""Guarda el marcador jugada a jugada de cada partido de Euroliga/EuroCup (tabla basket_timeline).

Fuente: live.euroleague.net/api/PlaybyPlay. Por partido se guarda una lista compacta de canastas
[minuto, puntos local, puntos visitante]. Incremental y despacio: la fuente corta el acceso (error 429)
si se le piden muchas cosas seguidas.

    python build_timeline.py [db] [--max 40] [--pause 2.5]
"""
import argparse
import json
import sqlite3
import time

import requests

from sportsdb.common import DEFAULT_DB, UA

API = "https://live.euroleague.net/api/PlaybyPlay"
PERIODS = ["FirstQuarter", "SecondQuarter", "ThirdQuarter", "ForthQuarter", "ExtraTime"]
SCHEMA = """CREATE TABLE IF NOT EXISTS basket_timeline (
  match_id TEXT PRIMARY KEY REFERENCES matches(match_id) ON DELETE CASCADE,
  events TEXT NOT NULL)      -- JSON: [[minuto, puntos local, puntos visitante], ...]; '[]' si la fuente no tiene jugadas"""


def minute(p):
    """Minuto de juego transcurrido al producirse la jugada."""
    m, s = (str(p.get("MARKERTIME") or "0:0").split(":") + ["0"])[:2]
    mi = int(p.get("MINUTE") or 1)
    end = -(-mi // 10) * 10 if mi <= 40 else 40 + -(-(mi - 40) // 5) * 5
    return round(end - (int(m or 0) + int(s or 0) / 60), 2)


def fetch(ses, year, code, comp):
    """Devuelve la lista de canastas, [] si no hay jugadas, o None si la fuente está cortando."""
    for wait in (0, 60, 180):
        time.sleep(wait)
        r = ses.get(API, params={"gamecode": code, "seasoncode": f"{comp}{year}"}, timeout=40)
        if r.status_code == 429:
            continue
        if r.status_code != 200 or not r.text.strip():
            return []
        d = r.json()
        ev = [[minute(p), int(p["POINTS_A"]), int(p["POINTS_B"])] for k in PERIODS for p in (d.get(k) or [])
              if p.get("POINTS_A") is not None and p.get("POINTS_B") is not None]
        return ev
    return None


def fetch_acb(source_id):
    """Liga Endesa: la página «resumen» del partido trae la evolución del marcador con su segundo exacto."""
    from sportsdb import acb
    from sportsdb.common import http_get
    try:
        payload = acb.rsc_payload(http_get(f"https://live.acb.com/es/partidos/x-{source_id}/resumen", tries=2).text)
    except Exception:
        return None
    lt = (acb.find_props(payload, "initialLeadTracker") or {}).get("initialLeadTracker") or {}
    return [[round(x["time"] / 60, 2), int(x["homeScore"]), int(x["awayScore"])] for x in lt.get("leadTrackerInfo") or []]


def main_acb(con, limit, pause, log):
    todo = con.execute("""SELECT match_id, source_id FROM matches WHERE league = 'Liga Endesa' AND status = 'played'
          AND match_id NOT IN (SELECT match_id FROM basket_timeline) ORDER BY date DESC LIMIT ?""", (limit,)).fetchall()
    ok = empty = fail = 0
    for i, (mid, source_id) in enumerate(todo):
        ev = fetch_acb(source_id)
        if ev is None:
            fail += 1
            if fail >= 5 and fail > ok:
                log("[Jugadas] Liga Endesa: la fuente no responde; se deja para la próxima vez")
                break
            continue
        con.execute("INSERT OR REPLACE INTO basket_timeline VALUES (?, ?)", (mid, json.dumps(ev, separators=(",", ":"))))
        ok += bool(ev)
        empty += not ev
        if (i + 1) % 50 == 0:
            con.commit()
            log(f"[Jugadas] Liga Endesa {i + 1}/{len(todo)}")
        time.sleep(pause)
    con.commit()
    left = con.execute("""SELECT count(*) FROM matches WHERE league = 'Liga Endesa' AND status = 'played'
                          AND match_id NOT IN (SELECT match_id FROM basket_timeline)""").fetchone()[0]
    log(f"[Jugadas] Liga Endesa: {ok} partidos nuevos, {empty} sin evolución del marcador, quedan {left}")


def main(db=DEFAULT_DB, limit=40, pause=2.5, log=print, only=None):
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute(SCHEMA)
    if only != "euro":
        main_acb(con, limit, min(pause, 1.0), log)
    if only == "acb":
        con.close()
        return
    todo = con.execute("""SELECT match_id, season_start, source_id, league FROM matches
        WHERE league IN ('Euroliga','EuroCup') AND status='played'
          AND match_id NOT IN (SELECT match_id FROM basket_timeline)
        ORDER BY date DESC LIMIT ?""", (limit,)).fetchall()
    ses = requests.Session()
    ses.headers["User-Agent"] = UA
    ok = empty = 0
    for i, (mid, year, code, league) in enumerate(todo):
        try:
            ev = fetch(ses, year, code, "E" if league == "Euroliga" else "U")
        except Exception:
            continue
        if ev is None:
            log(f"[Jugadas] la fuente corta el acceso; se deja para la próxima vez ({ok} guardados)")
            break
        con.execute("INSERT OR REPLACE INTO basket_timeline VALUES (?, ?)", (mid, json.dumps(ev, separators=(",", ":"))))
        ok += bool(ev)
        empty += not ev
        if (i + 1) % 25 == 0:
            con.commit()
            log(f"[Jugadas] {i + 1}/{len(todo)}")
        time.sleep(pause)
    con.commit()
    left = con.execute("""SELECT count(*) FROM matches WHERE league IN ('Euroliga','EuroCup') AND status='played'
                          AND match_id NOT IN (SELECT match_id FROM basket_timeline)""").fetchone()[0]
    con.close()
    log(f"[Jugadas] {ok} partidos nuevos, {empty} sin jugadas, quedan {left} por descargar")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db", nargs="?", default=str(DEFAULT_DB))
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--pause", type=float, default=2.5)
    ap.add_argument("--solo", choices=["acb", "euro"], default=None, help="descargar solo una de las dos fuentes")
    a = ap.parse_args()
    main(a.db, a.max, a.pause, only=a.solo)
