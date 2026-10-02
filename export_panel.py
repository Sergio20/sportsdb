#!/usr/bin/env python3
"""Exporta la base de datos a un JSON compacto para el panel SportsDB.

Uso:
    python export_panel.py [--db data/deportes.db] [--out data/panel_data.json]

Formato: por liga, lista de equipos y una fila por partido (arrays, para que pese poco).
Las columnas van en `cols`; los equipos se referencian por índice.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

from sportsdb.common import DEFAULT_DB

FOOT_COLS = ["season", "date", "time", "home", "away", "hs", "as", "played",
             "ht_h", "ht_a", "shots_h", "shots_a", "sot_h", "sot_a", "cor_h", "cor_a",
             "fouls_h", "fouls_a", "yel_h", "yel_a", "red_h", "red_a",
             "b365_h", "b365_d", "b365_a", "avg_h", "avg_d", "avg_a",
             "avgc_h", "avgc_d", "avgc_a", "maxc_h", "maxc_d", "maxc_a",
             "pinc_h", "pinc_d", "pinc_a", "over", "under"]

FOOT_SQL = """
SELECT season, date, time, home_team, away_team, home_score, away_score,
       ht_home, ht_away, shots_home, shots_away, sot_home, sot_away, corners_home, corners_away,
       fouls_home, fouls_away, yellow_home, yellow_away, red_home, red_away,
       b365_h, b365_d, b365_a, avg_h, avg_d, avg_a, avg_close_h, avg_close_d, avg_close_a,
       max_close_h, max_close_d, max_close_a, pin_close_h, pin_close_d, pin_close_a,
       avg_close_over25, avg_close_under25
FROM v_laliga ORDER BY date, time, home_team
"""

_ST = ["fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "assists", "steals",
       "turnovers", "blocks", "fouls", "rating"]
_SHORT = ["fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "tov", "blk", "pf", "val"]
BASK_COLS = (["season", "date", "time", "phase", "round", "home", "away", "hs", "as", "played", "ot",
              "q1_h", "q1_a", "q2_h", "q2_a", "q3_h", "q3_a", "q4_h", "q4_a"]
             + [f"{c}_h" for c in _SHORT] + [f"{c}_a" for c in _SHORT])

BASK_SQL = f"""
SELECT m.season, m.date, m.time, m.phase, m.round, m.home_team, m.away_team, m.home_code, m.away_code,
       m.home_score, m.away_score, m.status, m.overtimes,
       q1.home, q1.away, q2.home, q2.away, q3.home, q3.away, q4.home, q4.away,
       {', '.join('h.' + c for c in _ST)}, {', '.join('a.' + c for c in _ST)}
FROM matches m
LEFT JOIN periods q1 ON q1.match_id = m.match_id AND q1.period = 1
LEFT JOIN periods q2 ON q2.match_id = m.match_id AND q2.period = 2
LEFT JOIN periods q3 ON q3.match_id = m.match_id AND q3.period = 3
LEFT JOIN periods q4 ON q4.match_id = m.match_id AND q4.period = 4
LEFT JOIN basket_team_stats h ON h.match_id = m.match_id AND h.is_home = 1
LEFT JOIN basket_team_stats a ON a.match_id = m.match_id AND a.is_home = 0
WHERE m.league = ? AND m.status IN ('played', 'scheduled')
ORDER BY m.date, m.time, m.home_team
"""


def _football(con):
    teams, idx, rows = [], {}, []

    def tid(name):
        if name not in idx:
            idx[name] = len(teams)
            teams.append(name)
        return idx[name]

    for r in con.execute(FOOT_SQL):
        season, date, time, home, away, hs, as_ = r[:7]
        rows.append([season, date, time, tid(home), tid(away), hs, as_, 1 if hs is not None else 0, *r[7:]])
    return {"sport": "futbol", "teams": teams, "cols": FOOT_COLS, "matches": rows}


def _basket(con, league):
    # La identidad del club es su código (los nombres cambian con el patrocinador);
    # se muestra el nombre más reciente.
    teams, idx, rows = [], {}, []

    def tid(code, name):
        key = code or name
        if key not in idx:
            idx[key] = len(teams)
            teams.append(name)
        else:
            teams[idx[key]] = name  # filas en orden de fecha: gana el nombre más reciente
        return idx[key]

    for r in con.execute(BASK_SQL, (league,)):
        season, date, time, phase, rnd, home, away, hc, ac, hs, as_, status, ot = r[:13]
        played = 1 if status == "played" else 0
        rnd = int(rnd) if rnd is not None and str(rnd).isdigit() else rnd
        rows.append([season, date, time, phase, rnd, tid(hc, home), tid(ac, away),
                     hs if played else None, as_ if played else None, played, ot or 0, *r[13:]])
    return {"sport": "baloncesto", "teams": teams, "cols": BASK_COLS, "matches": rows,
            "extra_cols": EXTRA_COLS, "extra": _extra(con, league, idx)}


# Equivalencia de clubes españoles: código en acb.com -> código en Euroliga/EuroCup
ACB_TO_EL = {"9": "MAD", "2": "BAR", "3": "BAS", "13": "PAM", "5": "CAN", "8": "JOV",
             "10": "MAN", "22": "ANR"}
EL_TO_ACB = {v: k for k, v in ACB_TO_EL.items()}
EXTRA_COLS = ["date", "time", "comp", "home", "away", "hn", "an", "hs", "as"]


def _extra(con, league, idx):
    """Partidos jugados en OTRAS competiciones por los clubes de esta liga (para el cara a cara).

    `home`/`away` son el índice del club en esta liga, o -1 si el rival no pertenece a ella.
    """
    def key(src_league, code):
        if code is None:
            return None
        if league == "Liga Endesa":
            return code if src_league == "Liga Endesa" else EL_TO_ACB.get(code)
        return ACB_TO_EL.get(code) if src_league == "Liga Endesa" else code

    rows = []
    for lg, date, time, phase, home, away, hc, ac, hs, as_ in con.execute(
            "SELECT league, date, time, phase, home_team, away_team, home_code, away_code, "
            "home_score, away_score FROM matches WHERE sport = 'baloncesto' AND status = 'played' "
            "AND league <> ? ORDER BY date, time", (league,)):
        hi, ai = idx.get(key(lg, hc), -1), idx.get(key(lg, ac), -1)
        if hi < 0 and ai < 0:
            continue
        comp = lg if phase in (None, "", "Liga Regular") or lg in ("Euroliga", "EuroCup", "Liga Endesa") else f"{lg} · {phase}"
        rows.append([date, time, comp, hi, ai, home, away, hs, as_])
    return rows


def export(db=DEFAULT_DB, out=None):
    db = Path(db)
    out = Path(out) if out else db.parent / "panel_data.json"
    con = sqlite3.connect(db)
    data = {
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "leagues": {
            "LaLiga": _football(con),
            "Euroliga": _basket(con, "Euroliga"),
            "Liga Endesa": _basket(con, "Liga Endesa"),
        },
    }
    con.close()
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n = sum(len(v["matches"]) for v in data["leagues"].values())
    print(f"Datos del panel escritos en {out} ({n} partidos, {out.stat().st_size / 1e6:.1f} MB)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    export(a.db, a.out)
