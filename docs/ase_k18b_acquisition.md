# ASE K18B municipal evidence acquisition

The isolated September 8 workflow closes the exact-byte acquisition barrier for one municipal
K18B article. It does not activate a schedule, change the seven-company baseline, admit core
manufacturing claims or complete the forecasting gate.

## Reviewed missing-robots response

Acquisition **plan v2**, **run v3** and rule `fixed-url-reviewed-policy-response-v2` add two explicit
capabilities. Existing v1 plans retain their original rules and replay bytes.

- Exact identifier query parameters may appear in reviewed URLs. Keys must be unique and values
  nonempty ASCII identifiers; percent escapes, whitespace, duplicate keys, redirects and arbitrary
  query expressions are rejected. The full original URL is sent without sorting or rewriting.
- `reviewed_robots_404_body_v1` accepts only a direct HTTP 404 with completed transport, verified
  TLS, exact URL, no redirects, allowed media type and the exact reviewed error-body hash. It is
  available only for the same-origin `/robots.txt` access policy, never a rights policy or document.

The source-check ledger still records the 404 as a failed HTTP request. The policy result separately
records `reviewed_missing_robots_404`; no robots rules or successful fetch are fabricated. The
retained Big5 error HTML is hashed as raw bytes, not decoded as UTF-8 or parsed as robots. A new
200 robots response requires review too. 401, 403, 410, 429, 5xx, timeouts, TLS errors, redirects,
unreviewed bodies and changed rights policies cannot pass this gate.

[RFC 9309, section 2.3.1.3](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.3) distinguishes
unavailable robots from unreachable servers. This implementation accepts only explicitly reviewed
404s, not the standard's entire unavailable class. Robots compatibility is not a content licence.
The [source-specific review](../review_plans/2026-09-08-ase-k18b-kcg-source-review.json) records an
internal, narrow local-research decision after complete security/privacy-policy review. Municipal
copyright remains; no open licence, raw redistribution or model training is asserted. The separate
BIP access rejection is not bypassed or reclassified. No images, attachments or linked pages are
acquired. The internal review expires October 8 at 20:56:44 UTC without automatic renewal.

## Real observations and handoff

The policy-only probe retained three responses: one exact robots 404 and two HTTP-200 policy
pages. Two later three-request captures each rechecked the bound robots and security-policy
responses before fetching the exact article. Across this workflow there were nine direct requests,
including two article requests; browser research reads are not part of that direct-request ledger.

The first article retrieval completed at `2026-09-08T20:58:37.042730Z`. It retained 106,582 bytes,
SHA-256 `34c23c7e387e8f7e3b25b99939839b84cb68bbf52399f7763a003202a0234380`.
The second retrieval at `2026-09-08T21:01:39.070580Z` was byte-identical. This short repeat verifies
the acquisition/comparison loop, not monitoring longevity, completeness or detection accuracy.

The [text adjudication](../review_plans/2026-09-08-ase-k18b-source-text-adjudication.json) binds
the full opening paragraph and separate publication/CMS-date spans. It identifies a historical
groundbreaking and a Q1 2028 **building-completion** target. Those are not production, qualification
or usable-capacity assertions. K18B remains a source-native project, separate from the campus
aggregate, K28 and Nanzih Third Park. CMS/footer dates are not physical event dates.

The separate source-review queue has three events, two verified captures and one handed-off
candidate. It has zero pending text reviews; this queue disposition alone accepts no core claims. Restoring
its exported history reproduced all three event cutoffs plus the instant before first admission.
Nothing becomes system knowledge at the article's older publication date.
The subsequent [separate core admission](ase_k18b_core_admission.md) now retains the two reviewed
milestone statements in a new schema-7 snapshot while preserving the source queue and this capture.

Local artifacts under this isolated checkout:

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/2026-09-08-kcg-policy-probe-v1/manifest.json` | `9cc57bd0ca532c3db710d29a7b475c31e62d502fcf82c9d849a5ff99ac83c416` |
| `artifacts/2026-09-08-ase-k18b-kcg-capture-v1/manifest.json` | `5f1ffe2c3a6c7b4364f51f996cb17cf2ccfd3403166d7ddf5a3eed5a290e61b4` |
| `artifacts/2026-09-08-ase-k18b-kcg-capture-v2/manifest.json` | `47bc3725a9d382560850512e01a515c000d327819a2d0a99d189bc6a92a60535` |
| `artifacts/2026-09-08-ase-k18b-source-events-v1.json` | `86dcc3fd7ad293ce306ebb9ac0c8ca7ab7ba50bfe6e232c2fc4be0f967367aff` |
| `artifacts/2026-09-08-k18b-acquisition-independent-check-v1.json` | `3b63aae3a65c7d189d50bae29fb8bc5044757f4dfa53182605d876eff431ac03` |

Replay the retained capture without networking:

```sh
python3 scripts/capture_curated_sources.py verify \
  --run artifacts/2026-09-08-ase-k18b-kcg-capture-v2
```

Fresh captures use `acquisition_plans/ase_k18b_kcg_v1.json`, a new output directory and the preceding
`last_successful_checks.json`; the original scheduled checkout cannot run this v2 plan. Do not
copy it into the registered v1 study or silently change that study's code, denominator or windows.

## Verification and next evidence gate

Seven new acquisition tests exercise status, raw-body, transport, TLS, redirect, query, purpose,
rights and offline-replay boundaries. Three existing real v1 capture packets also replay unchanged.
The prior TSMC schema-7 snapshot still reconstructs byte-for-byte using its newly retained
[original producer](frozen_runtime.md#september-8-source-quantity-producer-retention); all 127
runtime files were separately compared with their committed Git blobs.

The full regression run completed 1,550 core tests in 241.484 seconds: 1,549 passed and one
expected optional-payload test skipped. All 12 web tests, syntax checks, `git diff --check`, and
source/wheel package builds passed. The parent checkout, checksum ledger, baseline, analytics
producer and schema-5 database remain unchanged; the retained schema-7 database hash also matches.

The separately reviewed K18B event types are now admitted in the linked core successor, preserving
the historical ceremony date and expected building-completion quarter without inventing a midpoint,
canonical facility identity, physical effective date or calibrated confidence. The NIST importer
remains production-target-only. Broader physical evidence, monitored refresh and measured predictive
improvement remain open.
