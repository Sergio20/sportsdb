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
PACE_EXTRA, LEVEL_GAP, FAV, TOTAL_GAP = 20, 3, 5, 18   # umbrales de los avisos del panel en vivo
Z80, Z90, Z95 = 0.8416, 1.2816, 1.6449                          # hándicaps de seguridad: justo + Z veces el margen de error
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
    up = lambda x: math.ceil(x * 2) / 2      # noqa: E731  medio punto hacia arriba: hándicap algo más largo, nunca más corto
    for p, name in ((1, "Final del 1.er cuarto"), (2, "Descanso"), (3, "Final del 3.er cuarto")):
        rit, desf, tot = [], [], []
        for g in games:
            if g["Q"] is None:
                continue
            ch = sum(g["q"][i][0] for i in range(1, p + 1))
            ca = sum(g["q"][i][1] for i in range(1, p + 1))
            M, r, el = ch - ca, 40 - 10 * p, 10 * p
            # Total desfasado: se apuesta a que el ritmo vuelve (menos de si va alto, más de si va bajo)
            cur = ch + ca
            tmean = cur + r * (KT["a"] * g["usual"] / 40 + KT["c"] * (cur / el - g["usual"] / 40))
            tsd, pace = KT["sd"] * math.sqrt(r), cur * 40 / el
            if abs(pace - tmean) >= TOTAL_GAP and p < 3:
                if pace > tmean:
                    hit = lambda line: int(g["reg"] < line)   # noqa: E731
                    tot.append((None, None, hit(round(tmean * 2) / 2), hit(up(tmean + Z80 * tsd)), hit(up(tmean + Z90 * tsd)), tmean - g["reg"], hit(up(tmean + Z95 * tsd))))
                else:
                    hit = lambda line: int(g["reg"] > line)   # noqa: E731
                    dn = lambda x: math.floor(x * 2) / 2      # noqa: E731
                    tot.append((None, None, hit(round(tmean * 2) / 2), hit(dn(tmean - Z80 * tsd)), hit(dn(tmean - Z90 * tsd)), g["reg"] - tmean, hit(dn(tmean - Z95 * tsd))))
            if M == 0:
                continue
            pre_m = 40 * (K["home"] + K["q"] * g["Q"])
            mean = M + r * (K["home"] + K["q"] * g["Q"] + K["m"] * M)
            sd = K["sd"] * math.sqrt(r)
            s = -1 if M > 0 else 1                      # lado del que va perdiendo
            final, line = s * (g["hs"] - g["as_"]), abs(M) + 0.5
            fair = -s * mean                            # hándicap justo del que pierde
            row = (100 * phi((s * mean + line) / sd), int(final + line > 0), int(final + round(fair * 2) / 2 > 0),
                   int(final + up(fair + Z80 * sd) > 0), int(final + up(fair + Z90 * sd) > 0), final + abs(M), int(final + up(fair + Z95 * sd) > 0))
            lead_pf = g["pf_h"] if M > 0 else g["pf_a"]
            if abs(M) >= 10 and (ch if M > 0 else ca) * 4 / p - lead_pf >= PACE_EXTRA and s * pre_m >= -LEVEL_GAP:
                rit.append(row)
            if p == 2 and s * pre_m >= FAV and g["id"] in halves and len(halves[g["id"]]) == 2:
                hf, hd = halves[g["id"]][1 if s > 0 else 0], halves[g["id"]][0 if s > 0 else 1]
                ef, ed = remontadas.efg(hf), remontadas.efg(hd)
                if ef is not None and ed is not None and ed - ef >= 15:
                    desf.append(row)
        for kind, rows in (("Ritmo insostenible", rit), ("Desfase (favorito con acierto muy inferior)", desf), ("Total desfasado", tot)):
            if len(rows) >= 10:
                n = len(rows)
                pc = lambda i: round(100 * sum(x[i] for x in rows) / n, 1)   # noqa: E731
                is_tot = rows[0][0] is None
                alerts.append(dict(kind=kind, when=name, n=n, pred=None if is_tot else round(sum(x[0] for x in rows) / n, 1),
                                   real=None if is_tot else pc(1), fair=pc(2), s80=pc(3), s90=pc(4), s95=pc(6), rec=round(sum(x[5] for x in rows) / n, 1),
                                   odds=None if is_tot or not pc(1) else round(100 / pc(1), 2)))
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
    """Entrada y salida con el marcador jugada a jugada (tabla basket_timeline: Euroliga, EuroCup y Liga Endesa).

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


# ---------- Cuartos anormales ----------
# Un cuarto son unos 20 tiros por equipo: uno disparatado casi siempre es casualidad y el siguiente vuelve a lo normal.
# Cada regla: (id, texto, mercado). El «residuo» es lo que pasó en el periodo siguiente menos lo esperable por la media
# del equipo (o de los dos); las líneas de seguridad son cuantiles de ese residuo en el histórico.
Q_RULES = {
    "eq_frio": dict(txt="un equipo anota 10 puntos o menos en un cuarto", market="equipo", over=True),
    "eq_caliente": dict(txt="un equipo anota 30 puntos o más en un cuarto", market="equipo", over=False),
    "tot_frio": dict(txt="un cuarto con 30 puntos o menos entre los dos", market="total", over=True),
    "tot_caliente": dict(txt="un cuarto con 54 puntos o más entre los dos", market="total", over=False),
    "paliza": dict(txt="un equipo pierde un cuarto por 10 o más", market="hcap", over=True),
    "mitad_fria": dict(txt="una primera parte con 66 puntos o menos", market="mitad", over=True),
    "mitad_caliente": dict(txt="una primera parte con 100 puntos o más", market="mitad", over=False),
}
Q_LEVELS = (80, 90, 95)


def quarter_cases(games):
    """Casos históricos de cada regla: (regla, temporada, residuo del periodo siguiente, ¿se cumplió el hecho simple?)."""
    out = []
    for g in games:
        if g["Q"] is None:
            continue
        q, exp_q = g["q"], 10 * (K["home"] + K["q"] * g["Q"])      # diferencia esperada por cuarto para el local
        pf = {0: g["pf_h"] / 4, 1: g["pf_a"] / 4}
        for p in (1, 2, 3):
            tot, nxt = q[p][0] + q[p][1], q[p + 1][0] + q[p + 1][1]
            for s in (0, 1):
                me, me_next = q[p][s], q[p + 1][s]
                if me <= 10:
                    out.append(("eq_frio", g["season"], me_next - pf[s], me_next > me))
                if me >= 30:
                    out.append(("eq_caliente", g["season"], me_next - pf[s], me_next < me))
                sign = 1 if s == 0 else -1
                if sign * (q[p][0] - q[p][1]) <= -10:
                    m_next = sign * (q[p + 1][0] - q[p + 1][1])
                    out.append(("paliza", g["season"], m_next - sign * exp_q, m_next > -5.5))
            if tot <= 30:
                out.append(("tot_frio", g["season"], nxt - g["usual"] / 4, nxt > tot))
            if tot >= 54:
                out.append(("tot_caliente", g["season"], nxt - g["usual"] / 4, nxt < tot))
        h1, h2 = q[1][0] + q[1][1] + q[2][0] + q[2][1], q[3][0] + q[3][1] + q[4][0] + q[4][1]
        if h1 <= 66:
            out.append(("mitad_fria", g["season"], h2 - g["usual"] / 2, h2 > h1))
        if h1 >= 100:
            out.append(("mitad_caliente", g["season"], h2 - g["usual"] / 2, h2 < h1))
    return out


def _quantiles(xs, over):
    """Residuo que deja por debajo (si se apuesta a «más de») o por encima («menos de») el 20, 10 y 5 % de los casos."""
    xs = sorted(xs)
    pick = lambda frac: xs[min(len(xs) - 1, max(0, int(frac * len(xs))))]  # noqa: E731
    return {p: (pick(1 - p / 100) if over else pick(p / 100)) for p in Q_LEVELS}


def quarter_rules(games, split=2024):
    """Líneas de cada regla (con todo el histórico) y su comprobación honrada: se aprenden con las temporadas
    anteriores a `split` y se mira cuántas veces se cumplen en las siguientes, que no han intervenido."""
    cases, rules = quarter_cases(games), {}
    for rid, meta in Q_RULES.items():
        mine = [c for c in cases if c[0] == rid]
        if len(mine) < 60:
            continue
        over, train, test = meta["over"], [c[2] for c in mine if c[1] < split], [c[2] for c in mine if c[1] >= split]
        qs = _quantiles([c[2] for c in mine], over)
        check = {}
        if len(train) >= 40 and len(test) >= 20:
            qt = _quantiles(train, over)
            # misma cuenta que al apostar: la línea se redondea al medio punto del lado seguro
            hit = lambda x, r: (x > math.floor(r * 2) / 2) if over else (x < math.ceil(r * 2) / 2)  # noqa: E731
            check = {p: round(100 * sum(hit(x, qt[p]) for x in test) / len(test), 1) for p in Q_LEVELS}
        rules[rid] = dict(n=len(mine), fact=round(100 * sum(c[3] for c in mine) / len(mine), 1), q={p: round(v, 2) for p, v in qs.items()},
                          test_n=len(test), test=check, **meta)
    return rules


def triples(con, games):
    """Triples insostenibles al descanso (Euroliga, EuroCup y Liga Endesa): ¿vuelve el acierto a lo normal? ¿y el marcador?"""
    half = defaultdict(dict)
    try:
        for mid, is_home, h, pts, m3, a3 in con.execute("SELECT match_id, is_home, half, points, fg3m, fg3a FROM basket_half_stats"):
            half[mid][(is_home, h)] = (pts or 0, m3 or 0, a3 or 0)
    except sqlite3.OperationalError:
        return None
    full = defaultdict(dict)
    for mid, is_home, m3, a3 in con.execute("SELECT match_id, is_home, fg3m, fg3a FROM basket_team_stats WHERE fg3a > 0"):
        full[mid][is_home] = (m3, a3)
    base = defaultdict(lambda: deque(maxlen=LAST_N))
    up = lambda x: math.ceil(x * 2) / 2  # noqa: E731
    rows = []
    for g in games:
        d, codes = half.get(g["id"]), {1: g["hc"], 0: g["ac"]}
        if d and len(d) == 4 and g["Q"] is not None and all(len(base[c]) >= MIN_GAMES for c in codes.values()):
            Mh = g["q"][1][0] + g["q"][2][0] - g["q"][1][1] - g["q"][2][1]
            mean, sd = Mh + 20 * (K["home"] + K["q"] * g["Q"] + K["m"] * Mh), K["sd"] * math.sqrt(20)
            for home in (1, 0):
                _, m3, a3 = d[(home, 1)]
                _, m3b, a3b = d[(home, 2)]
                b = base[codes[home]]
                p0 = sum(x[0] for x in b) / sum(x[1] for x in b)
                s = 1 if home else -1                       # lado del equipo de los triples
                if a3 < 8 or not a3b or s * Mh < 8:
                    continue
                z = (m3 - a3 * p0) / math.sqrt(a3 * p0 * (1 - p0))
                if z < 1.5:
                    continue
                rival, fr = -s * (g["hs"] - g["as_"]), s * mean      # diferencia final y hándicap neutro del rival
                rows.append(dict(p1=m3 / a3, p2=m3b / a3b, p0=p0, lead=s * Mh, d2=d[(home, 2)][0] - d[(1 - home, 2)][0],
                                 s80=rival + up(fr + Z80 * sd) > 0, s90=rival + up(fr + Z90 * sd) > 0, s95=rival + up(fr + Z95 * sd) > 0))
        for is_home, v in full.get(g["id"], {}).items():
            base[codes[is_home]].append(v)
    n = len(rows)
    if n < 30:
        return None
    m = lambda k: sum(r[k] for r in rows) / n  # noqa: E731
    return dict(n=n, p1=round(100 * m("p1")), p2=round(100 * m("p2")), p0=round(100 * m("p0")), lead=round(m("lead"), 1), d2=round(m("d2"), 1),
                s80=round(100 * m("s80"), 1), s90=round(100 * m("s90"), 1), s95=round(100 * m("s95"), 1))


def sent_log(con, path):
    """Avisos que el vigilante envió de verdad por Telegram, con su resultado cuando el partido ya ha terminado."""
    path = Path(path)
    if not path.exists():
        return None
    import avisos
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    rows = []
    for e in log:
        if "alert" not in e or e.get("anulado"):    # marcas del mensaje previo y avisos falsos por un fallo de la fuente
            continue
        r = con.execute("""SELECT m.home_score, m.away_score, m.status, m.date,
                                  (SELECT SUM(p.home + p.away) FROM periods p WHERE p.match_id = m.match_id AND p.period <= 4)
                           FROM matches m WHERE m.league = ? AND m.season_start = ? AND m.source_id = ?""",
                        ({"E": "Euroliga", "U": "EuroCup", "A": "Liga Endesa"}[e["comp"]], e["year"], str(e["code"]))).fetchone()
        al = e["alert"]
        if al["type"] == "cuarto":      # su resultado lo anota el propio vigilante al acabar el periodo apostado
            team = e["home"] if al["side"] == 1 else e["away"] if al["side"] == -1 else None
            where = "2.ª parte" if al["target"] == "mitad" else f"cuarto {al['target']}"
            bet = (f"hándicap de {team}" if al["market"] == "hcap" else f"puntos de {team}: {'más' if al['over'] else 'menos'} de" if al["market"] == "equipo"
                   else f"total: {'más' if al['over'] else 'menos'} de") + f" ({where})"
            rows.append(dict(ts=e["ts"], comp=e["comp"], home=e["home"], away=e["away"], el=e["el"], score=e["score"], type="cuarto", level=al["level"],
                             bet=bet, lines=al["lines"], hcap=al["market"] == "hcap", res=e.get("res"),
                             final=str(e.get("value")) if e.get("res") else (f"{r[0]}-{r[1]}" if r and r[2] == "played" else None)))
            continue
        row = dict(ts=e["ts"], comp=e["comp"], home=e["home"], away=e["away"], el=e["el"], score=e["score"], type=al["type"], level=al["level"],
                   bet=("menos de" if al.get("under") else "más de") if al["type"] == "total" else (e["home"] if al["side"] > 0 else e["away"]),
                   lines=al["lines"], hcap=al["type"] != "total", res=None, final=None)
        if r and r[2] == "played" and r[0] is not None:
            row["res"] = {str(p): ok for p, ok in avisos.settle({**al, "lines": {int(p): v for p, v in al["lines"].items()}}, r[0], r[1], r[4]).items()}
            row["final"] = f"{r[0]}-{r[1]}"
        rows.append(row)
    done = [x for x in rows if x["res"]]
    by = {}
    for x in done:
        b = by.setdefault(x["type"], dict(n=0, s80=0, s90=0, s95=0))
        b["n"] += 1
        for p in ("80", "90", "95"):
            b["s" + p] += int(x["res"][p])
    return dict(rows=rows[::-1][:200], n=len(rows), done=len(done), by=by)


def build(con, today=None, log_path=None):
    games, halves, hist = walk(con)
    pre = fit_pre(games)
    return dict(triples=triples(con, games), sent=sent_log(con, log_path) if log_path else None, cuartos=quarter_rules(games),updated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), pre=pre,
                check=check(games, halves, pre), games=upcoming(con, hist, pre, today), timing=timing(con, games),
                consts=dict(K=K, KT=KT, eurocup_gap=EUROCUP_GAP))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args()
    d = build(sqlite3.connect(a.db))
    print(json.dumps({k: d[k] for k in ("pre", "check", "timing")}, ensure_ascii=False, indent=1))
    print(len(d["games"]), "partidos próximos; ejemplo:", json.dumps(d["games"][:2], ensure_ascii=False))
