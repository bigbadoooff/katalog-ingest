import os, sys, json, requests, psycopg

# Das lokale Modell ordnet jeden freien Tag ein. verknuepfe.py liest das Ergebnis aus tag_klasse.
# Einträge mit quelle = 'manuell' werden nie überschrieben.
OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
MODELL = "gemma3:12b"
KLASSEN = {
    "person": "ein bestimmter Mensch, z. B. ein Familienmitglied",
    "vorgang": "eine konkrete, abgegrenzte Angelegenheit oder ein bestimmter Gegenstand, "
               "zu dem mehrere Dokumente gehören, z. B. ein Erbfall, ein Fahrzeug, ein Bauprojekt",
    "kategorie": "ein allgemeiner Themenbereich, unter den viele unabhängige Dokumente fallen",
}
SCHEMA = {"type": "object",
          "properties": {"klasse": {"type": "string", "enum": list(KLASSEN)},
                         "begruendung": {"type": "string"}},
          "required": ["klasse", "begruendung"]}

def einordnen(tag, beispiele):
    prompt = (
        "In einem privaten Dokumentenarchiv gibt es den Tag unten. Ordne ihn genau einer Klasse zu:\n"
        + "\n".join(f"- {k}: {v}" for k, v in KLASSEN.items())
        + f"\n\nTAG: {tag}\n\nDOKUMENTE MIT DIESEM TAG:\n"
        + "\n".join(f"- {t}: {z or '(keine Zusammenfassung)'}" for t, z in beispiele)
        + "\n\nBegründe in einem Satz.")
    r = requests.post(f"{OLLAMA}/api/chat", json={
        "model": MODELL, "stream": False, "format": SCHEMA,
        "options": {"num_ctx": 4096, "temperature": 0},
        "messages": [{"role": "user", "content": prompt}]}, timeout=900)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])

try:
    requests.get(OLLAMA, timeout=5)
except requests.RequestException:
    print("Ollama nicht erreichbar, PC vermutlich aus. Abbruch ohne Änderung.")
    sys.exit(0)

with psycopg.connect(os.environ["DB_DSN"], autocommit=True) as conn:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tag_klasse (
            tag         text PRIMARY KEY,
            klasse      text NOT NULL CHECK (klasse IN ('person', 'vorgang', 'kategorie')),
            begruendung text,
            quelle      text NOT NULL DEFAULT 'modell' CHECK (quelle IN ('modell', 'manuell')),
            modell      text,
            erstellt_am timestamptz NOT NULL DEFAULT now())
    """)
    offen = [r[0] for r in conn.execute("""
        SELECT DISTINCT t FROM eintrag, unnest(tags) t
        WHERE typ = 'dokument' AND t NOT LIKE 'von:%%' AND t NOT LIKE 'art:%%'
          AND t NOT IN (SELECT tag FROM tag_klasse)
        ORDER BY 1
    """).fetchall()]
    n = 0
    for tag in offen:
        beispiele = conn.execute("""
            SELECT titel, zusammenfassung FROM eintrag
            WHERE typ = 'dokument' AND %s = ANY(tags) ORDER BY zeitstempel DESC NULLS LAST LIMIT 6
        """, (tag,)).fetchall()
        try:
            e = einordnen(tag, beispiele)
        except Exception as ex:
            print("Fehler bei Tag", tag, ":", ex)
            break
        conn.execute("""
            INSERT INTO tag_klasse (tag, klasse, begruendung, modell) VALUES (%s, %s, %s, %s)
            ON CONFLICT (tag) DO NOTHING
        """, (tag, e["klasse"], e["begruendung"], MODELL))
        n += 1
        print(f"{tag:<20} -> {e['klasse']:<10} {e['begruendung']}")
print(f"{n} Tags eingeordnet")
