"""Utilidades comunes: esquema SQLite, conexión, HTTP con reintentos y temporadas."""
from __future__ import annotations

import datetime as dt
import sqlite3
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "deportes.db"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS matches (
    match_id      TEXT PRIMARY KEY,     -- LL-2021-Valencia-Getafe | EL-2025-406 | ACB-104190
    league        TEXT NOT NULL,        -- LaLiga | Euroliga | Liga Endesa
    sport         TEXT NOT NULL,        -- futbol | baloncesto
    season        TEXT NOT NULL,        -- 2021-22
    season_start  INTEGER NOT NULL,     -- 2021
    source_id     TEXT,                 -- id del partido en la fuente
    date          TEXT,                 -- YYYY-MM-DD
    time          TEXT,                 -- HH:MM
    time_ref      TEXT,                 -- UK (football-data) | UTC (baloncesto)
    phase         TEXT,                 -- Liga Regular, Playoff, Final Four...
    round         TEXT,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    home_code     TEXT,                 -- código/id estable del club en la fuente
    away_code     TEXT,
    home_score    INTEGER,
    away_score    INTEGER,
    result        TEXT,                 -- H | D | A
    status        TEXT NOT NULL,        -- played | scheduled | not_played
    overtimes     INTEGER DEFAULT 0,    -- nº de prórrogas (baloncesto)
    venue         TEXT,
    attendance    INTEGER,
    referees      TEXT,
    home_coach    TEXT,
    away_coach    TEXT,
    stats_loaded  INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_matches_league_season ON matches(league, season_start, date);

-- Parciales: fútbol 1T/2T; baloncesto Q1..Q4 y OT1..OTn (puntos del periodo, no acumulados)
CREATE TABLE IF NOT EXISTS periods (
    match_id   TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    period     INTEGER NOT NULL,
    label      TEXT NOT NULL,
    home       INTEGER,
    away       INTEGER,
    PRIMARY KEY (match_id, period)
);

CREATE TABLE IF NOT EXISTS football_team_stats (
    match_id        TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    is_home         INTEGER NOT NULL,
    team            TEXT NOT NULL,
    goals           INTEGER,
    ht_goals        INTEGER,
    xg              REAL,
    shots           INTEGER,
    shots_on_target INTEGER,
    fouls           INTEGER,
    corners         INTEGER,
    yellow_cards    INTEGER,
    red_cards       INTEGER,
    PRIMARY KEY (match_id, is_home)
);

CREATE TABLE IF NOT EXISTS basket_team_stats (
    match_id   TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    is_home    INTEGER NOT NULL,
    team       TEXT NOT NULL,
    points INTEGER, fg2m INTEGER, fg2a INTEGER, fg3m INTEGER, fg3a INTEGER,
    ftm INTEGER, fta INTEGER, oreb INTEGER, dreb INTEGER, treb INTEGER,
    assists INTEGER, steals INTEGER, turnovers INTEGER,
    blocks INTEGER, blocks_against INTEGER,
    fouls INTEGER, fouls_drawn INTEGER,
    dunks INTEGER,                      -- sólo Liga Endesa
    rating INTEGER,                     -- valoración (PIR)
    PRIMARY KEY (match_id, is_home)
);

CREATE TABLE IF NOT EXISTS basket_player_stats (
    match_id   TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    is_home    INTEGER NOT NULL,
    team       TEXT NOT NULL,
    player_id  TEXT NOT NULL,
    player     TEXT,
    starter    INTEGER,
    seconds    INTEGER,
    points INTEGER, fg2m INTEGER, fg2a INTEGER, fg3m INTEGER, fg3a INTEGER,
    ftm INTEGER, fta INTEGER, oreb INTEGER, dreb INTEGER, treb INTEGER,
    assists INTEGER, steals INTEGER, turnovers INTEGER,
    blocks INTEGER, blocks_against INTEGER,
    fouls INTEGER, fouls_drawn INTEGER,
    dunks INTEGER, plus_minus INTEGER, rating INTEGER,
    PRIMARY KEY (match_id, is_home, player_id)
);

-- Cuotas en formato largo (sólo LaLiga: football-data.co.uk)
CREATE TABLE IF NOT EXISTS odds (
    match_id   TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    bookmaker  TEXT NOT NULL,   -- ver tabla bookmakers
    market     TEXT NOT NULL,   -- 1X2 | OU (más/menos goles) | AH (hándicap asiático)
    selection  TEXT NOT NULL,   -- H/D/A | over/under | home/away
    stage      TEXT NOT NULL,   -- pre (pre-partido, días antes) | closing (cierre)
    line       REAL,            -- 2.5 en OU; hándicap del local en AH
    price      REAL NOT NULL,   -- cuota decimal
    PRIMARY KEY (match_id, bookmaker, market, selection, stage)
);

CREATE TABLE IF NOT EXISTS bookmakers (code TEXT PRIMARY KEY, name TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS load_log (
    run_at TEXT, league TEXT, season TEXT, matches INTEGER, played INTEGER,
    stats_fetched INTEGER, errors INTEGER, note TEXT
);

-- Vista ancha de LaLiga: partido + estadísticas + cuotas principales
CREATE VIEW IF NOT EXISTS v_laliga AS
SELECT m.match_id, m.season, m.date, m.time, m.home_team, m.away_team,
       m.home_score, m.away_score, m.result,
       h.ht_goals AS ht_home, a.ht_goals AS ht_away,
       h.xg AS xg_home, a.xg AS xg_away,
       h.shots AS shots_home, a.shots AS shots_away,
       h.shots_on_target AS sot_home, a.shots_on_target AS sot_away,
       h.corners AS corners_home, a.corners AS corners_away,
       h.fouls AS fouls_home, a.fouls AS fouls_away,
       h.yellow_cards AS yellow_home, a.yellow_cards AS yellow_away,
       h.red_cards AS red_home, a.red_cards AS red_away,
       m.referees,
       MAX(CASE WHEN o.bookmaker='B365' AND o.market='1X2' AND o.stage='pre' AND o.selection='H' THEN o.price END) AS b365_h,
       MAX(CASE WHEN o.bookmaker='B365' AND o.market='1X2' AND o.stage='pre' AND o.selection='D' THEN o.price END) AS b365_d,
       MAX(CASE WHEN o.bookmaker='B365' AND o.market='1X2' AND o.stage='pre' AND o.selection='A' THEN o.price END) AS b365_a,
       MAX(CASE WHEN o.bookmaker='PIN' AND o.market='1X2' AND o.stage='closing' AND o.selection='H' THEN o.price END) AS pin_close_h,
       MAX(CASE WHEN o.bookmaker='PIN' AND o.market='1X2' AND o.stage='closing' AND o.selection='D' THEN o.price END) AS pin_close_d,
       MAX(CASE WHEN o.bookmaker='PIN' AND o.market='1X2' AND o.stage='closing' AND o.selection='A' THEN o.price END) AS pin_close_a,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='1X2' AND o.stage='pre' AND o.selection='H' THEN o.price END) AS avg_h,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='1X2' AND o.stage='pre' AND o.selection='D' THEN o.price END) AS avg_d,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='1X2' AND o.stage='pre' AND o.selection='A' THEN o.price END) AS avg_a,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='1X2' AND o.stage='closing' AND o.selection='H' THEN o.price END) AS avg_close_h,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='1X2' AND o.stage='closing' AND o.selection='D' THEN o.price END) AS avg_close_d,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='1X2' AND o.stage='closing' AND o.selection='A' THEN o.price END) AS avg_close_a,
       MAX(CASE WHEN o.bookmaker='MAX' AND o.market='1X2' AND o.stage='closing' AND o.selection='H' THEN o.price END) AS max_close_h,
       MAX(CASE WHEN o.bookmaker='MAX' AND o.market='1X2' AND o.stage='closing' AND o.selection='D' THEN o.price END) AS max_close_d,
       MAX(CASE WHEN o.bookmaker='MAX' AND o.market='1X2' AND o.stage='closing' AND o.selection='A' THEN o.price END) AS max_close_a,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='OU' AND o.stage='closing' AND o.selection='over' THEN o.price END) AS avg_close_over25,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='OU' AND o.stage='closing' AND o.selection='under' THEN o.price END) AS avg_close_under25,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='AH' AND o.stage='closing' AND o.selection='home' THEN o.line END) AS ah_close_line,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='AH' AND o.stage='closing' AND o.selection='home' THEN o.price END) AS avg_close_ah_home,
       MAX(CASE WHEN o.bookmaker='AVG' AND o.market='AH' AND o.stage='closing' AND o.selection='away' THEN o.price END) AS avg_close_ah_away
FROM matches m
LEFT JOIN football_team_stats h ON h.match_id = m.match_id AND h.is_home = 1
LEFT JOIN football_team_stats a ON a.match_id = m.match_id AND a.is_home = 0
LEFT JOIN odds o ON o.match_id = m.match_id
WHERE m.league = 'LaLiga'
GROUP BY m.match_id;

-- Vista ancha de baloncesto: partido + parciales + estadísticas de equipo
CREATE VIEW IF NOT EXISTS v_baloncesto AS
SELECT m.match_id, m.league, m.season, m.date, m.time, m.phase, m.round,
       m.home_team, m.away_team, m.home_score, m.away_score, m.result, m.overtimes,
       q1.home AS q1_home, q1.away AS q1_away, q2.home AS q2_home, q2.away AS q2_away,
       q3.home AS q3_home, q3.away AS q3_away, q4.home AS q4_home, q4.away AS q4_away,
       (SELECT SUM(home) FROM periods p WHERE p.match_id = m.match_id AND p.period > 4) AS ot_home,
       (SELECT SUM(away) FROM periods p WHERE p.match_id = m.match_id AND p.period > 4) AS ot_away,
       h.fg2m AS fg2m_home, h.fg2a AS fg2a_home, h.fg3m AS fg3m_home, h.fg3a AS fg3a_home,
       h.ftm AS ftm_home, h.fta AS fta_home, h.oreb AS oreb_home, h.dreb AS dreb_home,
       h.assists AS ast_home, h.steals AS stl_home, h.turnovers AS tov_home,
       h.blocks AS blk_home, h.fouls AS pf_home, h.rating AS val_home,
       a.fg2m AS fg2m_away, a.fg2a AS fg2a_away, a.fg3m AS fg3m_away, a.fg3a AS fg3a_away,
       a.ftm AS ftm_away, a.fta AS fta_away, a.oreb AS oreb_away, a.dreb AS dreb_away,
       a.assists AS ast_away, a.steals AS stl_away, a.turnovers AS tov_away,
       a.blocks AS blk_away, a.fouls AS pf_away, a.rating AS val_away,
       m.venue, m.attendance, m.referees, m.home_coach, m.away_coach
FROM matches m
LEFT JOIN periods q1 ON q1.match_id = m.match_id AND q1.period = 1
LEFT JOIN periods q2 ON q2.match_id = m.match_id AND q2.period = 2
LEFT JOIN periods q3 ON q3.match_id = m.match_id AND q3.period = 3
LEFT JOIN periods q4 ON q4.match_id = m.match_id AND q4.period = 4
LEFT JOIN basket_team_stats h ON h.match_id = m.match_id AND h.is_home = 1
LEFT JOIN basket_team_stats a ON a.match_id = m.match_id AND a.is_home = 0
WHERE m.sport = 'baloncesto' AND m.status = 'played';
"""

MATCH_COLS = ["match_id", "league", "sport", "season", "season_start", "source_id", "date",
              "time", "time_ref", "phase", "round", "home_team", "away_team", "home_code",
              "away_code", "home_score", "away_score", "result", "status", "overtimes",
              "venue", "attendance", "referees", "home_coach", "away_coach"]

BASKET_STATS = ["points", "fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb",
                "treb", "assists", "steals", "turnovers", "blocks", "blocks_against",
                "fouls", "fouls_drawn", "dunks", "rating"]


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def season_label(start: int) -> str:
    return f"{start}-{str(start + 1)[-2:]}"


def current_season_start(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def result_of(h, a):
    if h is None or a is None:
        return None
    return "H" if h > a else "A" if a > h else "D"


def upsert_match(con: sqlite3.Connection, m: dict) -> None:
    """Inserta o actualiza un partido sin tocar stats_loaded. Los campos None no pisan datos."""
    row = {c: m.get(c) for c in MATCH_COLS}
    row["overtimes"] = row["overtimes"] or 0
    row["updated_at"] = now()
    cols = list(row)
    keep = ("venue", "attendance", "referees", "home_coach", "away_coach")
    sets = ", ".join(
        f"{c}=COALESCE(excluded.{c}, {c})" if c in keep else f"{c}=excluded.{c}"
        for c in cols if c != "match_id")
    con.execute(
        f"INSERT INTO matches ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
        f"ON CONFLICT(match_id) DO UPDATE SET {sets}", row)


def replace_rows(con, table: str, match_id: str, rows: list[dict]) -> None:
    con.execute(f"DELETE FROM {table} WHERE match_id = ?", (match_id,))
    if not rows:
        return
    cols = list(rows[0])
    con.executemany(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
        rows)


def set_periods(con, match_id: str, quarters: list[tuple[int, int]], football=False) -> None:
    rows = []
    for i, (h, a) in enumerate(quarters, 1):
        if football:
            label = f"{i}T"
        else:
            label = f"Q{i}" if i <= 4 else f"OT{i - 4}"
        rows.append({"match_id": match_id, "period": i, "label": label, "home": h, "away": a})
    replace_rows(con, "periods", match_id, rows)


def log_run(con, league, season, matches, played, fetched, errors, note=""):
    con.execute("INSERT INTO load_log VALUES (?,?,?,?,?,?,?,?)",
                (now(), league, season, matches, played, fetched, errors, note))


_session = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})
        adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16)
        _session.mount("https://", adapter)
    return _session


def http_get(url: str, *, params=None, tries: int = 4, timeout: int = 60,
             ok_404: bool = False) -> requests.Response | None:
    """GET con reintentos y espera creciente. Devuelve None si ok_404 y la fuente da 404."""
    last = None
    for i in range(tries):
        try:
            r = session().get(url, params=params, timeout=timeout)
            if r.status_code == 404 and ok_404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except requests.RequestException as e:  # red, timeout o 5xx
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"No se pudo descargar {url}: {last}")


def as_int(v):
    if v is None or v == "":
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def as_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
