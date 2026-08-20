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
import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from .llm import get_chat_model, is_mock

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

_SYSTEM_PROMPT = """You are the voice of a wildfire geospatial analyst.

You are given the facts the analysis already computed. Write the analyst's reply
to the user in English.

Absolute rules:
1. Use ONLY the supplied facts. Never introduce a number, percentage, date,
   place name, fire name, or dataset that is not in them.
2. Never upgrade an observation into a cause. A satellite label shows what was
   detected, not what happened or why.
3. Keep any uncertainty the facts record. If a caveat is supplied, carry its
   meaning; you may re-word it, you may not drop it.
4. If the facts do not answer what the user asked, say so plainly and state what
   they do cover. Do not fill the gap.
5. Answer the question that was actually asked, at the register requested.

Style: 2-4 sentences of plain, direct English. No bullet lists, no headings, no
restating the question back. Write as an analyst talking to a colleague, not as
a form letter. The reply is rendered as plain text, so write no markdown: no
asterisks, backticks, bullets, or headings."""

_DISCUSSION_PROMPT = """You are the voice of a wildfire geospatial analyst.

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
3. If answering would require data that is not present, say what is missing and
   what would be needed, rather than estimating it.
4. Do not claim an analysis was re-run. Nothing was recomputed for this turn.

Style: 2-5 sentences of plain, direct English, pitched to the expertise level
given. Explain the idea, do not lecture. The reply is rendered as plain text, so
write no markdown: no asterisks, backticks, bullets, or headings."""


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


async def narrate(
    *,
    question: str,
    facts: Any,
    fallback: str,
    expertise: str = "general",
) -> str:
    """Re-word computed facts as an answer to `question`.

    Returns `fallback` - the deterministic sentence - whenever the model is
    unavailable, errors, or produces a draft that fails verification.
    """
    if is_mock():
        return fallback
    human = (
        f"The user asked:\n{question}\n\n"
        f"Requested expertise level: {expertise}\n\n"
        f"Facts computed by the analysis:\n{_fact_corpus(facts)}\n\n"
        f"The deterministic summary of these facts reads:\n{fallback}\n\n"
        "Write the analyst's reply."
    )
    try:
        return verify_grounded(await _draft(_SYSTEM_PROMPT, human), [facts, fallback])
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
        draft: _Discussion = await model.ainvoke([("system", _DISCUSSION_PROMPT), ("human", human)])
        try:
            verify_layer_claims(draft.layers_referenced, corpus)
        except NarrationRejected as exc:
            human = f"{base}\n\nYour previous draft was rejected: {exc}. Rewrite it."
            continue
        return draft.text.strip()
    return _nothing_to_answer_from(displayed)
