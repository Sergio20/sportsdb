"""Euroliga desde la API oficial.

- Calendario, resultado y parciales: api-live.euroleague.net/v2/.../games
- Estadísticas de equipo y jugador: api-live.euroleague.net/v2/.../games/{code}/stats
  (si falla, alternativa: live.euroleague.net/api/Boxscore)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .common import (as_int, http_get, log_run, replace_rows, result_of, season_label,
                     set_periods, upsert_match)

LEAGUE = "Euroliga"
API = "https://api-live.euroleague.net/v2/competitions/{comp}/seasons/{comp}{year}"
LIVE = "https://live.euroleague.net/api/Boxscore"
PHASES = {"RS": "Liga Regular", "PI": "Play-In", "PO": "Playoff", "FF": "Final Four",
          "TS": "Top 16", "8F": "Octavos", "4F": "Cuartos", "2F": "Semifinales", "Final": "Final"}

V2_MAP = {"points": "points", "fg2m": "fieldGoalsMade2", "fg2a": "fieldGoalsAttempted2",
          "fg3m": "fieldGoalsMade3", "fg3a": "fieldGoalsAttempted3", "ftm": "freeThrowsMade",
          "fta": "freeThrowsAttempted", "oreb": "offensiveRebounds", "dreb": "defensiveRebounds",
          "treb": "totalRebounds", "assists": "assistances", "steals": "steals",
          "turnovers": "turnovers", "blocks": "blocksFavour", "blocks_against": "blocksAgainst",
          "fouls": "foulsCommited", "fouls_drawn": "foulsReceived", "rating": "valuation"}
LIVE_MAP = {k: v[0].upper() + v[1:] for k, v in V2_MAP.items()}


def _mins_to_sec(s):
    try:
        m, sec = str(s).split(":")
        return int(m) * 60 + int(sec)
    except (ValueError, AttributeError):
        return None


def fetch_stats(year: int, code: int, comp: str = "E") -> dict | None:
    """Devuelve {'teams': [..2 filas..], 'players': [...], 'coaches': (local, visitante)}."""
    try:
        r = http_get(f"{API.format(year=year, comp=comp)}/games/{code}/stats", ok_404=True, tries=3)
        if r is not None:
            d = r.json()
            teams, players, coaches = [], [], []
            for is_home, side in ((1, "local"), (0, "road")):
                s = d[side]
                tot = s["total"]
                teams.append({k: as_int(tot.get(v)) for k, v in V2_MAP.items()} | {"is_home": is_home})
                coaches.append((s.get("coach") or {}).get("name"))
                for p in s.get("players") or []:
                    st, person = p["stats"], p["player"]["person"]
                    if not st.get("timePlayed"):
                        continue
                    players.append({k: as_int(st.get(v)) for k, v in V2_MAP.items()} | {
                        "is_home": is_home, "player_id": str(person["code"]).strip(),
                        "player": person.get("name"), "starter": int(bool(st.get("startFive"))),
                        "seconds": as_int(st.get("timePlayed")),
                        "plus_minus": as_int(st.get("plusMinus"))})
            if teams[0]["points"] is not None:
                return {"teams": teams, "players": players, "coaches": tuple(coaches)}
    except Exception:
        pass
    # Alternativa: boxscore del servicio en directo (orden: local, visitante)
    r = http_get(LIVE, params={"gamecode": code, "seasoncode": f"{comp}{year}"}, tries=3)
    if not r.text.strip():
        return None
    d = r.json()
    teams, players, coaches = [], [], []
    for is_home, s in zip((1, 0), d["Stats"]):
        tot = s["totr"]
        teams.append({k: as_int(tot.get(v)) for k, v in LIVE_MAP.items()} | {"is_home": is_home})
        coaches.append(s.get("Coach"))
        for p in s["PlayersStats"]:
            sec = _mins_to_sec(p.get("Minutes"))
            if not sec:
                continue
            players.append({k: as_int(p.get(v)) for k, v in LIVE_MAP.items()} | {
                "is_home": is_home, "player_id": str(p["Player_ID"]).strip().lstrip("P"),
                "player": p.get("Player"), "starter": as_int(p.get("IsStarter")),
                "seconds": sec, "plus_minus": as_int(p.get("Plusminus"))})
    return {"teams": teams, "players": players, "coaches": tuple(coaches)}


def _quarters(g: dict) -> list[tuple[int, int]]:
    hp, ap = g["local"].get("partials") or {}, g["road"].get("partials") or {}
    q = [(hp.get(f"partials{i}"), ap.get(f"partials{i}")) for i in range(1, 5)]
    if any(h is None or a is None for h, a in q):
        return []
    he, ae = hp.get("extraPeriods") or {}, ap.get("extraPeriods") or {}
    for k in sorted(set(he) | set(ae), key=int):
        q.append((he.get(k, 0), ae.get(k, 0)))
    return q


def update(con, seasons: list[int], refresh: bool = False, log=print, workers: int = 6,
           comp: str = "E", league: str = LEAGUE, prefix: str = "EL") -> None:
    for year in seasons:
        r = http_get(f"{API.format(year=year, comp=comp)}/games", params={"limit": 1000}, ok_404=True)
        games = r.json().get("data", []) if r is not None else []
        if not games:
            log(f"[{league}] {season_label(year)}: sin calendario publicado")
            continue
        todo, n_played = [], 0
        for g in games:
            code = g["gameCode"]
            mid = f"{prefix}-{year}-{code}"
            hs, as_ = as_int(g["local"].get("score")), as_int(g["road"].get("score"))
            played = bool(g.get("played"))
            quarters = _quarters(g) if played else []
            date = (g.get("utcDate") or g.get("date") or "")
            past = date[:10] and date[:10] < __import__("datetime").date.today().isoformat()
            refs = [g.get(f"referee{i}") for i in range(1, 5)]
            upsert_match(con, {
                "match_id": mid, "league": league, "sport": "baloncesto",
                "season": season_label(year), "season_start": year, "source_id": str(code),
                "date": date[:10] or None, "time": date[11:16] or None, "time_ref": "UTC",
                "phase": PHASES.get(g["phaseType"]["code"], g["phaseType"].get("name")),
                "round": str(g.get("round")),
                "home_team": g["local"]["club"]["name"], "away_team": g["road"]["club"]["name"],
                "home_code": g["local"]["club"]["code"], "away_code": g["road"]["club"]["code"],
                "home_score": hs if played else None, "away_score": as_ if played else None,
                "result": result_of(hs, as_) if played else None,
                # no jugado y con fecha pasada = cancelado (p. ej. clubes rusos en 2021-22)
                "status": "played" if played else ("not_played" if past else "scheduled"),
                "overtimes": max(0, len(quarters) - 4),
                "venue": (g.get("venue") or {}).get("name"),
                "attendance": as_int(g.get("audience")) or None,
                "referees": "; ".join(x["name"] for x in refs if x) or None,
            })
            if quarters:
                set_periods(con, mid, quarters)
            if played:
                n_played += 1
                done = con.execute("SELECT stats_loaded FROM matches WHERE match_id=?",
                                   (mid,)).fetchone()[0]
                if refresh or not done:
                    todo.append((mid, code, g["local"]["club"]["name"], g["road"]["club"]["name"]))
        con.commit()
        errors = fetched = 0
        with ThreadPoolExecutor(workers) as ex:
            results = ex.map(lambda t: _safe(year, t, comp), todo)
            for (mid, code, home, away), st in zip(todo, results):
                if not st:
                    errors += 1
                    continue
                names = {1: home, 0: away}
                replace_rows(con, "basket_team_stats", mid, [
                    {"match_id": mid, "team": names[t["is_home"]], "dunks": None} | t
                    for t in st["teams"]])
                replace_rows(con, "basket_player_stats", mid, [
                    {"match_id": mid, "team": names[p["is_home"]], "dunks": None} | p
                    for p in st["players"]])
                con.execute("UPDATE matches SET stats_loaded=1, home_coach=?, away_coach=? "
                            "WHERE match_id=?", (*st["coaches"], mid))
                fetched += 1
                if fetched % 100 == 0:
                    con.commit()
        log_run(con, league, season_label(year), len(games), n_played, fetched, errors)
        con.commit()
        log(f"[{league}] {season_label(year)}: {len(games)} partidos ({n_played} jugados), "
            f"estadísticas nuevas: {fetched}, errores: {errors}")


def _safe(year, t, comp="E"):
    try:
        return fetch_stats(year, t[1], comp)
    except Exception:
        return None
