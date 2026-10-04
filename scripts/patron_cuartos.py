#!/usr/bin/env python3
"""¿Se «compensan» los cuartos? Estudio con el histórico de una idea de Sergio:

  1. Si un equipo pierde dos cuartos seguidos, ¿gana el siguiente (aunque sea peor equipo)?
  2. Si un cuarto acaba empatado y el siguiente lo gana A, ¿el tercero lo pierde A?
  3. Si un equipo pierde los cuartos 3.º y 4.º y hay prórroga, ¿quién gana la prórroga?

Cada caso se compara con lo esperable para esos dos equipos (por su nivel), no con un 50 %: el peor equipo suele
perder más cuartos, así que «pierde dos y gana el tercero» puede pasar menos de la mitad de las veces y aun así ser
más (o menos) de lo normal para él. Nivel = diferencia media de puntos en sus 15 partidos anteriores de la misma
liga (sin mirar el futuro). Se separa por temporadas (2021-24 frente a 2024-27) para ver si se repite.

    python scripts/patron_cuartos.py [--db data/deportes.db]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sportsdb.common import DEFAULT_DB  # noqa: E402

LEAGUES = None      # todas las de baloncesto con cuartos
WINDOW = 15


def load(con):
    rows = con.execute("""SELECT m.match_id, m.league, m.season, m.date, m.home_team, m.away_team, p.period, p.home, p.away
                          FROM matches m JOIN periods p USING(match_id)
                          WHERE m.sport = 'baloncesto' AND m.home_score IS NOT NULL
                          ORDER BY m.date, m.match_id, p.period""").fetchall()
    games = {}
    for mid, lg, season, date, ht, at, per, h, a in rows:
        g = games.setdefault(mid, dict(league=lg, season=season, date=date, home=ht, away=at, q={}))
        g["q"][per] = (h, a)
    out = []
    for g in games.values():
        if all(k in g["q"] for k in (1, 2, 3, 4)):
            g["qs"] = [g["q"][k] for k in sorted(g["q"])]
            out.append(g)
    return sorted(out, key=lambda g: g["date"] or "")


def with_levels(games):
    """Nivel previo de cada equipo (sin mirar el futuro): diferencia media en sus últimos WINDOW partidos de la liga."""
    hist = defaultdict(lambda: deque(maxlen=WINDOW))
    for g in games:
        kh, ka = (g["league"], g["home"]), (g["league"], g["away"])
        g["lh"] = sum(hist[kh]) / len(hist[kh]) if len(hist[kh]) >= 5 else None
        g["la"] = sum(hist[ka]) / len(hist[ka]) if len(hist[ka]) >= 5 else None
        m = sum(h - a for h, a in g["qs"])
        hist[kh].append(m)
        hist[ka].append(-m)
    return [g for g in games if g["lh"] is not None and g["la"] is not None]


def sign(x):
    return (x > 0) - (x < 0)


def side_rows(g):
    """Cada partido visto desde los dos equipos: (diferencia de nivel a su favor, [resultado de cada periodo +1/0/-1])."""
    gap = g["lh"] - g["la"]
    res = [sign(h - a) for h, a in g["qs"]]
    return [(gap + 2.5, res), (-gap - 2.5, [-r for r in res])]      # 2,5 = ventaja aproximada de jugar en casa


BINS = [(-99, -6, "mucho peor (−6 o menos)"), (-6, -2, "algo peor (−6 a −2)"), (-2, 2, "parejo (−2 a +2)"),
        (2, 6, "algo mejor (+2 a +6)"), (6, 99, "mucho mejor (+6 o más)")]


def bin_of(d):
    return next(i for i, (lo, hi, _) in enumerate(BINS) if lo <= d < hi)


def study(games, label):
    # Base: probabilidad de ganar un cuarto (2.º a 4.º) para cada nivel, sin condición
    base = defaultdict(lambda: [0, 0])
    c1 = defaultdict(lambda: [0, 0, 0])       # perdió k y k+1 -> gana k+2 ?   [casos, gana, empata]
    c1_any = [0, 0, 0, 0.0]                   # [casos, gana, empata, esperado]
    c2 = [0, 0, 0, 0.0]                       # empate en k, gana k+1 -> pierde k+2 ? [casos, pierde, empata, esperado de perder]
    ot = [0, 0, 0.0]                          # perdió 3.º y 4.º, hay prórroga -> gana la prórroga ? [casos, gana, esperado]
    for g in games:
        for d, res in side_rows(g):
            b = bin_of(d)
            for k in (1, 2, 3):
                base[b][0] += 1
                base[b][1] += res[k] > 0
    pw = {b: base[b][1] / base[b][0] for b in base}
    lose = defaultdict(lambda: [0, 0])
    for g in games:
        for d, res in side_rows(g):
            for k in (1, 2, 3):
                lose[bin_of(d)][0] += 1
                lose[bin_of(d)][1] += res[k] < 0
    pl = {b: lose[b][1] / lose[b][0] for b in lose}
    for g in games:
        for d, res in side_rows(g):
            b = bin_of(d)
            for k in (0, 1):                  # cuartos (1,2)->3 y (2,3)->4
                if res[k] < 0 and res[k + 1] < 0:
                    x = c1[(b, k)]
                    x[0] += 1; x[1] += res[k + 2] > 0; x[2] += res[k + 2] == 0
                    c1_any[0] += 1; c1_any[1] += res[k + 2] > 0; c1_any[2] += res[k + 2] == 0; c1_any[3] += pw[b]
            for k in (0, 1):                  # empate en k, gana k+1 -> ¿pierde k+2?
                if res[k] == 0 and res[k + 1] > 0:
                    c2[0] += 1; c2[1] += res[k + 2] < 0; c2[2] += res[k + 2] == 0; c2[3] += pl[b]
            if len(res) > 4 and res[2] < 0 and res[3] < 0:
                ot[0] += 1; ot[1] += res[4] > 0; ot[2] += 0.5
    print(f"\n=== {label}: {len(games)} partidos ===")
    print("Probabilidad normal de ganar un cuarto según el nivel:", ", ".join(f"{BINS[b][2]} {100 * pw[b]:.0f} %" for b in sorted(pw)))
    n, w, t, e = c1_any
    print(f"\n1) Pierde dos cuartos seguidos -> gana el siguiente: {w}/{n} = {100 * w / n:.1f} % (empata {100 * t / n:.1f} %)."
          f" Lo normal para esos equipos: {100 * e / n:.1f} %. Diferencia: {100 * (w - e) / n:+.1f} puntos.")
    for b in range(len(BINS)):
        for k in (0, 1):
            x = c1.get((b, k))
            if x and x[0] >= 30:
                print(f"   {BINS[b][2]:<26} cuartos {k + 1}-{k + 2} perdidos -> gana el {k + 3}.º: {x[1]}/{x[0]} = {100 * x[1] / x[0]:.1f} %"
                      f" (normal {100 * pw[b]:.1f} %)")
    n, w, t, e = c2
    print(f"\n2) Empate en un cuarto, A gana el siguiente -> A pierde el tercero: {w}/{n} = {100 * w / n:.1f} % (empata {100 * t / n:.1f} %)."
          f" Lo normal: {100 * e / n:.1f} %. Diferencia: {100 * (w - e) / n:+.1f} puntos.")
    n, w, e = ot
    if n:
        print(f"\n3) Pierde 3.º y 4.º y hay prórroga -> gana la prórroga: {w}/{n} = {100 * w / n:.1f} %.")
    return dict(c1=c1_any, c2=c2, ot=ot)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args()
    games = with_levels(load(sqlite3.connect(a.db)))
    study(games, "Todas las ligas")
    for lgs, name in ((("Liga Endesa",), "Liga Endesa"), (("Euroliga", "EuroCup"), "Euroliga y EuroCup")):
        study([g for g in games if g["league"] in lgs], name)
    study([g for g in games if g["season"] < "2024"], "Temporadas 2021-24")
    study([g for g in games if g["season"] >= "2024"], "Temporadas 2024-27")


if __name__ == "__main__":
    main()
