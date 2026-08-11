# Generated geospatial interface

Version 0.1 includes a generated, dependency-free, read-only interface over a pinned release bundle;
the database is never queried directly from the browser. `generate_atlas.py` verifies the release
GeoJSON against its manifest, embeds it safely in `atlas.html`, and updates the manifest atomically.
Only explicit WGS84 geometry claims appear on the map. EPA FRS raw NAD83 scalars therefore remain
unmapped until a separately versioned CRS transform exists.

The current interface exposes the release's materialized entities and source attribution. A later
interactive version should show sites, facilities, production units, and projects without conflating
them and provide controls for both world time and database-knowledge time. Filters should cover
geography, activity, process or packaging capability, wafer size, lifecycle, and the five capacity
bases. Selecting a field should reveal its claim kind, valid and recorded times, confidence or
interval, evidence fragments, contradictions, and revision history. Facts, estimates, system
forecasts, scenarios, and alerts must be visually distinct.

The build input is deterministic GeoJSON or equivalent release data plus a manifest. Generated assets
must be stable, content-hashed, and safe against script injection. Any external basemap or runtime
dependency must be named, pinned, and allowed by its terms; an interface is not "standalone" if it
requires an undocumented CDN.

Public builds retain required source attribution and license notices, exclude restricted excerpts and
imagery, omit personal data and sensitive infrastructure detail, and coarsen geometry where required.
The UI must display coverage age and blind spots so an empty area is not mistaken for proof that no
facility or project exists.
