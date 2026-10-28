import os, sys, requests, psycopg

OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
MODELL = "qwen2.5:7b"
frage = " ".join(sys.argv[1:])
if not frage:
    sys.exit("Aufruf: python frage.py Deine Frage")

r = requests.post(f"{OLLAMA}/api/embed",
                  json={"model": "bge-m3", "input": frage}, timeout=120)
r.raise_for_status()
vek = str(r.json()["embeddings"][0])

with psycopg.connect(os.environ["DB_DSN"]) as conn:
    rows = conn.execute("""
        SELECT titel, zeitstempel::date, tags, link, inhalt_auszug
        FROM eintrag WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector LIMIT 5
    """, (vek,)).fetchall()

kontext = "\n\n".join(
    f"[{i}] Titel: {t} | Datum: {d} | Tags: {', '.join(tg or [])}\n{(a or '')[-700:]}"
    for i, (t, d, tg, l, a) in enumerate(rows, 1))

prompt = (
    "Du beantwortest Fragen zu privaten Dokumenten. Nutze ausschließlich die "
    "folgenden Treffer. Steht die Antwort nicht darin, sag das ehrlich. "
    "Antworte kurz auf Deutsch und nenne die Nummer des Treffers, auf den du dich stützt.\n\n"
    f"TREFFER:\n{kontext}\n\nFRAGE: {frage}")

a = requests.post(f"{OLLAMA}/api/chat", json={
    "model": MODELL, "stream": False,
    "options": {"num_ctx": 4096, "temperature": 0.2},
    "messages": [{"role": "user", "content": prompt}]}, timeout=600)
a.raise_for_status()
print(a.json()["message"]["content"])
print("\nQuellen:")
for i, (t, d, tg, l, _) in enumerate(rows, 1):
    print(f"[{i}] {d} {t}\n    {l}")
