# Wildfire Analyst Agent

A multi-agent geospatial system. The **User Goal Agent** defines the problem before any analysis
begins, so the agents downstream make fewer mistakes; the **Planning Agent** then decides which
data layers answer that problem and draws them.

```
User Prompt → 1. Requirement Understanding → 2. Task Compiler
            → 3. Ambiguity Resolution → 4. Analysis Contract     ← User Goal Agent
            → 5. Layer Selection → 6. Fetch & Render             ← Planning Agent
```

The Analysis Contract is the only thing that crosses between them, which is what keeps the
boundary real: Task 1 never picks a dataset, and an incomplete contract is never analysed.

**Showcase area: Altadena, California — the Eaton Fire of January 2025.**

New here? Read [`CONTRIBUTING.md`](CONTRIBUTING.md) — it covers the layout, the three sources of
truth, and how to add a hazard object or a data layer without breaking the boundary between the
two agents.

---

## Quick start

**The full demo runs without an API key.** `LLM_PROVIDER=mock` is a set of keyword rules driving
the *same* SSE pipeline as a real model (`/api/health` and the sidebar both label it `MOCK`).

```bash
# Backend
cd backend
uv sync --extra anthropic --extra openai
cp ../.env.example ../.env
LLM_PROVIDER=mock uv run uvicorn wildfire_agent.api:app --reload

# Frontend, in a second terminal
cd frontend
pnpm install
cp .env.local.example .env.local
pnpm dev            # → http://localhost:3000
```

The command line works too:

```bash
cd backend
uv run wildfire --check                                        # config self-check, no LLM call
uv run wildfire -q "Which areas burned in the Eaton Fire around Altadena?"
uv run wildfire --expertise expert -q "..."                    # same question, expert register
uv run wildfire --graph                                        # export the Mermaid diagram
uv run --group dev pytest                                      # 71 tests, no API key needed
```

### Switching model is an environment change

No file imports a provider SDK; everything goes through
`backend/src/wildfire_agent/llm.py`.

```bash
LLM_PROVIDER=openai     LLM_MODEL=gpt-5.6-luna                 # current test model
LLM_PROVIDER=anthropic  LLM_MODEL=claude-sonnet-4-5-20250929
LLM_PROVIDER=openai     LLM_MODEL=qwen2.5  LLM_BASE_URL=http://localhost:11434/v1
LLM_PROVIDER=mock                                              # keyword rules, no key
```

Supported: `anthropic` / `openai` / `azure_openai` / `google_genai` / `groq` / `ollama` / `mock`.
Install the matching extra with `uv sync --extra <provider>`.

> **Secrets.** `.env` is gitignored, as are `api.txt`, `*.key`, and anything matching `*secret*`.
> Keys never appear in tracked files. `.env.example` documents the variables with empty values.

---

## What this agent is for

> **Knowing what to ask, and what not to.**

Ask about everything and the user gives up; ask about nothing and the agent has degraded into a
pass-through with no reason to exist. So the test is not "is the field empty" but **"would the
emptiness change the answer, and is there a cheaper way to settle it?"**

| Situation | What happens |
|---|---|
| Empty, does not affect the conclusion | Use the neutral default, **and record it in `assumptions`** |
| Empty, affects the conclusion, confirmable visually | Draw it on the map for the user to adjust — **no interruption** |
| Empty, affects the conclusion, no cheap substitute | **Ask**, everything at once, phrased for the expertise level |
| Asked and still unsettled | Record it under `unresolved` and hand off — **never pretend it is closed** |

In the primary scenario, `"Which areas burned in the Eaton Fire around Altadena?"`, the agent asks
exactly **one** question:

> Do you mean officially confirmed fire perimeters, or satellite thermal detections? A detection
> is only a heat signature — it may be an agricultural burn rather than a wildfire.

The search radius it works out and draws for you to adjust. That several US places share a name it
states rather than asks. **That contrast — between what it asks and what it settles by itself — is
the demo.**

Then the map makes the point concrete: the confirmed perimeter is one shape, and over a thousand
satellite detections spill visibly outside it. Picking the wrong one would not have produced a
rougher answer, it would have produced a different one.

### Some domain facts are not left to the model

`active_fire` is served by two data families whose meanings are not interchangeable. Choosing
wrongly does not coarsen the answer, it invalidates it.

Expressing that as a prompt instruction failed in practice — gpt-4.1-mini read it and defaulted the
slot anyway, deleting the most important moment in the workflow. The rule now lives in the domain
model (`taxonomy.DataFamilyChoice`) and is enforced in code
(`nodes.enforce_family_disambiguation`). The model is left to phrase the question, not to remember
that the question exists.

---

## Repository layout

```
wildfire/
├── CONTRIBUTING.md      # how to work in this repo
├── docs/
│   ├── 01-taxonomy.md   # hazard objects + the intent→slot requirement matrix
│   ├── 02-walkthroughs.md  # three end-to-end scenarios that drove the schema
│   └── 03-planning-agent.md  # downstream stage + the showcase dataset
├── backend/             # FastAPI + LangGraph (uv)
│   ├── src/wildfire_agent/
│   │   ├── llm.py       # the only LLM entry point, provider agnostic
│   │   ├── mock_llm.py  # LLM_PROVIDER=mock: full pipeline with no key
│   │   ├── taxonomy.py  # single source of truth for the domain vocabulary
│   │   ├── contract.py  # the Analysis Contract — interface to the Planning Agent
│   │   ├── geocoding.py # spatial grounding (Nominatim)
│   │   ├── graph/       # the six stages, plus prompts
│   │   ├── planning/    # Planning Agent: capability registry, planner, executor
│   │   ├── cli.py       # command line demo
│   │   └── api.py       # FastAPI + SSE
│   ├── data/altadena/   # snapshotted showcase layers, with provenance
│   ├── scripts/         # fetch_showcase_data.py - re-snapshot from source
│   └── tests/           # 71 tests, stub LLM and stub geocoder, no network
└── frontend/            # Next.js 15 + React 19 + Tailwind 4 + MapLibre
    ├── lib/             # SSE client, session hook, contract types
    └── components/      # Sidebar / PipelineStepper / MapView / ContractCard / ChatPanel
```

### Three sources of truth

Before changing something, work out which of these owns it — and **do not restate it anywhere
else**:

| File | Owns | Consumed by |
|---|---|---|
| `taxonomy.py` | intents / expertise / roles / hazard objects / slot matrix | prompt fragments are generated from it; the frontend reads `/api/taxonomy` |
| `contract.py` | the shape of the Analysis Contract | structured LLM output, OpenAPI, frontend types via `/api/schema/contract` |
| `.env` | every URL and key | all code. **Never hard-code `localhost:3000`** |

---

## Three-column interface

| Column | Contents |
|---|---|
| **Left** | Held open — "to be designed". Reserved for the data-layer panel once product design lands. Only the model badge and the study area sit here. |
| **Centre** | The pipeline stepper across the top, showing the handoff from the User Goal Agent to the Planning Agent. Then the map, with the resolved scope (dashed = assumed, solid = confirmed) and the drawn layers, each legend entry carrying its caveat and source. Below it, the Analysis Contract as it assembles. |
| **Right** | The conversation. Clarification options render as buttons that append to the composer, so several answers go in one reply. The expertise picker sits by the composer, like a model picker. |

## Backend API

| Endpoint | Purpose |
|---|---|
| `GET  /api/health` | Is the LLM ready, and is it the mock? |
| `GET  /api/taxonomy` | All enums, the slot matrix, the capability catalogue and the study area |
| `GET  /api/schema/contract` | JSON Schema for generating TypeScript types |
| `POST /api/sessions` | Create a session |
| `POST /api/sessions/{id}/messages` | Send a message, receive an SSE stream |

SSE events: `stage` · `contract` · `clarification` · `plan` · `layer` · `summary` · `done` ·
`error`. Layers each go out on their own event, since they carry full GeoJSON. One endpoint serves both
the opening question and a clarification answer; the backend decides which by checking whether the
session is parked on an interrupt.

> The stream uses CRLF separators (`\r\n\r\n`), as sse-starlette emits. A client that only splits
> on `\n\n` receives nothing while the server logs a clean 200. `frontend/lib/api.test.ts` guards
> this.

---

## Status

| Step | State |
|---|---|
| 1. Three walkthrough scenarios | Done — `docs/02-walkthroughs.md` |
| 2. Analysis Contract schema | Done — validated against all three scenarios, six revisions applied |
| 3. Project skeleton, dependencies pinned | Done |
| 4. LangGraph state machine + CLI | Done — verified end to end on `gpt-5.6-luna` |
| 5. Three-column frontend | Done — typecheck, 6 tests, production build, verified in a headless browser |
| 6. Planning Agent + showcase data | Done — layers selected, fetched and drawn; 71 backend tests |

### Open items

1. **Left panel design** — deliberately blank, awaiting product. The natural occupant is a
   data-layer panel with per-layer visibility toggles.
2. **`docs/01-taxonomy.md` §5 decisions await a product review** — hazard-object granularity,
   default values, and the placement of `capabilities`. All three are decided and implemented; the
   cost of reversing each is documented alongside.
3. **The map echo is not yet interactive** — the buffer circle can be seen but not dragged.
4. **Only `active_fire` has data.** Every other hazard object reports honestly as unavailable,
   which is correct but limits what the demo can answer. Adding an exposure layer (LA County
   publishes parcel-level damage inspection data for the Eaton Fire) would unlock the assessment
   and decision-support scenarios.
