#!/usr/bin/env python3
"""Construye la web estática en _site/ (la que publica GitHub Pages).

    _site/index.html     panel SportsDB (panel/index.html + cabecera HTML)
    _site/data.json      datos del panel (export_panel.py)
    _site/en-vivo.html   panel de partidos en directo (export_live.py)
    _site/jornada.html   previsión pre-partido de los próximos días (export_informes.py)
    _site/funciona.html  comprobación del modelo con el histórico (export_informes.py)
    _site/pruebas.html   banco de pruebas: avisos de Telegram como apuestas simuladas (export_informes.py)
"""
from __future__ import annotations

import argparse
import datetime as dt
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


# Los navegadores (y GitHub Pages, 10 minutos) guardan copia de las páginas: sin esto, tras publicar una versión nueva
# se puede seguir viendo la antigua. Cada página lleva el sello de su construcción y lo compara con version.json, que
# se pide siempre sin caché; si hay una más nueva, se recarga sola añadiendo ?v=sello a la dirección.
RELOAD = ('<script>(function(){var B="%s";function c(){fetch("version.json",{cache:"no-store"}).then(function(r){return r.json()})'
          '.then(function(v){if(!v.build||v.build===B)return;var u=new URL(location.href);if(u.searchParams.get("v")===v.build)return;'
          'u.searchParams.set("v",v.build);location.replace(u)}).catch(function(){})}c();setInterval(c,180000)})();</script>')


def stamp(out):
    build_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    for page in out.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        i = html.rindex("</body>")
        page.write_text(html[:i] + RELOAD % build_id + html[i:], encoding="utf-8")
    (out / "version.json").write_text('{"build":"%s"}' % build_id, encoding="utf-8")


def build(db=DEFAULT_DB, out=ROOT / "_site"):
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    panel = (ROOT / "panel" / "index.html").read_text(encoding="utf-8")
    assert LIVE_LINK in panel, "no se encuentra la barra de pestañas del panel"
    panel = panel.replace(LIVE_LINK, LIVE_LINK + '\n    <a href="en-vivo.html">En vivo</a>\n    <a href="jornada.html">Jornada</a>'
                                                 '\n    <a href="funciona.html">¿Funciona?</a>\n    <a href="pruebas.html">Banco de pruebas</a>')
    (out / "index.html").write_text(HEAD + panel + "</body></html>", encoding="utf-8")
    export_panel.export(db, out / "data.json")
    live = export_live.export(db, out / "en-vivo.html")
    html = live.read_text(encoding="utf-8")
    html = html.replace('<meta name="viewport"', '<meta name="robots" content="noindex,nofollow">\n<meta name="viewport"', 1)
    html = html.replace('<div><h1>SportsDB en vivo</h1>', '<div><div class="nav"><a href="./">Panel</a><a href="en-vivo.html" aria-current="page">En vivo</a>'
                        '<a href="jornada.html">Jornada</a><a href="funciona.html">¿Funciona?</a><a href="pruebas.html">Banco de pruebas</a></div><h1>SportsDB en vivo</h1>', 1)
    html = html.replace("</style>", export_informes.NAV_CSS + "\n</style>", 1)
    live.write_text(html, encoding="utf-8")
    export_informes.export(db, out)
    stamp(out)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Web construida en {out}: " + ", ".join(sorted(p.name for p in out.iterdir())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(ROOT / "_site"))
    a = ap.parse_args()
    build(a.db, a.out)
