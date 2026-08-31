# Three End-to-End Walkthroughs

> The interface is defined by its consumer, not by whatever is convenient to produce. Each scenario
> below runs the whole chain:
> `user's words → clarification → contract instance → what the Planning Agent selects → what it draws`
>
> Scenario 1 is the only one the MVP must run. Scenarios 2 and 3 exist to check the schema does not
> buckle when extended.
>
> Depends on the hazard objects and slot matrix in `docs/01-taxonomy.md`.

> ### Status: design-time artifact, partly superseded
>
> These walkthroughs were written *before* the code, to derive the contract schema — and they did
> their job, which is why they are kept as written rather than rewritten to match. **Do not read
> them as a description of current behaviour.**
>
> One thing has since changed and it is load-bearing in scenario 1: the data-family question is no
> longer asked. `enforce_family_disambiguation` decides it by stated policy and records a
> retractable assumption instead. `docs/01` §5.5 has the reasoning and the cost. The correction is
> marked inline at scenario 1 §3.
>
> What the scenarios still establish, and what they were written for, is unaffected: the schema they
> forced (§ "Schema revisions these scenarios forced"), and scenario 3's demonstration that a
> capability gap is reported rather than substituted.

---

## Scenario 1 — the primary demo

**User says:** *"Where are the active fires near Altadena?"*
**Target classification:** `unknown` role × `general` expertise × `[observation]`

### 1. Requirement Understanding

| Inference | Value | Grounds |
|---|---|---|
| task_intent | `[observation]` | "Where are" retrieves current state |
| user_role | `unknown` | No role signal at all |
| expertise | `general` | No technical vocabulary, no parameters requested |
| hazard_objects | `[active_fire]` | "active fires" matches directly |

**On leaving the role unknown.** The framework says to infer a role only when the request clearly
supports it, and nothing here supports one. `unknown` plus a neutral default is the correct
behaviour, and it does **not** trigger a question — for an observation request rendered over the
whole area, a planner would receive the identical map. Role does not materially change the
analysis.

Worth saying out loud in the demo: **restraint is a capability, not an omission.**

### 2. Task Compiler → initial slots

| slot | Value | is_blocking | Interrupts? | Note |
|---|---|---|---|---|
| `location` | raw "near Altadena" → grounded | yes | **no** | see below |
| `time_horizon` | `now` | no | no | D default, recorded in assumptions |
| `target` | *(empty)* | **yes, promoted from D** | **yes** | see below |
| `requested_output` | `map` | no | no | D default, recorded in assumptions |

**`is_blocking` does not mean "must ask".** They are separate questions: `is_blocking` asks whether
the slot matters, and interrupting additionally depends on whether the gap has a cheaper remedy.
`location` is always blocking, but once it geocodes, **echoing it on the map is enough** — dragging
a circle is faster and more precise than reading a list of radii. That is override rule 2.

**Why the radius is not asked.** "near Altadena" resolves through Nominatim, a 25 km buffer is
applied, the circle is drawn **dashed** on the map, and the assumption is recorded. One glance and
the user can change it without answering anything.
The mirror case is in `backend/tests/test_geocoding.py` and
`test_graph.py::test_ungroundable_location_does_block`: "near my house" yields no coordinates, and
only then must the agent ask.

**Why the data type is asked.** `active_fire` is served by two families whose meanings are not
interchangeable:

- officially confirmed fire perimeters (WFIGS family)
- satellite thermal detections (HMS/VIIRS family — **possibly an agricultural burn or an industrial
  source, not a wildfire**)

This gap has **no cheap remedy**: it cannot be drawn on a map, and either default can make the
answer wrong rather than merely coarse. So the agent speaks. This is the clearest illustration of
"reduce errors" in the whole system, and the one line worth rehearsing for the demo.

> Enforced in code rather than requested in the prompt — see `docs/01-taxonomy.md` §5.4 for why.

### 3. Ambiguity Resolution — exactly one question

`expertise=general`, so: concrete options with plain-language consequences, never open-ended.

> I have drawn a 25 km radius around Altadena — the dashed circle on the map, which you can drag.
> One thing I need you to decide:
>
> **Which should count as an "active fire"?**
> ▸ **Officially confirmed fire perimeters** — verified by fire agencies, the most reliable, but
>   slow to update and frequently empty for any given area
> ▸ **Satellite thermal detections** — much timelier, but a detection is only a heat signature and
>   may be an agricultural burn or an industrial source
> ▸ **Both, so they cross-check each other** (suggested)

**Map behaviour.** A dashed 25 km circle (dashed = assumed, unconfirmed). Drag it to 10 km and it
tightens, turns solid, and the "radius not specified" assumption disappears from the contract.

> **Demo line:** "Notice it asked one question. The radius it worked out and drew for you to
> adjust. But it would not decide between confirmed fires and satellite heat on your behalf,
> because getting that wrong doesn't make the answer rougher — it makes it wrong.
> **Knowing which is which is the entire product.**"

**User replies:** "both, 10 km"

> #### ⚠ Superseded — this question is no longer asked
>
> As of `docs/01` §5.5, the family choice does not block and no question is put. *"Where are the
> active fires near Altadena?"* carries no heat or hotspot wording, so branch 3 selects **officially
> confirmed perimeters** — the conservative default — and writes
> `"Fire evidence selected automatically: Officially confirmed fire perimeters."` into
> `assumptions`, where the user can retract it. The radius behaviour below is unchanged.
>
> The demo line above must not be delivered as written. What is true now is nearer to:
>
> > "It did not stop to ask which kind of fire evidence you meant — that would have made you
> > adjudicate WFIGS against VIIRS before seeing a map. It chose the verified one, told you it
> > chose, and left it retractable. What it *will* stop and ask about is going out to a third party
> > on your behalf."
>
> The reply *"both, 10 km"* below therefore has no question to answer. Read the rest of this
> scenario as the schema derivation it was written to be, not as a transcript.

### 4. Resulting contract

```json
{
  "user_role": "unknown",
  "expertise": "general",
  "task_intent": ["observation"],
  "hazard_objects": ["active_fire"],
  "slots": {
    "location": {
      "kind": "spatial",
      "value": "10 km buffer around Altadena, CA",
      "raw": "near Altadena",
      "resolved": {
        "display_name": "Altadena, Los Angeles County, California, United States",
        "center": [-118.1312, 34.1897],
        "buffer_km": 10,
        "bbox": [-118.2400, 34.0997, -118.0224, 34.2797],
        "geocoder": "nominatim",
        "confirmed_by_user": false,
        "alternatives": ["Altadena, Nevada"],
        "ambiguous": false
      },
      "confidence": 1.0, "source": "user_stated", "is_blocking": true
    },
    "target": {
      "kind": "scalar",
      "value": "official_fire_perimeters + satellite_hotspots",
      "confidence": 1.0, "source": "user_stated", "is_blocking": true
    },
    "time_horizon": { "kind": "scalar", "value": "now", "source": "default", "confidence": 0.5 },
    "requested_output": { "kind": "scalar", "value": "map", "source": "default", "confidence": 0.5 }
  },
  "assumptions": [
    "No time window was specified, so the analysis refers to fires active now.",
    "No output format was specified, so the neutral default is a map.",
    "Spatial scope resolved by the agent: not yet confirmed by the user on the map."
  ],
  "unresolved": [],
  "ready_for_planning": true
}
```

### 5. How the Planning Agent reads it

| Contract field | Downstream action |
|---|---|
| `task_intent=[observation]` | Retrieve-and-render workflow: no modelling, no ranking |
| `hazard_objects=[active_fire]` | Match capabilities → `official_fire_perimeters` and `satellite_hotspots` |
| `slots.target` | Both families are wanted; nothing to guess |
| `slots.location.resolved.bbox` | Clip box for the executor |
| `expertise=general` | Render in plain language, no API names, and explain that a detection is not a wildfire |
| `user_role=unknown` | Neutral framing; no personal-safety emphasis |

**Output:** one map of the 10 km area with two layers (confirmed perimeters as polygons, satellite
detections as points) and a plain-language note.
**A layer returning zero features is a valid result** and renders as "no confirmed fires right now" —
never padded with synthetic data.

### What scenario 1 proved about the schema

- `Slot.is_blocking` must be **mutable at runtime** (the matrix says D, a rule promotes it to B), so
  it cannot be a static schema constant. Confirmed.
- **"Matters" and "must ask" have to be decoupled**: `is_blocking` for the first,
  `needs_clarification` for the second. `SpatialSlot` overrides the latter, because a map echo is
  cheaper than a question. Derived property, not another field.
- `ResolvedLocation` needs `confirmed_by_user` — echoing the scope back is an explicit Task 1 duty.
- Clarification questions need structured `options`; the general level must offer choices.
- `Slot.blocking_reason` is required, so a promotion or demotion stays auditable.

---

## Scenario 2 — does the schema survive multiple intents?

**User says:** *"Which communities are currently most exposed?"*
**Target classification:** `government_community_planner` × `practitioner` × `[assessment, decision_support]`

### What differs

- **Two intents.** "most exposed" is assessment (measure exposure) plus decision support (rank to
  find "most").
- **Blocking set is the union:** `{location, target, comparison_basis}`
  - `location` — the user gave **nothing**, a real gap rather than scenario 1's vague-but-resolvable
  - `target` — "communities" is given, but at what unit? tract, block group, municipality?
  - `comparison_basis` — **what does "most exposed" rank by?** Population count? Distance to fire?
    Weighted by vulnerability? This slot **does not exist at all in scenario 1**.
- **Hazard objects span all three tiers:** `[active_fire, smoke_plume, exposure, vulnerability]`

### Clarification, practitioner register

> Three things before I can give you a ranking worth trusting:
> ① **Extent** — the whole San Gabriel foothills, or Altadena plus a buffer?
> ② **Community unit** — census tract, block group, or municipal boundary? Finer units make the
>    ranking less stable.
> ③ **Ranking basis:**
> ▸ Absolute exposed population (large communities always lead)
> ▸ Exposure per capita (removes the effect of size)
> ▸ Exposure weighted by social vulnerability (surfaces places both badly hit and slow to recover;
>   usually the best fit for allocating mitigation resources)

### Contract deltas

```json
{
  "user_role": "government_community_planner",
  "expertise": "practitioner",
  "task_intent": ["assessment", "decision_support"],
  "hazard_objects": ["active_fire", "smoke_plume", "exposure", "vulnerability"],
  "slots": {
    "comparison_basis": { "value": "exposure × SVI weighted", "source": "user_stated", "is_blocking": true },
    "target": { "value": "census_tract", "source": "user_stated", "is_blocking": true },
    "threshold": { "value": null, "source": "default", "is_blocking": false }
  },
  "assumptions": ["No exposure threshold set, so the output is a continuous ranking rather than a binary classification."],
  "ready_for_planning": true
}
```

### Planning Agent output

A graded community-priority map, a ranked table, and a statement of data sources and limitations —
**no code**, per the practitioner register. This matches the framework's own worked example
verbatim: "a community-priority map and explanation, with data sources and limitations but no
unnecessary code".

### What it proved

- The slot set **cannot be four fixed fields**; it has to grow with intent. Hence
  `slots: dict[str, Slot]` rather than named attributes.
- Keeping `vulnerability` separate from `exposure` is load-bearing: two of the three ranking bases
  need the distinction.

---

## Scenario 3 — saying "we cannot do that" honestly

**User says:** *"Where may the fire spread in the next 24 hours?"*
**Target classification:** `researcher_gis_analyst` × `expert` × `[prediction]`

### What differs

- `time_horizon` goes from D to **B** (a prediction needs a stated window) — but the user already
  said "next 24 hours", so **no question is triggered**. This is the clean demonstration that
  **B does not imply asking; B only means "ask if missing"**.
- `scenario` is optional: an expert user cares about the wind assumption, but it does not block.
- **`hazard_objects = [fire_spread, fire_weather, fuel]`, and two of those have no coverage.**

### Clarification, expert register

> Window and extent are clear. Two modelling parameters, defaulted and recorded if you skip them:
> ① Wind field: current NWS forecast, or a sensitivity case such as wind +20%?
> ② Spatial resolution and ignition source: which detection or perimeter seeds the front?

### The point: how the contract expresses a capability gap

**Task 1 does not consult the coverage table** — that is the downstream capability match. It states
the need honestly:

```json
{
  "expertise": "expert",
  "task_intent": ["prediction"],
  "hazard_objects": ["fire_spread", "fire_weather", "fuel"],
  "slots": {
    "time_horizon": { "value": "next 24h", "source": "user_stated", "is_blocking": true },
    "scenario": { "value": "NWS current forecast wind", "source": "default", "is_blocking": false }
  },
  "assumptions": ["Wind field follows the current NWS forecast; no sensitivity case was run."],
  "unresolved": [],
  "ready_for_planning": true
}
```

The Planning Agent then matches against capabilities:

- `fire_weather` ✓ → `weather`
- `fire_spread` ✗ no spread model
- `fuel` ✗ no fuel map

→ and replies: **"The available sources are observational only, with no fire behaviour model, so a
spread forecast is not possible. What can be offered instead: current detections plus the downwind
vector and wind speed, as a qualitative indication of direction."**

### What it proved

- **`ready_for_planning: true` is correct here.** The contract is complete; "cannot be done" is a
  capability problem, not a definition problem. Conflating the two would have Task 1 pre-empting a
  downstream judgement, crossing the boundary the brief draws.
- Consequently the schema needs **no** `feasible` field.

---

## Schema revisions these scenarios forced

| # | Change | From | Why |
|---|---|---|---|
| 1 | `slots` became `dict[str, Slot]` | scenario 2 | Intent determines the slot set; four fixed fields cannot hold `comparison_basis` |
| 2 | `ResolvedLocation.confirmed_by_user` added | scenario 1 | Echoing the scope back is an explicit Task 1 duty |
| 3 | `ClarificationQuestion` with `options` added | scenario 1 | The general and practitioner levels must offer concrete choices |
| 4 | `Slot.blocking_reason` added | scenario 1 | Promotions and demotions have to stay auditable |
| 5 | `needs_clarification` as a derived property | scenario 1 | Decouples "matters" from "must ask"; `SpatialSlot` overrides it |
| 6 | **No** `feasible` or `capabilities` field | scenario 3 | Out of bounds — downstream responsibility |
