# Planning Agent and the Showcase Dataset

> The stage downstream of the Analysis Contract. It exists to prove the contract
> is worth producing: everything here is driven by the contract and by nothing
> else, and the evidence family the contract settled is what makes the layer
> selection unambiguous.

---

## 1. Why it lives outside Task 1

The task brief is explicit that the User Goal Agent "does not decide which
datasets or tools should be used". So fetching data is not bolted into Task 1 —
it is a separate pair of graph nodes that run *after* the contract is finished:

```
… → analysis_contract ──(ready?)──→ planning → execution → END
                       └─(not ready)────────────────────→ END
```

> **Read §5 before relying on this.** The graph above is accurate, but it is no
> longer the whole downstream picture. A second path was built alongside it and
> now carries most of what the system does. This section describes the registry
> path; §5 describes both and says what governs each.

Two consequences worth stating:

- **Task 1 still never picks a dataset.** The boundary is a node edge, and the
  contract is the only thing that crosses it.
- **An incomplete contract does not get analysed.** Running analysis on top of
  unresolved ambiguity is exactly the error this system exists to prevent, so
  the graph routes straight to the end and the gaps stay recorded.

## 2. The LLM proposes, the registry validates

Layer selection is a genuine judgement call, so a model makes it. It is also a
place where a plausible mistake is expensive, so the result is checked. Three
rules are enforced in code, not requested in the prompt:

| Rule | Why |
|---|---|
| A `capability_id` outside the catalogue is dropped | A hallucinated source must never reach the map |
| A layer whose family contradicts the contract's `target` is dropped | `target` records a decision already taken and disclosed; a proposal does not get to quietly reverse it |
| A layer whose temporality contradicts the request is dropped | "Since 2000" answered with today's perimeter is a different question |

And two floors are added back:

- Any data family the user explicitly chose is drawn even if the proposal
  omitted it — dropping one silently changes the answer they asked for.
- Any declared hazard object with no capability at all is reported as an
  `UnmetNeed` rather than substituted with something that looks similar.

If the model is unreachable, `deterministic_plan` produces the same shape from a
registry lookup and the demo continues.

## 3. Showcase area: Altadena, California

An unincorporated Los Angeles County community of roughly 42,000 people across
about 22 km², most of which burned in the **Eaton Fire of January 2025**. Chosen
over "Los Angeles" because it fits on one screen and because the event is
recent, severe, and unusually well documented by public agencies.

### Layers

All three are public, need no API key, and are snapshotted into
`backend/data/altadena/` by `scripts/fetch_showcase_data.py`.

| Layer | Source | Features | Family / temporality |
|---|---|---|---|
| Officially confirmed fire perimeters | LA County Dept. of Public Works, Eaton Fire perimeter as of 2025-01-21 | 20 polygons | `official_fire_perimeters` / snapshot |
| Satellite thermal detections | NOAA NESDIS Hazard Mapping System, daily archive, 8–9 Jan 2025 | 1,300 points | `satellite_hotspots` / snapshot |
| Historical fire perimeters | National Interagency Fire Center, InteragencyFirePerimeterHistory, ≥ 2000 | 9 polygons | `official_fire_perimeters` / historical |

**Why these three.** Together they put the central claim of the whole system on
a map for the first time. The confirmed perimeter is one polygon set an agency
verified. The satellite layer is over a thousand thermal detections, some of
them coarse GOES pixels, plainly spilling outside that perimeter. Choosing
between them does not change the resolution of the answer — it changes the
answer. That is why the choice is made by stated policy and disclosed rather
than left to a model's judgement — `docs/01` §5.5.

The historical layer carries the same family tag as the current perimeters,
because it *is* agency-verified polygons; only `temporality` separates them.
Tagging it otherwise meant "official perimeters, since 2000" resolved to the
January snapshot instead of the archive.

### Honesty rules the snapshot has to keep

- **Provenance travels with the data.** Every file records its source, its
  as-of date, and when it was retrieved; every layer surfaces its caveat in the
  map legend. A snapshot presented without its origin is indistinguishable from
  invented data.
- **The dataset is pinned, and says so.** Every plan that uses the current
  layers carries a note that this is January 2025, not live. Sample questions
  are phrased against the event for the same reason: a question implying "right
  now" answered with month-old data is precisely the quiet mismatch this project
  exists to prevent.
- **Empty is a result.** A scope with no features returns zero and says so. It
  is never padded.
- **Truncation is reported.** Display is capped at 1,500 features per layer, and
  a capped layer is labelled so its count is not mistaken for a total.

### Refreshing the snapshot

```bash
cd backend && uv run python scripts/fetch_showcase_data.py
```

## 4. What is deliberately not here

Scoped to the **registry path**. Much of what this section once listed as absent
has since been built on the renderer path instead — see §5 and `docs/01` §1.4.

- No `evacuation` and no `suppression_resource`, on either path. Those hazard
  objects exist in the taxonomy and are reported as `UnmetNeed` when a contract
  declares them — the honest answer, and what walkthrough scenario 3 was written
  to check. Both paths report it; see §5.
- No `infrastructure` and no `critical_facility`. Both are plausible next steps:
  the taxonomy already names the families, and neither needs a new archive.
- On the registry path specifically, **no analysis beyond spatial clipping**. Its
  three layers are drawn, not interpreted. Derived analysis — burn severity,
  vegetation change, spread behaviour, community intersection with shares —
  exists, but on the renderer path.

## 5. Two downstream paths

Recorded 2026-08-27. §§1–4 describe the registry path as though it were the only
one. It is not, and the difference decides where a given answer's safety comes
from.

```
contract (ready)
   ├── registry path ──→ planning ──→ execution ──→ 3 Altadena snapshot layers
   │                     LLM proposes, registry validates
   └── renderer path ──→ _render_answer
                            ├── plan_fire_raster hit  → _render_fire_event
                            └── otherwise             → _render_city_context
```

| | Registry path | Renderer path |
|---|---|---|
| Entry | `nodes.planning` → `nodes.execution` | `api.py:_render_answer` |
| Selection | Model proposes layers; `validate_proposal` drops what it may not do | `plan_fire_raster(contract)` matches the local archive deterministically; otherwise the place path runs |
| Data | Three pinned Altadena snapshots | TS-SatFire archive, WFIGS, FIRMS, NWS, Open-Meteo, Census ACS, ArcGIS catalogue |
| Covers | `active_fire` only | Everything in `docs/06` §§2–7 |
| Governed by | The capability registry (§2) | The controls below |

**What governs the renderer path.** It consults no registry, so the guarantees
§2 provides have to come from somewhere else. Three things provide them:

1. **A host allowlist, not a model-chosen URL.** `portals.py` permits HTTPS to
   allow-listed hosts only. The model contributes a *search term*; it never
   contributes an address. A suggested host that is not on the list is inert.
2. **Deterministic local matching.** Which archived event a question refers to is
   decided by `plan_fire_raster` and `resolve_local_fire` from the user's own
   words, not by a model naming a file. An id that is not in the catalogue cannot
   be reached.
3. **Routing guards on the user's literal wording.** A model restatement cannot
   turn a weather question into a fire-data request. See
   `docs/07-runtime-routing.md`.

**How a capability gap reaches the screen from either path.** The registry path
reports one as `UnmetNeed` inside the plan it built. The renderer path has no
plan to attach it to, so `planner.deployment_unmet_needs` answers the wider
question directly: what nothing in this deployment can produce, by any path. It
reads `CAPABILITIES` and `RENDERER_COVERAGE` and calls no model, which is why it
is affordable on a turn whose layer selection is skipped entirely.

The `plan` event carries it either way. When the renderer path is answering, the
event goes out with its **layers stripped** — those layers are the registry's
Altadena snapshots, they are not what was drawn, and reporting them as though
they were would trade one dishonesty for another.

> This was broken until 2026-08-27, and the fix had an order. The registry knew
> only its three `active_fire` entries, so a gap report built on it would have
> denied `exposure` in the same reply that had just fetched Census figures.
> `RENDERER_COVERAGE` had to be declared first. README items 5–7 record all
> three corrections.

**Why it was built this way.** Extending the registry means declaring, per layer,
a file path, a family, a temporality, and exactly which required variables it
supplies. For a pinned snapshot that is a few lines. For a live source with an
approval gate, a per-event archive with thirty bands, and a catalogue searched at
request time, the schema does not fit without being redesigned. Writing renderers
shipped the capabilities; the cost is that the registry stopped describing the
system, and this section is the interest payment.
