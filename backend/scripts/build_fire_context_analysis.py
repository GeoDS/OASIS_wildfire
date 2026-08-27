#!/usr/bin/env python3
"""Trusted per-day context analyses over one fire's mapped burned area.

Three questions share one traversal of the archive, because all three need the
same thing first: the cumulative burned-area object for this event, day by day.

    composition   what kind of land the fire burned
    spread        how far and in which direction it moved each day
    weather       the fire-weather conditions over the footprint as it moved

Everything is read from bands that already sit in the local files. Nothing is
resampled, and no value is carried across a day boundary.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from _rasterkit import component_nearest, dated_files, pixel_size_km, point_pixel, read_band

#: MODIS IGBP classes as carried by FirePred band 14 (LC_Type1).
LAND_COVER_CLASSES: dict[int, str] = {
    1: "Evergreen needleleaf forest",
    2: "Evergreen broadleaf forest",
    3: "Deciduous needleleaf forest",
    4: "Deciduous broadleaf forest",
    5: "Mixed forest",
    6: "Closed shrubland",
    7: "Open shrubland",
    8: "Woody savanna",
    9: "Savanna",
    10: "Grassland",
    11: "Permanent wetland",
    12: "Cropland",
    13: "Urban and built-up",
    14: "Cropland / natural vegetation mosaic",
    15: "Permanent snow and ice",
    16: "Barren",
    17: "Water",
}

LAND_COVER_COLORS: dict[int, str] = {
    1: "#1b5e20",
    2: "#2e7d32",
    3: "#388e3c",
    4: "#43a047",
    5: "#66bb6a",
    6: "#8d6e63",
    7: "#a1887f",
    8: "#9ccc65",
    9: "#c5e1a5",
    10: "#dce775",
    11: "#4dd0e1",
    12: "#ffb74d",
    13: "#78909c",
    14: "#ffd54f",
    15: "#eceff1",
    16: "#bcaaa4",
    17: "#4fc3f7",
}

#: FirePred bands sampled over the footprint. The forecast bands (15-19) are
#: excluded because they are not on the same scale as their observed
#: counterparts - forecast wind direction runs -89 to 87, which is not a bearing.
#:
#: Precipitation reads zero for every day of the Bobcat Fire, which is a dry
#: September rather than a dead band: sampling across all nine events finds real
#: values up to 6.8 mm. Reporting it is right; concluding from one fire that the
#: band was degenerate was not.
WEATHER_BANDS: dict[str, dict] = {
    "precipitation_mm": {"band": 3, "label": "Precipitation", "unit": "mm"},
    "wind_speed": {"band": 4, "label": "Wind speed", "unit": "m/s"},
    "wind_direction": {"band": 5, "label": "Wind direction", "unit": "°", "circular": True},
    "max_temperature_c": {"band": 7, "label": "Max temperature", "unit": "°C", "offset": -273.15},
    "energy_release_component": {"band": 8, "label": "Energy release component", "unit": "index"},
    "specific_humidity": {"band": 9, "label": "Specific humidity", "unit": "kg/kg"},
    "drought_index_pdsi": {"band": 13, "label": "Palmer drought severity", "unit": "index"},
}

TERRAIN_BANDS: dict[str, dict] = {
    "slope_deg": {"band": 10, "label": "Slope", "unit": "°"},
    "aspect_deg": {"band": 11, "label": "Aspect", "unit": "°", "circular": True},
    "elevation_m": {"band": 12, "label": "Elevation", "unit": "m"},
}


def _circular_mean(degrees: np.ndarray) -> float:
    """Mean of a bearing. Averaging 350° and 10° arithmetically gives 180°, which
    would be the exact opposite direction, so the mean is taken on the unit circle."""
    radians = np.radians(degrees)
    return float(
        np.degrees(math.atan2(float(np.sin(radians).mean()), float(np.cos(radians).mean()))) % 360
    )


def _summarize(values: np.ndarray, spec: dict) -> dict | None:
    finite = values[np.isfinite(values)]
    if not finite.size:
        return None
    finite = finite + spec.get("offset", 0.0)
    if spec.get("circular"):
        return {
            "label": spec["label"],
            "unit": spec["unit"],
            "mean": round(_circular_mean(finite), 1),
        }
    return {
        "label": spec["label"],
        "unit": spec["unit"],
        "mean": round(float(finite.mean()), 3),
        "min": round(float(finite.min()), 3),
        "max": round(float(finite.max()), 3),
    }


def _bearing(from_row: float, from_col: float, to_row: float, to_col: float) -> float:
    """Compass bearing of a pixel-space displacement. Rows increase southward."""
    north = from_row - to_row
    east = to_col - from_col
    return float(math.degrees(math.atan2(east, north)) % 360)


def _compass(bearing: float) -> str:
    points = (
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    )
    return points[int((bearing + 11.25) % 360 // 22.5)]


def build(args: argparse.Namespace) -> None:
    viirs = dated_files(args.viirs_dir)
    if not viirs:
        raise ValueError("No dated VIIRS_Day files were found for this event")
    firepred = dict(dated_files(args.firepred_dir)) if args.firepred_dir.exists() else {}

    reference, reference_dataset = read_band(viirs[0][1], 8)
    transform = tuple(reference_dataset.GetGeoTransform())
    shape = reference.shape

    # The event object: everything that ever burned, restricted to the connected
    # component nearest the catalogue centre, so a neighbouring scar in the same
    # tile is never counted as part of this fire.
    union = np.zeros(shape, dtype=bool)
    per_day: list[tuple[str, np.ndarray]] = []
    for date, path in viirs:
        if date > args.end_date:
            continue
        values, dataset = read_band(path, 8)
        if values.shape != shape or tuple(dataset.GetGeoTransform()) != transform:
            raise ValueError("Burned-area labels are not aligned across days")
        burned = np.isfinite(values) & (values != 0)
        per_day.append((date, burned))
        union |= burned
        dataset = None
    seed = point_pixel(transform, args.center_latitude, args.center_longitude)
    event = component_nearest(union, *seed)

    centre_latitude = transform[3] + (shape[0] / 2) * transform[5]
    pixel_width_km, pixel_height_km = pixel_size_km(transform, centre_latitude)
    pixel_area_km2 = pixel_width_km * pixel_height_km

    # --- composition ---------------------------------------------------------
    composition: list[dict] = []
    land_cover_source = None
    if firepred:
        cover_path = firepred.get(args.end_date) or max(firepred.items())[1]
        cover, cover_dataset = read_band(cover_path, 14)
        if cover.shape == shape and tuple(cover_dataset.GetGeoTransform()) == transform:
            land_cover_source = "FirePred band 14 (LC_Type1, MODIS IGBP)"
            inside = cover[event]
            inside = inside[np.isfinite(inside)]
            total = int(inside.size)
            for code, count in zip(*np.unique(inside.astype(int), return_counts=True), strict=True):
                composition.append(
                    {
                        "code": int(code),
                        "label": LAND_COVER_CLASSES.get(int(code), f"Class {int(code)}"),
                        "color": LAND_COVER_COLORS.get(int(code), "#9e9e9e"),
                        "pixels": int(count),
                        "area_km2": round(float(count) * pixel_area_km2, 2),
                        "percent": round(float(count) / total * 100, 1) if total else 0.0,
                    }
                )
            composition.sort(key=lambda item: item["area_km2"], reverse=True)
        cover_dataset = None

    # --- spread + weather ----------------------------------------------------
    timeline: list[dict] = []
    cumulative = np.zeros(shape, dtype=bool)
    previous_centroid: tuple[float, float] | None = None
    for date, burned in per_day:
        confined = burned & event
        previous = cumulative.copy()
        cumulative |= confined
        newly = cumulative & ~previous
        entry: dict = {
            "date": date,
            "new_pixels": int(newly.sum()),
            "new_area_km2": round(float(newly.sum()) * pixel_area_km2, 2),
            "cumulative_area_km2": round(float(cumulative.sum()) * pixel_area_km2, 2),
        }

        if cumulative.any():
            rows, columns = np.where(cumulative)
            centroid = (float(rows.mean()), float(columns.mean()))
            if previous_centroid is not None:
                row_shift = centroid[0] - previous_centroid[0]
                column_shift = centroid[1] - previous_centroid[1]
                distance = math.hypot(row_shift * pixel_height_km, column_shift * pixel_width_km)
                entry["centroid_shift_km"] = round(distance, 2)
                if distance >= 0.05:
                    bearing = _bearing(previous_centroid[0], previous_centroid[1], *centroid)
                    entry["spread_bearing_deg"] = round(bearing, 1)
                    entry["spread_compass"] = _compass(bearing)
            previous_centroid = centroid

        if newly.any():
            rows, columns = np.where(newly)
            entry["growth_front_pixels"] = int(rows.size)

        path_for_wind = firepred.get(date)
        if path_for_wind is not None:
            samples: dict[str, dict] = {}
            zone = newly if newly.any() else cumulative
            for key, spec in WEATHER_BANDS.items():
                values, dataset = read_band(path_for_wind, spec["band"])
                if values.shape != shape:
                    dataset = None
                    continue
                summary = _summarize(values[zone], spec)
                if summary:
                    samples[key] = summary
                dataset = None
            if samples:
                entry["conditions"] = samples
                entry["conditions_zone"] = (
                    "newly burned pixels" if newly.any() else "cumulative footprint"
                )
                # Wind direction is recorded the meteorological way - the direction
                # the wind blows FROM - so the direction fire would be pushed is the
                # reciprocal. The offset between that and the observed spread is the
                # honest way to state alignment, rather than asserting causation.
                wind = samples.get("wind_direction", {}).get("mean")
                bearing = entry.get("spread_bearing_deg")
                if wind is not None and bearing is not None:
                    downwind = (wind + 180) % 360
                    offset = abs((bearing - downwind + 180) % 360 - 180)
                    entry["downwind_bearing_deg"] = round(downwind, 1)
                    entry["spread_wind_offset_deg"] = round(offset, 1)
        timeline.append(entry)

    # --- terrain over the whole footprint ------------------------------------
    terrain: dict[str, dict] = {}
    if firepred:
        terrain_path = max(firepred.items())[1]
        for key, spec in TERRAIN_BANDS.items():
            values, dataset = read_band(terrain_path, spec["band"])
            if values.shape == shape:
                summary = _summarize(values[event], spec)
                if summary:
                    terrain[key] = summary
            dataset = None

    # Did the fire climb? Compare the elevation of what burned early against late.
    elevation_trend = None
    if firepred and "elevation_m" in terrain:
        elevation, dataset = read_band(max(firepred.items())[1], 12)
        dataset = None
        half = max(1, len(per_day) // 2)
        early = np.zeros(shape, dtype=bool)
        for _date, burned in per_day[:half]:
            early |= burned & event
        late = event & ~early
        if early.any() and late.any():
            early_mean = float(np.nanmean(elevation[early]))
            late_mean = float(np.nanmean(elevation[late]))
            elevation_trend = {
                "early_mean_m": round(early_mean, 1),
                "late_mean_m": round(late_mean, 1),
                "change_m": round(late_mean - early_mean, 1),
                "direction": "uphill" if late_mean > early_mean else "downhill",
                "split": f"first {half} of {len(per_day)} days against the rest",
            }

    # Alignment is only meaningful where the fire actually moved: on a quiet day
    # the centroid wanders by a pixel and its bearing is noise. Weighting by new
    # area lets the days that carried the fire speak loudest.
    wind_alignment = None
    weighed = [
        entry
        for entry in timeline
        if "spread_wind_offset_deg" in entry and entry["new_area_km2"] > 0
    ]
    if weighed:
        weight = sum(entry["new_area_km2"] for entry in weighed)
        mean_offset = (
            sum(entry["spread_wind_offset_deg"] * entry["new_area_km2"] for entry in weighed)
            / weight
        )
        aligned = [entry for entry in weighed if entry["spread_wind_offset_deg"] <= 45]
        wind_alignment = {
            "days_compared": len(weighed),
            "area_weighted_offset_deg": round(mean_offset, 1),
            "days_within_45_deg": len(aligned),
            "area_share_within_45_deg": round(
                sum(entry["new_area_km2"] for entry in aligned) / weight * 100, 1
            ),
        }

    peak = max(timeline, key=lambda entry: entry["new_area_km2"], default=None)
    peak_day = None
    if peak and peak["new_area_km2"] > 0:
        peak_day = {
            "date": peak["date"],
            "new_area_km2": peak["new_area_km2"],
            "centroid_shift_km": peak.get("centroid_shift_km"),
            "spread_compass": peak.get("spread_compass"),
            "spread_wind_offset_deg": peak.get("spread_wind_offset_deg"),
            "conditions": peak.get("conditions"),
        }

    total_area = round(float(event.sum()) * pixel_area_km2, 2)
    payload = {
        "event_id": args.event_id,
        "end_date": args.end_date,
        "footprint_area_km2": total_area,
        "footprint_pixels": int(event.sum()),
        "pixel_area_km2": round(pixel_area_km2, 5),
        "land_cover_source": land_cover_source,
        "composition": composition,
        "terrain": terrain,
        "elevation_trend": elevation_trend,
        "wind_alignment": wind_alignment,
        "peak_growth_day": peak_day,
        "timeline": timeline,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--viirs-dir", type=Path, required=True)
    parser.add_argument("--firepred-dir", type=Path, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--center-longitude", type=float, required=True)
    parser.add_argument("--center-latitude", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
