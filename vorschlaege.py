import os, io, sys, base64, requests, psycopg

HILFE = """Aufruf:
  python vorschlaege.py              offene Vorschläge anzeigen
  python vorschlaege.py ja 3 5       Vorschläge 3 und 5 übernehmen
  python vorschlaege.py nein 4       Vorschlag 4 verwerfen (kommt nicht wieder)"""

def beschreibung(art, i):
    if art == "geburtstag_immich":
        alt = f" (bisher {i['alt']})" if i.get("alt") else ""
        return (f"Immich: Geburtstag {i['name']} -> {i['geburtstag']}{alt}",
                f"aus Kontakt {i['kontakt_titel']}: {i['grund']}")
    if art == "foto_kontakt":
        return (f"Kontakt {i['kontakt_titel']}: Foto von Immich-Person {i['name']}",
                f"Kontakt hat noch kein Foto; {i['grund']}")
    return f"{i.get('von_titel')}  --{i.get('art')}-->  {i.get('zu_titel')}", i.get("begruendung")

def uebernimm_beziehung(cur, vid, i):
    da = cur.execute("SELECT count(*) FROM eintrag WHERE id IN (%s, %s)", (i["von"], i["zu"])).fetchone()[0]
    if da < 2:
        return "Einträge existieren nicht mehr, bitte verwerfen"
    # ableitung = false: von Hand bestätigt, verknuepfe.py lässt sie stehen
    cur.execute("""
        INSERT INTO beziehung (von_id, zu_id, art, ableitung, begruendung)
        VALUES (%s, %s, %s, false, %s)
        ON CONFLICT (von_id, zu_id, art) DO UPDATE SET ableitung = false,
                                                       begruendung = EXCLUDED.begruendung
    """, (i["von"], i["zu"], i["art"], "bestätigt: " + i.get("begruendung", "")))
    return None

def uebernimm_geburtstag(i):
    url = os.environ["IMMICH_URL"].rstrip("/")
    h = {"x-api-key": os.environ["IMMICH_API_KEY"]}
    jetzt = requests.get(f"{url}/api/people/{i['person_id']}", headers=h, timeout=30)
    if jetzt.status_code != 200:
        return f"Immich-Person nicht lesbar (HTTP {jetzt.status_code})"
    if (jetzt.json().get("birthDate") or None) != (i.get("alt") or None):
        return "Geburtstag wurde in Immich inzwischen geändert, nichts geschrieben"
    r = requests.put(f"{url}/api/people/{i['person_id']}", headers=h,
                     json={"birthDate": i["geburtstag"]}, timeout=30)
    if r.status_code != 200:
        return f"Immich-Fehler HTTP {r.status_code}: {r.text[:200]}"
    return None

def falte(zeile):
    # vCard-Zeilen höchstens 75 Zeichen, Fortsetzung mit führendem Leerzeichen
    teile = [zeile[:75]] + [" " + zeile[i:i + 74] for i in range(75, len(zeile), 74)]
    return "\r\n".join(teile)

def uebernimm_foto(i):
    rad_url = os.environ.get("RADICALE_URL", "").rstrip("/")
    auth = (os.environ.get("RADICALE_USER", ""), os.environ.get("RADICALE_PASSWORD", ""))
    if not rad_url or not all(auth):
        return "RADICALE_URL, RADICALE_USER oder RADICALE_PASSWORD fehlt in .env"
    url, h = os.environ["IMMICH_URL"].rstrip("/"), {"x-api-key": os.environ["IMMICH_API_KEY"]}
    bild = requests.get(f"{url}/api/people/{i['person_id']}/thumbnail", headers=h, timeout=60)
    if bild.status_code != 200 or not bild.content:
        return f"Immich-Bild nicht lesbar (HTTP {bild.status_code})"
    daten = bild.content
    if not daten.startswith(b"\xff\xd8"):
        from PIL import Image
        puffer = io.BytesIO()
        Image.open(io.BytesIO(daten)).convert("RGB").save(puffer, "JPEG", quality=90)
        daten = puffer.getvalue()

    karte_url = f"{rad_url}/{auth[0]}/{i['sammlung']}/{i['datei']}"
    alt = requests.get(karte_url, auth=auth, timeout=30)
    if alt.status_code != 200:
        return f"Kontakt per CardDAV nicht lesbar (HTTP {alt.status_code})"
    text = alt.content.decode("utf-8")
    zeilen = text.replace("\r\n", "\n").split("\n")
    if any(z.split(":", 1)[0].split(";")[0].split(".")[-1].upper() == "PHOTO"
           for z in zeilen if ":" in z and not z.startswith((" ", "\t"))):
        return "Kontakt hat inzwischen ein Foto, nichts geschrieben"
    b64 = base64.b64encode(daten).decode()
    v4 = any(z.strip().upper() == "VERSION:4.0" for z in zeilen)
    foto = f"PHOTO:data:image/jpeg;base64,{b64}" if v4 else f"PHOTO;ENCODING=b;TYPE=JPEG:{b64}"
    ende = max(n for n, z in enumerate(zeilen) if z.strip().upper() == "END:VCARD")
    neu = "\r\n".join(z for z in zeilen[:ende] if z) + "\r\n" + falte(foto) + "\r\nEND:VCARD\r\n"
    kopf = {"Content-Type": "text/vcard; charset=utf-8"}
    if alt.headers.get("ETag"):
        kopf["If-Match"] = alt.headers["ETag"]
    r = requests.put(karte_url, data=neu.encode("utf-8"), headers=kopf, auth=auth, timeout=30)
    if r.status_code not in (200, 201, 204):
        return f"CardDAV-Fehler HTTP {r.status_code}: {r.text[:200]}"
    return None

with psycopg.connect(os.environ["DB_DSN"], autocommit=True) as conn, conn.cursor() as cur:
    if len(sys.argv) == 1:
        rows = cur.execute("SELECT id, art, inhalt FROM vorschlag WHERE status = 'offen' ORDER BY id").fetchall()
        for vid, art, i in rows:
            kopf, grund = beschreibung(art, i)
            print(f"[{vid}] {kopf}\n      {grund}")
        print(f"\n{len(rows)} offene Vorschläge\n\n{HILFE}")
        sys.exit()

    aktion, ids = sys.argv[1], [int(x) for x in sys.argv[2:]]
    if aktion not in ("ja", "nein") or not ids:
        sys.exit(HILFE)
    for vid in ids:
        row = cur.execute("SELECT art, inhalt FROM vorschlag WHERE id = %s AND status = 'offen'",
                          (vid,)).fetchone()
        if not row:
            print(f"[{vid}] nicht gefunden oder nicht mehr offen")
            continue
        art, i = row
        kopf, _ = beschreibung(art, i)
        if aktion == "nein":
            cur.execute("UPDATE vorschlag SET status = 'verworfen' WHERE id = %s", (vid,))
            print(f"[{vid}] verworfen: {kopf}")
            continue
        if art == "beziehung":
            fehler = uebernimm_beziehung(cur, vid, i)
        elif art == "geburtstag_immich":
            fehler = uebernimm_geburtstag(i)
        elif art == "foto_kontakt":
            fehler = uebernimm_foto(i)
        else:
            fehler = f"Art {art} kann noch nicht übernommen werden"
        if fehler:
            print(f"[{vid}] NICHT übernommen: {fehler}")
            continue
        cur.execute("UPDATE vorschlag SET status = 'uebernommen' WHERE id = %s", (vid,))
        print(f"[{vid}] übernommen: {kopf}")
