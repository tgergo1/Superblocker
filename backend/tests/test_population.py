import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from app.models.schemas import BoundingBox
from app.services.design.pilot_plan import PilotPlanner
from app.services.importers import population as population_module
from app.services.importers.population import (
    PopulationGrid,
    read_population,
    read_population_csv,
    residents_by_polygon,
    summarize_population,
)
from app.services.partitioning.city_partitioner import CityPartitioner
from tests.test_pilot_plan import block
from tests.test_routing_and_partitioning import partition_graph

AREA = BoundingBox(north=47.52, south=47.49, east=19.07, west=19.04)


def write_raster(path, crs, origin, size, values, nodata=-200.0):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(origin[0], origin[1], size, size),
        nodata=nodata,
    ) as dataset:
        dataset.write(values.astype("float32"), 1)


def test_wgs84_raster_cells_become_points_inside_the_box(tmp_path):
    # 4 x 4 cells of 0.01 degrees from 19.03 E, 47.53 N; one nodata, one empty.
    values = np.full((4, 4), 10.0)
    values[0, 0] = -200.0
    values[3, 3] = 0.0
    path = tmp_path / "pop.tif"
    write_raster(path, "EPSG:4326", (19.03, 47.53), 0.01, values)
    grid = read_population(path, AREA)
    assert grid.residents.size == 14
    assert grid.residents.sum() == pytest.approx(140.0)
    assert grid.lon.min() == pytest.approx(19.035)
    assert grid.lat.max() == pytest.approx(47.525)


def test_mollweide_raster_is_reprojected(tmp_path):
    # One 100 m GHS-POP style cell around Deak ter (19.0547 E, 47.4973 N).
    from rasterio.warp import transform

    xs, ys = transform("EPSG:4326", "ESRI:54009", [19.0547], [47.4973])
    path = tmp_path / "ghs.tif"
    write_raster(path, "ESRI:54009", (xs[0] - 50, ys[0] + 50), 100, np.array([[250.0]]))
    grid = read_population(path, AREA)
    assert grid.residents.tolist() == [250.0]
    assert grid.lon[0] == pytest.approx(19.0547, abs=1e-4)
    assert grid.lat[0] == pytest.approx(47.4973, abs=1e-4)


def test_csv_grid_and_missing_file(tmp_path):
    grid = read_population_csv(
        "﻿Lon,Lat,Population\n19.05,47.50,120\n19.06,47.51,0\nbad,47.5,3\n19.2,47.6,9\n"
    )
    assert grid.residents.tolist() == [120.0, 9.0]
    assert grid.total_in(AREA) == 120.0
    path = tmp_path / "grid.csv"
    path.write_text("lon,lat,residents\n19.05,47.50,7\n", encoding="utf-8")
    assert read_population(path, AREA).residents.tolist() == [7.0]
    with pytest.raises(FileNotFoundError):
        read_population(tmp_path / "missing.tif", AREA)


def test_cells_count_once_for_the_polygon_holding_their_centre():
    grid = PopulationGrid(
        lon=np.array([0.5, 1.5, 1.0, 5.0]),
        lat=np.array([0.5, 0.5, 0.5, 5.0]),
        residents=np.array([100.0, 50.0, 10.0, 999.0]),
    )
    counts = residents_by_polygon(grid, {"a": box(0, 0, 1, 1), "b": box(1, 0, 2, 1)})
    # The cell on the shared edge goes to one polygon only.
    assert counts["a"] + counts["b"] == pytest.approx(160.0)
    assert counts["a"] >= 100.0 and counts["b"] >= 50.0

    summary = summarize_population(
        grid,
        {"a": box(0, 0, 1, 1), "b": box(1, 0, 2, 1)},
        {"a": 10.0, "b": 0.0},
        BoundingBox(north=6, south=0, east=6, west=0),
    )
    assert summary.residents_in_area == 1159
    assert summary.residents_in_superblocks == 160
    assert summary.share_in_superblocks == pytest.approx(0.138)
    densities = {item.superblock_id: item.residents_per_ha for item in summary.superblocks}
    assert densities["b"] == 0.0
    assert densities["a"] == pytest.approx(counts["a"] / 10.0, abs=0.1)
    empty = residents_by_polygon(
        PopulationGrid(np.array([]), np.array([]), np.array([])), {"a": box(0, 0, 1, 1)}
    )
    assert empty == {"a": 0.0}


def test_pilot_ranking_uses_residents_per_hectare():
    blocks = [block("sb_a", 1), block("sb_b", 1)]
    areas = {"sb_a": 10.0, "sb_b": 10.0}
    plan = PilotPlanner().plan(blocks, areas_ha=areas, residents={"sb_a": 100.0, "sb_b": 4_000.0})
    assert [item.superblock_id for item in plan.candidates] == ["sb_b", "sb_a"]
    assert plan.candidates[0].residents == 4_000
    assert "residents per hectare" in plan.limitations[-1]
    assert PilotPlanner().plan(blocks, areas_ha=areas).candidates[0].residents is None


def test_configured_grid_loads_or_stays_out_of_the_way(monkeypatch, tmp_path):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "population_path", None)
    assert population_module.load_configured_population(AREA) is None
    monkeypatch.setattr(get_settings(), "population_path", str(tmp_path / "missing.tif"))
    assert population_module.load_configured_population(AREA) is None
    path = tmp_path / "grid.csv"
    path.write_text("lon,lat,population\n19.05,47.50,7\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "population_path", str(path))
    assert population_module.load_configured_population(AREA).residents.tolist() == [7.0]


def test_partition_counts_residents():
    grid = PopulationGrid(
        lon=np.array([0.001, 0.0015, 0.5]),
        lat=np.array([0.001, 0.0005, 0.5]),
        residents=np.array([300.0, 200.0, 1_000.0]),
    )
    result = CityPartitioner(
        partition_graph(),
        BoundingBox(north=0.002, south=0, east=0.002, west=0),
        target_size_ha=5,
        min_area_ha=0.5,
        max_area_ha=20,
        arterial_road_types={"primary"},
        population=grid,
    ).partition()
    assert result.population is not None
    assert result.population.residents_in_area == 500
    assert result.population.residents_in_superblocks > 0
    assert result.population.source.startswith("GHS-POP")


def test_raster_away_from_the_area_is_empty(tmp_path):
    path = tmp_path / "far.tif"
    write_raster(path, "EPSG:4326", (10.0, 50.0), 0.01, np.full((2, 2), 5.0))
    assert read_population(path, AREA).residents.size == 0


def test_share_uses_the_boundary_not_its_bounding_box():
    grid = PopulationGrid(
        lon=np.array([0.5, 3.5]),
        lat=np.array([0.5, 3.5]),
        residents=np.array([100.0, 900.0]),
    )
    triangle = Polygon([(0, 0), (4, 0), (0, 4)])
    area = BoundingBox(north=4, south=0, east=4, west=0)
    summary = summarize_population(grid, {"a": box(0, 0, 1, 1)}, {"a": 1.0}, area, triangle)
    assert summary.residents_in_area == 100
    assert summary.share_in_superblocks == 1.0
    whole_box = summarize_population(grid, {"a": box(0, 0, 1, 1)}, {"a": 1.0}, area)
    assert whole_box.residents_in_area == 1000


def test_grid_without_cells_here_keeps_the_street_density_ranking():
    grid = PopulationGrid(np.array([50.0]), np.array([50.0]), np.array([10.0]))
    result = CityPartitioner(
        partition_graph(),
        BoundingBox(north=0.002, south=0, east=0.002, west=0),
        target_size_ha=5,
        min_area_ha=0.5,
        max_area_ha=20,
        arterial_road_types={"primary"},
        population=grid,
    ).partition()
    assert result.population.residents_in_superblocks == 0
    assert "no residents" in result.population.limitations[-1]
    assert all(item.residents is None for item in result.pilot_plan.candidates)
