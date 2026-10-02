#!/usr/bin/env python3
"""Comprobaciones de la base de datos para la actualización automática.

    check_db.py DB --save FICHERO      guarda el recuento de partidos por liga
    check_db.py DB --compare FICHERO   falla si la base está dañada o ha perdido partidos
    check_db.py DB --checkpoint        vuelca el WAL para poder comprimir el fichero
"""
import json
import sqlite3
import sys

MAIN = ("LaLiga", "Euroliga", "Liga Endesa", "EuroCup")


def counts(con):
    return {lg: [n, p] for lg, n, p in con.execute(
        "SELECT league, COUNT(*), SUM(status='played') FROM matches GROUP BY league")}


def main():
    db, mode = sys.argv[1], sys.argv[2]
    con = sqlite3.connect(db)
    if mode == "--checkpoint":
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return 0
    if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        print("ERROR: la base de datos está dañada")
        return 1
    now = counts(con)
    if mode == "--save":
        json.dump(now, open(sys.argv[3], "w"))
        return 0
    before = json.load(open(sys.argv[3]))
    bad = [lg for lg in MAIN if lg in before and (now.get(lg, [0, 0])[0] < before[lg][0] or now.get(lg, [0, 0])[1] < before[lg][1])]
    for lg in MAIN:
        b, n = before.get(lg, [0, 0]), now.get(lg, [0, 0])
        print(f"{lg}: {n[0]} partidos ({n[1]} jugados), {n[1] - b[1]:+d} jugados nuevos")
    if bad:
        print("ERROR: han desaparecido partidos de: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
