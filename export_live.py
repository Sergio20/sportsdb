#!/usr/bin/env python3
"""Genera "SportsDB en vivo.html": el panel de partidos en directo de Euroliga y EuroCup.

La página lee ella sola los datos en vivo de la fuente oficial; este script sólo le incrusta
los valores habituales de cada equipo (medias de sus últimos partidos) para compararlos.

Uso:
    python export_live.py [--db data/deportes.db] [--out "data/SportsDB en vivo.html"]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from sportsdb.common import DEFAULT_DB

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
import remontadas  # noqa: E402
TEMPLATE = ROOT / "panel" / "en_vivo_plantilla.html"
LAST_N = 30
# Los equipos que suben de la EuroCup rinden en Euroliga unos 9-10 puntos por partido peor que en la
# EuroCup del año anterior (scripts/remontadas.py, 6 casos 2021-25). Los partidos de EuroCup se pasan
# a escala Euroliga restando esto a la diferencia; así el favorito es comparable entre competiciones.
EUROCUP_GAP = 9.0
KEY_GAMES, KEY_PLAYERS = 10, 6   # jugadores clave: los que más minutos juegan en sus últimos 10 partidos

SQL = """
SELECT CASE s.is_home WHEN 1 THEN m.home_code ELSE m.away_code END AS code, m.date,
       s.points, s.fg2m, s.fg2a, s.fg3m, s.fg3a, s.ftm, s.fta, s.oreb, s.turnovers,
       CASE s.is_home WHEN 1 THEN m.away_score ELSE m.home_score END AS against, m.league
FROM basket_team_stats s JOIN matches m ON m.match_id = s.match_id
WHERE m.league IN ({leagues}) AND m.status = 'played' AND s.fg2a IS NOT NULL AND m.date < ?
ORDER BY m.date DESC
"""

PLAYERS_SQL = """
SELECT CASE p.is_home WHEN 1 THEN m.home_code ELSE m.away_code END AS code, m.match_id,
       p.player_id, p.player, p.seconds, p.points, m.season_start
FROM basket_player_stats p JOIN matches m ON m.match_id = p.match_id
WHERE m.league IN ({leagues}) AND m.status = 'played'
ORDER BY m.date DESC
"""


def key_players(con, leagues=("Euroliga", "EuroCup")) -> dict:
    """Por equipo: [id, nombre, puntos por partido, minutos por partido, partidos jugados de los últimos 10].
    Solo con partidos de su temporada más reciente (las plantillas cambian mucho en verano) y si hay al menos 2."""
    games, stats, latest = {}, {}, {}
    for code, mid, pid, name, secs, pts, season in con.execute(PLAYERS_SQL.format(leagues=",".join("?" * len(leagues))), leagues):
        if latest.setdefault(code, season) != season:
            continue
        g = games.setdefault(code, [])
        if mid not in g:
            if len(g) >= KEY_GAMES:
                continue
            g.append(mid)
        s = stats.setdefault(code, {}).setdefault(pid, [name, 0, 0, 0])
        s[1] += pts or 0
        s[2] += secs or 0
        s[3] += 1
    out = {}
    for code, ps in stats.items():
        n = len(games[code])
        if n < 2:
            continue
        top = sorted(ps.items(), key=lambda kv: -kv[1][2])[:KEY_PLAYERS]
        out[code] = [[pid.strip().lstrip("P"), v[0], round(v[1] / v[3], 1), round(v[2] / 60 / v[3], 1), v[3]] for pid, v in top if v[3] >= max(2, n // 2)]
    return out


def euroleague_seasons(con) -> dict:
    """Temporadas de Euroliga que ha jugado cada equipo (año de inicio)."""
    out = {}
    for code, season in con.execute("""SELECT home_code, season_start FROM matches WHERE league = 'Euroliga' AND status = 'played'
                                       UNION SELECT away_code, season_start FROM matches WHERE league = 'Euroliga' AND status = 'played'"""):
        out.setdefault(code, set()).add(season)
    return out


def baseline(con, before="9999-12-31", leagues=("Euroliga", "EuroCup")) -> dict:
    """Valores habituales de cada equipo. `before` deja fuera los partidos de esa fecha en adelante
    (para repasar partidos ya jugados sin hacer trampa con lo que pasó después). `leagues` permite
    calcularlos para la Liga Endesa (el vigilante de Telegram), con sus propios códigos de club."""
    by = {}
    for r in con.execute(SQL.format(leagues=",".join("?" * len(leagues))), (*leagues, before)):
        rows = by.setdefault(r[0], [])
        if len(rows) < LAST_N:
            rows.append(r)
    players, el_seasons = key_players(con, leagues), euroleague_seasons(con)
    current = max((s for ss in el_seasons.values() for s in ss), default=0)
    out = {}
    for code, rows in by.items():
        if not code or len(rows) < 3:
            continue
        s = lambda i: sum(x[i] or 0 for x in rows)  # noqa: E731
        n = len(rows)
        ec = sum(1 for x in rows if x[12] == "EuroCup")
        fga, fgm = s(4) + s(6), s(3) + s(5)
        poss = fga + 0.44 * s(8) - s(9) + s(10)
        seasons = el_seasons.get(code, set())
        out[code] = {"n": n, "p2": round(100 * s(3) / s(4), 1), "p3": round(100 * s(5) / s(6), 1),
                     "ft": round(100 * s(7) / s(8), 1), "efg": round(100 * (fgm + 0.5 * s(5)) / fga, 1),
                     "r3": round(100 * s(6) / fga, 1), "ppp": round(s(2) / poss, 3),
                     "poss": round(poss / n, 1), "pf": round(s(2) / n, 1),
                     "tot": round((s(2) + s(11)) / n, 1),   # total de puntos habitual de sus partidos
                     # puntos recibidos en escala Euroliga (pf - pa = nivel comparable entre competiciones)
                     "pa": round((s(11) + EUROCUP_GAP * ec) / n, 1), "ec": ec,
                     # temporadas de Euroliga anteriores a la actual (de las que hay en la base)
                     "elx": len([x for x in seasons if x < current]), "new": current in seasons and (current - 1) not in seasons,
                     "pl": players.get(code, [])}
    return out


def export(db=DEFAULT_DB, out=None):
    db = Path(db)
    out = Path(out) if out else db.parent / "SportsDB en vivo.html"
    con = sqlite3.connect(db)
    base = baseline(con)
    base.update(baseline(con, leagues=("Liga Endesa",)))     # códigos de club de acb.com (numéricos): no chocan con los de Euroliga
    hist = remontadas.cases(con)
    con.close()
    html = TEMPLATE.read_text(encoding="utf-8")
    a, b = html.index("/*BASELINE*/"), html.index("/*END*/")
    html = html[:a] + "/*BASELINE*/" + json.dumps(base, separators=(",", ":")) + html[b:]
    a, b = html.index("/*HISTORY*/"), html.index("/*ENDHISTORY*/")
    html = html[:a] + "/*HISTORY*/" + json.dumps(hist, separators=(",", ":")) + html[b:]
    out.write_text(html, encoding="utf-8")
    print(f"Panel en vivo escrito en {out} ({len(base)} equipos con valores habituales, "
          f"{len(hist['fav'])} remontadas y {len(hist['pace'])} ventajas de 10+ del histórico)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    export(a.db, a.out)
