"""Recognition validation tests; no real provider call is made."""

from __future__ import annotations

from pathlib import Path

from wildfire_agent.local_catalog import DatasetMetadata, LocalDataCatalog
from wildfire_agent.local_recognition import (
    DatasetChoice,
    MetadataRecognition,
    deterministic_recognition,
    validate_recognition,
)


def _catalog() -> LocalDataCatalog:
    return LocalDataCatalog(
        root=Path("/private/demo-data"),
        datasets=[
            DatasetMetadata(
                dataset_id="local:full_data::24461771::VIIRS_Day",
                relative_path="full_data/24461771/VIIRS_Day",
                kind="geotiff_series",
                file_count=24,
                size_bytes=24,
                variables=["VIIRS_Day"],
                time_start="2020-09-04",
                time_end="2020-09-27",
                event_id="24461771",
                event_name="Bobcat Fire area",
            ),
            DatasetMetadata(
                dataset_id="local:full_data::24461771::VIIRS_Night",
                relative_path="full_data/24461771/VIIRS_Night",
                kind="geotiff_series",
                file_count=24,
                size_bytes=24,
                variables=["VIIRS_Night"],
                time_start="2020-09-04",
                time_end="2020-09-27",
                event_id="24461771",
                event_name="Bobcat Fire area",
            ),
        ],
    )


def test_mock_baseline_matches_semantic_chinese_variable_names():
    result = deterministic_recognition("Bobcat Fire 有哪些白天和夜间卫星影像？", _catalog())

    assert result.required_variables == ["VIIRS_Day", "VIIRS_Night"]
    assert [choice.dataset_id for choice in result.selected_datasets] == [
        "local:full_data::24461771::VIIRS_Day",
        "local:full_data::24461771::VIIRS_Night",
    ]
    assert result.selection_mode == "deterministic_mock"


def test_unknown_model_dataset_id_is_rejected():
    result = MetadataRecognition(
        interpreted_request="Bobcat Fire daytime data",
        selected_datasets=[
            DatasetChoice(
                dataset_id="local:made_up::weather",
                relevance="invented",
            ),
            DatasetChoice(
                dataset_id="local:full_data::24461771::VIIRS_Day",
                relevance="catalogued",
            ),
        ],
        confidence=0.8,
    )

    validated = validate_recognition(result, _catalog())

    assert [choice.dataset_id for choice in validated.selected_datasets] == [
        "local:full_data::24461771::VIIRS_Day"
    ]
    assert any("local:made_up::weather" in note for note in validated.limitations)
