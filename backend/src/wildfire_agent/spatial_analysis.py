"""Typed, allow-listed raster analysis plans and deterministic execution."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import settings
from .raster_layers import (
    _PREVIEW_ROOT,
    FireRasterPlan,
    RasterLayerError,
    _event_center,
    _gdal_python,
    _inside_root,
    raster_datasets,
)

OperationType = Literal[
    "select",
    "align_grids",
    "difference",
    "mask",
    "zonal_statistics",
]


class RasterInput(BaseModel):
    dataset: str
    variable: str
    band: int = Field(ge=1)
    band_description: str
    date: str
    temporal_meaning: str
    scale_factor: float


class AnalysisOperation(BaseModel):
    type: OperationType
    parameters: dict[str, Any] = Field(default_factory=dict)


class RasterAnalysisPlan(BaseModel):
    analysis_id: str
    subject_event_id: str
    subject_name: str
    operation: Literal["ndvi_change", "nbr_change"]
    index: Literal["ndvi_viirs", "ndvi_firepred", "nbr_viirs"]
    inputs: list[RasterInput]
    operations: list[AnalysisOperation]
    mask: Literal["cumulative_burned_area"]
    formula: str
    output_views: list[Literal["before", "after", "difference"]]


_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


#: What each operation reads, and how it is described to the user. `variable`
#: is the local dataset directory the two dated inputs come from.
_OPERATION_SPECS: dict[str, dict] = {
    "ndvi_change": {
        "index": "ndvi_viirs",
        "variable": "VIIRS_Day",
        "band": 2,
        "band_description": "VIIRS I2 near-infrared reflectance, paired with I1 red",
        "temporal_meaning": (
            "Both bands are same-day surface reflectance, so the index is a "
            "same-day observation rather than a value carried forward."
        ),
        "scale_factor": 1.0,
        "formula_name": "NDVI",
    },
    "ndvi_change_firepred": {
        "index": "ndvi_firepred",
        "variable": "FirePred",
        "band": 1,
        "band_description": "Most recently available NDVI carried by the daily FirePred stack",
        "temporal_meaning": (
            "The value is named NDVI_last in the local source; it is not claimed to be "
            "a same-day vegetation observation."
        ),
        "scale_factor": 0.0001,
        "formula_name": "NDVI_last",
    },
    "nbr_change": {
        "index": "nbr_viirs",
        "variable": "VIIRS_Day",
        "band": 2,
        "band_description": "VIIRS I2 near-infrared reflectance, paired with M11 shortwave infrared",
        "temporal_meaning": (
            "Both bands are same-day surface reflectance. dNBR is the pre-fire NBR "
            "minus the post-fire NBR, so a positive value means a larger drop."
        ),
        "scale_factor": 1.0,
        "formula_name": "NBR",
    },
}


def _dates_for(event_id: str, variable: str) -> list[str]:
    dataset = next(
        (
            item
            for item in raster_datasets()
            if item.event_id == event_id and item.variable == variable
        ),
        None,
    )
    return list(dataset.dates) if dataset else []


def compile_raster_analysis(
    request: str,
    fire_plan: FireRasterPlan,
    *,
    operation: str,
) -> RasterAnalysisPlan | None:
    """Compile one named operation into a data-only execution plan.

    Which operation a request asks for is decided once, in `analyses`. This
    module does not sniff wording: it is handed a name and turns it into a plan,
    which is why a second caller cannot disagree with the first about what the
    user meant.
    """
    if not fire_plan.dataset.event_id:
        return None
    spec = _OPERATION_SPECS[operation]
    public_operation = "ndvi_change" if operation.startswith("ndvi_change") else operation
    event_id = fire_plan.dataset.event_id

    available = _dates_for(event_id, spec["variable"])
    if len(available) < 2:
        raise RasterLayerError(
            f"This fire has fewer than two local {spec['variable']} dates for comparison"
        )

    explicit = [day for day in _DATE_RE.findall(request) if day in available]
    if len(explicit) >= 2:
        first_day, last_day = sorted(explicit[:2])
    else:
        first_day, last_day = available[0], available[-1]
    if first_day == last_day:
        raise RasterLayerError(
            f"{spec['formula_name']} change requires two distinct available dates"
        )

    inputs = [
        RasterInput(
            dataset=spec["variable"],
            variable=spec["formula_name"],
            band=spec["band"],
            band_description=spec["band_description"],
            date=day,
            temporal_meaning=spec["temporal_meaning"],
            scale_factor=spec["scale_factor"],
        )
        for day in (first_day, last_day)
    ]
    seed = {
        "event_id": event_id,
        "operation": public_operation,
        "index": spec["index"],
        "dates": [first_day, last_day],
        "mask": "cumulative_burned_area",
        "version": 2,
    }
    analysis_id = hashlib.sha256(json.dumps(seed, sort_keys=True).encode()).hexdigest()[:20]
    if public_operation == "nbr_change":
        formula = f"NBR({first_day}) - NBR({last_day})"
        difference_order = "first_minus_last"
    else:
        formula = f"{spec['formula_name']}({last_day}) - {spec['formula_name']}({first_day})"
        difference_order = "last_minus_first"
    return RasterAnalysisPlan(
        analysis_id=analysis_id,
        subject_event_id=event_id,
        subject_name=(fire_plan.dataset.event_name or event_id).removesuffix(" area"),
        operation=public_operation,  # type: ignore[arg-type]
        index=spec["index"],
        inputs=inputs,
        operations=[
            AnalysisOperation(
                type="select", parameters={"variable": f"{spec['variable']}.{spec['formula_name']}"}
            ),
            AnalysisOperation(type="align_grids", parameters={"policy": "exact_match"}),
            AnalysisOperation(type="difference", parameters={"order": difference_order}),
            AnalysisOperation(
                type="mask",
                parameters={"source": "VIIRS_Day cumulative BA through end date"},
            ),
            AnalysisOperation(
                type="zonal_statistics",
                parameters={"zone": "mapped burned-area footprint"},
            ),
        ],
        mask="cumulative_burned_area",
        formula=formula,
        output_views=["before", "after", "difference"],
    )


def _caveats(plan: RasterAnalysisPlan, metadata: dict[str, Any]) -> list[str]:
    """Caveats follow the actual provenance, so a source change cannot leave a stale one behind."""
    shared = [
        "Only pixels with valid values on both dates and inside the cumulative mapped BA footprint are summarized.",
        "Before–after change does not by itself establish wildfire causality.",
    ]
    if plan.index == "ndvi_firepred":
        return [
            "NDVI_last is the most recently available vegetation-index value carried by each daily FirePred stack; it is not presented as a same-day observation.",
            *shared,
        ]
    optical = (
        f"{metadata.get('index_label', 'The index')} is computed from same-day VIIRS surface "
        "reflectance, so cloud or smoke on either date removes those pixels rather than "
        "carrying an older value forward."
    )
    if plan.operation == "nbr_change":
        return [
            optical,
            "Severity classes use the USGS/Key & Benson dNBR breaks, which are calibrated against field surveys elsewhere and are applied here without local calibration.",
            *shared,
        ]
    return [optical, *shared]


def _analysis_tokens(plan: RasterAnalysisPlan) -> dict[str, str]:
    return {
        view: hashlib.sha256(f"{plan.analysis_id}:{view}".encode()).hexdigest()[:32]
        for view in plan.output_views
    }


def _source_file(event_id: str, dataset: str, day: str) -> Path:
    root = settings.resolved_local_data_root
    path = _inside_root(root / "full_data" / event_id / dataset / f"{day}_{dataset}.tif", root)
    if not path.exists():
        raise RasterLayerError(f"The requested local input is unavailable: {dataset} {day}")
    return path


def execute_raster_analysis(plan: RasterAnalysisPlan) -> dict[str, Any]:
    """Execute a validated plan with trusted GDAL/NumPy code, never generated code."""
    allowed = {"select", "align_grids", "difference", "mask", "zonal_statistics"}
    unknown = {operation.type for operation in plan.operations} - allowed
    if unknown:
        raise RasterLayerError(f"Unsupported raster operation(s): {', '.join(sorted(unknown))}")
    if plan.operation not in {"ndvi_change", "nbr_change"} or len(plan.inputs) != 2:
        raise RasterLayerError("The current executor supports two-date index-change plans")

    event = next(
        (
            dataset
            for dataset in raster_datasets()
            if dataset.event_id == plan.subject_event_id and dataset.variable == "VIIRS_Day"
        ),
        None,
    )
    if event is None:
        raise RasterLayerError("The analysis requires VIIRS_Day burned-area labels")
    center = _event_center(event)
    if center is None:
        raise RasterLayerError("The selected event has no catalogue centre")

    first, last = plan.inputs
    first_path = _source_file(plan.subject_event_id, first.dataset, first.date)
    last_path = _source_file(plan.subject_event_id, last.dataset, last.date)
    viirs_dir = _inside_root(
        settings.resolved_local_data_root / "full_data" / plan.subject_event_id / "VIIRS_Day",
        settings.resolved_local_data_root,
    )
    tokens = _analysis_tokens(plan)
    _PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    metadata_path = _PREVIEW_ROOT / f"{plan.analysis_id}.analysis.json"
    asset_paths = {view: _PREVIEW_ROOT / f"{token}.png" for view, token in tokens.items()}

    if not metadata_path.exists() or not all(path.exists() for path in asset_paths.values()):
        script = Path(__file__).resolve().parents[2] / "scripts" / "build_raster_analysis.py"
        if not script.exists():
            raise RasterLayerError("The trusted raster-analysis builder is unavailable")
        try:
            subprocess.run(
                [
                    _gdal_python(),
                    str(script),
                    "--index",
                    plan.index,
                    "--first",
                    str(first_path),
                    "--last",
                    str(last_path),
                    "--viirs-dir",
                    str(viirs_dir),
                    "--end-date",
                    last.date,
                    "--center-longitude",
                    str(center[0]),
                    "--center-latitude",
                    str(center[1]),
                    "--scale-factor",
                    str(first.scale_factor),
                    "--before-output",
                    str(asset_paths["before"]),
                    "--after-output",
                    str(asset_paths["after"]),
                    "--difference-output",
                    str(asset_paths["difference"]),
                    "--metadata-output",
                    str(metadata_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "Raster analysis failed").strip()
            raise RasterLayerError(detail[-700:]) from exc
        except subprocess.TimeoutExpired as exc:
            raise RasterLayerError("Raster analysis exceeded the 45 second demo limit") from exc

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    bounds = [float(value) for value in metadata["bounds"]]
    palette = metadata["palettes"]
    label = metadata.get("index_label", "NDVI")
    change_label = "burn severity (dNBR)" if plan.operation == "nbr_change" else f"{label} change"
    titles = {
        "before": f"{plan.subject_name} · {label} before",
        "after": f"{plan.subject_name} · {label} after",
        "difference": f"{plan.subject_name} · {change_label}",
    }
    dates = {"before": first.date, "after": last.date, "difference": last.date}
    source_note = metadata.get("index_source", "local raster input")
    difference_note = (
        f"{plan.formula}. Higher values mean a larger drop in reflectance between the "
        "two dates, classified with the USGS/Key & Benson severity breaks."
        if plan.operation == "nbr_change"
        else f"{plan.formula}. Red means lower {label}; green means higher {label}."
    )
    explanations = {
        "before": f"{source_note}, {first.date}.",
        "after": f"{source_note}, {last.date}.",
        "difference": f"{difference_note} The map is masked to the cumulative mapped BA footprint.",
    }
    layers = []
    for view in plan.output_views:
        stops = palette["difference" if view == "difference" else "ndvi"]
        layers.append(
            {
                "id": f"analysis_{plan.analysis_id}_{view}",
                "title": titles[view],
                "dataset_id": f"analysis:{plan.analysis_id}",
                "event_name": plan.subject_name,
                "variable": f"{plan.index}_{view}",
                "date": dates[view],
                "display_band": 1,
                "bounds": bounds,
                "image_url": f"/api/raster-previews/{tokens[view]}.png",
                "opacity": 0.84,
                "value_range": metadata["value_ranges"][view],
                "mask": "Cumulative TS-SatFire burned-area label through the end date",
                "source": f"full_data/{plan.subject_event_id}/FirePred band 1",
                "variable_label": change_label if view == "difference" else label,
                "legend_label": change_label if view == "difference" else label,
                "legend_color": "#f7f7f7",
                "legend_stops": stops,
                "explanation": explanations[view],
                "source_label": "TS-SatFire FirePred auxiliary input",
                "analysis_view": view,
            }
        )

    stats = metadata["statistics"]
    breakdown = metadata.get("class_breakdown", [])
    if plan.operation == "nbr_change":
        severe = sum(
            item["percent"]
            for item in breakdown
            if "Moderate-high" in item["label"] or "High severity" in item["label"]
        )
        unburned = sum(item["percent"] for item in breakdown if "Unburned" in item["label"])
        summary = (
            f"Across the {plan.subject_name} mapped burned-area footprint, mean dNBR is "
            f"{stats['mean_delta']:.3f} between {first.date} and {last.date}, which sits in the "
            f"moderate range on the USGS severity scale. {severe:.0f}% of the footprint burned at "
            f"moderate-high or high severity, and {unburned:.0f}% classifies as unburned despite "
            "falling inside the mapped area. Severity is inferred from a reflectance drop, not "
            "from an on-the-ground damage survey."
        )
    else:
        direction = "decreased" if stats["mean_delta"] < 0 else "increased"
        summary = (
            f"Inside the {plan.subject_name} mapped burned-area footprint, mean {label} "
            f"{direction} by {abs(stats['mean_delta']):.3f} between {first.date} and "
            f"{last.date}. {stats['negative_percent']:.0f}% of valid mapped pixels were lower "
            "in the later record. On the Difference view, red pixels have lower "
            f"{label} in the later record, and darker red means a larger decrease. This is a "
            "before–after association, not proof that fire alone caused every change."
        )
    return {
        "analysis_id": plan.analysis_id,
        "event_id": plan.subject_event_id,
        "event_name": plan.subject_name,
        "operation": plan.operation,
        "title": f"{plan.subject_name} {change_label}",
        "formula": plan.formula,
        "inputs": [item.model_dump(mode="json") for item in plan.inputs],
        "operations": [item.model_dump(mode="json") for item in plan.operations],
        "mask": plan.mask,
        "bounds": bounds,
        "layers": layers,
        "statistics": stats,
        "class_breakdown": breakdown,
        "index": plan.index,
        "index_source": source_note,
        "summary": summary,
        "default_view": "difference",
        "caveats": _caveats(plan, metadata),
    }
