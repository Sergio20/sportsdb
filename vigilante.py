#!/usr/bin/env python3
"""Vigila los partidos en directo de Euroliga y EuroCup y envía los avisos por Telegram.

Lo lanza cada tarde .github/workflows/vigilante.yml. Mientras haya partidos en juego lee las jugadas
oficiales cada 10-20 segundos, aplica las reglas de avisos.py y manda un mensaje por cada aviso nuevo,
con las líneas de seguridad (80, 90 y 95 %). Al acabar cada partido manda cómo quedó cada aviso.
Todo queda anotado en un fichero (rama `avisos` de GitHub) para medir después cuánto aciertan.

    python vigilante.py [--db data/deportes.db] [--log avisos.json] [--horas 4.5]
    python vigilante.py --prueba                    # solo manda un mensaje de prueba
    python vigilante.py --repetir E2026 26 18       # ensayo: el partido 26, parado en el minuto 18 (no anota)

Telegram: variables de entorno TELEGRAM_TOKEN y TELEGRAM_CHAT_ID. Sin ellas, los mensajes solo se escriben en pantalla.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import analisis
import avisos
import export_live
from build_timeline import PERIODS, minute
from sportsdb import acb
from sportsdb.common import DEFAULT_DB, UA, current_season_start, http_get

LIVE = "https://live.euroleague.net/api/"
GAMES = "https://api-live.euroleague.net/v2/competitions/{c}/seasons/{c}{y}/games?limit=1000"
EURO = ("E", "U")
COMP = {"E": "Euroliga", "U": "EuroCup", "A": "Liga Endesa"}
WEB = "https://sergio20.github.io/sportsdb/en-vivo.html"
# Ritmo de revisión: lo más rápido que aguanta cada fuente. Con pocos partidos a la vez se revisa cada MIN_EVERY
# segundos; con muchos, el intervalo crece para no pasar de ~0,4 lecturas por segundo a Euroleague (por encima
# corta el acceso varios minutos, que es mucho peor) ni de ~0,3 a acb.com. Una sola lectura por partido y vuelta.
MIN_EVERY, PER_EURO, PER_ACB, GAP = 10, 2.5, 3.5, 0.3
EXTRA_MAX = 30                # si la fuente corta (429), se suman segundos al intervalo y luego se van quitando
LATE, GIVE_UP = 40, 150     # minutos sin empezar tras su hora: aviso de «¿aplazado?» y, más tarde, se deja de vigilar
PUBLISH_EVERY = 30            # la Liga Endesa se publica para la web cada medio minuto como mucho (en segundo plano)
pace = {"extra": 0}
ses = requests.Session()
ses.headers["User-Agent"] = UA
now = lambda: dt.datetime.now(dt.timezone.utc)  # noqa: E731


def get(url, **params):
    """JSON de la fuente, o None si falla o está cortando (se reintenta en la siguiente vuelta)."""
    time.sleep(GAP)
    try:
        r = ses.get(url, params=params or None, timeout=30)
    except requests.RequestException:
        return None
    if r.status_code == 429:
        pace["extra"] = min(EXTRA_MAX, pace["extra"] + 10)
        print(f"  {now():%H:%M:%S} la fuente corta el acceso: pausa de 45 s y {pace['extra']} s más entre revisiones", flush=True)
        time.sleep(45)
        return None
    return r.json() if r.status_code == 200 and r.text.strip() else None


def send(text, reply_to=None, buttons=None):
    """Manda un mensaje por Telegram y devuelve su número (o False). El token nunca se escribe en pantalla."""
    print("\n" + text + "\n", flush=True)
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("  (Telegram sin configurar: el mensaje no se ha enviado)", flush=True)
        return False
    for wait in (0, 3, 10, 30):         # un corte momentáneo no puede costar un aviso: hasta 4 intentos
        time.sleep(wait)
        try:
            body = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
            if reply_to:
                body["reply_to_message_id"] = reply_to
            if buttons:
                body["reply_markup"] = buttons
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, json=body)
            if r.status_code == 200:
                return (r.json().get("result") or {}).get("message_id") or True
            print(f"  Telegram ha rechazado el mensaje (código {r.status_code})", flush=True)
            if r.status_code in (400, 401, 403):    # mensaje o credenciales no válidos: reintentar no sirve
                return False
        except requests.RequestException:
            print("  no se ha podido contactar con Telegram", flush=True)
    return False


def elapsed(h):
    q = int(h.get("Quarter") or 0)
    if not 1 <= q <= 4:
        return None
    m, s = (str(h.get("RemainingPartialTime") or "0:0").strip().split(":") + ["0"])[:2]
    return (q - 1) * 10 + 10 - (int(m or 0) + int(s or 0) / 60)


def read_plays(comp, year, code):
    d = get(LIVE + "PlaybyPlay", gamecode=code, seasoncode=f"{comp}{year}")
    if not d:
        return None
    return [dict(t=minute(p), team=(p.get("CODETEAM") or "").strip(), type=(p.get("PLAYTYPE") or "").strip())
            for k in PERIODS[:4] for p in (d.get(k) or [])]


MADRID = ZoneInfo("Europe/Madrid")
local = lambda t=None: (t or now()).astimezone(MADRID)  # noqa: E731


def message(al, g, el, score):
    """Aviso completo: cabecera con el partido y el momento, y el cuerpo explícito de avisos.describe."""
    moment = (f"Final del {avisos.ORD[al['done']]} cuarto" if al["type"] == "cuarto"
              else f"Minuto {el:.0f} de partido ({avisos.ORD[min(4, int(el // 10) + 1)]} cuarto)")
    return (f"🔔 {avisos.name_of(al).upper()} · {COMP[g['comp']]}\n{g['home']} {score} {g['away']}\n{moment} · {local():%H:%M}\n\n"
            + avisos.compact(al, g["home"], g["away"]))


def agenda(con, log, log_path, hours, games, hist):
    """Mensaje previo, una vez al día: los partidos que se van a vigilar, con lo que se espera de cada uno."""
    told = {i for e in log for i in e.get("agenda", [])}    # partidos ya anunciados en una tanda anterior
    rows = analisis.upcoming(con, hist, analisis.fit_pre(games), today=now().date())
    lines, ids = [], []
    for r in rows:
        start = dt.datetime.fromisoformat(f"{r['date']}T{r['time'] or '12:00'}:00+00:00")
        rid = f"{r['date']}|{r['home']}|{r['away']}"
        if rid in told or not now() - dt.timedelta(hours=1) <= start <= now() + dt.timedelta(hours=hours + 9):
            continue
        ids.append(rid)
        txt = f"{local(start):%H:%M} · {r['league']} · {r['home']} – {r['away']}"
        if r.get("margin") is not None:
            fav = r["home"] if r["margin"] >= 0 else r["away"]
            txt += f"\n   Favorito {fav} ({max(r['p'], 100 - r['p'])} %) · total previsto {r['total']:.0f}"
        for side, name in (("H", r["home"]), ("A", r["away"])):
            s, flags = r[side], []
            if s.get("new"):
                flags.append("recién llegado a la Euroliga")
            if s.get("rest") is not None and s["rest"] <= 2:
                flags.append(f"solo {s['rest']} día{'s' if s['rest'] != 1 else ''} de descanso")
            if s.get("week", 0) >= 2:
                flags.append(f"{s['week'] + 1}.º partido en 7 días")
            if flags:
                txt += f"\n   ⚠ {name}: " + ", ".join(flags)
        lines.append(txt)
    if not lines:
        return
    send("📋 Próximos partidos que vigilo (hora de España)\n\n" + "\n\n".join(lines)
         + "\n\nTe aviso en el momento en que salte un desfase, un ritmo insostenible, unos triples anormales o un total desfasado.")
    log.append(dict(agenda=ids, ts=now().isoformat(timespec="seconds")))
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


def quarter_step(g, st, base, rules, log, save):
    """Avisos de «cuarto anormal» nada más terminar cada cuarto, y su resultado en cuanto acaba el periodo apostado."""
    quarters, done = st.get("quarters") or [], st.get("done") or 0
    for e in g["sent"].values():                       # 1) resultados de avisos anteriores
        al = e["alert"]
        if al["type"] != "cuarto" or e.get("closed"):
            continue
        r = avisos.settle_quarter(al, quarters[:4 if st["final"] else done])
        if not r:
            continue
        res, value = r
        e.update(closed=True, res={str(p): bool(ok) for p, ok in res.items()}, value=value)
        what = "la 2.ª parte" if al["target"] == "mitad" else f"el {avisos.ORD[int(al['target'])]} cuarto"
        team = g["home"] if al["side"] == 1 else g["away"] if al["side"] == -1 else None
        got = ((f"{team} ganó {what} por {value}" if value > 0 else f"{team} perdió {what} por {-value}" if value < 0 else f"{team} empató {what}")
               if al["market"] == "hcap" else f"{team} metió {value} puntos en {what}" if al["market"] == "equipo"
               else f"hubo {value} puntos entre los dos en {what}")
        send(f"🏁 RESULTADO · {avisos.name_of(al).upper()}\n{g['home']} – {g['away']}\n\nAPOSTABAS A\n{avisos.bet_of(al, g['home'], g['away'])['market']}"
             + f"\n\nLO QUE PASÓ\n{got[0].upper() + got[1:]}.\n\n" + "\n".join(avisos.result_lines(al, e["res"], g["home"], g["away"])))
        save()
    el, bA, bB = st.get("el"), base.get(g["hc"]), base.get(g["ac"])
    if not st["live"] or el is None or not bA or not bB or done not in (1, 2, 3) or el - done * 10 > 2.5:
        return                                         # 2) avisos nuevos: solo justo al acabar el cuarto
    for al in avisos.quarter_alerts(done, quarters[:done], bA, bB, rules):
        k = avisos.key(al)
        if k in g["sent"]:
            continue
        score = f"{st['hs']}-{st['as_']}"
        mid = send(message(al, g, done * 10, score), buttons=BUTTON)
        entry = dict(ts=now().isoformat(timespec="seconds"), comp=g["comp"], year=g["year"], code=g["code"], home=g["home"], away=g["away"],
                     key=k, el=done * 10, score=score, alert=al, msg=[mid] if isinstance(mid, int) else [])
        g["sent"][k] = entry
        log.append(entry)
        save()


def result_message(g, entries, hs, as_):
    """Resultado, al acabar el partido, de cada aviso que saltó en él (los de cuarto se resuelven aparte)."""
    out = [f"🏁 RESULTADO FINAL\n{g['home']} {hs}-{as_} {g['away']}"]
    for e in entries:
        al = e["alert"]
        res = {str(p): ok for p, ok in avisos.settle(al, hs, as_).items()}
        if al["type"] == "total":
            got = f"Hubo {hs + as_} puntos entre los dos."
        else:
            team, m = (g["home"] if al["side"] > 0 else g["away"]), al["side"] * (hs - as_)
            got = f"{team} ganó por {m}." if m > 0 else f"{team} perdió por {-m}." if m < 0 else f"{team} empató."
        out += ["", f"{avisos.NAMES[al['type']].upper()} · aviso del minuto {e['el']:.0f} ({e['score']})",
                "Apostabas a: " + avisos.bet_of(al, g["home"], g["away"])["market"], "Lo que pasó: " + got]
        out += avisos.result_lines(al, res, g["home"], g["away"])
    return "\n".join(out)


def todays_games(year):
    out = []
    for comp in EURO:
        d = get(GAMES.format(c=comp, y=year))
        for g in (d or {}).get("data", []):
            if not g.get("utcDate") or g["local"]["club"].get("isVirtual") or g["road"]["club"].get("isVirtual"):
                continue
            start = dt.datetime.fromisoformat(g["utcDate"].replace("Z", "+00:00"))
            if abs((start - now()).total_seconds()) < 8 * 3600:
                out.append(dict(comp=comp, year=year, code=g["gameCode"], start=start, home=g["local"]["club"]["name"], away=g["road"]["club"]["name"],
                                hc=g["local"]["club"]["code"], ac=g["road"]["club"]["code"], done=bool(g.get("played")), sent={}))
    return out


def acb_games(con, year):
    """Partidos de Liga Endesa de hoy, del calendario guardado en la base (códigos de club de acb.com)."""
    out = []
    lo, hi = (now() - dt.timedelta(days=1)).date().isoformat(), (now() + dt.timedelta(days=1)).date().isoformat()
    for code, date, tm, ht, at, hc, ac, status in con.execute(
            "SELECT source_id, date, time, home_team, away_team, home_code, away_code, status FROM matches "
            "WHERE league = 'Liga Endesa' AND date BETWEEN ? AND ? AND time IS NOT NULL", (lo, hi)):
        start = dt.datetime.fromisoformat(f"{date}T{tm}:00+00:00")
        if abs((start - now()).total_seconds()) < 8 * 3600:
            out.append(dict(comp="A", year=year, code=int(code), start=start, home=ht, away=at, hc=hc, ac=ac, done=status == "played", sent={}))
    return out


def state_euro(g):
    """Estado de un partido de Euroliga/EuroCup con UNA sola lectura (las jugadas ya traen si está en juego y el cuarto).
    None si no se puede leer; si no, dict(live, final, el, A, B, hs, as_)."""
    d = get(LIVE + "PlaybyPlay", gamecode=g["code"], seasoncode=f"{g['comp']}{g['year']}")
    if not d:
        return None
    raw = [p for k in PERIODS for p in (d.get(k) or [])]
    plays = [dict(t=minute(p), team=(p.get("CODETEAM") or "").strip(), type=(p.get("PLAYTYPE") or "").strip(), clock=bool(str(p.get("MARKERTIME") or "").strip()))
             for p in raw]
    A, B = avisos.totals(plays, g["hc"])
    hs, as_ = A["pts"], B["pts"]
    quarters, done = [], 0                  # puntos de cada equipo por cuarto y cuántos cuartos han terminado ya
    for k in PERIODS[:4]:
        per = [dict(t=0, team=(p.get("CODETEAM") or "").strip(), type=(p.get("PLAYTYPE") or "").strip()) for p in (d.get(k) or [])]
        qa, qb = avisos.totals(per, g["hc"])
        quarters.append([qa["pts"], qb["pts"]])
        if done == len(quarters) - 1 and any(p["type"] in ("EP", "EG") for p in per):   # jugada «fin de periodo»
            done += 1
    if not d.get("Live"):
        ended = any(p["type"] == "EG" for p in plays) or (hs + as_ > 0 and now() > g["start"] + dt.timedelta(minutes=100))
        return dict(live=False, final=ended, hs=hs, as_=as_, quarters=quarters, done=4 if ended else done)
    q, timed = int(d.get("ActualQuarter") or 0), [p["t"] for p in plays if p["clock"]]
    if not 1 <= q <= 4 or not timed:       # prórroga o recién empezado: sin avisos
        return dict(live=True, final=False, el=None, hs=hs, as_=as_, quarters=quarters, done=done)
    el = min(q * 10, max((q - 1) * 10, max(timed)))    # minuto de la última jugada con reloj
    return dict(live=True, final=False, el=el, A=A, B=B, hs=hs, as_=as_, q=q, left=q * 10 - el, quarters=quarters, done=done)


def state_acb(g):
    """Lo mismo para la Liga Endesa, leyendo la ficha pública del partido en live.acb.com."""
    time.sleep(GAP)
    try:
        payload = acb.rsc_payload(http_get(acb.MATCH_URL.format(id=g["code"]), tries=2).text)
    except Exception:
        return None
    head = (acb.find_props(payload, "initialMatchHeader") or {}).get("initialMatchHeader")
    if not head:
        return None
    status, hs, as_ = str(head.get("status") or "").upper(), int(head.get("currentHomeScore") or 0), int(head.get("currentAwayScore") or 0)
    q = int(head.get("currentQuarter") or 0)
    # ¿Aplazado? Lo dice el estado de la ficha o una fecha que ya no es la del calendario de la mañana (aplazamientos
    # de días; el margen de 6 horas evita confundirse si la ficha diera la hora local en vez de la UTC).
    moved = None
    for k in ("startDateTime", "matchDate", "startDate", "date"):
        try:
            d = dt.datetime.fromisoformat(str(head.get(k)).replace("Z", "+00:00"))
        except ValueError:
            continue
        d = d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        if abs(d - g["start"]) > dt.timedelta(hours=6):
            moved = d
        break
    if hs + as_ == 0 and (moved or any(w in status for w in ("POSTPON", "SUSPEND", "CANCEL", "APLAZ"))):
        return dict(live=False, final=False, hs=0, as_=0, postponed="aplazado", new_start=moved)
    if status == "NOT_STARTED":
        return dict(live=False, final=False, hs=hs, as_=as_)
    quarters = [[x["home"], x["away"]] for x in sorted(head.get("quarterScores") or [], key=lambda x: x["quarter"])]
    try:
        m, s = str(head.get("timeLeft") or "0:0").split(":")[:2]
        el = (q - 1) * 10 + 10 - (int(m) + int(s) / 60)
    except ValueError:
        el = None
    stats = (acb.find_props(payload, "initialStatistics") or {}).get("initialStatistics") or {}
    home_id, tot = head["teams"]["home"]["id"], {}
    for i, tb in enumerate(stats.get("teamBoxscores") or []):
        t = next((p["stats"]["total"] for p in tb["statsByPeriods"] if p["quarter"] == 0), None)
        if t:
            n = lambda k: int(t.get(k) or 0)  # noqa: E731
            is_home = tb["team"]["id"] == home_id if tb.get("team") else i == 0
            tot[is_home] = dict(pts=n("points"), m2=n("twoPointersMade"), a2=n("twoPointersAttempted"), m3=n("threePointersMade"),
                                a3=n("threePointersAttempted"), mf=n("freeThrowsMade"), af=n("freeThrowsAttempted"),
                                # el resto solo lo usa la web (vista de detalle del partido)
                                oreb=n("offRebounds"), dreb=n("defRebounds"), ast=n("assists"), stl=n("steals"), tov=n("turnovers"),
                                blk=n("blocks"), pf=n("personalFouls"), val=n("rating"))
    both = dict(A=tot[True], B=tot[False]) if len(tot) == 2 else {}
    if status == "FINALIZED":     # terminado: se dejan las estadísticas finales para la web
        return dict(live=False, final=True, hs=hs, as_=as_, quarters=quarters, done=4, **both)
    if not 1 <= q <= 4:           # prórroga o entre estados: sin avisos
        return dict(live=False, final=False, hs=hs, as_=as_, quarters=quarters, done=min(4, len(quarters)))
    done = int(el // 10) if el is not None else 0     # al acabar un cuarto el reloj queda en 0:00 (o ya marca 10:00 del siguiente)
    if el is None or not both:
        print(f"  Liga Endesa {g['home']}: en juego pero sin datos completos (estado {status}, cuarto {q}, tiempo {head.get('timeLeft')!r})", flush=True)
        return dict(live=True, final=False, el=None, hs=hs, as_=as_, quarters=quarters, done=done)
    return dict(live=True, final=False, el=el, A=tot[True], B=tot[False], hs=hs, as_=as_, q=q, left=q * 10 - el, quarters=quarters, done=done)


def suspicious(g, st):
    """¿Lectura en la que no hay que fiarse? La fuente a veces da, unos segundos, las estadísticas de equipo vacías
    (sobre todo al cambiar de cuarto) o una copia vieja del partido (marcador o reloj hacia atrás). Con esas lecturas
    no se avisa de nada. Devuelve el motivo, o None si la lectura es buena."""
    A, B, el = st.get("A"), st.get("B"), st.get("el")
    if st["live"] and A and B:
        if el is not None and el >= 1 and A["pts"] + B["pts"] == 0:
            return "estadísticas vacías"
        if abs(A["pts"] - st["hs"]) > 4 or abs(B["pts"] - st["as_"]) > 4:     # las estadísticas no cuadran con el marcador
            return f"estadísticas {A['pts']}-{B['pts']} con marcador {st['hs']}-{st['as_']}"
    last = g.get("good")
    if not last:
        return None
    if st["hs"] < last["hs"] or st["as_"] < last["as_"]:
        return f"marcador hacia atrás ({last['hs']}-{last['as_']} → {st['hs']}-{st['as_']})"
    if el is not None and last.get("el") is not None and el < last["el"] - 0.3:
        return f"reloj hacia atrás (min {last['el']:.1f} → {el:.1f})"
    if A and B and last.get("A") and last.get("B") and any(
            T[k] < L[k] - (0 if k == "pts" else 2) for T, L in ((A, last["A"]), (B, last["B"])) for k in ("pts", "a2", "a3", "af")):
        return "estadísticas hacia atrás"
    return None


DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


NO_LINE = re.compile(r"\b(no\s*(aparece|sale|hay|esta|está|ofrece)|nada|ninguna)\b", re.I)
BUTTON = {"inline_keyboard": [[{"text": "🚫 No aparece en mi casa", "callback_data": "noaparece"},
                                {"text": "📊 Detalle", "url": "https://sergio20.github.io/sportsdb/en-vivo.html"}]]}
NUM = re.compile(r"[+-]?\d+(?:[.,]\d+)?")


def replies_step(log, save):
    """Respuestas a los avisos en Telegram: Sergio contesta a un aviso con la línea y la cuota que le ofrece su casa
    («+7,5 1,12») y se le dice al momento si tiene valor. Cada respuesta queda anotada en el aviso (banco de pruebas)."""
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    mark = next((e for e in log if "tg_offset" in e), None)
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10,
                         params={"offset": (mark or {}).get("tg_offset", 0), "timeout": 0,
                                 "allowed_updates": '["message","callback_query"]'})
        ups = r.json().get("result", []) if r.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return
    if not ups:
        return
    if not mark:
        mark = {"tg_offset": 0}
        log.append(mark)
    for u in ups:
        mark["tg_offset"] = u["update_id"] + 1
        cq = u.get("callback_query")
        if cq:                      # botón «No aparece en mi casa»
            try:
                button_press(cq, chat, log, token)
            except Exception as e:
                print(f"  botón de Telegram no atendido: {e}", flush=True)
            continue
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != str(chat) or not m.get("text"):
            continue                # solo se atiende a Sergio
        try:
            answer_reply(m, log)
        except Exception as e:      # una respuesta rara no puede parar la vigilancia
            print(f"  respuesta de Telegram no entendida: {e}", flush=True)
    save()


def answer_reply(m, log):
    to = (m.get("reply_to_message") or {}).get("message_id")
    e = next((x for x in log if "alert" in x and to in (x.get("msg") or [])), None) if to else None
    if not e:
        if to or NUM.search(m["text"]):
            send("Para valorar una cuota, RESPONDE directamente al mensaje del aviso (mantén pulsado el aviso → Responder) "
                 "con la línea y la cuota, por ejemplo: +7,5 1,12", reply_to=m["message_id"])
        return
    if NO_LINE.search(m["text"]) and len(NUM.findall(m["text"])) < 2:
        send(no_line(e), reply_to=m["message_id"])
        return
    nums = [float(x.replace(",", ".")) for x in NUM.findall(m["text"])]
    if len(nums) < 2:
        send("Necesito la línea y la cuota, por ejemplo: +7,5 1,12", reply_to=m["message_id"])
        return
    line, odds = nums[0], nums[1]
    al = (e.get("msg_alert") or {}).get(str(to)) or e["alert"]
    p = avisos.prob_of_line(al, line)
    if p is None or odds <= 1:
        send("No he podido valorar esa línea. Escríbela así: +7,5 1,12", reply_to=m["message_id"])
        return
    p = max(1.0, min(99.0, p))
    need, ev = 100 / p, (p / 100 * odds - 1) * 100
    b = avisos.bet_of(al, e["home"], e["away"])
    what = f"{b['team']} {avisos.fmt(line)}" if b["market"].startswith("HÁNDICAP") else f"{b['way'].capitalize()} {avisos.num(line)}"
    verdict = (f"✅ CON VALOR: por cada 100 € apostados, a la larga +{ev:.0f} €" if odds > need
               else f"❌ SIN VALOR: por cada 100 € apostados, a la larga {ev:.0f} €. No apuestes.")
    warn = "\n⚠️ Ojo: por debajo del 80 % de acierto. Lo acordado es apostar solo líneas seguras." if p < 80 else ""
    send(f"{what} a {avisos.num(odds, 2)}\nAcierta {p:.0f} % → cuota mínima {avisos.num(need, 2)}\n{verdict}{warn}", reply_to=m["message_id"])
    e.setdefault("casa", []).append(dict(ts=now().isoformat(timespec="seconds"), line=line, odds=odds, p=round(p, 1)))


def no_line(e):
    """Anota que la casa no ofrecía ninguna de nuestras líneas y lo confirma."""
    e.setdefault("casa", []).append(dict(ts=now().isoformat(timespec="seconds"), none=True))
    n = sum(1 for x in e["casa"] if x.get("none"))
    return ("📝 Anotado: tu casa no ofrecía ninguna de nuestras líneas" + (f" ({n}.ª vez en este aviso)" if n > 1 else "") + ".\n"
            "Si ves otra línea (aunque sea más ajustada), respóndeme al aviso con ella y su cuota y te digo cuánto acierta.")


def button_press(cq, chat, log, token):
    msg = cq.get("message") or {}
    if str((msg.get("chat") or {}).get("id")) != str(chat):
        return
    requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", timeout=10,
                  json={"callback_query_id": cq["id"], "text": "Anotado: no aparece en tu casa"})
    e = next((x for x in log if "alert" in x and msg.get("message_id") in (x.get("msg") or [])), None)
    if e:
        send(no_line(e), reply_to=msg["message_id"])


def nap(seconds, log, save):
    """Espera sin partidos, pero atendiendo las respuestas de Telegram cada 20 segundos."""
    end = time.time() + seconds
    while time.time() < end:
        replies_step(log, save)
        time.sleep(max(0, min(20, end - time.time())))


def gap_step(gap, g, st, games):
    """Si la tanda anterior acabó hace más de 10 minutos y hay partidos en juego, ha habido un rato sin vigilar
    (GitHub lanzó tarde esta tanda, o la anterior se cayó). Se avisa por Telegram, una vez, para que se sepa."""
    if gap["told"] or not gap["since"] or not st["live"]:
        return
    gap["told"] = True          # se decide con la primera lectura en juego de la tanda, y ya no se vuelve a mirar
    if now() - gap["since"] < dt.timedelta(minutes=10) or g["start"] > now() - dt.timedelta(minutes=5):
        return
    live = [x for x in games if (x.get("good") or {}).get("live")]
    send(f"⚠️ HE ESTADO SIN VIGILAR de las {local(gap['since']):%H:%M} a las {local():%H:%M}\n"
         "GitHub no lanzó a tiempo la tanda del vigilante (o la anterior se cayó). Ya estoy vigilando otra vez.\n"
         "Partidos en juego ahora: " + ", ".join(f"{x['home']} – {x['away']}" for x in live)
         + ".\nLos avisos de ese rato no se han podido mandar; los de cuarto anormal de los cuartos ya acabados tampoco.")


def unstarted_step(g, st, log, save):
    """Partido que no arranca a su hora. Avisa por Telegram una sola vez: «aplazado» si la ficha oficial lo dice y
    «¿aplazado?» si LATE minutos después sigue sin empezar (la fuente no siempre lo marca). Si luego arranca, avisa
    también. Devuelve True si en esta vuelta no hay nada más que hacer con el partido."""
    base = f"{g['comp']}|{g['year']}|{g['code']}"
    title, late = f"{g['home']} – {g['away']} ({COMP[g['comp']]})", (now() - g["start"]).total_seconds() / 60

    def tell(kind, text):
        if f"{base}|{kind}" in g["told"]:
            return
        send(text)
        g["told"].add(f"{base}|{kind}")
        log.append(dict(aplazado=f"{base}|{kind}", ts=now().isoformat(timespec="seconds")))
        save()

    if st["live"] or st["final"] or st["hs"] + st["as_"] > 0 or any(sum(q) for q in st.get("quarters") or []):
        if f"{base}|sin empezar" in g["told"]:
            tell("empezado", f"▶ Ya ha empezado {title}, con retraso: lo vigilo con normalidad.")
        return False
    if st.get("postponed"):
        new = st.get("new_start")
        tell("aplazado", f"🚫 PARTIDO APLAZADO\n{title}\nEstaba previsto hoy a las {local(g['start']):%H:%M}."
             + (f"\nNueva fecha según la ficha oficial: {DIAS[local(new).weekday()]} {local(new):%d/%m a las %H:%M}." if new
                else "\nLa ficha oficial todavía no da nueva fecha.")
             + "\nDejo de vigilarlo hoy.")
        g["done"] = True
        return True
    if late < LATE:
        return False
    st["postponed"] = "dudoso"
    tell("sin empezar", f"⏳ ¿PARTIDO APLAZADO?\n{title}\nDebía empezar a las {local(g['start']):%H:%M} y {late:.0f} minutos después la "
         "fuente oficial no lo da por empezado. Puede estar aplazado o con mucho retraso: compruébalo antes de apostar.\n"
         "Lo sigo mirando cada pocos minutos y te aviso si arranca.")
    g["slow_until"] = time.time() + 180          # sin prisa: una lectura cada 3 minutos
    if late >= GIVE_UP:
        g["done"] = True
    return True


def publish(snapshot):
    """Estado en directo de la Liga Endesa para la web (rama `vivo`, fichero vivo.json). El navegador no puede leer
    acb.com, así que lo lee de aquí. Solo dentro de GitHub (necesita su credencial); en local no hace nada."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    data = json.dumps(snapshot, ensure_ascii=False)
    if not token or not repo:
        return
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "vivo.json").write_text(data, encoding="utf-8")
        run = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True)  # noqa: E731
        run("init", "-q", "-b", "vivo")
        run("config", "user.name", "github-actions[bot]")
        run("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        run("add", "vivo.json")
        run("commit", "-q", "-m", "Liga Endesa en directo")
        r = run("push", "--force", f"https://x-access-token:{token}@github.com/{repo}.git", "vivo")
        if r.returncode != 0:
            print("  no se ha podido publicar el estado de la Liga Endesa para la web", flush=True)


def vivo_data(games, bases):
    out = []
    for g in games:
        if g["comp"] != "A":
            continue
        st = g.get("st") or dict(live=False, final=g["done"], hs=0, as_=0)     # aún sin leer: se anuncia con su hora
        bA, bB = bases["A"].get(g["hc"]), bases["A"].get(g["ac"])
        row = dict(code=g["code"], year=g["year"], hc=g["hc"], ac=g["ac"], home=g["home"], away=g["away"], start=g["start"].isoformat(),
                   live=st["live"], final=st["final"], hs=st["hs"], as_=st["as_"], q=st.get("q"), el=round(st["el"], 2) if st.get("el") is not None else None,
                   left=round(st["left"], 2) if st.get("left") is not None else None, quarters=st.get("quarters") or [],
                   A=st.get("A"), B=st.get("B"), alerts=[], postponed=st.get("postponed"),
                   new_start=st["new_start"].isoformat() if st.get("new_start") else None)
        if st.get("A") and bA and bB:
            pct = lambda m, a: round(100 * m / a, 1) if a else None  # noqa: E731
            row["shooting"] = [dict(p2=pct(t["m2"], t["a2"]), p3=pct(t["m3"], t["a3"]), ft=pct(t["mf"], t["af"]), m3=t["m3"], a3=t["a3"],
                                    u2=b["p2"], u3=b["p3"], uft=b["ft"]) for t, b in ((st["A"], bA), (st["B"], bB))]
        for al in g.get("live_alerts") or []:
            row["alerts"].append(dict(type=al["type"], level=al["level"], title=avisos.NAMES[al["type"]], text=avisos.describe(al, g["home"], g["away"])))
        out.append(row)
    # Avisos de cuarto anormal aún abiertos, de las tres ligas (la web no los calcula: los enseña tal cual)
    quarters = [dict(comp=COMP[g["comp"]], home=g["home"], away=g["away"], score=e["score"], title=avisos.name_of(e["alert"]),
                     text=avisos.describe(e["alert"], g["home"], g["away"]), ts=e["ts"])
                for g in games for e in g["sent"].values() if e["alert"]["type"] == "cuarto" and not e.get("closed")]
    return dict(updated=now().isoformat(timespec="seconds"), games=out, cuartos=quarters)


def watch(con, log_path, hours):
    year = current_season_start()
    deadline = now() + dt.timedelta(hours=hours)
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    bases = {"E": export_live.baseline(con), "A": export_live.baseline(con, leagues=("Liga Endesa",))}
    bases["U"] = bases["E"]
    games = todays_games(year) + acb_games(con, year)
    for g in games:   # lo ya avisado en una tanda anterior del mismo día no se repite
        g["sent"] = {e["key"]: e for e in log if "alert" in e and (e["comp"], e["year"], e["code"]) == (g["comp"], g["year"], g["code"])}
        g["told"] = {e["aplazado"] for e in log if "aplazado" in e}     # avisos de aplazamiento ya enviados
    save = lambda: log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")  # noqa: E731
    rules = {}
    try:
        hist_games, _, hist = analisis.walk(con)
        rules = analisis.quarter_rules(hist_games)       # líneas de los avisos de «cuarto anormal», con todo el histórico
        agenda(con, log, log_path, hours, hist_games, hist)
    except Exception as e:      # ni el mensaje previo ni las reglas de cuartos deben impedir la vigilancia
        print(f"  no se ha podido preparar el mensaje previo o las reglas de cuartos: {e}", flush=True)
    print(f"{len(games)} partidos alrededor de esta hora; vigilando hasta las {deadline:%H:%M} UTC como muy tarde", flush=True)
    for g in sorted(games, key=lambda g: g["start"]):
        print(f"  {g['start']:%H:%M} UTC · {COMP[g['comp']]} · {g['home']} - {g['away']}" + (" (ya jugado)" if g["done"] else ""), flush=True)
    loops, published, bg, was_open = 0, 0.0, None, False
    # ¿Cuándo acabó la tanda anterior? Si hubo un hueco y ahora hay partidos en juego, se avisa una vez (gap_step).
    last = max((e["tanda_fin"] for e in log if "tanda_fin" in e), default=None)
    gap = {"since": dt.datetime.fromisoformat(last) if last else None, "told": False}
    if any(g["comp"] == "A" for g in games):      # la web muestra los partidos de Liga Endesa del día desde el arranque
        publish(vivo_data(games, bases))
    while now() < deadline:
        pending = [g for g in games if not g["done"] and g["start"] < deadline]
        if not pending:     # nada que vigilar en esta tanda: espera a que acabe; la siguiente busca los partidos nuevos
            nap(max(1, min(300, (deadline - now()).total_seconds())), log, save)
            continue
        soon = [g for g in pending if g["start"] <= now() + dt.timedelta(minutes=3)]
        if not soon:
            nap(min(300, max(30, (min(g["start"] for g in pending) - now()).total_seconds() - 120)), log, save)
            continue
        t0 = time.time()
        loops += 1
        replies_step(log, save)
        if loops % 30 == 0 and pace["extra"] > 0:     # tras un rato sin cortes, se vuelve a acelerar
            pace["extra"] -= 5
        n_acb = sum(g["comp"] == "A" for g in soon)
        every = max(MIN_EVERY, PER_EURO * (len(soon) - n_acb), PER_ACB * n_acb) + pace["extra"]
        for g in soon:
            failed = False
            try:
                if g.get("slow_until", 0) > time.time():
                    continue
                st = (state_acb if g["comp"] == "A" else state_euro)(g)
                if not st:
                    continue
                if unstarted_step(g, st, log, save):
                    g["st"] = st
                    continue
                last = g.get("good") or {}
                if st["final"] and last.get("A") and not (st.get("A") or {}).get("pts"):     # al acabar, acb.com a veces vacía las
                    st["A"], st["B"] = last["A"], last["B"]                                # estadísticas: se dejan las últimas buenas
                why = suspicious(g, st)
                if why:
                    g["bad"] = g.get("bad", 0) + 1
                    print(f"  {now():%H:%M:%S} {g['home'][:14]}: lectura descartada ({why})", flush=True)
                    # Hacia atrás varias veces seguidas = corrección real del acta (p. ej. un triple que era de 2): se acepta.
                    # Estadísticas vacías o que no cuadran con el marcador: nunca.
                    if g["bad"] < 4 or "atrás" not in why:
                        continue
                g["bad"], g["good"] = 0, st
                gap_step(gap, g, st, games)
                if st["live"] and (st["hs"], st["as_"]) != (g.get("st") or {}).get("score"):   # rastro para medir la frescura de la fuente
                    print(f"  {now():%H:%M:%S} {g['home'][:14]} {st['hs']}-{st['as_']} {g['away'][:14]} · min {st.get('el') or 0:.1f}", flush=True)
                st["score"] = (st["hs"], st["as_"])
                g["st"], g["live_alerts"] = st, []
                try:
                    quarter_step(g, st, bases[g["comp"]], rules, log, save)
                except Exception as e:      # un fallo aquí no debe tumbar el resto de avisos
                    print(f"  aviso de cuarto: {e}", flush=True)
                if st["final"]:
                    g["done"] = True
                    mine = [e for e in g["sent"].values() if e["alert"]["type"] != "cuarto"]
                    if mine and not any(e.get("closed") for e in mine):
                        send(result_message(g, mine, st["hs"], st["as_"]))
                        for e in mine:
                            e["closed"] = True
                            e["res"] = {str(k): ok for k, ok in avisos.settle(e["alert"], st["hs"], st["as_"]).items()}
                            e["final"] = f"{st['hs']}-{st['as_']}"
                        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
                    continue
                base = bases[g["comp"]]
                el, bA, bB = st.get("el"), base.get(g["hc"]), base.get(g["ac"])
                if not st["live"] or el is None or not bA or not bB:
                    continue
                A, B = st["A"], st["B"]
                g["live_alerts"] = avisos.evaluate(el, A, B, bA, bB)
                for al in g["live_alerts"]:
                    k, old = avisos.key(al), g["sent"].get(avisos.key(al))
                    if old and not (old["alert"]["level"] == "moderado" and al["level"] == "fuerte" and not old.get("upgraded")):
                        continue
                    score = f"{A['pts']}-{B['pts']}"
                    mid = send(message(al, g, el, score), buttons=BUTTON)
                    if old:                 # pasa de moderado a fuerte: se avisa otra vez, pero cuenta el primero
                        old["upgraded"] = True
                        if isinstance(mid, int):        # su respuesta se valora con las líneas de ese mensaje
                            old.setdefault("msg", []).append(mid)
                            old.setdefault("msg_alert", {})[str(mid)] = al
                        continue
                    entry = dict(ts=now().isoformat(timespec="seconds"), comp=g["comp"], year=g["year"], code=g["code"], home=g["home"], away=g["away"],
                                 key=k, el=round(el, 1), score=score, alert=al, msg=[mid] if isinstance(mid, int) else [])
                    g["sent"][k] = entry
                    log.append(entry)
                    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception as e:      # un fallo con un partido no puede parar la vigilancia de los demás
                failed = True
                print(f"  {now():%H:%M:%S} {g['home'][:14]}: error al revisarlo ({type(e).__name__}: {e}); sigo", flush=True)
                g["errs"] = g.get("errs", 0) + 1
                if g["errs"] == 12 and not g.get("errs_told"):     # ~2-4 minutos seguidos sin poder leerlo: que se sepa
                    g["errs_told"] = True
                    send(f"⚠️ NO PUEDO LEER {g['home']} – {g['away']} ({COMP[g['comp']]})\nLa fuente devuelve datos que no entiendo "
                         "desde hace unos minutos. Sigo intentándolo; mientras tanto, de este partido no saldrán avisos.")
            finally:                    # (también cuando la vuelta acaba con continue)
                if not failed:
                    if g.get("errs_told"):
                        send(f"✅ Vuelvo a leer bien {g['home']} – {g['away']}.")
                        g["errs_told"] = False
                    g["errs"] = 0

        # La web va aparte, en segundo plano: nunca retrasa la siguiente revisión ni un aviso de Telegram
        open_q = any(e["alert"]["type"] == "cuarto" and not e.get("closed") for g in games for e in g["sent"].values())
        if time.time() - published >= PUBLISH_EVERY and (n_acb or open_q or was_open) and not (bg and bg.is_alive()):
            was_open = open_q       # una publicación más al cerrarse el último, para que desaparezca de la web
            bg = threading.Thread(target=publish, args=(vivo_data(games, bases),), daemon=True)
            bg.start()
            published = time.time()
        time.sleep(max(2, every - (time.time() - t0)))
    if any(g.get("st") for g in games):
        if bg:
            bg.join(30)
        publish(vivo_data(games, bases))      # deja publicado el estado final
    log.append(dict(tanda_fin=now().isoformat(timespec="seconds")))       # para detectar huecos entre tandas
    log[:] = [e for e in log if "tanda_fin" not in e] + [log[-1]]          # solo hace falta la última marca
    save()
    print(f"Fin de la vigilancia: {sum(len(g['sent']) for g in games)} avisos en total hoy", flush=True)
    # ¿Quedan partidos sin terminar o por empezar en las próximas horas? Entonces hace falta otra tanda ya.
    # (Un partido aplazado que nunca termina no debe encadenar tandas sin fin: solo cuentan los de las últimas 5 horas.)
    return any(not g["done"] and now() - dt.timedelta(hours=5) <= g["start"] <= now() + dt.timedelta(hours=4) for g in games)


def replay(base, season, code, el):
    """Ensayo con un partido ya jugado, parado en un minuto: manda los avisos que habría en ese momento."""
    comp, year = season[0], int(season[1:])
    h = get(LIVE + "Header", gamecode=code, seasoncode=season)
    plays = read_plays(comp, year, code)
    if not h or not plays:
        print("No se ha podido leer el partido.")
        return 1
    hc, ac = h["CodeTeamA"].strip(), h["CodeTeamB"].strip()
    g = dict(comp=comp, home=h["TeamA"].strip().title(), away=h["TeamB"].strip().title())
    A, B = avisos.totals(plays, hc, el)
    als = avisos.evaluate(el, A, B, base[hc], base[ac])
    if not als:
        print(f"En el minuto {el} no había ningún aviso ({A['pts']}-{B['pts']}).")
    for al in als:
        send("🧪 ENSAYO con un partido ya jugado (no es un aviso real)\n\n" + message(al, g, el, f"{A['pts']}-{B['pts']}")
             + "\n\n🧪 Prueba: el botón «No aparece» y las respuestas solo funcionan con avisos reales.", buttons=BUTTON)
    if als:
        send("ENSAYO\n" + result_message(g, [dict(alert=al, el=el, score=f"{A['pts']}-{B['pts']}") for al in als], int(h["ScoreA"]), int(h["ScoreB"])))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--log", default="avisos.json")
    ap.add_argument("--horas", type=float, default=4.5)
    ap.add_argument("--prueba", action="store_true")
    ap.add_argument("--repetir", nargs=3, metavar=("TEMPORADA", "PARTIDO", "MINUTO"))
    a = ap.parse_args()
    if a.prueba:
        ok = send("✅ SportsDB: los avisos por Telegram funcionan. Aquí llegarán los desfases, ritmos insostenibles, triples y totales de los partidos en directo.")
        return 0 if ok else 1
    con = sqlite3.connect(a.db)
    if a.repetir:
        return replay(export_live.baseline(con), a.repetir[0], int(a.repetir[1]), int(a.repetir[2]))
    return 3 if watch(con, Path(a.log), a.horas) else 0     # 3 = quedan partidos: el workflow lanza otra tanda


if __name__ == "__main__":
    sys.exit(main())
