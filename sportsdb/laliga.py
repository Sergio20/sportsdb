"""LaLiga desde football-data.co.uk (un CSV por temporada: resultado, descanso, estadísticas, cuotas)."""
from __future__ import annotations

import csv
import datetime as dt
import io

from .common import (as_float, as_int, http_get, log_run, replace_rows, result_of,
                     season_label, set_periods, upsert_match)

LEAGUE = "LaLiga"
URL = "https://www.football-data.co.uk/mmz4281/{code}/SP1.csv"

# código en el CSV -> (código en la BD, nombre). P y PS son ambos Pinnacle.
BOOKMAKERS = {
    "B365": ("B365", "Bet365"), "BW": ("BW", "Bet&Win (bwin)"), "IW": ("IW", "Interwetten"),
    "PS": ("PIN", "Pinnacle"), "P": ("PIN", "Pinnacle"), "WH": ("WH", "William Hill"),
    "VC": ("VC", "VC Bet (BetVictor)"), "BV": ("BV", "BetVictor"), "BFD": ("BFD", "Betfred"),
    "BMGM": ("BMGM", "BetMGM"), "CL": ("CL", "Coral"), "LB": ("LB", "Ladbrokes"),
    "BF": ("BF", "Betfair Sportsbook"), "BFE": ("BFE", "Betfair Exchange"),
    "1XB": ("1XB", "1xBet"), "PP": ("PP", "Paddy Power"), "SKB": ("SKB", "Sky Bet"),
    "Max": ("MAX", "Máxima del mercado"), "Avg": ("AVG", "Media del mercado"),
}
_BK_SORTED = sorted(BOOKMAKERS, key=len, reverse=True)
# sufijo de columna -> (mercado, selección, etapa)
SUFFIX = {
    "H": ("1X2", "H", "pre"), "D": ("1X2", "D", "pre"), "A": ("1X2", "A", "pre"),
    "CH": ("1X2", "H", "closing"), "CD": ("1X2", "D", "closing"), "CA": ("1X2", "A", "closing"),
    ">2.5": ("OU", "over", "pre"), "<2.5": ("OU", "under", "pre"),
    "C>2.5": ("OU", "over", "closing"), "C<2.5": ("OU", "under", "closing"),
    "AHH": ("AH", "home", "pre"), "AHA": ("AH", "away", "pre"),
    "CAHH": ("AH", "home", "closing"), "CAHA": ("AH", "away", "closing"),
}
BASE_COLS = {"Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "HTHG",
             "HTAG", "HTR", "HxG", "AxG", "HS", "AS", "HST", "AST", "HF", "AF", "HC", "AC",
             "HY", "AY", "HR", "AR", "Referee", "Attendance", "AHh", "AHCh"}


def parse_odds_column(col: str):
    """'B365CH' -> ('B365', '1X2', 'H', 'closing'). None si no es una columna de cuotas."""
    for bk in _BK_SORTED:
        if col.startswith(bk) and col[len(bk):] in SUFFIX:
            return (BOOKMAKERS[bk][0],) + SUFFIX[col[len(bk):]]
    return None


def season_code(start: int) -> str:
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def parse_date(s: str) -> str:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Fecha no reconocida: {s!r}")


def update(con, seasons: list[int], refresh: bool = False, log=print) -> None:
    con.executemany("INSERT OR REPLACE INTO bookmakers VALUES (?, ?)",
                    sorted(set(BOOKMAKERS.values())))
    for start in seasons:
        r = http_get(URL.format(code=season_code(start)), ok_404=True)
        if r is None:
            log(f"[LaLiga] {season_label(start)}: aún no publicado en football-data")
            continue
        rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig", "replace"))))
        header = list(rows[0].keys()) if rows else []
        colmap = {c: parse_odds_column(c) for c in header if c and c not in BASE_COLS}
        unknown = [c for c, v in colmap.items() if v is None]
        if unknown:
            log(f"[LaLiga] {season_label(start)}: columnas no reconocidas (ignoradas): {unknown}")
        n = n_odds = 0
        for row in rows:
            home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
            if not home or not away or not row.get("Date"):
                continue
            hg, ag = as_int(row.get("FTHG")), as_int(row.get("FTAG"))
            mid = f"LL-{start}-{home}-{away}".replace(" ", "_")
            upsert_match(con, {
                "match_id": mid, "league": LEAGUE, "sport": "futbol",
                "season": season_label(start), "season_start": start, "source_id": mid,
                "date": parse_date(row["Date"]), "time": (row.get("Time") or "").strip() or None,
                "time_ref": "UK", "phase": "Liga", "round": None,
                "home_team": home, "away_team": away, "home_code": home, "away_code": away,
                "home_score": hg, "away_score": ag, "result": result_of(hg, ag),
                "status": "played" if hg is not None else "scheduled",
                "referees": (row.get("Referee") or "").strip() or None,
                "attendance": as_int(row.get("Attendance")),
            })
            hth, hta = as_int(row.get("HTHG")), as_int(row.get("HTAG"))
            if hg is not None and hth is not None and hta is not None:
                set_periods(con, mid, [(hth, hta), (hg - hth, ag - hta)], football=True)
            stats = []
            for is_home, team, p in ((1, home, "H"), (0, away, "A")):
                stats.append({
                    "match_id": mid, "is_home": is_home, "team": team,
                    "goals": hg if is_home else ag, "ht_goals": hth if is_home else hta,
                    "xg": as_float(row.get(p + "xG")), "shots": as_int(row.get(p + "S")),
                    "shots_on_target": as_int(row.get(p + "ST")), "fouls": as_int(row.get(p + "F")),
                    "corners": as_int(row.get(p + "C")), "yellow_cards": as_int(row.get(p + "Y")),
                    "red_cards": as_int(row.get(p + "R")),
                })
            replace_rows(con, "football_team_stats", mid, stats)
            ah = {"pre": as_float(row.get("AHh")), "closing": as_float(row.get("AHCh"))}
            odds = {}
            for col, spec in colmap.items():
                price = as_float(row.get(col))
                if spec is None or price is None or price <= 1:
                    continue
                bk, market, sel, stage = spec
                line = 2.5 if market == "OU" else ah[stage] if market == "AH" else None
                odds[(bk, market, sel, stage)] = {
                    "match_id": mid, "bookmaker": bk, "market": market, "selection": sel,
                    "stage": stage, "line": line, "price": price}
            replace_rows(con, "odds", mid, list(odds.values()))
            con.execute("UPDATE matches SET stats_loaded = ? WHERE match_id = ?",
                        (1 if hg is not None else 0, mid))
            n += 1
            n_odds += len(odds)
        log_run(con, LEAGUE, season_label(start), n, n, n, 0, f"{n_odds} cuotas")
        con.commit()
        log(f"[LaLiga] {season_label(start)}: {n} partidos, {n_odds} cuotas")
