"""Fetch IndianaMap orthoimagery tiles and store them as script blocks served by jsDelivr.

Each block file t/<z>/<bx>/<by>.js holds up to 64 tiles of zoom z (its parent tile at z-3)
and calls AquilaTiles.add(blockKey, {"z/x/y": base64webp}). index.js lists every block.
Claude pages can only load scripts from jsDelivr, so imagery has to travel as script files.

usage: python build.py area <lat> <lon> <cache.json>   fetch detail around one job site
       python build.py overview <cache.json>          fetch the statewide overview, zooms 7-12
       python build.py state <cache.json>             fetch statewide counties, towns and electric territories (merge writes gis/indiana.js)
       python build.py merge <cache.json>             write fetched tiles and GIS into blocks and rebuild index.js
GIS around a site lands in g/14/<x>/<y>.js (parcels, address points, roads) and calls AquilaGIS.add.
Fetching and merging are separate so the workflow can retry the merge on top of newer commits.
"""
import base64, concurrent.futures as cf, io, json, math, os, re, sys, urllib.parse, urllib.request
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
    gkeys = sorted(os.path.relpath(os.path.join(d, f), "g")[:-3]
                   for d, _, fs in os.walk("g") for f in fs if f.endswith(".js"))
    with open("index.js", "w") as f:
        f.write(f"AquilaTiles.index({json.dumps(keys, separators=(',', ':'))});\n")
        f.write(f"AquilaTiles.gisIndex&&AquilaTiles.gisIndex({json.dumps(gkeys, separators=(',', ':'))});\n")
    return len(keys)

def repo_mb():
    return sum(os.path.getsize(os.path.join(d, f)) for top in ("t", "g", "gis") for d, _, fs in os.walk(top) for f in fs) / 1e6

GIS = "https://gisdata.in.gov/server/rest/services/Hosted/"
ELECTRIC = "https://services3.arcgis.com/OYP7N6mAJJCyH6hd/arcgis/rest/services/Electric_Retail_Service_Territories_HIFLD/FeatureServer/0"
# layer -> (service, fields, radius in miles around a site)
AREA_GIS = {
    "parcels": (GIS + "Parcel_Boundaries_of_Indiana_Current/FeatureServer/0", "state_parcel_id,prop_add", 1),
    "addresses": (GIS + "Address_Points_of_Indiana_Current/FeatureServer/0", "geofulladdress", 1),
    "roads": (GIS + "Road_Centerlines_of_Indiana_Current/FeatureServer/0", "geofulladdress", 3),
}

def query(url, envelope, fields, extra=None):
    feats, offset = [], 0
    while True:
        params = {"geometry": ",".join(map(str, envelope)), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                  "spatialRel": "esriSpatialRelIntersects", "outFields": fields, "outSR": 4326, "f": "geojson",
                  "geometryPrecision": 6, "resultOffset": offset, **(extra or {})}
        for attempt in range(3):
            try:
                d = json.load(urllib.request.urlopen(url + "/query?" + urllib.parse.urlencode(params), timeout=120)); break
            except Exception:
                if attempt == 2: raise
        page = d.get("features", [])
        for f in page:
            f["properties"] = {k: v for k, v in (f.get("properties") or {}).items() if v not in (None, "")}
        feats += page
        if not page or not (d.get("exceededTransferLimit") or d.get("properties", {}).get("exceededTransferLimit")):
            return feats
        offset += len(page)

def tile_envelope(z, x, y):
    n = 2 ** z
    lon = lambda tx: tx / n * 360 - 180
    lat = lambda ty: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    return (lon(x), lat(y + 1), lon(x + 1), lat(y))

def fetch_area_gis(lat, lon):
    out = {}
    for layer, (url, fields, miles) in AREA_GIS.items():
        for z, x, y in tiles_around(lat, lon, 14, miles):
            out.setdefault(f"14/{x}/{y}", {})[layer] = query(url, tile_envelope(z, x, y), fields)
    return out

def fetch_state():
    s, n, w, e = INDIANA
    env = (w, s, e, n)
    simple = {"maxAllowableOffset": 0.0008, "geometryPrecision": 5}
    data = {
        "counties": query(GIS + "County_Boundaries_of_Indiana_Current/FeatureServer/0", env, "name_lcase,fips", simple),
        "towns": query(GIS + "Municipal_Boundaries_of_Indiana/FeatureServer/0", env, "namelsad,munic_typ", simple),
        "electric": query(ELECTRIC, env, "NAME,TYPE,STATE,TELEPHONE,WEBSITE", {"maxAllowableOffset": 0.003, "geometryPrecision": 4}),
    }
    print({k: len(v) for k, v in data.items()})
    return data

def write_state(data):
    os.makedirs("gis", exist_ok=True)
    with open("gis/indiana.js", "w") as f:
        f.write(f"AquilaGIS.state({json.dumps(data, separators=(',', ':'))});\n")

def write_gis(gis):
    for key, layers in gis.items():
        path = f"g/{key}.js"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(f'AquilaGIS.add("{key}",{json.dumps(layers, separators=(",", ":"))});\n')
    return len(gis)

def wanted_tiles(argv):
    if argv[0] == "area":
        lat, lon = float(argv[1]), float(argv[2])
        s, n, w, e = INDIANA
        if not (s <= lat <= n and w <= lon <= e):
            sys.exit(f"{lat},{lon} is outside Indiana")
        return [t for z, mi in AREA_TIERS.items() for t in tiles_around(lat, lon, z, mi)], argv[3]
    if argv[0] == "overview":
        return [t for z in OVERVIEW_ZOOMS for t in tiles_in_box(*INDIANA, z)], argv[1]
    sys.exit(__doc__)

def main():
    argv = sys.argv[1:] or ["help"]
    if argv[0] == "state":
        json.dump({"state": fetch_state()}, open(argv[1], "w")); return
    if argv[0] == "merge":
        cache = json.load(open(argv[1]))
        found = {tuple(map(int, k.split("/"))): v for k, v in cache.get("tiles", cache).items() if "/" in k}
        nb = write_blocks(found); write_gis(cache.get("gis", {}))
        if "state" in cache: write_state(cache["state"])
        total = write_index(); mb = repo_mb()
        print(f"merged {len(found)} tiles into {nb} blocks; {total} blocks in repo; tiles total {mb:.1f} MB")
        if mb > REPO_LIMIT_MB:
            sys.exit(f"Repo is {mb:.0f} MB, over the {REPO_LIMIT_MB} MB jsDelivr budget. Start a new tiles repo.")
        return
    wanted, cache = wanted_tiles(argv)
    have = set()
    for z, bx, by in {(z, x >> 3, y >> 3) for z, x, y in wanted}:
        have |= {tuple(map(int, k.split("/"))) for k in read_block(block_path(z, bx, by))}
    todo = [t for t in set(wanted) if t not in have]
    print(f"{len(wanted)} tiles wanted, {len(todo)} to fetch")
    with cf.ThreadPoolExecutor(16) as ex:
        found = {"/".join(map(str, t)): b for t, b in ex.map(fetch, todo) if b}
    gis = fetch_area_gis(float(argv[1]), float(argv[2])) if argv[0] == "area" else {}
    json.dump({"tiles": found, "gis": gis}, open(cache, "w"))
    print(f"fetched {len(found)} tiles and GIS for {len(gis)} blocks")

if __name__ == "__main__":
    main()
