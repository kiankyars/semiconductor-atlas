# Bounded source-native assertion inventory

This additive parser extracts a small set of manufacturing-related statements from three
already retained documents. It makes their source-native subject, predicate, literal wording and
exact evidence bytes inspectable. It does not turn those statements into manufacturing facts.

The implementation is separate from the [registered calendar-target detector](prospective_source_targets.md).
The September 8–15 UTC study, its four supported calendar routes, code pins and scoring policy
remain unchanged. This work uses exposed development evidence and invented regression fixtures;
it is not a prospective evaluation or a new registration.

## Supported statements

| Exact document route | Extracted formulations | Interpretation limit |
| --- | --- | --- |
| Amkor Peoria groundbreaking article | Reported ceremony; undated construction wording | No inferred ceremony year, project phase allocation, completion, HVM or capacity |
| Chandler Advanced Manufacturing page, Intel accordion | Reported Fab 52 opening; reported production | Undated source wording, not a newly observed opening or proof of HVM |
| Singapore MTI Micron HBM speech | Opening and closing references to groundbreaking; undated completion aspiration | Two formulations can refer to one ceremony; aspiration is neither a dated schedule nor attained completion |

The exact URL roster is in `semiconductor_atlas/source_assertion_detector.py`. Aliases, query
variants and other documents are not automatically accepted. The MTI speech's existing NAND
operations and market projections are not allocated to the new HBM project. Intel's multi-fab
investment and two-campus employment are not allocated to Fab 52.

## Extraction and comparison contract

`extract(url, raw_bytes)` is a pure function. Each accepted formulation includes a source-native
subject, typed predicate, stable formulation key, normalized literal and UTF-8 byte ranges with
SHA-256 hashes for its complete paragraph and identifying context. `event_date` and
`calendar_target` remain null. Document-level publication metadata stays in the evidence context.

`analyze(url, before_bytes, after_bytes)` returns:

- `extraction_only` when a valid first observation has no recorded predecessor;
- `no_candidate` when recognized assertions and their bound context agree;
- `revision_candidate` when aligned, recognized literal wording differs; or
- `abstain` when extraction, alignment or context coverage is insufficient.

Literal differences can be editorial. For example, the supported construction wording variants
do not establish different physical stages. A candidate is a source-text review item, not an
accepted claim, material-change judgment or deliverable alert. An unfamiliar completion,
negation, target, qualification or changed context requires review rather than silently becoming
a quiet comparison. Unsupported wording is not automatically assigned a lifecycle state.

The parser binds the selected article/section context, rejects ambiguous identities and duplicate
assertions, and does not use a whole-body hash allowlist as its extraction grammar. Recognized
inline markup changes can retain the same normalized assertion. Navigation and unrelated employer
sections are excluded explicitly. Script, style, template, hidden/inert content and closed native
disclosures cannot supply assertions. The Chandler accordion is interpreted statically; browser
visibility is not verified. External stylesheets and all possible linguistic constructions are
not modeled.

Coverage remains partial even when extraction succeeds: `source_unparsed` is true and
`whole_document_complete` is false. Equal unfamiliar manufacturing wording does not bypass the
parser. Nevertheless, `no_candidate` describes only this recognized, context-bound extraction;
it is not whole-document recall, proof of unchanged physical state or complete scoped assertion
coverage. Failure to find an assertion is not evidence of cancellation or capacity loss.

## Full retained-population accounting

`scripts/inventory_source_assertions.py` verifies the complete frozen source-observation census
before analysis. Every in-window document opportunity stays in the report, including unsupported
URLs, blocked or failed checks, first observations and incomplete polling intents.

Paired comparisons use the collector's actual retained successful predecessor, never a convenient
adjacent document or a failed partial response. First observations cannot receive paired verdicts.
The inventory rechecks all frozen source files, code, census bytes and the original queue-event
prefix before returning and again before output. Genuine later queue appends are allowed without
rewriting the old inventory. Output is new-only, size-bounded and cannot be placed within retained
source/polling directories or through symlink parents. There are no network requests, labels,
queue dispositions, canonical claims or schedule changes.

The retained September 7 development census has 22 opportunities across eight exact URLs:

- Seven opportunities use these three routes: five Amkor pairs, one Chandler first observation
  and one MTI first observation.
- All five Amkor pairs contain the same before/after body hash and return `no_candidate`.
  They are five checks of one exact pair, not five independent manufacturing events.
- The two first observations return `extraction_only` and supply no change-detection evidence.
- The remaining 15 opportunities stay `unsupported_exact_url` in this separate inventory,
  including one policy-blocked check whose original status is retained.
- The three distinct source bodies yield seven formulations, including the two references to
  the same Micron ceremony. No real revision candidate occurs in this retained sample.

The report is local-only:

```sh
python3 scripts/inventory_source_assertions.py \
  --frozen artifacts/2026-09-07-curated-observation-population-v1-r3-frozen.json \
  --reference-root . \
  --output artifacts/NEW-source-assertion-inventory.json
```

This historical replay requires the retained sources and original queue prefix; a clean clone
does not contain them. The existing report is
`artifacts/2026-09-07-source-assertions-v1-report.json`, 186,348 bytes, SHA-256
`cf9da33b62232703bc1c58319d7878df7c89a4098ea346701a1f3d51f7545686`.
Do not overwrite it or redistribute the retained publisher documents.

## Remaining evaluation gate

The regression fixtures test deterministic parsing, uncertainty boundaries, byte evidence,
source failures, actual-predecessor selection and replay integrity. They are not outcome labels
or a performance population. Precision, recall, false-positive burden, detection lag, physical
accuracy and forecast calibration remain unscored.

The next evidence gate is still a complete future observation population, predictions sealed
before separate outcome review, meaningful positive cases and measured review workload. This
parser is not part of that registered study; any future integration needs an explicit versioned
protocol before its evaluation window. Neither this inventory nor the existing registration
establishes useful manufacturing-change detection or calibrated forecasting yet.
