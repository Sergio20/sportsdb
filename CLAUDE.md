# SportsDB — contexto del proyecto

Aplicativo personal de Sergio (habla español; responde siempre en español, sin jerga técnica).
Base de datos deportiva + web de análisis orientada a apuestas, publicada con GitHub Pages.

## Cómo funciona

- **Rama `main`**: código. Cada push vuelve a publicar la web.
- **Rama `data`**: un único commit con `deportes_db.zip` (la base SQLite `deportes.db` comprimida).
  El workflow la reescribe cada día con `--force`; no tiene historial a propósito.
- **`.github/workflows/actualizar.yml`**: cada día a las 06:10 UTC descarga la base de la rama
  `data`, ejecuta `update_db.py`, comprueba (`scripts/check_db.py`), construye la web
  (`build_site.py` → `_site/`), la publica en Pages y guarda la base en `data`.
  Un push a `main` solo reconstruye y publica (no descarga datos ni toca `data`).

## Piezas

- `sportsdb/`: cargadores. `laliga.py` (football-data.co.uk, con cuotas), `euroleague.py` y
  `eurocup.py` (API oficial), `acb.py` (acb.com), `flashscore.py` (ligas nacionales y copas de los
  clubes de Euroliga; solo páginas públicas — NO usar el feed interno de Flashscore).
- `update_db.py`: actualización incremental de la temporada en curso (`--full` reconstruye todo).
- `build_halves.py`: tiro por mitades (tabla `basket_half_stats`), lo llama `update_db.py`.
- `export_panel.py` → `data.json` del panel. `export_live.py` → página en vivo con los valores
  habituales de cada equipo incrustados. `export_excel.py` → Excel (no se usa en la web).
- `panel/index.html`: el panel (pestañas Resultados, Rachas, Equipos, Tiro, Cara a cara, Cuotas,
  Simulador). Es un fragmento sin `<html>`: `build_site.py` le pone la cabecera.
- `panel/en_vivo_plantilla.html`: panel en directo de Euroliga/EuroCup. Lee en el navegador
  `live.euroleague.net/api/*` cada 5 s. Incluye el modelo de margen restante (constantes `K`,
  ajustadas con 2.660 partidos) y un registro de apuestas guardado en `localStorage`.
  Arriba, «Desfases ahora» revisa cada 20 s todos los partidos en directo de las dos competiciones
  y avisa cuando un favorito claro va peor de lo previsto por un acierto anormal (umbrales `FAV`,
  `GAP`, `Z`). Para probarlo sin partidos en directo: `en-vivo.html?demo=25` repite la última
  jornada jugada parada en el minuto 25.

## Reglas

- No hay cuotas de baloncesto (ninguna fuente gratuita). Solo LaLiga tiene cuotas.
- Ningún dato personal en el repositorio: es público.
- Antes de dar algo por hecho, probarlo: `python build_site.py` y abrir `_site/` en un navegador.
- Sergio valora la franqueza: decir lo que no se ha podido verificar y no prometer certezas en apuestas.

## Pendiente

- Calendario de LaLiga (la fuente solo trae partidos jugados).
- Histórico completo de ligas nacionales extranjeras (Flashscore solo da lo reciente).
- Validar el modelo en vivo contra cuotas reales con el registro de apuestas.
- El «favorito» del panel en vivo sale de la diferencia de puntos de los últimos 30 partidos sin
  distinguir competición: un equipo que viene de la EuroCup queda sobrevalorado en Euroliga.
- Comprobar con el histórico cuántas veces el aviso de desfase acaba cubriendo el hándicap justo.
