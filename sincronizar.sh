#!/usr/bin/env bash
# Pone esta carpeta al día con GitHub: el código (rama main) y la base de datos del día (rama data → data/deportes.db).
# En Windows: botón derecho en la carpeta → «Open Git Bash here» →  bash sincronizar.sh
set -e
cd "$(dirname "$0")"

echo "1/2 Código..."
git pull --ff-only origin main

echo "2/2 Base de datos..."
git fetch --depth=1 origin data
mkdir -p data
git show FETCH_HEAD:deportes_db.zip > data/deportes_db.zip
if command -v unzip >/dev/null 2>&1; then
  unzip -o -q data/deportes_db.zip -d data
else
  powershell -NoProfile -Command "Expand-Archive -Force 'data/deportes_db.zip' 'data'"
fi
rm -f data/deportes_db.zip
echo "Hecho. Base guardada en GitHub el $(git log -1 --format=%cd --date=format:'%d-%m-%Y a las %H:%M' FETCH_HEAD)."
