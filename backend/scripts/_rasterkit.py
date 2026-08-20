"""Raster primitives shared by the trusted builder scripts.

The builders run as separate processes under the GDAL interpreter, so they
cannot import from `wildfire_agent`. They were each carrying their own copy of
the same geometry, and the copies had quietly drifted: one `_bounds` ignored the
rotation terms, one `_component_nearest` raised on an empty mask while another
returned nothing. Drift like that does not announce itself - it just makes two
analyses of the same fire disagree.

Where the copies differed, this module keeps the more general behaviour and
leaves the decision to the caller: a helper reports what it found, and whether
"nothing" is an error belongs to the analysis, not to the geometry.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")

#: Mean Earth radius expressed as km per degree of latitude. Longitude degrees
#: shrink by cos(latitude), which every distance here accounts for. This is a
#: local equirectangular approximation, not an equal-area projection.
KM_PER_DEGREE = 111.32


def read_band(path: Path, band_number: int) -> tuple[np.ndarray, gdal.Dataset]:
    """Read one band as float64. The dataset is returned so the caller can keep
    its geotransform and projection alive while the array is in use."""
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None or dataset.RasterCount < band_number:
        raise ValueError(f"{path.name} does not contain band {band_number}")
    return dataset.GetRasterBand(band_number).ReadAsArray().astype(np.float64), dataset


def dated_files(directory: Path) -> list[tuple[str, Path]]:
    """Every `YYYY-MM-DD`-named raster in a directory, in date order."""
    found = []
    for path in sorted(directory.glob("*.tif*")):
        match = DATE_RE.search(path.name)
        if match:
            found.append((match.group("date"), path))
    return sorted(found)


def point_pixel(transform: tuple[float, ...], latitude: float, longitude: float) -> tuple[int, int]:
    """Pixel holding a coordinate, for north-up rasters only."""
    if transform[2] != 0 or transform[4] != 0:
        raise ValueError("Rotated rasters are not supported")
    row = round((latitude - transform[3]) / transform[5])
    column = round((longitude - transform[0]) / transform[1])
    return row, column


def component_nearest(mask: np.ndarray, seed_row: int, seed_column: int) -> np.ndarray:
    """The 8-connected component of `mask` nearest a seed pixel.

    This is what separates one fire from its neighbours: a source tile spans two
    degrees and routinely holds several burn scars, so the component nearest the
    catalogue centre is the event, and everything else in the tile is not.

    An empty mask yields an empty mask. Callers that require pixels say so
    themselves, in their own words.
    """
    rows, columns = np.where(mask)
    selected = np.zeros_like(mask, dtype=bool)
    if not len(rows):
        return selected

    distances = (rows - seed_row) ** 2 + (columns - seed_column) ** 2
    nearest = int(distances.argmin())
    start = (int(rows[nearest]), int(columns[nearest]))
    selected[start] = True

    height, width = mask.shape
    queue = [start]
    while queue:
        row, column = queue.pop()
        for row_offset in (-1, 0, 1):
            for column_offset in (-1, 0, 1):
                if row_offset == 0 and column_offset == 0:
                    continue
                candidate_row = row + row_offset
                candidate_column = column + column_offset
                if not (0 <= candidate_row < height and 0 <= candidate_column < width):
                    continue
                if (
                    mask[candidate_row, candidate_column]
                    and not selected[candidate_row, candidate_column]
                ):
                    selected[candidate_row, candidate_column] = True
                    queue.append((candidate_row, candidate_column))
    return selected


def crop_box(mask: np.ndarray, padding: int = 3) -> tuple[int, int, int, int] | None:
    """Row/column window enclosing the true pixels, or None when there are none."""
    rows, columns = np.where(mask)
    if not len(rows):
        return None
    return (
        max(0, int(rows.min()) - padding),
        min(mask.shape[0], int(rows.max()) + padding + 1),
        max(0, int(columns.min()) - padding),
        min(mask.shape[1], int(columns.max()) + padding + 1),
    )


def cropped_transform(transform: tuple[float, ...], row0: int, col0: int) -> tuple[float, ...]:
    return (
        transform[0] + col0 * transform[1] + row0 * transform[2],
        transform[1],
        transform[2],
        transform[3] + col0 * transform[4] + row0 * transform[5],
        transform[4],
        transform[5],
    )


def bounds(transform: tuple[float, ...], width: int, height: int) -> list[float]:
    """West, south, east, north. All four corners are projected, so this stays
    correct if a rotated raster ever reaches it."""
    corners = [
        (transform[0], transform[3]),
        (transform[0] + width * transform[1], transform[3] + width * transform[4]),
        (transform[0] + height * transform[2], transform[3] + height * transform[5]),
        (
            transform[0] + width * transform[1] + height * transform[2],
            transform[3] + width * transform[4] + height * transform[5],
        ),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    return [min(xs), min(ys), max(xs), max(ys)]


def pixel_size_km(transform: tuple[float, ...], latitude: float) -> tuple[float, float]:
    """Width and height of one pixel in km at a given latitude."""
    width = abs(transform[1]) * KM_PER_DEGREE * math.cos(math.radians(latitude))
    height = abs(transform[5]) * KM_PER_DEGREE
    return width, height


def pixel_area_km2(transform: tuple[float, ...], latitude: float) -> float:
    width, height = pixel_size_km(transform, latitude)
    return width * height


def hex_color(value: str) -> tuple[int, int, int]:
    value = value.removeprefix("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def colorize(values: np.ndarray, valid: np.ndarray, stops: list[dict]) -> np.ndarray:
    """Classify continuous values into a stop palette and return RGBA."""
    output = np.zeros((*values.shape, 4), dtype=np.uint8)
    thresholds = [float(stop["value"]) for stop in stops]
    colors = [hex_color(str(stop["color"])) for stop in stops]
    indices = np.searchsorted(thresholds, values, side="right") - 1
    indices = np.clip(indices, 0, len(colors) - 1)
    for index, color in enumerate(colors):
        selected = valid & (indices == index)
        output[selected, :3] = color
    output[valid, 3] = 225
    return output


def mask_rgba(mask: np.ndarray, color: tuple[int, int, int, int]) -> np.ndarray:
    """A single flat colour wherever the mask is true, transparent elsewhere."""
    output = np.zeros((*mask.shape, 4), dtype=np.uint8)
    for index, value in enumerate(color[:3]):
        output[:, :, index] = np.where(mask, value, 0).astype(np.uint8)
    output[:, :, 3] = np.where(mask, color[3], 0).astype(np.uint8)
    return output


def write_rgba_png(
    path: Path,
    rgba: np.ndarray,
    transform: tuple[float, ...],
    projection: str,
) -> None:
    height, width, _ = rgba.shape
    memory = gdal.GetDriverByName("MEM").Create("", width, height, 4, gdal.GDT_Byte)
    memory.SetGeoTransform(transform)
    memory.SetProjection(projection)
    for index in range(4):
        memory.GetRasterBand(index + 1).WriteArray(rgba[:, :, index])
    memory.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
    path.parent.mkdir(parents=True, exist_ok=True)
    output = gdal.GetDriverByName("PNG").CreateCopy(str(path), memory, strict=1)
    output.FlushCache()
    output = None
    memory = None
