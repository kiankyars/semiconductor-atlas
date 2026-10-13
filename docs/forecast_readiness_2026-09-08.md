# September 8 prospective forecast readiness and cutoff repair

This pass screened every expected milestone in the retained schema-6 successor and repaired two
experimental capacity-forecast selection defects. It did not register a study, freeze predictions,
admit new manufacturing facts, or establish calibration.

## Complete current evidence screen

The database is `artifacts/2026-09-08-tsmc-fab-correspondence-schema6.sqlite`, SHA-256
`d31fd0597ddf188f76c44d2758f46f95d00f0a22169c1360b34b7c390aaeaeeb`.
It contains eight expected milestone claims on seven source-native project entities. Six are
legacy `nist_timeline_window_v1` derived estimates; the other two are document-version statements
about the same reviewed TSMC Fab 2 project, not two projects.

The complete [screening record](../review_plans/2026-09-08-prospective-milestone-roster-screen.json)
was reviewed at `2026-09-08T19:11:01Z`. Its 15,760 bytes have SHA-256
`9027e8fca50996d156c8f93c9055e15c5b8d03f0fc0c21dd4928399cfbd7e339`.
All eight stored claim payloads and identities were checked against the database; all six retained
source bodies and six scoped byte spans matched their recorded hashes. This is a selected
retained-source screen, not a fresh exhaustive publisher search or independent blind review.

| Candidate | Exact event and retained source wording | Prospective treatment |
| --- | --- | --- |
| TSMC Arizona Fab 2 | Production start; second half of 2027 | Future source interval, with null midpoint; earlier 2028 statement stays historical context |
| Hemlock new facility | Production start; 2028 | Future source year; follow-up found an accepted literal scalar, so no new admission is needed for the comparator |
| SK hynix Indiana | Mass production; second half of 2028 | Future source interval; follow-up uses an accepted scalar under the comparator's distinct event contract |
| Amkor Peoria | Mass production; end of 2027 | Abstain on vague bounds and unresolved correspondence to the newer Phase One opening scope |
| Infinera San Jose | Production; summer 2026 | Abstain: no defensible wholly future literal interval; no attainment/nonoccurrence inference |
| Infinera Bethlehem | Production; summer 2026 | Same abstention; related to San Jose under the shared award |
| Legacy TSMC Arizona aggregate | Extracted first-fab 2025 estimate on a multi-fab entity | Screen out until phase allocation is resolved; not a second Fab 2 case |

Hemlock's event concerns a new polysilicon facility on an existing operating campus, not the
campus's original commissioning. The SK hynix source concerns the West Lafayette packaging line
and R&D testbed facility, with no invented phase number. Infinera's site-specific statistics
distinguish the fab/foundry project from the test/packaging project, while the shared overview
mentions both sites. A generic same-page geographic relationship is not sufficient allocation.

The legacy `milestone.production_start` predicate is not an event definition: Amkor and SK hynix
have typed `milestone_type = mass_production`. Likewise, stored July 1, October 1, December 1 and
July 15 midpoints, 0.75 confidence and vague-season calendar bounds are importer assumptions,
not publisher-supplied values. They must not enter a source-guidance comparator as source truth.

All candidates are in the United States. State-level comparisons are not evidence of
international generalization. The two Infinera cases must remain grouped; the existing v1
benchmark prohibits one project group crossing geography labels, so a later contract must
explicitly represent their joint geographic grouping rather than silently splitting them into
independent holdouts. No partitions were assigned in this review.

## Repaired forecast input boundary

The [September 7 audit](forecast_readiness_2026-09-07.md) identified a caller-override defect.
It was reproduced against this actual successor: querying year-2000 world and knowledge cutoffs
normally yields no forecasts, but supplying the 104,286 current physical-view records through
`claims=` previously yielded two Amkor scenario series at those year-2000 cutoffs.

In this isolated branch, `forecast_current_capacity` now always loads the authoritative database
view at both cutoffs. The optional `claims=` argument is a complete-view consistency check, not
an override or subset selection. Stale, missing, duplicate, extra or altered records fail before
forecasting. A source-only view with unknown effective dates is not accepted as a replacement for
the physical-current view. Reordering an otherwise identical complete view is allowed.

The complete read and transitive document-pairing queries share one SQLite savepoint snapshot.
A concurrent WAL writer cannot change the lineage between the view read and pairing. An existing
caller transaction/savepoint and its uncommitted work remain intact; forecasting does not commit
that work or leave a new transaction open. Caller-side repeated hydration is now a deliberate
verification cost, not a performance cache shortcut.

The real before/after check is retained in
`artifacts/2026-09-08-forecast-cutoff-repair-check-v1.json`, SHA-256
`1f66dafe68c9856e666f0ea68fc767c3ef3ff5b0f6002838f6b9ca3410f5652d`.
It ran the unchanged parent implementation and the isolated repaired implementation against the
same read-only database. The old override produced two year-2000 forecasts; the repaired override
raises an explicit cutoff/view error. Normal year-2000 queries still produce zero. Current
correctly selected scenario CSV bytes match exactly, SHA-256
`a68e6b0408fcd2547dea99ed7823378d44458a383007160e6adb365b28713497`.
Both diagnostic runs made zero database changes.

This is not a complete forecast-lineage audit: selected roots are now authentic and cutoff-bound,
but the experimental bridge still lacks the prospective benchmark's full transitive availability,
project-phase, fitted-training and outcome controls. In particular, preserving its current two
Amkor scenario series does not resolve their applicability to the expanded project reviewed in
the [Amkor successor](amkor_peoria_successor_review_2026-09-07.md).

## Cancellation evidence conflict

Independent review also reproduced a synthetic case where one linked expected production
milestone caused the bridge to ignore a linked cancelled production milestone. The bridge now
abstains when cancellation evidence links to an input requiring a production ramp. Conflicting
active/cancelled evidence is reported as `conflicting_production_milestone_statuses`, with both
sets of claim IDs retained. A cancellation alone keeps the existing cancellation exclusion.

This uses the bridge's existing direct/shared-source lineage pairing rule, not newly resolved
project identity. An unrelated unlinked cancellation does not override a supported target, and
cancellation of a future ramp does not erase separately stated economically usable capacity.
No physical closure or capacity loss is inferred from the abstention.

## Verification

The final core suite ran 1,457 tests in 242.319 seconds: 1,456 passed and the expected optional
local-source-payload test was skipped. All 12 web tests, Python syntax checks and source/wheel
builds passed. Fourteen new regressions cover both cutoffs, altered/omitted input records,
supersession, transaction/savepoint preservation, concurrent WAL writes, and linked versus
unlinked cancellation evidence. The former source-view test now requires an explicit view-mismatch
error instead of allowing caller data through to a downstream capacity exclusion.

The previously retained TSMC correspondence successor also passed its exact hash-bound replay
under the changed checkout. The original source-native importer, scheduled parent analytics,
schema-5 database, baseline input and checksum ledger remain unchanged. New analytics SHA-256 is
`ed688653d98aacf15185a7cd0b26a34d1ba688ea1b166a2207ebd9c1118e92ae`; the parent analytics remains
`f98c1541be422e29e28fbadaa20a111b9b8d4ab7e83a47a1884ad01fc98537ab`.

## Next forecasting gate

The subsequent [executed source-guidance interval comparator](guidance_interval_baseline.md)
now preserves three supported future intervals, three explicit abstentions and the screened-out
aggregate, with exact replay, distinct `mass_production`, null midpoints and prespecified complete
outcome review. Its separate input map corrects the original screen's scalar-admission assumption
and old/new TSMC source binding without rewriting the screen bytes. This is a new interval-only
contract, not a changed v1 point-prediction study or an opaque unexecuted model artifact.

The next evidence gates are broader prospective project coverage and separately reviewed later
realizations. The current comparator has no real outcomes or measured predictive performance;
retained guidance must not be treated as a fresh publisher check or the evaluation target.

This comparator is necessary for evaluating a later predictive model, not a substitute for one.
A complete north-star still requires broader non-survivor/project/geography coverage, measured
change-detection performance, genuine usable-capacity evidence, and tested predictive improvement.
The current work remains local in the isolated branch; scheduled code and frozen artifacts are
not repinned or rerouted.
