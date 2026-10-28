#!/bin/bash
# Gesamtlauf: alle Schritte in fester Reihenfolge, in einem Wegwerf-Container.
# Schreibt nie in eine Quelle. Was dorthin soll, bleibt als Vorschlag liegen bis
# "vorschlaege.py ja/nein". Ist der PC aus, überspringen die Ollama-Worker ihren Teil.
# Noch ohne Timer: Start von Hand mit ./nachtlauf.sh, Protokoll unter logs/.
set -u

SCHRITTE="
ingest_paperless
ingest_radicale
ingest_immich
worker_abschnitte
worker_zusammenfassung
worker_felder
worker_tags
verknuepfe
worker_personen
worker_kontakte
sync_geburtstage
sync_fotos
"

if [ "${1:-}" = "--im-container" ]; then
    pip install -q --root-user-action=ignore --disable-pip-version-check requests 'psycopg[binary]' || exit 1
    fehler=""
    for s in $SCHRITTE; do
        echo "=== $(date +%H:%M:%S) $s"
        python -u "$s.py" || fehler="$fehler $s"
    done
    echo "=== $(date +%H:%M:%S) offene Vorschläge"
    python -u vorschlaege.py
    if [ -n "$fehler" ]; then
        echo "=== fertig, Fehler in:$fehler"
        exit 1
    fi
    echo "=== fertig ohne Fehler"
    exit 0
fi

cd "$(dirname "$0")"
exec 9>/tmp/katalog-nachtlauf.lock
flock -n 9 || { echo "Ein Gesamtlauf läuft schon."; exit 1; }
mkdir -p logs
LOG="logs/nachtlauf-$(date +%Y%m%d-%H%M%S).log"
docker run --rm --network host --env-file .env -v "$PWD":/app -w /app \
    -v /opt/stacks/radicale/data/collections/collection-root/martin:/radicale:ro \
    python:3.12-slim bash /app/nachtlauf.sh --im-container 2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}
echo "Protokoll: $LOG"
exit "$status"
