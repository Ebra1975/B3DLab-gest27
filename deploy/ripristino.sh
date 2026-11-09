#!/usr/bin/env bash
# Prova di ripristino (da fare una volta al mese): estrae l'ultimo backup in una cartella di prova
# e confronta il numero di fatture e incassi con il database in uso.
set -euo pipefail
CARTELLA="$(cd "$(dirname "$0")/.." && pwd)"
ULTIMO="$(ls -1t "$CARTELLA"/dati/backup/b3d-fisco_*.tar.gz | head -1)"
PROVA="$(mktemp -d)"
tar -xzf "$ULTIMO" -C "$PROVA"
DB="$(ls "$PROVA"/db_*.sqlite)"
for t in fattura incasso versamento parametro; do
  a=$(docker exec b3d-fisco sqlite3 /app/dati/b3d-fisco.sqlite "SELECT COUNT(*) FROM $t")
  b=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $t")
  echo "$t: in uso $a, nel backup $b"
done
rm -rf "$PROVA"
echo "Ripristino di prova completato da $(basename "$ULTIMO")"
