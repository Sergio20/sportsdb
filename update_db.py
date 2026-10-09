#!/usr/bin/env python3
"""Crea o actualiza la base de datos deportiva (LaLiga, Euroliga, Liga Endesa).

Ejemplos:
    python update_db.py                 # actualización incremental de la temporada en curso
    python update_db.py --full          # histórico: 5 temporadas completas + la actual
    python update_db.py --seasons 2023 2024 --leagues acb
    python update_db.py --refresh       # vuelve a descargar estadísticas ya cargadas
    python update_db.py --excel         # y al terminar regenera el Excel

Sólo se descargan las fichas de partidos jugados que aún no tienen estadísticas,
así que relanzarlo es barato y sirve también para reintentar los que fallaron.
"""
from __future__ import annotations

import argparse
import sys
import time

from sportsdb import acb, eurocup, euroleague, flashscore, futbol_hist, laliga
from sportsdb.common import DEFAULT_DB, connect, current_season_start

LEAGUES = {"laliga": laliga, "euroliga": euroleague, "acb": acb, "eurocup": eurocup,
           "otras": flashscore, "futbol": futbol_hist}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB), help="ruta del fichero SQLite")
    ap.add_argument("--leagues", nargs="+", choices=list(LEAGUES), default=list(LEAGUES))
    ap.add_argument("--seasons", nargs="+", type=int, metavar="AÑO",
                    help="año de inicio de cada temporada (2024 = 2024-25)")
    ap.add_argument("--full", action="store_true", help="últimas 5 temporadas completas + la actual")
    ap.add_argument("--refresh", action="store_true", help="recargar estadísticas ya descargadas")
    ap.add_argument("--excel", action="store_true", help="exportar a Excel al terminar")
    a = ap.parse_args()

    cur = current_season_start()
    seasons = a.seasons or (list(range(cur - 5, cur + 1)) if a.full else [cur])
    con = connect(a.db)
    failed = []
    for name in a.leagues:
        t0 = time.time()
        try:
            LEAGUES[name].update(con, seasons, refresh=a.refresh)
        except Exception as e:  # una liga caída no debe impedir actualizar las otras
            con.rollback()
            failed.append(name)
            print(f"[{name}] ERROR: {e}", file=sys.stderr)
        print(f"[{name}] hecho en {time.time() - t0:.0f}s", flush=True)
    con.close()
    try:  # tiro por mitades de Euroliga y EuroCup (para el análisis de remontadas)
        import build_halves
        build_halves.main(a.db)
    except Exception as e:
        print(f"[mitades] ERROR: {e}", file=sys.stderr)
    try:  # marcador jugada a jugada (para el estudio de entrada y salida); pocos por vez: la fuente corta si se abusa
        import build_timeline
        build_timeline.main(a.db, limit=60)
    except Exception as e:
        print(f"[jugadas] ERROR: {e}", file=sys.stderr)
    if a.excel:
        import export_excel
        export_excel.export(a.db)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
