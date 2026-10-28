import os, re, psycopg
from psycopg.types.json import Jsonb
from abgleich import kontakte_laden, finde_kontakt, passung

FAMILIE = "Engler"
# Wörter, die beim Namensabgleich Kontakt <-> Absender nicht zählen
FUELLWOERTER = {"med", "prof", "dipl", "praxis", "zahnarztpraxis", "kinderarzt", "hno",
                "gmbh", "und", "der", "die", "das", "von"}
AEHNLICH_AB = 0.75
ARTEN = ("von", "betrifft", "gehoert_zu", "aehnlich", "ist", "erwaehnt", "zeigt", "selber_tag")

def ziel(tag, klassen):
    if tag.startswith("von:"):
        return "absender", tag[4:], "von"
    if klassen.get(tag) == "person":
        return "person", tag, "betrifft"
    if klassen.get(tag) == "vorgang":
        return "vorgang", tag, "gehoert_zu"
    return None

def woerter(s):
    return {w for w in re.findall(r"[a-zäöüß0-9]+", (s or "").lower())
            if len(w) >= 3 and w not in FUELLWOERTER}

def nummern_norm(text):
    text = re.sub(r"(\+\s*49|\b0049)\s*(\(0\))?\s*", "0", text or "")
    return re.sub(r"(?<=\d)[\s/\-().]+(?=\d)", "", text)

def kennungen(text):
    mails = {m.lower() for m in re.findall(r"^E-Mail: (\S+)", text or "", re.M)}
    tels = {t for t in (re.sub(r"\D", "", nummern_norm(x))
                        for x in re.findall(r"^Telefon: (.+)$", text or "", re.M)) if len(t) >= 8}
    return mails, tels

with psycopg.connect(os.environ["DB_DSN"]) as conn, conn.cursor() as cur:
    # Einordnung der Tags kommt vom lokalen Modell (worker_tags.py)
    klassen = dict(cur.execute("SELECT tag, klasse FROM tag_klasse").fetchall())
    soll, unbekannt = set(), set()
    for eid, tags in cur.execute("SELECT id, tags FROM eintrag WHERE quelle <> 'katalog'").fetchall():
        for t in tags or []:
            z = ziel(t, klassen)
            if z:
                soll.add((eid, *z, t))
            elif not t.startswith(("art:", "gruppe:")) and t not in klassen:
                unbekannt.add(t)

    knoten = {}
    for typ, name in sorted({(s[1], s[2]) for s in soll}):
        knoten[typ, name] = cur.execute("""
            INSERT INTO eintrag (quelle, quelle_id, typ, titel, ableitung, status)
            VALUES ('katalog', %s, %s, %s, true, 'fertig')
            ON CONFLICT (quelle, quelle_id) DO UPDATE SET titel = EXCLUDED.titel
            RETURNING id
        """, (f"{typ}:{name}", typ, name)).fetchone()[0]

    kanten = [(eid, knoten[typ, name], art, f"Paperless-Tag {t}") for eid, typ, name, art, t in soll]

    # Kontakte: über E-Mail/Telefon sicher an Dokumente, über den Namen nur als Vorschlag
    # an Absender/Personen (bestätigt mit vorschlaege.py)
    vorschlaege = []
    kontakte = cur.execute("SELECT id, titel, inhalt_auszug FROM eintrag WHERE typ = 'kontakt'").fetchall()
    dokumente = [(i, nummern_norm(t).lower()) for i, t in
                 cur.execute("SELECT id, inhalt_auszug FROM eintrag WHERE typ = 'dokument'").fetchall()]
    for kid, titel, text in kontakte:
        firma = (re.search(r"^Firma: (.+)$", text or "", re.M) or [None, ""])[1]
        name_w, teile = woerter(f"{titel} {firma}"), (titel or "").split()
        for (typ, name), hid in knoten.items():
            if typ == "absender" and woerter(name) and woerter(name) <= name_w:
                vorschlaege.append((kid, hid, "ist", f"Name passt zu Absender {name}", titel, name))
            elif typ == "person" and teile and teile[0] == name and teile[-1] == FAMILIE:
                vorschlaege.append((kid, hid, "ist", f"Name passt zu Person {name}", titel, name))
        mails, tels = kennungen(text)
        for did, dtext in dokumente:
            treffer = [m for m in mails if m in dtext] + \
                      [t for t in tels if re.search(rf"(?<!\d){t}(?!\d)", dtext)]
            if treffer:
                kanten.append((did, kid, "erwaehnt", "Kontaktdaten im Dokument: " + ", ".join(sorted(treffer)[:3])))

    # Immich: Fototag -> abgebildete Person (Gesichtserkennung), Dokument -> Fototag mit gleichem Datum,
    # Immich-Person -> Kontakt nur bei eindeutig gleichem Namen (Stufe 1-2 in abgleich.passung)
    ipers = dict(cur.execute("SELECT quelle_id, id FROM eintrag WHERE quelle = 'immich' AND typ = 'person'").fetchall())
    for tid, f in cur.execute("SELECT id, felder FROM eintrag WHERE typ = 'fototag'").fetchall():
        kanten += [(tid, ipers[f"person:{p}"], "zeigt", "Gesichtserkennung Immich")
                   for p in f.get("personen", []) if f"person:{p}" in ipers]
    kanten += cur.execute("""
        SELECT d.id, t.id, 'selber_tag', 'Dokumentdatum ' || (d.felder->>'datum')
        FROM eintrag d JOIN eintrag t ON t.typ = 'fototag' AND t.felder->>'datum' = d.felder->>'datum'
        WHERE d.typ = 'dokument'
    """).fetchall()
    alle_kontakte = kontakte_laden(cur)
    for eid, name in cur.execute("SELECT id, titel FROM eintrag WHERE quelle = 'immich' AND typ = 'person'").fetchall():
        ergebnis, k, grund = finde_kontakt(name, alle_kontakte)
        if ergebnis == "ok" and passung(name, k)[0] <= 2:
            kanten.append((eid, k["id"], "ist", f"Immich-Person, {grund}"))

    cur.execute("CREATE TEMP TABLE soll (von_id bigint, zu_id bigint, art text, begruendung text) ON COMMIT DROP")
    cur.executemany("INSERT INTO soll VALUES (%s, %s, %s, %s)", kanten)
    cur.execute("""
        INSERT INTO soll
        SELECT a.id, b.id, 'aehnlich', 'Ähnlichkeit ' || round((1 - (a.embedding <=> b.embedding))::numeric, 2)
        FROM eintrag a JOIN eintrag b ON a.id < b.id
        WHERE a.typ = 'dokument' AND b.typ = 'dokument'
          AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
          AND 1 - (a.embedding <=> b.embedding) >= %s
          AND NOT EXISTS (SELECT 1 FROM soll x JOIN soll y ON x.zu_id = y.zu_id
                          WHERE x.von_id = a.id AND y.von_id = b.id)
    """, (AEHNLICH_AB,))

    weg = cur.execute("""
        DELETE FROM beziehung b WHERE b.ableitung AND b.art = ANY(%s)
          AND NOT EXISTS (SELECT 1 FROM soll s
                          WHERE s.von_id = b.von_id AND s.zu_id = b.zu_id AND s.art = b.art)
    """, (list(ARTEN),)).rowcount
    neu = cur.execute("""
        INSERT INTO beziehung (von_id, zu_id, art, ableitung, begruendung)
        SELECT DISTINCT ON (von_id, zu_id, art) von_id, zu_id, art, true, begruendung FROM soll
        ON CONFLICT (von_id, zu_id, art) DO UPDATE SET begruendung = EXCLUDED.begruendung
        WHERE beziehung.begruendung IS DISTINCT FROM EXCLUDED.begruendung
    """).rowcount
    verwaist = cur.execute("""
        DELETE FROM eintrag e WHERE e.quelle = 'katalog'
          AND NOT EXISTS (SELECT 1 FROM beziehung b WHERE e.id IN (b.von_id, b.zu_id))
    """).rowcount

    # Jeder Vorschlag nur einmal, auch verworfene kommen nicht wieder
    neue_vorschlaege = 0
    for von, zu, art, begr, von_t, zu_t in vorschlaege:
        neue_vorschlaege += cur.execute("""
            INSERT INTO vorschlag (art, inhalt, quell_ids)
            SELECT 'beziehung', %s, %s
            WHERE NOT EXISTS (SELECT 1 FROM vorschlag WHERE art = 'beziehung'
                              AND inhalt->>'von' = %s AND inhalt->>'zu' = %s AND inhalt->>'art' = %s)
        """, (Jsonb({"von": von, "zu": zu, "art": art, "begruendung": begr,
                     "von_titel": von_t, "zu_titel": zu_t}),
              [von, zu], str(von), str(zu), art)).rowcount
    print(f"{neue_vorschlaege} neue Vorschläge, zum Prüfen: python vorschlaege.py")

    print(f"{len(knoten)} Knoten, {neu} Beziehungen neu/geändert, {weg} entfernt, {verwaist} verwaiste Knoten gelöscht")
    for art, n in cur.execute("SELECT art, count(*) FROM beziehung GROUP BY 1 ORDER BY 1").fetchall():
        print(f"  {art}: {n}")
    if unbekannt:
        print("Noch nicht eingeordnete Tags (worker_tags.py laufen lassen):", ", ".join(sorted(unbekannt)))
