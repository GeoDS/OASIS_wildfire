"""Use an LLM to recognise which local datasets can answer a request.

Only the metadata catalogue crosses the model boundary.  Dataset ids returned by
the model are validated against that catalogue before anything downstream can
open a file.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from .local_catalog import DatasetMetadata, LocalDataCatalog


class DatasetChoice(BaseModel):
    dataset_id: str = Field(description="Exact dataset_id from the supplied catalogue")
    relevance: str = Field(description="Why this dataset helps answer the request")
    usable_variables: list[str] = Field(
        default_factory=list,
        description="Variables explicitly present in metadata that the request can use",
    )
    caveats: list[str] = Field(
        default_factory=list,
        description="Limits visible in metadata; never infer pixel contents",
    )


class MetadataRecognition(BaseModel):
    interpreted_request: str
    required_variables: list[str] = Field(default_factory=list)
    selected_datasets: list[DatasetChoice] = Field(default_factory=list)
    missing_variables: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    selection_mode: Literal["llm", "deterministic_mock"] = "llm"


_SYSTEM_PROMPT = """You are testing local-data recognition for a wildfire analysis demo.

You receive a user request and a catalogue produced by trusted code. Select the local datasets
whose metadata supports the request.

Rules:
- Use only exact `dataset_id` values from the catalogue. Never invent a path or source.
- Treat metadata as evidence about availability, not evidence about raster pixel values.
- Match event name/id, spatial scope, time range, variable, format, and resolution when present.
- Select the minimum sufficient set. If complementary day/night or predictor/target variables are
  explicitly requested, select every required series.
- Put requested variables absent from the catalogue in `missing_variables`.
- If ambiguity would materially change the selection, ask one concise clarification question.
- State limitations visible in metadata, especially names/dates inferred from file paths.
"""


async def recognise_local_data(
    query: str,
    catalog: LocalDataCatalog,
) -> MetadataRecognition:
    """Return a validated metadata-only selection for ``query``."""
    # Keep provider imports lazy: catalog scanning and the offline baseline need
    # no LangChain/provider packages and remain independently testable.
    from .llm import is_mock, structured

    if is_mock():
        return deterministic_recognition(query, catalog)

    result: MetadataRecognition = await structured(MetadataRecognition).ainvoke(
        [
            ("system", _SYSTEM_PROMPT),
            (
                "human",
                "User request:\n"
                + query
                + "\n\nTrusted local metadata catalogue:\n"
                + json.dumps(catalog.prompt_payload(), ensure_ascii=False, indent=2),
            ),
        ]
    )
    result.selection_mode = "llm"
    return validate_recognition(result, catalog)


def validate_recognition(
    result: MetadataRecognition,
    catalog: LocalDataCatalog,
) -> MetadataRecognition:
    """Drop hallucinated/duplicate ids and preserve an audit note."""
    known = {dataset.dataset_id for dataset in catalog.datasets}
    selected: list[DatasetChoice] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for choice in result.selected_datasets:
        if choice.dataset_id not in known:
            rejected.append(choice.dataset_id)
            continue
        if choice.dataset_id in seen:
            continue
        seen.add(choice.dataset_id)
        selected.append(choice)
    result.selected_datasets = selected
    if rejected:
        result.limitations.append(
            "Model returned unknown dataset ids; code rejected them: " + ", ".join(rejected)
        )
    return result


_VARIABLE_ALIASES: dict[str, tuple[str, ...]] = {
    "VIIRS_Day": ("viirs day", "viirs_day", "daytime", "day time", "day", "白天", "日间"),
    "VIIRS_Night": (
        "viirs night",
        "viirs_night",
        "nighttime",
        "night time",
        "night",
        "夜间",
        "夜晚",
    ),
    "FirePred": ("firepred", "fire prediction", "spread prediction", "火势", "蔓延", "预测"),
    "ESRI_LULC": ("esri lulc", "lulc", "land cover", "土地覆盖", "地表覆盖"),
    "wind": ("wind", "wind speed", "wind field", "风速", "风场", "风向"),
    "weather": ("weather", "temperature", "humidity", "天气", "气温", "湿度"),
    "air_quality": ("air quality", "pm2.5", "空气质量", "烟雾", "烟尘"),
}


def _normalise(text: str) -> str:
    return " ".join(part for part in re.split(r"[^\w\u4e00-\u9fff]+", text.lower()) if part)


def _requested_variables(query: str) -> list[str]:
    lowered = _normalise(query)
    return [
        variable
        for variable, aliases in _VARIABLE_ALIASES.items()
        if any(_normalise(alias) in lowered for alias in aliases)
    ]


def _dataset_text(dataset: DatasetMetadata) -> str:
    values = [
        dataset.dataset_id,
        dataset.relative_path,
        dataset.kind,
        dataset.event_id or "",
        dataset.event_name or "",
        dataset.spatial_scope or "",
        dataset.time_start or "",
        dataset.time_end or "",
        " ".join(dataset.variables),
        " ".join(dataset.fields),
    ]
    return _normalise(" ".join(values))


def _event_matches(query: str, dataset: DatasetMetadata) -> bool:
    lowered = _normalise(query)
    event_id = _normalise(dataset.event_id or "")
    if event_id and event_id in lowered:
        return True
    ignored = {"fire", "area", "county", "co"}
    for phrase in (dataset.event_name or "", dataset.spatial_scope or ""):
        distinctive = {
            token
            for token in _normalise(phrase).split()
            if len(token) >= 4 and token not in ignored
        }
        if distinctive and distinctive.issubset(set(lowered.split())):
            return True
    return False


def deterministic_recognition(query: str, catalog: LocalDataCatalog) -> MetadataRecognition:
    """Transparent offline baseline used only when ``LLM_PROVIDER=mock``.

    It makes the catalogue pipeline demoable without claiming that keyword
    matching is model inference.
    """
    lowered = _normalise(query)
    requested = _requested_variables(query)
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", query))
    choices: list[tuple[int, DatasetMetadata]] = []

    for dataset in catalog.datasets:
        text = _dataset_text(dataset)
        score = 0
        if _event_matches(query, dataset):
            score += 8
        if years and any(year in text for year in years):
            score += 3

        dataset_variables = {_normalise(variable) for variable in dataset.variables}
        variable_match = not requested or any(
            _normalise(variable) in dataset_variables for variable in requested
        )
        if requested and variable_match:
            score += 6
        elif requested and not variable_match:
            continue

        query_tokens = {token for token in lowered.split() if len(token) >= 4}
        score += min(3, len(query_tokens.intersection(text.split())))
        if score:
            choices.append((score, dataset))

    if not choices and not requested:
        choices = [(1, dataset) for dataset in catalog.datasets]

    choices.sort(key=lambda pair: (-pair[0], pair[1].dataset_id))
    best_score = choices[0][0] if choices else 0
    selected = [dataset for score, dataset in choices if score >= max(1, best_score - 2)]
    available_variables = {
        variable for dataset in catalog.datasets for variable in dataset.variables
    }
    missing = [variable for variable in requested if variable not in available_variables]

    return MetadataRecognition(
        interpreted_request=query,
        required_variables=requested,
        selected_datasets=[
            DatasetChoice(
                dataset_id=dataset.dataset_id,
                relevance=(
                    f"Metadata matches event/time/variable terms; available variables: "
                    f"{', '.join(dataset.variables) or 'not declared'}."
                ),
                usable_variables=dataset.variables,
                caveats=dataset.notes,
            )
            for dataset in selected
        ],
        missing_variables=missing,
        limitations=[
            (
                "LLM_PROVIDER=mock: this result is a deterministic keyword baseline, "
                "not an LLM judgment."
            ),
            "Raster contents were not opened; selection uses metadata and path conventions only.",
        ],
        confidence=0.55 if selected else 0.2,
        selection_mode="deterministic_mock",
    )
