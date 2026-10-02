#!/usr/bin/env python3
"""¿Remonta el favorito cuando va perdiendo? Estudio con los partidos ya jugados.

Para cada partido de Euroliga, EuroCup y Liga Endesa se calcula quién era el favorito
ANTES de empezar (solo con partidos anteriores de la misma competición) y se mira el
marcador al final de cada cuarto. Cuando el favorito va perdiendo, se comprueba qué
pasó al final y con qué frecuencia habría cubierto un hándicap positivo.

Uso:
    python scripts/remontadas.py [--db data/deportes.db] [--json salida.json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sportsdb.common import DEFAULT_DB  # noqa: E402

LEAGUES = ("Euroliga", "EuroCup", "Liga Endesa")
LAST_N = 20       # partidos recientes para medir el nivel de cada equipo
MIN_GAMES = 5     # mínimo para considerar fiable ese nivel
FAV = 5           # diferencia prevista para hablar de «favorito claro»
DEFICITS = [(6, 9), (10, 14), (15, 99)]
LINES = [0, 2.5, 4.5, 6.5, 8.5]

SQL = """
SELECT m.match_id, m.league, m.season_start, m.date, m.home_code, m.away_code,
       m.home_team, m.away_team, m.home_score, m.away_score, m.overtimes,
       (SELECT group_concat(p.period || ':' || p.home || ':' || p.away) FROM periods p
         WHERE p.match_id = m.match_id AND p.period <= 4) AS q
FROM matches m
WHERE m.league IN ({}) AND m.status = 'played' AND m.home_score IS NOT NULL
ORDER BY m.date, m.time, m.match_id
""".format(",".join("?" * len(LEAGUES)))

HALF_SQL = """
SELECT match_id, is_home, points, fg2m, fg2a, fg3m, fg3a, ftm, fta
FROM basket_half_stats WHERE half = 1
"""


def efg(r):
    fga = (r["fg2a"] or 0) + (r["fg3a"] or 0)
    return 100 * ((r["fg2m"] or 0) + 1.5 * (r["fg3m"] or 0)) / fga if fga else None


def load(con):
    games = []
    for r in con.execute(SQL, LEAGUES):
        q = {}
        for part in (r[11] or "").split(","):
            if part:
                p, h, a = part.split(":")
                q[int(p)] = (int(h or 0), int(a or 0))
        if len(q) < 4:
            continue
        games.append(dict(id=r[0], league=r[1], season=r[2], date=r[3], hc=r[4], ac=r[5], ht=r[6], at=r[7],
                          hs=r[8], as_=r[9], ot=r[10] or 0, q=q))
    halves = defaultdict(dict)
    con.row_factory = sqlite3.Row
    for r in con.execute(HALF_SQL):
        halves[r["match_id"]][r["is_home"]] = dict(r)
    return games, halves


def pregame(games):
    """Añade a cada partido la diferencia prevista para el local, sin mirar el futuro."""
    by_league = defaultdict(list)
    for g in games:
        by_league[g["league"]].append(g)
    for lg, gs in by_league.items():
        hist = defaultdict(lambda: deque(maxlen=LAST_N))
        # factor campo de la competición (media de toda la muestra; varía poco entre temporadas)
        hca = sum(g["hs"] - g["as_"] for g in gs) / len(gs)
        for g in gs:
            h, a = hist[g["hc"]], hist[g["ac"]]
            if len(h) >= MIN_GAMES and len(a) >= MIN_GAMES:
                g["exp"] = hca + (sum(h) / len(h) - sum(a) / len(a)) / 2
            else:
                g["exp"] = None
            m = g["hs"] - g["as_"]
            h.append(m - hca)
            a.append(-m + hca)
        # calibración: la diferencia prevista se ajusta a la real con una recta
        xs = [(g["exp"], g["hs"] - g["as_"]) for g in gs if g["exp"] is not None]
        n = len(xs)
        mx, my = sum(x for x, _ in xs) / n, sum(y for _, y in xs) / n
        b = sum((x - mx) * (y - my) for x, y in xs) / sum((x - mx) ** 2 for x, _ in xs)
        for g in gs:
            if g["exp"] is not None:
                g["exp"] = my + b * (g["exp"] - mx)
        print(f"{lg}: {n} partidos con favorito calculable · factor campo {hca:+.1f} · ajuste {b:.2f}")


def study(games, halves):
    rows = []
    for g in games:
        e = g["exp"]
        if e is None or abs(e) < FAV:
            continue
        s = 1 if e > 0 else -1                      # 1 = el favorito juega en casa
        final = s * (g["hs"] - g["as_"])
        cum_h = cum_a = 0
        for p in (1, 2, 3):
            cum_h += g["q"][p][0]
            cum_a += g["q"][p][1]
            mid = s * (cum_h - cum_a)                # diferencia del favorito al final del cuarto p
            if mid >= 0:
                continue
            row = dict(league=g["league"], season=g["season"], id=g["id"], date=g["date"], q=p, exp=abs(e),
                       deficit=-mid, final=final, rest=final - mid,
                       fav=g["ht"] if s > 0 else g["at"], dog=g["at"] if s > 0 else g["ht"])
            if p == 2 and g["id"] in halves and len(halves[g["id"]]) == 2:
                hf, hd = halves[g["id"]][1 if s > 0 else 0], halves[g["id"]][0 if s > 0 else 1]
                row["efg_fav"], row["efg_dog"] = efg(hf), efg(hd)
            rows.append(row)
    return rows


def pct(x, n):
    return f"{100 * x / n:4.0f} %" if n else "   –"


def report(rows, games):
    # Referencia: cuánto recupera el favorito según su ventaja prevista restante
    print("\nCUANDO EL FAVORITO CLARO (5+ puntos antes de empezar) VA PERDIENDO")
    print("Recuperación = puntos que gana en lo que queda de partido; «por nivel» = lo que se esperaba solo por ser mejor.")
    print("«+X» = veces que acaba perdiendo por menos de X (cubre ese hándicap).")
    for p, name in ((1, "Final del 1.er cuarto"), (2, "Descanso"), (3, "Final del 3.er cuarto")):
        print(f"\n== {name} ==")
        print(f"{'Pierde por':<11}{'Casos':>6}{'Recupera':>10}{'Por nivel':>10}{'Gana':>7}" + "".join(f"{'+' + str(l):>8}" for l in LINES[1:]))
        for lo, hi in DEFICITS:
            sub = [r for r in rows if r["q"] == p and lo <= r["deficit"] <= hi]
            n = len(sub)
            if not n:
                continue
            rec = sum(r["rest"] for r in sub) / n
            lvl = sum(r["exp"] * (4 - p) / 4 for r in sub) / n
            line = f"{f'{lo}-{hi}' if hi < 99 else f'{lo}+':<11}{n:>6}{rec:>+10.1f}{lvl:>+10.1f}{pct(sum(r['final'] > 0 for r in sub), n):>7}"
            line += "".join(f"{pct(sum(r['final'] + l > 0 for r in sub), n):>8}" for l in LINES[1:])
            print(line)
    # ¿Importa que la desventaja venga de un acierto anormal?
    print("\n== Descanso: ¿remonta más si va perdiendo por un acierto anormal? ==")
    half = [r for r in rows if r["q"] == 2 and r.get("efg_fav") is not None and r.get("efg_dog") is not None]
    for lo, hi in DEFICITS:
        sub = [r for r in half if lo <= r["deficit"] <= hi]
        if not sub:
            continue
        hot = [r for r in sub if r["efg_dog"] - r["efg_fav"] >= 15]
        cold = [r for r in sub if r["efg_dog"] - r["efg_fav"] < 5]
        for lab, s in (("acierto muy desigual (15+ pts eFG a favor del rival)", hot), ("acierto parecido (menos de 5 pts)", cold)):
            if s:
                print(f"  pierde {lo}-{hi if hi < 99 else '+'} · {lab}: {len(s)} casos · recupera {sum(r['rest'] for r in s) / len(s):+.1f} · "
                      f"gana {pct(sum(r['final'] > 0 for r in s), len(s)).strip()} · cubre +4,5 {pct(sum(r['final'] + 4.5 > 0 for r in s), len(s)).strip()}")
    # Por competición
    print("\n== Descanso, pierde por 10 o más, por competición ==")
    for lg in LEAGUES:
        sub = [r for r in rows if r["q"] == 2 and r["deficit"] >= 10 and r["league"] == lg]
        if sub:
            print(f"  {lg:<12} {len(sub):>4} casos · recupera {sum(r['rest'] for r in sub) / len(sub):+.1f} · gana {pct(sum(r['final'] > 0 for r in sub), len(sub)).strip()}"
                  f" · cubre +4,5 {pct(sum(r['final'] + 4.5 > 0 for r in sub), len(sub)).strip()} · cubre +8,5 {pct(sum(r['final'] + 8.5 > 0 for r in sub), len(sub)).strip()}")


def promoted(games):
    """Equipos que pasan de EuroCup a Euroliga: nivel en EuroCup frente a lo que rinden en Euroliga."""
    net = defaultdict(list)
    for g in games:
        net[(g["league"], g["season"], g["hc"])].append(g["hs"] - g["as_"])
        net[(g["league"], g["season"], g["ac"])].append(g["as_"] - g["hs"])
    names = {}
    for g in games:
        names[g["hc"]], names[g["ac"]] = g["ht"], g["at"]
    print("\n== Equipos que suben de EuroCup a Euroliga ==")
    pairs = []
    for (lg, season, code), ms in net.items():
        if lg != "EuroCup" or len(ms) < 10:
            continue
        nxt = net.get(("Euroliga", season + 1, code))
        prev_el = net.get(("Euroliga", season, code))
        if nxt and len(nxt) >= 10 and not prev_el:
            a, b = sum(ms) / len(ms), sum(nxt) / len(nxt)
            pairs.append((a, b))
            print(f"  {names.get(code, code):<28} EuroCup {season}-{(season + 1) % 100:02d}: {a:+5.1f} por partido → Euroliga siguiente: {b:+5.1f}")
    if pairs:
        da = sum(a - b for a, b in pairs) / len(pairs)
        print(f"  Media: rinden {da:.1f} puntos por partido peor en Euroliga que en la EuroCup del año anterior ({len(pairs)} casos)")
    return pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    games, halves = load(con)
    pregame(games)
    rows = study(games, halves)
    report(rows, games)
    promoted(games)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
