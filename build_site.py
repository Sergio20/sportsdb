#!/usr/bin/env python3
"""Construye la web estática en _site/ (la que publica GitHub Pages).

    _site/index.html     panel SportsDB (panel/index.html + cabecera HTML)
    _site/data.json      datos del panel (export_panel.py)
    _site/en-vivo.html   panel de partidos en directo (export_live.py)
    _site/jornada.html   previsión pre-partido de los próximos días (export_informes.py)
    _site/funciona.html  comprobación del modelo con el histórico (export_informes.py)
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import export_informes
import export_live
import export_panel
from sportsdb.common import DEFAULT_DB

ROOT = Path(__file__).resolve().parent
HEAD = ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="robots" content="noindex,nofollow">'
        '<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style></head><body>')
LIVE_LINK = '<a href="#simulador">Simulador</a>'


def build(db=DEFAULT_DB, out=ROOT / "_site"):
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    panel = (ROOT / "panel" / "index.html").read_text(encoding="utf-8")
    assert LIVE_LINK in panel, "no se encuentra la barra de pestañas del panel"
    panel = panel.replace(LIVE_LINK, LIVE_LINK + '\n    <a href="en-vivo.html">En vivo</a>\n    <a href="jornada.html">Jornada</a>'
                                                 '\n    <a href="funciona.html">¿Funciona?</a>')
    (out / "index.html").write_text(HEAD + panel + "</body></html>", encoding="utf-8")
    export_panel.export(db, out / "data.json")
    live = export_live.export(db, out / "en-vivo.html")
    html = live.read_text(encoding="utf-8")
    html = html.replace('<meta name="viewport"', '<meta name="robots" content="noindex,nofollow">\n<meta name="viewport"', 1)
    html = html.replace('<div><h1>SportsDB en vivo</h1>', '<div><div class="nav"><a href="./">Panel</a><a href="en-vivo.html" aria-current="page">En vivo</a>'
                        '<a href="jornada.html">Jornada</a><a href="funciona.html">¿Funciona?</a></div><h1>SportsDB en vivo</h1>', 1)
    html = html.replace("</style>", export_informes.NAV_CSS + "\n</style>", 1)
    live.write_text(html, encoding="utf-8")
    export_informes.export(db, out)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Web construida en {out}: " + ", ".join(sorted(p.name for p in out.iterdir())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(ROOT / "_site"))
    a = ap.parse_args()
    build(a.db, a.out)
