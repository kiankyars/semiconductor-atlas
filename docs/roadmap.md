# Semiconductor Atlas roadmap

The roadmap is gated by evidence quality and reproducibility, not headline row count. Each phase must
leave a useful, auditable product even if later phases are never completed.

## Phase 0: contract and gold fixture

Deliver:

- entity, claim, temporal, capability, capacity, resource, constraint, and rights ontologies;
- versioned database migrations and deterministic identifiers;
- one multi-phase site fixture with at least two facilities or production units, several
  organizations, and several independent source families;
- contradictory announcement, permit, imagery, and equipment evidence;
- a correction, late-arriving observation, and entity-resolution decision; and
- tests for field-level evidence, derivation, bitemporal reconstruction, units, and rights.

Exit gate: the fixture can reproduce what the database believed before and after a correction,
without deleting either state.

## Phase 1: honest v0.1 registry

Deliver:

- source documents, fragments, retrieval hashes, source families, and ingestion runs;
- stable sites, facilities, buildings, production units, projects, organizations, and relationships;
- atomic facts and estimates with the five capacity bases;
- entity-resolution candidates, decisions, assignments, and redirects;
- strict offline curated import, validation, summary, and deterministic release commands;
- CSV or JSONL claim exports, GeoJSON site views, source-input manifests, and file hashes; and
- package-local methodology, data contract, source registry, and coverage report.

The release must say that it is a seed, not a global census. No system forecast, automated alert,
or investment ranking is required for v0.1.

## Phase 2: repeatable official-source updates

Add a small number of high-value adapters in rights-cleared order:

1. organization and alias baselines;
2. official company and regulatory filings;
3. environmental and facility registries;
4. subsidies, procurement, permits, and utility records; and
5. open geometry and imagery catalogs.

Each adapter needs an offline fixture, idempotent replay, source checkpoint, correction handling,
rate-limit policy, and release-rights test. Add a reviewer queue for conflicts, resolution, and
high-impact claim revisions.

The organization-resolution ledger and bounded offline GLEIF Level 1 parser are implemented. The
ledger preserves every multi-document ingestion input, seals cutoff-safe resolution runs before
decisions, limits active outcomes to `match`, `reject`, and `defer`, and keeps assignments
bitemporal. Exact reviewed LEIs may enrich assigned organizations; name and fuzzy searches only
create review candidates.

The bounded GLEIF snapshot fetcher, offline verifier, reviewed importer, and schema-v3 release are
implemented. They enforce a strict checksum-valid LEI allowlist, one Golden Copy publication, exact
content-addressed response bytes, deterministic derivative replay, a rate below the published API
limit, descriptor-safe file access, and atomic no-replace installation. Review plans bind snapshot
and Golden Copy hashes to an exact target entity, source-backed target claims, outcome, reviewer,
reason, and assignment date. Acceptance re-verifies both snapshot and review bytes at the commit
boundary.

The first accepted pilot contains exactly one Level 1 record, TSMC Arizona Corporation. It passed
exact replay, refresh and omission semantics, assignment correction, acceptance-boundary mutation,
cutoff-lineage, and deterministic schema-v3 release tests. GLEIF claims remain on the source-native
organization, while the reviewed assignment points to the NIST organization. Accounting-parent
relationships remain separate from facility ownership, and Level 2 data is not ingested. This pilot
does not establish broad company or facility coverage.

The first non-U.S. facility and geography gate is now accepted through Taiwan MOENV `EMS_S_01`.
The immutable full-package snapshot pins its POST acquisition, archive and inner-member hashes,
publisher MD5, update and retrieval clocks, Taiwan Open Government Data License attribution, exact
`2611`/`2612`/`2613` filter, and full-package denominator. Its source-native import preserves 724
case-sensitive control numbers and 798 distinct variants, including 73 conflicting identities,
without assigning organizations or inferring operation, ownership, lifecycle, production, or
capacity. Valid WGS84 pairs become source-point geometry only. Full same-filter refreshes may close
source-assertion intervals; partial refreshes and real-world facility state remain open.

The Taiwan facility-resolution gate is now grounded in the independent national registered-factory
publication. Its full-package adapter admits only exact principal-product token `261半導體`, omits the
responsible-person field from derivatives and releases, and treats `生產中` as administrative
registration status rather than observed operation. Schema v4 permits only same-kind organization or
facility identity; cross-kind identity remains prohibited. The reviewed facility pass uses exact
documented factory-number normalization plus compatible address evidence. It accepted 142 assignments,
deferred one succession conflict, and preserves 10 MOENV identifiers absent from the active-only roster
as unresolved. The schema-v4 release is byte-reproducible from its pinned inputs and includes a
standalone atlas.

The bounded Taiwan MOF `BGMOPEN1` acquisition, source-native organization importer, and separate
facility-to-tax-unit review are now accepted. A 466-UBN allowlist re-derived from the two facility
snapshots produced 390 exact active tax-registration matches and 76 misses across the complete
1,709,795-row business population. The reviewed pass accepted 944 exact same-time UBN links as
`registered_tax_unit_reference` claims across 935 facility subjects and 390 tax units. Nine facility
subjects retain two conflicting source references rather than an inferred winner. Tax units,
branches, legal persons, owners, operators, and legal parents remain distinct semantics; the links
assert none of those roles, and quantity fields remain separate from semiconductor output and
capacity. The 24-managed-file release exports the complete review/action ledger and is byte-reproducible.

The EEA v16 European candidate gate is now accepted. Its pinned 2.03 GB Access database and complete
99,275-row EEA-reported production-facility population replay offline to 108 manual-review leads.
The extraction is complete only within that reported population: it is not a European industrial or
semiconductor census, and no European semiconductor-recall denominator is available. A hash-bound
108-item adjudication queue and fail-closed source-native importer are implemented. Queue priority
does not decide scope; no candidate has been adjudicated or imported. The next EEA gate is an
evidence-backed complete review. Tabular-spatial correspondence must be verified separately before
emitting geometry. Wider GLEIF activation still requires a manually reviewed allowlist and pinned
bulk or delta acquisition at one Golden Copy publication.

EPA FRS is the first bounded environmental-registry candidate adapter: it admits only exact NAICS
`334413` or SIC `3674`, retains raw NAD83 coordinates as non-geometry scalars, and treats rows as
U.S. registry leads rather than lifecycle or capacity evidence. Complete same-filter correction and
valid-time interval closure are tested, but non-selection does not yet distinguish code removal from
archive absence. The refresh importer now reuses semantically unchanged claims, versions derived
classifications when qualifying dependencies change, and verifies historical replays without writes.
Source snapshots retain the raw monthly ZIP at a content-addressed path and regenerate the derivative
during verification. Hashing and semantic parsing are bound to private copies, same-document importer
upgrades retain distinct processing runs, and partial snapshots do not create retractions. FRS runs
also separate archive retrieval from database acceptance. A full 3,065-candidate unchanged-refresh
benchmark completed in 20.36 seconds, reused all 74,896 FRS claims, and created no claim versions.
The remaining gates are explicit declassification, absence, and Registry ID merge evidence. None of
those signals may become a facility-closure claim.

Exit gate: refreshes are reproducible from immutable inputs, and coverage age and adapter failures
are visible by geography and source family.

## Phase 3: change detection and alerts

The AI-critical cross-vintage comparison now provides a bounded foundation for this phase. It pins
both release manifests, derives stable semantic series, records `reaffirmed`, `revised`, `added`,
and `not_carried_forward` outcomes, and emits deterministic alert proposals. These proposals have
unknown confidence and are not delivery-eligible. `not_carried_forward` is not negative evidence
unless a future durable ledger proves a successful check of the relevant source and scope.
The retained `r2` to `r3` replay produces 75 reaffirmed series and no proposals, demonstrating that
release-local claim IDs do not create false changes. Historical compatibility is currently bounded
to manifest-bound prose rendering within the installed structured schema.

This foundation does not complete Phase 3. It has no alert acknowledgement, retraction, hysteresis,
or blind historical replay, and therefore has not met the exit gate below.

The first substantive local successor comparison is the September 7, 2026 Amkor review. It links
captured issuer evidence to a reviewed two-phase project scope and an earlier groundbreaking,
preserves unresolved applicability of old throughput claims, and generates one lifecycle proposal.
A checked 11-attempt ledger makes successes and failures visible by company, country and source
family. It covers three selected documents, not a complete publisher search; the six other cohort
members were not refreshed. The subsequent [reviewed-URL collector](curated_acquisition.md) makes
those three document checks repeatable: it binds reviewed policy hashes, retains request receipts,
replays exact bytes, distinguishes text changes from byte-only churn, and carries the last eligible
observation through later failures. Its live repeat produced no content-review candidates.
The [durable source-version review queue](curated_review.md) now retains pending work across quiet
and failed checks, admits unseen versions despite an unchanged capture label, surfaces observed
returns to resolved versions, and preserves reviewer dispositions with knowledge-time replay.
A live integrated repeat retained three pending items until explicit review; its event export
restored to an identical queue history. This is source-text triage, not claim acceptance or alert
delivery. The [coverage catalog and report](curated_coverage.md) now retain all seven cohort
facilities in the denominator and separate configured document age, failed or blocked checks,
expired review windows, and queue backlog. The pilot has three recent Amkor checks and six
unmonitored facilities. The [polling runner](curated_poll.md) adds a process lock, independent cadence
guard, private plan snapshots, invocation receipts, and recovery of completed packets before refetch.
The daily app schedule is enabled, and a manual live capture plus not-due repeats passed. An actual
scheduler-triggered outcome remains unobserved. A subsequent [NIST source expansion](nist_monitoring_expansion.md)
adds two exact project-page checks relevant to TSMC Phoenix and Samsung Taylor, bringing the
configured denominator to three of seven scopes and five documents. Two first observations remain
pending review. Scope-bearing report v2 preserves the original v1 reports byte-for-byte; project-wide
text is not automatically attributed to an individual fab. The daily task uses the expanded config.
The other four scopes, wider approved coverage, new-document discovery,
complete collection-chain accounting, and historical detection-performance evaluation remain open.

Add:

- open optical and radar change proposals;
- permit, filing, procurement, and source-document diffs;
- versioned lifecycle and milestone reconciliation;
- deterministic alerts for acceleration, stall, completion-date shift, withdrawal, redesign,
  announced-to-installed gap, installed-to-qualified gap, and material capacity revision; and
- alert deduplication, hysteresis, acknowledgement, resolution, and retraction.

An alert must show the before state, after state, triggering claims, evidence fragments, rule or
model version, and confidence. Absence signals require a recorded successful source check.

Exit gate: a blind historical replay measures alert precision, false-positive burden, detection lag,
and retraction behavior.

## Phase 4: facility-level forecasts

Add versioned quarterly forecasts for project milestones, capacity bases, utilization, yield, and
resource use. Forecast runs pin their input release, cutoff, model, configuration, and twenty-quarter
point series. Aggregates cover company, country, process, packaging, wafer size, product/end market,
and quarter with an explicit ownership, operator, or customer attribution basis.

Forecast publication requires time-split and geography-held-out backtests. Report error, bias,
calibration, and interval coverage by company, region, technology, and stage. Company guidance is an
input source claim, not the forecast target.

Exit gate: forecast vintages can be replayed exactly and evaluated against later realized claims
without look-ahead leakage.

## Phase 5: supply-demand scenarios and investable signals

Add scenario assumptions for demand, yield, qualification, project timing, export controls,
equipment availability, pricing, and utilization. Scenario results estimate capacity, supply,
pricing, market share, equipment demand, capital intensity, and listed-company earnings effects.

Investment signals rank positive and negative revision risk only when they identify:

- the listed issuer or security;
- the consensus-sensitive driver and horizon;
- magnitude and uncertainty;
- base and alternative scenarios;
- disconfirming evidence; and
- exact claim, forecast, scenario, and alert lineage.

Exit gate: a frozen historical evaluation demonstrates signal calibration and avoids selecting
only surviving or successful projects.

## Scale and parity gates

Broader automation, commercial sources, or high-resolution imagery are added only when marginal
information value exceeds legal, operational, and review cost. Distributed queues and streaming
infrastructure are deferred until batch jobs and database-backed work queues are demonstrably
insufficient.

No claim of global comprehensiveness or superiority to SEMI, SemiAnalysis, TechInsights, TrendForce,
or Omdia is allowed until a lawfully licensed common snapshot is normalized to the same entity,
capacity-basis, geography, and time definitions and evaluated for coverage, freshness, accuracy,
calibration, and lineage.
