#!/usr/bin/env python3
"""Construye la web estática en _site/ (la que publica GitHub Pages).

    _site/index.html     panel SportsDB (panel/index.html + cabecera HTML)
    _site/data.json      datos del panel (export_panel.py)
    _site/en-vivo.html   panel de partidos en directo (export_live.py)
    _site/jornada.html   previsión pre-partido de los próximos días (export_informes.py)
    _site/funciona.html  comprobación del modelo con el histórico (export_informes.py)
    _site/pruebas.html   banco de pruebas: avisos de Telegram como apuestas simuladas (export_informes.py)
    _site/futbol.html    chuleta de fútbol en directo según el marcador al descanso (futbol.py)
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


# Botón «Actualizar datos» (abajo a la derecha de todas las páginas): lanza a mano la actualización de actualizar.yml.
# La clave de GitHub (permiso Actions: lectura y escritura, solo este repositorio) se pega la primera vez y queda SOLO
# en el navegador de quien la pega (localStorage); nunca va en el código ni en la web publicada.
ACTUALIZAR = """<style>#sdb-act{position:fixed;right:14px;bottom:14px;z-index:50;display:flex;gap:6px;align-items:center;font:13px system-ui,sans-serif}
#sdb-act button{border:1px solid #888;background:#fff;color:#111;border-radius:8px;padding:8px 12px;cursor:pointer;font:inherit;box-shadow:0 1px 4px #0003}
#sdb-act button[disabled]{opacity:.6;cursor:wait}#sdb-act #sdb-key{padding:8px 9px}
#sdb-msg{position:fixed;right:14px;bottom:60px;z-index:50;max-width:min(320px,calc(100vw - 28px));background:#111;color:#fff;border-radius:8px;padding:9px 12px;font:13px system-ui,sans-serif;line-height:1.35}
@media (prefers-color-scheme:dark){#sdb-act button{background:#222;color:#eee;border-color:#666}#sdb-msg{background:#eee;color:#111}}</style>
<div id="sdb-act"><button id="sdb-go" type="button" title="Lanza la actualización de datos y de la web (tarda unos 2 minutos)">Actualizar datos</button>
<button id="sdb-key" type="button" title="Olvidar la clave de GitHub guardada en este navegador" aria-label="Olvidar la clave de GitHub">&#128273;</button></div>
<script>(function(){var R="Sergio20/sportsdb",K="sdb_gh_token",go=document.getElementById("sdb-go"),msg,tm;
function say(t){if(!msg){msg=document.createElement("div");msg.id="sdb-msg";document.body.appendChild(msg)}msg.textContent=t;msg.hidden=false;clearTimeout(tm);tm=setTimeout(function(){msg.hidden=true},9000)}
function get(){try{return localStorage.getItem(K)}catch(e){return null}}
function put(v){try{if(v)localStorage.setItem(K,v);else localStorage.removeItem(K)}catch(e){}}
document.getElementById("sdb-key").onclick=function(){put(null);say("Clave olvidada. La próxima vez te la pedirá de nuevo.")};
go.onclick=function(){var t=get();
if(!t){t=prompt("Pega tu clave de GitHub (la primera vez). Debe ser una clave de acceso personal de grano fino, solo para el repositorio sportsdb y con el permiso «Actions: Read and write». Se guarda únicamente en este navegador.");
if(!t||!(t=t.trim()))return;put(t)}
go.disabled=true;
fetch("https://api.github.com/repos/"+R+"/actions/workflows/actualizar.yml/runs?per_page=5",{headers:{Accept:"application/vnd.github+json"},cache:"no-store"})
.then(function(r){return r.ok?r.json():{workflow_runs:[]}}).catch(function(){return{workflow_runs:[]}})
.then(function(d){var busy=(d.workflow_runs||[]).some(function(w){return w.event!=="push"&&(w.status==="in_progress"||w.status==="queued")});
if(busy){say("Ya hay una actualización en marcha. Espera a que termine (unos 2 minutos).");return}
return fetch("https://api.github.com/repos/"+R+"/actions/workflows/actualizar.yml/dispatches",{method:"POST",
headers:{Accept:"application/vnd.github+json",Authorization:"Bearer "+t,"Content-Type":"application/json"},body:JSON.stringify({ref:"main"})})
.then(function(r){if(r.status===204)say("Actualización lanzada. Tarda unos 2 minutos; esta página se recargará sola cuando haya datos nuevos.");
else if(r.status===401||r.status===403||r.status===404){put(null);say("GitHub no acepta la clave (caducada o sin permiso «Actions: Read and write» en sportsdb). La he olvidado: pulsa de nuevo para pegar otra.")}
else say("GitHub ha respondido con el error "+r.status+". Inténtalo en un rato.")})})
.catch(function(){say("No se ha podido contactar con GitHub. Revisa la conexión.")})
.then(function(){go.disabled=false})};})();</script>
"""


def stamp(out):
    build_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    for page in out.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        i = html.rindex("</body>")
        page.write_text(html[:i] + ACTUALIZAR + RELOAD % build_id + html[i:], encoding="utf-8")
    (out / "version.json").write_text('{"build":"%s"}' % build_id, encoding="utf-8")


def build(db=DEFAULT_DB, out=ROOT / "_site"):
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    panel = (ROOT / "panel" / "index.html").read_text(encoding="utf-8")
    assert LIVE_LINK in panel, "no se encuentra la barra de pestañas del panel"
    panel = panel.replace(LIVE_LINK, LIVE_LINK + '\n    <a href="en-vivo.html">En vivo</a>\n    <a href="jornada.html">Jornada</a>'
                                                 '\n    <a href="funciona.html">¿Funciona?</a>\n    <a href="pruebas.html">Banco de pruebas</a>\n    <a href="futbol.html">Fútbol</a>')
    (out / "index.html").write_text(HEAD + panel + "</body></html>", encoding="utf-8")
    export_panel.export(db, out / "data.json")
    live = export_live.export(db, out / "en-vivo.html")
    html = live.read_text(encoding="utf-8")
    html = html.replace('<meta name="viewport"', '<meta name="robots" content="noindex,nofollow">\n<meta name="viewport"', 1)
    html = html.replace('<div><h1>SportsDB en vivo</h1>', '<div><div class="nav"><a href="./">Panel</a><a href="en-vivo.html" aria-current="page">En vivo</a>'
                        '<a href="jornada.html">Jornada</a><a href="funciona.html">¿Funciona?</a><a href="pruebas.html">Banco de pruebas</a><a href="futbol.html">Fútbol</a></div><h1>SportsDB en vivo</h1>', 1)
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
