# Hazard Objects and the Slot Requirement Matrix

> Fills the two gaps the source framework left open. It defines five task intents, three expertise
> levels and eight user roles, but gives only worked examples for hazard objects
> (`Active fire + Exposure + Evacuation`) and slots
> (`Location, time horizon, target communities, requested output`). This document extrapolates
> those examples into something implementable, and records why each choice was made.
>
> Design constraint: everything here stops at the **data family** level. Choosing a concrete API
> endpoint is the downstream Planning Agent's job.
>
> Machine-readable counterpart: `backend/src/wildfire_agent/taxonomy.py`.

---

## 1. Hazard objects — thirteen, in three tiers

The tiering follows the shape a wildfire question always has: **hazard → receptor → action**. The
framework's own example, `Active fire + Exposure + Evacuation`, takes exactly one from each tier.
That is not a coincidence; it is the structure.

### 1.1 Hazard tier — the hazard itself

| id | Required variables | Candidate dataset/API families |
|---|---|---|
| `active_fire` | fire perimeter, hotspot location, detection time, detection confidence, fire size | official perimeters (IRWIN/WFIGS family), satellite hotspots (VIIRS/MODIS/HMS family) |
| `fire_spread` | rate of spread, direction, fire front position, time step | fire behaviour models (FARSITE/FlamMap family), time-series hotspot differencing |
| `smoke_plume` | plume polygon, density class, PM2.5, observation time | satellite smoke analysis (HMS family), air quality monitoring networks, dispersion models |
| `fire_weather` | wind speed, wind direction, temperature, relative humidity, fire danger index | weather forecast services (NWS family), fire danger rating products |
| `fuel` | fuel model, vegetation type, fuel load, fuel moisture, canopy characteristics | land cover / fuel maps (LANDFIRE family), vegetation index remote sensing |

### 1.2 Exposure tier — who or what is affected

| id | Required variables | Candidate dataset/API families |
|---|---|---|
| `exposure` | population count, building footprints, housing density, WUI boundary | census demographics, building footprints, WUI layers |
| `vulnerability` | age structure, income, no-vehicle households, language isolation, mobility limitation | social vulnerability indices (SVI/CDC family), census demographics |
| `infrastructure` | road network, power lines, communication sites, water facilities | road networks (TIGER family), utility assets, HIFLD family |
| `critical_facility` | hospital / fire / police / school / shelter locations, capacity | POI and facility point data (HIFLD/OSM family) |
| `ecological_asset` | watershed, habitat, protected area, soil erosion risk | protected area boundaries, watershed layers, habitat maps |

### 1.3 Action tier — what people do about it

| id | Required variables | Candidate dataset/API families |
|---|---|---|
| `evacuation` | evacuation zone, route, network accessibility, travel time, shelter | official evacuation orders, road network + routing, accessibility analysis |
| `suppression_resource` | crew/equipment location and count, control lines, water sources, response time | incident resource summaries (ICS/SIT family), water source inventories |
| `mitigation_treatment` | treatment unit boundary, treatment type and year, prescribed burn records, priority | treatment records (NFPORS family), forest management plans |

`hazard_objects` is a list and may span tiers. It never names an API — only the family of data
required.

### 1.4 Coverage in this deployment

Which hazard objects the shipped dataset can actually serve. The authoritative list is
`backend/src/wildfire_agent/planning/capabilities.py`; this is a summary.

| hazard object | Covered? | capability |
|---|---|---|
| `active_fire` | yes | `official_fire_perimeters`, `satellite_hotspots`, `historical_fire_perimeters` |
| everything else | **no** | — |

> **Task 1 never consults this table.** It declares what is needed; the Planning Agent matches
> that against real capability and reports the shortfall rather than inventing an answer around
> it. That separation is what walkthrough scenario 3 was written to check, and it is why the
> registry lives downstream — see `docs/03-planning-agent.md`.

### 1.5 Data families that are not interchangeable

Some hazard objects are served by two families that answer *different questions*. Choosing wrongly
does not degrade the answer, it invalidates it — so `target` cannot be defaulted while one of these
is in play.

| hazard object | Choice | What it costs you |
|---|---|---|
| `active_fire` | Officially confirmed perimeters | Verified by fire agencies, but slow to update and often empty for a given area |
| | Satellite thermal detections | Near real time, but a detection is only a heat signature — possibly an agricultural burn or an industrial source |
| `smoke_plume` | Satellite plume extent | Where smoke is overhead across a wide area, but nothing about ground-level concentration |
| | Ground monitor air quality | What people actually breathe, but only at sparse stations |

Declared on `DataFamilyChoice` in `taxonomy.py` and enforced by
`nodes.enforce_family_disambiguation`. **This deliberately does not rely on the prompt** — see §5.4.

---

## 2. Slots

The first four come from the framework's example; the rest are required by intents it did not
work through.

| slot | Meaning | Example |
|---|---|---|
| `location` | Geographic scope — the only slot needing real geocoding | "Altadena, CA + 25 km buffer" |
| `time_horizon` | Temporal reference or window | "now" / "next 24h" / "2024 fire season" |
| `target` | The receptor or object under analysis | "communities" / "my house" / "roads" |
| `requested_output` | Desired product form | "map" / "ranking" / "report" / "alert" |
| `threshold` | The cut-off that defines "high" or "serious" | "PM2.5 > 35" / "within 5 km" |
| `comparison_basis` | The criterion used to rank or compare | "by exposed population" / "weighted by vulnerability" |
| `intervention` | The intervention being evaluated, plus its baseline | "2023 treatment in area X, versus before" |
| `scenario` | Assumed conditions for a prediction | "current forecast wind" / "wind +20%" |

---

## 3. Intent → slot requirement matrix

**This matrix is the engine behind Ambiguity Resolution.** `is_blocking` is not guesswork: the
matrix supplies a baseline and the model adjusts it within stated rules.

Legend: **B** = blocking (must be settled) · D = defaultable (fall back, but **record it in
`assumptions`**) · O = optional · — = not applicable

| slot | observation | assessment | prediction | decision_support | evaluation_adaptation |
|---|---|---|---|---|---|
| `location` | **B** | **B** | **B** | **B** | **B** |
| `time_horizon` | D → `now` | D → `now` | **B** | D → `now` | **B** |
| `target` | D → all | **B** | D → all | **B** | **B** |
| `requested_output` | D → `map` | D → `map` | D → `map` | D → `ranking + map` | D → `report + map` |
| `threshold` | — | O | — | O | O |
| `comparison_basis` | — | — | — | **B** | — |
| `intervention` | — | — | — | — | **B** |
| `scenario` | — | — | O | — | — |

Multiple intents take the union at the strictest level: `[assessment, decision_support]` blocks on
`{location, target, comparison_basis}`.

### Three override rules on top of the matrix

1. **Promote: semantic ambiguity outweighs a default.** `target` is D under observation, but when
   the hazard objects carry non-interchangeable data families (§1.5), it becomes B. This one is
   enforced in code, not requested of the model.
2. **Promote: the spatial phrase cannot be grounded.** `location` is always B, but whether it
   *interrupts* depends on geocoding. "near my house" must be asked. "near Altadena" resolves, so
   the buffer is drawn on the map for confirmation — which is not an interruption.
3. **Demote: the role already implies the answer.** A `resident_general_public` asking about
   `target` implies "around where I am". That is the framework's "ask only when role materially
   changes the analysis", made operational.

### Blocking is not the same as asking

`is_blocking` says the slot matters. Whether to *interrupt* additionally depends on whether the gap
has a cheaper remedy. Encoded as `Slot.needs_clarification`, which `SpatialSlot` overrides: once
grounding succeeds the slot counts as satisfied, because dragging a circle beats reading a list of
options.

### Ask once

Collect every slot with `is_blocking and not is_filled` and put them in **one** batch. Phrasing
follows expertise:

| expertise | How to ask |
|---|---|
| `general` | 2–3 concrete options, never open-ended, each with a plain-language consequence |
| `practitioner` | Domain terms, options, and how each affects the conclusion |
| `expert` | Parameters, resolution, data families; open-ended is fine |

---

## 4. Where `capabilities` belongs

The framework's MVP chain reads `... + slots → capabilities → workflow` without defining
`capabilities`. **Our reading: it is the Planning Agent's first step, not part of Task 1.** It is
the executable form of the coverage table in §1.4:

```
Task 1 emits a contract (hazard_objects + slots)
  → the Planning Agent matches hazard_objects against capabilities (what data and operators exist)
  → what matches becomes a workflow; what does not is reported honestly as out of reach
```

Rationale and the cost of reversing this: §5.3.

---

## 5. Decisions taken on the open questions

Recorded on 2026-08-09, decided from the framework documents rather than left waiting. Each states
its grounds and what reversing it would cost.

### 5.1 Thirteen hazard objects; `vulnerability` stays separate from `exposure`

The framework's Assessment definition lists "hazard, **exposure**, **vulnerability**, impact, or
risk" as peers — it already treats them as different things. It also holds up in practice: of the
three ranking bases in walkthrough scenario 2, one needs exposure alone and another needs both.
Merging them makes that distinction inexpressible.

**Reversal cost:** low. Delete one `HazardObject` entry.

### 5.2 Defaults kept, but `requested_output` now varies by intent

`time_horizon → now` is grounded: of the framework's five intent examples only prediction carries
its own time window. `requested_output → map` is grounded in the stated expectation that ~95% of
outputs are geovisualisations.

One inconsistency was found and fixed: a single global `map` default is wrong for decision support,
which asks what to prioritise — a picture alone does not answer that. Now resolved per intent by
`taxonomy.slot_default(slot, intents)`.

**Reversal cost:** low. One dictionary, `INTENT_SLOT_DEFAULT_OVERRIDES`.

### 5.3 `capabilities` belongs to the Planning Agent

Three independent grounds:

1. The task brief states plainly that the compiler "does not decide which datasets or tools should
   be used". A capability inventory is exactly that.
2. Capabilities describe **what the system has**; the contract describes **what the user asked**.
   The former changes whenever a data source is added or removed. Putting it in the contract makes
   our interface with the downstream agent wobble every time the data layer changes — and the
   contract is supposed to be the stable part.
3. In the chain `slots → capabilities → workflow`, the workflow is unambiguously the Planning
   Agent's output. Capabilities sit next to it.

What Task 1 owes in return: `hazard_objects` has to be granular enough for that match to be
possible. That is why §1 lists required variables and candidate families rather than bare names.

**Reversal cost:** medium, and not recommended. Task 1 would have to carry a runtime capability
registry, which puts it back across the boundary the brief draws.

### 5.4 The data-family rule is enforced in code, not requested in the prompt

Originally rule 1 of §3 was an instruction in the Task Compiler prompt. gpt-4.1-mini read it and
defaulted `target` anyway, silently choosing between confirmed perimeters and satellite detections
and removing the single most important moment in the workflow.

A domain fact that must hold on every run does not belong in a prompt. It now lives on
`DataFamilyChoice` and is enforced by `nodes.enforce_family_disambiguation`; the model is left to
phrase the question, not to remember that a question is required. Regression tests:
`backend/tests/test_disambiguation.py`.

---

## 6. Still needed from the product side

Neither blocks implementation:

- Competition scoring criteria and deadline — affects prioritisation, not the schema.
- Which hazard object to cover next. Only `active_fire` has data today, so assessment and
  decision-support questions currently report as unavailable — correct, but limiting.
