# PMT outcome access addendum — September 9, 2026

Recorded at 04:35 UTC after review of retained request records and the current TI
terms. This adds actual access decisions to the earlier frozen 45-group research
ledger; it does not replace that ledger or admit its unretained outcome leads.

## Texas Instruments: automated acquisition not approved

The [current TI terms](https://www.ti.com/legal/terms-conditions/terms-of-use.html),
section 4, prohibit automated scraping/extraction. The separate personal and
noncommercial-use allowance does not remove that restriction. The main reviewer
re-read the terms through the web tool; no exact policy body is retained here.
No production-release capture, alternative TI host or rehost acquisition was
performed. Production leads remain research leads, not admitted primary outcomes.
The original two-fab Sherman group must not be declared complete from an SM1-only
observation even if a permitted evidence route later becomes available.

## Microchip: one permitted IR-policy response, then a main-policy denial

Both probes used an identified research user agent, bounded response sizes and
timeouts, no redirects, no cookies and no retries. Their exact retained ledgers
distinguish HTTP success from a policy decision.

| Request | Actual UTC request interval | Result |
| --- | --- | --- |
| `https://ir.microchip.com/robots.txt` | 04:17:27.411166–04:17:27.622196 | HTTP 200; 84 bytes; body disallows `/form-submit` only |
| `https://www.microchip.com/robots.txt` | 04:18:14.159947–04:18:14.457505 | HTTP 403; 383 bytes; stop |

The first response declared HTML content type but contained plain robots text.
Its SHA-256 is `9118fee9663583b50adf049c1a2a276fff8ac0347512ec1fff128e9b380e2a84`.
The denial body SHA-256 is
`3b064b4f92354131784a59c1ee3c00d755123ac94fdcb9e73ac7fe358ee244a8`.
Both transport records report zero redirects and successful TLS verification.

The main-origin copyright policy and IR disclaimer were planned but not captured
after the first main-origin denial. No filing body was acquired, no SEC route
was attempted, and no filing claim was admitted. Previously web-read copyright
guidance is not an exact retained policy graph. The Gresham and Colorado Springs
expansion-pause leads remain unadmitted; do not confuse them with the separate
Tempe closure or infer a plant closure from an expansion pause.

Retained manifests, with file-level hashes and actual request records:

- `artifacts/2026-09-09-microchip-ir-policy-probe-v1/manifest.json`, SHA-256
  `55b11c8884ad8f15c66c60093642ec6aeb8f56e839a89e7917478ed8391f23f7`.
- `artifacts/2026-09-09-microchip-rights-policy-probe-v1/manifest.json`, SHA-256
  `1bed494b79c3c5cfe08d41cc21cef18a7c01ca431d4e58dcc5b719aa607ac862`.

## NIST: the exact three-source acquisition passed

The Bosch, Coherent and Powerex 2026 NIST releases were retained under their
separate fixed-URL review. Both pinned policy checks passed before source
requests. All three source bodies returned HTTP 200 without redirects, with
successful TLS verification. The complete packet replays offline through
`capture_curated_sources.py verify`; its manifest SHA-256 is
`05a33260bde85bf62efe4c3e8c5bd81430ba3c9c62f140ad718785cc5064579a`.

This is an access-cleared subset, not representative outcome sampling. Publisher
access failure is not project failure, cancellation, delay or nonoccurrence.
No permission request, raw publication, training use, database write or scheduler
change follows from this addendum.
