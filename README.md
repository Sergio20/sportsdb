# Base de datos deportiva: LaLiga, Euroliga y Liga Endesa

Histórico por partido (resultado, parciales, estadísticas y cuotas cuando existen) en SQLite,
con scripts para mantenerlo al día y exportarlo a Excel.

## Uso

```bash
pip install requests openpyxl

python update_db.py                # actualiza la temporada en curso (incremental)
python update_db.py --excel        # igual, y regenera el Excel
python update_db.py --full         # reconstruye: 5 temporadas completas + la actual
python update_db.py --seasons 2023 2024 --leagues acb euroliga
python update_db.py --refresh      # vuelve a bajar estadísticas ya cargadas
python export_excel.py             # sólo exportar a data/deportes.xlsx
```

Relanzar el script es seguro: sólo descarga las fichas de partidos jugados que aún no
tienen estadísticas, y reintenta las que fallaron. La tabla `load_log` guarda cada ejecución.

## Fuentes

| Liga | Fuente | Qué aporta |
|---|---|---|
| LaLiga | football-data.co.uk (`mmz4281/{temporada}/SP1.csv`) | resultado, descanso, tiros, córners, faltas, tarjetas, xG (desde 2026-27) y cuotas |
| Euroliga | api-live.euroleague.net (`/v2/.../games` y `/games/{n}/stats`); alternativa live.euroleague.net/api/Boxscore | resultado, parciales, estadísticas de equipo y jugador |
| Liga Endesa | acb.com (calendario) y live.acb.com (ficha del partido) | resultado, parciales, estadísticas de equipo y jugador |

Sólo LaLiga tiene cuotas: las fuentes oficiales de baloncesto no las publican.

La ACB no ofrece API: los datos se leen del contenido incrustado en sus páginas. Si
rediseñan la web, `sportsdb/acb.py` es el fichero que habrá que adaptar.

## Tablas

- `matches`: un partido por fila (las tres ligas). `status`: played / scheduled / not_played.
- `periods`: parciales. Fútbol `1T`, `2T`; baloncesto `Q1`..`Q4`, `OT1`...
- `football_team_stats`: dos filas por partido (local y visitante).
- `basket_team_stats`, `basket_player_stats`: estadísticas de equipo y de jugador.
- `odds`: cuotas en formato largo (`bookmaker`, `market` 1X2/OU/AH, `selection`, `stage` pre/closing, `line`, `price`).
- `bookmakers`: nombres de las casas. `MAX` y `AVG` son el máximo y la media del mercado.
- Vistas `v_laliga` y `v_baloncesto`: una fila por partido con todo en columnas.

Horas: LaLiga en hora del Reino Unido (así las publica la fuente); baloncesto en UTC.

## Publicación (GitHub)

La web se publica sola con GitHub Pages. El código vive en la rama `main` y la base de datos,
comprimida, en la rama `data`. El workflow `.github/workflows/actualizar.yml` actualiza los datos
y republica la web cada día; también se puede lanzar a mano desde la pestaña Actions.

Para trabajar en local: descomprime `deportes_db.zip` (rama `data`) en una carpeta `data/`.

## Otras competiciones (para el cara a cara)

- **EuroCup** (`sportsdb/eurocup.py`): misma API oficial que la Euroliga, cinco temporadas con estadísticas.
- **Ligas nacionales y copas de los clubes de Euroliga** (`sportsdb/flashscore.py`): se leen de las
  páginas públicas de flashscore.es. Sólo resultado y parciales. De cada club, sus últimos ~40
  partidos; de cada liga nacional, los ~100 partidos más recientes de cada temporada. El resto del
  histórico no está disponible por esta vía; desde ahora se acumula con la actualización diaria.

## Partidos en directo

`SportsDB en vivo.html` se abre con doble clic en el navegador y lee él solo, cada 20 segundos,
los datos oficiales en directo de Euroliga y EuroCup (marcador, tiro, jugadores, jugadas).
`export_live.py` lo regenera a partir de `panel/en_vivo_plantilla.html` para actualizar los
valores habituales de cada equipo. La Liga Endesa no ofrece una fuente abierta en directo.
