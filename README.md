# FireScope

A multi-agent geospatial system for wildfire analysis. FireScope turns a
natural-language question into an explicit *Analysis Contract* — what is being
asked, over which event and time span, using which families of data — and defers
execution until that contract resolves. Answers come back as qualified claims
with maps, sources, a reasoning trace, and stated limits.

Stack: FastAPI + LangGraph behind an SSE stream; Next.js, React and MapLibre in
front.

---

## How to run

**Requirements:** Python 3.11+ with [uv](https://docs.astral.sh/uv/), Node 20+
with [pnpm](https://pnpm.io/).

### 1. Configure a language model

FireScope is provider agnostic — switching providers is an environment change,
not a code change. Copy the example file and fill in your own provider, model and
key:

```bash
cp .env.example .env
```

```ini
# anthropic | openai | azure_openai | google_genai | groq | ollama
LLM_PROVIDER=openai
LLM_MODEL=gpt-5.6-luna
LLM_TEMPERATURE=0.0

OPENAI_API_KEY=your-key-here
```

For a local or OpenAI-compatible endpoint, also set `LLM_BASE_URL` (for example
`http://localhost:11434/v1` for Ollama). `.env` is gitignored and must never be
committed.

### 2. Download the data archive

**Required.** The fire lifecycle, burn severity (dNBR), NDVI change, land cover,
spread behaviour, fire weather and community intersection all read a ~1.9 GB
TS-SatFire subset that is **not redistributed in this repository**.

1. Download it from
   [this folder](https://drive.google.com/drive/folders/14AgkvlPX2Mae20yv7Gm9NvKPnQEJnx4w).
2. Place the downloaded directory anywhere outside the checkout.
3. Set `LOCAL_DATA_ROOT` to that directory — the one that **contains**
   `full_data/`, not `full_data/` itself.

```bash
echo 'LOCAL_DATA_ROOT=/absolute/path/to/firescope/data' >> .env
```

Expected layout:

```
<LOCAL_DATA_ROOT>/
  full_data/          per-event daily raster stacks (24461771 is the Bobcat Fire)
  boundaries/         TIGER/Line California place and state boundaries
  Info_events.xlsx    event index
```

`boundaries/` is what the community-intersection questions read; without it the
lifecycle analyses still work and the city results do not.

Once the backend is running (step 3), verify:

```bash
curl -s localhost:8000/api/local-data/fire-events
```

A working setup reports `"event_count": 9`. **A wrong path is not an error** — the
catalogue is simply empty (`event_count: 0`) and every question about a historical
fire silently finds no match. This is worth knowing before concluding something is
broken.

The subset holds nine Southern California events between 2017 and 2021, each a
daily raster stack on a shared 594 × 596 grid. Layout and band semantics are
documented in [`backend/data/README.md`](backend/data/README.md).

### 3. Backend

```bash
cd backend && uv sync --extra anthropic --extra openai
uv run uvicorn wildfire_agent.api:app --reload
```

### 4. Frontend, in a second terminal

```bash
cd frontend && pnpm install
cp .env.local.example .env.local
pnpm dev                                    # → http://localhost:3000
```

### CLI

```bash
uv run wildfire -q "What is the life cycle of the Bobcat Fire?"
```

That question reads the archive from step 2.

### Additional keys

These unlock individual data sources. The backend starts without them; what
stops working are the questions that depend on each source.

| Variable | Needed for | Where to get it |
|---|---|---|
| `CENSUS_API_KEY` | Census ACS population and vulnerability attributes | [api.census.gov](https://api.census.gov/data/key_signup.html) — free, now required for every query |
| `FIRMS_MAP_KEY` | NASA FIRMS near-real-time thermal detections | [firms.modaps.eosdis.nasa.gov](https://firms.modaps.eosdis.nasa.gov/api/map_key/) — free |
| `GEOCODER_USER_AGENT` | Nominatim geocoding | Nominatim's terms require an identifiable contact address |

Without a Census key the API returns an HTML page as HTTP 200 rather than an
error, so a missing key looks like a malformed response.

---

## Data access

### Live sources

Queried at request time, and offered for approval before any outside fetch:
NIFC WFIGS perimeters, NASA FIRMS thermal detections, NWS weather, Open-Meteo air
quality, U.S. Census ACS, and an ArcGIS catalogue searched for post-fire hazard.

---

## Example questions

Every capability is written out as a question you can type in
[`docs/06-how-to-use.md`](docs/06-how-to-use.md), which also lists what does
*not* work (§10) rather than omitting it. A representative set follows.

**Start a session.** Answered from a declared inventory — no pipeline runs, and
the map is left exactly as it was.

```
What can you do?
What fires do you have data for?
```

**Live conditions.** Each outside source is offered for approval before it is
contacted, once per session.

```
Are there any ongoing wildfires in the USA?
How is the weather at Altadena?
Is there a fire near Pasadena right now?
```

**A historical fire.** Reads the archive from step 2.

```
Show the lifecycle of the Bobcat Fire on 2020-09-27.
Show the Woolsey fire on 2018-11-16.
What about the Alisal fire in 2021?
```

**Which communities it reached**, and who lives there. The second group needs
`CENSUS_API_KEY` and is offered rather than fetched — approve it with `Fetch it`.

```
Which cities did it reach?
Did the fire get into any populated areas?

How wealthy are those places?
How many homes are in those cities?
Who couldn't have driven out?
```

**Derived analysis and fire environment.**

```
How badly did the Bobcat fire burn between the first and last day?
Which direction did the Bobcat fire spread, and how fast?
What were the fire weather conditions during the Woolsey fire?
```

**Post-fire hazard.**

```
Is there a debris flow risk?
What happens in the rainy season?
```


## Case study

A single session over the Bobcat Fire (Angeles National Forest, Los Angeles
County, September 2020). The opening request, *"What is the life cycle of bobcat
fire"*, names an event and nothing else. The agent matches it in the local
TS-SatFire archive and loads the twenty-four daily frames covering 2020-09-04 to
2020-09-27. FireScope draws the active-fire labels over the accumulated burned area, and
the analysis panel presents an interactive lifecycle bar plot so the severity of
each day in the series can be inspected. The answer in the conversation covers
the TS-SatFire record and nothing beyond it: 62 active-fire label pixels on
2020-09-27, against approximately 529.0 km² of cumulative mapped burned area.

![FireScope after the second Bobcat Fire turn](docs/images/case-study.png)

> **(A)** workflow and view controls · **(B)** source-aware map legend ·
> **(C)** lifecycle panel · **(D)** information panel with Result, Data,
> Reasoning and Limits · **(E)** composer with inferred query scope.
> The conversation appears at right.

From here the session can continue in either of two directions. The first is to
ask about the layers already on the map — what BA means, or how NDVI differs
before and after the fire. The second is to ask for information that concerns the
fire but is not carried by those layers. The screenshot follows the second: we
asked *"What cities did this fire impact"*. The agent joins the accumulated
burned-area pixels to Census place boundaries, returns the places that overlap
the footprint, and reports the share of each place that falls inside it. Monrovia,
Duarte and Arcadia intersect the footprint. Palmdale is reported separately and
under a caveat, because its six same-day active-fire pixels do not establish that
the city burned, or that those detections belonged to this fire. The agent then
asks for permission before connecting to an external source, the Census Bureau's
American Community Survey, which supplies the population and housing attributes
that no local layer carries.

The panels beneath the map carry the evidence for the answer. **Result** states
how the agent understood the request as a single question, and the answer it
returned for that question. **Data** lists every source involved in the session
together with its metadata. **Reasoning** presents the steps taken: interpreting
the request, resolving scope, selecting evidence, performing the spatial
analysis, and building the result. **Limits** records the assumptions and caveats
the session rests on.
---

## Acknowledgements

This work was conducted at the University of Wisconsin–Madison.

The historical analyses are built on the **TS-SatFire** dataset (Zhao, Gerard and
Ban), a multi-task satellite image time-series dataset for wildfire detection and
prediction, distributed at
<https://www.kaggle.com/datasets/z789456sx/ts-satfire/data>.

FireScope builds on other public data and open infrastructure besides. We thank
the National Interagency Fire Center for WFIGS perimeters and the
InteragencyFirePerimeterHistory archive; NASA FIRMS and the
NOAA NESDIS Hazard Mapping System for satellite thermal detections; the National
Weather Service and Open-Meteo for weather and air-quality data; the U.S. Census
Bureau for American Community Survey estimates; the Los Angeles County Department
of Public Works for the Eaton Fire perimeter; and OpenStreetMap contributors and
CARTO for basemap tiles.
