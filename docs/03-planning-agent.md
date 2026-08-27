# Planning Agent and the Showcase Dataset

> The stage downstream of the Analysis Contract. It exists to prove the contract
> is worth producing: everything here is driven by the contract and by nothing
> else, and the one question Task 1 insisted on asking is what makes the layer
> selection unambiguous.

---

## 1. Why it lives outside Task 1

The task brief is explicit that the User Goal Agent "does not decide which
datasets or tools should be used". So fetching data is not bolted into Task 1 —
it is a separate pair of graph nodes that run *after* the contract is finished:

```
… → analysis_contract ──(ready?)──→ planning → execution → END
                       └─(not ready)────────────────────→ END
```

Two consequences worth stating:

- **Task 1 still never picks a dataset.** The boundary is a node edge, and the
  contract is the only thing that crosses it.
- **An incomplete contract does not get analysed.** Running analysis on top of
  unresolved ambiguity is exactly the error this system exists to prevent, so
  the graph routes straight to the end and the gaps stay recorded.

## 2. The LLM proposes, the registry validates

Layer selection is a genuine judgement call, so a model makes it. It is also a
place where a plausible mistake is expensive, so the result is checked. Three
rules are enforced in code, not requested in the prompt:

| Rule | Why |
|---|---|
| A `capability_id` outside the catalogue is dropped | A hallucinated source must never reach the map |
| A layer whose family contradicts the contract's `target` is dropped | The user was interrupted specifically to make that choice |
| A layer whose temporality contradicts the request is dropped | "Since 2000" answered with today's perimeter is a different question |

And two floors are added back:

- Any data family the user explicitly chose is drawn even if the proposal
  omitted it — dropping one silently changes the answer they asked for.
- Any declared hazard object with no capability at all is reported as an
  `UnmetNeed` rather than substituted with something that looks similar.

If the model is unreachable, `deterministic_plan` produces the same shape from a
registry lookup and the demo continues.

## 3. Showcase area: Altadena, California

An unincorporated Los Angeles County community of roughly 42,000 people across
about 22 km², most of which burned in the **Eaton Fire of January 2025**. Chosen
over "Los Angeles" because it fits on one screen and because the event is
recent, severe, and unusually well documented by public agencies.

### Layers

All three are public, need no API key, and are snapshotted into
`backend/data/altadena/` by `scripts/fetch_showcase_data.py`.

| Layer | Source | Features | Family / temporality |
|---|---|---|---|
| Officially confirmed fire perimeters | LA County Dept. of Public Works, Eaton Fire perimeter as of 2025-01-21 | 20 polygons | `official_fire_perimeters` / snapshot |
| Satellite thermal detections | NOAA NESDIS Hazard Mapping System, daily archive, 8–9 Jan 2025 | 1,300 points | `satellite_hotspots` / snapshot |
| Historical fire perimeters | National Interagency Fire Center, InteragencyFirePerimeterHistory, ≥ 2000 | 9 polygons | `official_fire_perimeters` / historical |

**Why these three.** Together they put the central claim of the whole system on
a map for the first time. The confirmed perimeter is one polygon set an agency
verified. The satellite layer is over a thousand thermal detections, some of
them coarse GOES pixels, plainly spilling outside that perimeter. Choosing
between them does not change the resolution of the answer — it changes the
answer. That is the argument for asking the user, made visible.

The historical layer carries the same family tag as the current perimeters,
because it *is* agency-verified polygons; only `temporality` separates them.
Tagging it otherwise meant "official perimeters, since 2000" resolved to the
January snapshot instead of the archive.

### Honesty rules the snapshot has to keep

- **Provenance travels with the data.** Every file records its source, its
  as-of date, and when it was retrieved; every layer surfaces its caveat in the
  map legend. A snapshot presented without its origin is indistinguishable from
  invented data.
- **The dataset is pinned, and says so.** Every plan that uses the current
  layers carries a note that this is January 2025, not live. Sample questions
  are phrased against the event for the same reason: a question implying "right
  now" answered with month-old data is precisely the quiet mismatch this project
  exists to prevent.
- **Empty is a result.** A scope with no features returns zero and says so. It
  is never padded.
- **Truncation is reported.** Display is capped at 1,500 features per layer, and
  a capped layer is labelled so its count is not mistaken for a total.

### Refreshing the snapshot

```bash
cd backend && uv run python scripts/fetch_showcase_data.py
```

## 4. What is deliberately not here

- No spread modelling, fuels, exposure, vulnerability, evacuation, or
  suppression data. Those hazard objects exist in the taxonomy and are reported
  as `UnmetNeed` when a contract declares them — which is the honest answer, and
  the behaviour walkthrough scenario 3 was written to check.
- No analysis beyond spatial clipping. The layers are drawn, not interpreted.
  Ranking, exposure counts, and impact assessment belong to a later stage.
