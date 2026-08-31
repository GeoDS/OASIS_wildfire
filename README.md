# Wildfire Analyst Agent

A multi-agent geospatial system that **defines the question before it answers it**.

The **User Goal Agent** turns a natural-language request into an *Analysis Contract*: what kind of
question it is, who is asking, which families of data it needs, and where on the ground. What
happens next depends on what the contract asks for — a named event in the local archive and a
resolvable place take different routes to an answer.

```
User Prompt → Turn Routing → Requirement Understanding → Task Compiler
            → Ambiguity Resolution → Analysis Contract            ← User Goal Agent
                             │
                      ready? ├── no ──→ END, with the gaps recorded
                             │
                             └── yes ─┬→ Layer Selection → Render  ← registry path
                                      └→ Renderers → Clip, Derive  ← renderer path
```

A structured contract passing between stages is the seam the pipeline extends along: each agent
consumes what the one before it produced, so a new stage can be added without the earlier ones
changing. It is also what keeps the current boundary real — the contract stops at the **data family**
level and never names an API, and an incomplete contract is never analysed.

**Two paths run downstream of a settled contract**, and it is worth knowing which one you are
looking at. The *registry path* is the Planning Agent proper: a model proposes layers and a
capability registry validates the proposal against what this deployment actually holds. The
*renderer path* carries everything built since — the historical archive analyses, live conditions
for a place, community intersection, and the consent-gated outside fetches. `docs/03` describes
both, including what governs the renderer path in place of the registry.

Showcase area: **Altadena, California — the Eaton Fire of January 2025**, which is what the three
registry-path layers hold. The fullest demonstration is the **Woolsey Fire** chain in
[`docs/06-how-to-use.md`](docs/06-how-to-use.md) §9 — twelve communities, every derived analysis,
and debris-flow coverage.

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
before it happens, asked once per session per source, and recorded where you can
see it.

---

## Quick start

**No API key needed to start.** `LLM_PROVIDER=mock` drives the same pipeline with keyword rules, and
the UI badges it so a stub never passes for real inference. **It covers a session's first turn
only.** Any follow-up — anything resolving *"it"*, *"those places"*, *"the next day"* — raises
rather than guessing, because `conversation.resolve_turn` will not infer a reference from keyword
rules. Demonstrating the conversational chain needs a configured provider.

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

> **Deciding out loud.**

Every geospatial agent has to choose between sources that answer different questions. Officially
confirmed perimeters are agency-verified and lag. Satellite thermal detections are about three hours
old and are heat signatures, not fires — a kiln and a flare produce the same pixel. Choosing wrongly
does not give you a rougher answer, it gives you a **different** one.

There are two obvious ways to handle that, and both are bad. Ask the user, and a non-expert has to
understand WFIGS versus VIIRS before they are allowed to see anything. Choose silently, and the most
consequential decision in the workflow becomes the one nobody can see. This system takes a third:
**it chooses by stated policy, and says so where you can read it and take it back.**

Ask `"Is there a fire near Altadena right now?"` and confirmed perimeters are used — the conservative
default. Ask about *heat signatures* or *hotspots* and satellite detections are used instead. Either
way the choice lands in `assumptions` with its confidence attached, visible and retractable. Name an
archived event and the local TS-SatFire AF and BA labels are selected explicitly, so a lifecycle
view is never allowed to pass for an official perimeter. The search radius it works out and draws for
you to adjust. That several US places share a name it states rather than asks. Anything it could not
settle at all lands in `unresolved` rather than being quietly closed.

**What it does stop and ask for is permission to leave the machine.** An outside fetch is offered
before it happens, asked once per session per source, and recorded in the **Limits** tab with who
authorised it, which source and vintage answered, what it supplied, and what is still missing from
any source. Decline and you are not asked again. Consent is the one thing the system will not assume
on your behalf.

Then the map makes the stakes concrete: the confirmed perimeter is one shape, and over a thousand
satellite detections spill visibly outside it. The system tells you which of the two you are looking
at, instead of requiring you to know the difference before you can look.

> **Why this changed.** An earlier version made this a blocking question, and `docs/01` §5.4 argued
> for it. Asking turned out to demand product literacy as the price of a first map. The rule did not
> go away — it moved from *asking* to *deciding and disclosing*, and `docs/01` §5.5 records the
> trade-off and its cost.

---

## Where to read more

| | |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Layout, the two interfaces, how to add a hazard object or a data layer, conventions |
| [`docs/01-taxonomy.md`](docs/01-taxonomy.md) | Hazard objects, the intent → slot requirement matrix, and why each decision was taken |
| [`docs/02-walkthroughs.md`](docs/02-walkthroughs.md) | Three end-to-end scenarios; these drove the contract schema |
| [`docs/03-planning-agent.md`](docs/03-planning-agent.md) | The two downstream paths, what governs each, and the showcase dataset |
| [`docs/06-how-to-use.md`](docs/06-how-to-use.md) | Every capability as a question you can type, plus what does not work |
| [`docs/07-runtime-routing.md`](docs/07-runtime-routing.md) | What decides whether the pipeline runs at all, and where the system stops to ask |
| `backend/src/wildfire_agent/events.py` | The SSE contract between backend and frontend |

Stack: FastAPI + LangGraph behind an SSE stream; Next.js, React and MapLibre in front.
384 backend tests (including the MCP package) and 11 frontend cases, none of which touch the network
or a provider.

---

## Open items

1. **The left data panel is a test harness**, not the final layer-management design. Visibility,
   ordering, opacity controls, and durable selections still need product design.
2. **Exposure ships at place level, not at parcel level.** Fuel, terrain and fire weather are read
   from the archive — burn severity (dNBR), land cover and terrain, spread behaviour against wind,
   and per-day fire weather all come from bands that ship with the local files. Census ACS supplies
   place-level exposure and vulnerability on approval. What is still absent is anything finer: an
   LA County parcel-level damage inspection layer would unlock the assessment and decision-support
   scenarios, and building footprints and the WUI boundary remain unavailable from any source the
   deployment can reach. Fourteen of the archive's thirty bands remain unread, including both
   VIIRS_Night bands.
3. **The map echo is not interactive yet** — the buffer circle can be seen but not dragged.
4. **`docs/01-taxonomy.md` §5 decisions await a product review.** All are decided and implemented;
   the cost of reversing each is documented alongside, and §5.5 records the one that was later
   relaxed.

### Fixed in the 2026-08-27 audit

A docs-versus-code audit found four defects. All are corrected; recorded here
because the reasoning outlives the fix.

5. **The capability registry did not know what the renderer path delivers.**
   `capabilities_for()` recognised three `active_fire` entries and returned
   nothing for `exposure`, `fuel`, `fire_weather`, `fire_spread`,
   `post_fire_debris_flow` or `smoke_plume`. `RENDERER_COVERAGE` now declares
   what that path serves, per hazard object and per required variable, strictly
   enough that a partial gap is still reported as one. `/api/taxonomy` gained
   `served` and `unserved_variables` alongside `covered_by`, which keeps its
   literal meaning: which registry capability draws this.
6. **`UnmetNeed` had no route to the screen on the normal path.** The Limits
   tab's "Unavailable capability" panel reads `plan.unmet`, and the `plan` event
   was suppressed once a place resolved. The event now travels with its layers
   stripped — the registry's Altadena snapshots are not what was drawn and must
   not be reported as though they were — so the gap arrives without the wrong
   layers arriving with it. Fixing 5 first was not optional: turning this on
   over the old registry would have denied a capability in the same reply that
   had just exercised it.
7. **The Planning Agent ran on every settled contract and its output was
   discarded.** One model call per turn for a layer selection nobody saw. The
   node now skips selection when the renderer path will answer, and computes
   only the capability gap, which needs no model at all.
8. **Answering any clarification crashed the turn.** `national` was assigned only
   where a *new* question is classified and read unconditionally after the graph,
   so every resume raised `UnboundLocalError` — after the user had already done
   the work of answering. Found while verifying 6, which needed a clarification
   answered to reach the code path at all. The human-in-the-loop flow is the last
   place that should fail quietly, and it was failing into a log nobody was
   reading.
