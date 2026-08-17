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
