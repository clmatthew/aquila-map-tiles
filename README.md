# Aquila map tiles

Indiana orthoimagery (IndianaMap, State of Indiana, public domain) cut into tile blocks for the Aquila power tracker.
Claude pages may only load scripts from jsDelivr, so each block is a script that hands its tiles to the page.

- `t/<z>/<bx>/<by>.js` holds up to 64 WebP tiles of zoom `z`, base64-encoded.
- `index.js` lists every block.
- Run **Actions → Fetch imagery** with a latitude and longitude to add detail around a job site, or `overview` for the statewide base.

jsDelivr serves at most 150 MB per repo, and `build.py` stops at 140 MB. Start a second repo when it does.
