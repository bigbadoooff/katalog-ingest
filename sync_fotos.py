import os, psycopg
from psycopg.types.json import Jsonb
from abgleich import finde_kontakt, immich_personen, kontakte_laden

# Richtung: Immich-Gesichtsbild -> Radicale-Kontakt, nur wenn der Kontakt noch kein Foto hat.
# Liest die Kontaktdateien schreibgeschützt (Mount /radicale) und legt Vorschläge an;
# geschrieben wird erst mit vorschlaege.py über CardDAV.
WURZEL = os.environ.get("RADICALE_DIR", "/radicale")
SAMMLUNG = "contacts_fixed"

def dateien_nach_uid():
    pfad, out = os.path.join(WURZEL, SAMMLUNG), {}
    for f in os.listdir(pfad):
        if f.startswith("."):
            continue
        text = open(os.path.join(pfad, f), encoding="utf-8", errors="replace").read()
        uid = next((z.split(":", 1)[1].strip() for z in text.splitlines() if z.upper().startswith("UID:")), f)
        foto = any(z.split(":", 1)[0].split(";")[0].split(".")[-1].upper() == "PHOTO"
                   for z in text.splitlines() if ":" in z and not z.startswith((" ", "\t")))
        out[uid] = (f, foto)
    return out

leute = immich_personen()
dateien = dateien_nach_uid()

with psycopg.connect(os.environ["DB_DSN"]) as conn, conn.cursor() as cur:
    kontakte = [k for k in kontakte_laden(cur) if k["uid"] in dateien]
    zaehler = {"neu": 0, "hat_foto": 0, "schon_vorgeschlagen": 0}
    ohne, mehrdeutig = [], []
    for p in sorted(leute, key=lambda p: p["name"]):
        status, k, grund = finde_kontakt(p["name"], kontakte)
        if status == "kein":
            ohne.append(p["name"])
            continue
        if status == "mehrdeutig":
            mehrdeutig.append(f"{p['name']}: " + ", ".join(x["titel"] for x in k))
            continue
        datei, foto = dateien[k["uid"]]
        if foto:
            zaehler["hat_foto"] += 1
            continue
        n = cur.execute("""
            INSERT INTO vorschlag (art, inhalt, quell_ids)
            SELECT 'foto_kontakt', %s, %s
            WHERE NOT EXISTS (SELECT 1 FROM vorschlag WHERE art = 'foto_kontakt'
                              AND inhalt->>'person_id' = %s AND inhalt->>'uid' = %s)
        """, (Jsonb({"person_id": p["id"], "name": p["name"], "kontakt_id": k["id"],
                     "kontakt_titel": k["titel"], "uid": k["uid"], "sammlung": SAMMLUNG,
                     "datei": datei, "grund": grund}),
              [k["id"]], p["id"], k["uid"])).rowcount
        zaehler["neu" if n else "schon_vorgeschlagen"] += 1

print(f"{len(leute)} benannte Personen in Immich: {zaehler['neu']} neue Foto-Vorschläge, "
      f"{zaehler['schon_vorgeschlagen']} schon vorgeschlagen, {zaehler['hat_foto']} Kontakt hat schon ein Foto, "
      f"{len(mehrdeutig)} mehrdeutig, {len(ohne)} ohne Kontakt")
for m in mehrdeutig:
    print("  mehrdeutig:", m)
