#!/usr/bin/env python3
"""Chuleta de fútbol para el directo: qué pasa en la segunda parte según el marcador al descanso y lo favorito que era
cada equipo antes del partido (tabla `futbol_hist`, sportsdb/futbol_hist.py; LaLiga, Premier, Bundesliga, Serie A y
Ligue 1 desde 1995-96).

Para cada situación: cuántos casos, cómo acabó (gana / empata / pierde) y la cuota mínima a partir de la cual cada
apuesta habitual tiene valor (1X, X2, gana, marca en la 2.ª parte, hándicap asiático +1,5 y +2,5). Solo se sabe el
marcador al descanso, no el minuto de los goles. Lo usa export_informes.py para futbol.html.

    python futbol.py [--db data/deportes.db]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict

from sportsdb.common import DEFAULT_DB

# Nivel del equipo por su probabilidad de ganar antes del partido (cuotas sin el margen de la casa)
TIERS = [("muy favorito (70 % o más)", 0.70, 1.01), ("favorito (55-70 %)", 0.55, 0.70), ("ligero favorito (45-55 %)", 0.45, 0.55),
         ("algo peor (30-45 %)", 0.30, 0.45), ("claro inferior (menos del 30 %)", 0.0, 0.30)]
TEAMS = {"Barcelona": "Barcelona", "Real Madrid": "Real Madrid", "Ath Madrid": "Atlético de Madrid", "Betis": "Real Betis"}
STATES = [(-9, -2, "pierde por 2 o más"), (-1, -1, "pierde por 1"), (0, 0, "empata"), (1, 1, "gana por 1"), (2, 9, "gana por 2 o más")]
MIN_N = 15          # con menos casos no se da cuota (demasiado incierto)


def sides(con):
    """Cada partido visto desde los dos equipos: liga, temporada, equipo, rival, prob. de ganar, descanso y final."""
    for lg, season, home, away, fh, fa, hh, ha, ph, pa in con.execute(
            "SELECT league, season_start, home, away, fthg, ftag, hthg, htag, p_h, p_a FROM futbol_hist "
            "WHERE hthg IS NOT NULL AND htag IS NOT NULL"):
        yield dict(league=lg, season=season, team=home, opp=away, p=ph, ht=hh - ha, ft=fh - fa, g2=fh - hh, home=True)
        yield dict(league=lg, season=season, team=away, opp=home, p=pa, ht=ha - hh, ft=fa - fh, g2=fa - ha, home=False)


def wilson(k, n, z=1.645):
    """Franja del 90 % para un porcentaje con n casos."""
    if not n:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / den, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(100 * (c - h)), round(100 * (c + h))


def summary(xs):
    n = len(xs)
    if not n:
        return dict(n=0)
    k = dict(win=sum(x["ft"] > 0 for x in xs), draw=sum(x["ft"] == 0 for x in xs), lose=sum(x["ft"] < 0 for x in xs),
             score2=sum(x["g2"] >= 1 for x in xs), ah15=sum(x["ft"] >= -1 for x in xs), ah25=sum(x["ft"] >= -2 for x in xs))
    k["dc1x"], k["dcx2"] = k["win"] + k["draw"], k["draw"] + k["lose"]
    out = dict(n=n)
    for key, v in k.items():
        lo, hi = wilson(v, n)
        out[key] = dict(pct=round(100 * v / n, 1), lo=lo, hi=hi,
                        # cuota mínima con valor, usando el extremo prudente de la franja (no el valor central)
                        odds=round(100 / lo, 2) if n >= MIN_N and lo and lo > 0 else None)
    return out


def build(con):
    rows = list(sides(con))
    if not rows:
        return None
    groups = {}
    for name, lo, hi in TIERS:
        groups[name] = lambda x, lo=lo, hi=hi: x["p"] is not None and lo <= x["p"] < hi
    for code, name in TEAMS.items():
        groups[name] = lambda x, code=code: x["team"] == code
    leagues = {"Todas las ligas": lambda x: True, "Solo LaLiga": lambda x: x["league"] == "LaLiga"}
    out = dict(groups=list(groups), leagues=list(leagues), states=[s[2] for s in STATES], table={})
    for lname, lsel in leagues.items():
        for gname, gsel in groups.items():
            xs = [x for x in rows if lsel(x) and gsel(x)]
            def nolose(era, lo, hi):      # % de «no pierde» en una mitad del histórico, para ver si se repite
                ys = [x for x in xs if era(x["season"]) and lo <= x["ht"] <= hi]
                return round(100 * sum(x["ft"] >= 0 for x in ys) / len(ys), 1) if len(ys) >= MIN_N else None
            out["table"][f"{lname}|{gname}"] = dict(
                all=summary(xs), by=[summary([x for x in xs if lo <= x["ht"] <= hi]) for lo, hi, _ in STATES],
                old=[nolose(lambda s: s < 2012, lo, hi) for lo, hi, _ in STATES],
                new=[nolose(lambda s: s >= 2012, lo, hi) for lo, hi, _ in STATES])
    by_league = defaultdict(set)
    for x in rows:
        by_league[x["league"]].add(x["season"])
    out["coverage"] = {lg: [min(s), max(s), len(s)] for lg, s in sorted(by_league.items())}
    out["matches"] = len(rows) // 2
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args()
    d = build(sqlite3.connect(a.db))
    if not d:
        print("Sin datos en futbol_hist: ejecuta update_db.py --leagues futbol")
    else:
        print(json.dumps({k: d[k] for k in ("coverage", "matches")}, ensure_ascii=False))
        t = d["table"]["Solo LaLiga|muy favorito (70 % o más)"]
        for st, s in zip(d["states"], t["by"]):
            print(st, s.get("n"), {k: s[k]["pct"] for k in ("win", "draw", "lose", "dc1x")} if s.get("n") else "")
