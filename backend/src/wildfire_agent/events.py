"""The server-sent event contract between backend and frontend.

This module exists so the wire format has one definition rather than a set of
string literals scattered across `api.py`. Two people can build the producer and
the consumer against it without reading each other's code, and a renamed event
breaks a name lookup here instead of silently delivering nothing.

Every event is `event: <name>` with a JSON `data:` payload.

| event           | payload                                          | when |
|-----------------|--------------------------------------------------|------|
| `turn`          | `{kind}` - `analysis` or `discussion`             | first event of every turn |
| `stage`         | `{node, label, agent}`                           | a pipeline stage starts |
| `contract`      | the full `AnalysisContract`, plus `pending_slots` and `filled_ratio` | the contract changes |
| `clarification` | `{type, preamble, questions[], pending_slots[]}`  | the run pauses for the user |
| `plan`          | `ExecutionPlan` — `{layers[], unmet[], notes[]}`  | the Planning Agent has chosen |
| `layer`         | one `LayerResult`, including its GeoJSON          | once per fetched layer |
| `fire_data`     | backend fire-match status and explanation         | after a complete contract |
| `fire_lifecycle`| AF/BA layers, dates, and daily event metrics       | when a TS-SatFire event matches |
| `spatial_analysis`| typed derived-raster result, views, statistics, and provenance | after a supported calculation |
| `fire_context`  | land-cover composition, terrain, spread behaviour and fire weather | after a supported context analysis |
| `summary`       | `{text}`                                          | prose summary of the result |
| `done`          | the final `AnalysisContract`                      | the run finished |
| `error`         | `{message, type}`                                 | anything failed |

Notes for consumers:

- **Layers arrive one event each**, because each carries a full FeatureCollection
  and batching them produces a payload the browser has to swallow whole.
- **`clarification` is not terminal.** Reply on the same endpoint; the backend
  knows the session is parked on an interrupt and resumes it.
- **`done` fires even when the contract is not `ready_for_planning`.** In that
  case no `plan` or `layer` events precede it: an incomplete contract is
  deliberately not analysed.
- **`turn` always arrives first**, so a consumer knows before any payload whether
  this turn will replace the map. A `discussion` turn runs no pipeline: it emits
  `summary` and ends, and the previous result stays on screen.
- **The stream uses CRLF.** Blocks are separated by `\\r\\n\\r\\n`. A parser that
  splits only on `\\n\\n` receives nothing while the server logs a clean 200.
"""

from __future__ import annotations

from typing import Literal

EventName = Literal[
    "turn",
    "stage",
    "contract",
    "clarification",
    "plan",
    "layer",
    "fire_data",
    "fire_lifecycle",
    "spatial_analysis",
    "fire_context",
    "summary",
    "done",
    "error",
]

#: Every event name, for tests and for documentation generators.
EVENT_NAMES: tuple[str, ...] = (
    "turn",
    "stage",
    "contract",
    "clarification",
    "plan",
    "layer",
    "fire_data",
    "fire_lifecycle",
    "spatial_analysis",
    "fire_context",
    "summary",
    "done",
    "error",
)
