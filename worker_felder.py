import os, re, sys, json, argparse, requests, psycopg
from psycopg.types.json import Jsonb

OLLAMA = os.environ["OLLAMA_URL"].rstrip("/")
MODELL = "gemma3:12b"
MAX_TEXT = 6000
ARTEN = ["Rechnung", "Bescheid", "Vertrag", "Brief", "Mitteilung", "Anleitung", "Sonstiges"]

DATUM = {"type": ["string", "null"], "description": "JJJJ-MM-TT"}
SCHEMA = {
    "type": "object",
    "properties": {
        "art": {"type": "string", "enum": ARTEN},
        "absender": {"type": ["string", "null"]},
        "empfaenger": {"type": ["string", "null"]},
        "datum": DATUM,
        "betreff": {"type": "string"},
        "betrag": {"type": ["number", "null"]},
        "faellig_am": DATUM,
        "nummern": {"type": "array", "items": {
            "type": "object",
            "properties": {"bezeichnung": {"type": "string"}, "wert": {"type": "string"}},
            "required": ["bezeichnung", "wert"]}},
        "fristen": {"type": "array", "items": {
            "type": "object",
            "properties": {"datum": DATUM, "was": {"type": "string"}},
            "required": ["datum", "was"]}},
    },
    "required": ["art", "absender", "empfaenger", "datum", "betreff", "betrag",
                 "faellig_am", "nummern", "fristen"],
}

p = argparse.ArgumentParser()
p.add_argument("--limit", type=int, default=0, help="höchstens so viele Einträge (0 = alle)")
p.add_argument("--debug", action="store_true", help="rohe Modellantwort mit ausgeben")
args = p.parse_args()

MAX_NUMMERN = 6
# Bankverbindungen und Zugangsdaten gehören nicht in den Katalog
GEHEIM = re.compile(r"iban|bic|konto|blz|(tan|pin)\b|passwort|kennwort|zugangscode|sicherheitscode", re.I)

def iso(v):
    v = (v or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", v)
    if m:
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.match(r"^(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}|\d{2})$", v)
    if m:
        jahr = m[3] if len(m[3]) == 4 else f"20{m[3]}"
        return f"{jahr}-{int(m[2]):02d}-{int(m[1]):02d}"
    return None

def leer(v):
    return (v or "").strip().lower() in ("", "null", "none", "-", "n/a")

def bereinige(f):
    f["datum"], f["faellig_am"] = iso(f.get("datum")), iso(f.get("faellig_am"))
    f["fristen"] = [{**x, "datum": iso(x.get("datum"))} for x in f.get("fristen", []) if iso(x.get("datum"))]
    f["nummern"] = [x for x in f.get("nummern", [])
                    if not leer(x.get("wert")) and not GEHEIM.search(x.get("bezeichnung", ""))
                    and not re.match(r"^[A-Z]{2}\d{2}[\s\dX]{10,}$", x.get("wert", ""))][:MAX_NUMMERN]
    for k in ("absender", "empfaenger"):
        if leer(f.get(k)):
            f[k] = None
    if not isinstance(f.get("betrag"), (int, float)):
        f["betrag"] = None
    return f

def extrahiere(titel, tags, text):
    text = " ".join((text or "").split())
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 1000] + " [...] " + text[-1000:]
    absender = next((t[4:] for t in tags or [] if t.startswith("von:")), None)
    prompt = (
        "Lies das folgende private Dokument und fülle die Felder aus. Regeln:\n"
        + (f"- absender: In der Ablage ist der Absender als '{absender}' erfasst. Verwende genau diesen Namen.\n"
           if absender else "")
        +
        "- Sonst nur Angaben, die im Text stehen. Fehlt etwas, null bzw. leere Liste.\n"
        "- Datumsangaben immer als JJJJ-MM-TT. datum: Ausstellungs- oder Rechnungsdatum. "
        "faellig_am: Zahlungsfrist. betrag: Gesamtbetrag brutto in Euro als Zahl.\n"
        "- absender: Name der Firma, Behörde oder Person, die das Dokument ausgestellt hat, "
        "ohne Zusätze aus Briefkopf oder Logo. empfaenger: an wen es gerichtet ist, ohne Anrede.\n"
        "- betreff: der konkrete Inhalt in höchstens 12 Wörtern, nicht nur die Dokumentart "
        "(z. B. 'Bremsscheiben und Bremsbeläge hinten erneuert' statt 'Rechnung').\n"
        f"- nummern: die 1 bis {MAX_NUMMERN} wichtigsten Nummern, mit denen man das Dokument "
        "oder den Vorgang wiederfindet, die wichtigste zuerst, z. B. Rechnungs-Nr., Aktenzeichen, "
        "Vertrags-Nr., Versicherungsschein-Nr., amtl. Kennzeichen, Fahrgestell-Nr. "
        "Wert wörtlich, Bezeichnung so wie im Text davor. Deute keine Nummer um.\n"
        "- fristen: Termine oder Fristen, die der Empfänger beachten muss.\n\n"
        f"TITEL: {titel or ''}\nTEXT:\n{text}")
    r = requests.post(f"{OLLAMA}/api/chat", json={
        "model": MODELL, "stream": False, "format": SCHEMA,
        "options": {"num_ctx": 4096, "temperature": 0},
        "messages": [{"role": "user", "content": prompt}]}, timeout=900)
    r.raise_for_status()
    roh = r.json()["message"]["content"]
    if args.debug:
        print("ROH:", roh)
    return bereinige(json.loads(roh))

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
            WHERE felder IS NULL AND typ = 'dokument' AND id > %s
            ORDER BY id LIMIT 1
        """, (letzte,)).fetchone()
        if not row:
            break
        eid, titel, tags, text = row
        letzte = eid
        try:
            f = extrahiere(titel, tags, text)
        except Exception as e:
            print("Fehler bei Eintrag", eid, ":", e)
            break
        conn.execute("UPDATE eintrag SET felder = %s WHERE id = %s", (Jsonb(f), eid))
        gesamt += 1
        print(f"[{eid}] {titel}\n{json.dumps(f, ensure_ascii=False, indent=2)}\n")
print(f"{gesamt} Einträge mit Feldern versehen")
