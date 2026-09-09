# Local evidence viewer

The installed viewer connects [literal registry discovery](evidence_search.md)
to [exact-entity dossiers](evidence_dossier.md). It is a local inspection surface,
not a public release, a source-acquisition service, a global facility census or
a physical-state certification. The existing release map and its pinned
AI-critical templates are unchanged.

## Run

From an installed wheel:

```sh
semiconductor-atlas evidence-viewer \
  --database artifacts/2026-09-09-new-albany-project-statements-schema9.sqlite \
  --as-of 2026-09-09 \
  --recorded-at 2026-09-09T05:52:00.817388Z
```

From the source checkout, substitute `python3 -m semiconductor_atlas.cli` for
`semiconductor-atlas`. Open the loopback URL printed at startup. Keep that
session URL private; its random path is a capability. Stop the server with
Ctrl-C. `--port` optionally selects a port; the default asks the OS for an
unused port. There is deliberately no network-interface or public-host option.

Use a retained local database image, **not the scheduled production database**.
The viewer opens an existing schema-5-or-later database read-only, respects any
committed WAL, and pins one read transaction before announcing readiness. It
does not migrate, checkpoint, acquire sources or accept claims. Later commits
are invisible until restart. A held read transaction can delay checkpoint
progress or block a rollback-journal writer; read-only is not a guarantee of
zero coordination cost. SQLite may use its normal WAL/SHM sidecars.

## Inspect

1. Search an entity name, immutable key, exact ID or directly associated
   publisher/source/family metadata. Omit the query to browse. Matching is the
   server's NFC/casefold literal substring operation, not aliases, fuzzy matching
   or document full text.
2. Optionally choose an entity kind and change either cutoff. The knowledge
   timestamp requires a timezone and preserves microseconds as text. Changing
   inputs clears old results and detail; submit to apply them.
3. Select an exact record. Similar names, including the City and NIST Ohio
   records, remain separate. Pagination keeps the full matching and visible
   registry denominators; it does not count operating factories.
4. Inspect source statements and world-current claims separately. Expand claim
   cards for full typed values, evidence roles, locators, publisher families,
   hashes, valid/recorded clocks and dependencies. History and dependency-only
   context are separate sections. The full dossier JSON is also accessible.

The selected search result supplies the exact entity ID and normalized cutoffs
to the dossier request. Out-of-order responses cannot overwrite a newer query,
cutoff or entity selection. Empty results, unknown values and rejected dossiers
are distinct states. Dossier rejection never yields a partial or empty-looking
report; the CLI can provide more specific provenance diagnostics.

The existing dossier allowlist is reused without a second claim-hydration path.
Source quantities retain fiscal periods and units; monthly rates keep their
denominators and qualifiers; milestones keep bounds, precision and null
midpoints. Null confidence is not rendered as zero. Source reports do not
become installed capacity, measured outcomes or corroborated facts. Source
families and document counts are not confidence scores.

Geometries remain typed values in the dossier. This viewer does not synthesize
points or centroids, plot source-only locations, merge source-native identities
or add these records to the published map. Geographic-map integration remains
open, alongside reviewed semantic discovery and the alert/forecast gates.

## Local HTTP boundary

- Only `127.0.0.1` is bound. Every GET requires the exact Host and random session
  path. Supplied Origin and Fetch Metadata headers must be same-origin (or a
  direct-navigation `none` fetch site). There is no CORS permission.
- Only three packaged assets and three read endpoints exist: `api/config`,
  `api/search`, `api/dossier`. API parameters are allowlisted; duplicate and
  unsupported fields are rejected. Both cutoffs are required for data requests.
  No arbitrary file, SQL, source URL, export path or mutation endpoint exists.
- Non-GET methods are unsupported. Request targets are bounded to 8,192
  characters and accepted socket reads have a five-second timeout. Queries
  retain the search API's 200-character and 200-row bounds. Requests are
  serialized, so this is an individual local viewer, not a multi-user service.
- Responses use no-store, no-referrer, nosniff, same-origin resource policy,
  frame denial and a restrictive self-only script/style/connect policy. Assets
  contain no external libraries, fonts, images or map tiles.
- Access logging is disabled to avoid recording capability paths or private
  search names. Dynamic metadata uses `textContent`; only credential-free
  HTTP(S) source URLs become links, with `noreferrer`/`noopener`.

These controls are not authentication against other processes or users who
already possess the local capability URL. Do not expose the listener through a
proxy, tunnel or shared server. Raw publisher bodies, excerpts, record payloads,
claim notes, run parameters and verbatim rate wording are excluded by the
dossier contract. Local access does not grant publication or redistribution
rights; nothing is uploaded by this workflow.

## Validation

```sh
python3 -m unittest tests.test_evidence_viewer
node --test web/tests/evidence_viewer_state.test.cjs
python3 -m unittest discover -s tests
python3 -m unittest discover -s web/tests
uv build --offline --quiet --out-dir artifacts/NEW-BUILD-DIRECTORY
```

The 25 HTTP tests exercise actual loopback requests, exact search/dossier API
bytes, cutoff boundaries, invalid lineage, pagination, origin/Host/token and
parameter rejection, unchanged database bytes, and committed-WAL snapshot
isolation across restart. The 13 JavaScript tests execute the actual packaged
client script with a minimal DOM double to exercise selection, summaries,
safe links, error states, pagination and stale-response guards. They are not
browser interaction, accessibility or screenshot validation.

The fixed-source full run executed 1,963 core tests: 1,962 passed and one
optional-payload check was skipped. All 12 existing web tests passed. No hosted
deployment or rendered-browser QA was performed.

Installed acceptance used the actual console entry point in a fresh `-I -S -B`
process extracted from the wheel, with all 43 imported Atlas modules verified
under that installation. It traversed all 4,904 entities over 25 HTTP pages and
matched their exact order to an independent SQLite query. Five real searches
matched the previously retained search JSON; Ohio One, Fab 21, Villach and Polar
dossiers matched all original JSON bytes and repeated identically. One
microsecond before the municipal admission, Ohio search retained only the NIST
record. Host/Origin rejection and packaged asset bytes were also checked. No
capability URL is written to the receipt.

- Checker: `artifacts/2026-09-09-check-installed-evidence-viewer.py`, SHA-256
  `f9479568bb0c4480b07adfec8b8924b238ee9a71a7d2ffdd7b294f27913d2062`.
- Receipt: `artifacts/2026-09-09-installed-evidence-viewer-check-v1.json`, SHA-256
  `7b4c140996ec9bf86e507a9080288d28497b108e4c27e2bcd84357bf4cbac9cd`.
- Tested wheel: `artifacts/2026-09-09-evidence-viewer-build-v1/semiconductor_atlas-0.1.0-py3-none-any.whl`,
  SHA-256 `e983634e363d8ca88f0c7ca8182a0f6e88d099a497a26eb1873422d517b545b1`.
- Documentation-followup build v2 wheel: SHA-256
  `1f5bdab71a3e89b84f76a9062031038ccb7584246d44290bfcdcae670980c4e2`.
  Both its wheel and sdist were checked for exact current viewer/CLI Python and
  all three asset bytes. No product code changed after the full test run.
- The 242,892,800-byte database remains SHA-256
  `0901ee33eeada18c70e949411be3c9582117262ec4e8cfd87638681f25787b5d`.
  Frozen Ohio admission replay still reproduced its full bytes; receipt
  `artifacts/2026-09-09-post-viewer-city-frozen-replay-v1.json`, SHA-256
  `ecbae525e331ec04e8b4e194bf1525c55f84e6c21b2843620b73d60827e50050`.

These receipts are local acceptance records, not proof of global coverage,
browser usability, physical truth or independent corroboration. The protected
parent database, baseline, checksum ledger, analytics file and original `r3`
archive retained their prior hashes. Neither a release nor a service was
published, and the temporary acceptance/preview servers were stopped.
