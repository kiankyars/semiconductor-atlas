# Satellite and computer-vision pipeline

Status: operating specification; implementation is deferred beyond the v0.1 registry.

## Role and limits

Satellite imagery is one evidence channel in a document- and entity-resolution system. It is useful
for dating external construction, measuring visible geometry, and detecting site-level change. It
cannot by itself establish an owner, operator, customer, process node, packaging technology, tool
installation inside a building, yield, qualification, economically usable capacity, or wafer starts.

Computer vision produces dated source-level observations and review candidates. It never writes a
canonical lifecycle state or capacity estimate directly.

## Tier 1: global open-data proposals

Run season-matched optical and orbit-consistent radar comparisons around known sites and in
prioritized industrial cells. A later global scan may be added only after geography-held-out recall
and cost are measured.

Initial inputs are:

- Sentinel-2 surface reflectance for vegetation loss, bare-soil and impervious gain, roof emergence,
  water and construction context;
- Sentinel-1 radar for cloud-prone, tropical, and winter regions, normalized by orbit, incidence
  angle, terrain, and acquisition mode; and
- Landsat for longer historical context and coarse thermal corroboration.

The pipeline records catalog item and acquisition IDs rather than treating a web-map screenshot as
evidence. It masks clouds, cloud shadow, snow, and invalid pixels; co-registers images; matches
season and solar geometry where practical; and retains both deterministic change features and a
learned temporal model. The deterministic path makes drift and missed detections auditable.

Proposal classes include:

- land clearing, grading, roads, drainage, and foundations;
- shell and roof emergence or expansion;
- central utility, cooling-tower, water-treatment, wastewater, substation, and generation work;
- tank, gas, chemical, warehouse, and equipment-yard changes;
- parking, traffic, temporary construction, and laydown activity; and
- demolition, long stall, or repurposing.

Open imagery resolution is usually insufficient for production-equipment identification. A roof or
utility-yard change does not prove a semiconductor facility.

## Tier 2: candidate ranking

Rank, rather than hard-filter, proposals using independent context:

- known-site expansion buffers and industrial zoning;
- permit, environmental, subsidy, procurement, contractor, and company evidence;
- transmission, substations, water, wastewater, gas, rail, port, and highway context;
- repeated large cleanroom-compatible shells and central-utility layout;
- temporal construction velocity and consistency; and
- novelty and expected information gain.

Power, water, or industrial-land proximity may raise priority but cannot be mandatory. Brownfield,
interior, compact, or urban projects can lack a visible greenfield signature.

Hard negatives include data centres, battery and solar factories, display plants, pharmaceutical
cleanrooms, warehouses, cold storage, steel and chemical plants, power stations, water-treatment
plants, hospitals, airports, greenhouses, and ordinary industrial expansions.

## Tier 3: licensed high-resolution confirmation

Commercial imagery is disabled until a contract covers automated analysis, model training, derived
coordinates and features, retention, contractors, public release, and model weights. Authorized
imagery is stored in a separate rights-controlled bucket.

For approved candidates, high-resolution analysis may segment and track:

- building shell, roof, cleanroom-support, and central-utility footprints;
- cooling towers, chillers, water and wastewater systems;
- substations, transformers, transmission feeds, generation, and storage;
- bulk-gas and chemical storage areas at a non-sensitive aggregate level;
- construction equipment, laydown, parking, and vehicle activity; and
- demolition, reroofing, and internal-fitout proxies visible from outside.

The model must not publish access-control paths, security procedures, exploitable equipment detail,
or personal vehicle traces. Public geometry is coarsened when rights, law, or safety requires it.

## Tier 4: analyst adjudication

The review interface shows before and after imagery, acquisition metadata, change masks,
independent documentary evidence, proposed entity matches, model scores, and known conflicts. A
reviewer can confirm a visible event, reject a hard negative, split or merge a candidate, request
newer imagery, or leave it unresolved.

Every action records the reviewer or model, time, input versions, reason, and output claim. Review
decisions become labelled feedback only after leakage-safe dataset versioning.

## Lifecycle inference

Visible events support project milestones, not capacity bases:

- **site preparation:** sustained clearing, grading, access, and drainage;
- **civil works:** pads, foundations, underground utilities, and structural work;
- **shell:** external walls and roof completion;
- **cleanroom fitout proxy:** shell complete plus sustained utility and service-yard work, supported
  by documentary evidence;
- **utilities ready proxy:** visible utility assets plus authoritative permit or utility evidence;
- **commissioning proxy:** complete external plant plus corroborating inspections, hiring, utility,
  or company evidence; and
- **ramp corroboration:** persistent parking, traffic, light, thermal, utility, or logistics change.

Imagery alone cannot assign `tool_installed`, `qualified`, or `economically_usable` capacity. A
physical-construction capacity estimate may use visible progress only through a documented model
with facility archetype, floor-area, schedule, and uncertainty inputs.

## Production and resource estimation

Imagery-derived quantities are estimates, never measurements unless the sensor directly measures
the stated variable at validated accuracy. Candidate features include building and cleanroom-support
area, cooling and utility-plant geometry, water infrastructure, substation scale, parking occupancy,
and construction velocity.

Night-time lights, thermal imagery, radar coherence, parking, and traffic are correlated proxies.
They can corroborate commissioning or ramp but cannot independently determine utilization, yield,
product mix, process node, or output. Shared substations, reserve capacity, climate, shutdowns,
shift patterns, and unrelated tenants must be modelled explicitly.

All estimates return a named interval and retain feature, prior, model, and calibration versions.
They link to the exact imagery fragments and independent claims used in reconciliation.

## Training and evaluation

Splits are by site, geography, operator, climate, facility activity, construction vintage, and time.
Neighboring tiles or dates from one site must not span train and test. Maintain gold sets for:

1. construction timelines across clear, cloudy, snowy, tropical, desert, and dense-urban regions;
2. semiconductor-site identity with explicit hard negatives;
3. visible milestone dates and pause, cancellation, redesign, and repurposing cases; and
4. resource or capacity ground truth from authoritative records, held out from model inputs.

Primary metrics are proposal recall and precision, false proposals per area, median and P90 detection
lag, change IoU or F1, object average precision, identity Brier score and calibration error,
lifecycle macro-F1, event-date error, numeric absolute log error and bias, and empirical interval
coverage. Report each by geography, facility activity, site size, stage, and sensor availability.

Random neighboring-tile splits, post-event document leakage, and evaluating only known successful
projects are prohibited. Model promotion requires a fixed blind set and a documented regression
budget.

## Provenance, rights, and storage

Each imagery observation records provider, collection, product and acquisition ID, timestamp,
footprint, bands or polarization, processing level, resolution or pixel spacing, cloud/quality mask,
license, attribution, access class, source hash, processing graph, code and environment version,
model artifact, and derived-artifact hashes.

Process cloud-optimized open imagery in place when permitted. Store compact composites, change maps,
features, model outputs, and hashes rather than copying global archives without need. Retain enough
input identifiers and configuration to replay a result. Restricted imagery and derived sensitive
artifacts never enter a public release bundle.

The source registry controls activation. Sentinel, Landsat, and every commercial or third-party
collection retain their own rights and attribution; portal terms are not assumed to apply to every
dataset hosted by the portal.

## Operational monitoring

Every scheduled cell or site has a checkpoint with intended cadence, last successful acquisition,
last cloud-free or usable observation, model version, and failure reason. Coverage reports distinguish
"no change detected" from "not observed" and "observation unusable."

Alerts require persistent or independently corroborated change, deterministic fingerprints,
hysteresis, and retraction. A quiet cancellation alert cannot be generated from one missing image or
short-term absence of activity.
