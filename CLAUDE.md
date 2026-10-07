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
  Dos colas (`concurrency`): la de datos (diaria o a mano) y la de publicaciones por push (la nueva sustituye a la
  anterior). Si falla o falla alguna fuente, aviso por Telegram. **Guardia** (`guardia.yml`, cada hora, min. 41 →
  `scripts/guardia_datos.sh`): cancela ejecuciones atascadas en cola >30 min sin ninguna en marcha y, si pasadas las
  05:15 UTC la rama `data` no se ha guardado hoy después de las 03:00 UTC, relanza la actualización (máx. 3 al día) y avisa por Telegram. Motivo:
  el 5-10-2026 una publicación se quedó «en cola» toda la noche, bloqueó la cola única y el día 6 no hubo actualización.
  GitHub retrasa a veces horas la de las 04:10 (el 7-10-2026 arrancó a las 10:59): la guardia la cubre.
- En el PC de Sergio: `bash sincronizar.sh` (Git Bash) trae el código y la base del día a `data/deportes.db`.

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
- Banco de pruebas (`pruebas.html`, plantilla `panel/pruebas_plantilla.html`; `analisis.save_bank` → tabla
  `banco_pruebas` de la base): cada aviso enviado por Telegram cuenta como apuesta simulada de 500 € (`STAKE`) en cada
  una de sus tres líneas, a las cuotas aproximadas de la casa que dio Sergio (`ODDS`: 95 % 1,175 · 90 % 1,275 · 80 % 1,40);
  una cartera por línea; importe y cuotas editables en la página. Cada apuesta se escribe corta («Breogán hándicap +29,5»,
  «Manresa menos de 28,5 puntos», con el periodo encima). Es un
  simulacro: Sergio no apuesta con esto y nunca se publican sus apuestas reales. El vigilante anota `res`/`final` en
  cada aviso al acabar el partido, así el banco no espera a la actualización diaria.
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
  `.github/workflows/vigilante.yml` en una cadena continua de tandas de 4,4 h, cada una lanza la siguiente
  haya partidos o no (el programador de GitHub se retrasa horas o se salta tandas; queda de respaldo cada hora, min. 23). También vigila
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
- Lecturas dudosas (`suspicious` en `vigilante.py`): acb.com a veces da unos segundos las estadísticas de equipo a
  cero al cambiar de cuarto o una copia vieja (marcador/reloj/tiros hacia atrás). Esas lecturas se descartan y no
  generan avisos (el 3-10-2026 salieron dos «Total desfasado» con 0-0 por esto). Una bajada repetida 4 veces se acepta
  (corrección del acta). `analisis.sent_log` ignora avisos con `anulado` o con 0-0 pasado el minuto 1.
- Vigilancia de la vigilancia (todo por Telegram, para enterarse al momento): «HE ESTADO SIN VIGILAR» si una tanda
  arranca más de 10 min después de acabar la anterior con partidos en juego (`gap_step`, marca `tanda_fin` en
  `avisos.json`); «NO PUEDO LEER» si un partido falla 12 lecturas seguidas (y «Vuelvo a leer bien»); aviso si una tanda
  falla o no se puede lanzar la siguiente (pasos finales de `vigilante.yml`, con 5 reintentos). Cada partido se revisa
  en su propio `try`: un error con uno no para los demás. `send` reintenta 4 veces.
- Tabla de líneas (`avisos.line_table`): la casa casi nunca ofrece justo nuestras líneas, así que cada aviso trae
  «SI LA CASA TE OFRECE OTRA LÍNEA»: probabilidad y cuota mínima de cada línea entre el 80 y el 97 %, sacadas de la
  misma campana que las tres líneas del aviso (con dos de ellas se recupera centro y anchura).
- Mensaje de Telegram corto (`avisos.compact`, ~10 líneas, para decidir en segundos): por qué, mercado y periodo, las tres
  líneas con su cuota mínima, «Otras» (4 líneas de la tabla) y los botones. La explicación larga (`describe`) solo va a la web.
- Botones del aviso (`lines_kb`, `odds_kb`, `button_press` en `vigilante.py`): una tecla por línea (`avisos.offer_lines`);
  al tocarla, teclas de cuota alrededor de la mínima ya marcadas ✅/❌; al tocar una cuota sale al instante en pantalla si
  tiene valor y se anota en `casa`. «No aparece» abre las líneas más ajustadas que suele dar la casa (`casa_kb`,
  `avisos.market_lines`: del 30 al 80 % según nuestra campana) con su % al lado; Sergio toca la que ve y su cuota y queda
  anotada como las demás (con p < 80 avisa de que es arriesgada). «Ninguna parecida» anota `none` una sola vez por aviso (`note_none`).
  Teclas de cuota en proporción a la mínima (sirven de 1,05 a 3). `analisis._casa` añade a cada anotación lo que cree la
  casa (`implied` = 100/cuota), el valor esperado (`ev`) y si esa línea se ganó (`hit`, con `avisos.line_hits`); el banco
  enseña en «Tu casa de apuestas» si ofrecía nuestras líneas, dónde pone las suyas, la ventaja por tipo de aviso y el
  balance apostando solo la oferta con más valor de cada aviso. Objetivo: ver dónde se le puede ganar a la casa. La cuenta del valor
  es la misma para botones y respuestas escritas (`value_of`). Un hilo (`listen`) escucha Telegram sin parar (espera
  larga de 25 s) para contestar al momento; `LOCK` protege el registro compartido. En el ensayo los botones responden
  (rehace la campana con `avisos_from_keyboard`) pero no anotan.
- Respuestas en Telegram (`replies_step` / `answer_reply` en `vigilante.py`): Sergio contesta a un aviso con la línea y
  la cuota de su casa («+7,5 1,12») y el vigilante responde al momento con el % de acierto (`avisos.prob_of_line`, misma
  campana que `line_table`), la cuota mínima y si tiene valor. Cada aviso guarda el número de su mensaje (`msg`) y las
  respuestas en `casa`; el banco de pruebas las enseña. Se leen con getUpdates (posición en la marca `tg_offset` de
  `avisos.json`) desde el hilo `listen`. Solo se atiende al chat de `TELEGRAM_CHAT_ID`. Respuesta «no aparece»
  (`NO_LINE`): igual que el botón. El banco de pruebas resume en «Tu casa de apuestas» cuántas veces no ofrecía nuestras
  líneas y cuántas ofertas tenían valor.
- Banco a 7-10-2026 (31 avisos, 3 días; 500 € a 1,175/1,275/1,40): línea 95 % 29/31 (+1.538 €), 90 % 24/31 (−200 €),
  80 % 20/31 (−1.500 €). Equilibrio: 1,07 / 1,29 / 1,55. Los de cuarto, por debajo de lo prometido (90 % 13/18, 80 % 12/18);
  fallos juntos en un mismo partido desigual (Baskonia 118-78 Girona). Muestra corta y muy agrupada por partidos.
  Las dos veces que Sergio miró la casa (6-10) no ofrecía nuestras líneas.
  Revisión cuantitativa (7-10): 31 avisos = 9 partidos; los del mismo partido fallan juntos. Desde entonces: solo un
  aviso de cuarto por partido y cuarto (`avisos.strongest`: la regla con más acierto fuera del ajuste, línea del 90; los
  descartados en `also`; el vigilante no manda otro si ya salió uno de ese cuarto) y cartera «Con tope de 500 € por
  partido» en el banco (solo el primer aviso de cada partido: 9/9, 8/9, 7/9 a 7-10). Estudio «Paliza»/«Equipo caliente»
  con el partido muy roto (2024+): con 15+ de diferencia la línea del 90 acierta 85-87 % frente a 88-91 % con menos;
  diferencia pequeña y dentro del ruido (n≈180), no se cambia nada. Ojo: en el histórico las líneas «del 90» de cuarto
  aciertan 87-93 % según la regla, no exactamente 90.
- Partidos aplazados (`unstarted_step` en `vigilante.py`): si la ficha de acb.com dice aplazado (estado o fecha
  cambiada) manda «PARTIDO APLAZADO» por Telegram y deja de vigilarlo; si cualquier partido sigue sin empezar 40 min
  después de su hora (`LATE`) manda «¿PARTIDO APLAZADO?», lo revisa cada 3 min y avisa si arranca; a las 2 h 30
  (`GIVE_UP`) lo deja. La web pone «Aplazado» / «¿Aplazado?» (`postponed()` en la plantilla en vivo). Sin comprobar aún
  con un aplazamiento real cómo marca la ficha de acb.com el estado; la regla de los 40 minutos no depende de ello.
- Quinto aviso, «Cuarto anormal» (solo lo calcula el vigilante; la web lo enseña tal cual desde
  `vivo.json`): al acabar cada cuarto, si un equipo metió ≤10 o ≥30, el cuarto tuvo ≤30 o ≥54
  puntos, un equipo lo perdió por 10+, o la primera parte tuvo ≤66 o ≥100, propone una apuesta para
  el periodo siguiente (puntos del equipo, total del cuarto, hándicap del cuarto o total de la 2.ª
  parte). Reglas y líneas en `analisis.Q_RULES` / `quarter_rules` (cuantiles del histórico,
  comprobados con temporadas que no intervienen); `avisos.quarter_alerts` y `settle_quarter`;
  el vigilante manda el resultado en cuanto termina el periodo apostado (`quarter_step`).
- `scripts/repaso.py --desde AAAA-MM-DD`: repasa partidos ya jugados minuto a minuto con las
  mismas reglas de aviso del panel y dice qué hándicap se habría ganado con cada línea.
- `scripts/patron_cuartos.py`: idea de Sergio de que los cuartos «se compensan». Con 7.727 partidos: quien pierde dos
  cuartos seguidos gana el siguiente un 48 % frente al 44 % normal para su nivel (+4 puntos, igual en 2021-24 y 2024-27).
  Se concentra en el favorito (nivel +2 o más): gana el siguiente un 61 % frente al 56 %; su hándicap +3,5 en ese cuarto
  acierta 81 % (75 % sin la condición). Empate y luego gana A → A pierde el 3.º: +4 puntos (50 %). Prórroga tras perder
  3.º y 4.º: 50 %, sin efecto. Convertido en el sexto aviso, «Racha de cuartos» (regla `racha` en `Q_RULES`, mismo
  circuito que «Cuarto anormal»: tipo `cuarto`, `sub="racha"`; nombre con `avisos.name_of`): el favorito
  (`RACHA_FAV`, se espera que gane cada cuarto por 0,5+) pierde dos cuartos seguidos (sin «paliza» en el último) → su
  hándicap en el cuarto siguiente. Desde el 5-10-2026 también si el favorito no ha ganado ninguno de los tres primeros cuartos aunque empatara alguno (`racha()` en `analisis.py` y `avisos.py`, idea de Sergio): 49 casos más que aciertan como los demás. 788 casos; con 2024+ fuera del ajuste acierta 84/93/98 %. Estudio aparte: un equipo se queda sin ganar ningún cuarto en el 14,5 % de los partidos (1 de cada 7, casi lo mismo que al azar); tras tres sin ganar, gana el 4.º el 49 % (favorito 63 % frente al 56 % normal).
  Criterio de Sergio para nuevos avisos: solo si la línea acierta 85 % o más, comprobado con temporadas que no
  intervienen, y más que la misma apuesta sin la anomalía. Descartados con ese criterio: favorito +6 que pierde al
  descanso → hándicap 2.ª parte (84 % con la línea del 85 %); quien gana de 20+ tras el 3.º → hándicap del rival en el
  4.º (con todas las ligas 88 %, pero en Liga Endesa/Euroliga/EuroCup y 2024+ solo 84 % y 86 % con las líneas del 85/90).
- `scripts/cronologia.py E2026 22 26`: un partido minuto a minuto (marcador, ritmo, máxima ventaja).
- `scripts/remontadas.py`: estudio con el histórico de qué pasa cuando el favorito va perdiendo
  al final de cada cuarto (cuánto recupera, cuántas veces cubre +2,5…+8,5) y cuánto bajan los
  equipos que suben de EuroCup a Euroliga (de ahí `EUROCUP_GAP` en `export_live.py`).
- En local no hay `python` en el PATH: usar `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
  La base se saca de la rama `data` a `data/deportes.db` (carpeta ignorada por git).

- Revisión del sistema (7-10-2026): ver «Tareas programadas» abajo para no duplicar trabajo entre GitHub y las rutinas.

- Rapidez (7-10-2026): cada aviso guarda `lat` = {read: s desde la lectura de la fuente hasta que Telegram lo aceptó,
  poll: cada cuántos s se leía el partido} (`vigilante.lat_of`); `analisis._timing` añade `react` (s hasta el primer toque
  de Sergio). El banco lo enseña en «Rapidez de los avisos». No mide el retraso de la fuente frente a la tele.
- Freno automático (`analisis.rule_check`, `vigilante.freno_step`): por regla (tipo, o regla de cuarto), un aviso por
  partido; con `FRENO_MIN` = 40 partidos o más, si el límite alto del intervalo del 90 % de la línea del 90 no llega al
  `FRENO_TOPE` = 85 %, la regla se silencia (no manda avisos) y se avisa por Telegram; vuelve sola si mejora. Marcas
  `{freno, off}` en `avisos.json`.
- Informe semanal por Telegram los lunes (`informe.yml` → `scripts/informe_semanal.py`): 7 días y total, por línea,
  con tope por partido, por tipo, casa, rapidez y freno.
- Estudios descartados (7-10-2026): cansancio (equipo con partido hace ≤2 días frente a rival con 3+): Liga Endesa +0,7 a
  +1,1 puntos frente a lo esperado (sin efecto, n≈240-280), Euroliga/EuroCup +0,3. Ausencia de la estrella (máximo
  anotador con 22+ min en sus 10 partidos previos, `basket_player_stats`): −1,2 puntos frente a lo esperado (±0,8,
  n=575; −1,6 en 2024+), demasiado poco y la casa ya lo sabe antes del partido. Ninguno se convierte en aviso.

## Tareas programadas (todas en GitHub salvo el informe)

- `vigilante.yml`: cadena continua de tandas + respaldo cada hora (min. 23). Únicos avisos en directo.
- `actualizar.yml`: base y web a las 04:10 UTC (GitHub la retrasa a veces horas) y en cada push (solo web).
- `guardia.yml`: cada hora (min. 41). La ÚNICA que relanza la actualización si falta la base del día.
- `informe.yml`: lunes 07:37 UTC, informe semanal por Telegram.
- Rutina de Claude «Informe diario SportsDB» (08:00 Madrid): solo informa por correo; no relanza nada.
- La rutina antigua «Actualización diaria SportsDB» (carpeta del PC) se borró el 7-10-2026: la sustituye `actualizar.yml`.

- Asistentes especializados en `.claude/agents/` (de VoltAgent/awesome-claude-code-subagents, licencia MIT, commit 721e973):
  `quant-analyst`, `data-scientist` y `risk-manager`. Solo se usan si Sergio lo pide.

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
