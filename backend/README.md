# backend - User Goal Agent (Task 1)

FastAPI + LangGraph implementation. See the repository root `README.md` for context.

```bash
uv sync --extra anthropic --extra openai
cp ../.env.example ../.env       # fill in an API key
uv run wildfire --check          # config self-check, no LLM call
uv run wildfire -q "Which areas burned in the Eaton Fire around Altadena?"
uv run --group dev pytest        # 49 tests, no key required
```

## Module map

| File | Responsibility |
|---|---|
| `llm.py` | **The only LLM entry point.** Provider agnostic; switching model is an env change |
| `mock_llm.py` | `LLM_PROVIDER=mock` - keyword rules driving the same pipeline, no key needed |
| `taxonomy.py` | Single source of truth: intents / expertise / roles / hazard objects / slot matrix |
| `contract.py` | The Analysis Contract - the interface handed to the Planning Agent |
| `geocoding.py` | Spatial grounding via Nominatim; the only external call Task 1 makes |
| `graph/` | The four sub-stages as a LangGraph state machine, plus prompts |
| `cli.py` | Command line demo |
| `api.py` | FastAPI + SSE for the frontend |

## Map data test endpoints

- `POST /api/layers/public` calls the local Southern California MCP package and returns renderer-safe
  GeoJSON layers plus the MCP `loaded` / `empty` / `error` metadata.
- `POST /api/layers/local` accepts an allow-listed capability and `[west, south, east, north]` bbox,
  then geometrically clips the local snapshot before returning it.
- `GET /api/local-data/fire-events` groups the local TS-SatFire subset into historical wildfire
  events and reports which of the three paper tasks each event can support.
- `POST /api/layers/fire-lifecycle` returns event-scoped daily AF (VIIRS_Day band 7), cumulative
  BA (band 8), and a daily progression timeline. Generated PNGs are cached under `/private/tmp`.

`FirePred` is treated as the 19-band auxiliary feature stack for next-day prediction. It is never
labelled or rendered as a trained model prediction; `model_prediction_output` remains false until
an actual checkpoint and inference path are connected.
