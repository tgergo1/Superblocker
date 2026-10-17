import numpy as np
import pytest

from app.models.simulation import SimulatedLink
from app.services.impact.environment import EnvironmentModel, crtn_laeq, nox_g_per_km
from app.services.importers.open_data import AirQualityStation
from app.services.importers.population import PopulationGrid


def road(lon0, lat, lon1, volume, planned, highway="primary"):
    return SimulatedLink(
        coordinates=[[lon0, lat], [lon1, lat]],
        osm_id=1,
        highway=highway,
        baseline_vph=volume,
        planned_vph=planned,
        planned_elastic_vph=planned * 0.9,
        capacity_vph=2000,
        baseline_vc=0.5,
        planned_vc=0.5,
        length_m=(lon1 - lon0) * 111_320 * np.cos(np.radians(lat)),
        free_speed_kph=50,
        baseline_speed_kph=40,
        planned_speed_kph=40,
        planned_elastic_speed_kph=40,
    )


def test_crtn_and_emission_curves_have_the_expected_shape():
    level = crtn_laeq(np.array([1000.0, 2000.0]), np.array([50.0, 50.0]), np.array([0.0, 0.0]))
    assert level[0] == pytest.approx(66.4, abs=0.1)
    assert level[1] - level[0] == pytest.approx(3.01, abs=0.01)
    factors = nox_g_per_km(np.array([10.0, 50.0, 120.0]), np.zeros(3))
    assert factors[1] < factors[0] and factors[1] < factors[2]


def test_model_calibrates_to_stations_and_counts_exposure():
    links = [
        road(19.00, 47.50, 19.05, 2000, 2000),
        road(19.00, 47.51, 19.05, 800, 200, "residential"),
    ]
    # Measured annual means: highest next to the busy road, lowest far away.
    stations = [
        AirQualityStation("busy", 19.025, 47.5002, 40.0),
        AirQualityStation("quiet", 19.025, 47.5102, 24.0),
        AirQualityStation("middle", 19.025, 47.505, 18.0),
        AirQualityStation("far", 19.10, 47.56, 9.0),
    ]
    homes = PopulationGrid(
        lon=np.array([19.025, 19.025, 19.10]),
        lat=np.array([47.5003, 47.5103, 47.56]),
        residents=np.array([100.0, 200.0, 50.0]),
        source="t",
    )
    result = EnvironmentModel(links, stations, homes).run()
    assert result.r_squared > 0.8
    assert result.background_no2 > 0
    today, plan, response = (result.scenarios[k] for k in ("today", "plan", "plan_with_response"))
    assert plan.nox_kg < today.nox_kg and response.nox_kg < plan.nox_kg
    assert plan.population_weighted_no2 < today.population_weighted_no2
    assert result.noise_better_residents["plan"] == pytest.approx(200.0)
    assert result.no2_worse_residents["plan"] == pytest.approx(0.0)
    assert today.residents_noise_over_55 >= 300 - 50
