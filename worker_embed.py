import os, sys, requests, psycopg

OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
BATCH = 16

def embed(texte):
    r = requests.post(f"{OLLAMA}/api/embed",
                      json={"model": "bge-m3", "input": texte}, timeout=300)
    r.raise_for_status()
    return r.json()["embeddings"]

def text_von(titel, tags, auszug):
    return f"{titel or ''}\n{', '.join(tags or [])}\n{(auszug or '')[:2000]}"

try:
    requests.get(OLLAMA, timeout=5)
except requests.RequestException:
    print("Ollama nicht erreichbar, PC vermutlich aus. Abbruch ohne Änderung.")
    sys.exit(0)

gesamt = 0
with psycopg.connect(os.environ["DB_DSN"], autocommit=True) as conn:
    while True:
        rows = conn.execute("""
            UPDATE eintrag SET status = 'in_arbeit'
            WHERE id IN (SELECT id FROM eintrag WHERE status = 'neu'
                         ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED)
            RETURNING id, titel, tags, inhalt_auszug
        """, (BATCH,)).fetchall()
        if not rows:
            break
        ids = [r[0] for r in rows]
        try:
            vek = embed([text_von(r[1], r[2], r[3]) for r in rows])
            for i, v in zip(ids, vek):
                conn.execute(
                    "UPDATE eintrag SET embedding = %s::vector, status = 'fertig' WHERE id = %s",
                    (str(v), i))
            gesamt += len(ids)
        except Exception as e:
            conn.execute("UPDATE eintrag SET status = 'neu' WHERE id = ANY(%s)", (ids,))
            print("Fehler, Batch zurückgesetzt:", e)
            break
print(f"{gesamt} Einträge mit Embedding versehen")
