"""Grounded narration: the model chooses the words, the code owns the facts.

Every figure a user reads is computed by the analysis pipeline. This module
hands those computed facts to the model and asks it to write the reply, then
checks that the reply introduced no figure and no place name that the facts do
not contain. A rejected draft falls back to the deterministic sentence.

That ordering is deliberate. A mechanical sentence is a small failure; a fluent
sentence carrying a number nobody computed is the failure this whole project
exists to avoid.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from .capability_overview import FALLBACK_ASKS, fallback_answer
from .external_sources import catalogue
from .llm import get_chat_model, is_mock

logger = logging.getLogger(__name__)

#: Numbers written any of the ways prose writes them: 1116, 1,116, 276.9, 88%.
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: Capitalised words are candidate place names. Sentence-initial words and the
#: vocabulary the narrator legitimately uses are excluded below.
_PROPER_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,})\b", re.MULTILINE)

#: Domain and connective words that are capitalised without naming a place.
_NOT_A_PLACE = frozenset(
    {
        "Active",
        "Air",
        "Analysis",
        "August",
        "Burned",
        "Census",
        "Conditions",
        "Current",
        "December",
        "Difference",
        "Fire",
        "February",
        "Historical",
        "January",
        "July",
        "June",
        "March",
        "May",
        "Modeled",
        "Monday",
        "NDVI",
        "November",
        "October",
        "Quality",
        "September",
        "Satellite",
        "Separately",
        "The",
        "This",
        "Thursday",
        "Tuesday",
        "Wednesday",
        "Weather",
        "Friday",
        "Saturday",
        "Sunday",
        "April",
        "Conversely",
        "However",
        "Because",
        "Although",
        "While",
        "These",
        "Those",
        "There",
        "That",
        "When",
        "What",
        "Where",
        "Which",
        "Inside",
        "Outside",
        "Across",
        "Within",
        "During",
    }
)

_SYSTEM_PROMPT = """You are a wildfire geospatial analyst answering a colleague.

You are given the facts the analysis already computed. Write the reply.

Write the way a competent research assistant talks: lead with the answer, give
the figure that settles it, add the one thing they would get wrong if you left
it out. Not a form letter, not a disclaimer with a number buried in it.

Absolute rules:
1. Use ONLY the supplied facts. Never introduce a number, percentage, date,
   place name, fire name, or dataset that is not in them.
2. Never upgrade an observation into a cause. A satellite label shows what was
   detected, not what happened or why.
3. Carry the caveat that guards a figure you actually state. A share, a
   population, a pixel count each have a specific way of being misread, and the
   caveat that prevents it travels with the number. You may re-word it; you may
   not drop it and still quote the number.
4. Do NOT recite standing properties of the archive that no figure in your reply
   depends on. That there is no official perimeter, no trained-model output and
   no historical air quality is true of every answer this system gives. Saying
   it every time buries the one caveat that matters this time, and reads as the
   system talking about itself instead of answering. State it when the user asks
   what is missing, or when it changes how to read a number you just gave.
5. If the facts do not answer what the user asked, say so plainly and state what
   they do cover. Do not fill the gap.
6. When the missing thing is something this deployment can fetch, say so and name
   the source, rather than stopping at "not available". The user can authorise it;
   a flat refusal hides that the answer is one question away. Never claim to have
   fetched anything - you are writing the reply, not making the request.
7. Answer the question that was actually asked, at the register requested. Open
   with the variable they asked about. When the facts carry more than that, the
   rest follows as support in a later sentence - it does not go first, and a
   figure nobody asked for does not need to appear at all.
8. A template sentence built from the same facts may be supplied. It exists so
   you can check you have missed nothing. It is NOT a draft to reword. Do not
   follow its sentence order, do not mirror its phrasing, and do not repeat a
   caveat just because it appears there. Answering from the facts and happening
   to agree with it is right; paraphrasing it is not.

External sources this deployment can fetch on request:
{sources}

Style: 2-4 sentences of plain, direct English. No bullet lists, no headings, no
restating the question back. Do not open with "The available facts", "The
analysis shows", "The record indicates" or any other throat-clearing about the
data - open with the answer. One hedge where it is load-bearing beats a hedge in
every sentence. The reply is rendered as plain text, so write no markdown: no
asterisks, backticks, bullets, or headings."""

_DISCUSSION_PROMPT = """You are a wildfire geospatial analyst answering a colleague.

Write the way a competent research assistant talks: lead with the answer, then
the reasoning. Not a form letter, not a recital of what the system does and does
not hold.

The user is asking about the analysis already on their screen - what a term
means, why a method behaves as it does, how to read a result. No new analysis is
being run for this turn, so no new data is available to you.

You are given the session's structured context and the facts behind the result
currently displayed.

Absolute rules:
1. Answer from the supplied context and facts, plus general remote-sensing and
   wildfire domain knowledge that any analyst would state without looking
   anything up.
2. Never introduce a number, percentage, date, place name, or fire name that is
   not in the supplied material. General explanation is welcome; new specifics
   are not.
3. The context may carry a `fetched` section: what approved external fetches
   have already returned this session. Those figures are in hand. Answer from
   them. Telling the user data is unavailable while it sits in `fetched` is the
   worst answer this system can give - they were stopped and asked to authorise
   that fetch, and they said yes.
4. If answering would genuinely require data that is in neither the facts nor
   `fetched`, say what is missing and what would be needed, rather than
   estimating it.
5. Do not claim an analysis was re-run. Nothing was recomputed for this turn.
6. Do NOT recite standing properties of the archive that nothing in your answer
   depends on. Repeating what the dataset is not, every time, reads as the
   system talking about itself instead of answering the question.
7. When something genuinely missing is fetchable, name the source and say it can
   be fetched. "Not available" and "not available yet, and here is where it comes
   from" are different answers, and only the second one is true.
8. The context may carry an `archive` section: the historical fire events this
   deployment holds, each with its date span and whether it has burned-area
   labels or only active-fire detections. A question about what data exists is
   answered from it - name the fires. Do not answer it by describing the map.
9. Open with the variable they asked about. Other figures in the material follow
   as support if they help, and stay out if they do not. Asked about income, do
   not open with a population count.

External sources this deployment can fetch on request:
{sources}

Style: 2-5 sentences of plain, direct English, pitched to the expertise level
given. Do not open with "The available analysis", "The supplied facts" or any
other throat-clearing about the material - open with the answer. Explain the
idea, do not lecture. The reply is rendered as plain text, so write no markdown:
no asterisks, backticks, bullets, or headings."""


_CAPABILITY_PROMPT = """You are a wildfire geospatial analyst telling a colleague what you can do.

The user asked what this system is for - not about any fire, place, or result.
Nothing is on their screen and nothing needs to be. Do not describe the map, do
not say that no analysis is displayed, and do not ask which area they mean.

Rules:
1. Use only the supplied topics, archive and sources. Invent nothing.
2. Quote two or three `ask` examples verbatim - that wording is known to work -
   and list the same ones in `examples`. Not all nine; a full inventory is how a
   user gives up. Never put a topic marked `follow_up` in `examples`: its
   wording refers back to an earlier answer, so it means nothing on its own. You
   may still describe what it does in the prose.
3. Give the archive count and name two or three fires whose
   `has_burned_area_labels` is true. One with only active-fire detections cannot
   answer most of these topics, so offering it sends the user to a dead end.
4. Say that some sources are fetched only after they approve them, once per
   source. That is how the system behaves, not an apology.
5. If they asked something narrower, answer that from the same material instead
   of reciting everything.

Style: 4-8 sentences of plain English at the expertise level given. Lead with
what they can ask for. No markdown. Write examples inside a sentence, like: you
could ask "Show the Woolsey fire on 2018-11-16".
"""


def _with_sources(prompt: str) -> str:
    """Fold the live source catalogue into a prompt.

    The resolver was told what this deployment can fetch; the two prompts that
    actually write to the user were not. So a refusal read "that is not
    available" when the honest answer was "not yet - it comes from the Census
    ACS, and I can fetch it if you say so".
    """
    return prompt.format(sources=catalogue())


class NarrationRejected(RuntimeError):
    """The draft introduced material the computed facts do not support."""


class _Discussion(BaseModel):
    """A discussion reply plus a declaration of what it leans on.

    The model states which layers or datasets its answer treats as being on the
    user's screen, and that declaration is checked against what actually is.
    Asking for a form and verifying the form is the pattern this codebase uses
    everywhere else; it beats trying to infer the same claim back out of prose.
    """

    text: str = Field(min_length=1, description="The analyst's reply, in English.")
    layers_referenced: list[str] = Field(
        default_factory=list,
        description=(
            "Every layer, dataset, or variable this reply treats as currently "
            "displayed or computed - NDVI, FirePred, burned area, and so on. "
            "Leave empty when the reply is purely definitional and claims "
            "nothing about what is on screen. Do not list something you are "
            "telling the user is absent."
        ),
    )


class _CapabilityAnswer(BaseModel):
    """A capability reply plus the examples it led with.

    The examples are reported rather than parsed back out of the prose, so the
    options offered under the reply are the ones the reply actually named. They
    are still checked against the declared topics before being offered - see
    `capability_overview.suggestions_for`.
    """

    text: str = Field(description="The reply, plain text, no markdown")
    examples: list[str] = Field(
        default_factory=list,
        description=(
            "The example questions you quoted, copied verbatim from the `ask` "
            "fields you were given. Two or three. Omit any you did not quote."
        ),
    )


class _Draft(BaseModel):
    text: str = Field(min_length=1, description="The analyst's reply, in English.")


def _figures(text: str) -> set[str]:
    """Numeric tokens in `text`, normalised so 1,116 and 1116 compare equal."""
    found = set()
    for raw in _NUMBER_RE.findall(text):
        cleaned = raw.replace(",", "").rstrip(".")
        if not cleaned:
            continue
        # Trailing zeros are presentation, not a different quantity.
        if "." in cleaned:
            cleaned = cleaned.rstrip("0").rstrip(".")
        found.add(cleaned or "0")
    return found


def _places(text: str) -> set[str]:
    return {word for word in _PROPER_RE.findall(text) if word not in _NOT_A_PLACE}


def _fact_corpus(facts: Any) -> str:
    return json.dumps(facts, ensure_ascii=False, default=str)


def verify_grounded(text: str, facts: Any) -> str:
    """Return `text` unchanged, or raise if it outran the facts.

    Both checks are one-directional: prose may say less than the facts, never
    more. Dropping a figure is an editorial choice; adding one is fabrication.
    """
    corpus = _fact_corpus(facts)
    allowed_figures = _figures(corpus)
    unsupported = _figures(text) - allowed_figures
    if unsupported:
        raise NarrationRejected(
            f"introduced figures absent from the computed facts: {sorted(unsupported)}"
        )
    allowed_places = _places(corpus) | {word.title() for word in _places(corpus)}
    unknown = _places(text) - allowed_places
    if unknown:
        raise NarrationRejected(
            f"introduced names absent from the computed facts: {sorted(unknown)}"
        )
    return text


def _mentions(corpus: str, claim: str) -> bool:
    """Whether `claim` names something the corpus already contains.

    Capability ids are snake_case (`subject_fire_observed_footprint`) while a
    reply says "observed footprint", so a claim also matches when all of its
    words appear. The bias is deliberately lenient: rejecting a sound answer
    costs the user the answer entirely, while the prompt already states plainly
    what is on screen.
    """
    token = re.sub(r"[^a-z0-9]+", " ", claim.casefold()).strip()
    if not token:
        return False
    if token in corpus:
        return True
    return all(word in corpus for word in token.split())


def verify_layer_claims(claims: Iterable[str], corpus: Any) -> None:
    """Raise if the reply treats something as on screen that is not.

    This is the counterpart to `verify_grounded`: that one guards figures, this
    one guards the existence of what those figures would describe. A reply may
    explain NDVI in general; it may not imply an NDVI layer is displayed when
    the session never computed one.
    """
    haystack = _fact_corpus(corpus).casefold()
    unknown = [claim for claim in claims if not _mentions(haystack, claim)]
    if unknown:
        raise NarrationRejected(f"claimed layers that are not on screen: {sorted(unknown)}")


async def _draft(system: str, human: str) -> str:
    model = get_chat_model().with_structured_output(_Draft)
    draft: _Draft = await model.ainvoke([("system", system), ("human", human)])
    return draft.text.strip()


def verify_preserved(text: str, required: tuple[str, ...]) -> str:
    """Return `text` unchanged, or raise if it dropped an authorised figure.

    `verify_grounded` is deliberately one-directional: prose may say less than
    the facts, because trimming is the narrator's job. That holds for figures
    the analysis computed on its own. It does not hold for figures the user was
    stopped and asked to authorise a network fetch for - dropping those spends
    someone's consent on a reply they cannot tell apart from a refusal.

    One is enough, deliberately. The rule being enforced is that an approved
    fetch visibly changed the answer, not that every value it returned appears
    in every later reply. Demanding all of them rejected a sound answer about
    income for omitting the populations, and the fallback it dropped to was a
    paragraph about which cities were reached - not what was asked.
    """
    if not required:
        return text
    written = _figures(text)
    if not any(figure in written for figure in _figures(" ".join(required))):
        raise NarrationRejected(
            "used none of the figures the user approved a fetch for: "
            f"{sorted(_figures(' '.join(required)))}"
        )
    return text


async def narrate(
    *,
    question: str,
    facts: Any,
    fallback: str,
    expertise: str = "general",
    preserve: tuple[str, ...] = (),
) -> str:
    """Re-word computed facts as an answer to `question`.

    Returns `fallback` - the deterministic sentence - whenever the model is
    unavailable, errors, or produces a draft that fails verification.

    `preserve` names figures that arrived through a fetch the user approved.
    They are asked for in the prompt and checked in the draft, so an answer the
    user paid a question for cannot come back reading like the one they would
    have got by declining.
    """
    if is_mock():
        return fallback
    human = (
        f"The user asked:\n{question}\n\n"
        f"Requested expertise level: {expertise}\n\n"
        f"Facts computed by the analysis:\n{_fact_corpus(facts)}\n\n"
        "A template sentence generated from those same facts follows. It is a "
        "checklist, not a draft: use it to confirm you have missed nothing, then "
        "answer the question in your own order and your own words.\n"
        f"{fallback}\n\n"
    )
    if preserve:
        human += (
            "The user was asked to authorise an external fetch for these figures and "
            f"agreed, so every one of them must appear in your reply: {', '.join(preserve)}. "
            "Take an extra sentence or two if you need it; carry their caveat with them.\n\n"
        )
    human += "Answer the question."
    try:
        draft = verify_grounded(await _draft(_with_sources(_SYSTEM_PROMPT), human), [facts, fallback])
        return verify_preserved(draft, preserve)
    except Exception:  # noqa: BLE001 - rejection, provider error, timeout: all fall back
        return fallback


def _nothing_to_answer_from(displayed: list[str]) -> str:
    """The honest reply when the model cannot answer within what is on screen."""
    if not displayed:
        return (
            "Nothing is on the map yet, so there is no result for me to interpret. "
            "Ask for a fire or a place first and I will explain what comes back."
        )
    return (
        "I cannot answer that from what is currently displayed without guessing. "
        f"On screen right now: {', '.join(displayed)}. "
        "Ask me to compute the figure you want and I will run it."
    )


async def discuss(
    *,
    question: str,
    context: Any,
    facts: Any,
    displayed: list[str] | None = None,
    expertise: str = "general",
) -> str:
    """Answer a conversational turn about work already on screen.

    The reply declares which layers it treats as displayed, and that declaration
    is checked. A rejected draft is retried once with the objection stated, then
    gives way to a sentence that says plainly what is and is not available -
    telling the user nothing is better than telling them about a layer that does
    not exist.
    """
    displayed = displayed or []
    on_screen = ", ".join(displayed) if displayed else "nothing"
    base = (
        f"The user asked:\n{question}\n\n"
        f"Requested expertise level: {expertise}\n\n"
        f"Currently displayed on the user's map: {on_screen}. Nothing else is on "
        "screen. If the question is about something not in that list, say it is "
        "not displayed rather than describing it as if it were.\n\n"
        f"Session context:\n{_fact_corpus(context)}\n\n"
        f"Facts behind the result currently displayed:\n{_fact_corpus(facts)}\n\n"
        "Write the analyst's reply."
    )
    corpus = [displayed, context, facts]

    human = base
    for _attempt in range(2):
        model = get_chat_model().with_structured_output(_Discussion)
        draft: _Discussion = await model.ainvoke([("system", _with_sources(_DISCUSSION_PROMPT)), ("human", human)])
        try:
            verify_layer_claims(draft.layers_referenced, corpus)
        except NarrationRejected as exc:
            human = f"{base}\n\nYour previous draft was rejected: {exc}. Rewrite it."
            continue
        return draft.text.strip()
    return _nothing_to_answer_from(displayed)


async def describe_capabilities(
    *,
    question: str,
    facts: Any,
    expertise: str = "general",
) -> tuple[str, list[str]]:
    """Answer "what can you do?" from the declared inventory.

    Deliberately not routed through `discuss`. That prompt is built for a
    question about work already on screen - it is told to describe what is
    displayed and instructed *not* to recite standing properties of the archive.
    Both are right for a discussion turn and exactly wrong here, which is why
    "what can you do" used to trail off into what was not on the map.

    Returns the reply and the example questions it quoted, so the options offered
    beneath it are the ones it actually named rather than a second guess.
    """
    human = (
        f"The user asked:\n{question}\n\n"
        f"Requested expertise level: {expertise}\n\n"
        f"What this deployment can do:\n{_fact_corpus(facts)}\n\n"
        "Write the analyst's reply."
    )
    try:
        model = get_chat_model().with_structured_output(_CapabilityAnswer)
        answer: _CapabilityAnswer = await model.ainvoke(
            [("system", _with_sources(_CAPABILITY_PROMPT)), ("human", human)]
        )
    except Exception:
        # The question a reviewer asks first must not depend on a model call
        # landing. A reasoning model can spend its whole completion budget
        # thinking and return nothing, which arrives as an ordinary exception
        # and is indistinguishable from an outage - and the demo has to survive
        # both. Composed from the same facts, so the answer is the same answer.
        logger.exception("capability narration failed; composing from the facts")
        return fallback_answer(facts), list(FALLBACK_ASKS)
    text = answer.text.strip()
    if not text:
        return fallback_answer(facts), list(FALLBACK_ASKS)
    return text, list(answer.examples)
