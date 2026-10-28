#!/usr/bin/env python3
"""Setzt fuer jede benannte Person in Immich das schaerfste Gesichtsfoto als Vorschaubild.

Benoetigt: pip install requests opencv-python-headless numpy
Zugangsdaten aus .env: IMMICH_URL und IMMICH_API_KEY (oder per Umgebungsvariable).
API-Key-Rechte: person.read, person.update, asset.read, asset.view, face.read
"""
import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import requests

MIN_FACE_PX = 100      # kleinere Gesichter (im Preview-Bild) werden ignoriert
CROP_MARGIN = 0.15     # Rand um die Gesichtsbox
EVAL_SIZE = 160        # Gesicht wird fuer die Messung auf diese Kantenlaenge gebracht
IMPROVE_FACTOR = 1.10  # nur wechseln, wenn neues Bild mind. 10 % schaerfer ist


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Immich:
    def __init__(self, base_url: str, api_key: str):
        base = base_url.rstrip("/")
        self.base = base if base.endswith("/api") else base + "/api"
        self.s = requests.Session()
        self.s.headers["x-api-key"] = api_key
        self.s.headers["Accept"] = "application/json"

    def get(self, path, **kw):
        r = self.s.get(self.base + path, timeout=60, **kw)
        r.raise_for_status()
        return r

    def people(self):
        page = 1
        while True:
            data = self.get("/people", params={"withHidden": "false", "size": 500, "page": page}).json()
            yield from data["people"]
            if not data.get("hasNextPage"):
                break
            page += 1

    def assets_of_person(self, person_id, limit):
        page, seen = 1, 0
        while seen < limit:
            r = self.s.post(
                self.base + "/search/metadata",
                json={"personIds": [person_id], "type": "IMAGE", "size": 100, "page": page, "order": "desc"},
                timeout=60,
            )
            r.raise_for_status()
            block = r.json()["assets"]
            for item in block["items"]:
                yield item["id"]
                seen += 1
                if seen >= limit:
                    return
            if not block.get("nextPage"):
                return
            page = int(block["nextPage"])

    def faces(self, asset_id):
        return self.get("/faces", params={"id": asset_id}).json()

    def preview(self, asset_id):
        return self.get(f"/assets/{asset_id}/thumbnail", params={"size": "preview"}).content

    def set_featured(self, person_id, asset_id):
        r = self.s.put(self.base + f"/people/{person_id}", json={"featureFaceAssetId": asset_id}, timeout=60)
        if not r.ok:
            print(f"   FEHLER {r.status_code}: {r.text[:300]}")
        return r.ok


def sharpness(img_bytes, face):
    img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    sx = w / face["imageWidth"]
    sy = h / face["imageHeight"]
    x1, x2 = face["boundingBoxX1"] * sx, face["boundingBoxX2"] * sx
    y1, y2 = face["boundingBoxY1"] * sy, face["boundingBoxY2"] * sy
    side = min(x2 - x1, y2 - y1)
    if side < MIN_FACE_PX:
        return None
    mx, my = (x2 - x1) * CROP_MARGIN, (y2 - y1) * CROP_MARGIN
    crop = img[max(0, int(y1 - my)):int(y2 + my), max(0, int(x1 - mx)):int(x2 + mx)]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (EVAL_SIZE, EVAL_SIZE), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env")
    ap.add_argument("--limit", type=int, default=300, help="Fotos pro Person, die geprueft werden")
    ap.add_argument("--apply", action="store_true", help="ohne diese Option nur Trockenlauf")
    ap.add_argument("--unbenannte", action="store_true", help="auch Personen ohne Namen bearbeiten")
    ap.add_argument("--state", default=str(Path.home() / ".immich_featured_state.json"))
    args = ap.parse_args()

    load_env(Path(args.env))
    url, key = os.environ.get("IMMICH_URL"), os.environ.get("IMMICH_API_KEY")
    if not url or not key:
        sys.exit("IMMICH_URL und IMMICH_API_KEY muessen in der .env oder Umgebung stehen.")

    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    api = Immich(url, key)

    for person in api.people():
        name = person.get("name")
        if not name and not args.unbenannte:
            continue
        pid = person["id"]
        name = name or f"(unbenannt {pid[:8]})"
        print(f"\n{name}: pruefe bis zu {args.limit} Fotos ...")
        best = (0.0, None)
        for asset_id in api.assets_of_person(pid, args.limit):
            try:
                face = next((f for f in api.faces(asset_id) if (f.get("person") or {}).get("id") == pid), None)
                if not face:
                    continue
                score = sharpness(api.preview(asset_id), face)
            except requests.RequestException as e:
                print(f"   uebersprungen {asset_id}: {e}")
                continue
            if score and score > best[0]:
                best = (score, asset_id)
        if not best[1]:
            print("   kein geeignetes Foto gefunden")
            continue

        old = state.get(pid, {})
        print(f"   bestes Foto {best[1]} (Schaerfe {best[0]:.0f}, vorher {old.get('score', 0):.0f})")
        if best[0] <= old.get("score", 0) * IMPROVE_FACTOR:
            print("   keine deutliche Verbesserung, bleibt wie es ist")
            continue
        if args.apply:
            if api.set_featured(pid, best[1]):
                state[pid] = {"asset": best[1], "score": best[0]}
                print("   Vorschaubild gesetzt")
        else:
            print("   (Trockenlauf, nichts geaendert)")

    if args.apply:
        state_path.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
