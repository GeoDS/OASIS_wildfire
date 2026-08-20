# Local Data Recognition Smoke Test

This is the first slice of the next demo milestone: test whether a configured
LLM can recognise useful local data from directory metadata before any raster
analysis or external API call happens.

## Boundary of this test

The model does **not** browse the filesystem and does not read raster pixels.
Trusted code scans one allow-listed root, groups files into compact datasets,
and sends only relative paths and metadata to the model. The model returns
structured dataset ids; code rejects ids that were not in the catalogue.

```text
LOCAL_DATA_ROOT
  -> metadata scanner
  -> compact catalogue (relative paths only)
  -> structured LLM recognition
  -> id validation
  -> selected datasets + missing variables + clarification questions
```

The first scanner supports:

- GeoTIFF time series grouped by event and variable directory;
- `Info_events.xlsx`, joined to raster groups by folder/event id;
- GeoJSON / JSON provenance, fields, feature counts and geometry types;
- CSV / TSV headers and record counts;
- ZIP member manifests without extraction.

## Configure

The repository `.env` is local-only and ignored by Git. Point it at the data
directory and begin with the mock baseline:

```dotenv
LLM_PROVIDER=mock
LOCAL_DATA_ROOT=../data
```

Then switch to a real provider by setting `LLM_PROVIDER`, `LLM_MODEL`, and the
matching API key. Never commit or paste the key into logs.

## Run

From `backend/`:

```bash
# Verify directory discovery without any model call.
uv run python scripts/test_local_metadata_llm.py --catalog-only

# Offline keyword baseline; clearly labelled deterministic_mock.
uv run python scripts/test_local_metadata_llm.py \
  -q "Which day and night satellite layers cover the Bobcat Fire?"

# The same command uses structured model inference after a real provider is configured.
uv run python scripts/test_local_metadata_llm.py \
  -q "For the Bobcat Fire, find local daytime and nighttime VIIRS data from September 2020."
```

## Initial test cases

1. **Exact event and variables** - Bobcat Fire plus `VIIRS_Day` and
   `VIIRS_Night`; both series should be selected and `FirePred` / `ESRI_LULC`
   should not be added.
2. **Semantic wording** - “白天和夜间卫星影像” should resolve to the same two
   VIIRS series even though the folder names are English.
3. **Time constraint** - a requested year outside the selected event range must
   be reported, not silently ignored.
4. **Missing variable** - weather, wind, or air-quality requests should appear
   under `missing_variables`; no nearby raster is a valid substitute.
5. **Unknown event** - the model should ask for clarification or report that the
   local catalogue has no matching event.
6. **Hallucinated path** - any dataset id absent from the catalogue must be
   removed by validation before later code can open it.

## Pass criteria for the demo

- Every selected id exists in the generated catalogue.
- Exact and semantic variants select the same expected local series.
- Requested but absent variables are stated explicitly.
- The result distinguishes metadata inference from raster-content analysis.
- The prompt contains no absolute local path and no API key.
- Mock output is visibly labelled and is never presented as LLM inference.
