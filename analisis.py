#!/usr/bin/env python3
"""Motor de los informes «Jornada» (pre-partido) y «¿Funciona?» (comprobación del modelo).

Recorre el histórico en orden de fecha calculando, para cada partido, lo que se sabía ANTES de
jugarlo (nivel y total habitual de cada equipo con sus últimos 30 partidos). Con eso:
  - ajusta la previsión pre-partido de cada liga (diferencia y total de puntos),
  - comprueba el modelo del panel en vivo al final de cada cuarto (constantes K y KT),
  - prepara la previsión de los próximos partidos.

    python analisis.py [--db data/deportes.db]      # imprime un resumen
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
import remontadas  # noqa: E402
from export_panel import ACB_TO_EL  # noqa: E402
from sportsdb.common import DEFAULT_DB  # noqa: E402

LEAGUES = ("Euroliga", "EuroCup", "Liga Endesa")
LAST_N, MIN_GAMES = 30, 5
EUROCUP_GAP = 9.0                                   # ver export_live.py
K = dict(home=0.10, q=0.021, m=-0.010, sd=2.1)      # modelo del margen en vivo (panel/en_vivo_plantilla.html)
KT = dict(a=1.02, c=0.08, sd=2.48)                  # modelo del total en vivo: del exceso de ritmo solo se mantiene c
PACE_EXTRA, LEVEL_GAP, FAV = 20, 3, 5               # umbrales de los avisos del panel en vivo
DAYS_AHEAD = 6


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def fam(league):
    return "ACB" if league == "Liga Endesa" else "EU"


def club(league, code):
    """Identidad del club común a todas las competiciones (para el descanso entre partidos)."""
    return ACB_TO_EL.get(str(code), f"acb:{code}") if league == "Liga Endesa" else code


def walk(con):
    """Partidos jugados con lo que se sabía antes de cada uno: Q (diferencia de nivel) y total habitual."""
    games, halves = remontadas.load(con)
    hist = defaultdict(lambda: deque(maxlen=LAST_N))        # (familia, código) -> (diferencia ajustada, total, puntos a favor)
    for g in games:
        kh, ka = (fam(g["league"]), g["hc"]), (fam(g["league"]), g["ac"])
        h, a = hist[kh], hist[ka]
        g["reg"] = sum(g["q"][p][0] + g["q"][p][1] for p in (1, 2, 3, 4))
        if len(h) >= MIN_GAMES and len(a) >= MIN_GAMES:
            mean = lambda xs, i: sum(x[i] for x in xs) / len(xs)  # noqa: E731
            g["Q"] = mean(h, 0) - mean(a, 0)
            g["usual"] = (mean(h, 1) + mean(a, 1)) / 2
            g["pf_h"], g["pf_a"] = mean(h, 2), mean(a, 2)
        else:
            g["Q"] = None
        adj = EUROCUP_GAP if g["league"] == "EuroCup" else 0
        m = g["hs"] - g["as_"]
        h.append((m - adj, g["reg"], g["hs"]))
        a.append((-m - adj, g["reg"], g["as_"]))
    return games, halves, hist


def fit_pre(games):
    """Por liga: diferencia = h + b·Q (mínimos cuadrados) y total = habitual + sesgo, con su error típico."""
    out = {}
    for lg in LEAGUES:
        xs = [(g["Q"], g["hs"] - g["as_"], g["reg"] - g["usual"]) for g in games if g["league"] == lg and g["Q"] is not None]
        n = len(xs)
        mx, my = sum(x[0] for x in xs) / n, sum(x[1] for x in xs) / n
        b = sum((x[0] - mx) * (x[1] - my) for x in xs) / sum((x[0] - mx) ** 2 for x in xs)
        h = my - b * mx
        sd = math.sqrt(sum((x[1] - h - b * x[0]) ** 2 for x in xs) / n)
        bias = sum(x[2] for x in xs) / n
        sdt = math.sqrt(sum((x[2] - bias) ** 2 for x in xs) / n)
        out[lg] = dict(n=n, h=round(h, 2), b=round(b, 3), sd=round(sd, 1), bias=round(bias, 1), sdt=round(sdt, 1))
    return out


def bins(pairs, edges=(50, 60, 70, 80, 90, 100.01)):
    """pairs = [(probabilidad prevista en %, acierto 0/1)] -> filas [desde, hasta, casos, previsto medio, real]."""
    rows = []
    for lo, hi in zip(edges, edges[1:]):
        sub = [p for p in pairs if lo <= p[0] < hi]
        if sub:
            rows.append([lo, min(100, round(hi)), len(sub), round(sum(p[0] for p in sub) / len(sub), 1),
                         round(100 * sum(p[1] for p in sub) / len(sub), 1)])
    return rows


def check(games, halves, pre):
    """Comprobaciones del modelo con el histórico. Devuelve un diccionario listo para la página."""
    out = {}
    # 1) Pre-partido: probabilidad de victoria del favorito previsto frente a lo que pasó
    pairs, err_m, err_t = [], [], []
    for g in games:
        if g["Q"] is None:
            continue
        f = pre[g["league"]]
        m = f["h"] + f["b"] * g["Q"]
        p = phi(m / f["sd"])
        real = g["hs"] - g["as_"]
        pairs.append((100 * max(p, 1 - p), int((real > 0) == (m > 0))))
        err_m.append(abs(real - m))
        err_t.append(abs(g["reg"] - g["usual"] - f["bias"]))
    out["pre"] = dict(n=len(pairs), bins=bins(pairs), mae_margin=round(sum(err_m) / len(err_m), 1), mae_total=round(sum(err_t) / len(err_t), 1))

    # 2) En vivo, al final de cada cuarto
    live = {}
    for p, name in ((1, "Final del 1.er cuarto"), (2, "Descanso"), (3, "Final del 3.er cuarto")):
        win, hcap, zs, tz, e_model, e_pace = [], [], [], [], [], []
        for g in games:
            if g["Q"] is None:
                continue
            ch = sum(g["q"][i][0] for i in range(1, p + 1))
            ca = sum(g["q"][i][1] for i in range(1, p + 1))
            M, r, el = ch - ca, 40 - 10 * p, 10 * p
            mean = M + r * (K["home"] + K["q"] * g["Q"] + K["m"] * M)
            sd = K["sd"] * math.sqrt(r)
            final = g["hs"] - g["as_"]
            ph = phi(mean / sd)
            win.append((100 * max(ph, 1 - ph), int((final > 0) == (mean > 0))))
            zs.append((final - mean) / sd)
            if M != 0:      # apuesta tipo: el que va perdiendo, con hándicap igual a su desventaja
                s = -1 if M > 0 else 1
                line = abs(M) + 0.5
                hcap.append((100 * phi((s * mean + line) / sd), int(s * final + line > 0)))
            cur = ch + ca
            tmean = cur + r * (KT["a"] * g["usual"] / 40 + KT["c"] * (cur / el - g["usual"] / 40))
            tz.append((g["reg"] - tmean) / (KT["sd"] * math.sqrt(r)))
            e_model.append(abs(g["reg"] - tmean))
            e_pace.append(abs(g["reg"] - cur * 40 / el))
        n = len(zs)
        live[p] = dict(name=name, n=n, win=bins(win), hcap=bins(hcap, (0, 40, 50, 60, 70, 80, 90, 100.01)),
                       over=round(100 * sum(z > 0 for z in zs) / n, 1), within=round(100 * sum(abs(z) < 1 for z in zs) / n, 1),
                       bias=round(sum(zs) / n * K["sd"] * math.sqrt(40 - 10 * p), 2),
                       t_over=round(100 * sum(z > 0 for z in tz) / n, 1), t_within=round(100 * sum(abs(z) < 1 for z in tz) / n, 1),
                       t_mae_model=round(sum(e_model) / n, 1), t_mae_pace=round(sum(e_pace) / n, 1))
    out["live"] = live

    # 3) Los dos avisos, reproducidos al final de cada cuarto
    alerts = []
    for p, name in ((1, "Final del 1.er cuarto"), (2, "Descanso"), (3, "Final del 3.er cuarto")):
        rit, desf = [], []
        for g in games:
            if g["Q"] is None:
                continue
            ch = sum(g["q"][i][0] for i in range(1, p + 1))
            ca = sum(g["q"][i][1] for i in range(1, p + 1))
            M, r = ch - ca, 40 - 10 * p
            if M == 0:
                continue
            pre_m = 40 * (K["home"] + K["q"] * g["Q"])
            mean = M + r * (K["home"] + K["q"] * g["Q"] + K["m"] * M)
            sd = K["sd"] * math.sqrt(r)
            s = -1 if M > 0 else 1                      # lado del que va perdiendo
            final, line = s * (g["hs"] - g["as_"]), abs(M) + 0.5
            fair = round(-s * mean * 2) / 2             # hándicap justo del que pierde
            row = (100 * phi((s * mean + line) / sd), int(final + line > 0), int(final + fair > 0), final + abs(M))
            lead_pf = g["pf_h"] if M > 0 else g["pf_a"]
            if abs(M) >= 10 and (ch if M > 0 else ca) * 4 / p - lead_pf >= PACE_EXTRA and s * pre_m >= -LEVEL_GAP:
                rit.append(row)
            if p == 2 and s * pre_m >= FAV and g["id"] in halves and len(halves[g["id"]]) == 2:
                hf, hd = halves[g["id"]][1 if s > 0 else 0], halves[g["id"]][0 if s > 0 else 1]
                ef, ed = remontadas.efg(hf), remontadas.efg(hd)
                if ef is not None and ed is not None and ed - ef >= 15:
                    desf.append(row)
        for kind, rows in (("Ritmo insostenible", rit), ("Desfase (favorito con acierto muy inferior)", desf)):
            if len(rows) >= 10:
                n = len(rows)
                real = 100 * sum(x[1] for x in rows) / n
                alerts.append(dict(kind=kind, when=name, n=n, pred=round(sum(x[0] for x in rows) / n, 1), real=round(real, 1),
                                   fair=round(100 * sum(x[2] for x in rows) / n, 1), rec=round(sum(x[3] for x in rows) / n, 1),
                                   odds=round(100 / real, 2) if real else None))
    out["alerts"] = alerts
    return out


def upcoming(con, hist, pre, today=None):
    """Próximos partidos con su previsión y el contexto de descanso de cada equipo."""
    today = today or dt.date.today()
    last = today + dt.timedelta(days=DAYS_AHEAD)
    # calendario completo de baloncesto por club, para descanso y carga de partidos
    cal = defaultdict(list)
    for lg, date, hc, ac, ht, at, status in con.execute(
            "SELECT league, date, home_code, away_code, home_team, away_team, status FROM matches "
            "WHERE sport = 'baloncesto' AND status <> 'not_played' AND date IS NOT NULL ORDER BY date"):
        cal[club(lg, hc)].append((date, lg, at, True, status))
        cal[club(lg, ac)].append((date, lg, ht, False, status))
    el_seasons = defaultdict(set)
    for code, season in con.execute("SELECT home_code, season_start FROM matches WHERE league = 'Euroliga' AND status = 'played' "
                                    "UNION SELECT away_code, season_start FROM matches WHERE league = 'Euroliga' AND status = 'played'"):
        el_seasons[code].add(season)
    cur_season = max((s for ss in el_seasons.values() for s in ss), default=0)

    def side(lg, code, date):
        d = dt.date.fromisoformat(date)
        prev = [x for x in cal[club(lg, code)] if x[0] < date]
        played = [x for x in prev if x[4] == "played"]
        lastg = played[-1] if played else None
        week = sum(1 for x in prev if (d - dt.date.fromisoformat(x[0])).days <= 7)
        h = hist[(fam(lg), code)]
        return dict(rest=(d - dt.date.fromisoformat(lastg[0])).days if lastg else None,
                    last=dict(comp=lastg[1], vs=lastg[2], home=lastg[3]) if lastg else None, week=week, n=len(h),
                    net=round(sum(x[0] for x in h) / len(h), 1) if h else None,
                    new=lg == "Euroliga" and cur_season in el_seasons[code] and (cur_season - 1) not in el_seasons[code])

    out = []
    for lg, date, time, hc, ac, ht, at, rnd in con.execute(
            "SELECT league, date, time, home_code, away_code, home_team, away_team, round FROM matches "
            "WHERE league IN (?,?,?) AND status = 'scheduled' AND date BETWEEN ? AND ? ORDER BY date, time", (*LEAGUES, today.isoformat(), last.isoformat())):
        h, a, f = hist[(fam(lg), hc)], hist[(fam(lg), ac)], pre[lg]
        row = dict(league=lg, date=date, time=time, round=rnd, home=ht, away=at, H=side(lg, hc, date), A=side(lg, ac, date))
        if len(h) >= MIN_GAMES and len(a) >= MIN_GAMES:
            mean = lambda xs, i: sum(x[i] for x in xs) / len(xs)  # noqa: E731
            m = f["h"] + f["b"] * (mean(h, 0) - mean(a, 0))
            row.update(margin=round(m, 1), p=round(100 * phi(m / f["sd"])), total=round((mean(h, 1) + mean(a, 1)) / 2 + f["bias"], 1),
                       sd=f["sd"], sdt=f["sdt"])
        out.append(row)
    return out


def timing(con, games):
    """Entrada y salida con el marcador jugada a jugada (tabla basket_timeline, Euroliga y EuroCup).

    Para cada partido, la PRIMERA vez que un equipo alcanza una ventaja de L puntos (10, 15, 20) antes del
    minuto 30 ante un rival de su nivel o mejor: qué pasa después con esa ventaja.
    """
    try:
        tl = {m: json.loads(e) for m, e in con.execute("SELECT match_id, events FROM basket_timeline WHERE events <> '[]'")}
    except sqlite3.OperationalError:
        return None
    by_id = {g["id"]: g for g in games}
    out = dict(n=len(tl), levels=[], peak=None)
    peaks = []
    for L in (10, 15, 20):
        rows = []
        for mid, ev in tl.items():
            g = by_id.get(mid)
            if not g or g["Q"] is None:
                continue
            reg = [e for e in ev if e[0] <= 40]
            hit = next((i for i, e in enumerate(reg) if abs(e[1] - e[2]) >= L and e[0] <= 30), None)
            if hit is None:
                continue
            t0, a0, b0 = reg[hit]
            s = 1 if a0 > b0 else -1                               # 1 = gana el local
            if -s * 40 * (K["home"] + K["q"] * g["Q"]) < -LEVEL_GAP:   # el que pierde es claramente peor: fuera
                continue
            after = [(e[0], s * (e[1] - e[2])) for e in reg[hit:]]
            final = after[-1][1]
            low_t, low = min(after, key=lambda x: x[1])            # momento en que la ventaja fue menor
            top_t, top = max(after, key=lambda x: x[1])            # ventaja máxima que llegó a tener después
            half_t = next((t for t, d in after if d <= L / 2), None)   # primera vez que se queda en la mitad
            rows.append(dict(t0=t0, final=final, low=low, low_t=low_t, top=top, top_t=top_t, half_t=half_t))
            if L == 15:
                peaks.append(top_t)
        n = len(rows)
        if n < 30:
            continue
        halved = [r for r in rows if r["half_t"] is not None]
        waits = sorted(r["half_t"] - r["t0"] for r in halved)
        tops = sorted(r["top"] for r in rows)
        out["levels"].append(dict(
            L=L, n=n, t0=round(sorted(r["t0"] for r in rows)[n // 2], 1),
            cover=round(100 * sum(r["final"] < L for r in rows) / n, 1),          # el que pierde cubre +L,5 (aprox.)
            win=round(100 * sum(r["final"] < 0 for r in rows) / n, 1),
            final=round(sum(r["final"] for r in rows) / n, 1),
            halved=round(100 * len(halved) / n, 1), wait=round(waits[len(waits) // 2], 1) if waits else None,
            worse=round(100 * sum(r["top"] >= L + 5 for r in rows) / n, 1),      # la ventaja aún creció 5+ puntos
            top=tops[n // 2], low=round(sum(r["low"] for r in rows) / n, 1)))
    if len(peaks) >= 30:
        peaks.sort()
        out["peak"] = dict(n=len(peaks), q1=round(peaks[len(peaks) // 4], 1), med=round(peaks[len(peaks) // 2], 1), q3=round(peaks[3 * len(peaks) // 4], 1))
    return out


def build(con, today=None):
    games, halves, hist = walk(con)
    pre = fit_pre(games)
    return dict(updated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), pre=pre,
                check=check(games, halves, pre), games=upcoming(con, hist, pre, today), timing=timing(con, games),
                consts=dict(K=K, KT=KT, eurocup_gap=EUROCUP_GAP))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args()
    d = build(sqlite3.connect(a.db))
    print(json.dumps({k: d[k] for k in ("pre", "check", "timing")}, ensure_ascii=False, indent=1))
    print(len(d["games"]), "partidos próximos; ejemplo:", json.dumps(d["games"][:2], ensure_ascii=False))
