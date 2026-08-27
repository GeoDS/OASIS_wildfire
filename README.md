# Wildfire Analyst Agent

A multi-agent geospatial system that **defines the question before it answers it**.

Two agents are implemented so far. The **User Goal Agent** turns a natural-language request into an
*Analysis Contract*: what kind of question it is, who is asking, which families of data it needs,
and where on the ground. The **Planning Agent** reads that contract, picks the layers that answer
it, and draws them.

```
User Prompt → Requirement Understanding → Task Compiler
            → Ambiguity Resolution → Analysis Contract      ← User Goal Agent
            → Layer Selection → Fetch & Render               ← Planning Agent
            → …                                              ← further agents
```

A structured contract passing between stages is the seam the pipeline extends along: each agent
consumes what the one before it produced, so a new stage can be added without the earlier ones
changing. It is also what keeps the current boundary real — the User Goal Agent never picks a
dataset, and an incomplete contract is never analysed.

Showcase area: **Altadena, California — the Eaton Fire of January 2025.**

**What you can ask it** is documented question by question in
[`docs/06-how-to-use.md`](docs/06-how-to-use.md) — current national fire activity,
conditions for a place, a historical fire's progression, which communities it
reached, who lives there, post-fire debris-flow hazard, and derived raster
analysis. Every capability there was verified end to end against a running
backend; what does not work is listed rather than omitted.

Data comes from the local TS-SatFire archive plus live public sources: NIFC
WFIGS perimeters, NASA FIRMS near-real-time thermal detections, NWS weather,
Open-Meteo air quality, U.S. Census ACS, and an ArcGIS catalogue searched at
request time for post-fire debris-flow hazard. An outside fetch is offered
before it happens, asked once per session, and recorded where you can see it.

---

## Quick start

**No API key needed.** `LLM_PROVIDER=mock` drives the same pipeline with keyword rules, and the UI
badges it so a stub never passes for real inference.

```bash
# Backend
cd backend && uv sync --extra anthropic --extra openai
cp ../.env.example ../.env
LLM_PROVIDER=mock uv run uvicorn wildfire_agent.api:app --reload

# Frontend, second terminal
cd frontend && pnpm install
cp .env.local.example .env.local
pnpm dev                                    # → http://localhost:3000
```

**The historical fire analyses need a separate archive.** Burn severity, NDVI change,
land cover, spread behaviour and fire weather all read a ~1.9 GB TS-SatFire subset that
is not in this repository. Download it from
[this folder](https://drive.google.com/drive/folders/14AgkvlPX2Mae20yv7Gm9NvKPnQEJnx4w),
place `full_data/` outside the checkout, and set `LOCAL_DATA_ROOT` to its parent
directory. Without it the app runs and the catalogue is simply empty — no error, so it
looks like nothing matched. See [`backend/data/README.md`](backend/data/README.md).

The existing left sidebar includes two direct integration checks. **Public API** calls the
Southern California MCP and overlays its GeoJSON response. **Local clip** uses the bbox resolved
from the current chat request, clips an allow-listed local snapshot on the backend, and overlays
the result on the same MapLibre map. Ask a location-based question before using Local clip.

For a real model, set `LLM_PROVIDER` and `LLM_MODEL` in `.env` — `anthropic`, `openai`,
`azure_openai`, `google_genai`, `groq` and `ollama` all work, and no code changes. There is a CLI
too: `uv run wildfire -q "Which areas burned in the Eaton Fire around Altadena?"`.

---

## What makes it worth looking at

> **Knowing what to ask, and what not to.**

Ask about everything and the user gives up. Ask about nothing and the agent is a pass-through with
no reason to exist. So the test is not "is this field empty" but **"would the emptiness change the
answer, and is there a cheaper way to settle it?"**

Ask `"Which areas burned in the Eaton Fire around Altadena?"` and the agent asks exactly **one**
question:

> Do you mean officially confirmed fire perimeters, or satellite thermal detections? A detection is
> only a heat signature — it may be an agricultural burn rather than a wildfire.

The search radius it works out and draws for you to adjust. That several US places share a name it
states rather than asks. Anything it decided on your behalf lands in `assumptions`, visible and
retractable; anything it could not settle lands in `unresolved` rather than being quietly closed.

Then the map makes the point concrete: the confirmed perimeter is one shape, and over a thousand
satellite detections spill visibly outside it. Picking the wrong one would not have produced a
rougher answer — it would have produced a different one.

---

## Where to read more

| | |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Layout, the two interfaces, how to add a hazard object or a data layer, conventions |
| [`docs/01-taxonomy.md`](docs/01-taxonomy.md) | Hazard objects, the intent → slot requirement matrix, and why each decision was taken |
| [`docs/02-walkthroughs.md`](docs/02-walkthroughs.md) | Three end-to-end scenarios; these drove the contract schema |
| [`docs/03-planning-agent.md`](docs/03-planning-agent.md) | The downstream agent and the showcase dataset |
| `backend/src/wildfire_agent/events.py` | The SSE contract between backend and frontend |

Stack: FastAPI + LangGraph behind an SSE stream; Next.js, React and MapLibre in front.
72 backend tests and 6 frontend tests, none of which touch the network or a provider.

---

## Open items

1. **The left data panel is a test harness**, not the final layer-management design. Visibility,
   ordering, opacity controls, and durable selections still need product design.
2. **Exposure is the missing hazard object.** Fuel, terrain and fire weather are now read from
   the archive — burn severity (dNBR), land cover and terrain, spread behaviour against wind, and
   per-day fire weather all come from bands that ship with the local files. What is still absent
   is exposure: an LA County parcel-level damage inspection layer would unlock the assessment and
   decision-support scenarios. Fourteen of the archive's thirty bands remain unread, including
   both VIIRS_Night bands.
3. **The map echo is not interactive yet** — the buffer circle can be seen but not dragged.
4. **`docs/01-taxonomy.md` §5 decisions await a product review.** All are decided and implemented;
   the cost of reversing each is documented alongside.
