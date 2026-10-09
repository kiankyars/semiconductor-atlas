# Open Data Center Atlas — source data and tools

This directory holds the curated records, open layers and the dependency-free tooling that builds
the public site at <https://kiankyars.github.io/semiconductor-atlas/>.

| Path | Contents |
| --- | --- |
| [`projects/`](projects/) | One JSON file per data center build. This is the source of truth. |
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

`build` writes Parquet files too when `pyarrow` is installed (the Pages workflow installs it).
Builds are deterministic: the same commit and base URL produce byte-identical files.

## Publishing

`.github/workflows/pages.yml` validates the records, runs the tests, builds the site with the
repository's Pages URL, and deploys it to GitHub Pages on every push to `main`. CI runs the same
validation and a trial build on every pull request.
