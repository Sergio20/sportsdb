#!/usr/bin/env python3
"""Prueba de la fuente de fútbol en directo (API-Football, v3.football.api-sports.io) con la clave FUTBOL_API_KEY.

Comprueba: que la clave vale, el plan y cuántas consultas quedan hoy, si el plan da la temporada actual de LaLiga
(liga 140), los próximos partidos y si hay alguno en juego ahora. No escribe la clave en pantalla.
"""
import datetime as dt
import json
import os
import sys

import requests

KEY = os.environ.get("FUTBOL_API_KEY")
BASE = "https://v3.football.api-sports.io/"


def get(path, **params):
    r = requests.get(BASE + path, params=params, headers={"x-apisports-key": KEY}, timeout=30)
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    print(f"\n== {path} {params} -> HTTP {r.status_code} · consultas restantes hoy: {r.headers.get('x-ratelimit-requests-remaining')}")
    if d.get("errors"):
        print("   ERRORES:", json.dumps(d["errors"], ensure_ascii=False))
    return d


def main():
    if not KEY:
        print("Falta el secreto FUTBOL_API_KEY")
        return 1
    st = get("status").get("response") or {}
    if st:
        print("   cuenta:", (st.get("subscription") or {}), "· consultas:", st.get("requests"))
    year = dt.date.today().year if dt.date.today().month >= 7 else dt.date.today().year - 1
    lg = get("leagues", id=140, season=year).get("response") or []
    for x in lg:
        for s in x.get("seasons", []):
            print("   temporada", s.get("year"), "cobertura:", json.dumps(s.get("coverage", {}).get("fixtures", {})))
    nx = get("fixtures", league=140, season=year, next=10).get("response") or []
    for f in nx:
        print("   próximo:", f["fixture"]["date"], f["teams"]["home"]["name"], "-", f["teams"]["away"]["name"])
    live = get("fixtures", live="all").get("response") or []
    print(f"   partidos en juego ahora (todas las ligas): {len(live)}")
    for f in live[:5]:
        print("   en juego:", f["league"]["name"], f["teams"]["home"]["name"], f["goals"]["home"], "-", f["goals"]["away"],
              f["teams"]["away"]["name"], "minuto", f["fixture"]["status"].get("elapsed"))
    last = get("fixtures", league=140, season=year, last=3).get("response") or []
    for f in last:
        print("   último:", f["fixture"]["date"], f["teams"]["home"]["name"], f["goals"]["home"], "-", f["goals"]["away"],
              f["teams"]["away"]["name"], "· descanso", f["score"]["halftime"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
