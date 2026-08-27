#!/usr/bin/env python3
"""Build lightweight, browser-ready TS-SatFire lifecycle assets.

The application runtime intentionally does not import GDAL's Python bindings.
This helper is launched with the system GDAL Python when a lifecycle is first
requested, then the API serves the resulting PNG masks and compact JSON cache.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from _rasterkit import (
    DATE_RE,
    bounds,
    component_nearest,
    cropped_transform,
    mask_rgba,
    pixel_size_km,
    point_pixel,
    read_band,
    write_rgba_png,
)

CACHE_VERSION = 1


def _dated_files(event_dir: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for path in sorted((event_dir / "VIIRS_Day").glob("*.tif*")):
        match = DATE_RE.search(path.name)
        if match:
            files.append((match.group("date"), path))
    if not files:
        raise ValueError(f"No VIIRS_Day GeoTIFFs found in {event_dir}")
    return files


def _read_label_mask(path: Path, band_number: int) -> tuple[np.ndarray, object]:
    """A TS-SatFire label band as a boolean mask.

    The AF and BA bands carry a value where a label exists and NaN where it does
    not, so presence - not magnitude - is the signal. The kit reads the raw band;
    the meaning of "labelled" belongs here.
    """
    values, dataset = read_band(path, band_number)
    return np.isfinite(values) & (values != 0), dataset


def _crop_window(mask: np.ndarray, padding: int = 4) -> tuple[int, int, int, int]:
    rows, cols = np.where(mask)
    if not len(rows):
        raise ValueError("The event contains no finite AF or BA label pixels")
    row0 = max(0, int(rows.min()) - padding)
    row1 = min(mask.shape[0], int(rows.max()) + padding + 1)
    col0 = max(0, int(cols.min()) - padding)
    col1 = min(mask.shape[1], int(cols.max()) + padding + 1)
    return row0, row1, col0, col1


def _expanded_component_box(mask: np.ndarray, padding: int = 40) -> np.ndarray:
    """Limit sparse AF pixels to the vicinity of the selected BA object."""
    rows, columns = np.where(mask)
    if not len(rows):
        return np.ones_like(mask, dtype=bool)
    output = np.zeros_like(mask, dtype=bool)
    row0 = max(0, int(rows.min()) - padding)
    row1 = min(mask.shape[0], int(rows.max()) + padding + 1)
    col0 = max(0, int(columns.min()) - padding)
    col1 = min(mask.shape[1], int(columns.max()) + padding + 1)
    output[row0:row1, col0:col1] = True
    return output


def _write_mask_png(
    path: Path,
    mask: np.ndarray,
    crop: tuple[int, int, int, int],
    transform: tuple[float, ...],
    projection: str,
    color: tuple[int, int, int, int],
) -> None:
    """One flat-coloured label layer, cropped to the event window."""
    row0, row1, col0, col1 = crop
    selected = mask[row0:row1, col0:col1]
    write_rgba_png(
        path,
        mask_rgba(selected, color),
        cropped_transform(transform, row0, col0),
        projection,
    )


def build(
    event_dir: Path,
    output_dir: Path,
    *,
    center_latitude: float,
    center_longitude: float,
    overwrite: bool = False,
) -> Path:
    event_dir = event_dir.resolve()
    output_dir = output_dir.resolve()
    metadata_path = output_dir / "metadata.json"
    if metadata_path.exists() and not overwrite:
        return metadata_path

    dated_files = _dated_files(event_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / "assets"
    if overwrite and assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    active_union: np.ndarray | None = None
    burned_union: np.ndarray | None = None
    transform: tuple[float, ...] | None = None
    projection = ""
    for _, path in dated_files:
        active, dataset = _read_label_mask(path, 7)
        burned, _ = _read_label_mask(path, 8)
        if active_union is None or burned_union is None:
            active_union = np.zeros_like(active, dtype=bool)
            burned_union = np.zeros_like(active, dtype=bool)
            transform = tuple(dataset.GetGeoTransform())
            projection = dataset.GetProjection()
        active_union |= active
        burned_union |= burned
        dataset = None

    assert active_union is not None and burned_union is not None and transform is not None
    seed_row, seed_column = point_pixel(transform, center_latitude, center_longitude)
    event_burned_domain = component_nearest(burned_union, seed_row, seed_column)
    active_domain = _expanded_component_box(event_burned_domain)
    active_union &= active_domain
    event_union = event_burned_domain | active_union
    crop = _crop_window(event_union)
    row0, row1, col0, col1 = crop
    window_transform = cropped_transform(transform, row0, col0)
    window_bounds = bounds(window_transform, col1 - col0, row1 - row0)
    center_latitude = (window_bounds[1] + window_bounds[3]) / 2
    pixel_width_km, pixel_height_km = pixel_size_km(transform, center_latitude)
    pixel_area_km2 = pixel_width_km * pixel_height_km

    cumulative = np.zeros_like(event_union, dtype=bool)
    timeline: list[dict[str, int | float | str]] = []
    for date, path in dated_files:
        active, dataset = _read_label_mask(path, 7)
        burned, _ = _read_label_mask(path, 8)
        active &= active_domain
        burned &= event_burned_domain
        previous = cumulative.copy()
        cumulative |= burned
        newly_burned = cumulative & ~previous

        _write_mask_png(
            assets_dir / f"{date}_burned_area.png",
            cumulative,
            crop,
            transform,
            projection,
            (91, 83, 108, 170),
        )
        _write_mask_png(
            assets_dir / f"{date}_active_fire.png",
            active,
            crop,
            transform,
            projection,
            (224, 82, 52, 235),
        )
        active_pixels = int(active.sum())
        new_pixels = int(newly_burned.sum())
        cumulative_pixels = int(cumulative.sum())
        timeline.append(
            {
                "date": date,
                "active_pixels": active_pixels,
                "new_burned_pixels": new_pixels,
                "cumulative_burned_pixels": cumulative_pixels,
                "new_burned_km2": round(new_pixels * pixel_area_km2, 2),
                "cumulative_burned_km2": round(cumulative_pixels * pixel_area_km2, 2),
            }
        )
        dataset = None

    payload = {
        "cache_version": CACHE_VERSION,
        "event_id": event_dir.name,
        "dates": [date for date, _ in dated_files],
        "bounds": window_bounds,
        "pixel_area_km2": pixel_area_km2,
        "timeline": timeline,
        "source": "TS-SatFire local VIIRS_Day labels",
        "event_center": [center_longitude, center_latitude],
        "selection": (
            "BA is the connected label object nearest the catalogue event centre; "
            "AF is limited to its surrounding event window"
        ),
        "semantics": {
            "active_fire": "VIIRS_Day band 7 (AF): finite non-zero label pixels for the selected day",
            "burned_area": "Cumulative union through the selected day of VIIRS_Day band 8 (BA label, locally described as 'first')",
            "prediction_inputs": "FirePred contains 19 auxiliary predictor bands; it is not a model prediction output",
        },
    }
    temporary = metadata_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(metadata_path)
    return metadata_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("event_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--center-latitude", type=float, required=True)
    parser.add_argument("--center-longitude", type=float, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    print(
        build(
            args.event_dir,
            args.output_dir,
            center_latitude=args.center_latitude,
            center_longitude=args.center_longitude,
            overwrite=args.overwrite,
        )
    )


if __name__ == "__main__":
    main()
