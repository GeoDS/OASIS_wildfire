# Telling the Frontend a Turn Changed Subject

> A contract change, and the reasoning behind it. The backend already knows
> whether a turn is about the same thing as the last one; today it keeps that to
> itself, and the map pays for it.

---

## 1. The symptom

Ask about one place, then ask about a different one. Until the new turn's first
layer payload arrives — geocoding, planning, raster rendering, several seconds —
the map keeps the previous place's layers on screen *and stays centred on them*.

Two behaviours are tangled here, and only one of them is a bug.

**Holding the old layers is deliberate.** `useSession.ts` marks each slot stale
on `turn` and replaces it when its own payload lands, rather than emptying
everything up front. Its comment says why: asking which cities a fire reached
re-sends that fire's whole lifecycle unchanged, and clearing first would make
the map blink for a turn that draws the same thing again.

**Staying centred on the old place is not.** `MapView.tsx` picks a viewport in
this priority order:

```
subject_* layer bounds  →  raster bounds  →  other layer bounds  →  resolved.center
```

The `contract` event carries the new `spatial.resolved.center` and arrives early,
as soon as geocoding finishes. But the old layers are still in state at that
moment, so `candidates.length` is non-zero and the effect re-fits to the *old*
bounds. The `resolved.center` branch is unreachable in exactly the case that
needs it.

## 2. The root cause

The frontend cannot distinguish **"same subject, refreshing"** from **"different
subject, replacing."** The stale-slot design is right for the first and wrong for
the second, and all it receives is `turn: {kind: "analysis"}`.

The backend already knows. `resolve_turn` returns `relation` (`new_request` /
`follow_up` / `correction`) and `inherited_subject`; `_run` computes
`prior_fire_event_id`. None of it reaches the wire.

## 3. The constraint that shapes the fix

`turn` is emitted **before the graph runs**, which is where geocoding happens. At
that moment the backend knows the resolver's verdict but **not the new
coordinates**. A viewport therefore cannot ride on `turn`.

So the split is: `turn` answers *did the subject change*, and the viewport
continues to come from `contract`, which already carries it.

## 4. The contract change

`turn`'s payload gains one field:

| event  | payload                    | when                      |
|--------|----------------------------|---------------------------|
| `turn` | `{kind, subject_changed}`  | first event of every turn |

- `kind` — `analysis` or `discussion`, unchanged.
- `subject_changed` — `true` when this turn is about something other than what
  the last one was about.

For a turn that went through the resolver, the rule is one expression:

```python
subject_changed = not resolution.inherited_subject
```

`relation` is deliberately **not** consulted. It is tempting to read
`correction` as "changed", but a correction can correct the *date* and keep the
fire — "no, I meant the 28th" must not wipe the map. `inherited_subject` is the
field that actually answers the question, and `resolve_turn` already forces it
to `False` on a `new_request`, so that case falls out for free.

Per emission site:

| emission site                                    | `subject_changed` | why |
|--------------------------------------------------|-------------------|-----|
| resolved turn, `inherited_subject == False`       | `true`  | a new fire or place, or a correction that replaced one |
| resolved turn, `inherited_subject == True`        | `false` | same subject; a follow-up or a correction to time or method |
| resuming a clarification interrupt                | `false` | mid-clarification is the same question |
| answering a fetch offer ("Fetch it")              | `false` | the second half of one question |
| `kind == "discussion"`                            | `false` | discussion turns draw nothing; the frontend already holds |

The first analysis turn of a session short-circuits to `new_request` with
`inherited_subject` at its `False` default, so it reports `true`. Nothing is on
screen to clear, so this is harmless.

## 5. What the frontend does with it

Two changes, both in `useSession.ts`. **`MapView.tsx` is not touched.**

- On `turn` with `subject_changed: true`, run every entry of the existing `slots`
  record immediately — `layers`, `rasters`, `fireDataStatus`, `fireLifecycle`,
  `spatialAnalysis`, `fireContext`, `plan` — instead of adding them to
  `staleRef`. The stale-slot path stays exactly as it is for
  `subject_changed: false`.
- Nothing else. Clearing the layers removes the competing bounds candidates, so
  `MapView`'s existing `resolved.center` fallback fires on its own the moment
  `contract` lands.

**The accepted trade.** On a subject change the map goes empty for a beat before
`contract` arrives and recentres. That is the intended behaviour: empty over the
right place beats populated over the wrong one. A turn that keeps its subject is
unaffected and still never blinks.

## 6. Testing

Backend:

- One case per row of the table above, asserting the emitted flag.
- A `correction` that inherits the subject reports `false`. This is the case the
  obvious reading of `relation` gets wrong, so it is worth its own test.
- `turn` is still the first event of every turn, and now always carries the field.
- `events.py` documents the new payload; the existing contract test covers it.

Frontend (for whoever implements section 5):

- `subject_changed: true` clears layers before any `layer` event arrives.
- `subject_changed: false` preserves the current stale-slot behaviour — an
  unchanged lifecycle re-sent by a follow-up must not blink.

## 7. Ownership

Sections 1–4 and 6 (backend) are on the backend side. Section 5 is a frontend
change and is specified here rather than made, per the division agreed on
2026-08-20.
