"""Count residents per superblock from a free population grid.

The EU's GHS-POP (Global Human Settlement Layer, CC BY 4.0) gives residents
per 100 m cell for the whole world, so the tool can rank superblocks by how
many people they serve instead of by street density. Any single-band
GeoTIFF of people per cell works, in any projection (GHS-POP ships in
Mollweide and in WGS84). A CSV of ``lon,lat,population`` points also works
for grids converted elsewhere.

Each cell's residents go to the superblock that contains the cell centre.
At 100 m cells and superblocks of 10 to 40 hectares that is accurate to a
few percent; small or thin superblocks get a rougher count.
"""

from __future__ import annotations

import csv
import io
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import base as shapely_base
from shapely.strtree import STRtree

from app.models.schemas import BoundingBox, PopulationSummary, SuperblockPopulation

logger = logging.getLogger(__name__)

GHS_POP_SOURCE = "GHS-POP, European Commission JRC (CC BY 4.0)"
WGS84 = "EPSG:4326"
# Pull in a margin so cells whose centre falls just outside the box still count.
BBOX_MARGIN_DEG = 0.002


@dataclass
class PopulationGrid:
    """Population cell centres in WGS84 with their resident counts."""

    lon: np.ndarray
    lat: np.ndarray
    residents: np.ndarray
    source: str = GHS_POP_SOURCE
    limitations: list[str] = field(default_factory=list)

    def total_in(self, bbox: BoundingBox, area: shapely_base.BaseGeometry | None = None) -> float:
        """Residents in the box, or in ``area`` (such as a district boundary) when given."""
        inside = (
            (self.lon >= bbox.west)
            & (self.lon <= bbox.east)
            & (self.lat >= bbox.south)
            & (self.lat <= bbox.north)
        )
        if area is not None and not area.is_empty and inside.any():
            points = shapely.points(self.lon[inside], self.lat[inside])
            inside[inside] = shapely.intersects(area, points)
        return float(self.residents[inside].sum())


def _from_points(lon, lat, residents, source: str) -> PopulationGrid:
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    residents = np.asarray(residents, dtype=float)
    keep = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(residents) & (residents > 0)
    return PopulationGrid(lon[keep], lat[keep], residents[keep], source)


def read_population_csv(text: str, source: str = GHS_POP_SOURCE) -> PopulationGrid:
    """Read ``lon,lat,population`` rows (header names are case-insensitive)."""
    lon: list[float] = []
    lat: list[float] = []
    residents: list[float] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for row in reader:
        values = {key.strip().lower(): value for key, value in row.items() if key}
        try:
            lon.append(float(values["lon"]))
            lat.append(float(values["lat"]))
            residents.append(float(values.get("population") or values.get("residents") or 0))
        except (KeyError, TypeError, ValueError):
            continue
    return _from_points(lon, lat, residents, source)


def read_population_raster(
    path: str | Path, bbox: BoundingBox, source: str = GHS_POP_SOURCE
) -> PopulationGrid:
    """Read the raster cells covering ``bbox``, with centres converted to WGS84."""
    import rasterio
    from rasterio.warp import transform, transform_bounds
    from rasterio.windows import from_bounds

    with rasterio.open(path) as dataset:
        crs = dataset.crs or WGS84
        west, south, east, north = transform_bounds(
            WGS84,
            crs,
            bbox.west - BBOX_MARGIN_DEG,
            bbox.south - BBOX_MARGIN_DEG,
            bbox.east + BBOX_MARGIN_DEG,
            bbox.north + BBOX_MARGIN_DEG,
        )
        # Every cell that touches the area, then clipped to the raster.
        exact = from_bounds(west, south, east, north, dataset.transform)
        col_off = max(0, math.floor(exact.col_off))
        row_off = max(0, math.floor(exact.row_off))
        col_end = min(dataset.width, math.ceil(exact.col_off + exact.width))
        row_end = min(dataset.height, math.ceil(exact.row_off + exact.height))
        if col_end <= col_off or row_end <= row_off:
            return PopulationGrid(np.array([]), np.array([]), np.array([]), source)
        window = rasterio.windows.Window(col_off, row_off, col_end - col_off, row_end - row_off)
        values = dataset.read(1, window=window, masked=True).astype(float)
        if values.size == 0:
            return PopulationGrid(np.array([]), np.array([]), np.array([]), source)
        rows, cols = np.nonzero(~np.ma.getmaskarray(values) & (values.filled(0) > 0))
        window_transform = dataset.window_transform(window)
        xs, ys = rasterio.transform.xy(window_transform, rows, cols, offset="center")
        lon, lat = transform(crs, WGS84, np.atleast_1d(xs), np.atleast_1d(ys))
        return _from_points(lon, lat, values.data[rows, cols], source)


def read_population(path: str | Path, bbox: BoundingBox) -> PopulationGrid:
    """A GeoTIFF or a ``lon,lat,population`` CSV, by file extension."""
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"Population grid not found: {file}")
    if file.suffix.lower() in {".csv", ".txt"}:
        return read_population_csv(file.read_text(encoding="utf-8-sig"))
    return read_population_raster(file, bbox)


def residents_by_polygon(
    grid: PopulationGrid, polygons: dict[str, shapely_base.BaseGeometry]
) -> dict[str, float]:
    """Residents whose grid cell centre lies in each polygon.

    A cell on a shared edge counts once, for the first polygon that holds it.
    """
    ids = [key for key, polygon in polygons.items() if polygon is not None and not polygon.is_empty]
    counts = dict.fromkeys(polygons, 0.0)
    if not ids or grid.residents.size == 0:
        return counts
    tree = STRtree([polygons[key] for key in ids])
    points = shapely.points(grid.lon, grid.lat)
    point_index, polygon_index = tree.query(points, predicate="intersects")
    seen: set[int] = set()
    for point, polygon in zip(point_index.tolist(), polygon_index.tolist(), strict=True):
        if point in seen:
            continue
        seen.add(point)
        counts[ids[polygon]] += float(grid.residents[point])
    return counts


def summarize_population(
    grid: PopulationGrid,
    polygons: dict[str, shapely_base.BaseGeometry],
    areas_ha: dict[str, float],
    bbox: BoundingBox,
    area: shapely_base.BaseGeometry | None = None,
) -> PopulationSummary:
    """Residents per superblock and the plan's share of the area's residents.

    ``area`` is the analysis boundary, so the share is measured against the
    same district the superblocks were drawn in rather than its bounding box.
    """
    counts = residents_by_polygon(grid, polygons)
    in_area = grid.total_in(bbox, area)
    in_superblocks = sum(counts.values())
    return PopulationSummary(
        source=grid.source,
        residents_in_area=round(in_area),
        residents_in_superblocks=round(in_superblocks),
        share_in_superblocks=round(min(1.0, in_superblocks / in_area), 3) if in_area > 0 else 0.0,
        superblocks=[
            SuperblockPopulation(
                superblock_id=key,
                residents=round(value),
                residents_per_ha=round(value / areas_ha[key], 1)
                if areas_ha.get(key, 0) > 0
                else 0.0,
            )
            for key, value in counts.items()
        ],
        limitations=[
            *grid.limitations,
            "Residents are counted by grid cell centre, so small superblocks get a rough count.",
        ],
    )


def load_configured_population(bbox: BoundingBox) -> PopulationGrid | None:
    """The population grid named by ``POPULATION_PATH`` for ``bbox``; never raises."""
    from app.core.config import get_settings

    path = get_settings().population_path
    if not path:
        return None
    try:
        return read_population(path, bbox)
    except Exception as error:  # A broken grid must not block the plan.
        logger.warning("Population grid unavailable: %s", error)
        return None
