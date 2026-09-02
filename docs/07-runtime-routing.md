# Runtime Routing and Where the System Stops to Ask

> What decides whether the contract pipeline runs at all, and the two places a
> turn can pause for a human. Recorded 2026-08-27, after an audit found that
> neither was documented anywhere — the architecture docs describe a request
> going straight into the graph, and by the time a request reaches the graph
> several decisions have already been taken.
>
> Code: `backend/src/wildfire_agent/request_intent.py`,
> `backend/src/wildfire_agent/conversation.py`, and `_run` in
> `backend/src/wildfire_agent/api.py`.

---

## 1. Why there is a router at all

`docs/01` §5.4 established a principle: **a fact that must hold on every run does
not belong in a prompt.** A model asked to remember a rule will sometimes not.

That principle was applied once, to data families. It has since been applied
repeatedly, and the accumulated result is a thin deterministic layer that reads
the user's own words and can override what the model made of them. Every check in
it exists because a model's paraphrase produced a wrong answer that no downstream
stage could recover from.

The checks are deliberately small. They do not classify the request — that is the
goal agent's work. They *veto* specific misreadings.

## 2. What runs before the graph

In order, inside `_run`:

**1. An outstanding fetch offer takes precedence.** If the previous turn ended
with an offer, this message is read as the answer to it (§4), not as a new
question — unless it plainly is one.

**2. An interrupted graph takes precedence.** If the graph is parked on a
clarification, the message is a `Command(resume=…)` and nothing is reclassified.
The same question is still being answered.

**3. A question about the system is answered here and returns.**
`_asks_capability_question` catches *"what can you do"*, *"what can I ask you"*,
*"who are you"*. It runs **before** the resolver, for three reasons: it saves the
call, it works under the mock provider where the resolver deliberately refuses to
guess, and the answer must not depend on how a model classified the turn. Both
failure modes it replaces were real — one phrasing reached the discussion prompt,
which is written for a result already on screen, and trailed off into what was
not displayed; another ran the whole pipeline and replied by asking which
geographic area was meant. `docs/06` "Start here" documents the behaviour.

Its anchoring is worth copying if you add a check like it. The bare *"what can I
do"* branch matches only at the end of a clause, because *"what can I do about
the debris flow risk"* is a question about a burn scar — and answering that with
a menu of capabilities is the worse failure of the two.

**4. Otherwise the turn is resolved against session context.**
`conversation.resolve_turn` returns a `relation` (`new_request`, `follow_up`,
`correction`), a `standalone_request` with references expanded, and the inherited
subject. This is a model call. **It raises under the mock provider** rather than
guessing what *"those places"* refers to from keyword rules — a deliberate
refusal, and the reason mock mode covers only a session's first turn.

**5. Three overrides, on the user's literal words.** Each can overrule step 4.

| Check | Overrides | Why |
|---|---|---|
| `_asks_post_fire_risk_question` | `discussion` → `analysis` | A question about a hazard nothing local carries still needs data. A restatement that inherited the previous turn's vocabulary does not get to answer "no" |
| `_archive_kind` / `_asks_archive_question` | `analysis` → `discussion` | *"What fires do you have data for?"* is answered from the session's own record of the catalogue. Running the pipeline would ask which area you meant |
| `_asks_national_fire_question` | supplies scope | A question about the country names no place, so geocoding has nothing to work with. The contiguous-US box is a constant, handed in as an already-resolved fact |

The national case is worth dwelling on, because the alternative was tried. Skipping
the pipeline produced a right answer with an empty reasoning panel, and fabricating
a contract would have been worse than an empty one. So the contract, the stages and
the reasoning are the real ones; only the scope is supplied rather than geocoded.

**6. Data-selection guards.** `request_intent.py` holds five regex checks —
`requests_fire`, `requests_weather`, `requests_air_quality`, `is_weather_only`,
`explicitly_excludes_fire`. Their job is narrow: **an LLM restatement must not be
able to turn a weather request into a fire-data request.** *"Weather only"* and
*"not about fires"* are honoured as written.

**7. Explicit fetch instructions are consent.** Asking in plain words for data to
be fetched is an instruction, not a remark about the map. It was previously read as
the latter and answered *"the analysis does not include ACS data"* — a refusal to
do the one thing asked. Consent given in words is recorded per source, so asking
for one source does not authorise the others.

Only after all seven does a request become graph input.

## 3. The risk this carries

Stated plainly, because it is the same risk `docs/01` §5.5 records for branch 3 of
the family rule, and for the same reason.

**These are keyword tests, so they are sensitive to rewording.** Two phrasings of
one intent can take different routes, and the user sees no sign of which. A guard
that fires when it should not is usually visible — the wrong kind of answer comes
back. A guard that fails to fire is not: the model's classification stands, and it
may be the one the guard existed to prevent.

They are still the right mechanism. A rule that must hold on every run cannot live
in a prompt, and a regex that is wrong on an unusual phrasing is better than an
instruction that is wrong unpredictably. But the coverage is what it is: anyone
adding a guard should assume the phrasings they did not think of are unguarded,
and anyone changing one should treat it as changing an answer.

## 4. Two places a turn stops for a human

The system interrupts in exactly two situations, and they are implemented in
different layers for a reason worth recording.

### 4.1 A blocking gap in the contract — inside the graph

`nodes.ambiguity_resolution` calls `interrupt()`. LangGraph checkpoints the whole
state, and the next message arrives as `Command(resume=…)`.

Every blocking slot is collected and asked in **one** batch, phrased for the user's
expertise (`docs/01` §3). What reaches this point is narrower than it used to be:
`target` no longer blocks (`docs/01` §5.5), and a spatial phrase that geocodes is
confirmed by drawing the buffer rather than by asking. What remains is chiefly an
ungroundable location — *"near my house"* — and slots that only certain intents
require: `comparison_basis` for decision support, `intervention` for evaluation,
`time_horizon` for prediction.

### 4.2 Permission to fetch from outside — after the graph

`PendingFill`, held in `api.py`. Its docstring says why it cannot use the graph's
mechanism: *"the fire rendering runs after the graph has finished, so it cannot use
`interrupt()`."* Same idea, implemented in the layer that owns the question — hold
the offer, ask, act on the answer at the start of the next turn.

It reaches the browser on the **same `clarification` event** as the graph's own
interrupt, so the frontend needs no second mechanism.

Four properties, each deliberate:

- **The answer is about the source, not the layer or the turn.** `PendingFill.key`
  is the source id. "Fetch the Census figures", asked again three turns later, is
  the same question and the user has already answered it.
- **A decision is recorded and not re-asked.** `FillDecisions` holds it for the
  session. Approve once and it stays approved; decline and you are not asked again.
- **Consent is never guessed.** `decide()` returns `True`, `False`, or **`None`**
  when the reply is neither. On `None` the offer stays open — and if the message
  was a real question, it is carried through to the pipeline and the offer is put
  again afterwards. The user's question gets its answer; the decision stays theirs.
- **The offer is only put when it would change the answer.** A fetch is not offered
  because a field is empty, but because the reply would print figures that are
  missing.

**What it leaves behind.** The Limits tab records who authorised it, which source
and vintage answered, what it supplied, and what is still missing from any source —
*"At your approval, ACS 5-year estimates, 2020-2024 was fetched … Still unavailable
from any source: building footprints, WUI boundary."* A capped fetch says the count
is the cap, not the total. A source that was reached and returned nothing is
reported differently from one that could not be reached.

### 4.3 Why this is the more important of the two

The blocking-gap interrupt asks the user to supply information. The consent gate
asks permission to take an action with consequences outside the process — a network
call to a third party, on the user's behalf, from a question they asked for another
reason.

`docs/01` §5.5 records the system moving from *asking* to *deciding and disclosing*
for data families. That direction has a limit, and this is where it stops: an
assumption can be disclosed and retracted after the fact, but a request already sent
cannot be. **Consent is the one thing the system will not assume.**

---

## 5. Known gaps in this document's subject

- The routing guards have no test for phrasings nobody thought of, by
  construction. `docs/06` §10 lists the behaviours known to be reachable only
  through unusual wording.
- `_is_a_new_question` decides whether an unclear reply to an offer is a question
  or noise. It is the least principled check here and the one most likely to be
  wrong on an unusual reply; the failure is recoverable — the offer is re-put.
- `MessageIn.text` is capped at 4,000 characters. The endpoint takes no
  credentials, so an unbounded field would turn one request into an unbounded bill.
  That cap is the only thing standing between the deployment and that, which is a
  deployment problem, not a routing one.
