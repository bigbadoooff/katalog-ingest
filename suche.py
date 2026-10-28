import os, sys, requests, psycopg

OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
frage = " ".join(sys.argv[1:])
if not frage:
    sys.exit("Aufruf: python suche.py Deine Frage")

r = requests.post(f"{OLLAMA}/api/embed",
                  json={"model": "bge-m3", "input": frage}, timeout=120)
r.raise_for_status()
vek = str(r.json()["embeddings"][0])

with psycopg.connect(os.environ["DB_DSN"]) as conn:
    rows = conn.execute("""
        SELECT titel, quelle, tags, link,
               1 - (embedding <=> %s::vector) AS aehnlichkeit
        FROM eintrag
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT 5
    """, (vek, vek)).fetchall()

for titel, quelle, tags, link, a in rows:
    print(f"{a:.2f}  [{quelle}] {titel}")
    print(f"      {', '.join(tags or [])}")
    print(f"      {link}\n")
