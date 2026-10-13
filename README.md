# Semiconductor Atlas

Explore semiconductor fabs, packaging facilities, and manufacturing projects, with the sources
behind each statement.

**[Browse the atlas](https://kiankyars.github.io/semiconductor-atlas/)** ·
[Download the public data](web/data/2026-09-07/) ·
[Data contract](docs/data_contract.md)

The public dataset covers seven facilities and projects from TSMC, Samsung, Intel, Micron,
SK hynix, Amkor, and ASE. Its latest included review is September 7, 2026. Each record links
reported capabilities, milestones, and capacity to dated source evidence. Coverage is selective;
unknown values stay blank. The site uses a searchable list because these records do not yet have
verified map coordinates.

Data centers have moved to **[Data Center Atlas](https://kiankyars.github.io/datacenter-atlas/)**,
maintained in [its own repository](https://github.com/kiankyars/datacenter-atlas).

## Use the data

The [public bundle](web/data/2026-09-07/) includes:

| File | Contents |
| --- | --- |
| [facilities.csv](web/data/2026-09-07/facilities.csv) | Facility and project records |
| [claims.jsonl](web/data/2026-09-07/claims.jsonl) | Individual statements and their evidence links |
| [evidence.jsonl](web/data/2026-09-07/evidence.jsonl) | Source references and evidence locators |
| [capacity.csv](web/data/2026-09-07/capacity.csv) | Capacity with its unit, scope, and reported basis |
| [atlas.geojson](web/data/2026-09-07/atlas.geojson) | Records in GeoJSON, with null geometry where location is unverified |
| [ATTRIBUTION.md](web/data/2026-09-07/ATTRIBUTION.md) | Source attribution and reuse terms |
| [manifest.json](web/data/2026-09-07/manifest.json) | File sizes and hashes for verification |

Capacity keeps five distinct bases: announced, physical construction, tool installed, qualified,
and economically usable. A groundbreaking, equipment delivery, or stated opening date does not
establish a numeric capacity or operating output. Records preserve the source's unit, project
scope, and date precision.

## Run locally

Python 3.11 or newer is required. The package uses the standard library.

```sh
python3 -m pip install --editable .
python3 web/build_site.py --output /tmp/semiconductor-atlas-site --base-url http://localhost:8000/
python3 -m http.server --directory /tmp/semiconductor-atlas-site 8000
```

Open <http://localhost:8000/>. The site builds from the tracked public bundle without access to
private local evidence, a database, or the network. See [web/README.md](web/README.md) for the
publishing workflow.

For local research, the [evidence viewer](docs/evidence_viewer.md) searches an existing database
and shows each record's statements, evidence links, and history. It opens the database read-only.
The [CLI and source workflows](DEVELOPMENT.md) cover ingestion, review, export, and reproduction.
Historical snapshot bodies and research databases stay in the local locations listed in
[LOCAL_DATA.md](LOCAL_DATA.md).

## Research and corrections

Keep source-native facility and project identities separate until their relationship is supported.
Use dated primary sources, preserve the exact project scope, and distinguish a publisher's target
from reported completion or production. A new retrieval date does not make an old statement
current. Review existing records for corrections as well as adding missing facilities.

The core records completed milestones, source quantities and rates, project-scoped capacity, and
changes over time. Evidence dates and the time a statement entered the database remain separate.
Research preserved locally can be reviewed without changing the public dataset.

| Workflow | Guide |
| --- | --- |
| Sources and acquisition | [Source registry](docs/source_registry.md), [curated acquisition](docs/curated_acquisition.md) |
| Review source statements | [Review workflow](docs/source_statement_review.md), [project targets](docs/source_project_targets.md) |
| Completed milestones | [Milestone records](docs/completed_milestone_core.md) |
| Quantities, rates, and capacity scope | [Source quantities](docs/source_quantities.md), [source rates](docs/source_rates.md), [capacity scope](docs/capacity_scope_review.md) |
| Inspect evidence and changes | [Search](docs/evidence_search.md), [record detail](docs/evidence_dossier.md), [changes](docs/evidence_changes.md) |
| Historical reproduction | [Frozen runtimes](docs/frozen_runtime.md), [repository consolidation](docs/repository_consolidation.md) |

## Checks

```sh
python3 -m unittest discover -s tests -q
python3 -m unittest discover -s web/tests -q
node --test web/tests/evidence_viewer_state.test.cjs
python3 -m compileall -q -x 'web/data/' semiconductor_atlas scripts tests web
```

CI runs these checks and builds the public site from a clean checkout. Changes merged into
`main` publish the semiconductor site through GitHub Pages. Public source attribution and
original review dates travel with the data; publisher bodies remain local.
