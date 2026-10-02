"""EuroCup: misma API oficial que la Euroliga (competición "U")."""
from __future__ import annotations

from . import euroleague

LEAGUE = "EuroCup"


def update(con, seasons, refresh=False, log=print, workers=6):
    euroleague.update(con, seasons, refresh=refresh, log=log, workers=workers,
                      comp="U", league=LEAGUE, prefix="EC")
