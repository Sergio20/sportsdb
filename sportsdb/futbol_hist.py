"""Histórico largo de fútbol desde football-data.co.uk para los estudios de directo (tabla `futbol_hist`).

Solo lo que hace falta para «qué pasa cuando el favorito va perdiendo al descanso»: resultado final, resultado al
descanso y la probabilidad de cada equipo antes del partido según las cuotas (sin el margen de la casa). Una fila por
partido; LaLiga reciente sigue en `matches` con todo el detalle (laliga.py). Las temporadas pasadas no cambian: solo se
descargan las que faltan, y se repasan la actual y la anterior.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

from .common import as_float, as_int, current_season_start, http_get

URL = "https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"
DIVS = {"SP1": "LaLiga", "E0": "Premier League", "D1": "Bundesliga", "I1": "Serie A", "F1": "Ligue 1"}
FIRST = 1995            # desde 1995-96 todos traen el resultado al descanso
# Cuotas 1X2 antes del partido, de la mejor fuente disponible a la peor (el nombre de columna cambia con los años)
ODDS = [("PS", "Pinnacle"), ("Avg", "media"), ("BbAv", "media"), ("B365", "Bet365"), ("WH", "William Hill"),
        ("IW", "Interwetten"), ("BW", "bwin"), ("LB", "Ladbrokes"), ("GB", "Gamebookers")]

SCHEMA = """
CREATE TABLE IF NOT EXISTS futbol_hist (
    match_id TEXT PRIMARY KEY, league TEXT, season_start INTEGER, date TEXT, home TEXT, away TEXT,
    fthg INTEGER, ftag INTEGER, hthg INTEGER, htag INTEGER,
    p_h REAL, p_d REAL, p_a REAL, odds_src TEXT
);
CREATE INDEX IF NOT EXISTS futbol_hist_league ON futbol_hist (league, season_start);
CREATE TABLE IF NOT EXISTS futbol_prox (
    league TEXT, date TEXT, time_uk TEXT, home TEXT, away TEXT, p_h REAL, p_d REAL, p_a REAL, odds_src TEXT,
    PRIMARY KEY (league, date, home, away)
);
"""
FIXTURES = "https://www.football-data.co.uk/fixtures.csv"     # próximos partidos con sus cuotas (se renueva cada semana)


def season_code(start: int) -> str:
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def parse_date(s: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def probs(row):
    """Probabilidad de 1, X y 2 según la primera casa con las tres cuotas, quitando su margen."""
    for col, name in ODDS:
        h, d, a = (as_float(row.get(col + s)) for s in ("H", "D", "A"))
        if h and d and a and min(h, d, a) > 1:
            s = 1 / h + 1 / d + 1 / a
            return round(1 / h / s, 4), round(1 / d / s, 4), round(1 / a / s, 4), name
    return None, None, None, None


def parse(text: str, div: str, start: int):
    """Filas de la tabla a partir del CSV de una temporada (ignora las incompletas)."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        home, away = row.get("HomeTeam") or row.get("HT"), row.get("AwayTeam") or row.get("AT")
        date = parse_date(row.get("Date") or "")
        fthg, ftag = as_int(row.get("FTHG") or row.get("HG")), as_int(row.get("FTAG") or row.get("AG"))
        if not home or not away or not date or fthg is None or ftag is None:
            continue
        ph, pd_, pa, src = probs(row)
        out.append((f"{div}-{start}-{home}-{away}".replace(" ", "_"), DIVS[div], start, date, home, away, fthg, ftag,
                    as_int(row.get("HTHG")), as_int(row.get("HTAG")), ph, pd_, pa, src))
    return out


def update(con, seasons=None, refresh: bool = False, log=print) -> None:
    con.executescript(SCHEMA)
    cur = current_season_start()
    have = {(lg, s) for lg, s in con.execute("SELECT league, season_start FROM futbol_hist GROUP BY 1, 2")}
    for div, league in DIVS.items():
        for start in range(FIRST, cur + 1):
            if (league, start) in have and start < cur - 1 and not refresh:
                continue
            r = http_get(URL.format(code=season_code(start), div=div), ok_404=True)
            if r is None:
                log(f"[fútbol] {league} {start}: no publicado")
                continue
            try:
                text = r.content.decode("utf-8-sig")
            except UnicodeDecodeError:          # los ficheros antiguos van en latin-1
                text = r.content.decode("latin-1")
            rows = parse(text, div, start)
            con.execute("DELETE FROM futbol_hist WHERE league = ? AND season_start = ?", (league, start))
            con.executemany("INSERT OR REPLACE INTO futbol_hist VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
            log(f"[fútbol] {league} {start}-{(start + 1) % 100:02d}: {len(rows)} partidos, "
                f"{sum(r[8] is not None for r in rows)} con descanso, {sum(r[10] is not None for r in rows)} con cuotas")
    try:        # próximos partidos con cuotas: el vigilante sabe así quién es favorito antes de empezar
        r = http_get(FIXTURES, ok_404=True)
        rows = []
        for row in csv.DictReader(io.StringIO(r.content.decode("utf-8-sig", "replace"))) if r is not None else []:
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
            div, date = row.get("Div"), parse_date(row.get("Date") or "")
            if div not in DIVS or not date or not row.get("HomeTeam"):
                continue
            ph, pd_, pa, src = probs(row)
            rows.append((DIVS[div], date, row.get("Time") or None, row["HomeTeam"], row["AwayTeam"], ph, pd_, pa, src))
        if rows:
            con.execute("DELETE FROM futbol_prox")
            con.executemany("INSERT OR REPLACE INTO futbol_prox VALUES (?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
        log(f"[fútbol] próximos partidos con cuotas: {len(rows)}")
    except Exception as e:      # sin próximos partidos el histórico sigue valiendo
        log(f"[fútbol] no se han podido leer los próximos partidos: {e}")
