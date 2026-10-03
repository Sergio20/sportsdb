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
- `build_halves.py`: tiro por mitades (tabla `basket_half_stats`), lo llama `update_db.py`. Las tres
  ligas: Euroliga/EuroCup de la API de tiros y Liga Endesa de la ficha por cuartos de live.acb.com.
  `build_timeline.py` saca la evolución del marcador de la Liga Endesa de la página «resumen».
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
  Tercer aviso, «Total desfasado» (`TOTAL_GAP`): el ritmo actual lleva a un total 18+ puntos
  distinto del esperable. Modelo del total: constantes `KT` (del exceso de ritmo solo se mantiene
  un 8 %). La calculadora y el registro admiten hándicap y total (más/menos); cada apuesta guarda
  el aviso que había (`kind`) y el registro resume el resultado por tipo de aviso. Faltas de los
  jugadores clave contadas en las jugadas (`foulTrouble`). Botón «Activar avisos con sonido»:
  pitido + notificación del navegador con cada aviso nuevo, también con la pestaña en segundo plano.
- `analisis.py`: motor de los informes. Recorre el histórico sin mirar el futuro, ajusta la
  previsión pre-partido por liga y comprueba el modelo en vivo (`K`, `KT`) al final de cada cuarto.
  `export_informes.py` lo vuelca en `jornada.html` (previsión de los próximos 6 días con descanso,
  carga de partidos y comparador de líneas; plantilla `panel/jornada_plantilla.html`) y en
  `funciona.html` (¿se cumplen las probabilidades?, avisos reproducidos con el histórico, entrada y
  salida; plantilla `panel/funciona_plantilla.html`). Si se cambian `K`/`KT` o los umbrales de los
  avisos en la plantilla en vivo, cambiarlos también en `analisis.py`.
- `build_timeline.py`: marcador jugada a jugada (tabla `basket_timeline`), 60 partidos por
  actualización diaria porque la fuente limita; de ahí sale el apartado «Entrar y salir».
- Hándicaps de seguridad (`SAFE` en la plantilla en vivo y en `avisos.py`, `Z80/Z90/Z95` en
  `analisis.py`): línea neutra + 0,84 / 1,28 / 1,64 veces el margen de error; en el histórico
  aciertan 76-83 %, 87-93 % y 93-96 %. El «hándicap justo» (acierta 1 de cada 2) ya NO se muestra
  en ningún sitio por decisión de Sergio: solo líneas del 80 % en adelante. Recordarle siempre la
  cuota mínima (1,25 / 1,11 / 1,05).
- `avisos.py`: las reglas de los cuatro avisos en Python (desfase, ritmo, triples, total), espejo de
  la plantilla en vivo. `vigilante.py` las aplica en directo y manda cada aviso por Telegram
  (secretos `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` del repositorio); lo lanza
  `.github/workflows/vigilante.yml` en cuatro tandas encadenadas (09:10–02:30 UTC). También vigila
  la Liga Endesa leyendo la ficha pública de live.acb.com (`state_acb`: estado, cuarto, tiempo y
  tiros de equipo). El navegador no puede leer acb.com, así que el vigilante publica el estado de
  esos partidos cada minuto en `vivo.json` (rama `vivo`, un solo commit) y la web en vivo lo lee
  por la API de GitHub cada 90 s (límite: 60 lecturas/hora por conexión). En la página la Liga
  Endesa es la tercera competición (`data-comp="A"`): `acbGames()` convierte cada partido a la
  misma forma que los de Euroleague, así que sirven la tira, la vista de detalle, la calculadora
  y el registro; sus equipos van en `BASE` con el código numérico de acb.com. PRIORIDAD: Telegram.
  Revisión de cada partido cada 10 s con pocos partidos a la vez (`MIN_EVERY`) y hasta ~20 s con 8
  (`PER_EURO`, `PER_ACB`), una sola lectura por partido; si la fuente corta (429) suma segundos y
  luego los quita. La publicación para la web va en segundo plano para no retrasar los avisos.
  Antes de los partidos manda un mensaje previo con la agenda (`agenda`). Sin fuente en directo: LaLiga y las ligas nacionales de Flashscore (solo resultados).
  Cada aviso enviado queda en `avisos.json`
  (rama `avisos`, un solo commit) y `analisis.sent_log` lo resuelve para «¿Funciona?».
  Ensayo sin partidos: `python vigilante.py --repetir E2026 26 16`.
- `scripts/repaso.py --desde AAAA-MM-DD`: repasa partidos ya jugados minuto a minuto con las
  mismas reglas de aviso del panel y dice qué hándicap se habría ganado con cada línea.
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
