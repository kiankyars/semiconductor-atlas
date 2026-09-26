# Milestone benchmark commands

The [benchmark contract](milestone_benchmark.md) defines source-bound, unfitted timing scenarios.
This command-line interface does not collect evidence, run a predictive model, write core claims,
register an automation, or establish calibration. All five commands require an existing schema-5
database, opened read-only. No real study is registered by installing or testing the commands.

```sh
python3 scripts/milestone_benchmark.py --help
```

| Command | Required inputs besides `--database` | Result |
| --- | --- | --- |
| `freeze-study` | `--specification`, `--output` | Actual-clock, evidence-bound roster and split policy |
| `freeze-vintage` | `--study`, `--predictions`, `--model-artifact`, `--configuration`, `--evidence-cutoff-at`, `--horizon-end`, `--output` | Complete active prediction/abstention set with verified lineage |
| `verify-vintage` | `--vintage` | Compact verification receipt; no output file |
| `review-outcomes` | `--vintage`, `--review`, `--source-manifest`, `--output` | Separately reviewed outcome references, not source bodies |
| `score` | `--vintage`, `--outcomes`, `--source-manifest`, `--output` | Timing diagnostics with explicit missing/unknown/censored denominators |

Study specifications, prediction arrays and model configurations follow the Python API's documented
schemas. `--model-artifact` is an opaque local file, not executable input to this command. Its bytes
are retained but the command does not prove it generated the supplied predictions.

The review JSON has exactly `reviewed_by`, `prior_exposure`, and `outcomes`. It is a complete later
adjudication, not another prediction input. The command verifies the vintage against the database
before admitting this review; it does not certify blinded or independent adjudication.

## Retained local sources

Both outcome commands require a local source manifest. Its shape is:

```json
{
  "format": "semiconductor-atlas-milestone-local-sources-v1",
  "documents": [
    {"sha256": "EXACT_LOWERCASE_64_CHARACTER_SHA256", "path": "/absolute/path/to/retained-source.html"}
  ]
}
```

The hash and path above are placeholders. Each real hash must match the exact bytes of the
explicit absolute local file. Duplicate hashes, missing or extra source bodies, relative paths,
symlinks, wrong hashes and more than 20 MB of combined source bytes are rejected. An empty source
list is valid only when the complete outcome review references no evidence, such as all-unknown
cases. Missing sources never become observed outcomes or successful scores.

Publisher bodies are read for verification and are not embedded in outcome artifacts. Output
excerpts remain local-review material unless separately cleared under the source's recorded
rights; these commands grant no redistribution permission. Avoid publishing the local manifest,
which contains machine-specific paths.

## Failure and preservation behavior

Inputs use bounded regular-file reads, reject duplicate JSON keys, and retain exact canonical
bytes for frozen artifacts. The database is never initialized or migrated. Output paths must be
new and have real, non-symlink parents; existing targets are never overwritten. A successful
command prints only a compact receipt, not full source content. A publication error can leave a
complete artifact requiring verification; do not replace it merely because the command failed.

The synthetic end-to-end CLI test freezes a roster and vintage, verifies it, reviews outcomes and
scores them with the core database byte-unchanged. It also tests existing-output preservation and
rejection of unsafe or inconsistent source inputs. This validates workflow mechanics, not real
manufacturing outcomes or predictive performance.
