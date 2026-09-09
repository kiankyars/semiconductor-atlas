# Recorded evidence changes

The installed `evidence-changes` command and the local viewer's **New evidence** view
expose stored claim admissions and supersessions. They do not count physical manufacturing
events, certify corrections, infer replacement claims, or deliver alerts.

## Use

```sh
semiconductor-atlas evidence-changes \
  --database artifacts/2026-09-09-exact-clocks-schema10-v2.sqlite \
  --since 2026-09-08T19:00:00Z \
  --recorded-at 2026-09-09T05:52:00.817388Z \
  --as-of 2026-09-09
```

Use `python3 -m semiconductor_atlas.cli` in a source checkout. JSON is emitted from
an existing schema-5-or-later database opened read-only, including committed WAL.
No migration, source acquisition or scheduled-runtime change occurs.

`since` is exclusive and `recorded_at` inclusive, compared as integer UTC microseconds.
Equivalent timezone representations select identical results; stored timestamp spelling
is preserved. Physical dates, publication and retrieval do not select events. Late-arriving
historical reports, expired claims, future-effective claims and undated source statements
remain eligible. `as_of` supplies physical-date context only for dossier drill-through.

The default page size is 50, bounded at 200. `--kind` filters an exact entity kind;
`--entity-id` selects one exact subject without related entities, aliases or allocation.
Each event contains its stable ID, kind, recorded time, exact entity, typed claim, direct
evidence and dossier selection. Transitive dependencies and ingestion runs accompany the
page. Quantities, rates, units, qualifiers and unknowns retain the dossier representation.
Excerpts, record payloads, claim notes, run parameters and verbatim rate wording are omitted;
publication requires separate rights review.

## Accounting and pagination

Admission and later supersession are two events about one claim. A new version's admission
and an old version's supersession can share an instant; admission sorts first, then
supersession, then claim ID. Event IDs bind the format, kind, immutable claim ID and exact
normalized instant. Repeated values and similar subject names are not deduplicated.
Supersession is bookkeeping, not evidence of closure, cancellation, capacity loss or retraction.

The complete selected metadata inventory supplies `total_events`, `event_counts`,
`distinct_claims`, `distinct_subjects` and `population_sha256`. Only the returned page and
its dependencies are hydrated and validated; unseen pages are not represented as checked.
Malformed stored claim clocks or missing selected subjects reject inventory construction.

For another page, pass `--offset` from `next_offset` and the first page's
`--population-sha256`. Nonzero offsets without that hash fail. The hash binds format/schema,
normalized window, dossier date, filters and event metadata, not page size or offset.
Selected insertions, removals or metadata changes reject the old hash. Restart at offset
zero instead of interpreting rejection as an empty page.

This is **not a commit-ordered synchronization protocol**: later inserts backdated before
`since` are outside this inventory. Disjoint timestamp windows cannot guarantee discovering
arbitrary backdated writes. Recheck overlapping windows by event ID or retain frozen inputs;
do not claim exactly-once delivery.

The hash is **not a whole-evidence snapshot commitment**. Appended support can change hydrated
evidence without changing event metadata. Each call has one coherent read snapshot, but
cross-call evidence identity requires a frozen database or caller-held read transaction.
The local viewer pins a transaction for its entire session; restart to see later commits.

## Lineage and availability

Page validation checks typed claim hashes, support, complete acyclic dependencies,
subject/series/target creation, document/source/record clocks, succeeded referenced runs,
completion and explicit acceptance, and auxiliary run inputs. Every declared evidence link
must survive hydration; missing documents, sources or families cannot silently disappear
behind another valid support link.

An explicit run `accepted_at` must be a timezone-aware timestamp within the
inclusive processing interval and at or before the requested cutoff. Explicit
null, malformed timestamps and acceptance outside the run interval reject the
page. Legacy runs without this field remain supported; absence is not a supplied
acceptance timestamp. The dossier uses the same validation.

Validation uses the **upper cutoff**, not each original event instant. Legacy ingestion can
record claims before its run completes. A cutoff before required completion rejects the page
instead of silently dropping the claim. Retry the original lower bound with a later upper
cutoff; never advance after an error. Stored admission times are not redated or promoted to
proof that complete provenance was available then. Future supersessions are masked.

No original source bytes are reverified. Physical expiry, entity edits, appended evidence
and raw deletion are not synthesized into events. Empty means no matching retained events,
not no real-world change or zero capacity.

## Retained acceptance case

On September 9, the example interval matched independent enumeration: **18 admissions,
seven exact subjects, zero supersessions**. TSMC company quantities contribute four; ASE
K18B two; Villach output five; Tower Fab1 two; Tower Arai one; Polar one; and the City-reported
Ohio One program three. These are source claims, not 18 verified manufacturing events.

All 104,472 retained claims have distinct series IDs and none is superseded. Correction,
recurrence and mutation tests are synthetic regressions, not measured correction performance.
Database SHA-256:
`a1d59ad714c7df0280335faf9c4c7fd73a4a31d9f838185f60f5d1b5052306cd`.
Example population hash:
`e797723bf4957cb6e97d14bcdc53d250d99c68870314f4dacfe85bd08bb6aa04`.
Three seven-event pages match the whole inventory. One microsecond before Ohio admission
returns 15 events without that subject. Moving dossier world date to 1900 preserves event IDs.

Regressions in `tests/test_evidence_changes.py`, `tests/test_evidence_viewer.py` and
`web/tests/evidence_viewer_state.test.cjs` cover exact cutoffs, stable IDs, mutations between
pages, provenance-only changes, WAL concurrency, caller transactions, lineage rejection,
typed values, and distinct loading, empty, error and stale-response states.

### Installed-package verification

The final full run executed **2,067 core tests: 2,066 passed and one expected optional-data
skip** in 266.818 seconds. All 12 web tests and 20 viewer-state JavaScript tests passed.
This change adds 28 feed tests, six HTTP tests, eleven chronological-metadata tests and
seven viewer-state tests. No live browser interaction or screenshot QA was performed.

The isolated acceptance worker ran under Python `-I -S -B`; all 45 imported Atlas modules
came from the unpacked wheel, and all 104 packaged files matched the source checkout.
Independent enumeration, event IDs, pagination, exact cutoffs, CLI equality and four real
loopback routes passed. Database SHA remained exact. Retained local receipts:

- `artifacts/2026-09-09-evidence-changes-installed-check-v1.json`, SHA-256
  `3316ce427943c078b5dad872285be821e6528a2b2162ce51ae9518b96d9ee07f`.
- Checker `artifacts/2026-09-09-check-evidence-changes-installed.py`, SHA-256
  `ec17b40b52931f34b94ca724b95cbc2a03dd5cb87f24ef778d0d7c4255861ece`.
- Wheel in `artifacts/2026-09-09-evidence-changes-dist-v1/`, SHA-256
  `0ccc91a0396b4c5a2c391c410849adca64f76e35e0801d968f2fbda99d070230`.
- Frozen Ohio producer replay `artifacts/2026-09-09-post-evidence-changes-city-frozen-replay-v1.json`,
  SHA-256 `b299e36be6a29814ae5972b0e9f6450d2ea3c6f333dc730241e282c4addd1dcc`.

The old Ohio schema-9 image still reconstructs byte-for-byte. The parent checkout, scheduled
database, baseline, original migrations, checksum ledger and r3 archive remain unchanged.
Nothing was pushed, published, deployed or rescheduled. This is progress toward inspectable
updates and historical replay; measured alerts and calibrated forecasts are still open.
