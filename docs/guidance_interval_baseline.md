# Executed source-guidance interval comparator

This isolated schema-6 workflow runs a deterministic comparator using accepted source statements.
It is a baseline for a later predictive model to beat, not a trained model, a current publisher
search, probability calibration, capacity forecast or completed north-star gate. A new vintage
uses the actual execution clock; retained guidance cannot become a backdated system prediction.

## Population and event contract

The input is the complete [September 8 screen](../review_plans/2026-09-08-prospective-milestone-roster-screen.json):
eight expected claims on seven source-native project entities. The separately reviewed
[source-input map](../review_plans/2026-09-08-guidance-interval-inputs-v1.json) preserves the original
screen bytes and corrects two interpretations:

- Hemlock and SK hynix already have accepted timeline/scope text scalars. The comparator uses
  those directly; no new core admission is required merely to derive a sidecar interval.
- The legacy TSMC aggregate's scalar evidence belongs to the older retained page, not the newer
  Fab 2 page associated with the screen's coarse source label.

The seven screened cases, six retained cases and three issued intervals are separate denominators.
The two Infinera site cases share one related-project group spanning California and Pennsylvania;
they are not independent samples or geographic holdouts. All projects are in the United States,
and the database's existing expected-claim population is not a project census. This design has
no train/test split, fitted parameters or performance claim.

| Case | Exact event | Source-guidance interval | Treatment |
| --- | --- | --- | --- |
| TSMC Arizona Fab 2 | Production start | July–December 2027 | Select later retained statement; preserve earlier 2028 input |
| Hemlock new facility | Production start | January–December 2028 | Use accepted literal timeline scalar |
| SK hynix Indiana | Mass production | July–December 2028 | Keep mass production distinct from production start |
| Amkor Peoria | Mass production | Unsupported precise bounds | Abstain on vague end-year language and phase correspondence |
| Infinera San Jose | Production start | Unsupported precise future bounds | Abstain; summer 2026 is not an attained/nonoccurrence report |
| Infinera Bethlehem | Production start | Unsupported precise future bounds | Same abstention; retain its own local scope and group |
| Legacy TSMC Arizona aggregate | Unresolved allocation of first-fab target | None | Retain screening exclusion; not another Fab 2 prediction |

Every issued interval has a null midpoint. Legacy July 1/October 1/December 1/July 15 midpoints,
confidence values and effective dates remain visible in the frozen historical input screen and
lineage but do not become model parameters, event dates or probability estimates. The comparator
does not assert that the retained guidance is still current, that no event has happened already,
or that the source search is complete. It records each selected document's original retrieval
clock and age at the actual vintage.

## Execution and replay

`semiconductor_atlas.guidance_baseline.freeze` receives the exact database image, screen bytes,
review bytes and local source bodies. It verifies the complete expected-claim denominator,
typed values, exact project identity, cutoff-bound transitive lineage, direct supporting document
links, source hashes and complete visible HTML table/row spans. The scoped text is interpreted by
a deliberately narrow calendar-year/half-year parser. The TSMC milestone retains the explicit
named Fab 2 clause and both reviewed document versions. Human source-scope judgment remains a
review input; byte checks do not independently certify manufacturing truth.

Predictions are executed by code, not accepted from a caller-supplied predictions file. A vintage
retains its complete case set, inputs, selected documents, transitive lineage, code inventory,
policy hash, interpreter identity and actual start/freeze clocks. Only intervals wholly after
the freeze day and within the declared horizon are issued. A later freeze may therefore abstain
on a formerly future interval.

Replay requires an independently retained **whole-file** SHA-256, the exact database image,
the retained source files, and the recorded code/runtime. It regenerates the comparator result
and compares every canonical output byte. A self-resealed changed artifact is insufficient.
The CLI rereads inputs before new-only output installation; the database is deserialized into
a query-only in-memory connection and is never migrated or written. Source paths are local
locators, not instructions to fetch. These are local clock/hash controls, not external timestamp
attestation, source authentication, reviewer-independence proof or a hermetic OS environment.
Parser-visible means the retained HTML parser's visibility rules, not a browser-rendered check
of external CSS or a guarantee that a publisher currently displays the same statement.

Source-checkout entry points:

After package or migration changes, use the separately retained
[September 8 producer runtime](frozen_runtime.md#september-8-guidance-producer-retention) to replay
this vintage. The current checkout's code must not be substituted for the recorded producer.

```sh
python3 scripts/guidance_interval_baseline.py freeze \
  --database artifacts/2026-09-08-tsmc-fab-correspondence-schema6.sqlite \
  --screen review_plans/2026-09-08-prospective-milestone-roster-screen.json \
  --input-review review_plans/2026-09-08-guidance-interval-inputs-v1.json \
  --evidence-cutoff-at ACTUAL_CURRENT_UTC_TIMESTAMP \
  --output NEW_VINTAGE_PATH

python3 scripts/guidance_interval_baseline.py verify \
  --database artifacts/2026-09-08-tsmc-fab-correspondence-schema6.sqlite \
  --vintage RETAINED_VINTAGE_PATH \
  --trusted-vintage-sha256 INDEPENDENTLY_RETAINED_WHOLE_FILE_SHA256
```

The caller's input cutoff must include the reviewed input map and cannot be later than execution.
There is no caller-provided freeze clock. These commands do not repin the parent checkout's
scheduled detector study or modify its schema-5 artifacts, checksum ledger, collectors or rights.

## Later outcomes and scoring

Each vintage prespecifies twenty full calendar quarters after the freeze quarter. Evidence
cutoffs are each quarter's last day at 23:59:59 UTC; reviews are due 31 days later. This is a
retained evaluation schedule, not an installed automation. The final quarter bounds the timing
horizon. Source evidence retrieved after a checkpoint must wait for a later checkpoint; a review
cannot be admitted before its checkpoint occurs. Late review remains explicitly flagged.

`review-outcomes` requires the verified vintage, a complete sorted seven-case review, and an exact
local source manifest in the existing `semiconductor-atlas-milestone-local-sources-v1` format.
Every review row repeats the exact project, scope and event and includes the evidence interpretation.
Statuses are `observed`, `right_censored`, `cancelled` or `unknown`; the unresolved aggregate stays
excluded with unknown outcome. A retained case that abstained still requires outcome review.

The source-guidance statement is an input, never the outcome label. Reviewers must establish the
same project's actual event, explicit nonoccurrence through a date, or cancellation. A revised
target, opening, tool installation, construction completion or another phase's event cannot stand
in for production. Exact source bytes/spans, publication/retrieval clocks and literal event dates
are verified, but substantive interpretation and independence remain disclosed reviewer judgments.

`score` re-executes the vintage and re-verifies the outcome artifact and evidence. It retains all
screened cases and all issued/retained denominator counts, including:

- observed intervals wholly inside the prediction (guaranteed containment), overlapping intervals
  (only possible overlap), and disjoint early/late misses;
- explicit nonoccurrence through or beyond the prediction's inclusive upper bound (a proven
  window miss, even if the eventual production date remains unknown);
- unknowns, cancellations, abstentions, pre-vintage outcomes, vintage/horizon-straddling intervals,
  after-horizon observations and the original aggregate exclusion.

Missing reports are unknown, not right-censoring. Cancellation is reported separately from an
observed production date. A later discovery of an already attained pre-vintage event does not
retroactively make this a successful prospective prediction. No point error, probability score,
calibration, conditional-only success rate or independent-case claim is emitted. Full counts
make unresolved and adverse outcomes visible rather than shrinking the denominator.

Outcome artifacts are separate new-only files; retain every quarterly review and its whole-file
hash. Raw source bodies are not embedded in outputs. The inherited input lineage and outcome
excerpts are local-review artifacts, not cleared public redistribution packages.

## First executed vintage: September 8, 2026

The real comparator was executed and frozen at `2026-09-08T19:46:41.806168Z`, after input
validation, with evidence cutoff `2026-09-08T19:46:41Z`. The retained artifact is
`artifacts/2026-09-08-guidance-interval-baseline-v1.json` (871,408 bytes), whole-file SHA-256
`d7d0b539717b3c51799710190e21c40247e8d62b20908d7035992a1ba7de4b00`.
Its internal content seal is
`32be372f69318d5889a6fa8ce3402a482dc17bccde703ed65f6eb6a7d113ae7c`; the whole-file hash,
not this internal seal, is the independently retained replay argument.

The actual output contains three issued intervals as listed above, three abstentions, and one
screened-out aggregate: seven screened cases, six retained cases. All eight expected milestone
claims have cutoff-bound screening lineage, separate from the fourteen accepted source-statement
inputs used for source selection and literal extraction. Eighty-six code/migration/script files
and the actual interpreter are fingerprinted. No core facts, numeric capacity, outcomes or
training artifacts were created.

TSMC's selected source capture was one day old at freeze; the other selected captures were
52 days old. Those ages are disclosed input limitations, not successful September 8 source
checks. The first outcome checkpoint is `2026-12-31T23:59:59Z`, with review due by
`2027-01-31`; the final timing horizon is `2031-09-30`. No real outcome review, scoring report,
calibration result or predictive improvement is asserted.

An independent CLI process re-executed the comparator and matched all 871,408 vintage bytes
against the retained whole-file hash:

```sh
python3 scripts/guidance_interval_baseline.py verify \
  --database artifacts/2026-09-08-tsmc-fab-correspondence-schema6.sqlite \
  --vintage artifacts/2026-09-08-guidance-interval-baseline-v1.json \
  --trusted-vintage-sha256 d7d0b539717b3c51799710190e21c40247e8d62b20908d7035992a1ba7de4b00
```

Pinned inputs:

| Input | SHA-256 |
| --- | --- |
| Exact schema-6 database | `d31fd0597ddf188f76c44d2758f46f95d00f0a22169c1360b34b7c390aaeaeeb` |
| Original complete screen | `9027e8fca50996d156c8f93c9055e15c5b8d03f0fc0c21dd4928399cfbd7e339` |
| Reviewed source-input map | `edee4f6d92a6932b58a1b1ac86d10e87f684c10b0a0a3da570b598bb659131e2` |

Validation: 1,519 core tests in 237.868 seconds (1,518 passed, one expected optional-payload
skip), all 12 web tests, syntax checks, and source/wheel builds passed. Sixty-two new tests cover
the comparator, diagnostic and outcome-review contracts. A synthetic command-line
freeze → outcome review → score smoke test also preserved all seven cases and unchanged database
bytes. These engineering fixtures are not historical forecast performance. This remains a
source-checkout workflow; successful wheel construction does not install these scripts.

The scheduled parent checkout, source snapshots, schema-5 database, baseline and checksum ledger
remain unchanged. The original screen is preserved byte-for-byte. Work and artifacts remain local
in the isolated `codex/completed-milestones-v6` branch; nothing was pushed or published.
