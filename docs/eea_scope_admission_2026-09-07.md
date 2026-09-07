# EEA v16 reviewed source admission: September 7, 2026

## Result and scope

The [corrected complete review](../review_plans/2026-09-07-eea-industrial-v16-scope-admission-v2.json)
decides all 108 candidates: **15 `accept_in_scope`, 93 `defer`, zero `reject_out_of_scope`**.
An isolated schema-5 working database now contains the accepted records and 132 exact EEA scalar
source statements. The frozen parent database is unchanged. This is bounded historical
facility-scope admission, not 15 new unique fabs, AI-critical cohort expansion, canonical identity
resolution, or evidence of current operation or capacity.

The admitted source records cover Infineon Regensburg, Dresden and Villach; ams Regensburg; Bosch
Dresden; X-FAB Corbeil; SEH Livingston; IQE Wafer Technology Milton Keynes; Greenock Larkfield;
Newport; St Mellons; Analog Devices Raheen; AT&S Leoben; and two separately identified CST records.
The CST source identifiers remain distinct despite overlapping site evidence. Names and address
strings are preserved as EEA reported them; historical aliases are not canonical identity decisions.

The [admission audit](../review_plans/2026-09-07-eea-industrial-v16-admission-audit-v1.json)
and its [evidence-binding correction](../review_plans/2026-09-07-eea-industrial-v16-admission-correction-v2.json)
reconcile the earlier research proposals without rewriting them:

- Of 38 proposed inclusions, 15 have sufficient bounded scope support. The other 23 remain deferred:
  15 decisive PDF-layout dependencies, six site/process bridges and two photovoltaic-scope choices.
- All 23 proposed exclusions remain deferred. Ordinary PCB, passive-component and connector
  boundaries are still provisional; five cases also have concrete establishment/process ambiguity.
- The original 47 research deferrals remain deferred. Missing evidence is not negative evidence.

This was exposed Codex-assisted collaborative review, including self-originated proposals, not
human sign-off or independent blind adjudication. No PDF page image was inspected. Two accepted
PDF accounts use self-contained prose rather than certificate rows or adjacent-site grouping;
decisive visual-layout dependencies remain deferred. External review sources are represented by
bounded excerpts, locators, URLs and original access clocks, not retained exact-byte publisher
bodies. The exact EEA package remains the archived source for every imported scalar. Repeat access
failures did not erase earlier observations or become fresh verification.

### Preserved correction history

The first review was admitted to an isolated rehearsal at `2026-09-07T17:43:24.540118Z`.
Final evidence audit then found that Newport's decision had omitted the issuer process passage
required by its readiness rationale, leaving only the regulator name/address entry. The corrected
review restores that evidence after a fresh body read at `2026-09-07T17:54:49Z`, retains the
regulator address bridge and changes no outcome. The other 107 decisions remain identical.

The first review, its audit and
`artifacts/2026-09-07-eea-reviewed-source-statements-v2.sqlite` remain byte-unchanged. That database's
SHA-256 is `010725c9fd932a7340e07bebfe001d0d2b12350d07484313a04447e00ee7d73a`; it is historical
rehearsal evidence, not the current candidate. The corrected review was rejected without writes when
tested against it. The corrected candidate below was created separately from the frozen parent;
this does not implement or imply multi-review refresh of an existing admitted database.

## Temporal and confidence correction

The v2 importer no longer assigns the dataset publication date as claim-effective time or assigns
confidence `1.0` to an uncalibrated source statement. All 132 new statements have null `valid_from`,
`valid_to` and `confidence`. Publication remains source-document metadata. The knowledge-time
source view can expose these statements from admission, while the physical-world view excludes
them until effective-time support exists.

New admission requires an existing schema-5 database and `--accept-now`; this command does not
initialize or migrate a database. `--accepted-at` is now exact-replay-only and must equal the
original run's acceptance clock. Existing v1 records retain their original representation on replay;
they are not silently corrected. Both v1 and v2 replay reject changed reviews and missing or
conflicting immutable rows without repair.

Replay also checks the complete document/role lineage set, not just the presence of expected
links. Regression tests reject an appended extra role in v1, v2 and an all-deferred v2 import,
while denying database writes. The original v1 producer independently reproduced the synthetic
legacy fixture's 56 semantic INSERT rows exactly; compatibility is not tested solely against new code.

The corrected admission clock was `2026-09-07T18:00:52.627180Z`, after transaction-bound validation
started at `2026-09-07T18:00:26.917054Z` and after the corrected review cutoff of
`2026-09-07T17:55:40Z`. The frozen schema prohibits ingestion-run updates, so the immutable
`completed_at` is explicitly identified as `actual_prewrite_validation_completion`, equal to
admission. It is not the later post-write verification or commit-completion time. Input and database
validation run again before commit, and backward-clock or validation failures roll back atomically.

## Retained local result

The derivative was created with SQLite `VACUUM INTO` from the read-only parent, only after verifying
that the target did not exist. Its smaller byte size reflects compaction, not removed parent rows.
Both databases are local ignored artifacts, not newly published release assets.

| Item | Value |
| --- | --- |
| Parent | `artifacts/2026-09-07-source-project-targets.sqlite` |
| Parent SHA-256 | `18c0d0ad1d6ddbbd9d97b376f2a98d35fea16676cb79df46db317df045e65b82` |
| Admitted derivative | `artifacts/2026-09-07-eea-reviewed-source-statements-v2-corrected.sqlite` |
| Derivative bytes | 244,006,912 |
| Derivative SHA-256 | `aca76a218775c5b3a4b2b9c0da838acbf572c509401d013fcdc1930e581d66db` |
| Run ID | `5a564772-8ae2-5930-ba6f-2bf1a726d283` |
| Corrected-review SHA-256 | `c0f724bac16d64f9dfb30a04a6469e5263874b155bd6dc89146bad3daa5ae34b` |
| Correction-audit SHA-256 | `28eec517549e830df07ee976f36e8c5294428f440fca8f44570c4e0c8da1d3e4` |
| Importer file SHA-256 | `bf53d66b9b23be5df2d190dabc2d8bba52a5371e6d2d60ce17ec80f3f3a8ce07` |
| CLI file SHA-256 | `a68a0fcd5e2042bd09598dd1cb62683ed8513a3dcc36c7c34c5cdfe87a36c477` |

The snapshot, queue and source hashes remain those in the
[candidate-source audit](eea_industrial_v16_acceptance_audit_2026-07-20.md). The original research
v1/v2 artifacts, legacy 353-entry checksum ledger, published r3 producer, both frozen detector code
inventories and the prospective-study registration remain unchanged.

## Verification

- SQLite quick check and repository database validation passed. Every parent row in every table
  remained present with identical values, verified by table-by-table set difference.
- The only additions were 15 entities, 15 source records, 132 claim series/versions/typed scalar
  values/evidence links, two documents, one source, one source family, one ingestion run and four
  run-document links. Final totals are 4,881 entities, 4,841 source records, 104,420 claims and 10 runs.
- All 132 statements are absent one microsecond before admission and present at admission in the
  source-knowledge view. None enters the physical-world view at the tested dates in 2000, the 2026
  publication date, September 7, 2026 or 2030. No geometry, capability, milestone, relationship,
  resource, constraint or capacity value was added.
- Exact replay against the admitted database, with SQLite INSERT/UPDATE/DELETE denied, attempted
  zero such operations, created zero rows and left the entire database SHA-256 unchanged.
- The final core suite passed 1,237 tests; the web suite passed 12. Regression coverage includes
  original v1 replay, v2 replay, changed/mixed reviews, admission clocks, unknown effective time and
  confidence, exact source bindings, the full decision ledger and preserved historical research.

To verify this retained admission, use its original clock; this is not a fresh deterministic rebuild:

```sh
python3 -B -m semiconductor_atlas ingest-eea-industrial-snapshot \
  --database artifacts/2026-09-07-eea-reviewed-source-statements-v2-corrected.sqlite \
  --snapshot source_snapshots/2026-07-20-eea-industrial-v16-semiconductor-candidates \
  --candidate-queue review_plans/2026-07-20-eea-industrial-v16-candidates.json \
  --review review_plans/2026-09-07-eea-industrial-v16-scope-admission-v2.json \
  --accepted-at 2026-09-07T18:00:52.627180Z
```

## Remaining north-star gates

One immutable scope review per source/database remains supported. Later decisions need an explicit
append-only scope-revision ledger and cutoff-aware consumers before another differing review may
be admitted. Scope withdrawal must neither delete source history nor assert physical closure.
The 93 deferred records still need their documented evidence or scope decisions; tabular-spatial
correspondence and canonical identity remain separate gates. This admission does not establish
current production, qualified capacity, detection accuracy, forecast calibration or a compatible
Supply Intelligence handoff. The prospective detector study remains frozen and unmodified.

### Subsequent revision workflow

The [append-only scope-revision workflow](eea_scope_revisions.md) now supplies the same-snapshot
revision and scoped-consumer semantics described above. It applies the Newport evidence correction
on a derivative of the first rehearsal while preserving both original databases. The producer hashes
in this admission audit identify the code at commit `434bed9`; later CLI additions do not rewrite
this historical admission record. Different source editions and the remaining manufacturing,
identity, geography, evaluation and downstream-integration gates remain open.
