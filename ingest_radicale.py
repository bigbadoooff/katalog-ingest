import os, sys, json, psycopg
from datetime import datetime, timezone

# Radicale-Sammlungen von martin, schreibgeschützt in den Container eingebunden
WURZEL = os.environ.get("RADICALE_DIR", "/radicale")
FELDER = (("NICKNAME", "Spitzname"), ("TITLE", "Funktion"), ("EMAIL", "E-Mail"), ("TEL", "Telefon"), ("ADR", "Adresse"),
          ("BDAY", "Geburtstag"), ("URL", "Web"), ("NOTE", "Notiz"))

def entfalte(text):
    zeilen = []
    for z in text.replace("\r\n", "\n").split("\n"):
        if z[:1] in (" ", "\t") and zeilen:
            zeilen[-1] += z[1:]
        else:
            zeilen.append(z)
    return zeilen

def wert(v):
    return (v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
             .replace("\\;", ";").replace("\\\\", "\\").strip())

def karten(text):
    karte = None
    for z in entfalte(text):
        if z.upper() == "BEGIN:VCARD":
            karte = {}
        elif z.upper() == "END:VCARD":
            if karte is not None:
                yield karte
            karte = None
        elif karte is not None and ":" in z:
            kopf, v = z.split(":", 1)
            name = kopf.split(";")[0].split(".")[-1].upper()
            if name not in ("PHOTO", "LOGO", "SOUND", "KEY"):
                karte.setdefault(name, []).append(v)

def teile(v):
    return [p for p in (wert(x) for x in v.split(";")) if p]

def eintrag_von(k):
    n = [wert(x) for x in k.get("N", [""])[0].split(";")] + ["", ""]
    nach, vor = n[0], n[1]
    org = ", ".join(", ".join(teile(o)) for o in k.get("ORG", []))
    titel = wert(k.get("FN", [""])[0]) or f"{vor} {nach}".strip() or org or "(ohne Namen)"
    zeilen = [f"Kontakt: {titel}"] + ([f"Vorname: {vor}"] if vor else []) + \
             ([f"Nachname: {nach}"] if nach else []) + ([f"Firma: {org}"] if org else [])
    for key, label in FELDER:
        for v in k.get(key, []):
            v = ", ".join(teile(v)) if key == "ADR" else wert(v)
            if v:
                zeilen.append(f"{label}: {v}")
    tags = ["art:Kontakt"] + [f"gruppe:{wert(c)}" for v in k.get("CATEGORIES", []) for c in v.split(",") if wert(c)]
    return titel, "\n".join(zeilen), tags

dateien = []
for props in sorted(os.listdir(WURZEL)):
    pfad = os.path.join(WURZEL, props)
    try:
        tag = json.load(open(os.path.join(pfad, ".Radicale.props"))).get("tag")
    except (OSError, ValueError):
        continue
    if tag == "VADDRESSBOOK":
        dateien += [os.path.join(pfad, f) for f in os.listdir(pfad)
                    if not f.startswith(".") and os.path.isfile(os.path.join(pfad, f))]
if not dateien:
    sys.exit(f"Keine Kontakte unter {WURZEL} gefunden, ist das Verzeichnis eingebunden? Abbruch ohne Änderung.")

with psycopg.connect(os.environ["DB_DSN"]) as conn, conn.cursor() as cur:
    gesehen = set()
    for f in dateien:
        stand = datetime.fromtimestamp(os.path.getmtime(f), timezone.utc)
        for k in karten(open(f, encoding="utf-8", errors="replace").read()):
            uid = wert(k.get("UID", [os.path.basename(f)])[0])
            if uid in gesehen:
                continue
            gesehen.add(uid)
            titel, text, tags = eintrag_von(k)
            cur.execute("""
                INSERT INTO eintrag (quelle, quelle_id, typ, titel, inhalt_auszug, tags, geaendert_am)
                VALUES ('radicale', %s, 'kontakt', %s, %s, %s, %s)
                ON CONFLICT (quelle, quelle_id) DO UPDATE SET
                    titel = EXCLUDED.titel,
                    inhalt_auszug = EXCLUDED.inhalt_auszug,
                    tags = EXCLUDED.tags,
                    status = CASE WHEN eintrag.inhalt_auszug IS DISTINCT FROM EXCLUDED.inhalt_auszug
                                    OR eintrag.titel IS DISTINCT FROM EXCLUDED.titel
                                  THEN 'neu' ELSE eintrag.status END,
                    geaendert_am = EXCLUDED.geaendert_am
            """, (uid, titel, text, tags, stand))
    weg = cur.execute("DELETE FROM eintrag WHERE quelle = 'radicale' AND quelle_id <> ALL(%s)",
                      (list(gesehen),)).rowcount
    print(f"{len(gesehen)} Kontakte aus {len(dateien)} Dateien verarbeitet, {weg} gelöschte entfernt")
