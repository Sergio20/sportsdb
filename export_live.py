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
from pathlib import Path

from sportsdb.common import DEFAULT_DB

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "panel" / "en_vivo_plantilla.html"
LAST_N = 30

SQL = """
SELECT CASE s.is_home WHEN 1 THEN m.home_code ELSE m.away_code END AS code, m.date,
       s.points, s.fg2m, s.fg2a, s.fg3m, s.fg3a, s.ftm, s.fta, s.oreb, s.turnovers,
       CASE s.is_home WHEN 1 THEN m.away_score ELSE m.home_score END AS against
FROM basket_team_stats s JOIN matches m ON m.match_id = s.match_id
WHERE m.league IN ('Euroliga', 'EuroCup') AND m.status = 'played' AND s.fg2a IS NOT NULL
ORDER BY m.date DESC
"""


def baseline(con) -> dict:
    by = {}
    for r in con.execute(SQL):
        rows = by.setdefault(r[0], [])
        if len(rows) < LAST_N:
            rows.append(r)
    out = {}
    for code, rows in by.items():
        if not code or len(rows) < 3:
            continue
        s = lambda i: sum(x[i] or 0 for x in rows)  # noqa: E731
        n = len(rows)
        fga, fgm = s(4) + s(6), s(3) + s(5)
        poss = fga + 0.44 * s(8) - s(9) + s(10)
        out[code] = {"n": n, "p2": round(100 * s(3) / s(4), 1), "p3": round(100 * s(5) / s(6), 1),
                     "ft": round(100 * s(7) / s(8), 1), "efg": round(100 * (fgm + 0.5 * s(5)) / fga, 1),
                     "r3": round(100 * s(6) / fga, 1), "ppp": round(s(2) / poss, 3),
                     "poss": round(poss / n, 1), "pf": round(s(2) / n, 1), "pa": round(s(11) / n, 1)}
    return out


def export(db=DEFAULT_DB, out=None):
    db = Path(db)
    out = Path(out) if out else db.parent / "SportsDB en vivo.html"
    con = sqlite3.connect(db)
    base = baseline(con)
    con.close()
    html = TEMPLATE.read_text(encoding="utf-8")
    a, b = html.index("/*BASELINE*/"), html.index("/*END*/")
    html = html[:a] + "/*BASELINE*/" + json.dumps(base, separators=(",", ":")) + html[b:]
    out.write_text(html, encoding="utf-8")
    print(f"Panel en vivo escrito en {out} ({len(base)} equipos con valores habituales)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    export(a.db, a.out)
