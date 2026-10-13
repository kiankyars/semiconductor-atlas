# Data Center Atlas archive

Data Center Atlas is maintained in [datacenter-atlas](https://github.com/kiankyars/datacenter-atlas)
and published at <https://kiankyars.github.io/datacenter-atlas/>. Submit additions and corrections
there. This directory retains the original records and tooling for reproducibility; it is no
longer the publishing source for this repository.

| Path | Contents |
| --- | --- |
| [`projects/`](projects/) | Archived original records, one JSON file per data center build. |
| [`layers/`](layers/) | Uncurated OpenStreetMap (ODbL) and Wikidata (CC0) layers with retrieval manifests. |
| [`schema/project.schema.json`](schema/project.schema.json) | JSON Schema generated from the contract. |
| [`dcatlas/`](dcatlas/) | Validator, exporters and static site generator. Python 3.11+, standard library only. |
| [`site/assets/`](site/assets/) | Stylesheet and the map/explorer scripts (no external dependencies). |
| [`vendor/`](vendor/) | Natural Earth basemap and country polygons (public domain), pre-projected. |
| [`tests/`](tests/) | Unit and end-to-end build tests with fictional fixtures. |

Documentation: [methodology](METHODOLOGY.md), [contributing](CONTRIBUTING.md),
[about and citation](ABOUT.md), [changelog](CHANGELOG.md), [data license](LICENSE-DATA.txt).

## Commands

Run from this directory:

```sh
python3 -m dcatlas validate                    # check every record against the contract
python3 -m unittest discover -s tests -t .     # run the tests
python3 -m dcatlas build --output /tmp/site    # build the site and every data format
python3 -m http.server -d /tmp/site 8000       # preview at http://localhost:8000/
python3 -m dcatlas fetch-layer osm             # refresh the OpenStreetMap layer
python3 -m dcatlas fetch-layer wikidata        # refresh the Wikidata layer
python3 -m dcatlas schema                      # regenerate the JSON Schema after contract edits
```

`build` writes Parquet files too when `pyarrow` is installed.
Builds are deterministic: the same commit, base URL and Python/SQLite versions produce
byte-identical files (the SQLite file header records the library version).

## Publishing

This repository's Pages workflow publishes Semiconductor Atlas. The build commands above remain
available to reproduce the archived data-center site locally. Current publishing and data updates
belong in the dedicated Data Center Atlas repository.
