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
does not decide scope. The
[full-population research pass](../review_plans/2026-09-07-eea-industrial-v16-scope-research-v2.json)
now documents all 108 candidates, with 38 proposed inclusions, 23 provisional exclusions and 47
evidence/scope deferrals. This closes the unresearched-queue gap, not final scope adjudication:
historical address/process bridges, chip-embedding/materials boundaries and layout-sensitive PDF
checks remain. A subsequent [complete bounded admission](eea_scope_admission_2026-09-07.md)
accepted 15 source records and deferred 93, with no final exclusions. Its isolated schema-5 database
adds 132 exact EEA scalar statements, preserves every parent row and passes write-denied exact
replay. Effective dates and uncalibrated confidence remain null; the original research and frozen
parent remain unchanged. This is exposed collaborative scope review, not blind adjudication,
canonical identity or operating capacity. The subsequent [scope-revision workflow](eea_scope_revisions.md)
adds immutable predecessor-bound reviews, reuse of original materializations, cutoff-aware source
exports and parent-bound restoration without schema changes. The real Newport evidence correction
created zero claims, retained the original cutoff view and restored from an identical portable
export. The legacy v1/v2 entry point remains single-review; source-edition refresh is not implemented.
The 93 evidence/scope holds remain unresolved. Tabular-spatial correspondence must
be verified separately before emitting geometry. Wider GLEIF activation still requires a manually
reviewed allowlist and pinned bulk or delta acquisition at one Golden Copy publication.

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

The comparison alone does not complete Phase 3. The subsequent proposal-review ledger described
below adds acknowledgment and retraction, but delivery hysteresis and blind historical
detection-performance evaluation remain open, so the exit gate below has not been met.

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
The [NIST index discovery collector](nist_discovery.md) now retains the observed current news and
awards page chains independently of facility-scoped checks. One manual eight-request pilot retained
64 links (14 company-name matches), and a restored packet reproduced its inventory exactly. It did
not acquire linked documents or accept claims. The subsequent [discovery queue and poll runner](discovery_review_and_poll.md)
add URL dispositions, admission-time history, recoverable imports and publisher-scoped cadence.
The existing daily task now includes this manually tested job; actual scheduler execution remains
unobserved. The live queue retains 64 URLs, including 14 routed leads and two deferrals, through
an unchanged second packet and exact export/restore. At that stage, handoffs to independently
approved linked-document acquisition remained open alongside wider approved coverage, complete
collection-chain accounting and historical detection-performance evaluation.

The [Intel Chandler expansion](intel_chandler_monitoring.md) adds one exact municipal page naming
Fab 52, bringing monitoring to six documents across four of seven cohort scopes. Its undated
opening/production statement was reviewed with no baseline revision; HVM attainment and numeric
capacity remain unsupported. An ordinary live poll made three requests for Intel while preserving
existing plan due times. The 14-event review queue restores at every event cutoff, and prior v1/v2
coverage reports remain byte-identical. The existing daily task was updated to the manually tested v3
config; actual scheduler execution remains unobserved. Micron Singapore, SK hynix M15X and ASE
Kaohsiung were the three unmonitored cohort scopes at this step.

The subsequent [Micron MTI expansion](micron_mti_monitoring.md) adds an exact Singapore government
speech about the January 8, 2025 HBM packaging groundbreaking. The v4 catalog/config now cover
seven documents across five of seven scopes; SK hynix M15X and ASE Kaohsiung remain unmonitored.
The older speech was reviewed without a baseline revision. A fresh retrieval, migration notice
or site-wide footer does not become a new physical milestone, and the separate 2026 NAND project
is excluded. An ordinary three-request live capture and two no-network repeats preserved prior
cadence. The 16-event queue restores at every event cutoff and historical v1/v2/v3 coverage remains
byte-identical. The existing daily task now uses the tested v4 config. This exact-URL monitoring
does not establish discovery of later Micron announcements, current project status, scheduler
uptime or an evaluated detection service; the issuer-site restriction remains unchanged.

The [discovery-to-source handoff verifier](discovery_handoff.md) now connects one observed URL to
independent exact-document approval, retained acquisition and explicit source-text review. The real
Samsung Taylor pilot found duplicate substantive text on the already monitored Austin page. Both
versions were reviewed without a baseline revision; CMS page metadata is not treated as claim-level
dating or manufacturing progress. The new URL remains a one-off capture, so coverage stays at seven
scheduled documents across five scopes. The 19-event source queue and five-event discovery queue
restore at every event cutoff, and historical v1/v2/v3/v4 coverage remains byte-identical. Local
handoff replay requires the original capture paths and is explicitly retrospective. This advances
evidence routing, not source-to-claim acceptance, incremental detection or forecast calibration.

The [source-native project target gate](source_project_targets.md) now accepts the retained TSMC
Fab 2 target revision as two document-version source statements in the core store, admitted together
at `2026-09-07T09:28:12.852741Z`. The old source says 2028; the newer source says second half of
2027. Schema 5 preserves unknown effective dates and confidence, literal calendar precision without
midpoints, and a separate knowledge-time source-claim export. The source-text queue has handed off
this version; the project remains unassigned to a canonical facility. Its first publication date
is not established by CMS metadata, and no attained production, capacity or first-fab baseline
revision follows. A populated working-copy upgrade preserves the original 104,286 claims and all
lineage. This closes a bounded source-to-claim integration gap, not the blind evaluation, forecast
or full-coverage gates.

The [unified proposal-review queue](alert_review_v2.md) now connects this accepted source-native
comparison to the same working review history as the Amkor facility proposal. Version 2 retains the
original two Amkor events exactly, admits the Fab 2 proposal at `2026-09-07T09:58:41.341424Z`, and
preserves its subsequent acknowledgment. The real four-event history restores at every cutoff;
one microsecond before project admission still exposes no project proposal. The builder replays
accepted source evidence on a coherent read-only core snapshot. Portable packet validation checks
retained-content consistency, not independent source authenticity or production attainment. Both
proposals remain non-deliverable, and the original seven-facility evaluator is not used to score
the unassigned Fab 2 project. The additive [accepted-project population evaluator](project_target_evaluation.md)
defines a complete supported-route core comparison denominator before a cutoff, including
unadmitted revisions and reaffirmations. Its retrospective source-statement labels separate
conditional proposal support from admission coverage and production realization. Independently
adjudicated outcomes, blind detection evaluation and calibrated forecasting remain open; the
new workflow alone does not meet those gates.

The additive [source-observation inventory](curated_observation_population.md) moves selection
upstream of accepted claims. It retains all source-queue packets, declared capture roots, durable
poll intents and original predecessor ledgers, including failed/uncomparable cases. Its actual
22-check retrospective population contains repeated unchanged observations but no semantic target
revision labels. Historical replay preserves the old population after later captures and queue
events are added. This is not a publisher-complete truth inventory or the blind evaluation gate.

The separate [source-native statement review](source_statement_review.md) covers all 22 frozen
checks without selecting only accepted claims or changed HTML. Eleven target-bearing pairs carry
sixteen formulations over nine distinct evidence pairs; five scoped no-target findings and six
uncomparable checks remain separate. No target revision is found in these actual paired versions.
This closes the exposed semantic annotation gap for the declared source sample, not source
completeness or measured detector performance.

The additive [selected source-vintage review](source_vintage_review.md) now covers all three
exact NIST award URLs shared by the July snapshot and September inventory. Twenty-five unmatched
historical award URLs, thirteen outside September checks and one shared policy-blocked check
remain explicit. Eight target formulations produce two revised Fab 2 formulations describing
one subject-level revision and six unchanged formulations across five described subject scopes.
Original collector predecessors and source statements remain unchanged; this known-positive
and control comparison is retrospective and exposed, not independent detector evaluation.

The next implementation gate is prospective shadow detection: declare supported URLs, target
scope, observation window, detector code/configuration and stopping rule before observing the
window; record every eligible opportunity, quiet result, failure and abstention independently
of claim acceptance. Seal predictions before separate adjudication, distinguish repeated
observations from distinct subject-version transitions, and reconcile the complete opportunity
denominator before scoring. Ordinary due collection must retain future unchanged, changed and
failed observations without bypassing policy or cadence. Independent unexposed adjudication,
meaningful positive coverage, measured detector performance and calibrated forecasts remain open.

The [prospective shadow protocol](prospective_source_targets.md) now implements pre-window
code/population registration, acceptance receipts, ordinary-poll predictions and a fixed-window
closing census. All configured documents remain in scope even where parser support is absent.
The September 8–15 UTC study is an operational prospective step, not proof of future execution,
unexposed outcome adjudication, positive-event coverage or measured detection performance.

A separate [source-only review and evaluation layer](prospective_target_evaluation.md) now
prespecifies document-triage accounting, hides detector outputs from the review artifact and
joins evidence-bound labels only after packet acceptance. It retains missing/late/abstaining
positive cases in timely sensitivity and includes late wrong candidates in review burden.
Actual future adjudication and independently verified detection performance remain open.

The [AI-critical alert-review ledger](ai_critical_alert_review.md) connects validated comparisons
and both release vintages to explicit, manifest-bound admission reviews. It retains derivative
evidence and supporting review bytes in a portable append-only history, with acknowledgment,
resolution, retraction and reopening. Actual admission clocks prevent retrospective events from
appearing as earlier detections. This is proposal review, not canonical claim acceptance or
automated delivery. The first real case is the Amkor retrospective lifecycle change; detection
precision, time-based hysteresis, independently adjudicated historical outcomes and forecast
calibration are still unproven. The [Micron acquisition review](../review_plans/2026-09-07-micron-singapore-acquisition-deferred.json)
found no sufficient retention permission for the selected issuer route and did not enable a
collector or revise the baseline. The full seven-facility coverage denominator is unchanged.

The [frozen diagnostic evaluator](alert_evaluation.md) retains the complete admitted-episode
population and evidence-linked outcome labels for offline replay. It measures conditional
precision, unresolved-label bounds, source-to-admission lag, duplicate matches and recorded
retractions. Earlier source events may support new backfills but do not count as in-window recall;
missing labels do not become verified misses. Window and deadline selection are retrospective,
independence is unverified and the truth inventory can be incomplete. This does not complete the
blind historical evaluation gate. An [ASE/BIP access check](../review_plans/2026-09-07-ase-bip-acquisition-blocked.json)
also retained a rejection page despite HTTP 200, leaving ASE monitoring disabled rather than
treating transport success as access approval.

The [subsequent gap review](monitoring_gap_review_2026-09-07.md) checked separate ASE
municipal and M15X labor-office leads. It distinguishes a missing robots file from
access rejection and successful robots access from unresolved document reuse
rights. Neither source is enabled. The three unsupported existing detector routes
contain historical or undated assertions, not three additional calendar schedules;
their interpretation requires separate source-native assertion work, not invented
dates or changes to the registered study.

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
