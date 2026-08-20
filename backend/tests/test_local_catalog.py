"""Local metadata discovery tests; no provider, network, or raster library needed."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import shapefile

from wildfire_agent.local_catalog import scan_local_data


def _write_event_index(path: Path) -> None:
    rows = [
        [
            "Folder name",
            "Location (lat, lon)",
            "Area",
            "Event name",
            "Start date",
            "End date",
            "Length (days)",
        ],
        [
            "24461771",
            "34.33, -117.93",
            "Angeles NF",
            "Bobcat Fire area",
            "2020-09-04",
            "2020-09-27",
            24,
        ],
    ]

    def cell(column: str, row: int, value: object) -> str:
        if isinstance(value, int):
            return f'<c r="{column}{row}"><v>{value}</v></c>'
        return f'<c r="{column}{row}" t="inlineStr"><is><t>{value}</t></is></c>'

    letters = "ABCDEFG"
    row_xml = []
    for row_number, values in enumerate(rows, start=1):
        row_xml.append(
            f'<row r="{row_number}">'
            + "".join(cell(letters[index], row_number, value) for index, value in enumerate(values))
            + "</row>"
        )

    with ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="SoCal Events" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(row_xml)}</sheetData></worksheet>",
        )


def test_groups_rasters_and_joins_event_metadata(tmp_path: Path):
    _write_event_index(tmp_path / "Info_events.xlsx")
    series = tmp_path / "full_data" / "24461771" / "VIIRS_Night"
    series.mkdir(parents=True)
    (series / "2020-09-04_VIIRS_Night.tif").write_bytes(b"first")
    (series / "2020-09-27_VIIRS_Night.tif").write_bytes(b"second")

    catalog = scan_local_data(tmp_path)
    raster = next(dataset for dataset in catalog.datasets if dataset.kind == "geotiff_series")

    assert raster.dataset_id == "local:full_data::24461771::VIIRS_Night"
    assert raster.file_count == 2
    assert raster.variables == ["VIIRS_Night"]
    assert raster.time_start == "2020-09-04"
    assert raster.time_end == "2020-09-27"
    assert raster.event_name == "Bobcat Fire area"
    assert raster.spatial_scope == "Angeles NF | 34.33, -117.93"


def test_prompt_payload_never_exposes_absolute_root(tmp_path: Path):
    (tmp_path / "example.csv").write_text("date,value\n2020-01-01,1\n", encoding="utf-8")
    payload = scan_local_data(tmp_path).prompt_payload()
    rendered = json.dumps(payload)

    assert str(tmp_path) not in rendered
    assert payload["root_label"] == tmp_path.name
    assert payload["datasets"][0]["relative_path"] == "example.csv"


def test_geojson_provenance_and_shape_are_catalogued(tmp_path: Path):
    (tmp_path / "fires.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "provenance": {"source": "County GIS", "as_of": "2025-01-21"},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"incident": "Eaton"},
                        "geometry": {"type": "Point", "coordinates": [-118.1, 34.2]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dataset = scan_local_data(tmp_path).datasets[0]
    assert dataset.source == "County GIS"
    assert dataset.time_start == "2025-01-21"
    assert dataset.record_count == 1
    assert dataset.geometry_types == ["Point"]
    assert dataset.fields == ["incident"]


def test_zip_is_inspected_without_extraction(tmp_path: Path):
    archive_path = tmp_path / "sample.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("full_data/event/VIIRS_Day/2020-01-01_VIIRS_Day.tif", b"raster")
        archive.writestr("full_data/event/VIIRS_Day/2020-01-02_VIIRS_Day.tif", b"raster")

    dataset = scan_local_data(tmp_path).datasets[0]
    assert dataset.kind == "zip_manifest"
    assert dataset.file_count == 2
    assert dataset.variables == ["VIIRS_Day"]
    assert dataset.time_start == "2020-01-01"
    assert dataset.time_end == "2020-01-02"
    assert not (tmp_path / "full_data").exists()


def test_shapefile_bundle_is_catalogued_as_a_boundary_mask(tmp_path: Path):
    path = tmp_path / "california.shp"
    with shapefile.Writer(str(path), shapeType=shapefile.POLYGON) as writer:
        writer.field("NAME", "C")
        writer.poly(
            [[[-124.0, 32.5], [-114.0, 32.5], [-114.0, 42.0], [-124.0, 42.0], [-124.0, 32.5]]]
        )
        writer.record("California")

    dataset = next(item for item in scan_local_data(tmp_path).datasets if item.kind == "shapefile")

    assert dataset.variables == ["boundary_mask"]
    assert dataset.geometry_types == ["POLYGON"]
    assert dataset.spatial_scope == "California"
    assert dataset.bbox == [-124.0, 32.5, -114.0, 42.0]
    assert dataset.record_count == 1
