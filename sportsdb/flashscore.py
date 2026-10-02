"""Otras competiciones de los clubes de Euroliga (ligas nacionales, copas, Liga ABA).

Fuente: la página pública de resultados de cada club en flashscore.es, que trae incrustados
sus últimos ~40 partidos de todas las competiciones. Sólo resultado y parciales (sin
estadísticas). No hay histórico más antiguo: se va acumulando con cada actualización diaria.

Se omiten Euroliga y EuroCup (vienen de la API oficial) y la Liga Endesa (viene de acb.com).
"""
from __future__ import annotations

import datetime as dt
import re

from .common import as_int, http_get, log_run, result_of, season_label, set_periods, upsert_match

URL = "https://www.flashscore.es/equipo/{slug}/{fsid}/resultados/"

# id de Flashscore -> (slug, código del club en Euroliga)
CLUBS = {
    "ny4KlAOf": ("anadolu-efes", "IST"), "KrXB5aC1": ("asvel-basket", "ASV"),
    "EDQLcAfL": ("barcelona", "BAR"), "8zQPdU9R": ("saski-baskonia", "BAS"),
    "6LstMRPo": ("bayern", "MUN"), "hE4Gkj9l": ("besiktas", "BES"),
    "b7EUA43e": ("dubai", "DUB"), "6uAuZ6Pt": ("crvena-zvezda-meridianbet", "RED"),
    "rDhoZR1l": ("fenerbahce", "ULK"), "x0WjVzjP": ("hapoel-tel-aviv", "HTA"),
    "nLuro05B": ("maccabi-tel-aviv", "TEL"), "YFxc9UAj": ("as-monaco", "MCO"),
    "tUT82gR9": ("olimpia-milano", "MIL"), "ptNXXDf6": ("olympiakos-piraeus", "OLY"),
    "bqRyXX9C": ("panathinaikos", "PAN"), "CdCP6AWf": ("paris", "PRS"),
    "GAiz1YL6": ("partizan", "PAR"), "MP6gLUO7": ("real-madrid", "MAD"),
    "6w8lqYGE": ("valencia-basket", "PAM"), "YwDFycYd": ("virtus-bologna", "VIR"),
    "6JbDYvUs": ("zalgiris-kaunas", "ZAL"), "ncLyc03K": ("alba-berlin", "BER"),
}
SKIP = ("euroliga", "eurocup", "amistosos", "liga endesa", ": acb")

# Ligas nacionales: su página de resultados trae los ~100 partidos más recientes de cada
# temporada (el final de la liga regular y los playoffs). Varias rutas = nombres antiguos.
BASE = "https://www.flashscore.es"
LEAGUE_PATHS = [("/baloncesto/turquia/super-lig",), ("/baloncesto/grecia/basket-league",),
                ("/baloncesto/italia/lega-a",), ("/baloncesto/alemania/bbl",),
                ("/baloncesto/francia/lnb",), ("/baloncesto/israel/super-league",),
                ("/baloncesto/lituania/lkl",), ("/baloncesto/serbia/superliga",),
                ("/baloncesto/europa/admiralbet-aba-league", "/baloncesto/europa/aba-league")]
FEED = re.compile(r"initialFeeds\[[\"\']results[\"\']\]\s*=\s*\{\s*data:\s*`([^`]*)`")


def _code(fsid):
    return CLUBS[fsid][1] if fsid in CLUBS else f"fs:{fsid}"


def parse(html: str) -> list[dict]:
    m = FEED.search(html)
    if not m:
        return []
    out, comp = [], None
    for blk in m.group(1).split("¬~"):
        kv = dict(x.split("÷", 1) for x in blk.split("¬") if "÷" in x)
        if "ZA" in kv:
            comp = kv["ZA"]
        if "AA" not in kv or not comp or any(s in comp.lower() for s in SKIP):
            continue
        hs, as_ = as_int(kv.get("AG")), as_int(kv.get("AH"))
        if kv.get("AB") != "3" or hs is None or as_ is None:
            continue
        country, _, name = comp.partition(": ")
        name, _, phase = name.partition(" - ")
        name = name.replace("AdmiralBet ", "")
        when = dt.datetime.fromtimestamp(int(kv["AD"]), dt.timezone.utc)
        year = when.year if when.month >= 7 else when.year - 1
        q = [(as_int(kv.get(h)), as_int(kv.get(a))) for h, a in (("BA", "BB"), ("BC", "BD"), ("BE", "BF"), ("BG", "BH"))]
        q = q if all(h is not None and a is not None for h, a in q) else []
        ot = 1 if q and sum(h for h, _ in q) != hs else 0
        out.append({"quarters": q, "match": {
            "match_id": f"FS-{kv['AA']}", "league": f"{name.strip()} ({country.strip().title()})",
            "sport": "baloncesto", "season": season_label(year), "season_start": year,
            "source_id": kv["AA"], "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M"),
            "time_ref": "UTC", "phase": phase.strip() or "Liga Regular", "round": kv.get("ER"),
            "home_team": kv.get("AE"), "away_team": kv.get("AF"),
            "home_code": _code(kv.get("PX")), "away_code": _code(kv.get("PY")),
            "home_score": hs, "away_score": as_, "result": result_of(hs, as_),
            "status": "played", "overtimes": ot}})
    return out


def update(con, seasons=None, refresh=False, log=print):
    total = errors = 0
    before = con.execute("SELECT COUNT(*) FROM matches WHERE match_id LIKE 'FS-%'").fetchone()[0]
    for fsid, (slug, code) in CLUBS.items():
        try:
            rows = parse(http_get(URL.format(slug=slug, fsid=fsid), tries=3).text)
        except Exception:
            errors += 1
            continue
        if not rows:
            errors += 1
        for r in rows:
            upsert_match(con, r["match"])
            if r["quarters"]:
                set_periods(con, r["match"]["match_id"], r["quarters"])
            total += 1
        con.commit()
    # Páginas de liga: la temporada en curso siempre; las anteriores sólo si se piden.
    from .common import current_season_start
    cur = current_season_start()
    for paths in LEAGUE_PATHS:
        for year in sorted(set(seasons or [cur])):
            for path in paths:
                url = f"{BASE}{path}/resultados/" if year == cur else f"{BASE}{path}-{year}-{year + 1}/resultados/"
                try:
                    r = http_get(url, tries=3, ok_404=True)
                except Exception:
                    r = None
                rows = parse(r.text) if r is not None else []
                for row in rows:
                    upsert_match(con, row["match"])
                    if row["quarters"]:
                        set_periods(con, row["match"]["match_id"], row["quarters"])
                total += len(rows)
                if rows:
                    break
        con.commit()
    after = con.execute("SELECT COUNT(*) FROM matches WHERE match_id LIKE 'FS-%'").fetchone()[0]
    log_run(con, "Otras competiciones", "", after, after, after - before, errors)
    con.commit()
    log(f"[Otras competiciones] {after} partidos guardados ({after - before} nuevos), "
        f"clubes sin datos: {errors}")
