# Public release and Supply Intelligence handoff audit

Verified September 7, 2026. This audit separates an available public release, consumer
compatibility, and sufficient evidence for numerical output. Those are different gates.

## The upstream release exists

The [r3 GitHub Release](https://github.com/kiankyars/semiconductor-atlas/releases/tag/2026-08-20-ai-critical-manufacturing-baseline-v1-r3)
was published on August 23, 2026 at 20:19:15 UTC. Its public asset was independently downloaded
for this audit, rather than supplied from a sibling working directory. GitHub asset metadata,
downloaded bytes and the pinned archive digest agree:

- Asset: `2026-08-20-ai-critical-manufacturing-baseline-v1-r3.tar.gz`, 84,065 bytes.
- Archive SHA-256: `fa3db457bd4734511dee267c13d8b2404b2f25a7b2d12f85bbf4c6f4aad542f4`.
- Manifest SHA-256: `1a7da06916cc935cb8b9310ae947de491c817b7ddd4abb18472fbc5d749aab45`.
- All 22 regular archive members verified: the manifest and its 21 size/hash-bound payloads.
- Seven bounded facility scopes, 75 claims, 12 evidence fragments and zero eligible quarterly
  capacity rows. The two numeric capacity rows are announced monthly rates without applicability
  period bounds. Qualification, yield and utilization remain unknown for all seven scopes.

The world-state cutoff remains August 20 and the original recorded-at clock remains
`2026-08-20T17:48:42.113878Z`. Downloading or repackaging the asset does not refresh either clock.
No publisher response bodies were extracted or executed, and no new release was published.

## Two consumers, neither compatible unchanged

The independently inspected Supply Intelligence checkout was clean at
`cee4db06c35be73d493358fa2794e181c7f809fc`. Its production upstream lock still has zero entries.
The source checkout was read for contract diagnostics only, not used as upstream production data.

| Boundary | Current consumer requirement | Actual r3 |
| --- | --- | --- |
| Pulse asset | Exact locked public GitHub `.zip` | Public `.tar.gz`; real asset descriptor rejected with `actual_public_asset.name must end with .zip` |
| Pulse archive | Exactly `manifest.json` and `claims.json` | 22 prefixed TAR members; actual downloaded bytes rejected with `locked upstream asset must be a valid zip` |
| Pulse schema | `ai-supply-upstream-claim-release.v1` | `ai-critical-manufacturing-schema-v1` with native Atlas claim/evidence semantics |
| Legacy manifest | `semiconductor-atlas-release-v1` | `semiconductor-atlas-ai-critical-release-v1` |
| Legacy evidence | Manifest-bound `evidence.csv` and legacy claim fields | `evidence.jsonl` and native AI-critical claim fields |
| Legacy numerical selection | Nonempty explicit IDs, quarter totals, exact quarter bounds and numeric confidence | Zero eligible quarterly export rows; native null confidence means unknown |

The two Pulse errors were reproduced against the public asset metadata and bytes, entirely as
read-only diagnostics. The legacy barriers were established from its implementation and the
verified manifest; no full legacy import or fabricated selection was attempted.

## Repackaging would not supply missing evidence

The existing Blackwell Constraint Pulse is a separate, frozen 2026-Q4 consumer. Numerical
eligibility requires a release cutoff within that quarter and no more than seven days before the
evaluated Sunday. August r3 cannot satisfy that freshness gate. Its seven facility IDs also are
not automatic mappings to the consumer's product-specific targets. Unknown target IDs remain
incompatible even when a claim is qualitative.

Numerical eligibility additionally needs compatible product allocation, scope, units, time basis,
posture, ranges and evidence. A date-only source publication date cannot become a fabricated
timezone-bearing timestamp. Announced monthly rates cannot become realized quarterly output by
multiplication, and missing confidence cannot become a calibrated number.

The frozen consumer requires 107 active inputs across manufacture, shipment and energization,
three stage targets and eight categorical gates. Seven generic facility scopes do not satisfy
that inventory. Its required non-estimate remains `no evidence-backed estimate.` No production
pulse was built with invented future clocks for this audit.

## Next implementation boundary

The useful compatibility change belongs in Supply Intelligence: a separate native-Atlas public
asset reader for bounded facility and evidence context. It should verify an explicit immutable
asset lock and all required lineage, preserve scope and unknowns, and report zero eligible
quarterly replacements for r3. It must not feed facility context into the existing numerical
Pulse gates or silently relabel synthetic inputs. This is an additive consumer change, not a
reason to mutate the published producer or emit an empty compatibility ZIP.

That work requires extending the sibling project's scope explicitly. Numerical progress remains
a separate acquisition and review task: genuinely supported product/quarter/scope-matched inputs,
followed by evaluated detection and calibrated forecasts. Neither an adapter nor this audit
completes those gates.

## Validation and retained diagnostic

The unchanged consumer's relevant suites were rerun:

```sh
env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_upstream_release tests.test_blackwell_pulse tests.test_atlas_adapter -q
```

All 45 tests passed in 0.217 seconds. They cover the existing contracts, not a new native-r3
adapter, numerical sufficiency or forecast accuracy. No Atlas production code changed, so the
full Atlas suite was not rerun for this documentation-only handoff.

Local-only diagnostic: `artifacts/2026-09-07-public-r3-handoff-diagnostic-v1.json`, 5,388 bytes,
SHA-256 `9060d8a59fe8154519889ac93029f56aa8e65eee5bd3a8e2d4ce4a41e32ae4a0`.
It records the public metadata, every payload verification count, preserved unknowns, consumer
HEAD/code/lock hashes and reproduced rejection messages. The one-off verifier is
`artifacts/2026-09-07-verify-public-r3-handoff.py`, SHA-256
`74ae2579cc75c760191f83662f5c7240714c7b9df92792df0530da83863d1606`.
These diagnostics are not portable production inputs or a new checksum-ledger entry.

No consumer source, lock, cache, scenario or claim state was changed. The published producer,
original checksum ledger, existing collectors and registered September 8–15 evaluation remain
unchanged.
