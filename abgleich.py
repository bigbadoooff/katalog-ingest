import os, re, requests

# Gemeinsamer Abgleich Immich-Person <-> Radicale-Kontakt für sync_geburtstage.py und sync_fotos.py

def norm(s):
    return " ".join(re.sub(r"[^\wäöüß]+", " ", (s or "").lower()).split())

def feld(text, name):
    m = re.search(rf"^{name}: (.+)$", text or "", re.M)
    return m[1].strip() if m else ""

def datum(v):
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", v or "")
    return f"{m[1]}-{m[2]}-{m[3]}" if m else None

def passung(person, k):
    """Je kleiner, desto sicherer; None = passt nicht."""
    pn = norm(person)
    pw = pn.split()
    titel, vor, nach, spitz = (norm(k["titel"]), norm(k["vor"]), norm(k["nach"]), norm(k["spitz"]))
    if titel == pn:
        return 1, "gleicher Name"
    if set(pw) in (set(titel.split()), set(f"{vor} {nach}".split())):
        return 2, "gleicher Name, andere Schreibweise/Reihenfolge"
    if len(pw) >= 2 and nach and nach == pw[-1]:
        if spitz and spitz == pw[0]:
            return 3, "Nachname gleich, Spitzname passt"
        if pw[0] in vor.split():
            return 3, "Nachname gleich, Vorname enthalten"
        if vor and len(min(vor, pw[0], key=len)) >= 3 and (vor.startswith(pw[0]) or pw[0].startswith(vor)):
            return 4, "Nachname gleich, Vorname ähnlich"
    return None

def kontakte_laden(cur):
    return [{"id": i, "uid": u, "titel": t, "vor": feld(x, "Vorname"), "nach": feld(x, "Nachname"),
             "spitz": feld(x, "Spitzname"), "geb": datum(feld(x, "Geburtstag"))}
            for i, u, t, x in cur.execute(
                "SELECT id, quelle_id, titel, inhalt_auszug FROM eintrag WHERE typ = 'kontakt'").fetchall()]

def finde_kontakt(name, kontakte):
    """('ok', kontakt, grund) | ('mehrdeutig', [kontakte], None) | ('kein', None, None)"""
    treffer = [(s, k) for s, k in ((passung(name, k), k) for k in kontakte) if s]
    if not treffer:
        return "kein", None, None
    beste = min(s[0] for s, _ in treffer)
    treffer = [(s, k) for s, k in treffer if s[0] == beste]
    if len(treffer) > 1:
        return "mehrdeutig", [k for _, k in treffer], None
    return "ok", treffer[0][1], treffer[0][0][1]

def immich():
    url = os.environ.get("IMMICH_URL", "").rstrip("/")
    key = os.environ.get("IMMICH_API_KEY", "")
    if not url or not key:
        raise SystemExit("IMMICH_URL oder IMMICH_API_KEY fehlt in .env. Abbruch ohne Änderung.")
    return url, {"x-api-key": key}

def immich_personen():
    url, h = immich()
    leute = requests.get(f"{url}/api/people?withHidden=true&size=1000", headers=h, timeout=30).json()["people"]
    return [p for p in leute if p.get("name")]
