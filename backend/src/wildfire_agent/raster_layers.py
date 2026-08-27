"""Safe local GeoTIFF discovery and California-boundary masked previews."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import settings
from .contract import AnalysisContract
from .local_catalog import DatasetMetadata, scan_local_data
from .planning.executor import validate_bbox
from .planning.models import LayerResult, LayerVisualization, PopupField
from .request_intent import requests_fire

_DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
_TOKEN_RE = re.compile(r"[0-9a-f]{32}")
_PREVIEW_ROOT = Path("/private/tmp/firescope-raster-previews")
_LIFECYCLE_ASSET_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})_(?:active_fire|burned_area)\.png")
_BOUNDARY_RELATIVE = Path("boundaries/california_state_2025/california_state_boundary_2025.shp")
_DISPLAY_BANDS = {
    "VIIRS_Day": 7,  # active-fire band (`af`)
    "VIIRS_Night": 7,
    "FirePred": 8,  # energy release component
    "ESRI_LULC": 1,
}


class RasterLayerError(ValueError):
    """A readable, expected raster-selection or processing failure."""


@dataclass(frozen=True)
class RasterDataset:
    dataset_id: str
    event_id: str | None
    event_name: str | None
    spatial_scope: str | None
    variable: str
    time_start: str | None
    time_end: str | None
    dates: list[str]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FireRasterPlan:
    """A backend-owned choice of local data for one named fire."""

    dataset: RasterDataset
    day: str
    presentation: str
    explanation: str


def _inside_root(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RasterLayerError("Local raster path escaped the configured data root") from exc
    return resolved


def _series_path(dataset: DatasetMetadata, root: Path) -> Path:
    return _inside_root(root / dataset.relative_path, root)


@lru_cache(maxsize=1)
def raster_datasets() -> tuple[RasterDataset, ...]:
    root = settings.resolved_local_data_root
    catalog = scan_local_data(root)
    output = []
    for dataset in catalog.datasets:
        if dataset.kind != "geotiff_series":
            continue
        series = _series_path(dataset, root)
        dates = sorted(
            {
                match.group("date")
                for file in series.glob("*.tif*")
                if (match := _DATE_RE.search(file.name))
            }
        )
        variable = dataset.variables[0] if dataset.variables else series.name
        output.append(
            RasterDataset(
                dataset_id=dataset.dataset_id,
                event_id=dataset.event_id,
                event_name=dataset.event_name,
                spatial_scope=dataset.spatial_scope,
                variable=variable,
                time_start=dataset.time_start,
                time_end=dataset.time_end,
                dates=dates,
            )
        )
    return tuple(sorted(output, key=lambda item: (item.event_name or "", item.variable)))


def raster_catalog_payload() -> dict[str, Any]:
    root = settings.resolved_local_data_root
    boundary = _inside_root(root / _BOUNDARY_RELATIVE, root)
    return {
        "datasets": [dataset.payload() for dataset in raster_datasets()],
        "mask": {
            "dataset_id": "local:" + _BOUNDARY_RELATIVE.as_posix().replace("/", "::"),
            "label": "California state boundary (2025)",
            "available": boundary.exists(),
            "crs": "EPSG:4326",
        },
    }


@lru_cache(maxsize=64)
def event_supports_burned_area(event_id: str, dates: tuple[str, ...]) -> bool:
    """Whether this event actually carries burned-area labels.

    The catalogue used to claim burned-area mapping for any event with a
    VIIRS_Day series, without ever looking inside band 8. Two events in the
    local subset carry thousands of active-fire pixels and no burned-area labels
    on any day, and a question about which cities they reached came back "the
    required mapped fire area was unavailable" - which reads as a transient
    failure rather than a gap that is permanent for that event.

    Only the last day is read. Burned area is accumulated through the selected
    date, so the final day is the maximum: if it is empty, every day is. That
    keeps this to one raster read per event, cached, rather than a full scan.
    """
    if not dates:
        return False
    try:
        return bool(burned_area_label_points(event_id, dates[-1]))
    except (RasterLayerError, FileNotFoundError, ValueError):
        # A raster that will not open is not evidence that labels exist.
        return False


def fire_event_catalog_payload() -> dict[str, Any]:
    """Group raster series into TS-SatFire events rather than generic places."""
    grouped: dict[str, dict[str, Any]] = {}
    for dataset in raster_datasets():
        if not dataset.event_id:
            continue
        event = grouped.setdefault(
            dataset.event_id,
            {
                "event_id": dataset.event_id,
                "event_name": dataset.event_name or dataset.event_id,
                "spatial_scope": dataset.spatial_scope,
                "dates": [],
                "variables": [],
                "task_support": {
                    "active_fire_detection": False,
                    "burned_area_mapping": False,
                    "next_day_prediction_inputs": False,
                    "model_prediction_output": False,
                },
            },
        )
        event["variables"].append(dataset.variable)
        if dataset.variable == "VIIRS_Day":
            event["dates"] = dataset.dates
            event["task_support"]["active_fire_detection"] = True
        elif dataset.variable == "FirePred":
            event["task_support"]["next_day_prediction_inputs"] = True
    events = sorted(grouped.values(), key=lambda item: (item["event_name"], item["event_id"]))
    for event in events:
        event["variables"] = sorted(set(event["variables"]))
        # Read from the data, not inferred from the variable being present.
        event["task_support"]["burned_area_mapping"] = event_supports_burned_area(
            event["event_id"], tuple(event["dates"])
        )
    return {
        "dataset": "TS-SatFire local subset",
        "event_count": len(events),
        "events": events,
        "notice": (
            "Each folder is a historical wildfire event. FirePred contains auxiliary "
            "model inputs, not a model prediction output."
        ),
    }


def _normalise_fire_name(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    ignored = {"fire", "area", "county", "co"}
    return " ".join(word for word in words if word not in ignored)


def _contract_search_text(contract: AnalysisContract) -> str:
    # Fire identity must come from the user's own wording.  A resolved city
    # slot is geographic context, not evidence that a same-named archive is
    # the subject of the question.
    return _normalise_fire_name(contract.analysis_request())


def _requested_day(contract: AnalysisContract, available: list[str]) -> str | None:
    """The day this contract asks for, or None to let the caller default.

    Two rules, and the order between them matters.

    **The request outranks the slot.** A date the user put in the question wins
    over whatever the time_horizon slot holds, the same way `plan_fire_raster`
    lets only the original wording authorise fire-data selection at all.

    **A span resolves to its end.** Every date is read, not the first one. A
    clarification answered "the 2020 incident" comes back carrying the event's
    whole date range, and taking the first match selected the fire's opening
    day - which, because burned area is cumulative, has none of it. "Which
    cities were affected" then answered "the required mapped fire area was
    unavailable" about a fire that had reached three. A question about a period
    asks what it came to, not what it started from.

    Membership is tested per date rather than once, so a date the archive lacks
    no longer discards a usable one later in the same sentence.
    """
    slot = contract.slots.get("time_horizon")

    def latest_in(text: str) -> str | None:
        found = [
            match.group("date")
            for match in _DATE_RE.finditer(text)
            if match.group("date") in available
        ]
        return max(found) if found else None

    asked = latest_in(" ".join([contract.analysis_request(), contract.restatement or ""]))
    return asked or latest_in(slot.value or "" if slot else "")


def plan_fire_raster(
    contract: AnalysisContract,
    datasets: tuple[RasterDataset, ...] | None = None,
) -> FireRasterPlan | None:
    """Match by fire identity, never by a coincidentally overlapping raster tile.

    The event name is the primary key. Location is deliberately not a fallback:
    asking about Eaton near Altadena must not silently select the nearby Bobcat
    archive merely because its two-degree source tile overlaps the request bbox.
    """
    # Only the original user wording can authorize fire-data selection. Slots
    # and LLM restatements may contain domain language even for "weather only".
    request = contract.analysis_request()
    if not requests_fire(request):
        return None
    available = datasets if datasets is not None else raster_datasets()
    search_text = f" {_contract_search_text(contract)} "
    request_lower = request.lower()
    archive_qualified = bool(
        re.search(r"\b(?:ts[- ]?satfire|historical|archive|dataset)\b", request_lower)
        or re.search(r"\b(?:2017|2018|2019|2020|2021)\b", request_lower)
    )
    event_groups: dict[str, list[RasterDataset]] = {}
    for dataset in available:
        if not dataset.event_name:
            continue
        # Four of the archived events are named for a place rather than for a
        # fire - Lake Hughes, Mojave / I-15, San Bernardino, Santa Barbara Co.
        # The old guard asked whether the *dataset's* name contained "fire",
        # which made all four unreachable: even "show the San Bernardino fire"
        # was dropped, and only adding a year rescued them.
        #
        # What the guard is for is telling "the San Bernardino fire" - a
        # historical event - from "is there a fire near San Bernardino" - a
        # question about now. That is in the user's words, so look there: the
        # event's name immediately followed by "fire" is someone naming an
        # incident. A fire word merely present in the sentence is not.
        named_fire = bool(re.search(r"\bfire\b", dataset.event_name, re.IGNORECASE))
        # Against the raw request, not `search_text`: that one strips the word
        # "fire" while normalising, so the pairing this looks for is gone by then.
        key = _normalise_fire_name(dataset.event_name)
        named_as_incident = bool(key) and bool(
            re.search(
                rf"\b{re.escape(key)}\b(?:\W+(?:co|county|area))?\W+fire\b",
                request_lower,
                re.IGNORECASE,
            )
        )
        id_qualified = bool(dataset.event_id and dataset.event_id.lower() in request_lower)
        if not (named_fire or named_as_incident or archive_qualified or id_qualified):
            continue
        event_groups.setdefault(dataset.event_name, []).append(dataset)

    matched: list[tuple[int, str, list[RasterDataset]]] = []
    for event_name, group in event_groups.items():
        key = _normalise_fire_name(event_name)
        if key and f" {key} " in search_text:
            matched.append((len(key), event_name, group))
    if matched:
        _, event_name, group = max(matched, key=lambda item: item[0])
    else:
        return None
    raw_text = " ".join([request, contract.restatement or "", *contract.task_intent]).lower()
    if any(token in raw_text for token in ("predict", "spread", "next", "risk", "forecast")):
        preferred = "VIIRS_Day"
        presentation = "historical fire progression labels"
        explanation = (
            "The archive contains observations and prediction inputs, but no model output. "
            "The map therefore shows historical AF/BA labels rather than presenting inputs as a forecast."
        )
    elif any(token in raw_text for token in ("land cover", "vegetation", "fuel type")):
        preferred = "ESRI_LULC"
        presentation = "land cover around the fire"
        explanation = (
            "Selected automatically because the question asks about the landscape around the fire."
        )
    else:
        preferred = "VIIRS_Day"
        presentation = "observed fire activity and burned area"
        explanation = "Selected automatically as the TS-SatFire historical record for this event."

    dataset = next((item for item in group if item.variable == preferred), None)
    if dataset is None:
        fallback_order = ("VIIRS_Day", "FirePred", "VIIRS_Night", "ESRI_LULC")
        dataset = next(
            (item for variable in fallback_order for item in group if item.variable == variable),
            None,
        )
    if dataset is None or not dataset.dates:
        return None
    # The day addresses the event's *daily* timeline, which the lifecycle renderer
    # always reads from VIIRS_Day. A variable such as ESRI_LULC carries a single
    # annual epoch, so taking the day from whichever variable the wording
    # preferred put that epoch on a daily axis and failed validation downstream.
    timeline = next(
        (item for item in group if item.variable == "VIIRS_Day" and item.dates),
        dataset,
    )
    requested_day = _requested_day(contract, timeline.dates)
    day = requested_day or timeline.dates[-1]
    return FireRasterPlan(
        dataset=dataset,
        day=day,
        presentation=presentation,
        explanation=explanation,
    )


def render_fire_raster(contract: AnalysisContract) -> dict[str, Any]:
    """Resolve and render local fire data without exposing source choices to the UI."""
    plan = plan_fire_raster(contract)
    if plan is None:
        return {
            "status": "no_match",
            "message": "No matching local archive was found for the named fire. No unrelated data was drawn.",
            "layer": None,
        }
    spatial = contract.spatial()
    bbox = spatial.resolved.bbox if spatial and spatial.resolved else None
    if not bbox:
        return {
            "status": "no_scope",
            "message": f"Matched {plan.dataset.event_name}, but the analysis has no resolved map area.",
            "layer": None,
        }
    try:
        layer = render_raster_preview(plan.dataset.dataset_id, bbox, day=plan.day)
    except RasterLayerError as exc:
        return {
            "status": "outside_scope",
            "message": (
                f"Matched {plan.dataset.event_name}, but its local fire data does not overlap "
                "the resolved analysis area. Nothing was drawn."
            ),
            "detail": str(exc),
            "layer": None,
        }
    event_label = (plan.dataset.event_name or "Local fire").removesuffix(" area")
    layer.update(
        {
            "title": f"{event_label} · {plan.presentation}",
            "variable_label": plan.presentation,
            "explanation": plan.explanation,
            "source_label": "Local fire archive",
        }
    )
    return {
        "status": "matched",
        "message": f"Matched {event_label}; the backend selected the local fire data and date.",
        "layer": layer,
    }


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Monotonic-chain hull around actual observation pixels."""
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(
        origin: tuple[float, float],
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
            second[0] - origin[0]
        )

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _event_center(dataset: RasterDataset) -> tuple[float, float] | None:
    match = re.search(
        r"(?P<lat>-?\d{1,2}(?:\.\d+)?),\s*(?P<lon>-?\d{1,3}(?:\.\d+)?)",
        dataset.spatial_scope or "",
    )
    if not match:
        return None
    return float(match.group("lon")), float(match.group("lat"))


@lru_cache(maxsize=1)
def _gdal_python() -> str:
    """Find a Python interpreter that owns the installed GDAL bindings."""
    candidates = [
        Path("/opt/homebrew/bin/python3"),
        Path("/usr/local/bin/python3"),
        Path(sys.executable),
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        try:
            subprocess.run(
                [str(candidate), "-c", "from osgeo import gdal"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        return str(candidate)
    raise RasterLayerError(
        "GDAL Python bindings are required to build TS-SatFire lifecycle previews"
    )


def _lifecycle_metadata_path(event: RasterDataset) -> Path:
    if not event.event_id:
        raise RasterLayerError("The selected raster series has no event id")
    center = _event_center(event)
    if center is None:
        raise RasterLayerError("The selected event has no catalogue centre")
    root = settings.resolved_local_data_root
    event_dir = _inside_root(root / "full_data" / event.event_id, root)
    if not event_dir.is_dir():
        raise RasterLayerError("The selected TS-SatFire event directory is unavailable")
    output_dir = _PREVIEW_ROOT / "lifecycle" / event.event_id
    metadata = output_dir / "metadata.json"
    if metadata.exists():
        return metadata
    script = Path(__file__).resolve().parents[2] / "scripts" / "build_fire_lifecycle_cache.py"
    if not script.exists():
        raise RasterLayerError("The lifecycle cache builder is unavailable")
    longitude, latitude = center
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                _gdal_python(),
                str(script),
                str(event_dir),
                str(output_dir),
                "--center-latitude",
                str(latitude),
                "--center-longitude",
                str(longitude),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Lifecycle cache build failed").strip()
        raise RasterLayerError(detail[-500:]) from exc
    except subprocess.TimeoutExpired as exc:
        raise RasterLayerError("Lifecycle cache build exceeded the 45 second demo limit") from exc
    if not metadata.exists():
        raise RasterLayerError("Lifecycle cache did not produce metadata")
    return metadata


def render_fire_lifecycle(event_id: str, *, day: str | None = None) -> dict[str, Any]:
    """Return AF/BA layers and daily metrics for one historical fire event."""
    observation = next(
        (
            item
            for item in raster_datasets()
            if item.event_id == event_id and item.variable == "VIIRS_Day"
        ),
        None,
    )
    if observation is None:
        raise RasterLayerError("Unknown TS-SatFire event id")
    metadata_path = _lifecycle_metadata_path(observation)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    dates = list(metadata.get("dates") or [])
    selected_day = day or (dates[-1] if dates else None)
    if selected_day not in dates:
        raise RasterLayerError("date must be one of the dates advertised by the event")
    bounds = [float(value) for value in metadata["bounds"]]
    event_name = (observation.event_name or event_id).removesuffix(" area")
    layer_specs = [
        (
            "burned_area",
            "Cumulative burned area label",
            "#5b536c",
            0.78,
            (
                "Cumulative union through this date of TS-SatFire's BA label. "
                "It is a historical dataset label, not burn severity."
            ),
        ),
        (
            "active_fire",
            "Active fire label",
            "#c76e00",
            0.92,
            (
                "Finite non-zero pixels in VIIRS_Day band 7 (AF) for this date. "
                "They are observations, not a perimeter or forecast."
            ),
        ),
    ]
    layers = []
    for kind, label, color, opacity, explanation in layer_specs:
        layers.append(
            {
                "id": f"lifecycle_{event_id}_{kind}_{selected_day}",
                "title": f"{event_name} · {label}",
                "dataset_id": observation.dataset_id,
                "event_name": event_name,
                "variable": kind,
                "date": selected_day,
                "display_band": 8 if kind == "burned_area" else 7,
                "bounds": bounds,
                "image_url": (f"/api/fire-lifecycle-assets/{event_id}/{selected_day}_{kind}.png"),
                "opacity": opacity,
                "value_range": [0.0, 1.0],
                "mask": "TS-SatFire event object nearest the catalogue event centre",
                "source": f"full_data/{event_id}/VIIRS_Day",
                "variable_label": label,
                "legend_label": label,
                "legend_color": color,
                "explanation": explanation,
                "source_label": "TS-SatFire historical labels",
            }
        )
    selected_metrics = next(
        item for item in metadata.get("timeline") or [] if item.get("date") == selected_day
    )
    return {
        "event_id": event_id,
        "event_name": event_name,
        "spatial_scope": observation.spatial_scope,
        "dates": dates,
        "selected_date": selected_day,
        "bounds": bounds,
        "layers": layers,
        "timeline": metadata.get("timeline") or [],
        "selected_metrics": selected_metrics,
        #: Area of one grid cell. Anything converting a pixel count into km²
        #: needs this, and recomputing it from the transform elsewhere is how
        #: two parts of the system come to disagree about the same footprint.
        "pixel_area_km2": float(metadata.get("pixel_area_km2") or 0.0),
        "source_label": "TS-SatFire local historical subset",
        "caveat": (
            "AF and BA are historical dataset labels. Mapped grid area is approximate; "
            "it is not an official acreage, current incident status, or forecast."
        ),
        "prediction_status": (
            "FirePred auxiliary inputs are available, but no trained-model output is loaded."
        ),
    }


def lifecycle_asset_path(event_id: str, filename: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_]+", event_id) or not _LIFECYCLE_ASSET_RE.fullmatch(filename):
        raise RasterLayerError("Invalid lifecycle asset path")
    root = (_PREVIEW_ROOT / "lifecycle").resolve()
    path = (root / event_id / "assets" / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RasterLayerError("Lifecycle asset escaped its cache root") from exc
    if not path.exists():
        raise RasterLayerError("Lifecycle asset not found")
    return path


@lru_cache(maxsize=256)
def _lifecycle_label_points(
    event_id: str,
    day: str,
    kind: str,
) -> tuple[tuple[float, float], ...]:
    """Return geographic centers of visible lifecycle-label pixels for one day."""
    if kind not in {"burned_area", "active_fire"}:
        raise RasterLayerError("Unknown lifecycle label kind")
    lifecycle = render_fire_lifecycle(event_id, day=day)
    west, south, east, north = map(float, lifecycle["bounds"])
    path = lifecycle_asset_path(event_id, f"{day}_{kind}.png")
    info = _gdal_info(path)
    width, height = map(int, info.get("size") or [0, 0])
    georeferenced = bool(info.get("geoTransform"))
    if width <= 0 or height <= 0:
        raise RasterLayerError("Burned-area preview has no readable dimensions")
    try:
        result = subprocess.run(
            [
                _tool("gdal_translate"),
                "-q",
                "-b",
                "4",
                "-of",
                "XYZ",
                str(path),
                "/vsistdout/",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RasterLayerError("Could not inspect cumulative burned-area label pixels") from exc
    points = []
    for line in result.stdout.splitlines():
        columns = line.split()
        if len(columns) != 3:
            continue
        try:
            pixel_x, pixel_y, alpha = map(float, columns)
        except ValueError:
            continue
        if alpha <= 0:
            continue
        if georeferenced:
            lon, lat = pixel_x, pixel_y
        else:
            row = -pixel_y - 0.5
            lon = west + (pixel_x / width) * (east - west)
            lat = north - ((row + 0.5) / height) * (north - south)
        points.append((lon, lat))
    return tuple(points)


def burned_area_label_points(event_id: str, day: str) -> tuple[tuple[float, float], ...]:
    """Return centers of cumulative burned-area label pixels for one day."""
    return _lifecycle_label_points(event_id, day, "burned_area")


def active_fire_label_points(event_id: str, day: str) -> tuple[tuple[float, float], ...]:
    """Return centers of same-day active-fire label pixels for one day."""
    return _lifecycle_label_points(event_id, day, "active_fire")


def _point_distance_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    mean_latitude = math.radians((first[1] + second[1]) / 2)
    east_west = (second[0] - first[0]) * math.cos(mean_latitude) * 111.32
    north_south = (second[1] - first[1]) * 111.32
    return math.hypot(east_west, north_south)


def _event_pixel_cluster(
    points: list[tuple[float, float]], center: tuple[float, float]
) -> list[tuple[float, float]]:
    """Choose the spatially continuous observation cluster nearest the event."""
    seed = min(points, key=lambda point: _point_distance_km(point, center))
    if _point_distance_km(seed, center) > 50:
        return []
    cluster = {seed}
    changed = True
    while changed:
        changed = False
        for point in points:
            if point in cluster:
                continue
            if any(_point_distance_km(point, member) <= 15 for member in cluster):
                cluster.add(point)
                changed = True
    return sorted(cluster)


def fire_activity_footprint_layer(plan: FireRasterPlan) -> LayerResult | None:
    """Derive a display footprint from non-null VIIRS observations.

    This is deliberately a fallback when an exact official perimeter is absent.
    It is an object-shaped map anchor, but it is never labelled as a burned-area
    or impact boundary.
    """
    observation = next(
        (
            item
            for item in raster_datasets()
            if item.event_id == plan.dataset.event_id and item.variable == "VIIRS_Day"
        ),
        None,
    )
    if observation is None or not observation.dates:
        return None
    day = (
        plan.day
        if plan.day in observation.dates
        else max(
            (candidate for candidate in observation.dates if candidate <= plan.day),
            default=observation.dates[-1],
        )
    )
    source, selected_day = _selected_file(observation, day)
    info = _gdal_info(source)
    band = min(_DISPLAY_BANDS["VIIRS_Day"], len(info.get("bands") or []))
    if band < 1:
        return None
    try:
        result = subprocess.run(
            [
                _tool("gdal_translate"),
                "-q",
                "-b",
                str(band),
                "-of",
                "XYZ",
                str(source),
                "/vsistdout/",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    points: list[tuple[float, float]] = []
    for line in result.stdout.splitlines():
        columns = line.split()
        if len(columns) != 3 or columns[2].lower() in {"nan", "inf", "-inf"}:
            continue
        try:
            lon, lat, value = map(float, columns)
        except ValueError:
            continue
        if math.isfinite(value):
            points.append((lon, lat))
    if not points:
        return None
    center = _event_center(observation)
    if center:
        points = _event_pixel_cluster(points, center)
    if not points:
        return None
    hull = _convex_hull(points)
    if len(hull) < 3:
        transform = info.get("geoTransform") or [0, 0.005, 0, 0, 0, -0.005]
        half_x = abs(float(transform[1])) / 2
        half_y = abs(float(transform[5])) / 2
        west = min(point[0] for point in points) - half_x
        east = max(point[0] for point in points) + half_x
        south = min(point[1] for point in points) - half_y
        north = max(point[1] for point in points) + half_y
        hull = [(west, south), (east, south), (east, north), (west, north)]
    ring = [[lon, lat] for lon, lat in [*hull, hull[0]]]
    fire_name = (observation.event_name or "Local fire").removesuffix(" area")
    feature = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {
            "name": fire_name,
            "date": selected_day,
            "observationPixels": len(points),
            "selection": "spatially connected VIIRS cluster nearest local event center",
            "geometryRole": "observed_activity_footprint",
        },
    }
    return LayerResult(
        capability_id="subject_fire_observed_footprint",
        title=f"{fire_name} observed activity footprint",
        hazard_object="active_fire",
        family="satellite_hotspots",
        geometry_type="Polygon",
        caveat=(
            "Convex hull of the spatially connected local VIIRS pixel cluster nearest "
            "the event center for this date; not an official perimeter, burned-area "
            "boundary, or uniform impact zone."
        ),
        feature_count=1,
        source="Derived from local VIIRS observation archive",
        as_of=selected_day,
        visualization=LayerVisualization(
            kind="fixed",
            label="Observed activity footprint",
            color="#d98a21",
            popup_fields=[
                PopupField(key="date", label="Observation date"),
                PopupField(key="observationPixels", label="VIIRS pixels"),
                PopupField(key="selection", label="Selection method"),
            ],
            explanation=(
                "Derived observation geometry; it is not an official perimeter or burn severity."
            ),
        ),
        geojson={"type": "FeatureCollection", "features": [feature]},
    )


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RasterLayerError(f"Required GDAL command is unavailable: {name}")
    return path


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "GDAL command failed").strip().splitlines()[-1]
        raise RasterLayerError(detail[:400]) from exc
    except subprocess.TimeoutExpired as exc:
        raise RasterLayerError("Raster mask exceeded the 60 second demo limit") from exc


def _gdal_info(path: Path, *, minmax: bool = False) -> dict:
    command = [_tool("gdalinfo"), "-json"]
    if minmax:
        command.append("-mm")
    command.append(str(path))
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RasterLayerError("Could not inspect local GeoTIFF metadata") from exc
    return json.loads(result.stdout)


def _wgs84_bbox(info: dict) -> tuple[float, float, float, float]:
    coordinates = ((info.get("wgs84Extent") or {}).get("coordinates") or [[]])[0]
    points = [point for point in coordinates if len(point) >= 2]
    if not points:
        raise RasterLayerError("GeoTIFF has no readable WGS84 extent")
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _intersection(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    bbox = (
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    )
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise RasterLayerError("Selected raster does not overlap the current request bbox")
    return bbox


def _selected_file(dataset: RasterDataset, day: str | None) -> tuple[Path, str]:
    root = settings.resolved_local_data_root
    metadata = next(
        item for item in scan_local_data(root).datasets if item.dataset_id == dataset.dataset_id
    )
    series = _series_path(metadata, root)
    selected_day = day or (dataset.dates[-1] if dataset.dates else None)
    if not selected_day or selected_day not in dataset.dates:
        raise RasterLayerError("date must be one of the dates advertised by the dataset")
    matches = sorted(series.glob(f"{selected_day}_*.tif*"))
    if not matches:
        raise RasterLayerError("Selected local GeoTIFF is unavailable")
    return _inside_root(matches[0], root), selected_day


def _colour_file(path: Path, minimum: float, maximum: float, *, hotspots: bool) -> None:
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        raise RasterLayerError("Raster has no finite values inside the requested area")
    if maximum <= minimum:
        maximum = minimum + 1.0
    span = maximum - minimum
    if hotspots:
        # The VIIRS `af` band uses its highest value as the ordinary background;
        # lower outliers are the useful anomaly pixels. Keep the background clear.
        lines = [
            f"{minimum} 249 65 68 235",
            f"{minimum + span * 0.33} 244 109 67 190",
            f"{minimum + span * 0.66} 254 224 139 75",
            f"{maximum} 0 0 0 0",
            "nv 0 0 0 0",
        ]
    else:
        lines = [
            f"{minimum} 254 240 138 45",
            f"{minimum + span * 0.33} 244 162 97 115",
            f"{minimum + span * 0.66} 232 93 62 190",
            f"{maximum} 114 29 61 235",
            "nv 0 0 0 0",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_raster_preview(
    dataset_id: str,
    bbox: tuple[float, float, float, float],
    *,
    day: str | None = None,
) -> dict[str, Any]:
    """Mask one local raster with California and the current request bbox."""
    request_bbox = validate_bbox(bbox)
    dataset = next(
        (item for item in raster_datasets() if item.dataset_id == dataset_id),
        None,
    )
    if dataset is None:
        raise RasterLayerError("Unknown or non-raster local dataset id")

    source, selected_day = _selected_file(dataset, day)
    source_info = _gdal_info(source)
    output_bbox = _intersection(request_bbox, _wgs84_bbox(source_info))
    band_count = len(source_info.get("bands") or [])
    band = min(_DISPLAY_BANDS.get(dataset.variable, 1), band_count)
    if band < 1:
        raise RasterLayerError("GeoTIFF contains no displayable bands")

    root = settings.resolved_local_data_root
    boundary = _inside_root(root / _BOUNDARY_RELATIVE, root)
    if not boundary.exists():
        raise RasterLayerError("California boundary mask is unavailable")

    cache_key = json.dumps(
        [
            "colour-v2",
            dataset_id,
            selected_day,
            output_bbox,
            source.stat().st_mtime_ns,
            band,
        ]
    )
    token = hashlib.sha256(cache_key.encode()).hexdigest()[:32]
    _PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    clipped = _PREVIEW_ROOT / f"{token}.tif"
    colours = _PREVIEW_ROOT / f"{token}.txt"
    preview = _PREVIEW_ROOT / f"{token}.png"

    if not preview.exists():
        west, south, east, north = output_bbox
        _run(
            [
                _tool("gdalwarp"),
                "-overwrite",
                "-of",
                "GTiff",
                "-srcband",
                str(band),
                "-te",
                str(west),
                str(south),
                str(east),
                str(north),
                "-te_srs",
                "EPSG:4326",
                "-cutline",
                str(boundary),
                "-cutline_srs",
                "EPSG:4326",
                "-dstnodata",
                "-9999",
                str(source),
                str(clipped),
            ]
        )
        clipped_info = _gdal_info(clipped, minmax=True)
        band_info = (clipped_info.get("bands") or [{}])[0]
        minimum = float(band_info.get("computedMin", 0.0))
        maximum = float(band_info.get("computedMax", minimum))
        _colour_file(
            colours,
            minimum,
            maximum,
            hotspots=dataset.variable.startswith("VIIRS"),
        )
        _run(
            [
                _tool("gdaldem"),
                "color-relief",
                str(clipped),
                str(colours),
                str(preview),
                "-alpha",
            ]
        )
    else:
        clipped_info = _gdal_info(clipped, minmax=True)
        band_info = (clipped_info.get("bands") or [{}])[0]
        minimum = float(band_info.get("computedMin", 0.0))
        maximum = float(band_info.get("computedMax", minimum))

    return {
        "id": f"raster_{token}",
        "title": f"Raster mask · {dataset.event_name or dataset.event_id} · {dataset.variable}",
        "dataset_id": dataset.dataset_id,
        "event_name": dataset.event_name,
        "variable": dataset.variable,
        "date": selected_day,
        "display_band": band,
        "bounds": list(output_bbox),
        "image_url": f"/api/raster-previews/{token}.png",
        "opacity": 0.72,
        "value_range": [minimum, maximum],
        "mask": "California state boundary (2025) + current request bbox",
        "source": str(source.relative_to(root)),
    }


def preview_path(token: str) -> Path:
    if not _TOKEN_RE.fullmatch(token):
        raise RasterLayerError("Invalid raster preview token")
    path = _PREVIEW_ROOT / f"{token}.png"
    if not path.exists():
        raise RasterLayerError("Raster preview not found")
    return path
