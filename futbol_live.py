"""Avisos de LaLiga en directo por Telegram: el favorito que va perdiendo (idea de Sergio).

Lo arranca vigilante.py en un hilo aparte (no frena el baloncesto). Los partidos y lo favorito que es cada equipo salen
de la tabla `futbol_prox` (cuotas antes del partido, football-data.co.uk); el marcador en directo, de API-Football
(clave en el secreto FUTBOL_API_KEY; el plan gratis da 100 consultas al día y solo el directo de la temporada actual).
Solo se consulta mientras hay en juego algún partido con favorito, como mucho cada POLL segundos, con una sola consulta
para todos los partidos en juego. Cada aviso trae lo que pasó en el histórico (futbol.py, desde 1995) en la misma situación.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
import unicodedata
from zoneinfo import ZoneInfo

import requests

import futbol

API = "https://v3.football.api-sports.io/fixtures"
LALIGA = 140
FAV = 0.55              # favorito: 55 % o más de ganar antes del partido
POLL = 300              # segundos entre consultas mientras hay un partido vigilado en juego
WINDOW = dt.timedelta(minutes=130)
RESERVE = 8             # consultas que se dejan sin gastar cada día
UK, MADRID = ZoneInfo("Europe/London"), ZoneInfo("Europe/Madrid")
BIG = {"Barcelona": "Barcelona", "Real Madrid": "Real Madrid", "Ath Madrid": "Atlético de Madrid", "Betis": "Real Betis"}
# Nombres de football-data que no se parecen al de API-Football
ALIAS = {"ath madrid": "atletico madrid", "ath bilbao": "athletic club", "sociedad": "real sociedad", "betis": "real betis",
         "celta": "celta vigo", "vallecano": "rayo vallecano", "espanol": "espanyol", "la coruna": "deportivo la coruna",
         "sp gijon": "sporting gijon", "alaves": "alaves"}
DROP = {"real", "club", "cf", "fc", "ud", "cd", "rcd", "sd", "de", "deportivo", "balompie", "sad"}


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = ALIAS.get(s, s)
    return {w for w in re.split(r"[^a-z0-9]+", s) if w and w not in DROP}


def same(a, b):
    """¿Mismo equipo? Los nombres de las dos fuentes difieren («Ath Madrid» / «Atletico Madrid»); como se exige que
    coincidan local y visitante a la vez, basta con que las palabras de uno estén dentro de las del otro."""
    x, y = norm(a), norm(b)
    return bool(x) and bool(y) and (x <= y or y <= x)


def tier_of(p):
    for name, lo, hi in futbol.TIERS:
        if lo <= p < hi:
            return name
    return None


class Watcher:
    def __init__(self, con, send, log, save, lock):
        self.send, self.log, self.save, self.lock = send, log, save, lock
        self.key = os.environ.get("FUTBOL_API_KEY")
        self.left, self.last_poll = None, 0.0
        self.games = []
        try:
            self.table = (futbol.build(con) or {}).get("table") or {}
        except Exception:
            self.table = {}
        try:
            rows = con.execute("SELECT date, time_uk, home, away, p_h, p_d, p_a FROM futbol_prox WHERE league = 'LaLiga'").fetchall()
        except Exception:
            rows = []
        for date, tm, home, away, ph, pd_, pa in rows:
            if ph is None or not tm:
                continue
            try:
                start = dt.datetime.fromisoformat(f"{date}T{tm}").replace(tzinfo=UK).astimezone(dt.timezone.utc)
            except ValueError:
                continue
            side = 1 if ph >= pa else -1
            p = max(ph, pa)
            fav = home if side == 1 else away
            if p >= FAV or fav in BIG:
                self.games.append(dict(start=start, home=home, away=away, side=side, p=p, fav=fav, dog=away if side == 1 else home))
        self.told = {e["futbol"] for e in log if "futbol" in e}

    def active(self, now):
        return [g for g in self.games if g["start"] - dt.timedelta(minutes=5) <= now <= g["start"] + WINDOW]

    def step(self, now):
        """Una vuelta: si hay partido vigilado en juego y toca, una consulta y los avisos que salgan."""
        if not self.key or not self.active(now) or time.time() - self.last_poll < POLL:
            return
        if self.left is not None and self.left <= RESERVE:
            return
        self.last_poll = time.time()
        try:
            r = requests.get(API, params={"live": "all"}, headers={"x-apisports-key": self.key}, timeout=20)
            self.left = int(r.headers.get("x-ratelimit-requests-remaining") or self.left or 100)
            live = [f for f in (r.json().get("response") or []) if f["league"]["id"] == LALIGA]
        except Exception as e:
            print(f"  fútbol: no se ha podido leer el directo ({type(e).__name__})", flush=True)
            return
        for g in self.active(now):
            f = next((f for f in live if same(g["home"], f["teams"]["home"]["name"]) and same(g["away"], f["teams"]["away"]["name"])), None)
            if f:
                self.check(g, f)

    def check(self, g, f):
        st, el = f["fixture"]["status"]["short"], f["fixture"]["status"].get("elapsed") or 0
        hg, ag = f["goals"]["home"] or 0, f["goals"]["away"] or 0
        diff = g["side"] * (hg - ag)            # a favor del favorito
        gid = f"{f['fixture']['id']}"
        if st in ("1H", "HT") and diff < 0:     # un aviso al empezar a perder y otro al descanso, nada más
            key = f"{gid}|{'HT' if st == 'HT' else '1H'}"
            if key not in self.told:
                self.tell(key, self.message(g, hg, ag, diff, el, st == "HT"))

    def message(self, g, hg, ag, diff, el, ht):
        when = "AL DESCANSO" if ht else f"EN EL MINUTO {el}"
        state = 0 if diff <= -2 else 1          # fila de la chuleta: pierde por 2 o más / pierde por 1
        head = (f"⚽ {g['fav'].upper()} PIERDE {when} · LaLiga\n{g['home']} {hg}-{ag} {g['away']}\n"
                f"Antes del partido: {g['fav']} {round(100 * g['p'])} % de ganar · empezó {local(g['start'])}")
        lines = []
        for group in ([BIG[g["fav"]]] if g["fav"] in BIG else []) + [tier_of(g["p"])]:
            t = self.table.get(f"Solo LaLiga|{group}") or self.table.get(f"Todas las ligas|{group}")
            s = t and t["by"][state]
            if not s or s.get("n", 0) < 15:
                continue
            lines.append(f"\nHistórico: {group}, perdiendo por {'2 o más' if state == 0 else '1'} al descanso ({s['n']} casos):")
            for key, label in (("dc1x", f"{g['fav']} no pierde (1X)"), ("dcx2", f"{g['dog']} no pierde (X2)"),
                               ("score2", f"{g['fav']} marca en la 2.ª parte"), ("ah15", f"{g['fav']} +1,5 (pierde como mucho por 1)")):
                v = s.get(key)
                if v and v["pct"] > 0:
                    lines.append(f"· {label}: {v['pct']:.0f} % → vale a {100 / v['pct']:.2f} o más".replace(".", ","))
            break
        note = "" if ht else "\n(El histórico es con el marcador al descanso; un gol pronto deja más tiempo para remontar.)"
        return head + "\n".join(lines) + note + "\n\nSi la cuota de tu casa está por encima, tiene valor. Detalle: https://sergio20.github.io/sportsdb/futbol.html"

    def tell(self, key, text):
        self.send(text)
        with self.lock:
            self.told.add(key)
            self.log.append(dict(futbol=key, ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        self.save()


def local(t):
    return t.astimezone(MADRID).strftime("%d/%m %H:%M")


def listen(watcher, stop):
    """Hilo del fútbol: una vuelta por minuto (la consulta real va limitada por POLL)."""
    while not stop.is_set():
        try:
            watcher.step(dt.datetime.now(dt.timezone.utc))
        except Exception as e:      # el fútbol nunca puede tumbar al vigilante de baloncesto
            print(f"  fútbol: {type(e).__name__}: {e}", flush=True)
        stop.wait(60)
