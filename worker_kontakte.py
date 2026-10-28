import os, sys, json, argparse, requests, psycopg
from psycopg.types.json import Jsonb
from abgleich import kontakte_laden

# Das lokale Modell prüft für jede Immich-Person ohne Kontakt, ob einer der noch freien Kontakte
# dieselbe Person ist. Treffer werden nur Vorschläge, bestätigt mit vorschlaege.py.
# Jede Person wird nur einmal gefragt, auch eine verworfene Antwort kommt nicht wieder.
OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
# gemma3:12b nahm im Test lieber einen Verwandten als "keine", deshalb hier ein größeres Modell
MODELL = os.environ.get("KONTAKTE_MODELL", "qwen2.5:14b")
KEINE = "keine"
PRUEFER = "worker_kontakte"

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=0)
args = ap.parse_args()

def zeile(k):
    teile = [f"{k['vor']} {k['nach']}".strip(), k["spitz"] and f"Spitzname {k['spitz']}",
             k["geb"] and f"Geburtstag {k['geb']}"]
    return f"[{k['id']}] {k['titel']}" + "".join(f" | {t}" for t in teile if t and t != k["titel"])

def pruefen(person, kontakte):
    prompt = (
        "In einer Fotosammlung gibt es die Person unten. Im Adressbuch stehen die folgenden Kontakte. "
        "Entscheide, welcher Kontakt dieselbe Person ist, und antworte mit seiner Nummer, "
        f"oder antworte '{KEINE}', wenn keiner sicher passt. Dieselbe Person heißt derselbe Mensch. "
        "Ein Verwandter oder jemand mit gleichem Nachnamen ist nicht dieselbe Person.\n\n"
        f"PERSON IN DER FOTOSAMMLUNG:\n{person}\n\nKONTAKTE:\n"
        + "\n".join(zeile(k) for k in kontakte)
        + "\n\nBegründe zuerst in einem Satz, dann entscheide.")
    # Begründung zuerst, damit das Modell abwägt, bevor es sich auf eine Nummer festlegt
    schema = {"type": "object",
              "properties": {"begruendung": {"type": "string"},
                             "kontakt": {"type": "string", "enum": [str(k["id"]) for k in kontakte] + [KEINE]},
                             "sicherheit": {"type": "string", "enum": ["hoch", "mittel", "niedrig"]}},
              "required": ["begruendung", "kontakt", "sicherheit"]}
    r = requests.post(f"{OLLAMA}/api/chat", json={
        "model": MODELL, "stream": False, "format": schema,
        "options": {"num_ctx": 12288, "temperature": 0},
        "messages": [{"role": "user", "content": prompt}]}, timeout=1800)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])

try:
    requests.get(OLLAMA, timeout=5)
except requests.RequestException:
    print("Ollama nicht erreichbar, PC vermutlich aus. Abbruch ohne Änderung.")
    sys.exit(0)

with psycopg.connect(os.environ["DB_DSN"], autocommit=True) as conn:
    cur = conn.cursor()
    vergeben = {r[0] for r in cur.execute("""
        SELECT b.zu_id FROM beziehung b JOIN eintrag p ON p.id = b.von_id
        WHERE b.art = 'ist' AND p.quelle = 'immich'""").fetchall()}
    frei = [k for k in kontakte_laden(cur) if k["id"] not in vergeben]
    titel = {k["id"]: k["titel"] for k in frei}
    offen = cur.execute("""
        SELECT p.id, p.titel, p.inhalt_auszug FROM eintrag p
        WHERE p.quelle = 'immich' AND p.typ = 'person'
          AND NOT EXISTS (SELECT 1 FROM beziehung b JOIN eintrag k ON k.id = b.zu_id
                          WHERE b.von_id = p.id AND b.art = 'ist' AND k.typ = 'kontakt')
          AND NOT EXISTS (SELECT 1 FROM vorschlag v WHERE v.art = 'beziehung'
                          AND v.inhalt->>'pruefer' = %s AND v.inhalt->>'von' = p.id::text)
        ORDER BY p.titel
    """, (PRUEFER,)).fetchall()
    if args.limit:
        offen = offen[:args.limit]
    n = 0
    for pid, name, text in offen:
        try:
            e = pruefen(text, frei)
        except Exception as ex:
            print("Fehler bei", name, ":", ex)
            break
        wahl = None if e["kontakt"] == KEINE else int(e["kontakt"])
        print(f"{name:<22} -> {titel.get(wahl, KEINE):<22} {e['sicherheit']:<8} {e['begruendung']}")
        # Auch "keine" wird festgehalten (als verworfen), damit die Person nicht erneut gefragt wird
        cur.execute("""
            INSERT INTO vorschlag (art, inhalt, quell_ids, status) VALUES ('beziehung', %s, %s, %s)
        """, (Jsonb({"von": pid, "zu": wahl, "art": "ist", "pruefer": PRUEFER, "modell": MODELL,
                     "begruendung": f"{MODELL} ({e['sicherheit']}): {e['begruendung']}",
                     "von_titel": name, "zu_titel": titel.get(wahl, KEINE)}),
              [x for x in (pid, wahl) if x], "offen" if wahl else "verworfen"))
        n += wahl is not None
print(f"{n} neue Vorschläge, zum Prüfen: python vorschlaege.py")
