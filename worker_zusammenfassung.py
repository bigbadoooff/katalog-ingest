import os, sys, argparse, requests, psycopg

OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
MODELL = "gemma3:12b"
MAX_TEXT = 6000

p = argparse.ArgumentParser()
p.add_argument("--limit", type=int, default=0, help="höchstens so viele Einträge (0 = alle)")
args = p.parse_args()

def fasse_zusammen(titel, tags, text):
    text = " ".join((text or "").split())
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 1000] + " [...] " + text[-1000:]
    prompt = (
        "Fasse das folgende private Dokument in höchstens 3 kurzen, sachlichen Sätzen auf Deutsch zusammen. "
        "Nenne, worum es geht, von welcher Firma oder Person es stammt und die wichtigsten Daten, "
        "Beträge oder Fristen, sofern sie im Text stehen. "
        "Übernimm Nummern wörtlich mit der Bezeichnung aus dem Text, deute sie nicht um. "
        "Lass Sachbearbeiter, Kundennummern und Bankdaten weg. "
        "Erfinde nichts. Gib nur die Zusammenfassung aus.\n\n"
        f"TITEL: {titel or ''}\nTAGS: {', '.join(tags or [])}\nTEXT:\n{text}")
    r = requests.post(f"{OLLAMA}/api/chat", json={
        "model": MODELL, "stream": False,
        "options": {"num_ctx": 4096, "temperature": 0.2},
        "messages": [{"role": "user", "content": prompt}]}, timeout=600)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()

try:
    requests.get(OLLAMA, timeout=5)
except requests.RequestException:
    print("Ollama nicht erreichbar, PC vermutlich aus. Abbruch ohne Änderung.")
    sys.exit(0)

gesamt, letzte = 0, 0
with psycopg.connect(os.environ["DB_DSN"], autocommit=True) as conn:
    while not args.limit or gesamt < args.limit:
        row = conn.execute("""
            SELECT id, titel, tags, inhalt_auszug FROM eintrag
            WHERE zusammenfassung IS NULL AND typ = 'dokument' AND id > %s
            ORDER BY id LIMIT 1
        """, (letzte,)).fetchone()
        if not row:
            break
        eid, titel, tags, text = row
        letzte = eid
        try:
            z = fasse_zusammen(titel, tags, text)
        except Exception as e:
            print("Fehler bei Eintrag", eid, ":", e)
            break
        if not z:
            print("Leere Antwort bei Eintrag", eid, ", übersprungen")
            continue
        conn.execute("UPDATE eintrag SET zusammenfassung = %s WHERE id = %s", (z, eid))
        gesamt += 1
        print(f"[{eid}] {titel}\n      {z}\n")
print(f"{gesamt} Einträge zusammengefasst")
