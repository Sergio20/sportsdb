#!/usr/bin/env python3
"""Genera las páginas «Jornada» (previsión pre-partido), «¿Funciona?» (comprobación del modelo) y «Banco de pruebas»
(cada aviso enviado por Telegram tratado como una apuesta simulada).

Los números salen de analisis.py; aquí solo se incrustan en las plantillas de panel/.

    python export_informes.py [--db data/deportes.db] [--out data]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

import analisis
from sportsdb.common import DEFAULT_DB

ROOT = Path(__file__).resolve().parent
PAGES = {"jornada.html": "jornada_plantilla.html", "funciona.html": "funciona_plantilla.html", "pruebas.html": "pruebas_plantilla.html"}
NAV_CSS = (".nav { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-bottom: 6px; font-size: 14px }\n"
           ".nav a { color: var(--fg-2); text-decoration: none; font-weight: 600 }\n"
           ".nav a:hover, .nav a[aria-current] { color: var(--accent) }\n"
           "a { color: var(--accent) }")


def shared_css() -> str:
    """Las páginas comparten el aspecto del panel en vivo: se copia su hoja de estilos."""
    live = (ROOT / "panel" / "en_vivo_plantilla.html").read_text(encoding="utf-8")
    return re.search(r"<style>(.*?)</style>", live, re.S).group(1).strip() + "\n" + NAV_CSS


def export(db=DEFAULT_DB, out=None):
    db = Path(db)
    out = Path(out) if out else db.parent
    con = sqlite3.connect(db)
    data = analisis.build(con, log_path=db.parent / "avisos.json")
    analisis.save_bank(con, data.get("sent"))
    data["bank_cfg"] = dict(stake=analisis.STAKE, odds=analisis.ODDS, min_odds=analisis.MIN_ODDS)
    con.close()
    css, blob = shared_css(), json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for page, tpl in PAGES.items():
        html = (ROOT / "panel" / tpl).read_text(encoding="utf-8")
        a, b = html.index("/*DATA*/"), html.index("/*END*/")
        html = html[:a] + "/*DATA*/" + blob + html[b:]
        (out / page).write_text(html.replace("/*CSS*/", css, 1), encoding="utf-8")
    export_futbol(db, out, css)
    t = data.get("timing") or {}
    print(f"Informes escritos en {out}: {len(data['games'])} partidos próximos, {t.get('n', 0)} partidos con jugadas")
    return out


def export_futbol(db, out, css):
    """Chuleta de fútbol en directo (futbol.py → futbol.html)."""
    import datetime as dt
    import futbol
    con = sqlite3.connect(db)
    try:
        data = futbol.build(con) or {}
    except sqlite3.OperationalError:     # aún no existe la tabla futbol_hist
        data = {}
    con.close()
    data["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    html = (ROOT / "panel" / "futbol_plantilla.html").read_text(encoding="utf-8")
    a, b = html.index("/*DATA*/"), html.index("/*END*/")
    html = html[:a] + "/*DATA*/" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + html[b:]
    (out / "futbol.html").write_text(html.replace("/*CSS*/", css, 1), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    export(a.db, a.out)
