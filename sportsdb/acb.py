"""Liga Endesa desde acb.com.

La web es una aplicación Next.js: los datos van incrustados en la página como
fragmentos `self.__next_f.push(...)`. Se leen de ahí (no hay API pública).

- Calendario por temporada: www.acb.com/es/liga/calendario?temporada={id}  (id = año - 1935)
- Ficha del partido:        live.acb.com/es/partidos/x-{id}/estadisticas
  (resultado, parciales, pabellón, público, árbitros, estadísticas de equipo y jugador)
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from .common import (as_int, http_get, log_run, replace_rows, result_of, season_label,
                     set_periods, upsert_match)

LEAGUE = "Liga Endesa"
CAL_URL = "https://www.acb.com/es/liga/calendario"
MATCH_URL = "https://live.acb.com/es/partidos/x-{id}/estadisticas"
SUBPHASES = {293: "Playoff - Cuartos", 292: "Playoff - Semifinales", 291: "Playoff - Final"}

STAT_MAP = {"points": "points", "fg2m": "twoPointersMade", "fg2a": "twoPointersAttempted",
            "fg3m": "threePointersMade", "fg3a": "threePointersAttempted",
            "ftm": "freeThrowsMade", "fta": "freeThrowsAttempted", "oreb": "offRebounds",
            "dreb": "defRebounds", "treb": "totalRebounds", "assists": "assists",
            "steals": "steals", "turnovers": "turnovers", "blocks": "blocks",
            "blocks_against": "receivedBlocks", "fouls": "personalFouls",
            "fouls_drawn": "foulsDrawn", "dunks": "dunks", "rating": "rating"}

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_DEC = json.JSONDecoder()


def edition_id(season_start: int) -> int:
    return season_start - 1935          # 2021-22 -> 86, 2025-26 -> 90


def rsc_payload(html: str) -> str:
    return "".join(json.loads(m.group(1)) for m in _PUSH.finditer(html))


def find_props(payload: str, key: str) -> dict | None:
    """Localiza la fila del payload que contiene `key` y devuelve sus props."""
    for line in payload.split("\n"):
        if f'"{key}"' not in line:
            continue
        i = line.find(":")
        try:
            obj, _ = _DEC.raw_decode(line[i + 1:])
        except ValueError:
            continue
        if isinstance(obj, list) and len(obj) > 3 and isinstance(obj[3], dict):
            props = obj[3]
            if key in props:
                return props
            if key in (props.get("data") or {}):
                return props["data"]
    return None


def fetch_calendar(season_start: int) -> list[dict]:
    html = http_get(CAL_URL, params={"temporada": edition_id(season_start)}).text
    data = find_props(rsc_payload(html), "availableFilters")
    if not data or (data.get("selectedFilters") or {}).get("season") != edition_id(season_start):
        raise RuntimeError("acb.com: no se encontró el calendario (¿cambió la web?)")
    teams = data["teams"]

    def team(ref):
        return teams[int(ref.rsplit(":", 1)[1])] if isinstance(ref, str) else ref

    out = []
    for rnd in data["rounds"]:
        sub = rnd.get("subphase")
        if sub:
            phase = SUBPHASES.get(sub["id"], f"Playoff ({sub['id']})")
            rlabel = f"{rnd['roundNumber']} (partido {sub.get('subphaseNumber')})"
        else:
            phase, rlabel = "Liga Regular", str(rnd["roundNumber"])
        for m in rnd["matches"]:
            if m.get("seasonStartYear") not in (None, season_start):
                continue
            out.append({"id": m["id"], "phase": phase, "round": rlabel,
                        "home": team(m["homeTeam"]), "away": team(m["awayTeam"]),
                        "home_score": m.get("homeTeamScore"), "away_score": m.get("awayTeamScore"),
                        "start": m.get("startDateTime"), "status": m.get("matchStatus")})
    return out


def _sec(s):
    try:
        m, sec = str(s).split(":")
        return int(m) * 60 + int(sec)
    except (ValueError, AttributeError):
        return None


def fetch_match(match_id: int) -> dict | None:
    payload = rsc_payload(http_get(MATCH_URL.format(id=match_id), tries=3).text)
    head = (find_props(payload, "initialMatchHeader") or {}).get("initialMatchHeader")
    stats = (find_props(payload, "initialStatistics") or {}).get("initialStatistics")
    if not head:
        return None
    out = {"quarters": [(q["home"], q["away"]) for q in
                        sorted(head.get("quarterScores") or [], key=lambda q: q["quarter"])],
           "home_score": head.get("currentHomeScore"), "away_score": head.get("currentAwayScore"),
           "teams": [], "players": [], "coaches": [None, None]}
    if not stats or not stats.get("teamBoxscores"):
        return out
    out.update(venue=stats.get("arena") or None, attendance=as_int(stats.get("attendance")) or None,
               referees="; ".join(stats.get("referees") or []) or None)
    home_id = head["teams"]["home"]["id"]
    for i, tb in enumerate(stats["teamBoxscores"]):
        is_home = int(tb["team"]["id"] == home_id) if tb.get("team") else int(i == 0)
        out["coaches"][0 if is_home else 1] = tb.get("headCoach")
        total = next((p["stats"] for p in tb["statsByPeriods"] if p["quarter"] == 0), None)
        if not total:
            continue
        out["teams"].append({k: as_int(total["total"].get(v)) for k, v in STAT_MAP.items()}
                            | {"is_home": is_home})
        for p in total.get("players") or []:
            sec = _sec(p.get("playTime"))
            if not p.get("player") or not sec:
                continue
            out["players"].append({k: as_int(p.get(v)) for k, v in STAT_MAP.items()} | {
                "is_home": is_home, "player_id": str(p["player"]["id"]),
                "player": p["player"].get("nickname"), "starter": int(bool(p.get("isStarted"))),
                "seconds": sec, "plus_minus": as_int(p.get("plusMinus"))})
    return out


def _safe(mid):
    try:
        return fetch_match(mid)
    except Exception:
        return None


def update(con, seasons: list[int], refresh: bool = False, log=print, workers: int = 4) -> None:
    for start in seasons:
        try:
            cal = fetch_calendar(start)
        except Exception as e:
            log(f"[Liga Endesa] {season_label(start)}: ERROR en calendario: {e}")
            log_run(con, LEAGUE, season_label(start), 0, 0, 0, 1, str(e))
            con.commit()
            continue
        todo, n_played, names = [], 0, {}
        for m in cal:
            mid = f"ACB-{m['id']}"
            played = m["status"] == "FINALIZED"
            hs, as_ = (m["home_score"], m["away_score"]) if played else (None, None)
            start_dt = m["start"] or ""
            names[mid] = {1: m["home"]["fullName"], 0: m["away"]["fullName"]}
            upsert_match(con, {
                "match_id": mid, "league": LEAGUE, "sport": "baloncesto",
                "season": season_label(start), "season_start": start, "source_id": str(m["id"]),
                "date": start_dt[:10] or None, "time": start_dt[11:16] or None, "time_ref": "UTC",
                "phase": m["phase"], "round": m["round"],
                "home_team": m["home"]["fullName"], "away_team": m["away"]["fullName"],
                "home_code": str(m["home"]["clubId"]), "away_code": str(m["away"]["clubId"]),
                "home_score": hs, "away_score": as_, "result": result_of(hs, as_),
                "status": "played" if played else "scheduled",
            })
            if played:
                n_played += 1
                done = con.execute("SELECT stats_loaded FROM matches WHERE match_id=?",
                                   (mid,)).fetchone()[0]
                if refresh or not done:
                    todo.append((mid, m["id"]))
        con.commit()
        errors = fetched = 0
        with ThreadPoolExecutor(workers) as ex:
            for (mid, _), d in zip(todo, ex.map(lambda t: _safe(t[1]), todo)):
                if not d or len(d["teams"]) != 2:
                    errors += 1
                    if d and d["quarters"]:
                        set_periods(con, mid, d["quarters"])
                    continue
                set_periods(con, mid, d["quarters"])
                replace_rows(con, "basket_team_stats", mid, [
                    {"match_id": mid, "team": names[mid][t["is_home"]]} | t for t in d["teams"]])
                replace_rows(con, "basket_player_stats", mid, [
                    {"match_id": mid, "team": names[mid][p["is_home"]]} | p for p in d["players"]])
                con.execute(
                    "UPDATE matches SET stats_loaded=1, overtimes=?, venue=?, attendance=?, "
                    "referees=?, home_coach=?, away_coach=? WHERE match_id=?",
                    (max(0, len(d["quarters"]) - 4), d.get("venue"), d.get("attendance"),
                     d.get("referees"), d["coaches"][0], d["coaches"][1], mid))
                fetched += 1
                if fetched % 50 == 0:
                    con.commit()
                    log(f"[Liga Endesa] {season_label(start)}: {fetched}/{len(todo)}")
        log_run(con, LEAGUE, season_label(start), len(cal), n_played, fetched, errors)
        con.commit()
        log(f"[Liga Endesa] {season_label(start)}: {len(cal)} partidos ({n_played} jugados), "
            f"estadísticas nuevas: {fetched}, errores: {errors}")
