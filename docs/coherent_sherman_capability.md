# Coherent Sherman: existing source-reported InP capability

The retained June 16, 2026 NIST release describes an existing high-volume 150 mm
indium-phosphide semiconductor manufacturing facility in Sherman, Texas. Its
proposed cleanroom and equipment expansion is a separate future intervention.

The [deliberate admission review](../review_plans/2026-09-09-coherent-sherman-capability-admission-v2.json)
accepts three atomic, typed `SOURCE_STATEMENT` capabilities:

| Capability type | Value | Unit |
| --- | --- | --- |
| `material` | `indium_phosphide` | null |
| `wafer_size` | 150 | mm |
| `production_scale` | `high_volume` | null |

All three share one source narrative and one scope qualifier. They are not three
independent observations. The native 150 mm value is retained; the parenthetical
six-inch description is not converted into a different diameter. `valid_from`,
`valid_to` and confidence remain null. Publication is recorded at day precision,
not treated as HVM onset or a continuing physical-effective state. These claims
remain outside physical-current results.

The subject is a new source-native FACILITY, not a city-name merge with the
existing GlobalWafers or Texas Instruments Sherman sites. EPA's FINISAR SHERMAN
entry is a research lead, not an accepted identity bridge: the NIST narrative
does not give its address, registry identifier or historical company identity.
No ownership, geometry, building or production-unit allocation is created.
The new source reuses the unchanged `us-official-semiconductor-awards` family
(`058a2903-ba0f-5a3b-a0c2-fcc50f1df3bd`), which already contains NIST CHIPS
disclosures. The capture ledger's different family label does not justify a
second, apparently independent core publisher family.

## Evidence and scope

The complete retained HTML body is 87,366 bytes, SHA-256
`05f30892d499bbb73679f10e24783eeb18e3530b3c0f9e9ba9d4d549ca70958c`.
The complete six-paragraph narrative occupies UTF-8 bytes `[72685,74920)`, SHA-256
`bae274760830345b31d90b33aa924bc024b7bc6538420f2af259b1d7e6301122`.
Separate complete title and publication elements are also hash-bound.

The frozen three-source acquisition packet retains Bosch and Powerex alongside
Coherent. Its manifest SHA-256 is
`05a33260bde85bf62efe4c3e8c5bd81430ba3c9c62f140ad718785cc5064579a`.
The importer verifies all 18 inputs totaling 375,005 bytes: the complete packet
and its unchanged access review, including policy bodies and capture receipts.
Offline capture replay checks the original source/policy results; no requests
are made. Only the reviewed Coherent capability is admitted to core.

The source was retrieved at `2026-09-09T04:23:23.622514Z`; the separate core
acceptance review completed at `2026-09-09T08:59:26Z`, SHA-256
`14db13eb1c90811adbe5c09c1ef6bf2982d9d50d2ebedb70f7ba787bca73d0d7`.
The earlier access review remains explicitly non-accepting. This admission grants
no raw redistribution, model training, linked PDF/image acquisition or scheduled
source execution.

The source's up-to-$50-million letter of intent is not a final funding award or
replacement of the original PMT. No expansion completion, dated HVM attainment,
numeric output, capacity basis, yield, utilization or qualification follows.
The original 45-group/50-case diagnostic, targets and outcome labels remain
unchanged. This is exposed retrospective source evidence, not independent
physical corroboration, a calibrated forecast or a measured alert result.

## Execution and replay

`scripts/admit_coherent_sherman_capability.py admit` creates a new-only schema-10
successor from the exact schema-10 parent. It records the actual admission clock,
complete evidence inventory and producer hashes. It neither migrates nor edits
the parent. `verify` reconstructs all successor bytes using the original clock.

After producer changes, the `coherent-sherman-capability` verify-only operation
in the [frozen runtime controller](frozen_runtime.md) runs the retained producer
with literal review and successor hashes. New capture or refresh behavior is
not part of this bounded admission rule.

## Executed admission

The new successor is
`artifacts/2026-09-09-coherent-sherman-capability-schema10-v2.sqlite`, 310,259,712
bytes, SHA-256
`d7fa83b3593030d4dc9a8942e066c8dd71b9e005dcabef7f887adcb0439d7572`.
Actual admission was `2026-09-09T09:01:20.471981Z`. Its subject is
`b3baff3d-46a7-5954-a400-f54c40dc7851`; its run is
`9fba2f32-cfd7-5763-93cf-92b07d60b4bb`.

The earlier filename without `-v2`, its v1 admission review and v1 checker
receipt are retained **rejected validation candidates**, not accepted successors
or publication candidates. They preserved rows but mistakenly added another
NIST source family. That semantic defect was caught separately from the passing
row-preservation check. V2 requires the existing family and forbids outputs
inside the immutable acquisition packet.

The old Ohio One schema-9 image also replayed exactly in its unchanged frozen
producer after this addition. The new receipt is
`artifacts/2026-09-09-post-coherent-city-frozen-replay-v1.json`, SHA-256
`b9aa31c2fabe15fc703b10148ba9e7d6909a19f468ac442d47e538748e9fe7b7`.
The schema-10 parent, original PMT diagnostic and reviews, protected parent
checkout, checksum ledger, scheduled database and r3 bundle retain their hashes.

## Independent and installed-package acceptance

The independent v2 checker verifies all 543,226 inherited rows across 30 tables,
unchanged schema SQL and precisely 22 added rows, including three capability
claims and no source-family addition. It does not import the admission module.
The installed readers reject the new entity a microsecond before admission,
return three source statements at admission, and retain zero physical-current
claims for the subject. The checker is
`artifacts/2026-09-09-check-coherent-independent-v2.py`, SHA-256
`a8145078ff99d5e162c0c492630022160231a3f5e1f070ae7c557d18fe01420a`;
receipt `artifacts/2026-09-09-coherent-independent-check-v2.json`, SHA-256
`95558e2cfc699f9d81aa799b96074fadb00ddd80793e37312f347d14e3d76791`.

The v2 wheel is
`artifacts/2026-09-09-coherent-capability-dist-v2/semiconductor_atlas-0.1.0-py3-none-any.whl`,
SHA-256 `f7bd247f2b04befb299ed671c0f16a7d31bf1c1f7a32a3cb2e93b46cfb0ce0b6`.
All 105 installed package files match the source checkout. The checker imported
19 Atlas modules only from that wheel under `-I -S -B`. The installed CLI also
produced `artifacts/2026-09-09-coherent-sherman-dossier-v2.md`, 19,100 bytes,
SHA-256 `e368548f2a0c8f59e736a8b3b01fea5cff4bae92f66b85edc906912304f7efc3`,
matching the independent reader's Markdown result. Direct v2 reconstruction
matched all 310,259,712 successor bytes.

Image-opening order matters on the tested CPython 3.14.7 / SQLite 3.53.4 runtime:
the importer deserializes on a bare connection before registering clock functions.
A reduced disposable test reproduced native SIGBUS after querying
`pragma_function_list`, then deserializing and querying it again, even with
Python statement caching disabled. Bare-deserialize-first checks passed. This
is a locally reproduced trigger and safe order, not a complete SQLite root-cause
diagnosis or a change to the shared database API.

## Regression validation

The final full suite executed 2,107 tests in 277.741 seconds: 2,106 passed and
one optional-data test skipped as expected. All 12 web tests and 20 viewer-state
JavaScript tests passed. The focused admission/dossier/feed/frozen-runtime suite
passed 144 tests, including 26 new Coherent tests, eight new explicit-acceptance
tests and six new replay-dispatch tests.

An earlier full run reported one synthetic request-cadence test failure. It did
not reproduce in the 242-test ordered prefix, 20 isolated repetitions, the
144-test focused suite or the final full run. No production clock or assertion
was weakened to obtain the pass; the precise cause of that earlier result is
not established.

No browser-rendered QA or deployment was performed in this backend/evidence turn.
The parent checkout and scheduled runtime remain unchanged; nothing was pushed
or published. Independent manufacturing outcomes, useful measured alerts and
calibrated forecasts remain open north-star gates.

## Frozen committed replay

Implementation commit `80c122ab2b6424650fb35948d3ee0879c6b6c885` is retained in
`artifacts/2026-09-09-80c122a-frozen-runtime-v1.json`: 156 source files,
3,386,422 decoded payload bytes and 4,560,948 bundle bytes, SHA-256
`6f5cccd7b0fb6cb0d23b343b620284c8e4dba9a7cf43cea1a4c52220f21551bc`.
Its isolated `coherent-sherman-capability` verification matched the complete v2
successor. All 25 imported project modules resolved inside the retained runtime.
The new-only receipt is
`artifacts/2026-09-09-frozen-coherent-capability-replay-v2.json`, 6,057 bytes,
SHA-256 `a1a86b3c8ff150f86162cd91af30cc8d160b35878dcb7781daa294a5a44fd0b0`.
This retains project code, not source payloads or a hermetic operating system.
