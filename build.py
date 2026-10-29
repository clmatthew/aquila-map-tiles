"""Fetch IndianaMap orthoimagery tiles and store them as script blocks served by jsDelivr.

Each block file t/<z>/<bx>/<by>.js holds up to 64 tiles of zoom z (its parent tile at z-3)
and calls AquilaTiles.add(blockKey, {"z/x/y": base64webp}). index.js lists every block.
Claude pages can only load scripts from jsDelivr, so imagery has to travel as script files.

usage: python build.py area <lat> <lon>    detail around one job site
       python build.py overview            statewide overview, zooms 7-12
"""
import base64, concurrent.futures as cf, io, json, math, os, re, sys, urllib.request
from PIL import Image

SVC = "https://di-ingov.img.arcgis.com/arcgis/rest/services/CacheWebMercator/IndianaCurrentImageryCacheL19v4/MapServer/tile"
INDIANA = (37.77, 41.77, -88.10, -84.78)  # south, north, west, east
REPO_LIMIT_MB = 140  # jsDelivr refuses packages over 150 MB
# zoom -> radius in miles around a job site
AREA_TIERS = {19: 0.5, 18: 1, 17: 2, 16: 3, 15: 6, 14: 12, 13: 24}
OVERVIEW_ZOOMS = range(7, 13)
QUALITY = 60

def tile_xy(lat, lon, z):
    n = 2 ** z
    return ((lon + 180) / 360 * n,
            (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)

def tiles_in_box(s, n, w, e, z):
    x0, y0 = tile_xy(n, w, z); x1, y1 = tile_xy(s, e, z)
    return [(z, x, y) for x in range(int(x0), int(x1) + 1) for y in range(int(y0), int(y1) + 1)]

def tiles_around(lat, lon, z, miles):
    dlat = miles / 69.0; dlon = miles / (69.0 * math.cos(math.radians(lat)))
    return tiles_in_box(lat - dlat, lat + dlat, lon - dlon, lon + dlon, z)

def fetch(t):
    z, x, y = t
    for _ in range(3):
        try:
            raw = urllib.request.urlopen(f"{SVC}/{z}/{y}/{x}", timeout=30).read()
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            if im.getextrema() in (((0, 0),) * 3, ((255, 255),) * 3):
                return t, None  # blank tile outside coverage
            out = io.BytesIO(); im.save(out, "WEBP", quality=QUALITY, method=6)
            return t, base64.b64encode(out.getvalue()).decode()
        except Exception:
            pass
    return t, None

def block_path(z, bx, by):
    return f"t/{z}/{bx}/{by}.js"

def read_block(path):
    if not os.path.exists(path):
        return {}
    m = re.search(r"\((\"[^\"]+\"),(\{.*\})\);\s*$", open(path).read(), re.S)
    return json.loads(m.group(2)) if m else {}

def write_blocks(found):
    blocks = {}
    for (z, x, y), b64 in found.items():
        blocks.setdefault((z, x >> 3, y >> 3), {})[f"{z}/{x}/{y}"] = b64
    for (z, bx, by), tiles in blocks.items():
        path = block_path(z, bx, by)
        merged = read_block(path); merged.update(tiles)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(f'AquilaTiles.add("{z}/{bx}/{by}",{json.dumps(merged, separators=(",", ":"))});\n')
    return len(blocks)

def write_index():
    keys = sorted(os.path.relpath(os.path.join(d, f), "t")[:-3]
                  for d, _, fs in os.walk("t") for f in fs if f.endswith(".js"))
    with open("index.js", "w") as f:
        f.write(f"AquilaTiles.index({json.dumps(keys, separators=(',', ':'))});\n")
    return len(keys)

def repo_mb():
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk("t") for f in fs) / 1e6

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "area":
        lat, lon = float(sys.argv[2]), float(sys.argv[3])
        s, n, w, e = INDIANA
        if not (s <= lat <= n and w <= lon <= e):
            sys.exit(f"{lat},{lon} is outside Indiana")
        wanted = [t for z, mi in AREA_TIERS.items() for t in tiles_around(lat, lon, z, mi)]
    elif mode == "overview":
        wanted = [t for z in OVERVIEW_ZOOMS for t in tiles_in_box(*INDIANA, z)]
    else:
        sys.exit(__doc__)
    have = set()
    for z, bx, by in {(z, x >> 3, y >> 3) for z, x, y in wanted}:
        have |= {tuple(map(int, k.split("/"))) for k in read_block(block_path(z, bx, by))}
    todo = [t for t in set(wanted) if t not in have]
    print(f"{len(wanted)} tiles wanted, {len(todo)} to fetch")
    with cf.ThreadPoolExecutor(16) as ex:
        found = {t: b for t, b in ex.map(fetch, todo) if b}
    nb = write_blocks(found)
    total = write_index()
    mb = repo_mb()
    print(f"fetched {len(found)} tiles into {nb} blocks; {total} blocks in repo; tiles total {mb:.1f} MB")
    if mb > REPO_LIMIT_MB:
        sys.exit(f"Repo is {mb:.0f} MB, over the {REPO_LIMIT_MB} MB jsDelivr budget. Start a new tiles repo.")

if __name__ == "__main__":
    main()
