#!/usr/bin/env python3
"""Cronología de un partido de Euroliga/EuroCup minuto a minuto (fuente oficial en directo).

Muestra, cada 2 minutos, el marcador, la diferencia y el ritmo al que iba cada equipo
(puntos proyectados a 40 minutos), y marca el momento de máxima ventaja.

Uso:
    python scripts/cronologia.py E2026 25 26         # temporada y códigos de partido
    python scripts/cronologia.py E2026 --jornada 2   # todos los de una jornada
"""
from __future__ import annotations

import argparse
import sys
import time

import requests

API = "https://live.euroleague.net/api/"
GAMES = "https://api-live.euroleague.net/v2/competitions/{c}/seasons/{s}/games?limit=1000"
PERIODS = ["FirstQuarter", "SecondQuarter", "ThirdQuarter", "ForthQuarter", "ExtraTime"]


def get(url, tries=4):
    for i in range(tries):
        r = requests.get(url, timeout=30)
        if r.status_code == 429:
            time.sleep(30 * (i + 1))
            continue
        r.raise_for_status()
        return r.json() if r.text.strip() else None
    raise RuntimeError("la fuente sigue limitando peticiones")


def minute(p):
    """Minuto de juego transcurrido al producirse la jugada."""
    m, s = (str(p.get("MARKERTIME") or "0:0").split(":") + ["0"])[:2]
    mi = int(p.get("MINUTE") or 1)
    end = -(-mi // 10) * 10 if mi <= 40 else 40 + -(-(mi - 40) // 5) * 5
    return end - (int(m or 0) + int(s or 0) / 60)


def timeline(season, code):
    h = get(f"{API}Header?gamecode={code}&seasoncode={season}")
    pbp = get(f"{API}PlaybyPlay?gamecode={code}&seasoncode={season}")
    pts = [(minute(p), int(p["POINTS_A"]), int(p["POINTS_B"])) for k in PERIODS for p in (pbp.get(k) or [])
           if p.get("POINTS_A") is not None and p.get("POINTS_B") is not None]
    return h, pts


def show(season, code, step=2):
    h, pts = timeline(season, code)
    a, b = h["TeamA"].strip(), h["TeamB"].strip()
    print(f"\n=== {a} {h['ScoreA']}-{h['ScoreB']} {b} ===")
    if not pts:
        print("sin jugadas")
        return
    mx = max(pts, key=lambda x: abs(x[1] - x[2]))
    print(f"Máxima ventaja: {abs(mx[1] - mx[2])} para {a if mx[1] > mx[2] else b} en el minuto {mx[0]:.0f} ({mx[1]}-{mx[2]})")
    print(f"{'Min':>4} {'Marcador':>9} {'Dif':>5}  Ritmo a 40 min ({a[:12]} / {b[:12]})")
    t, i, last = step, 0, (0, 0, 0)
    end = pts[-1][0]
    while t <= end + 1e-9:
        while i < len(pts) and pts[i][0] <= t:
            last = pts[i]
            i += 1
        sa, sb = last[1], last[2]
        print(f"{t:>4.0f} {sa:>4}-{sb:<4} {sa - sb:>+5}  {sa * 40 / t:>5.0f} / {sb * 40 / t:<5.0f}")
        t += step


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("season")
    ap.add_argument("codes", nargs="*", type=int)
    ap.add_argument("--jornada", type=int)
    ap.add_argument("--paso", type=int, default=2)
    a = ap.parse_args()
    codes = a.codes
    if a.jornada:
        d = get(GAMES.format(c=a.season[0], s=a.season))
        codes = [g["gameCode"] for g in d["data"] if g.get("round") == a.jornada and g.get("played")]
    for c in codes:
        show(a.season, c, a.paso)
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
