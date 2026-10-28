import os, psycopg
from psycopg.types.json import Jsonb
from abgleich import finde_kontakt, immich_personen, kontakte_laden

# Richtung: Radicale-Kontakte -> Immich-Personen. Schreibt nichts nach Immich,
# sondern legt Vorschläge an; übernommen wird mit vorschlaege.py.
leute = immich_personen()

with psycopg.connect(os.environ["DB_DSN"]) as conn, conn.cursor() as cur:
    kontakte = kontakte_laden(cur)
    zaehler = {"neu": 0, "gleich": 0, "ohne_geb": 0, "schon_vorgeschlagen": 0}
    ohne, mehrdeutig = [], []
    for p in sorted(leute, key=lambda p: p["name"]):
        status, k, grund = finde_kontakt(p["name"], kontakte)
        if status == "kein":
            ohne.append(p["name"])
            continue
        if status == "mehrdeutig":
            mehrdeutig.append(f"{p['name']}: " + ", ".join(x["titel"] for x in k))
            continue
        alt = p.get("birthDate") or None
        if not k["geb"]:
            zaehler["ohne_geb"] += 1
        elif alt == k["geb"]:
            zaehler["gleich"] += 1
        else:
            n = cur.execute("""
                INSERT INTO vorschlag (art, inhalt, quell_ids)
                SELECT 'geburtstag_immich', %s, %s
                WHERE NOT EXISTS (SELECT 1 FROM vorschlag WHERE art = 'geburtstag_immich'
                                  AND inhalt->>'person_id' = %s AND inhalt->>'geburtstag' = %s)
            """, (Jsonb({"person_id": p["id"], "name": p["name"], "geburtstag": k["geb"], "alt": alt,
                         "kontakt_id": k["id"], "kontakt_titel": k["titel"], "grund": grund}),
                  [k["id"]], p["id"], k["geb"])).rowcount
            zaehler["neu" if n else "schon_vorgeschlagen"] += 1

print(f"{len(leute)} benannte Personen in Immich: {zaehler['neu']} neue Vorschläge, "
      f"{zaehler['schon_vorgeschlagen']} schon vorgeschlagen, {zaehler['gleich']} bereits gleich, "
      f"{zaehler['ohne_geb']} Kontakt ohne Geburtstag, {len(mehrdeutig)} mehrdeutig, {len(ohne)} ohne Kontakt")
for m in mehrdeutig:
    print("  mehrdeutig:", m)
if ohne:
    print("  ohne Kontakt:", ", ".join(ohne))
