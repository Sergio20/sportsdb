#!/usr/bin/env python3
"""Guarda el tiro por mitades de cada partido de Euroliga, EuroCup y Liga Endesa (tabla basket_half_stats).

Fuentes: live.euroleague.net/api/Points (todos los tiros con su minuto) y, para la Liga Endesa, la ficha
pública del partido en live.acb.com (estadísticas por cuartos). Incremental.
"""
import sqlite3, sys
from concurrent.futures import ThreadPoolExecutor
from sportsdb import acb
from sportsdb.common import DEFAULT_DB, http_get

SCHEMA = """CREATE TABLE IF NOT EXISTS basket_half_stats (
  match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
  is_home INTEGER NOT NULL, half INTEGER NOT NULL,      -- 1 = primera parte, 2 = segunda (sin prórrogas)
  points INTEGER, fg2m INTEGER, fg2a INTEGER, fg3m INTEGER, fg3a INTEGER, ftm INTEGER, fta INTEGER,
  PRIMARY KEY (match_id, is_home, half))"""

def fetch(row):
    mid, year, code, hc, ac, league = row
    comp = "E" if league == "Euroliga" else "U"
    try:
        r = http_get("https://live.euroleague.net/api/Points", params={"gamecode": code, "seasoncode": f"{comp}{year}"}, tries=3)
        rows = r.json().get("Rows", []) if r.text.strip() else []
    except Exception:
        return mid, None
    agg = {}
    for x in rows:
        team, act, minute = (x.get("TEAM") or "").strip(), (x.get("ID_ACTION") or "").strip(), x.get("MINUTE") or 0
        if minute > 40 or team not in (hc, ac):
            continue
        a = agg.setdefault((1 if team == hc else 0, 1 if minute <= 20 else 2), dict(points=0, fg2m=0, fg2a=0, fg3m=0, fg3a=0, ftm=0, fta=0))
        a["points"] += x.get("POINTS") or 0
        if act in ("2FGM", "2FGA"): a["fg2a"] += 1; a["fg2m"] += act == "2FGM"
        elif act in ("3FGM", "3FGA"): a["fg3a"] += 1; a["fg3m"] += act == "3FGM"
        elif act in ("FTM", "FTA"): a["fta"] += 1; a["ftm"] += act == "FTM"
    return mid, agg

ACB_KEYS = {"points": "points", "fg2m": "twoPointersMade", "fg2a": "twoPointersAttempted", "fg3m": "threePointersMade",
            "fg3a": "threePointersAttempted", "ftm": "freeThrowsMade", "fta": "freeThrowsAttempted"}

def fetch_acb(row):
    """Liga Endesa: la ficha pública del partido trae las estadísticas de cada equipo por cuartos."""
    mid, source_id = row
    try:
        payload = acb.rsc_payload(http_get(acb.MATCH_URL.format(id=source_id), tries=3).text)
        head = (acb.find_props(payload, "initialMatchHeader") or {}).get("initialMatchHeader") or {}
        stats = (acb.find_props(payload, "initialStatistics") or {}).get("initialStatistics") or {}
        home_id = head["teams"]["home"]["id"]
    except Exception:
        return mid, None
    agg = {}
    for i, tb in enumerate(stats.get("teamBoxscores") or []):
        is_home = int(tb["team"]["id"] == home_id) if tb.get("team") else int(i == 0)
        for per in tb.get("statsByPeriods") or []:
            if per["quarter"] not in (1, 2, 3, 4):
                continue
            a = agg.setdefault((is_home, 1 if per["quarter"] <= 2 else 2), dict.fromkeys(ACB_KEYS, 0))
            for k, src in ACB_KEYS.items():
                a[k] += int(per["stats"]["total"].get(src) or 0)
    return mid, agg

def main(db=DEFAULT_DB):
    con = sqlite3.connect(db); con.execute("PRAGMA foreign_keys=ON"); con.execute(SCHEMA)
    todo_acb = con.execute("""SELECT match_id, source_id FROM matches WHERE league='Liga Endesa' AND status='played'
          AND match_id NOT IN (SELECT match_id FROM basket_half_stats)""").fetchall()
    ok = bad = 0
    with ThreadPoolExecutor(4) as ex:
        for mid, agg in ex.map(fetch_acb, todo_acb):
            if not agg or len(agg) < 4:
                bad += 1; continue
            for (is_home, half), a in agg.items():
                con.execute("INSERT OR REPLACE INTO basket_half_stats VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (mid, is_home, half, a["points"], a["fg2m"], a["fg2a"], a["fg3m"], a["fg3a"], a["ftm"], a["fta"]))
            ok += 1
            if ok % 200 == 0: con.commit(); print(f"[Mitades] Liga Endesa {ok}/{len(todo_acb)}", flush=True)
    con.commit()
    print(f"[Mitades] Liga Endesa: {ok} partidos nuevos, {bad} sin datos por cuartos")
    todo = con.execute("""SELECT match_id, season_start, source_id, home_code, away_code, league FROM matches
        WHERE league IN ('Euroliga','EuroCup') AND status='played'
          AND match_id NOT IN (SELECT match_id FROM basket_half_stats)""").fetchall()
    ok = bad = 0
    with ThreadPoolExecutor(8) as ex:
        for mid, agg in ex.map(fetch, todo):
            if not agg or len(agg) < 4:
                bad += 1; continue
            for (is_home, half), a in agg.items():
                con.execute("INSERT OR REPLACE INTO basket_half_stats VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (mid, is_home, half, a["points"], a["fg2m"], a["fg2a"], a["fg3m"], a["fg3a"], a["ftm"], a["fta"]))
            ok += 1
            if ok % 200 == 0: con.commit()
    con.commit(); con.close()
    print(f"[Mitades] {ok} partidos nuevos, {bad} sin datos de tiro")

if __name__ == "__main__":
    main(*sys.argv[1:2])
