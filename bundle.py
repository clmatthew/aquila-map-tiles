"""Regroup tile and GIS blocks into larger script bundles that ship inside the Power Tracker artifact.

claude.ai pages can't load GitHub-hosted data, so the artifact carries its own copy:
  tiles/t/<z>/<x>/<y>.js   tiles of zoom z under one zoom z-4 parent (up to 256 tiles), calls AquilaTiles.add
  tiles/g/<x>/<y>.js       zoom-14 GIS blocks under one zoom-12 parent, calls AquilaGIS.add per block
  tiles/gis/indiana.js     statewide counties, towns and electric territories
  tiles/index.js           lists every bundle
usage: python bundle.py <out dir>
"""
import json, os, re, shutil, sys

out = sys.argv[1]
def body(path):
    m = re.search(r"\((\"[^\"]+\"),(\{.*\})\);\s*$", open(path).read(), re.S)
    return json.loads(m.group(2))
groups, gis = {}, {}
for d, _, fs in os.walk("t"):
    for f in fs:
        for k, v in body(os.path.join(d, f)).items():
            z, x, y = map(int, k.split("/"))
            groups.setdefault(f"{z}/{x >> 4}/{y >> 4}", {})[k] = v
for d, _, fs in os.walk("g"):
    for f in fs:
        key = os.path.relpath(os.path.join(d, f), "g")[:-3]  # 14/x/y
        _, x, y = map(int, key.split("/"))
        gis.setdefault(f"{x >> 2}/{y >> 2}", {})[key] = body(os.path.join(d, f))
shutil.rmtree(out, ignore_errors=True)
for key, tiles in groups.items():
    p = f"{out}/t/{key}.js"; os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(f'AquilaTiles.add("{key}",{json.dumps(tiles, separators=(",", ":"))});\n')
for key, blocks in gis.items():
    p = f"{out}/g/{key}.js"; os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("".join(f'AquilaGIS.add("{k}",{json.dumps(v, separators=(",", ":"))});\n' for k, v in blocks.items()))
os.makedirs(f"{out}/gis", exist_ok=True); shutil.copy("gis/indiana.js", f"{out}/gis/indiana.js")
open(f"{out}/index.js", "w").write(f"AquilaTiles.index({json.dumps(sorted(groups), separators=(',', ':'))});\n"
                                   f"AquilaTiles.gisIndex({json.dumps(sorted(gis), separators=(',', ':'))});\n")
files = [os.path.relpath(os.path.join(d, f), out) for d, _, fs in os.walk(out) for f in fs]
size = sum(os.path.getsize(os.path.join(out, f)) for f in files)
print(f"{len(files)} files, {size / 1e6:.1f} MB, largest {max(os.path.getsize(os.path.join(out, f)) for f in files) / 1e6:.1f} MB")
