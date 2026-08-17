# Contributing

Everything you need to work in this repository without reading all of it first.

---

## Setup

```bash
# Backend — Python 3.11+, uv
cd backend
uv sync --extra anthropic --extra openai
cp ../.env.example ../.env          # fill in one provider key, or use mock

# Frontend — Node 20+, pnpm
cd ../frontend
pnpm install
cp .env.local.example .env.local
```

Run both:

```bash
cd backend  && uv run uvicorn wildfire_agent.api:app --reload   # :8000
cd frontend && pnpm dev                                         # :3000
```

**You do not need an API key to run the demo.** `LLM_PROVIDER=mock` drives the same pipeline
with keyword rules, and the UI badges it as `MOCK` so a stub never passes for real inference.

Before pushing:

```bash
cd backend  && uv run --group dev ruff check src tests && uv run --group dev pytest
cd frontend && pnpm typecheck && pnpm test && pnpm build
```

Tests never call a provider and never touch the network. If the suite suddenly takes seconds
instead of milliseconds, something acquired a real client — find it and stub it.

---

## The shape of the system

Two agents. The **User Goal Agent** defines the problem; the **Planning Agent** answers it.

```
User Prompt → Requirement Understanding → Task Compiler
            → Ambiguity Resolution → Analysis Contract        ← User Goal Agent
            → Layer Selection → Fetch & Render                 ← Planning Agent
```

**The Analysis Contract is the only thing that crosses between them.** That is the one invariant
worth protecting:

- The User Goal Agent never picks a dataset, never fetches, never renders.
- The Planning Agent never re-interprets the user's wording; it reads the contract.
- An incomplete contract is not analysed at all — the graph routes to the end with the gaps
  recorded. Running analysis on unresolved ambiguity is the error this project exists to prevent.

If a change would blur that line, it probably belongs on the other side of it.

---

## Three sources of truth

Work out which one owns the thing you are changing, and **do not restate it anywhere else**.

| File | Owns | Everything else derives from it |
|---|---|---|
| `backend/src/wildfire_agent/taxonomy.py` | intents, expertise levels, roles, hazard objects, slot matrix | prompt fragments are generated from it; the frontend reads `GET /api/taxonomy` |
| `backend/src/wildfire_agent/contract.py` | the Analysis Contract | LLM structured output, OpenAPI, and the frontend types via `GET /api/schema/contract` |
| `.env` | every URL, port and key | all code — never hard-code a host |

A fourth, narrower one: `backend/src/wildfire_agent/planning/capabilities.py` owns what data this
deployment can actually serve. `GET /api/taxonomy` derives each hazard object's `covered_by` from
it, so coverage is stated once.

---

## The two interfaces

### Agent to agent — the Analysis Contract

Defined in `contract.py`, carries `schema_version`, and is servable as JSON Schema:

```bash
curl localhost:8000/api/schema/contract
```

It is deliberately consumer-shaped: each field exists because the downstream agent routes on it.
Before adding a field, be able to say which decision downstream it changes. Fields nothing consumes
are how a contract turns into a dumping ground.

### Backend to frontend — the SSE stream

Declared and documented in `backend/src/wildfire_agent/events.py`, with a test that fails if
`api.py` emits an event the document does not mention, or documents one it never sends.

One caveat worth repeating because it cost real time: **the stream uses CRLF**. A client that
splits only on `\n\n` receives nothing at all while the server logs a clean `200`.

`frontend/lib/types.ts` mirrors the contract by hand for development convenience. **The backend
owns the shape**; when they disagree, the TypeScript is wrong.

---

## Common changes

### Add a hazard object

1. Add a `HazardObject` to `HAZARD_OBJECTS` in `taxonomy.py`, with its required variables and
   candidate dataset families. Stop at the family level — naming a specific API here crosses the
   boundary.
2. If it is served by families whose meanings are **not interchangeable**, add `DataFamilyChoice`
   entries with explicit `keywords`. Do not derive keywords from the id: that once made `fire` a
   keyword of `official_fire_perimeters`, so the phrase "active fires" counted as an answer when it
   was the question.
3. Add slot requirements to `INTENT_SLOT_MATRIX` if the object implies any.
4. Document the reasoning in `docs/01-taxonomy.md`.

### Add a data layer

1. Snapshot it in `backend/scripts/fetch_showcase_data.py`, recording `source`, `as_of` and
   `retrieved_at` in its `provenance` block. Data without provenance is indistinguishable from
   invented data.
2. Register a `Capability` in `planning/capabilities.py` — hazard object, family, temporality,
   geometry type, and a `caveat` the UI will always show. The caveat is not optional: a satellite
   heat pixel shown without one reads as "a fire".
3. Give it a colour in `LAYER_STYLE` in `frontend/components/MapView.tsx`.
4. Add a case to `backend/tests/test_planning.py`.

### Add an LLM provider

Set `LLM_PROVIDER` and `LLM_MODEL`, then `uv sync --extra <provider>`. Nothing else changes: no
module imports a provider SDK, everything goes through `llm.py`. If you find yourself importing
`langchain_openai` anywhere else, that is the bug.

---

## Conventions

- **English only** in code, comments, docs, commit messages and UI copy.
- **No secrets in the repository.** `.env`, `*.key`, `*secret*` and `api.txt` are gitignored;
  `.env.example` documents the variables with empty values. If a key is ever committed, rotate it —
  removing the file is not enough once it has been pushed.
- **Never hard-code a host or port.** They come from settings.
- **Modules are lower_snake_case.** macOS is case-insensitive, Linux is not, and `from Utils import`
  works locally right up until CI.
- **Empty is a result.** A layer with no features in scope returns zero and says so. Never pad,
  never substitute a lookalike, never present a truncated list as a total.
- **Assumptions are visible and retractable.** Anything the agent decided on the user's behalf goes
  in `assumptions`, tagged with the slot it concerns so it can be withdrawn when that slot is later
  answered. A stale assumption misreports the contract downstream.

### Rules the model is not asked to remember

Some domain facts must hold on every run. Those live in the domain model and are enforced in code —
not phrased as prompt instructions. The precedent: the rule that
`active_fire` covers two non-interchangeable data families was once a line in the Task Compiler
prompt, and the model read it and defaulted the slot anyway, deleting the most important moment in
the workflow. It now lives on `DataFamilyChoice` and is enforced by
`nodes.enforce_family_disambiguation`.

The general pattern, used at both agent boundaries: **the model proposes, code validates.** The
model brings judgement; code guarantees the invariants.

---

## Pull request checklist

- [ ] `ruff check` and `pytest` pass; `pnpm typecheck`, `pnpm test` and `pnpm build` pass
- [ ] Tests still run without network or API keys
- [ ] No secrets, no hard-coded hosts, no non-English text
- [ ] If you changed the contract or the SSE events, you updated `events.py` / `docs/` too
- [ ] If you added an assumption or a caveat, it reaches the UI
