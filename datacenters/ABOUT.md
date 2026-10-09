# About

The Open Data Center Atlas is an open dataset of large data center builds: hyperscale and AI
campuses, conversions of industrial and crypto-mining sites, and major colocation developments.
It exists so that journalists, researchers, planners, utilities and the public can see what is
being built, by whom, for whom, and at what scale, and check every claim against its source.

## Principles

- **Every value is sourced.** Each number, date, role and status links to the public source that
  reported it, with its basis, scope and date.
- **Unknown stays unknown.** Missing values are blank, never zero or guessed.
- **Open by default.** No account, no key, no paywall. Static files with stable URLs, in the
  formats people actually use.
- **Corrections in public.** Records live as JSON files in a public repository; every change is
  reviewable and its history is permanent.

## License

Curated records are released under [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
You may share and adapt them for any purpose, including commercially, if you give credit. The
OpenStreetMap layer is © OpenStreetMap contributors under the [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/);
the Wikidata layer is CC0. Cited sources keep their own rights.

## How to cite

> Open Data Center Atlas (2026). Data center builds, with every number sourced.
> https://kiankyars.github.io/semiconductor-atlas/ — data as of the date shown on the site.

```bibtex
@misc{open_data_center_atlas,
  title  = {Open Data Center Atlas: data center builds, with every number sourced},
  author = {Kyars, Kian},
  year   = {2026},
  url    = {https://kiankyars.github.io/semiconductor-atlas/},
  note   = {Dataset, CC BY 4.0}
}
```

Each project page also has a ready-made citation for that record. A `CITATION.cff` file at the
repository root lets GitHub generate citations too.

## Repository layout

| Path | Contents |
| --- | --- |
| `datacenters/projects/` | One JSON file per curated build, the source of truth. |
| `datacenters/layers/` | Uncurated OpenStreetMap and Wikidata layers with retrieval manifests. |
| `datacenters/dcatlas/` | Validator, exporters and the static site generator (Python, no dependencies). |
| `datacenters/site/assets/` | Stylesheet and the dependency-free map and explorer scripts. |
| `datacenters/schema/` | JSON Schema for a record. |

The site is rebuilt and published to GitHub Pages by a workflow on every change to `main`.
