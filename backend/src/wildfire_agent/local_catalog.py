"""Build a compact, safe metadata catalogue for local wildfire data.

The model never receives arbitrary filesystem access.  Code scans one configured
root, reads metadata only, groups raster time series so hundreds of files do not
flood the prompt, and hands the model relative paths plus descriptive fields.

Supported in the first demo slice:

* GeoTIFF series organised as ``event/variable/YYYY-MM-DD_variable.tif``
* the lightweight ``Info_events.xlsx`` event index
* GeoJSON / JSON, CSV / TSV, and ZIP directory manifests

Raster pixels are deliberately not opened here.  This stage answers "what local
data appears relevant?", not "what does the raster contain?".
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import shapefile

_DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
_CELL_RE = re.compile(r"([A-Z]+)")
_SUPPORTED_TABLES = {".csv", ".tsv"}
_SUPPORTED_JSON = {".json", ".geojson"}
_SUPPORTED_RASTERS = {".tif", ".tiff"}
_SUPPORTED_SHAPEFILES = {".shp"}


@dataclass(frozen=True)
class EventMetadata:
    event_id: str
    event_name: str | None = None
    area: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    length_days: int | None = None


@dataclass
class DatasetMetadata:
    dataset_id: str
    relative_path: str
    kind: str
    file_count: int
    size_bytes: int
    variables: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    time_start: str | None = None
    time_end: str | None = None
    event_id: str | None = None
    event_name: str | None = None
    spatial_scope: str | None = None
    record_count: int | None = None
    geometry_types: list[str] = field(default_factory=list)
    source: str | None = None
    bbox: list[float] | None = None
    sample_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def prompt_dict(self) -> dict[str, Any]:
        """Compact representation; intentionally excludes absolute filesystem paths."""
        return {key: value for key, value in asdict(self).items() if value not in (None, [], "")}


@dataclass
class LocalDataCatalog:
    root: Path
    datasets: list[DatasetMetadata]
    warnings: list[str] = field(default_factory=list)
    scanned_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "catalog_version": "1.0",
            "root_label": self.root.name,
            "scanned_at": self.scanned_at.isoformat(),
            "datasets": [dataset.prompt_dict() for dataset in self.datasets],
            "warnings": self.warnings,
        }


def _dataset_id(relative_path: str) -> str:
    return "local:" + relative_path.strip("/").replace("/", "::")


def _safe_relative(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Path escaped configured data root: {path}") from exc


def _date_range(names: list[str]) -> tuple[str | None, str | None]:
    dates = sorted(match.group("date") for name in names if (match := _DATE_RE.search(name)))
    return (dates[0], dates[-1]) if dates else (None, None)


def _spatial_scope(event: EventMetadata | None) -> str | None:
    if event is None:
        return None
    parts = [part for part in (event.area, event.location) if part]
    return " | ".join(parts) or None


def scan_local_data(root: str | Path, *, max_files: int = 10_000) -> LocalDataCatalog:
    """Scan one local root and return metadata suitable for an LLM prompt.

    Symlinks that resolve outside ``root`` are ignored.  Large raster collections
    are grouped by parent directory, and ZIP members are listed without extraction.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(
            f"Local data root does not exist or is not a directory: {root_path}"
        )

    warnings: list[str] = []
    paths: list[Path] = []
    for path in root_path.rglob("*"):
        if len(paths) >= max_files:
            warnings.append(f"Scan stopped at {max_files} filesystem entries.")
            break
        if any(part.startswith(".") for part in path.relative_to(root_path).parts):
            continue
        if path.is_symlink():
            try:
                path.resolve().relative_to(root_path)
            except ValueError:
                warnings.append(f"Ignored symlink outside root: {path.relative_to(root_path)}")
                continue
        if path.is_file():
            paths.append(path)

    workbook_paths = [p for p in paths if p.suffix.lower() == ".xlsx"]
    events: dict[str, EventMetadata] = {}
    workbook_datasets: list[DatasetMetadata] = []
    for workbook in workbook_paths:
        try:
            sheets = read_xlsx_sheets(workbook)
        except (BadZipFile, KeyError, ET.ParseError, ValueError) as exc:
            warnings.append(f"Could not read workbook metadata for {workbook.name}: {exc}")
            continue
        for sheet_name, rows in sheets.items():
            if not rows:
                continue
            header = [str(value).strip() if value is not None else "" for value in rows[0]]
            rel = _safe_relative(workbook, root_path)
            workbook_datasets.append(
                DatasetMetadata(
                    dataset_id=_dataset_id(f"{rel}#{sheet_name}"),
                    relative_path=rel,
                    kind="xlsx_sheet",
                    file_count=1,
                    size_bytes=workbook.stat().st_size,
                    variables=["event_metadata"],
                    fields=[value for value in header if value],
                    record_count=max(0, len(rows) - 1),
                    notes=[f"Worksheet: {sheet_name}"],
                )
            )
            events.update(_event_rows(header, rows[1:]))

    raster_groups: dict[Path, list[Path]] = defaultdict(list)
    for path in paths:
        if path.suffix.lower() in _SUPPORTED_RASTERS:
            raster_groups[path.parent].append(path)

    datasets: list[DatasetMetadata] = []
    for parent, files in sorted(raster_groups.items(), key=lambda item: item[0].as_posix()):
        files.sort(key=lambda item: item.name)
        rel = _safe_relative(parent, root_path)
        event_id = parent.parent.name if parent.parent != root_path else None
        variable = parent.name
        event = events.get(event_id or "")
        start, end = _date_range([file.name for file in files])
        datasets.append(
            DatasetMetadata(
                dataset_id=_dataset_id(rel),
                relative_path=rel,
                kind="geotiff_series",
                file_count=len(files),
                size_bytes=sum(file.stat().st_size for file in files),
                variables=[variable],
                time_start=start or (event.start_date if event else None),
                time_end=end or (event.end_date if event else None),
                event_id=event_id,
                event_name=event.event_name if event else None,
                spatial_scope=_spatial_scope(event),
                sample_files=[file.name for file in files[:3]],
                notes=[
                    (
                        "Dates and variable names are inferred from folder/file names; "
                        "raster pixels were not opened."
                    )
                ],
            )
        )

    handled = set(workbook_paths) | {file for files in raster_groups.values() for file in files}
    for path in sorted((p for p in paths if p not in handled), key=lambda item: item.as_posix()):
        suffix = path.suffix.lower()
        try:
            if suffix in _SUPPORTED_JSON:
                datasets.append(_json_metadata(path, root_path))
            elif suffix in _SUPPORTED_TABLES:
                datasets.append(_table_metadata(path, root_path))
            elif suffix == ".zip":
                datasets.append(_zip_metadata(path, root_path))
            elif suffix in _SUPPORTED_SHAPEFILES:
                datasets.append(_shapefile_metadata(path, root_path))
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            BadZipFile,
            shapefile.ShapefileException,
        ) as exc:
            warnings.append(f"Could not inspect {path.name}: {exc}")

    datasets.extend(workbook_datasets)
    datasets.sort(key=lambda dataset: dataset.dataset_id)
    return LocalDataCatalog(root=root_path, datasets=datasets, warnings=warnings)


def _event_rows(header: list[str], rows: list[list[Any]]) -> dict[str, EventMetadata]:
    aliases = {
        "event_id": {"folder name", "folder", "event id", "event_id"},
        "event_name": {"event name", "name"},
        "area": {"area", "region"},
        "location": {"location (lat, lon)", "location", "lat lon"},
        "start_date": {"start date", "start_date"},
        "end_date": {"end date", "end_date"},
        "length_days": {"length (days)", "length", "days"},
    }
    normalized = {name: index for index, name in enumerate(value.lower() for value in header)}

    def value(row: list[Any], key: str) -> Any:
        index = next((normalized[name] for name in aliases[key] if name in normalized), None)
        return row[index] if index is not None and index < len(row) else None

    output: dict[str, EventMetadata] = {}
    for row in rows:
        event_id = value(row, "event_id")
        if event_id in (None, ""):
            continue
        raw_length = value(row, "length_days")
        try:
            length_days = int(raw_length) if raw_length not in (None, "") else None
        except (TypeError, ValueError):
            length_days = None
        event = EventMetadata(
            event_id=str(event_id),
            event_name=_string_or_none(value(row, "event_name")),
            area=_string_or_none(value(row, "area")),
            location=_string_or_none(value(row, "location")),
            start_date=_string_or_none(value(row, "start_date")),
            end_date=_string_or_none(value(row, "end_date")),
            length_days=length_days,
        )
        output[event.event_id] = event
    return output


def _string_or_none(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _json_metadata(path: Path, root: Path) -> DatasetMetadata:
    rel = _safe_relative(path, root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    provenance = payload.get("provenance", {}) if isinstance(payload, dict) else {}
    features = payload.get("features", []) if isinstance(payload, dict) else []
    fields: set[str] = set()
    geometry_types: set[str] = set()
    for feature in features[:100]:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or {}
        if isinstance(properties, dict):
            fields.update(str(key) for key in properties)
        geometry = feature.get("geometry") or {}
        if isinstance(geometry, dict) and geometry.get("type"):
            geometry_types.add(str(geometry["type"]))
    return DatasetMetadata(
        dataset_id=_dataset_id(rel),
        relative_path=rel,
        kind="geojson" if path.suffix.lower() == ".geojson" else "json",
        file_count=1,
        size_bytes=path.stat().st_size,
        variables=sorted(fields),
        fields=sorted(fields),
        time_start=_string_or_none(provenance.get("as_of")),
        time_end=_string_or_none(provenance.get("as_of")),
        record_count=len(features) if isinstance(features, list) else None,
        geometry_types=sorted(geometry_types),
        source=_string_or_none(provenance.get("source")),
        sample_files=[path.name],
    )


def _table_metadata(path: Path, root: Path) -> DatasetMetadata:
    rel = _safe_relative(path, root)
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    count = 0
    header: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter=delimiter)
        for count, row in enumerate(reader, start=0):
            if count == 0:
                header = [str(value) for value in row]
    return DatasetMetadata(
        dataset_id=_dataset_id(rel),
        relative_path=rel,
        kind="tsv" if delimiter == "\t" else "csv",
        file_count=1,
        size_bytes=path.stat().st_size,
        variables=header,
        fields=header,
        record_count=max(0, count),
        sample_files=[path.name],
    )


def _shapefile_metadata(path: Path, root: Path) -> DatasetMetadata:
    """Read a Shapefile bundle without exposing paths outside the allow-listed root."""
    rel = _safe_relative(path, root)
    reader = shapefile.Reader(str(path))
    try:
        fields = [str(field[0]) for field in reader.fields[1:]]
        geometry_type = str(reader.shapeTypeName)
        bbox = [float(value) for value in reader.bbox]
        record_count = len(reader)
        spatial_scope = None
        if record_count and "NAME" in fields:
            record = reader.record(0)
            spatial_scope = _string_or_none(record[fields.index("NAME")])
    finally:
        reader.close()

    sidecars = sorted(
        candidate for candidate in path.parent.glob(f"{path.stem}.*") if candidate.is_file()
    )
    readme = path.parent / "README.md"
    source = None
    if readme.exists():
        for line in readme.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("- source:"):
                source = line.split(":", 1)[1].strip()
                break

    return DatasetMetadata(
        dataset_id=_dataset_id(rel),
        relative_path=rel,
        kind="shapefile",
        file_count=len(sidecars),
        size_bytes=sum(candidate.stat().st_size for candidate in sidecars),
        variables=["boundary_mask"],
        fields=fields,
        spatial_scope=spatial_scope,
        record_count=record_count,
        geometry_types=[geometry_type],
        source=source,
        bbox=bbox,
        sample_files=[candidate.name for candidate in sidecars],
        notes=[
            "Shapefile bundle metadata was read from its SHP/DBF sidecars.",
            "bbox order is [west, south, east, north].",
        ],
    )


def _zip_metadata(path: Path, root: Path) -> DatasetMetadata:
    rel = _safe_relative(path, root)
    with ZipFile(path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
    suffix_counts = Counter(
        PurePosixPath(info.filename).suffix.lower() or "(none)" for info in members
    )
    variables = sorted(
        {
            PurePosixPath(info.filename).parent.name
            for info in members
            if PurePosixPath(info.filename).suffix.lower() in _SUPPORTED_RASTERS
        }
    )
    start, end = _date_range([PurePosixPath(info.filename).name for info in members])
    top_level = sorted({PurePosixPath(info.filename).parts[0] for info in members if info.filename})
    notes = [
        "Archive was not extracted.",
        "Member types: "
        + ", ".join(f"{suffix}={count}" for suffix, count in sorted(suffix_counts.items())),
    ]
    extracted = [name for name in top_level if (root / name).is_dir()]
    if extracted:
        notes.append(
            "Matching extracted directory exists; prefer its grouped datasets for access: "
            + ", ".join(extracted)
        )
    return DatasetMetadata(
        dataset_id=_dataset_id(rel),
        relative_path=rel,
        kind="zip_manifest",
        file_count=len(members),
        size_bytes=path.stat().st_size,
        variables=variables,
        time_start=start,
        time_end=end,
        sample_files=[info.filename for info in members[:3]],
        notes=notes,
    )


def _column_index(cell_reference: str) -> int:
    match = _CELL_RE.match(cell_reference)
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def read_xlsx_sheets(path: str | Path) -> dict[str, list[list[Any]]]:
    """Read displayed cell values from a simple XLSX using only the standard library.

    Formula evaluation is intentionally out of scope.  The event index is a
    small value-only table, so parsing its Open XML parts avoids adding a heavy
    spreadsheet dependency to the runtime demo.
    """
    workbook_path = Path(path)
    namespaces = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with ZipFile(workbook_path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("main:si", namespaces):
                shared.append(
                    "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
                )

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in relationships.findall("pkg:Relationship", namespaces)
        }

        sheets: dict[str, list[list[Any]]] = {}
        for sheet in workbook.findall("main:sheets/main:sheet", namespaces):
            name = sheet.attrib["name"]
            relation_id = sheet.attrib[f"{{{namespaces['rel']}}}id"]
            target = targets[relation_id].lstrip("/")
            xml_path = target if target.startswith("xl/") else f"xl/{target}"
            sheet_xml = ET.fromstring(archive.read(xml_path))
            rows: list[list[Any]] = []
            for row_node in sheet_xml.findall(".//main:sheetData/main:row", namespaces):
                values: list[Any] = []
                for cell in row_node.findall("main:c", namespaces):
                    index = _column_index(cell.attrib.get("r", "A1"))
                    while len(values) <= index:
                        values.append(None)
                    cell_type = cell.attrib.get("t")
                    value_node = cell.find("main:v", namespaces)
                    if cell_type == "inlineStr":
                        inline = cell.find("main:is", namespaces)
                        value: Any = (
                            "".join(
                                node.text or "" for node in inline.iter() if node.tag.endswith("}t")
                            )
                            if inline is not None
                            else ""
                        )
                    elif value_node is None:
                        value = None
                    elif cell_type == "s":
                        value = shared[int(value_node.text or "0")]
                    elif cell_type == "b":
                        value = value_node.text == "1"
                    else:
                        raw = value_node.text or ""
                        try:
                            number = float(raw)
                            value = int(number) if number.is_integer() else number
                        except ValueError:
                            value = raw
                    values[index] = value
                rows.append(values)
            sheets[name] = rows
    return sheets
