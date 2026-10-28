import os, sys, json, requests, psycopg
from psycopg.types.json import Jsonb

# Das lokale Modell prüft für jeden Personen-Knoten (Paperless-Tag wie "Heike"), ob er einer
# benannten Immich-Person entspricht. Treffer werden nur Vorschläge, bestätigt mit vorschlaege.py.
# Jeder Knoten wird nur einmal gefragt, auch eine verworfene Antwort kommt nicht wieder.
OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
MODELL = "gemma3:12b"
KEINE = "keine"
PRUEFER = "worker_personen"

def schema(namen):
    return {"type": "object",
            "properties": {"immich_person": {"type": "string", "enum": namen + [KEINE]},
                           "sicherheit": {"type": "string", "enum": ["hoch", "mittel", "niedrig"]},
                           "begruendung": {"type": "string"}},
            "required": ["immich_person", "sicherheit", "begruendung"]}

def pruefen(knoten, dokumente, personen):
    prompt = (
        "In einem privaten Dokumentenarchiv gibt es die Person unten. Sie ist dort nur unter diesem "
        "Namen erfasst. In der Fotosammlung gibt es die folgenden benannten Personen. Entscheide, "
        f"welche davon dieselbe Person ist, oder antworte '{KEINE}', wenn keine sicher passt.\n\n"
        f"PERSON IM ARCHIV: {knoten}\nDOKUMENTE ZU DIESER PERSON:\n"
        + "\n".join(f"- {t}: {z or '(keine Zusammenfassung)'}" for t, z in dokumente)
        + "\n\nPERSONEN IN DER FOTOSAMMLUNG:\n"
        + "\n".join(f"- {p}" for p in personen.values())
        + "\n\nBegründe in einem Satz.")
    r = requests.post(f"{OLLAMA}/api/chat", json={
        "model": MODELL, "stream": False, "format": schema(list(personen)),
        "options": {"num_ctx": 8192, "temperature": 0},
        "messages": [{"role": "user", "content": prompt}]}, timeout=900)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])

try:
    requests.get(OLLAMA, timeout=5)
except requests.RequestException:
    print("Ollama nicht erreichbar, PC vermutlich aus. Abbruch ohne Änderung.")
    sys.exit(0)

with psycopg.connect(os.environ["DB_DSN"], autocommit=True) as conn:
    # Immich-Personen mit allem, was der Katalog über sie weiß
    personen, ids = {}, {}
    for eid, name, text, kontakt in conn.execute("""
        SELECT p.id, p.titel, p.inhalt_auszug, k.titel
        FROM eintrag p
        LEFT JOIN beziehung b ON b.von_id = p.id AND b.art = 'ist'
        LEFT JOIN eintrag k ON k.id = b.zu_id AND k.typ = 'kontakt'
        WHERE p.quelle = 'immich' AND p.typ = 'person' ORDER BY p.titel
    """).fetchall():
        zeilen = [z for z in (text or "").splitlines() if not z.startswith("Person:")]
        if kontakt:
            zeilen.append(f"Kontakt: {kontakt}")
        personen[name] = f"{name} ({'; '.join(zeilen)})"
        ids[name] = eid
    if not personen:
        sys.exit("Keine Immich-Personen im Katalog, erst ingest_immich.py laufen lassen.")

    offen = conn.execute("""
        SELECT n.id, n.titel FROM eintrag n
        WHERE n.quelle = 'katalog' AND n.typ = 'person'
          AND NOT EXISTS (SELECT 1 FROM vorschlag v WHERE v.art = 'beziehung'
                          AND v.inhalt->>'pruefer' = %s AND v.inhalt->>'zu' = n.id::text)
          AND NOT EXISTS (SELECT 1 FROM beziehung b JOIN eintrag p ON p.id = b.von_id
                          WHERE b.zu_id = n.id AND b.art = 'ist' AND p.quelle = 'immich')
        ORDER BY n.titel
    """, (PRUEFER,)).fetchall()
    n = 0
    for kid, knoten in offen:
        dokumente = conn.execute("""
            SELECT d.titel, d.zusammenfassung FROM beziehung b JOIN eintrag d ON d.id = b.von_id
            WHERE b.zu_id = %s AND b.art = 'betrifft' ORDER BY d.zeitstempel DESC NULLS LAST LIMIT 6
        """, (kid,)).fetchall()
        try:
            e = pruefen(knoten, dokumente, personen)
        except Exception as ex:
            print("Fehler bei", knoten, ":", ex)
            break
        wahl = e["immich_person"]
        print(f"{knoten:<10} -> {wahl:<22} {e['sicherheit']:<8} {e['begruendung']}")
        # Auch "keine" wird festgehalten (als verworfen), damit der Knoten nicht erneut gefragt wird
        conn.execute("""
            INSERT INTO vorschlag (art, inhalt, quell_ids, status) VALUES ('beziehung', %s, %s, %s)
        """, (Jsonb({"von": ids.get(wahl), "zu": kid, "art": "ist", "pruefer": PRUEFER, "modell": MODELL,
                     "begruendung": f"{MODELL} ({e['sicherheit']}): {e['begruendung']}",
                     "von_titel": wahl, "zu_titel": knoten}),
              [x for x in (ids.get(wahl), kid) if x], "verworfen" if wahl == KEINE else "offen"))
        n += wahl != KEINE
print(f"{n} neue Vorschläge, zum Prüfen: python vorschlaege.py")
