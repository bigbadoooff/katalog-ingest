import os, sys, psycopg, requests
from collections import Counter, defaultdict
from datetime import date
from psycopg.types.json import Jsonb
from abgleich import immich

# Immich als Quelle: benannte Personen und je Tag mit Fotos ein Eintrag (Orte, Personen, Beschreibungen).
# Die Fotos selbst bleiben in Immich. Verknüpft wird in verknuepfe.py.
JAHRE = 10
MAX_BESCHREIBUNGEN = 8
SICHTBAR = ("timeline", "archive")   # hidden/locked bleiben draußen

url, h = immich()
# Links im Katalog zeigen auf die Adresse, unter der Immich im Browser erreichbar ist
oeffentlich = os.environ.get("IMMICH_PUBLIC_URL", "").rstrip("/") or url
heute = date.today()
ab = heute.replace(year=heute.year - JAHRE).isoformat()

def fotos():
    seite = 1
    while seite:
        r = requests.post(f"{url}/api/search/metadata", headers=h, timeout=120, json={
            "takenAfter": f"{ab}T00:00:00.000Z", "size": 1000, "page": seite, "order": "asc",
            "withExif": True, "withPeople": True})
        r.raise_for_status()
        a = r.json()["assets"]
        yield from a["items"]
        seite = int(a["nextPage"]) if a.get("nextPage") else None

def ort(ex):
    teile = [ex.get(k) for k in ("city", "state", "country") if ex.get(k)]
    return f"{teile[0]} ({', '.join(teile[1:])})" if len(teile) > 1 else (teile[0] if teile else None)

tage = defaultdict(list)
personen = {}
for f in fotos():
    if f.get("isTrashed") or f.get("visibility") not in SICHTBAR:
        continue
    tag = (f.get("localDateTime") or f.get("fileCreatedAt") or "")[:10]
    if tag < ab:
        continue
    tage[tag].append(f)
    for p in f.get("people") or []:
        if p.get("name"):
            personen[p["id"]] = p
if not tage:
    sys.exit("Keine Fotos gefunden, Abbruch ohne Änderung.")

eintraege = []   # (quelle_id, typ, titel, text, tags, zeitstempel, geaendert, link, felder)
anzahl = Counter()
spanne = {}
for tag, fs in sorted(tage.items()):
    orte, leute, texte = Counter(), Counter(), []
    for f in fs:
        ex = f.get("exifInfo") or {}
        if ort(ex):
            orte[ort(ex)] += 1
        for p in f.get("people") or []:
            if p.get("name"):
                leute[p["id"]] += 1
        b = (ex.get("description") or "").strip()
        if b and b not in texte and len(texte) < MAX_BESCHREIBUNGEN:
            texte.append(b[:300])
    for pid in leute:
        anzahl[pid] += 1
        spanne.setdefault(pid, [tag, tag])[1] = tag
    videos = sum(f.get("type") == "VIDEO" for f in fs)
    zeilen = [f"Fototag: {tag}", f"Fotos: {len(fs)}" + (f" (davon {videos} Videos)" if videos else "")]
    if orte:
        zeilen.append("Orte: " + ", ".join(o for o, _ in orte.most_common()))
    if leute:
        zeilen.append("Personen: " + ", ".join(f"{personen[p]['name']} ({n})" for p, n in leute.most_common()))
    if texte:
        zeilen.append("Beschreibungen:\n" + "\n".join(f"- {t}" for t in texte))
    eintraege.append((f"tag:{tag}", "fototag", f"Fotos vom {date.fromisoformat(tag):%d.%m.%Y}",
                      "\n".join(zeilen), ["art:Fototag"], tag, max(f["updatedAt"] for f in fs),
                      f"{oeffentlich}/photos/{fs[0]['id']}",
                      {"datum": tag, "anzahl": len(fs), "personen": list(leute), "orte": [o for o, _ in orte.most_common()]}))

for pid, p in personen.items():
    zeilen = [f"Person: {p['name']}"]
    if p.get("birthDate"):
        zeilen.append(f"Geburtstag: {p['birthDate']}")
    zeilen.append(f"Fototage: {anzahl[pid]} (erster {spanne[pid][0]}, letzter {spanne[pid][1]})")
    eintraege.append((f"person:{pid}", "person", p["name"], "\n".join(zeilen), ["art:Person"], None,
                      p.get("updatedAt") or f"{heute}T00:00:00Z", f"{oeffentlich}/people/{pid}", {"immich_id": pid}))

with psycopg.connect(os.environ["DB_DSN"]) as conn, conn.cursor() as cur:
    neu = 0
    for qid, typ, titel, text, tags, zeit, geaendert, link, felder in eintraege:
        neu += cur.execute("""
            INSERT INTO eintrag (quelle, quelle_id, typ, titel, inhalt_auszug, tags, zeitstempel, geaendert_am, link, felder)
            VALUES ('immich', %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (quelle, quelle_id) DO UPDATE SET
                titel = EXCLUDED.titel, inhalt_auszug = EXCLUDED.inhalt_auszug, tags = EXCLUDED.tags,
                zeitstempel = EXCLUDED.zeitstempel, geaendert_am = EXCLUDED.geaendert_am,
                link = EXCLUDED.link, felder = EXCLUDED.felder, status = 'neu'
            WHERE eintrag.inhalt_auszug IS DISTINCT FROM EXCLUDED.inhalt_auszug
               OR eintrag.titel IS DISTINCT FROM EXCLUDED.titel
               OR eintrag.felder IS DISTINCT FROM EXCLUDED.felder
        """, (qid, typ, titel, text, tags, zeit, geaendert, link, Jsonb(felder))).rowcount
    weg = cur.execute("DELETE FROM eintrag WHERE quelle = 'immich' AND NOT (quelle_id = ANY(%s))",
                      ([e[0] for e in eintraege],)).rowcount
    print(f"{sum(len(f) for f in tage.values())} Fotos seit {ab}: {len(tage)} Fototage, {len(personen)} Personen; "
          f"{neu} Einträge neu/geändert, {weg} entfernt")
