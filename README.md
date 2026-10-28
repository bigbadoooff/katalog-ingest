# katalog-ingest

Ein privater KI-Katalog über die Daten im HomeLab: Paperless-Dokumente, Radicale-Kontakte und Immich-Personen. Alles landet in einer Datenbank, wird durchsuchbar und miteinander verknüpft.

## Rollen

- **Architekt (Mensch):** legt fest, was das System tun soll, und gibt jeden Schreibzugriff auf eine Quelle frei.
- **Builder:** baut den Rahmen, also Schema, Worker, Prompts, JSON-Schemas und Sicherheitsprüfungen.
- **Bewohner (lokales Modell über Ollama):** trifft die inhaltlichen Entscheidungen. Es fasst Dokumente zusammen, zieht Felder heraus und ordnet Tags ein.

Was an eine Quelle geschrieben werden soll, wird zuerst als Eintrag in der Tabelle `vorschlag` angelegt. Erst wenn der Architekt mit `vorschlaege.py ja <id>` zustimmt, wird tatsächlich geschrieben.

## Infrastruktur

| Teil | Ort |
|---|---|
| Postgres 17 mit pgvector (`katalog-db`) | Dockge-Stack `katalog` auf jarvis, Port 5432 nur über localhost |
| Ollama mit `bge-m3` (Embeddings) und `gemma3:12b` (Texte, Felder, Einordnung) | Windows-PC, GTX 1060 mit 6 GB; läuft nachts als Batch |
| Paperless-ngx, Radicale, Immich | eigene Stacks auf jarvis |

Auf jarvis wird nichts installiert. Jedes Skript läuft in einem Wegwerf-Container:

```sh
docker run --rm --network host --env-file .env -v "$PWD":/app -w /app \
  -v /opt/stacks/radicale/data/collections/collection-root/martin:/radicale:ro \
  python:3.12-slim sh -c "pip install -q requests 'psycopg[binary]' pillow && python -u <skript>.py"
```

Ist der PC aus, brechen die Ollama-Worker ab, ohne etwas zu verändern.

## Pipeline

| Schritt | Skript | Ollama |
|---|---|---|
| 1. Einlesen | `ingest_paperless.py`, `ingest_radicale.py` | – |
| 2. Abschnitte und Embeddings | `worker_abschnitte.py` | bge-m3 |
| 3. Zusammenfassung (nur Dokumente) | `worker_zusammenfassung.py` | gemma3 |
| 4. Feste Felder wie Art, Absender, Betrag, Nummern, Fristen | `worker_felder.py` | gemma3 |
| 5. Tags einordnen (Person, Vorgang oder Kategorie) | `worker_tags.py` | gemma3 |
| 6. Verknüpfen | `verknuepfe.py` | – |
| 7. Abgleich mit Immich (nur Vorschläge) | `sync_geburtstage.py` (Kontakt → Immich), `sync_fotos.py` (Immich → Kontakt) | – |
| 8. Vorschläge prüfen | `vorschlaege.py`, `vorschlaege.py ja 3 5`, `vorschlaege.py nein 4` | – |

Fragen stellen geht mit `frage.py <Frage>`. Das Modell lässt sich mit `FRAGE_MODELL=…` wählen. Für eine reine Ähnlichkeitssuche gibt es `suche.py <Text>`.

`immich_featured_face.py` wählt für jede Person das schärfste Gesichtsfoto als Vorschaubild. Ohne `--apply` macht es nur einen Trockenlauf. Am besten läuft es vor `sync_fotos.py`.

## Daten

Die Tabellenstruktur steht in `schema.sql`, das ist ein Dump nur des Schemas. Neu aufsetzen geht so:
`docker exec -i katalog-db psql -U katalog -d katalog < schema.sql`

- `eintrag`: Dokumente, Kontakte und Knoten (Absender, Personen, Vorgänge) mit `zusammenfassung`, `felder` (jsonb) und `embedding`
- `abschnitt`: Textstücke mit Embedding für `frage.py`
- `beziehung`: Verknüpfungen. `ableitung = true` heißt berechnet, `false` heißt vom Architekten bestätigt.
- `vorschlag`: offene, übernommene und verworfene Vorschläge
- `tag_klasse`: die Einordnung der Tags durch das Modell. Mit `quelle = 'manuell'` lässt sich eine Einordnung von Hand festlegen, die das Modell nie überschreibt.

## .env

`PAPERLESS_URL`, `PAPERLESS_PUBLIC_URL`, `PAPERLESS_TOKEN`, `DB_DSN`, `OLLAMA_URL`, `IMMICH_URL`, `IMMICH_API_KEY`, `RADICALE_URL`, `RADICALE_USER`, `RADICALE_PASSWORD`

Die Datei wird nie eingecheckt.

## Git

Die Arbeitskopie liegt auf jarvis in `~/katalog-ingest`, dort laufen die Skripte. GitHub (privat, `bigbadoooff/katalog-ingest`) wird nur vom PC aus beschrieben, damit jarvis keine GitHub-Zugangsdaten braucht.

1. Auf jarvis ändern und committen.
2. Auf dem PC in `C:\Users\engle\jarvis-setup\katalog-ingest` übertragen:
   ```sh
   git pull jarvis main
   git push github main
   ```

Der PC-Klon verwendet das Windows-OpenSSH (`core.sshCommand`), weil der SSH-Schlüssel im Agent des Passwortmanagers liegt.
