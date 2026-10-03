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
NAMES = {"desfase": "Desfase", "ritmo": "Ritmo insostenible", "triples": "Triples insostenibles", "total": "Total desfasado",
         "cuarto": "Cuarto anormal"}
PLAYS = {"2FGM": ("m2", "a2", 2), "2FGA": (None, "a2", 0), "3FGM": ("m3", "a3", 3), "3FGA": (None, "a3", 0),
         "FTM": ("mf", "af", 1), "FTA": (None, "af", 0)}


def up_half(x):
    """La línea acabada en ,5 inmediatamente por encima (o igual): nunca hay empate ni devolución."""
    return math.ceil(x - 0.5) + 0.5


def down_half(x):
    return math.floor(x + 0.5) - 0.5


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


def quarter_alerts(done, quarters, bA, bB, rules):
    """Avisos de «cuarto anormal» al acabar el cuarto `done` (1, 2 o 3). quarters = [[local, visitante], ...] ya terminados;
    rules = analisis.quarter_rules(...). Cada aviso propone una apuesta para el periodo SIGUIENTE (cuarto done+1, o la
    segunda parte) con sus líneas del 80, 90 y 95 %."""
    out = []
    if not rules or done not in (1, 2, 3) or len(quarters) < done:
        return out
    h, a = quarters[done - 1]
    Q = (bA["pf"] - bA["pa"]) - (bB["pf"] - bB["pa"])
    exp_q = 10 * (K["home"] + K["q"] * Q)                   # diferencia esperable por cuarto para el local
    usual_q = (bA["tot"] + bB["tot"]) / 8 if bA.get("tot") and bB.get("tot") else None

    def add(rid, pred, target, side=None, **extra):
        r = rules.get(rid)
        if not r or pred is None:
            return
        if r["market"] == "hcap":       # línea L tal que (diferencia del equipo en el periodo + L) > 0
            lines = {int(p): up_half(-(pred + float(q))) for p, q in r["q"].items()}
        else:
            lines = {int(p): (down_half if r["over"] else up_half)(pred + float(q)) for p, q in r["q"].items()}
        out.append(dict(type="cuarto", sub=rid, market=r["market"], over=r["over"], side=side, done=done, target=target, level="fuerte",
                        lines=lines, fact=r["fact"], n=r["n"], **extra))

    for s, me, base in ((1, h, bA), (-1, a, bB)):
        if me <= 10:
            add("eq_frio", base["pf"] / 4, done + 1, s, pts=me, usual=round(base["pf"] / 4, 1))
        if me >= 30:
            add("eq_caliente", base["pf"] / 4, done + 1, s, pts=me, usual=round(base["pf"] / 4, 1))
        if s * (h - a) <= -10:
            add("paliza", s * exp_q, done + 1, s, pts=abs(h - a))
    if usual_q:
        if h + a <= 30:
            add("tot_frio", usual_q, done + 1, pts=h + a, usual=round(usual_q, 1))
        if h + a >= 54:
            add("tot_caliente", usual_q, done + 1, pts=h + a, usual=round(usual_q, 1))
        if done == 2:
            h1 = sum(x[0] + x[1] for x in quarters[:2])
            if h1 <= 66:
                add("mitad_fria", 2 * usual_q, "mitad", pts=h1, usual=round(2 * usual_q, 1))
            if h1 >= 100:
                add("mitad_caliente", 2 * usual_q, "mitad", pts=h1, usual=round(2 * usual_q, 1))
    return out


def settle_quarter(al, quarters):
    """Resultado de un aviso de cuarto cuando su periodo ya ha terminado; None si aún no. quarters = [[local, visitante], ...]."""
    need = 4 if al["target"] == "mitad" else int(al["target"])
    if len(quarters) < need:
        return None
    if al["target"] == "mitad":
        value = sum(x[0] + x[1] for x in quarters[2:4])
    else:
        h, a = quarters[need - 1]
        value = {"equipo": h if al["side"] == 1 else a, "total": h + a, "hcap": (h - a) * (al["side"] or 1)}[al["market"]]
    if al["market"] == "hcap":
        return {p: value + line > 0 for p, line in al["lines"].items()}, value
    return {p: (value > line) if al["over"] else (value < line) for p, line in al["lines"].items()}, value


def key(al):
    """Identidad de un aviso dentro de un partido (para no repetirlo)."""
    if al["type"] == "cuarto":
        return f"cuarto:{al['sub']}:{al['done']}:{al['side']}"
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


ORD = {1: "1.er", 2: "2.º", 3: "3.er", 4: "4.º"}
ICON = {95: "🟢", 90: "🟡", 80: "🟠"}      # de más segura a más ajustada
FACTS = {
    "eq_frio": "ese equipo anotó más en el cuarto siguiente", "eq_caliente": "ese equipo anotó menos en el cuarto siguiente",
    "tot_frio": "el cuarto siguiente tuvo más puntos", "tot_caliente": "el cuarto siguiente tuvo menos puntos",
    "paliza": "ese equipo no perdió el cuarto siguiente por más de 5", "mitad_fria": "la 2.ª parte tuvo más puntos",
    "mitad_caliente": "la 2.ª parte tuvo menos puntos",
}


def _scope(al):
    """En qué periodo se resuelve la apuesta, dicho de forma que no quepa duda."""
    if al["type"] != "cuarto":
        return "el PARTIDO ENTERO (resultado final)", "al final del partido"
    if al["target"] == "mitad":
        return "la 2.ª PARTE (3.er y 4.º cuarto juntos, sin contar la 1.ª parte)", "sumando solo el 3.er y el 4.º cuarto"
    o = ORD[int(al["target"])]
    return f"el {o} CUARTO (solo ese cuarto)", f"contando solo los puntos del {o} cuarto"


def bet_of(al, home, away):
    """La apuesta de un aviso, desmenuzada: mercado, sentido, cómo se gana y cada línea con su condición exacta."""
    scope, count = _scope(al)
    team = home if al.get("side") == 1 else away if al.get("side") == -1 else None
    market = "hcap" if al["type"] in ("desfase", "ritmo", "triples") else "total" if al["type"] == "total" else al["market"]
    order = sorted(al["lines"].items(), key=lambda kv: -int(kv[0]))          # primero la más segura
    if market == "hcap":
        thing = "el partido" if al["type"] != "cuarto" else "la 2.ª parte" if al["target"] == "mitad" else "ese cuarto"

        def cond(line):
            return (f"gana si {team} gana {thing}, o si lo pierde por {int(line - 0.5)} o menos" if line > 0
                    else f"gana solo si {team} gana {thing} por {int(-line + 0.5)} o más")
        return dict(market=f"HÁNDICAP de {team} en {scope}", way=None, team=team,
                    win=f"Con un hándicap positivo ganas si {team} gana {thing}, o si lo pierde por MENOS puntos que el hándicap, {count}.",
                    lines=[(int(p), f"{team} {fmt(line)}", cond(line)) for p, line in order])
    over = al["over"] if al["type"] == "cuarto" else not al["under"]
    who = f"PUNTOS DE {team}" if market == "equipo" else "PUNTOS ENTRE LOS DOS EQUIPOS"
    way = "MÁS DE" if over else "MENOS DE"
    subject = team if market == "equipo" else "entre los dos"
    lines = [(int(p), f"{way.capitalize()} {num(line)}",
              f"gana si {subject} {'suman' if market != 'equipo' else 'mete'} {int(line + 0.5)} o más" if over
              else f"gana si {subject} {'suman' if market != 'equipo' else 'mete'} {int(line - 0.5)} o menos") for p, line in order]
    return dict(market=f"{who} en {scope}", way=way, team=team, lines=lines,
                win=f"Ganas si {subject} {'suman' if market != 'equipo' else 'mete'} {'MÁS' if over else 'MENOS'} puntos que la línea, {count}."
                    + ("" if al["type"] == "cuarto" else " Nuestro cálculo no cuenta la prórroga."))


def what_happens(al, home, away):
    """Una o dos frases: qué está pasando en el partido para que salte el aviso."""
    team, rival = (home, away) if al.get("side") == 1 else (away, home)
    if al["type"] == "cuarto":
        q, u = ORD[al["done"]], num(al.get("usual", 0))
        return {
            "eq_frio": f"{team} solo ha metido {al['pts']} puntos en el {q} cuarto. Su media es {u} por cuarto.",
            "eq_caliente": f"{team} ha metido {al['pts']} puntos en el {q} cuarto. Su media es {u} por cuarto.",
            "tot_frio": f"Solo {al['pts']} puntos entre los dos en el {q} cuarto. Lo habitual son {u}.",
            "tot_caliente": f"{al['pts']} puntos entre los dos en el {q} cuarto. Lo habitual son {u}.",
            "paliza": f"{team} ha perdido el {q} cuarto por {al['pts']} puntos.",
            "mitad_fria": f"Primera parte con solo {al['pts']} puntos entre los dos. Lo habitual son {u} por mitad.",
            "mitad_caliente": f"Primera parte con {al['pts']} puntos entre los dos. Lo habitual son {u} por mitad.",
        }[al["sub"]] + f"\nEn el histórico ({al['n']} casos), el {num(al['fact'], 0)} % de las veces {FACTS[al['sub']]}."
    if al["type"] == "total":
        return (f"Al ritmo que llevan acabarían en {al['pace']} puntos entre los dos. Su total habitual es {al['usual']}.\n"
                "En el histórico, del exceso (o defecto) de ritmo solo se mantiene un 8 % en lo que queda de partido.")
    if al["type"] == "desfase":
        return (f"{team} es el favorito y va peor de lo previsto: {al['gap']} puntos del marcador se explican por un acierto fuera de lo normal "
                f"(él por debajo de lo suyo, {rival} por encima).")
    if al["type"] == "ritmo":
        return f"{rival} gana de {round(al['deficit'])} anotando a un ritmo de {al['pace']} puntos por partido. Su media es {al['usual']}."
    parts = []
    if al.get("hot"):
        h = al["hot"]
        parts.append(f"{rival} lleva {h['m']} de {h['a']} en triples ({100 * h['m'] / h['a']:.0f} %); su habitual es {h['usual']:.0f} %")
    if al.get("cold"):
        c = al["cold"]
        parts.append(f"{team} lleva {c['m']} de {c['a']} en triples ({100 * c['m'] / c['a']:.0f} %); su habitual es {c['usual']:.0f} %")
    return (f"{rival} gana de {round(al['deficit'])}. " + " y ".join(parts)
            + ".\nEn el histórico ese acierto vuelve a lo normal, pero la ventaja ya conseguida se mantiene en su mayor parte.")


def describe(al, home, away):
    """Cuerpo del aviso para Telegram y la web: qué pasa, qué apostar, cada línea con su condición y cómo se gana."""
    b = bet_of(al, home, away)
    out = ["QUÉ PASA", what_happens(al, home, away), "", "QUÉ APOSTAR", "Mercado: " + b["market"]]
    if b["way"]:
        out.append("Sentido: " + b["way"])
    out += ["", "ELIGE UNA LÍNEA (de más segura a más ajustada)"]
    for p, label, cond in b["lines"]:
        out += [f"{ICON[p]} {label}", f"     {cond}", f"     acierta {p} de cada 100 · apuesta solo si la cuota es {num(100 / p, 2)} o más"]
    out += ["", "CÓMO SE GANA", b["win"]]
    return "\n".join(out)


def result_lines(al, res, home, away):
    """Para el mensaje de resultado: cada línea con su marca y GANADA / PERDIDA."""
    labels = {p: label for p, label, _ in bet_of(al, home, away)["lines"]}
    return [f"{'✅' if res[k] else '❌'} {labels[int(k)]} (la del {int(k)} %): {'GANADA' if res[k] else 'PERDIDA'}"
            for k in sorted(res, key=lambda k: -int(k))]
