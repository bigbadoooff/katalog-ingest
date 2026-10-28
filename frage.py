import os, re, sys, requests, psycopg

OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
MODELL = os.environ.get("FRAGE_MODELL", "gemma3:12b")
MAX_VERWANDT = 6
MAX_PERSONEN = 3
MAX_FOTOTAGE = 8
MAX_PERSONEN_DOKUMENTE = 3
ART = {"dokument": "Dokument", "fototag": "Fototag", "person": "Person", "kontakt": "Kontakt"}
MONATE = {"januar": [1], "februar": [2], "märz": [3], "april": [4], "mai": [5], "juni": [6], "juli": [7],
          "august": [8], "september": [9], "oktober": [10], "november": [11], "dezember": [12],
          "frühling": [3, 4, 5], "sommer": [6, 7, 8], "herbst": [9, 10, 11], "winter": [12, 1, 2],
          "weihnachten": [12], "silvester": [12, 1], "ostern": [3, 4]}
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
    weitere = [] if NEU else [
        (i, t, d, l, z, f"WEITERE DOKUMENTE AUS DEM VORGANG {v} (nur Zusammenfassung)")
        for i, t, d, l, z, v in conn.execute("""
            SELECT DISTINCT ON (d.id) d.id, d.titel, d.zeitstempel::date, d.link,
                   d.zusammenfassung, k.titel
            FROM beziehung b1
            JOIN eintrag k ON k.id = b1.zu_id AND k.typ = 'vorgang'
            JOIN beziehung b2 ON b2.zu_id = k.id AND b2.art = 'gehoert_zu'
            JOIN eintrag d ON d.id = b2.von_id
            WHERE b1.art = 'gehoert_zu' AND b1.von_id = ANY(%s) AND d.id <> ALL(%s)
              AND d.zusammenfassung IS NOT NULL
            ORDER BY d.id LIMIT %s
        """, (ids, ids, MAX_VERWANDT)).fetchall()]

    # Personen aus der Frage: ihre Fototage (gefiltert nach Jahr und Monat/Jahreszeit aus der Frage,
    # sortiert nach Nähe zur Frage) und die Dokumente, die sie betreffen
    woerter = set(re.findall(r"[a-zäöüß\-]+", frage.lower()))
    jahre = [int(j) for j in re.findall(r"\b(?:19|20)\d{2}\b", frage)]
    monate = sorted({m for w, ms in MONATE.items() if w in woerter for m in ms})
    personen = conn.execute("""
        SELECT p.id, p.titel, max(n.id) FROM eintrag p
        LEFT JOIN beziehung b ON b.von_id = p.id AND b.art = 'ist'
        LEFT JOIN eintrag n ON n.id = b.zu_id AND n.quelle = 'katalog' AND n.typ = 'person'
        WHERE p.quelle = 'immich' AND p.typ = 'person' GROUP BY p.id, p.titel
    """).fetchall()
    treffer = [p for p in personen if p[1].lower() in frage.lower()] or \
              [p for p in personen if p[1].split()[0].lower() in woerter]
    schon = set(ids) | {w[0] for w in weitere}
    for pid, name, knoten in treffer[:MAX_PERSONEN]:
        tage = conn.execute("""
            SELECT t.id, t.titel, t.zeitstempel::date, t.link, t.inhalt_auszug
            FROM beziehung b JOIN eintrag t ON t.id = b.von_id
            WHERE b.zu_id = %s AND b.art = 'zeigt'
              AND (cardinality(%s::int[]) = 0 OR extract(year FROM t.zeitstempel)::int = ANY(%s::int[]))
              AND (cardinality(%s::int[]) = 0 OR extract(month FROM t.zeitstempel)::int = ANY(%s::int[]))
            ORDER BY t.embedding <=> %s::vector LIMIT %s
        """, (pid, jahre, jahre, monate, monate, vek, MAX_FOTOTAGE)).fetchall()
        doks = conn.execute("""
            SELECT d.id, d.titel, d.zeitstempel::date, d.link, d.zusammenfassung
            FROM beziehung b JOIN eintrag d ON d.id = b.von_id
            WHERE b.zu_id = %s AND b.art = 'betrifft'
            ORDER BY d.embedding <=> %s::vector LIMIT %s
        """, (knoten, vek, MAX_PERSONEN_DOKUMENTE)).fetchall() if knoten else []
        weitere += [(*x, f"FOTOTAGE MIT {name.upper()}") for x in tage if x[0] not in schon]
        weitere += [(*x, f"DOKUMENTE ZU {name.upper()} (nur Zusammenfassung)") for x in doks if x[0] not in schon]
        schon |= {x[0] for x in tage + doks}

    typen = dict(conn.execute("SELECT id, typ FROM eintrag WHERE id = ANY(%s)",
                              ([r[0] for r in rows] + [w[0] for w in weitere],)).fetchall())
def stelle(i, eid, t, d, x):
    return f"[{i}] {ART.get(typen.get(eid), 'Eintrag')}: {t}{f' | Datum: {d}' if d else ''}\n{x}"

kontext = "\n\n".join(stelle(i, eid, t, d, x if NEU else x[:900])
                     for i, (eid, t, d, l, x) in enumerate(rows, 1))
abschnitt = None
for i, (eid, t, d, l, x, ueber) in enumerate(weitere, len(rows) + 1):
    if ueber != abschnitt:
        kontext += f"\n\n{ueber}:"
        abschnitt = ueber
    kontext += "\n\n" + stelle(i, eid, t, d, (x or "-")[:600])

prompt = (
    "Du beantwortest Fragen zu privaten Daten: Dokumente, Fototage (je Tag Orte, Personen und "
    "Bildbeschreibungen) und Kontakte. Nutze ausschließlich die folgenden Textstellen. Steht die "
    "Antwort nicht darin, sag das ehrlich. Bei Fototagen fasse zusammen, was die Bildbeschreibungen "
    "zeigen, wo die Fotos entstanden sind und wer dabei war. Antworte kurz auf Deutsch und nenne "
    "die Nummer der Stelle, auf die du dich stützt.\n\n"
    f"TEXTSTELLEN:\n{kontext}\n\nFRAGE: {frage}")

a = requests.post(f"{OLLAMA}/api/chat", json={
    "model": MODELL, "stream": False,
    "options": {"num_ctx": 8192, "temperature": 0.2},
    "messages": [{"role": "user", "content": prompt}]}, timeout=900)
a.raise_for_status()
print(a.json()["message"]["content"])
print("\nQuellen:")
quellen = {}
for i, (eid, t, d, l, _) in enumerate(rows, 1):
    quellen.setdefault(eid, [[], d, t, l])[0].append(i)
for i, (eid, t, d, l, _, _) in enumerate(weitere, len(rows) + 1):
    quellen.setdefault(eid, [[], d, t, l])[0].append(i)
for nrs, d, t, l in quellen.values():
    print(f"[{','.join(map(str, nrs))}] {f'{d} ' if d else ''}{t}\n    {l}")
