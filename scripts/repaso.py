#!/usr/bin/env python3
"""Repaso de partidos ya jugados: qué avisos habría dado el panel en vivo, minuto a minuto, y cómo acabaron.

Reproduce las reglas de panel/en_vivo_plantilla.html (desfase, ritmo insostenible, total desfasado) con las
jugadas oficiales y los valores habituales de cada equipo calculados SOLO con partidos anteriores.
Para cada aviso comprueba, con el resultado final, cuatro hándicaps: el justo, el igual a la desventaja,
y los «de seguridad» al 80 % y al 90 % (justo + 0,84 y + 1,28 veces el margen de error).

    python scripts/repaso.py --desde 2026-10-01 [--hasta 2026-10-02] [--db data/deportes.db] [--json salida.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import export_live  # noqa: E402
from build_timeline import PERIODS, minute  # noqa: E402
from sportsdb.common import DEFAULT_DB, UA  # noqa: E402

API = "https://live.euroleague.net/api/PlaybyPlay"
K = dict(home=0.10, q=0.021, m=-0.010, sd=2.1)
KT = dict(a=1.02, c=0.08, sd=2.48)
FAV, GAP, Z, MIN_EL = 5, 6, 1.5, 8
LEAD, PACE_EXTRA, LEVEL_GAP, MIN_EL_PACE = 10, 20, 3, 6
TOTAL_GAP = 18
Z80, Z90 = 0.8416, 1.2816


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def up_half(x):
    """Redondea hacia arriba al medio punto (hándicap un poco más largo, nunca más corto)."""
    return math.ceil(x * 2) / 2


def fetch(ses, year, code, comp):
    for wait in (0, 45, 90, 180, 300):
        time.sleep(wait)
        r = ses.get(API, params={"gamecode": code, "seasoncode": f"{comp}{year}"}, timeout=40)
        if r.status_code == 200 and r.text.strip():
            return r.json()
        if r.status_code != 429:
            return None
    return None


def totals(plays, code_a, upto):
    mk = lambda: dict(pts=0, m2=0, a2=0, m3=0, a3=0, mf=0, af=0)  # noqa: E731
    A, B = mk(), mk()
    for p in plays:
        if p["t"] > upto or not p["team"]:
            continue
        o, k = (A if p["team"] == code_a else B), p["type"]
        if k == "2FGM": o["m2"] += 1; o["a2"] += 1; o["pts"] += 2
        elif k == "2FGA": o["a2"] += 1
        elif k == "3FGM": o["m3"] += 1; o["a3"] += 1; o["pts"] += 3
        elif k == "3FGA": o["a3"] += 1
        elif k == "FTM": o["mf"] += 1; o["af"] += 1; o["pts"] += 1
        elif k == "FTA": o["af"] += 1
    return A, B


def xp(t, b):
    return 2 * t["a2"] * b["p2"] / 100 + 3 * t["a3"] * b["p3"] / 100 + t["af"] * b["ft"] / 100


def luck_var(t, b):
    v = lambda a, p, w: w * a * p / 100 * (1 - p / 100)  # noqa: E731
    return v(t["a2"], b["p2"], 4) + v(t["a3"], b["p3"], 9) + v(t["af"], b["ft"], 1)


def efg(t):
    fga = t["a2"] + t["a3"]
    return 100 * (t["m2"] + 1.5 * t["m3"]) / fga if fga else 0


def alerts_at(el, A, B, bA, bB):
    """Avisos activos en el minuto el. Cada uno: tipo, lado de la apuesta (1 = local, -1 = visitante) y líneas."""
    out = []
    r = 40 - el
    if r <= 0:
        return out
    Q = (bA["pf"] - bA["pa"]) - (bB["pf"] - bB["pa"])
    M = A["pts"] - B["pts"]
    mean, sd = M + r * (K["home"] + K["q"] * Q + K["m"] * M), K["sd"] * math.sqrt(r)
    pre = 40 * (K["home"] + K["q"] * Q)

    def lines(s):  # s = lado al que se apuesta con hándicap positivo
        fair = -s * mean
        return dict(side=s, deficit=-s * M, fair=round(fair * 2) / 2, now=-s * M + 0.5, s80=up_half(fair + Z80 * sd), s90=up_half(fair + Z90 * sd))

    # Desfase: favorito claro por debajo de lo previsto por un acierto anormal
    if el >= MIN_EL and abs(pre) >= FAV:
        s = 1 if pre >= 0 else -1
        lA, lB = A["pts"] - xp(A, bA), B["pts"] - xp(B, bB)
        gap = (lB - lA) if s > 0 else (lA - lB)
        v = math.sqrt(luck_var(A, bA) + luck_var(B, bB))
        if gap >= GAP and v > 0 and gap / v >= Z and s * M < abs(pre) * el / 40:
            out.append(dict(type="desfase", gap=gap, z=gap / v, **lines(s)))
    # Ritmo insostenible: gana de 10+ muy por encima de su media ante un rival de su nivel o mejor
    if el >= MIN_EL_PACE and abs(M) >= LEAD:
        s = 1 if M > 0 else -1          # lado que gana
        lead, base = (A, bA) if s > 0 else (B, bB)
        extra = lead["pts"] * 40 / el - base["pf"]
        if extra >= PACE_EXTRA and -s * pre >= -LEVEL_GAP:
            out.append(dict(type="ritmo", extra=extra, **lines(-s)))
    # Total desfasado
    if 8 <= el <= 32 and bA.get("tot") and bB.get("tot"):
        usual, cur = (bA["tot"] + bB["tot"]) / 2, A["pts"] + B["pts"]
        tmean = cur + r * (KT["a"] * usual / 40 + KT["c"] * (cur / el - usual / 40))
        tsd, pace = KT["sd"] * math.sqrt(r), cur * 40 / el
        if abs(pace - tmean) >= TOTAL_GAP:
            high = pace > tmean
            sgn = 1 if high else -1     # exceso de ritmo -> «menos de»; línea de seguridad por encima del total justo
            out.append(dict(type="total", high=high, pace=pace, fair=round(tmean * 2) / 2,
                            s80=up_half(tmean + sgn * Z80 * tsd) if high else math.floor((tmean - Z80 * tsd) * 2) / 2,
                            s90=up_half(tmean + sgn * Z90 * tsd) if high else math.floor((tmean - Z90 * tsd) * 2) / 2))
    return out


def review(con, row, base, ses):
    mid, league, year, code, hc, ac, ht, at, hs, as_ = row
    d = fetch(ses, year, code, "E" if league == "Euroliga" else "U")
    bA, bB = base.get(hc), base.get(ac)
    game = dict(id=mid, league=league, home=ht, away=at, hs=hs, as_=as_, alerts=[])
    if not d or not bA or not bB:
        game["skip"] = "sin jugadas" if not d else "equipo sin partidos previos suficientes"
        return game
    plays = [dict(t=minute(p), team=(p.get("CODETEAM") or "").strip(), type=(p.get("PLAYTYPE") or "").strip())
             for k in PERIODS[:4] for p in (d.get(k) or [])]
    reg = totals(plays, hc, 40)
    reg_total, final = reg[0]["pts"] + reg[1]["pts"], hs - as_
    seen = {}
    for el in range(6, 39):
        A, B = totals(plays, hc, el)
        for al in alerts_at(el, A, B, bA, bB):
            al.update(el=el, score=f'{A["pts"]}-{B["pts"]}')
            key = (al["type"], al.get("side"))
            seen.setdefault(key, []).append(al)
    for (kind, side), als in seen.items():
        first = als[0]
        best = max(als, key=lambda a: a.get("deficit", abs(a.get("pace", 0) - a["fair"])))   # momento de mayor desventaja / mayor desfase
        for label, al in (("primer aviso", first), ("mejor momento", best)):
            if label == "mejor momento" and al is first:
                continue
            if kind == "total":
                under = al["high"]
                res = {k: (reg_total < al[k]) if under else (reg_total > al[k]) for k in ("fair", "s80", "s90")}
                game["alerts"].append(dict(kind=kind, when=label, el=al["el"], score=al["score"], bet=("menos de" if under else "más de"),
                                           pace=round(al["pace"]), lines={k: al[k] for k in ("fair", "s80", "s90")}, res=res, minutes=len(als), end=reg_total))
            else:
                m = side * final
                res = {k: m + al[k] > 0 for k in ("fair", "now", "s80", "s90")}
                game["alerts"].append(dict(kind=kind, when=label, el=al["el"], score=al["score"], bet=(ht if side > 0 else at),
                                           lines={k: al[k] for k in ("fair", "now", "s80", "s90")}, res=res, minutes=len(als), end=m))
    return game


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--desde", required=True)
    ap.add_argument("--hasta", default="9999-12-31")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    base = export_live.baseline(con, before=a.desde)
    rows = con.execute("""SELECT match_id, league, season_start, source_id, home_code, away_code, home_team, away_team, home_score, away_score
        FROM matches WHERE league IN ('Euroliga','EuroCup') AND status = 'played' AND date BETWEEN ? AND ? ORDER BY date, time""", (a.desde, a.hasta)).fetchall()
    ses = requests.Session()
    ses.headers["User-Agent"] = UA
    games = []
    for row in rows:
        g = review(con, row, base, ses)
        games.append(g)
        print(f'\n=== {g["league"]} · {g["home"]} {g["hs"]}-{g["as_"]} {g["away"]} ===', flush=True)
        if g.get("skip"):
            print("  (no se puede repasar: " + g["skip"] + ")")
        elif not g["alerts"]:
            print("  Sin avisos.")
        for x in g["alerts"]:
            ok = lambda k: "SÍ" if x["res"][k] else "NO"  # noqa: E731
            if x["kind"] == "total":
                print(f'  [total · {x["when"]}] min {x["el"]} ({x["score"]}), ritmo {x["pace"]} → {x["bet"]}: justo {x["lines"]["fair"]} {ok("fair")} · '
                      f'80% {x["lines"]["s80"]} {ok("s80")} · 90% {x["lines"]["s90"]} {ok("s90")} · acabó en {x["end"]} · activo {x["minutes"]} min')
            else:
                print(f'  [{x["kind"]} · {x["when"]}] min {x["el"]} ({x["score"]}) → {x["bet"]}: justo {x["lines"]["fair"]:+} {ok("fair")} · ventaja {x["lines"]["now"]:+} {ok("now")} · '
                      f'80% {x["lines"]["s80"]:+} {ok("s80")} · 90% {x["lines"]["s90"]:+} {ok("s90")} · acabó {x["end"]:+} · activo {x["minutes"]} min')
        time.sleep(3)
    if a.json:
        Path(a.json).write_text(json.dumps(games, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
