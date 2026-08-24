# How To Use

> Everything the system can currently do, as questions you can type. Each topic
> opens with a question that starts it, then the follow-ups that build on it
> without re-running the pipeline.
>
> Every capability listed here was verified end to end against the running
> backend on 2026-08-24. Anything that does not work is in §9, not omitted.

---

## How to read this

Each topic gives four things:

1. **Ask** — wording that reliably triggers it.
2. **Does** — what the backend actually performs.
3. **Data** — which sources it touches, local and external.
4. **Result** — what comes back, and what it deliberately does not claim.

Two behaviours run through everything:

- **A follow-up does not re-run the analysis.** Asking what a number means, or
  asking for a figure the result already holds, is answered from what is on
  screen. Only a question needing new data redraws the map.
- **An external fetch is asked once per session, per source.** Approve it and it
  stays approved; decline it and you are not asked again. The question is only
  put when the answer would actually change.

---

## 1 · Current conditions for a place

**Ask**
```
How is the weather at Altadena?
Show current weather and fire-related conditions in Santa Barbara, California.
Is there a fire near Pasadena right now?
```

**Does** — Geocodes the place, resolves it to its Census place/CDP polygon, and
checks whether any agency-mapped fire perimeter currently overlaps that boundary.

**Data** — Nominatim for geocoding · NWS forecast · Open-Meteo air quality ·
WFIGS current perimeters · TIGER/Line 2025 Places for the boundary.

**Result** — Conditions at the resolved city reference point, plus a plain
statement about perimeter overlap. Absence of an overlap is reported as an
observation with a shelf life — *"that is the position as last published, not a
forecast"* — never as safety.

### Follow-ups

```
What's the population here?
How many housing units are there?
What's the median household income?
```

Asking about people or housing offers the Census ACS fill (§3.1). The place
polygon already carries its GEOID, so the fill attaches straight to it.

---

## 2 · A historical fire on one day

**Ask**
```
Show the lifecycle of the Bobcat Fire on 2020-09-27.
Show the Woolsey fire on 2018-11-16.
Now show me the Lake Hughes fire.
What about the Alisal fire in 2021?
```

**Does** — Matches the named event in the local TS-SatFire archive, selects the
day, and renders that day's active-fire (AF) labels over the burned area (BA)
accumulated through that date.

**Data** — TS-SatFire VIIRS_Day, band 7 as AF and band 8 as BA · the official
historical perimeter from WFIGS fire history, shown only as separate context.

**Result** — AF pixel count for the selected day, cumulative BA in km², and the
rendered lifecycle. The map is anchored to the label pixels themselves; no
buffer is invented, and the label extent is never presented as an official
perimeter.

**Naming.** Five events are named for a fire and match on the name alone.
Four are named for a place — Lake Hughes, Mojave / I-15, San Bernardino,
Santa Barbara Co. — and need the word *fire* next to the name (*"the San
Bernardino fire"*) or a year. That pairing is what separates *"the San Bernardino
fire"* from *"is there a fire near San Bernardino"*, which is a question about now
and correctly does not load a 2020 archive.

**Dates.** With no date, the last day of the event is used. A date in your
question wins over anything the time slot holds. A span resolves to its **end**,
because burned area is cumulative and a period is asking what it came to.

### Follow-ups

```
What changed the next day?
How many pixels was that?
What does BA stand for?
```

The first redraws for a different day. The other two are answered from the
result already on screen.

---

## 3 · Which communities the fire reached

**Ask**
```
Which cities did it reach?
What towns were affected by the fire?
List the municipalities the burned area overlaps.
Did the fire get into any populated areas?
```

**Does** — Intersects the cumulative BA label pixels with Census place
boundaries, and separately intersects the same day's AF pixels. Where the
footprint reaches no listed boundary, it reports the nearest places with
distances instead.

**Data** — TS-SatFire BA/AF labels × TIGER/Line 2025 Places.

**Result** — Each place with the share of **its own land** inside the footprint
and the burned area in km². Both figures travel together on purpose: the share
alone is misleading at both ends. Los Angeles had 3.4 km² inside the Woolsey
footprint and would round to "0%", while Hidden Hills' 0.7 km² reads as "16%".
A share is written `<1%` rather than `0%`, and capped at `100%` — a pixel whose
centre falls inside counts whole, so the raw ratio can exceed the polygon.

Same-day AF signals inside a place are reported **separately** and never merged
into the burned list: they do not establish that the place burned, or that the
signal belonged to this fire.

### 3.1 Follow-up: who lives there

```
Fetch it                          ← approves the Census fill
What's the median household income?
How wealthy are those places?
How many homes are in those cities?
What's the median age there?
Who couldn't have driven out?
```

**Does** — Attaches ACS attributes to each place by GEOID. Offered rather than
fetched, and offered only when the answer would print the figures.

**Data** — U.S. Census Bureau ACS 5-year estimates, 2020–2024, by Census place.
Requires `CENSUS_API_KEY`.

**Result** — Population, housing units, median age, seniors living alone, median
household income, households without a vehicle. Whole-place figures, carrying
the warning that they must never be multiplied by a burned-area share to imply
that many residents were affected.

The reply opens with the variable you asked for; the rest stay in session memory
and answer later questions rather than being recited. *Wealthy*, *affluent*,
*poor*, *cost of living* are all understood as income — the resolver reads the
question rather than matching keywords.

**Still missing after this fill:** building footprints, WUI boundary. The offer
says so before you approve it.

---

## 4 · Post-fire debris-flow hazard

**Ask**
```
What should I watch for next?
Is there a debris flow risk?
What happens in the rainy season?
```

**Does** — Nothing local carries this, so the system searches a public catalogue
at request time, offers what it found, and fetches only on approval.

**Data** — ArcGIS Online catalogue search → LA County Public Works "Post-Fire
Debris Flow Hazards View". Keyless. An allowlist governs which hosts may be
contacted; the model contributes a search term and never a URL.

**Result** — Modelled Phase 1 hazard polygons for the burn scar, drawn with a
legend. Published for planning — not a forecast of any storm, and not a record
that a debris flow occurred.

**Coverage is LA County only.** Of the nine archived events, only **Bobcat**
(13,954 polygons) and **Woolsey** (2,248) are in it. For anything else the answer
is explicit: *"the service was reached, but it publishes no hazard area for X —
that is an answer about this fire, not a failure."*

**The fetch is capped at 1,500 polygons.** When it is, the result says so:
what was drawn is a sample, not an inventory. `rainfall threshold` remains
missing either way, so the system will not say when concern is triggered.

### Follow-ups

```
How many hazard areas are there?
What does Phase 1 mean?
```

---

## 5 · Derived raster analysis

**Ask**
```
How badly did the Bobcat fire burn between the first and last day?
Compare NDVI inside the Woolsey fire's mapped burned area from the first to the last record.
Show only the difference and explain the red areas.
```

**Does** — Computes a spectral index on two dates and subtracts them, masked to
the mapped burned-area footprint.

**Data** — TS-SatFire VIIRS_Day reflectance bands. `nbr_viirs` for burn severity,
`ndvi_viirs` for vegetation change.

**Result** — The formula used, the two input dates, per-view rasters, and
statistics — alongside the caveats about what a VIIRS-derived index can and
cannot support.

**NDVI needs a comparison.** *"What is the NDVI?"* names a subject; only
*change*, *difference*, *compare*, *first day / last day* asks for this analysis.

---

## 6 · Fire environment

**Ask**
```
Which direction did the Bobcat fire spread, and how fast?
What were the fire weather conditions during the Woolsey fire?
What kind of land did the Alisal fire burn?
```

**Does** — Three separate analyses over the same event: spread behaviour, fire
weather, and land cover with terrain.

**Data** — TS-SatFire daily AF labels for spread · the event's local fire-weather
record · ESRI_LULC for land cover.

**Result** — Direction and rate of advance; the weather record as observed, not
as a driver; land-cover composition with terrain. A satellite label shows what
was detected, never what happened or why.

**Not every event supports all three.** Blue Ridge carries no ESRI_LULC and no
local fire-weather record, and says so plainly rather than substituting
something else. See §8.

---

## 7 · What the system holds

**Ask**
```
What fires do you have data for?
Which of those can't give me burned area?
What's in your archive?
```

**Does** — Answers from the session's own record of the local catalogue. No
pipeline runs and the map is untouched.

**Data** — The TS-SatFire event catalogue, with per-event support read from the
data rather than inferred from a variable being present.

**Result** — All nine events by name, their spans, and what each can actually
answer. Two of them carry active-fire detections and **no burned-area labels on
any day**; the answer names them rather than letting you find out by asking.

---

## 8 · What each fire supports

Verified 2026-08-24. `BA` = burned-area labels exist. `Cities` = places the
footprint reaches on the last day.

| Fire | Span | BA | Cities | Severity / NDVI | Spread | Fire weather | Land cover | Debris flow |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **Woolsey** | 2018-11-07 → 11-16 | ✅ | 12 | ✅ | ✅ | ✅ | ✅ | ✅ 2,248 |
| **Bobcat** | 2020-09-04 → 09-27 | ✅ | 3 | ✅ | ✅ | ✅ | ✅ | ✅ 13,954 |
| **Blue Ridge** | 2020-10-26 → 11-04 | ✅ | 4 | ✅ | ✅ | ❌ | ❌ | ❌ |
| **San Bernardino** | 2020-07-31 → 08-11 | ✅ | 3 | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Lake Hughes** | 2020-08-11 → 08-23 | ✅ | 2 | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Alisal** | 2021-10-05 → 10-16 | ✅ | 0 | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Mojave / I-15** | 2020-08-15 → 08-19 | ✅ | 0 | — | — | — | — | ❌ |
| **Thomas** | 2017-12-04 → 12-13 | ❌ | 0 | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Santa Barbara Co.** | 2017-07-07 → 07-14 | ❌ | 0 | ❌ | ❌ | ❌ | ❌ | ❌ |

**Woolsey is the fullest demonstration** — twelve places, shares from `<1%` to
`100%`, every analysis, and debris-flow coverage. The whole chain runs:

```
Show the Woolsey fire on 2018-11-16.
Which cities did it reach?
Fetch it
How wealthy are those places?
What should I watch for next?
Fetch it
```

**Alisal and Mojave / I-15 demonstrate a fire that reached nobody** — the result
switches to the nearest places with distances, and says that being nearby is not
being affected. (The distances are computed and present in the facts; the
narrated reply does not always list them. See §9.)

**Thomas and Santa Barbara Co. demonstrate an honest gap** — thousands of
active-fire pixels, no burned-area labels at all. The system says the quantity
does not exist for that event rather than reporting `0.0 km²` as a measurement.

---

## 9 · Known limits

- **Mojave / I-15 cannot be named.** Its normalised key becomes `mojave i 15`,
  which no natural phrasing produces. Reachable only through the sidebar. The
  only event whose name carries punctuation.
- **Thomas Fire's analyses fail with a raw traceback** in the error text, rather
  than the one-line explanation the other unsupported combinations give.
- **`the {fire_name}` assumes the name ends in "Fire".** Events named for a place
  read as *"the Lake Hughes had reached parts of…"*, and Santa Barbara Co.
  produces a doubled full stop.
- **Twelve places in one sentence is not readable.** The Woolsey community answer
  lists twelve names and twelve shares in a single paragraph.
- **The nearest-place list is sometimes trimmed away.** When a fire reaches no
  boundary, the distances are computed and travel in the facts, but the narrator
  may answer "no place was reached" without naming the nearest ones. Observed on
  Alisal, 2021-10-16.
- **The archive is not in this repository.** Without `LOCAL_DATA_ROOT` pointing at
  a TS-SatFire copy, the catalogue is empty and no fire matches — silently. See
  `backend/data/README.md`.
- **Historical air quality is unavailable** for archived events; current AQI is
  never substituted for it.
- **The map does not clear on a subject change yet.** The backend now reports
  `turn.subject_changed`; the frontend half is specified in
  [`05-turn-subject-change.md`](05-turn-subject-change.md) and not yet built, so
  the previous place's layers stay on screen until the new ones land.
