#!/usr/bin/env bash
# Guardia de la actualización diaria (la lanza .github/workflows/guardia.yml cada hora).
#
# El 5-10-2026 una ejecución de «Actualizar datos» se quedó atascada «en cola» toda la noche: como solo puede haber
# una a la vez, bloqueó a las demás y la base no se actualizó el día 6. Esta guardia:
#   1. Cancela las ejecuciones que llevan más de 30 min en cola sin que haya ninguna en marcha (atascadas).
#   2. Si la base (rama data) no se ha guardado hoy pasadas las 05:15 UTC y no hay ninguna actualización en marcha,
#      lanza una y avisa por Telegram. Como mucho 3 relanzamientos al día para no entrar en bucle si una fuente falla.
# Necesita GH_TOKEN (permiso actions: write) y, para avisar, TELEGRAM_TOKEN y TELEGRAM_CHAT_ID.
set -uo pipefail
repo="${GITHUB_REPOSITORY:?}"
now=$(date -u +%s)

tg() {
  [ -n "${TELEGRAM_TOKEN:-}" ] || return 0
  curl -sS -o /dev/null -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=$1" || true
}

runs=$(gh run list --repo "$repo" --workflow actualizar.yml --limit 30 \
  --json databaseId,status,event,createdAt,startedAt) || { echo "No puedo leer las ejecuciones"; exit 0; }

running=$(jq '[.[] | select(.status == "in_progress")] | length' <<<"$runs")
stuck=$(jq -r --argjson now "$now" '.[] | select((.status == "queued" or .status == "waiting" or .status == "pending"
  or .status == "requested") and ($now - (.createdAt | fromdateiso8601)) > 1800) | .databaseId' <<<"$runs")
if [ "$running" -eq 0 ] && [ -n "$stuck" ]; then
  for id in $stuck; do
    echo "Cancelo la ejecución atascada en cola $id"
    gh run cancel "$id" --repo "$repo" || true
  done
  sleep 20
  runs=$(gh run list --repo "$repo" --workflow actualizar.yml --limit 30 --json databaseId,status,event,createdAt,startedAt)
fi

# ¿Se ha guardado la base hoy? (la actualización empieza a las 04:10 UTC y tarda unos 20-40 min)
last=$(gh api "repos/$repo/commits/data" --jq .commit.committer.date) || { echo "No puedo leer la rama data"; exit 0; }
today=$(date -u +%Y-%m-%d)
due=$(date -u -d "$today 05:15" +%s)
if [ "$now" -lt "$due" ]; then           # aún no toca la de hoy: basta con que exista la de ayer
  need=$(date -u -d "$today 00:00 -1 day" +%s)
else
  need=$(date -u -d "$today 00:00" +%s)
fi
if [ "$(date -u -d "$last" +%s)" -ge "$need" ]; then
  echo "Base al día (guardada $last)"
  exit 0
fi

active=$(jq --argjson now "$now" '[.[] | select(.event != "push" and (.status == "in_progress" or
  ((.status == "queued" or .status == "waiting" or .status == "pending" or .status == "requested")
   and ($now - (.createdAt | fromdateiso8601)) <= 1800)))] | length' <<<"$runs")
if [ "$active" -gt 0 ]; then
  echo "Base sin guardar desde $last, pero ya hay una actualización en marcha"
  exit 0
fi
relaunched=$(jq --argjson now "$now" '[.[] | select(.event == "workflow_dispatch" and
  ($now - (.createdAt | fromdateiso8601)) < 86400)] | length' <<<"$runs")
if [ "$relaunched" -ge 3 ]; then
  echo "Ya se ha relanzado $relaunched veces en 24 h; no insisto (cada fallo ya avisa por Telegram)"
  exit 0
fi
echo "Base sin guardar desde $last: lanzo la actualización"
if gh workflow run actualizar.yml --repo "$repo"; then
  tg "🔧 La actualización diaria de la base no se había hecho (último guardado: ${last}). La he lanzado yo; tarda unos 30 min y avisaré si falla."
else
  tg "⚠️ LA BASE NO SE HA ACTUALIZADO HOY (último guardado: ${last}) y no he podido relanzarla. Revisa: ${GITHUB_SERVER_URL:-https://github.com}/${repo}/actions"
fi
