---
name: adding-a-fire-analysis
description: Use when adding, changing, or removing a derived analysis in this repo - a new spectral index, a new per-day context analysis, a new band read from the TS-SatFire archive, or a new panel for an analysis result.
---

# Adding a fire analysis

## Overview

Derived analyses are declared in a registry, not wired into the orchestrator. One
entry in `backend/src/wildfire_agent/analyses/__init__.py` plus an implementation
is the whole extension point; `api.py` never changes.

**Core principle: the request selects a named analysis, it never describes one.**
Recognition, computation, and prose are three separate jobs, and only the first
one is allowed to look at what the user typed.

## Before writing any code: verify the data

**Read the actual band values. Never build on a band name.**

```bash
/opt/homebrew/bin/python3 -c "
from osgeo import gdal; import numpy as np
d = gdal.Open('.../FirePred/2020-09-11_FirePred.tif')
for i in range(1, d.RasterCount + 1):
    b = d.GetRasterBand(i); a = b.ReadAsArray().astype('float64'); f = np.isfinite(a)
    print(f'{i:2d} {b.GetDescription():34s} valid={f.mean()*100:5.1f}% '
          f'min={a[f].min():9.2f} max={a[f].max():9.2f} uniq={len(np.unique(a[f]))}')
"
```

Check three things and act on them:

| Finding | What it means | What to do |
|---|---|---|
| One unique value across the archive | Degenerate band | Exclude it, and say so in a comment |
| Range that contradicts the name | e.g. a "direction" running −89…87 is not a bearing | Exclude it; do not guess the convention |
| Values you cannot decode | Non-monotonic, no documented meaning | Use the band only for presence, never for magnitude |

Two bands in this archive are excluded for exactly these reasons, and one
(`VIIRS_Day` band 8) is treated as a label because its numbers stayed undecoded.
Reproduce that reasoning rather than reversing it silently.

## Which family does it belong to

| Family | Shape | Add it to | Event |
|---|---|---|---|
| `index_change` | Two dates, one raster formula, before/after/difference views | `INDEX_SPECS` in `scripts/build_raster_analysis.py` | `spatial_analysis` |
| `fire_context` | Per-day traversal of the footprint, tabular result | `build_fire_context_analysis.py` + a summary in `fire_context.py` | `fire_context` |

A new spectral index is a row in `INDEX_SPECS` — bands, `kind`, `delta`
direction, palette, `damage_sign` — and nothing else. If you are writing a new
code path for one, you are in the wrong file.

## The registry entry

```python
AnalysisSpec(
    id="nbr_change",
    title="burn severity",
    event="spatial_analysis",       # must exist in events.py
    compile=_compile_index("nbr_change"),
    execute=execute_raster_analysis,
    patterns=(_SEVERITY,),          # scored, not ordered
    requires=(),                    # all must match for any score to count
    precedence=10,                  # ties only; lower wins
    notes={"family": "index_change"},
)
```

Two routing rules that cost real bugs when broken:

- **Score, don't order.** `choose()` counts distinct matching terms so the
  dominant subject wins. A first-match rule handed a land-cover question the
  spread analysis.
- **The user's words outrank the resolved rewrite.** The rewrite inherits the
  previous turn's vocabulary. This is the same rule `request_intent` applies to
  data-family selection.

## The result must describe itself

One event carries every result in a family, so the panel reads the payload rather
than assuming the analysis it was first written for. A result owes:

- a `title` naming *this* analysis, not the family's first member
- `caveats` derived from the actual provenance (see `_caveats`), so changing a
  source cannot leave a stale disclaimer behind
- statistics whose meaning survives the framing — for dNBR, "pixels lower" means
  *less* damage, so severity reports `damaged_percent` instead

## Verification

Tests passing is not evidence the builder still runs. Analyses are cached.

```bash
# Force a real rebuild before trusting a green suite
.venv/bin/python -c "
from wildfire_agent.raster_layers import _PREVIEW_ROOT
for p in _PREVIEW_ROOT.glob('*.analysis.json'): p.unlink()
for p in _PREVIEW_ROOT.glob('*.context.json'): p.unlink()
"
.venv/bin/python -m pytest -q          # runtime should jump; that is the builder
.venv/bin/python -m ruff check src tests scripts
```

**For a script with no test coverage** (`build_fire_lifecycle_cache.py`),
generate a baseline before editing and byte-compare after:

```bash
/opt/homebrew/bin/python3 scripts/build_fire_lifecycle_cache.py \
  <event_dir> /tmp/baseline --center-latitude LAT --center-longitude LON
# ...edit...
diff -rq /tmp/baseline /tmp/after
```

Then drive it in the browser. The panel is where wrong labels surface.

## Checklist

- [ ] Band values inspected; degenerate and undecodable bands excluded with a reason
- [ ] Implementation added to its family's spec table, not a new code path
- [ ] `AnalysisSpec` registered; `event` exists in `events.py`
- [ ] Patterns scored; the user's own wording takes precedence
- [ ] `title`, `caveats` and statistics describe this analysis, not the family default
- [ ] Cache cleared, suite green, ruff clean
- [ ] Uncovered scripts byte-compared against a pre-edit baseline
- [ ] Driven in the browser and the rendered panel read

## Common mistakes

| Mistake | Why it happened | Cost |
|---|---|---|
| Building on a band name | Names looked self-explanatory | Nearly shipped an index over Kelvin brightness temperature |
| A second parallel analysis stack | Faster than extending the registry | Plan model, compiler, executor, event and panel all duplicated |
| Routing on the resolved request | It was the string in hand | Land-cover question answered from the spread result, confidently |
| Reusing a panel unchanged | Same event, so it rendered | dNBR shown as "NDVI change" with an inverted statistic |
| Trusting a green suite | 26 tests passed | They were reading a cached JSON; the builder was never re-run |
| Renaming across a helper boundary | `_read_mask` → `read_band` looked mechanical | Different return types; the builder crashed on first real run |

## Red flags

- "The band name says what it is" → read the values first
- "This does not fit the registry, I will add a branch in `api.py`" → it fits; find the family
- "Tests pass" without having cleared the cache → you tested the cache
- "The panel already renders it" → check the title, the caveat, and every statistic label
