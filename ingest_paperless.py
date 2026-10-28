import os, requests, psycopg

URL = os.environ["PAPERLESS_URL"].rstrip("/")
PUB = os.environ["PAPERLESS_PUBLIC_URL"].rstrip("/")
H = {"Authorization": f"Token {os.environ['PAPERLESS_TOKEN']}"}

def alle(pfad):
    url, out = f"{URL}/api/{pfad}/?page_size=100", []
    while url:
        r = requests.get(url, headers=H, timeout=30)
        r.raise_for_status()
        d = r.json()
        out += d["results"]
        url = d["next"]
        if url and url.startswith("http://") and URL.startswith("http://localhost"):
            url = URL + url[url.index("/api/"):]
    return out

tags = {t["id"]: t["name"] for t in alle("tags")}
korr = {c["id"]: c["name"] for c in alle("correspondents")}
dtyp = {d["id"]: d["name"] for d in alle("document_types")}

with psycopg.connect(os.environ["DB_DSN"]) as conn, conn.cursor() as cur:
    n, fehlend = 0, set()
    for d in alle("documents"):
        t = [tags[i] for i in d["tags"] if i in tags]
        fehlend.update(("Tag", i) for i in d["tags"] if i not in tags)
        if d.get("correspondent") and d["correspondent"] not in korr:
            fehlend.add(("Korrespondent", d["correspondent"]))
        if d.get("document_type") and d["document_type"] not in dtyp:
            fehlend.add(("Dokumenttyp", d["document_type"]))
        if d.get("correspondent") in korr:
            t.append("von:" + korr[d["correspondent"]])
        if d.get("document_type") in dtyp:
            t.append("art:" + dtyp[d["document_type"]])
        cur.execute("""
            INSERT INTO eintrag (quelle, quelle_id, typ, titel, inhalt_auszug,
                                 zeitstempel, link, tags, geaendert_am)
            VALUES ('paperless', %s, 'dokument', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (quelle, quelle_id) DO UPDATE SET
                titel = EXCLUDED.titel,
                inhalt_auszug = EXCLUDED.inhalt_auszug,
                tags = EXCLUDED.tags,
                zeitstempel = EXCLUDED.zeitstempel,
                link = EXCLUDED.link,
                status = CASE WHEN eintrag.geaendert_am < EXCLUDED.geaendert_am
                              THEN 'neu' ELSE eintrag.status END,
                zusammenfassung = CASE WHEN eintrag.geaendert_am < EXCLUDED.geaendert_am
                              THEN NULL ELSE eintrag.zusammenfassung END,
                felder = CASE WHEN eintrag.geaendert_am < EXCLUDED.geaendert_am
                              THEN NULL ELSE eintrag.felder END,
                geaendert_am = EXCLUDED.geaendert_am
        """, (str(d["id"]), d["title"], (d.get("content") or ""),
              d.get("created"), f"{PUB}/documents/{d['id']}/details", t,
              d.get("modified")))
        n += 1
    print(f"{n} Dokumente verarbeitet")
    if fehlend:
        print("WARNUNG: Diese IDs sind für den Token-Benutzer nicht sichtbar, Rechte in Paperless prüfen:",
              ", ".join(f"{a} {i}" for a, i in sorted(fehlend)))
