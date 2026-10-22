# Semiconductor website

`build_site.py` builds the public Semiconductor Atlas with a searchable facility list,
country and activity filters, individual records, source documents and data downloads.
It uses the retained public bundle in `web/data/2026-09-07/`: seven records and ten
source documents. The snapshot date is September 7, 2026; publishing the website does
not refresh the underlying evidence. Each record displays its own dated stage.

```sh
python3 web/build_site.py --output /tmp/semiconductor-atlas-site
python3 -m unittest discover -s web/tests -v
```

For a local browser preview:

```sh
python3 web/build_site.py --output /tmp/semiconductor-atlas-preview --base-url http://127.0.0.1:8765/
python3 -m http.server 8765 --bind 127.0.0.1 --directory /tmp/semiconductor-atlas-preview
```

The output directory must not already exist. `--base-url` sets the deployment URL;
loopback HTTP URLs are also supported for local previews. The build verifies the pinned
manifest and the complete release before copying its files unchanged to
`downloads/2026-09-07/`. Source attribution and terms accompany those files. Raw
publisher documents remain in local evidence storage.

The seven records have no verified point geometry, so the website starts with a list.

`/registry/` publishes the tracked facility-registry bundle in `web/data/registry-2026-07-20/`.
`semiconductor_atlas/public_registry.py` derives it from the retained 2026-07-20 open-seed
release and the accepted EEA scope database, both local-only, plus the tracked EEA candidate
queue for reporting years. Every input is verified by SHA-256:

```sh
python3 scripts/build_public_registry.py --output /tmp/registry-bundle
```

The site build checks the bundle's pinned manifest and every file hash, then copies the files
unchanged to `downloads/registry-2026-07-20/`. The page draws a Taiwan map from the
MOENV-reported points only, over a simplified Natural Earth 1:50m outline
(`taiwan_outline.json`, public domain). Nothing is geocoded. Map dots are a pointer shortcut
to table rows, follow the table filters and stay out of the tab order. NIST funding, investment, jobs
and timeline statements are quoted verbatim under the scope of the page section they come from
(this site, the award page, or a program shared across award pages). Taiwan environmental-control
listings show each category's flag and release date as published. U.S. EPA FRS rows stay as download-only leads without map points.
The home page carries schema.org `Dataset` markup for dataset search engines. It declares no
license because reuse terms are set per source in `ATTRIBUTION.md`. Each facility page links to a
correction form prefilled with its record key.
Old datacenter project pages redirect to the matching project in
[Data Center Atlas](https://kiankyars.github.io/datacenter-atlas/). The project listing,
coverage, data, methodology and about pages redirect to their corresponding destinations.
Each has an ordinary fallback link for browsers without JavaScript. Unknown addresses show links to both
atlases; the semiconductor home page never redirects.

## Local evidence viewer

For interactive inspection of retained source reports, use the installed
[local evidence viewer](../docs/evidence_viewer.md). It connects literal search
and exact dossiers through a loopback-only read service, with world/knowledge
controls and full history. It does not alter the standalone release interface
described below or add source-only records to its map. Its JavaScript state
tests run with `node --test web/tests/evidence_viewer_state.test.cjs`; they use
a minimal DOM double, not a rendered browser.

## Standalone release interface

`generate_atlas.py` and `atlas-template.html` remain the original standalone release
interface. The generator verifies GeoJSON against its manifest, embeds it in `atlas.html`
and updates that release atomically. It is separate from the current website builder.
Only explicit WGS84 geometry claims appear on its map; raw EPA NAD83 scalars remain
unmapped. Historical release templates, data and manifests are preserved unchanged.
