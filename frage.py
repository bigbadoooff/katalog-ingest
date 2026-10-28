import os, sys, requests, psycopg

OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
MODELL = os.environ.get("FRAGE_MODELL", "qwen2.5:7b")
MAX_VERWANDT = 6
frage = " ".join(sys.argv[1:])
if not frage:
    sys.exit("Aufruf: python frage.py Deine Frage")

r = requests.post(f"{OLLAMA}/api/embed",
                  json={"model": "bge-m3", "input": frage}, timeout=120)
r.raise_for_status()
vek = str(r.json()["embeddings"][0])

NEU = any(w in frage.lower() for w in ("letzte", "neueste", "neuste", "zuletzt", "aktuell"))

with psycopg.connect(os.environ["DB_DSN"]) as conn:
    if NEU:
        rows = conn.execute("""
            WITH best AS (
                SELECT DISTINCT ON (e.id) e.id, e.titel, e.zeitstempel::date AS datum,
                       e.link, a.text, a.embedding <=> %s::vector AS dist
                FROM abschnitt a JOIN eintrag e ON e.id = a.eintrag_id
                WHERE a.embedding IS NOT NULL
                ORDER BY e.id, a.embedding <=> %s::vector
            )
            SELECT id, titel, datum, link, text FROM best
            WHERE dist <= (SELECT min(dist) FROM best) + 0.08
            ORDER BY datum DESC NULLS LAST LIMIT 3
        """, (vek, vek)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, titel, datum, link, text FROM (
                SELECT e.id, e.titel, e.zeitstempel::date AS datum, e.link, a.text,
                       a.embedding <=> %s::vector AS dist,
                       row_number() OVER (PARTITION BY e.id
                                          ORDER BY a.embedding <=> %s::vector) AS rn
                FROM abschnitt a JOIN eintrag e ON e.id = a.eintrag_id
                WHERE a.embedding IS NOT NULL
            ) x
            WHERE rn <= 2
            ORDER BY dist LIMIT 6
        """, (vek, vek)).fetchall()
    if NEU and rows:
        voll, zus = conn.execute("SELECT inhalt_auszug, zusammenfassung FROM eintrag WHERE id = %s",
                                 (rows[0][0],)).fetchone()
        voll = voll or ""
        rows = [(*rows[0][:4], f"ZUSAMMENFASSUNG: {zus or '-'}\nAUSZUG: " + voll[:800] + " [...] " + voll[-1500:])]

    # Weitere Dokumente aus denselben Vorgängen wie die Treffer, als Zusammenfassung.
    # Nicht bei Fragen nach dem Neuesten, sonst verdrängen ältere Dokumente den Treffer.
    ids = list(dict.fromkeys(r[0] for r in rows))
    verwandt = [] if NEU else conn.execute("""
        SELECT DISTINCT ON (d.id) d.id, d.titel, d.zeitstempel::date, d.link,
               d.zusammenfassung, k.titel
        FROM beziehung b1
        JOIN eintrag k ON k.id = b1.zu_id AND k.typ = 'vorgang'
        JOIN beziehung b2 ON b2.zu_id = k.id AND b2.art = 'gehoert_zu'
        JOIN eintrag d ON d.id = b2.von_id
        WHERE b1.art = 'gehoert_zu' AND b1.von_id = ANY(%s) AND d.id <> ALL(%s)
          AND d.zusammenfassung IS NOT NULL
        ORDER BY d.id LIMIT %s
    """, (ids, ids, MAX_VERWANDT)).fetchall()

kontext = "\n\n".join(
    f"[{i}] Dokument: {t} | Datum: {d}\n{x if NEU else x[:900]}"
    for i, (_, t, d, l, x) in enumerate(rows, 1))
if verwandt:
    kontext += "\n\nWEITERE DOKUMENTE AUS DEMSELBEN VORGANG (nur Zusammenfassung):\n\n" + "\n\n".join(
        f"[{i}] Dokument: {t} | Datum: {d} | Vorgang: {v}\n{z}"
        for i, (_, t, d, l, z, v) in enumerate(verwandt, len(rows) + 1))

prompt = (
    "Du beantwortest Fragen zu privaten Dokumenten. Nutze ausschließlich die "
    "folgenden Textstellen. Steht die Antwort nicht darin, sag das ehrlich. "
    "Antworte kurz auf Deutsch und nenne die Nummer der Stelle, auf die du dich stützt.\n\n"
    f"TEXTSTELLEN:\n{kontext}\n\nFRAGE: {frage}")

a = requests.post(f"{OLLAMA}/api/chat", json={
    "model": MODELL, "stream": False,
    "options": {"num_ctx": 4096, "temperature": 0.2},
    "messages": [{"role": "user", "content": prompt}]}, timeout=600)
a.raise_for_status()
print(a.json()["message"]["content"])
print("\nQuellen:")
quellen = {}
for i, (eid, t, d, l, _) in enumerate(rows, 1):
    quellen.setdefault(eid, [[], d, t, l, ""])[0].append(i)
for i, (eid, t, d, l, _, v) in enumerate(verwandt, len(rows) + 1):
    quellen.setdefault(eid, [[], d, t, l, f"  (Vorgang {v})"])[0].append(i)
for nrs, d, t, l, zusatz in quellen.values():
    print(f"[{','.join(map(str, nrs))}] {d} {t}{zusatz}\n    {l}")
