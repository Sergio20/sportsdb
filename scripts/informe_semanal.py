#!/usr/bin/env python3
"""Informe semanal del banco de pruebas por Telegram (lo lanza .github/workflows/informe.yml los lunes).

Últimos 7 días y desde el principio: acierto y balance de cada línea (apostando en todos los avisos y con tope de una
apuesta por partido), por tipo de aviso, lo que ofreció la casa de Sergio, la rapidez de los avisos y el freno automático.
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

LINES = ("95", "90", "80")
KIND = {"desfase": "Desfase", "ritmo": "Ritmo", "triples": "Triples", "total": "Total", "cuarto": "Cuarto anormal", "racha": "Racha de cuartos"}


def eur(v):
    return ("+" if v > 0 else "−" if v < 0 else "") + f"{abs(v):,.0f} €".replace(",", ".")


def block(rows, title):
    """Acierto y balance por línea: todos los avisos y una sola apuesta por partido (el primer aviso)."""
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
    for p in LINES:
        g = lambda xs: sum(analisis.STAKE * (analisis.ODDS[p] - 1) if x["res"][p] else -analisis.STAKE for x in xs)  # noqa: E731
        ok, okf = sum(bool(x["res"][p]) for x in done), sum(bool(x["res"][p]) for x in firsts)
        out.append(f"  {p} %: {ok}/{len(done)} ({100 * ok / len(done):.0f} %) {eur(g(done))} · por partido {okf}/{len(firsts)} {eur(g(firsts))}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--log", default="data/avisos.json")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    sent = analisis.sent_log(con, a.log) or {}
    rows = sent.get("bank") or []
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat()
    week = [x for x in rows if x["ts"] >= since]
    msg = ["📊 INFORME SEMANAL · banco de pruebas (simulado, 500 € por línea)", ""]
    msg += block(week, "Últimos 7 días") + [""] + block(rows, "Desde el principio")
    by = {}
    for x in rows:
        if x["res"]:
            b = by.setdefault(x["type"], [0, 0])
            b[0] += 1
            b[1] += bool(x["res"]["90"])
    if by:
        msg += ["", "Por tipo (línea del 90 %): " + " · ".join(f"{KIND.get(k, k)} {v[1]}/{v[0]}" for k, v in sorted(by.items()))]
    casa = [x for x in rows if x.get("casa")]
    offers = [c for x in casa for c in x["casa"] if c.get("line") is not None and c.get("odds")]
    msg += ["", f"Tu casa: {len(casa)} avisos valorados, {len(offers)} líneas anotadas, "
            f"{sum(c['ev'] > 0 for c in offers)} con valor; ofrecía nuestras líneas en "
            f"{sum(any(c.get('p', 0) >= 79.5 for c in x['casa'] if c.get('line') is not None) for x in casa)}."]
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
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
        print("Telegram:", r.status_code)
        return 0 if r.status_code == 200 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
