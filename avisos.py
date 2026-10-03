#!/usr/bin/env python3
"""Reglas de los avisos en directo, en Python (las mismas que panel/en_vivo_plantilla.html).

Las usan el vigilante que envía los avisos por Telegram (vigilante.py) y el repaso de partidos ya
jugados (scripts/repaso.py). Si se cambia un umbral aquí, cambiarlo también en la plantilla en vivo.

Cada aviso propone una apuesta y sus LÍNEAS DE SEGURIDAD: las que, según el modelo, se aciertan el
80, 90 y 95 % de las veces (comprobado con el histórico en analisis.py). No se da «hándicap justo».
"""
from __future__ import annotations

import math

K = dict(home=0.10, q=0.021, m=-0.010, sd=2.1)      # margen: lo que queda de partido
KT = dict(a=1.02, c=0.08, sd=2.48)                  # total de puntos
FAV, GAP, Z, MIN_EL = 5, 6, 1.5, 8                  # desfase
LEAD, PACE_EXTRA, LEVEL_GAP, MIN_EL_PACE = 10, 20, 3, 6   # ritmo insostenible
TOTAL_GAP = 18                                      # total desfasado
T3_ATT, T3_HOT, T3_COLD, T3_LEAD = 8, 1.5, -2.0, 8  # triples: intentos mínimos, z caliente, z frío, ventaja mínima
SAFE = ((80, 0.8416), (90, 1.2816), (95, 1.6449))
NAMES = {"desfase": "Desfase", "ritmo": "Ritmo insostenible", "triples": "Triples insostenibles", "total": "Total desfasado"}
PLAYS = {"2FGM": ("m2", "a2", 2), "2FGA": (None, "a2", 0), "3FGM": ("m3", "a3", 3), "3FGA": (None, "a3", 0),
         "FTM": ("mf", "af", 1), "FTA": (None, "af", 0)}


def up_half(x):
    return math.ceil(x * 2) / 2


def down_half(x):
    return math.floor(x * 2) / 2


def totals(plays, code_a, upto=999):
    """Tiros y puntos de cada equipo hasta el minuto `upto`. plays = [{t, team, type}]."""
    mk = lambda: dict(pts=0, m2=0, a2=0, m3=0, a3=0, mf=0, af=0)  # noqa: E731
    A, B = mk(), mk()
    for p in plays:
        k = PLAYS.get(p["type"])
        if not k or p["t"] > upto or not p["team"]:
            continue
        o = A if p["team"] == code_a else B
        o[k[1]] += 1
        if k[0]:
            o[k[0]] += 1
            o["pts"] += k[2]
    return A, B


def _xp(t, b):
    return 2 * t["a2"] * b["p2"] / 100 + 3 * t["a3"] * b["p3"] / 100 + t["af"] * b["ft"] / 100


def _luck_var(t, b):
    v = lambda a, p, w: w * a * p / 100 * (1 - p / 100)  # noqa: E731
    return v(t["a2"], b["p2"], 4) + v(t["a3"], b["p3"], 9) + v(t["af"], b["ft"], 1)


def _z3(t, b):
    p = b["p3"] / 100
    return (t["m3"] - t["a3"] * p) / math.sqrt(t["a3"] * p * (1 - p)) if t["a3"] >= T3_ATT and 0 < p < 1 else 0.0


def evaluate(el, A, B, bA, bB):
    """Avisos activos en el minuto `el` (A = local). Cada aviso lleva `side` (1 = apostar al local con hándicap
    positivo, -1 = al visitante) y `lines` {80: hándicap, 90: ..., 95: ...}; los de total, `under` y líneas de total."""
    out = []
    r = 40 - el
    if r <= 0 or el <= 0:
        return out
    Q = (bA["pf"] - bA["pa"]) - (bB["pf"] - bB["pa"])
    M = A["pts"] - B["pts"]
    mean, sd = M + r * (K["home"] + K["q"] * Q + K["m"] * M), K["sd"] * math.sqrt(r)
    pre = 40 * (K["home"] + K["q"] * Q)

    def hcap(kind, s, level, **extra):
        fr = -s * mean          # hándicap con el que la apuesta al lado s acierta 1 de cada 2
        out.append(dict(type=kind, side=s, level=level, deficit=-s * M, lines={p: up_half(fr + z * sd) for p, z in SAFE}, **extra))

    # Desfase: favorito claro por debajo de lo previsto por un acierto anormal de los dos equipos
    if el >= MIN_EL and abs(pre) >= FAV:
        s = 1 if pre >= 0 else -1
        lA, lB = A["pts"] - _xp(A, bA), B["pts"] - _xp(B, bB)
        gap = (lB - lA) if s > 0 else (lA - lB)
        v = math.sqrt(_luck_var(A, bA) + _luck_var(B, bB))
        if gap >= GAP and v > 0 and gap / v >= Z and s * M < abs(pre) * el / 40:
            hcap("desfase", s, "fuerte" if gap / v >= 2.2 and gap >= 9 else "moderado", gap=round(gap))
    # Ritmo insostenible: gana de 10+ anotando muy por encima de su media ante un rival de su nivel o mejor
    if el >= MIN_EL_PACE and abs(M) >= LEAD:
        s = 1 if M > 0 else -1
        lead, base = (A, bA) if s > 0 else (B, bB)
        pace = lead["pts"] * 40 / el
        if pace - base["pf"] >= PACE_EXTRA and -s * pre >= -LEVEL_GAP:
            hcap("ritmo", -s, "fuerte" if pace - base["pf"] >= 35 or abs(M) >= 18 else "moderado", pace=round(pace), usual=round(base["pf"]))
    # Triples insostenibles: el que gana lo hace con un triple disparado, o el que pierde con un triple hundido
    if el >= MIN_EL:
        zA, zB = _z3(A, bA), _z3(B, bB)
        for s, z_me, z_opp, me, opp, b_me, b_opp in ((1, zA, zB, A, B, bA, bB), (-1, zB, zA, B, A, bB, bA)):
            hot, cold = z_opp >= T3_HOT, z_me <= T3_COLD
            if -s * M >= T3_LEAD and (hot or cold) and not any(a["type"] in ("desfase", "ritmo") and a["side"] == s for a in out):
                hcap("triples", s, "fuerte" if (hot and cold) or z_opp >= 2.2 else "moderado",
                     hot=dict(m=opp["m3"], a=opp["a3"], usual=b_opp["p3"]) if hot else None,
                     cold=dict(m=me["m3"], a=me["a3"], usual=b_me["p3"]) if cold else None)
    # Total desfasado
    if 8 <= el <= 32 and bA.get("tot") and bB.get("tot"):
        usual, cur = (bA["tot"] + bB["tot"]) / 2, A["pts"] + B["pts"]
        tmean = cur + r * (KT["a"] * usual / 40 + KT["c"] * (cur / el - usual / 40))
        tsd, pace = KT["sd"] * math.sqrt(r), cur * 40 / el
        if abs(pace - tmean) >= TOTAL_GAP:
            under = pace > tmean
            out.append(dict(type="total", under=under, level="fuerte" if abs(pace - tmean) >= 28 else "moderado", pace=round(pace), usual=round(usual),
                            lines={p: up_half(tmean + z * tsd) if under else down_half(tmean - z * tsd) for p, z in SAFE}))
    return out


def key(al):
    """Identidad de un aviso dentro de un partido (para no repetirlo)."""
    return al["type"] + ":" + ("u" if al.get("under") else "o" if al["type"] == "total" else str(al["side"]))


def settle(al, hs, as_, reg_total=None):
    """Con el resultado final: {80: True/False, 90: ..., 95: ...}."""
    if al["type"] == "total":
        t = reg_total if reg_total is not None else hs + as_
        return {p: (t < line) if al["under"] else (t > line) for p, line in al["lines"].items()}
    m = al["side"] * (hs - as_)
    return {p: m + line > 0 for p, line in al["lines"].items()}


def fmt(x):
    return f"{x:+.1f}".replace(".", ",").replace("-", "−")


def num(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


def describe(al, home, away):
    """Texto del aviso para Telegram (sin el encabezado del partido)."""
    if al["type"] == "total":
        way = "MENOS de" if al["under"] else "MÁS de"
        head = f"Van camino de {al['pace']} puntos; el habitual de los dos es {al['usual']}. Del exceso de ritmo solo se mantiene un 8 %."
        lines = [f"  {p} %: {way} {num(line)}  (cuota mínima {num(100 / p, 2)})" for p, line in al["lines"].items()]
        return head + "\nApuesta: total de puntos\n" + "\n".join(lines)
    team, rival = (home, away) if al["side"] > 0 else (away, home)
    if al["type"] == "desfase":
        head = f"{team} es favorito y va peor de lo previsto: {al['gap']} puntos del marcador vienen de un acierto fuera de lo normal."
    elif al["type"] == "ritmo":
        head = f"{rival} gana de {round(al['deficit'])} anotando a ritmo de {al['pace']} puntos (su media es {al['usual']})."
    else:
        parts = []
        if al.get("hot"):
            h = al["hot"]
            parts.append(f"{rival} lleva {h['m']}/{h['a']} en triples ({100 * h['m'] / h['a']:.0f} %; su habitual es {h['usual']:.0f} %)")
        if al.get("cold"):
            c = al["cold"]
            parts.append(f"{team} lleva {c['m']}/{c['a']} en triples ({100 * c['m'] / c['a']:.0f} %; su habitual es {c['usual']:.0f} %)")
        head = " y ".join(parts) + ". En el histórico ese acierto vuelve a lo normal, aunque la ventaja ya conseguida se mantiene en su mayor parte."
    lines = [f"  {p} %: {team} {fmt(line)}  (cuota mínima {num(100 / p, 2)})" for p, line in al["lines"].items()]
    return head + f"\nApuesta: hándicap positivo de {team}\n" + "\n".join(lines)
