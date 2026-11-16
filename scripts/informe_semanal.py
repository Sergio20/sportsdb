#!/usr/bin/env python3
"""Informe semanal (lunes) y mensual (día 1, con --mes) del banco de pruebas por Telegram (.github/workflows/informe.yml).

El periodo y toda la v2: acierto y balance de las cuatro líneas con su color (95 azul, 90 naranja, 85 verde, rebajada
morada; en todos los avisos y con una apuesta por partido), lo que Sergio anotó de su casa (cuota media, acierto medio
nuestro frente al de la casa, balance con sus cuotas), por tipo de aviso, la rapidez y el freno automático.
Sin TELEGRAM_TOKEN / TELEGRAM_CHAT_ID solo lo escribe en pantalla.

    python scripts/informe_semanal.py [--db data/deportes.db] [--log data/avisos.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analisis  # noqa: E402
import avisos  # noqa: E402
from sportsdb.common import DEFAULT_DB  # noqa: E402

# Las cuatro líneas con las que se trabaja (16-11-2026, Sergio), con los colores del banco y de los avisos
LINES = (("95", "🔵", "95 %"), ("90", "🟠", "90 %"), ("85", "🟢", "85 %"), ("82", "🟣", "Rebajada"))
KIND = {"desfase": "Desfase", "ritmo": "Ritmo", "triples": "Triples", "total": "Total", "cuarto": "Cuarto anormal", "racha": "Racha de cuartos"}
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def eur(v):
    v = round(v)
    return ("+" if v > 0 else "−" if v < 0 else "") + f"{abs(v):,.0f} €".replace(",", ".")


def is_v2(x):
    return bool((x.get("rb") or {}).get("live"))


def block(rows, title):
    """Acierto y balance de cada línea (con su color): en todos los avisos y con una sola apuesta por partido."""
    done = [x for x in rows if x["res"]]
    if not done:
        return [f"{title}: sin avisos resueltos."]
    seen, firsts = set(), []
    for x in sorted(done, key=lambda x: x["ts"]):
        k = (x["comp"], x["home"], x["away"], x["ts"][:10])
        if k not in seen:
            seen.add(k)
            firsts.append(x)
    out = [f"{title}: {len(done)} avisos en {len(firsts)} partidos"]
    for p, icon, name in LINES:
        has = lambda xs: [x for x in xs if x["res"].get(p) is not None]  # noqa: E731
        g = lambda xs: sum(analisis.STAKE * (analisis.ODDS[p] - 1) if x["res"][p] else -analisis.STAKE for x in xs)  # noqa: E731
        xs, fs = has(done), has(firsts)
        if not xs:
            continue
        ok, okf = sum(bool(x["res"][p]) for x in xs), sum(bool(x["res"][p]) for x in fs)
        if p == "82":       # a qué % se apuesta con la rebajada: la media del % real de cada aviso (entre 82 y 89)
            name = f"Rebajada (la del {sum(avisos.rebaja_pct(x) for x in xs) / len(xs):.0f} % de media)"
        out.append(f"{icon} {name}: {ok}/{len(xs)} ({100 * ok / len(xs):.0f} %) {eur(g(xs))}{' 🟣' if p == '82' else ''}")
        out.append(f"      una por partido: {okf}/{len(fs)} {eur(g(fs))}")
    return out


def casa_block(rows, title):
    """Lo que Sergio anotó de su casa (línea y cuota reales): medias y lo que habría dejado apostar la línea de más
    valor de cada aviso, con el importe del banco."""
    rated = [x for x in rows if x.get("casa")]
    offers = [c for x in rated for c in x["casa"] if c.get("line") is not None and c.get("odds")]
    out = [f"👤 TU CASA · {title}"]
    if not offers:
        return out + ["Sin líneas anotadas." if not rated else f"{len(rated)} avisos valorados: en ninguno ofrecía nada parecido."]
    picks = [max((c for c in x["casa"] if c.get("line") is not None and c.get("odds")), key=lambda c: c.get("ev") or 0, default=None) for x in rated]
    picks = [c for c in picks if c and c.get("hit") is not None]
    avg = lambda f: sum(f(c) for c in offers) / len(offers)  # noqa: E731
    ours, theirs = avg(lambda c: c["p"]), avg(lambda c: c["implied"])
    out.append(f"{len(rated)} avisos valorados · {len(offers)} líneas anotadas · cuota media {avg(lambda c: c['odds']):.2f}".replace(".", ","))
    out.append(f"Acierto medio según nosotros: {ours:.0f} % · según la casa (100/cuota): {theirs:.0f} % → "
               + (f"ventaja nuestra de {ours - theirs:.0f} puntos" if ours > theirs else f"la casa pide {theirs - ours:.0f} puntos más de los que damos"))
    out.append(f"Con valor: {sum(c['ev'] > 0 for c in offers)} de {len(offers)}")
    if picks:
        won = sum(bool(c["hit"]) for c in picks)
        bal = sum(analisis.STAKE * (c["odds"] - 1) if c["hit"] else -analisis.STAKE for c in picks)
        out.append(f"Apostando tu línea en cada aviso ({analisis.STAKE:.0f} €): {won}/{len(picks)} ganadas ({100 * won / len(picks):.0f} %) {eur(bal)}")
    return out


def bold_marks(text):
    """Negrita (Telegram no tiene color de texto) para la línea rebajada y los títulos; posiciones en UTF-16."""
    out, pos = [], 0
    for line in text.split("\n"):
        n = len(line.encode("utf-16-le")) // 2
        if line.startswith(("🟣", "👤", "📊", "🗓️")):
            out.append({"type": "bold", "offset": pos, "length": n})
        pos += n + 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--log", default="data/avisos.json")
    ap.add_argument("--mes", action="store_true", help="informe mensual: el mes anterior si se lanza los 3 primeros días; si no, el mes en curso")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    sent = analisis.sent_log(con, a.log) or {}
    rows = sent.get("bank") or []
    now = dt.datetime.now(dt.timezone.utc)
    if a.mes:
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.day <= 3:
            end, first = first, (first - dt.timedelta(days=1)).replace(day=1)
        else:
            end = now
        period = [x for x in rows if first.isoformat() <= x["ts"] < end.isoformat()]
        label = f"{MESES[first.month - 1].capitalize()} de {first.year}" + ("" if now.day <= 3 else f" (hasta el día {now.day})")
        msg = [f"🗓️ INFORME MENSUAL · {label.upper()}", f"Banco de pruebas, simulado: {analisis.STAKE:.0f} € por línea", ""]
    else:
        since = (now - dt.timedelta(days=7)).isoformat()
        period = [x for x in rows if x["ts"] >= since]
        label = "Últimos 7 días"
        msg = ["📊 INFORME SEMANAL", f"Banco de pruebas, simulado: {analisis.STAKE:.0f} € por línea", ""]
    v2 = [x for x in rows if is_v2(x)]
    msg += block(period, label) + [""] + block(v2, "Toda la v2 (desde el 9-11-2026)")
    msg += [""] + casa_block(period, label) + [""] + casa_block(v2, "toda la v2")
    by = {}
    for x in period:
        if x["res"]:
            b = by.setdefault(x["type"], [0, 0])
            b[0] += 1
            b[1] += bool(x["res"]["90"])
    if by:
        msg += ["", f"Por tipo de aviso, 🟠 línea del 90 % ({label.lower()}): " + " · ".join(f"{KIND.get(k, k)} {v[1]}/{v[0]}" for k, v in sorted(by.items()))]
    msg.append("")
    reads = [x["lat"]["read"] for x in rows if x.get("lat")]
    polls = [x["lat"]["poll"] for x in rows if x.get("lat") and x["lat"].get("poll")]
    reacts = [x["react"] for x in rows if x.get("react") is not None]
    if reads or reacts:
        msg.append("Rapidez: " + " · ".join(filter(None, [
            f"lectura→aviso {statistics.median(reads):.0f} s" if reads else "",
            f"se leía cada {statistics.median(polls):.0f} s" if polls else "",
            f"aviso→tu primer toque {statistics.median(reacts):.0f} s" if reacts else ""])))
    log = json.loads(Path(a.log).read_text(encoding="utf-8")) if Path(a.log).exists() else []
    check = analisis.rule_check(log)
    off = [r for r, v in check.items() if v["off"]]
    near = [f"{r} {v['ok']}/{v['n']}" for r, v in check.items() if not v["off"] and v["n"] >= 15 and v["pct"] < 85]
    msg.append("Freno automático: " + (("silenciadas " + ", ".join(off)) if off else "ninguna regla silenciada")
               + (f" · a vigilar: {', '.join(near)}" if near else "")
               + f" (se silencia con {analisis.FRENO_MIN}+ partidos si la línea del 90 % no llega ni al {analisis.FRENO_TOPE} %)")
    msg += ["", "Detalle: https://sergio20.github.io/sportsdb/pruebas.html"]
    text = "\n".join(msg)
    print(text)
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True, "entities": bold_marks(text)})
        print("Telegram:", r.status_code)
        return 0 if r.status_code == 200 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
