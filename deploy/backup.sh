#!/usr/bin/env bash
# Backup notturno del database e degli XML importati. Conserva le ultime 30 copie.
# Cron sul BMAX (crontab -e):  30 2 * * * /percorso/b3d-fisco/deploy/backup.sh >> /percorso/b3d-fisco/dati/backup.log 2>&1
set -euo pipefail
CARTELLA="$(cd "$(dirname "$0")/.." && pwd)"
DATI="$CARTELLA/dati"
DEST="$DATI/backup"
STAMP="$(date +%Y-%m-%d_%H%M)"
mkdir -p "$DEST"

# Copia coerente anche con l'app accesa (eseguita dentro il container, che ha sqlite3)
docker exec b3d-fisco sqlite3 /app/dati/b3d-fisco.sqlite ".backup '/app/dati/backup/db_$STAMP.sqlite'"
tar -czf "$DEST/b3d-fisco_$STAMP.tar.gz" -C "$DEST" "db_$STAMP.sqlite" -C "$DATI" xml 2>/dev/null || \
  tar -czf "$DEST/b3d-fisco_$STAMP.tar.gz" -C "$DEST" "db_$STAMP.sqlite"
rm -f "$DEST/db_$STAMP.sqlite"

# Tiene solo le ultime 30 copie
ls -1t "$DEST"/b3d-fisco_*.tar.gz | tail -n +31 | xargs -r rm -f
echo "$(date -Iseconds) backup ok: b3d-fisco_$STAMP.tar.gz"
