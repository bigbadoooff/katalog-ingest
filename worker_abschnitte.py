import os, sys, requests, psycopg

OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
GROESSE, UEBERLAPP, BATCH = 800, 150, 16

def zerlege(text):
    text = " ".join((text or "").split())
    out, start = [], 0
    while start < len(text):
        ende = min(start + GROESSE, len(text))
        if ende < len(text):
            k = text.rfind(" ", start + GROESSE // 2, ende)
            if k > 0:
                ende = k
        out.append(text[start:ende])
        if ende >= len(text):
            break
        start = max(ende - UEBERLAPP, start + 1)
    return out

def embed(texte):
    r = requests.post(f"{OLLAMA}/api/embed",
                      json={"model": "bge-m3", "input": texte}, timeout=300)
    r.raise_for_status()
    return r.json()["embeddings"]

try:
    requests.get(OLLAMA, timeout=5)
except requests.RequestException:
    print("Ollama nicht erreichbar, PC vermutlich aus. Abbruch ohne Änderung.")
    sys.exit(0)

gesamt = 0
with psycopg.connect(os.environ["DB_DSN"], autocommit=True) as conn:
    conn.execute("UPDATE eintrag SET status='neu' WHERE status='in_arbeit'")
    while True:
        row = conn.execute("""
            UPDATE eintrag SET status = 'in_arbeit'
            WHERE id = (SELECT id FROM eintrag WHERE status = 'neu'
                        ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING id, titel, tags, inhalt_auszug
        """).fetchone()
        if not row:
            break
        eid, titel, tags, text = row
        titel = titel or ""
        stuecke = [f"{titel}\n{', '.join(tags or [])}"]
        stuecke += [f"{titel}\n{c}" for c in zerlege(text)]
        try:
            vek = []
            for i in range(0, len(stuecke), BATCH):
                vek += embed(stuecke[i:i + BATCH])
            with conn.transaction():
                conn.execute("DELETE FROM abschnitt WHERE eintrag_id = %s", (eid,))
                for nr, (s, v) in enumerate(zip(stuecke, vek)):
                    conn.execute(
                        "INSERT INTO abschnitt (eintrag_id, nr, text, embedding) VALUES (%s, %s, %s, %s::vector)",
                        (eid, nr, s, str(v)))
                conn.execute(
                    "UPDATE eintrag SET embedding = %s::vector, status = 'fertig' WHERE id = %s",
                    (str(vek[0]), eid))
            gesamt += 1
        except Exception as e:
            conn.execute("UPDATE eintrag SET status = 'neu' WHERE id = %s", (eid,))
            print("Fehler bei Eintrag", eid, ":", e)
            break
print(f"{gesamt} Einträge in Abschnitte zerlegt und eingebettet")
