"""A named fire the archive does not hold is told so, not answered as a place.

QA, 2026-10-20: "What happened in the Eaton Fire?" was answered with current
conditions in Eaton Canyon - no perimeter there today, no thermal detections -
which reads as an answer about the fire and is not one. "What is the life cycle
of the Camp Fire?" asked which Camp Fire was meant. Neither is in the archive,
and that is the answer: said plainly, with what the archive does hold.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from wildfire_agent import api
from wildfire_agent.capability_overview import TOPICS
from wildfire_agent.conversation import ConversationResolutionError
from wildfire_agent.geocoding import _local_fire_gazetteer

ARCHIVE = [
    {"name": "Bobcat Fire", "first_day": "2020-09-04", "last_day": "2020-09-27"},
    {"name": "Lake Hughes", "first_day": "2020-08-11", "last_day": "2020-08-23"},
    {"name": "Mojave / I-15", "first_day": "2020-08-15", "last_day": "2020-08-19"},
    {"name": "Santa Barbara Co.", "first_day": "2017-07-07", "last_day": "2017-07-14"},
    {"name": "Woolsey Fire", "first_day": "2018-11-07", "last_day": "2018-11-16"},
]


@pytest.fixture
def archive(monkeypatch):
    datasets = [
        SimpleNamespace(
            event_name=name,
            spatial_scope=f"{name} | {lat}, {lon}",
            variables=["VIIRS_Day"],
            time_start=start,
            time_end=end,
        )
        for name, lat, lon, start, end in (
            ("Bobcat Fire area", 34.33, -117.93, "2020-09-04", "2020-09-27"),
            ("Lake Hughes area", 34.68, -118.56, "2020-08-11", "2020-08-23"),
            ("Mojave / I-15", 35.29, -115.59, "2020-08-15", "2020-08-19"),
            ("Santa Barbara Co.", 34.97, -120.28, "2017-07-07", "2017-07-14"),
            ("Woolsey Fire area", 34.12, -118.80, "2018-11-07", "2018-11-16"),
        )
    ]
    monkeypatch.setattr(
        "wildfire_agent.geocoding.scan_local_data",
        lambda _root: SimpleNamespace(datasets=datasets),
    )
    monkeypatch.setattr(api, "_archive_summary", lambda: ARCHIVE)
    _local_fire_gazetteer.cache_clear()
    yield
    _local_fire_gazetteer.cache_clear()


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("What happened in the Eaton Fire?", "Eaton"),
        ("What is the life cycle of the Camp Fire?", "Camp"),
        ("How big was the Dixie Fire?", "Dixie"),
        ("Show the Palisades fire", "Palisades"),
        ("Eaton Fire 怎么样了？", "Eaton"),
    ],
)
def test_a_fire_the_archive_does_not_hold_is_recognised(archive, text, name):
    assert api._unarchived_fire_named(text) == name


@pytest.mark.parametrize(
    "text",
    [
        # Archived, under every kind of name the archive uses.
        "What is the life cycle of the Woolsey Fire?",
        "Which way did the Bobcat Fire spread?",
        "Describe the lifecycle of the Mojave / I-15 fire",
        "What kind of land did the Lake Hughes fire burn?",
        "Show the Santa Barbara Co. fire",
        # One of them is archived: the pipeline answers what it can.
        "Compare the Bobcat Fire with the Eaton Fire",
        # About now, which current data can answer.
        "Is the Eaton Fire still burning?",
        "Is there a fire near Altadena right now?",
        "Are there any ongoing wildfires in the USA?",
        # Not an incident at all.
        "What is the wildfire risk around Santa Barbara?",
        "Pasadena fire risk this year",
        "Call the LA Fire Department",
        "What Fire is that?",
        "Show me fires near Yorba Linda",
        "Altadena 附近有什么火灾？",
    ],
)
def test_everything_else_is_left_to_the_pipeline(archive, text):
    assert api._unarchived_fire_named(text) is None


def _turn(monkeypatch, text: str) -> tuple[list[tuple[str, dict]], list[str]]:
    resolved: list[str] = []

    async def fake_resolve(question, _context):
        resolved.append(question)
        raise ConversationResolutionError("stop")

    class _Idle:
        async def aget_state(self, _config):
            return SimpleNamespace(values={}, next=(), tasks=())

    monkeypatch.setattr(api, "resolve_turn", fake_resolve)
    monkeypatch.setattr(api, "_graph", _Idle())
    session_id = "unarchived-fire"

    async def drain():
        return [
            (event["event"], json.loads(event["data"]))
            async for event in api._run(session_id, api.MessageIn(text=text))
        ]

    try:
        return asyncio.run(drain()), resolved
    finally:
        api._session_contexts.pop(session_id, None)


def test_the_answer_names_the_fire_and_what_the_archive_holds(archive, monkeypatch):
    events, resolved = _turn(monkeypatch, "What happened in the Eaton Fire?")
    names = [name for name, _ in events]
    assert names == ["turn", "summary", "suggestions"]
    assert resolved == [], "the pipeline ran anyway"
    assert events[0][1]["kind"] == "discussion"

    text = events[1][1]["text"]
    assert "Eaton Fire" in text
    assert "Eaton Canyon" not in text
    for event in ARCHIVE:
        assert event["name"] in text
    assert "2018" in text  # the year tells the user which fire each one is


def test_only_declared_wording_is_offered(archive, monkeypatch):
    """A suggestion is one click from being sent, so it has to be wording that works."""
    events, _ = _turn(monkeypatch, "What happened in the Eaton Fire?")
    declared = {topic.ask for topic in TOPICS if not topic.follow_up}
    items = dict(events)["suggestions"]["items"]
    assert items and all(item["ask"] in declared for item in items)


def test_a_question_in_chinese_is_answered_in_chinese(archive, monkeypatch):
    events, _ = _turn(monkeypatch, "Eaton Fire 怎么样了？")
    text = dict(events)["summary"]["text"]
    assert "Eaton Fire" in text and "归档" in text


def test_the_turn_is_remembered_for_the_next_one(archive, monkeypatch):
    seen = {}
    original = api._session_store.save_context

    def keep(session_id, context):
        seen[session_id] = context
        return original(session_id, context)

    monkeypatch.setattr(api._session_store, "save_context", keep)
    _turn(monkeypatch, "What happened in the Eaton Fire?")
    turns = seen["unarchived-fire"]["recent_turns"]
    assert [turn["role"] for turn in turns] == ["user", "agent"]


def test_an_archived_fire_still_reaches_the_pipeline(archive, monkeypatch):
    _, resolved = _turn(monkeypatch, "What is the life cycle of the Woolsey Fire?")
    assert resolved == ["What is the life cycle of the Woolsey Fire?"]
