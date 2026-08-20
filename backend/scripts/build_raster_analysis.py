#!/usr/bin/env python3
"""Execute a trusted two-date index-change operation and write browser-ready PNGs.

One engine serves every supported index. Which bands to read, how to combine
them, which direction counts as damage, and how to classify the result are all
declared in `INDEX_SPECS` - never inferred from a request and never generated.
Adding an index means adding a row there, not adding a code path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from _rasterkit import (
    DATE_RE,
    bounds,
    colorize,
    component_nearest,
    crop_box,
    cropped_transform,
    pixel_area_km2,
    point_pixel,
    read_band,
    write_rgba_png,
)

NDVI_STOPS = [
    {"value": -1.0, "label": "Low (−1.0 to −0.2)", "color": "#7f3b08"},
    {"value": -0.2, "label": "Sparse (−0.2 to 0.1)", "color": "#d8b365"},
    {"value": 0.1, "label": "Moderate (0.1 to 0.4)", "color": "#f5f5dc"},
    {"value": 0.4, "label": "Vegetated (0.4 to 0.7)", "color": "#5ab4ac"},
    {"value": 0.7, "label": "High (0.7 to 1.0)", "color": "#01665e"},
]
DELTA_STOPS = [
    {"value": -1.0, "label": "Large decrease (≤ −0.25)", "color": "#7f0000"},
    {"value": -0.25, "label": "Decrease (−0.25 to −0.10)", "color": "#d73027"},
    {"value": -0.10, "label": "Small decrease (−0.10 to −0.03)", "color": "#fc8d59"},
    {"value": -0.03, "label": "Little change (−0.03 to 0.03)", "color": "#f7f7f7"},
    {"value": 0.03, "label": "Small increase (0.03 to 0.10)", "color": "#91cf60"},
    {"value": 0.10, "label": "Increase (0.10 to 0.25)", "color": "#1a9850"},
    {"value": 0.25, "label": "Large increase (≥ 0.25)", "color": "#006837"},
]
NBR_STOPS = [
    {"value": -1.0, "label": "Very low (≤ −0.25)", "color": "#67001f"},
    {"value": -0.25, "label": "Low (−0.25 to 0.0)", "color": "#d6604d"},
    {"value": 0.0, "label": "Moderate (0.0 to 0.25)", "color": "#f7f7f7"},
    {"value": 0.25, "label": "High (0.25 to 0.5)", "color": "#7fbc41"},
    {"value": 0.5, "label": "Very high (≥ 0.5)", "color": "#276419"},
]
#: Key & Benson / USGS severity breaks for unscaled dNBR (prefire − postfire).
DNBR_STOPS = [
    {"value": -2.0, "label": "Enhanced regrowth, high (≤ −0.25)", "color": "#1a6f3c"},
    {"value": -0.25, "label": "Enhanced regrowth, low (−0.25 to −0.10)", "color": "#7fbc41"},
    {"value": -0.10, "label": "Unburned (−0.10 to 0.10)", "color": "#f7f7f7"},
    {"value": 0.10, "label": "Low severity (0.10 to 0.27)", "color": "#fee08b"},
    {"value": 0.27, "label": "Moderate-low severity (0.27 to 0.44)", "color": "#fdae61"},
    {"value": 0.44, "label": "Moderate-high severity (0.44 to 0.66)", "color": "#e34a33"},
    {"value": 0.66, "label": "High severity (≥ 0.66)", "color": "#7f0000"},
]

#: Every supported index. `bands` are 1-based GDAL band numbers.
#: `delta` names which direction the difference view is computed in, so that a
#: positive number always means "more damage" for that index.
INDEX_SPECS: dict[str, dict] = {
    "ndvi_firepred": {
        "label": "NDVI",
        "source": "FirePred band 1 (NDVI_last)",
        "kind": "scaled",
        "bands": [1],
        "delta": "last_minus_first",
        "value_stops": NDVI_STOPS,
        "delta_stops": DELTA_STOPS,
        "damage_sign": -1,
    },
    "ndvi_viirs": {
        "label": "NDVI",
        "source": "VIIRS_Day same-day (b2 − b1) / (b2 + b1)",
        "kind": "ratio",
        "bands": [2, 1],
        "delta": "last_minus_first",
        "value_stops": NDVI_STOPS,
        "delta_stops": DELTA_STOPS,
        "damage_sign": -1,
    },
    "nbr_viirs": {
        "label": "NBR",
        "source": "VIIRS_Day same-day (b2 − m11) / (b2 + m11)",
        "kind": "ratio",
        "bands": [2, 6],
        "delta": "first_minus_last",
        "value_stops": NBR_STOPS,
        "delta_stops": DNBR_STOPS,
        "damage_sign": 1,
    },
}


def _index_values(path: Path, spec: dict, scale_factor: float) -> tuple[np.ndarray, object]:
    """Read one date and reduce it to the spec's index, as a plain float array.

    A normalised difference is undefined where the two bands sum to zero and
    meaningless where either is negative, so both cases become NaN rather than
    a number that would survive into a statistic.
    """
    if spec["kind"] == "scaled":
        values, dataset = read_band(path, spec["bands"][0])
        return values * scale_factor, dataset
    numerator_band, denominator_band = spec["bands"]
    first, dataset = read_band(path, numerator_band)
    second, _ = read_band(path, denominator_band)
    total = first + second
    with np.errstate(invalid="ignore", divide="ignore"):
        values = (first - second) / total
    values[~np.isfinite(total) | (total == 0)] = np.nan
    values[(first < 0) | (second < 0)] = np.nan
    return values, dataset


def _class_breakdown(values: np.ndarray, stops: list[dict], pixel_area: float) -> list[dict]:
    """Share of the analysed footprint falling in each classification band."""
    thresholds = [float(stop["value"]) for stop in stops]
    indices = np.clip(np.searchsorted(thresholds, values, side="right") - 1, 0, len(stops) - 1)
    total = int(values.size)
    breakdown = []
    for index, stop in enumerate(stops):
        count = int((indices == index).sum())
        if not count:
            continue
        breakdown.append(
            {
                "label": stop["label"],
                "color": stop["color"],
                "pixels": count,
                "area_km2": round(count * pixel_area, 2),
                "percent": round(count / total * 100, 1) if total else 0.0,
            }
        )
    return breakdown


def build(args: argparse.Namespace) -> None:
    spec = INDEX_SPECS[args.index]
    before_raw, before_dataset = _index_values(args.first, spec, args.scale_factor)
    after_raw, after_dataset = _index_values(args.last, spec, args.scale_factor)
    if before_raw.shape != after_raw.shape:
        raise ValueError("Input rasters do not share the same grid shape")
    before_transform = tuple(before_dataset.GetGeoTransform())
    after_transform = tuple(after_dataset.GetGeoTransform())
    if (
        before_transform != after_transform
        or before_dataset.GetProjection() != after_dataset.GetProjection()
    ):
        raise ValueError("Input rasters do not share the same CRS and transform")

    burned_union = np.zeros(before_raw.shape, dtype=bool)
    for path in sorted(args.viirs_dir.glob("*.tif*")):
        match = DATE_RE.search(path.name)
        if not match or match.group("date") > args.end_date:
            continue
        values, dataset = read_band(path, 8)
        if values.shape != before_raw.shape or tuple(dataset.GetGeoTransform()) != before_transform:
            raise ValueError("Burned-area labels do not align with the index grid")
        burned_union |= np.isfinite(values) & (values != 0)
        dataset = None

    seed = point_pixel(before_transform, args.center_latitude, args.center_longitude)
    burned = component_nearest(burned_union, *seed)
    before = before_raw
    after = after_raw
    valid = burned & np.isfinite(before) & np.isfinite(after)
    if not valid.any():
        raise ValueError(
            f"No pixel inside the mapped burned area has a valid {spec['label']} "
            "value on both dates"
        )
    crop = crop_box(valid)
    if crop is None:
        raise ValueError("The analysis mask contains no valid pixels")
    row0, row1, col0, col1 = crop
    transform = cropped_transform(before_transform, row0, col0)
    projection = before_dataset.GetProjection()
    before_crop = before[row0:row1, col0:col1]
    after_crop = after[row0:row1, col0:col1]
    valid_crop = valid[row0:row1, col0:col1]
    if spec["delta"] == "first_minus_last":
        difference = before_crop - after_crop
    else:
        difference = after_crop - before_crop

    write_rgba_png(
        args.before_output,
        colorize(before_crop, valid_crop, spec["value_stops"]),
        transform,
        projection,
    )
    write_rgba_png(
        args.after_output,
        colorize(after_crop, valid_crop, spec["value_stops"]),
        transform,
        projection,
    )
    write_rgba_png(
        args.difference_output,
        colorize(difference, valid_crop, spec["delta_stops"]),
        transform,
        projection,
    )

    values = difference[valid_crop]
    latitude = (transform[3] + (transform[3] + (row1 - row0) * transform[5])) / 2
    pixel_area = pixel_area_km2(transform, latitude)
    # `damage_sign` makes "worse" the same direction for every index, so the
    # damaged share is one expression rather than a per-index branch.
    damaged = values * spec["damage_sign"]
    stats = {
        "valid_pixels": int(values.size),
        "valid_area_km2": round(float(values.size * pixel_area), 2),
        "mean_before": round(float(before_crop[valid_crop].mean()), 4),
        "mean_after": round(float(after_crop[valid_crop].mean()), 4),
        "mean_delta": round(float(values.mean()), 4),
        "median_delta": round(float(np.median(values)), 4),
        "p10_delta": round(float(np.quantile(values, 0.10)), 4),
        "p90_delta": round(float(np.quantile(values, 0.90)), 4),
        "negative_percent": round(float((values < -0.03).mean() * 100), 1),
        "little_change_percent": round(float((np.abs(values) <= 0.03).mean() * 100), 1),
        "positive_percent": round(float((values > 0.03).mean() * 100), 1),
        "damaged_percent": round(float((damaged > 0.03).mean() * 100), 1),
    }
    metadata = {
        "index": args.index,
        "index_label": spec["label"],
        "index_source": spec["source"],
        "difference_order": spec["delta"],
        "bounds": bounds(transform, col1 - col0, row1 - row0),
        "statistics": stats,
        "class_breakdown": _class_breakdown(values, spec["delta_stops"], pixel_area),
        "value_ranges": {
            "before": [
                round(float(before_crop[valid_crop].min()), 4),
                round(float(before_crop[valid_crop].max()), 4),
            ],
            "after": [
                round(float(after_crop[valid_crop].min()), 4),
                round(float(after_crop[valid_crop].max()), 4),
            ],
            "difference": [round(float(values.min()), 4), round(float(values.max()), 4)],
        },
        "palettes": {
            "ndvi": spec["value_stops"],
            "value": spec["value_stops"],
            "difference": spec["delta_stops"],
        },
        "scale_factor": args.scale_factor,
        "mask": "Cumulative VIIRS_Day band 8 connected BA object nearest event centre",
    }
    args.metadata_output.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", choices=sorted(INDEX_SPECS), default="ndvi_firepred")
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--last", type=Path, required=True)
    parser.add_argument("--viirs-dir", type=Path, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--center-longitude", type=float, required=True)
    parser.add_argument("--center-latitude", type=float, required=True)
    parser.add_argument("--scale-factor", type=float, required=True)
    parser.add_argument("--before-output", type=Path, required=True)
    parser.add_argument("--after-output", type=Path, required=True)
    parser.add_argument("--difference-output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
