# SportsDB — contexto del proyecto

Aplicativo personal de Sergio (habla español; responde siempre en español, sin jerga técnica).
Base de datos deportiva + web de análisis orientada a apuestas, publicada con GitHub Pages.

## Cómo funciona

- **Rama `main`**: código. Cada push vuelve a publicar la web.
- **Rama `data`**: un único commit con `deportes_db.zip` (la base SQLite `deportes.db` comprimida).
  El workflow la reescribe cada día con `--force`; no tiene historial a propósito.
- **`.github/workflows/actualizar.yml`**: cada día a las 04:10 UTC (06:10 en Madrid en verano) descarga la base de la rama
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
  jornada jugada parada en el minuto 25. «Contexto del partido» muestra el nivel de cada equipo,
  si es recién llegado a la Euroliga y sus jugadores clave (marca los que no han aparecido en
  ninguna jugada pasados 10 minutos). Los datos los incrusta `export_live.py`.
  Segundo aviso, «Ritmo insostenible»: gana de 10+ anotando 20+ puntos por encima de su media ante
  un rival de su nivel (umbrales `LEAD`, `PACE_EXTRA`, `LEVEL_GAP`). Cada aviso trae «En el
  histórico» con los casos parecidos (`HISTORY`, de `scripts/remontadas.py`). En el registro de
  apuestas, la columna «Ahora» da para cada apuesta abierta la probabilidad actual y el «cierre
  justo» (importe × cuota × probabilidad) para decidir si aceptar el cash out de la casa.
  La fuente corta (error 429) si se le pide mucho: el escaneo lee como mucho 3 partidos por vuelta
  y cada uno cada 45 s (`SCAN_MAX`, `SCAN_EVERY`); tras un fallo la página espera 30 s.
- `scripts/cronologia.py E2026 22 26`: un partido minuto a minuto (marcador, ritmo, máxima ventaja).
- `scripts/remontadas.py`: estudio con el histórico de qué pasa cuando el favorito va perdiendo
  al final de cada cuarto (cuánto recupera, cuántas veces cubre +2,5…+8,5) y cuánto bajan los
  equipos que suben de EuroCup a Euroliga (de ahí `EUROCUP_GAP` en `export_live.py`).
- En local no hay `python` en el PATH: usar `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
  La base se saca de la rama `data` a `data/deportes.db` (carpeta ignorada por git).

## Reglas

- No hay cuotas de baloncesto (ninguna fuente gratuita). Solo LaLiga tiene cuotas.
- Ningún dato personal en el repositorio: es público.
- Antes de dar algo por hecho, probarlo: `python build_site.py` y abrir `_site/` en un navegador.
- Sergio valora la franqueza: decir lo que no se ha podido verificar y no prometer certezas en apuestas.

## Pendiente

- Calendario de LaLiga (la fuente solo trae partidos jugados).
- Histórico completo de ligas nacionales extranjeras (Flashscore solo da lo reciente).
- Validar el modelo en vivo contra cuotas reales con el registro de apuestas.
- `EUROCUP_GAP` (9) sale de solo 6 equipos: revisarlo cuando haya más ascensos.
- Las bajas solo se detectan una vez empezado el partido; no hay fuente gratuita de lesiones previas.
- No hay cuotas en directo de baloncesto guardadas: no se puede medir cuánto se equivoca la casa,
  solo cuánto remonta el favorito. El registro de apuestas del panel es la forma de ir midiéndolo.
