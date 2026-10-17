"""Air and noise from simulated traffic, and who lives with them.

Takes the street links of a capacity simulation (volumes and speeds with and
without the plan), turns them into emissions and sound on a fine grid, and
counts residents by the levels at their homes.

NO2. Each street emits NOx in proportion to its peak-hour volume times a
speed-dependent emission factor (a fleet average for cars plus a heavy
vehicle share by road type). Emissions are spread with two kernels: a
near-road one (exponential, ~30 m) for the steep gradient next to busy roads,
and an urban-scale one (~4 km) for the city's build-up. Annual mean NO2 at
measuring stations is then fitted as ``background + a * near + b * urban``
with non-negative least squares, so the model reproduces measured air rather
than an assumed share of traffic. The two decay lengths were chosen by
leave-one-out error on Budapest's ten stations (2025). The plan's NO2 is the same fit applied to
the plan's emissions. Chemistry is treated as linear, which overstates small
changes slightly in high-NOx streets where NO2 is ozone-limited.

Noise. Each street's peak-hour level comes from the UK CRTN method (volume,
speed, heavy share) at 10 m from the kerb. Sound from points along the street
spreads by inverse square from a minimum 10 m and is summed by energy. There
is no screening by buildings, so back façades are louder in the model than
in reality; the plan's change is the robust number, not the absolute level.

Everything runs as FFT convolutions on a metric grid, so a whole city with
hundreds of thousands of street sections takes seconds.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import nnls
from scipy.signal import fftconvolve

from app.models.simulation import SimulatedLink
from app.services.importers.open_data import AirQualityStation
from app.services.importers.population import PopulationGrid

METRES_PER_DEGREE_LAT = 110_540.0
METRES_PER_DEGREE_LON = 111_320.0

# Heavy vehicles (lorries, buses) as a share of traffic by road type.
HEAVY_SHARE = {
    "motorway": 0.12,
    "motorway_link": 0.10,
    "trunk": 0.10,
    "trunk_link": 0.08,
    "primary": 0.07,
    "primary_link": 0.06,
    "secondary": 0.05,
    "secondary_link": 0.05,
    "tertiary": 0.04,
    "tertiary_link": 0.04,
}
DEFAULT_HEAVY_SHARE = 0.02

SCENARIOS = ("today", "plan", "plan_with_response")


@dataclass(frozen=True)
class EnvironmentSettings:
    cell_m: float = 20.0
    near_decay_m: float = 30.0
    near_radius_m: float = 400.0
    urban_decay_m: float = 4_000.0
    urban_cell_m: float = 200.0
    urban_radius_m: float = 16_000.0
    noise_min_distance_m: float = 10.0
    # Sound from everything but road traffic (people, trams, plant, distant
    # roads), so a street going from 2 to 20 cars an hour is not a 10 dB change.
    ambient_db: float = 45.0
    noise_radius_m: float = 300.0
    sample_step_m: float = 10.0


def nox_g_per_km(speed_kph: np.ndarray, heavy_share: np.ndarray) -> np.ndarray:
    """Fleet NOx emission factor in g per vehicle-km.

    U-shaped in speed like COPERT's urban curves: stop-start traffic and fast
    traffic emit most. Only the shape matters for the NO2 result, since the
    station fit sets the overall scale.
    """
    v = np.clip(speed_kph, 5.0, 130.0)
    car = 0.12 + 3.0 / v + 0.00002 * v**2
    heavy = 1.0 + 60.0 / v + 0.0001 * v**2
    return (1.0 - heavy_share) * car + heavy_share * heavy


def crtn_laeq(flow_vph: np.ndarray, speed_kph: np.ndarray, heavy_share: np.ndarray) -> np.ndarray:
    """Hourly LAeq 10 m from the kerb (CRTN L10 hourly, minus 3 dB)."""
    q = np.maximum(flow_vph, 1.0)
    v = np.clip(speed_kph, 20.0, 130.0)
    p = heavy_share * 100.0
    l10 = (
        42.2
        + 10.0 * np.log10(q)
        + 33.0 * np.log10(v + 40.0 + 500.0 / v)
        + 10.0 * np.log10(1.0 + 5.0 * p / v)
        - 68.8
    )
    return l10 - 3.0


@dataclass
class _Grid:
    lon0: float
    lat0: float
    cell: float
    cos_lat: float
    nx: int
    ny: int

    def xy(self, lon, lat) -> tuple[np.ndarray, np.ndarray]:
        x = (np.asarray(lon) - self.lon0) * METRES_PER_DEGREE_LON * self.cos_lat
        y = (np.asarray(lat) - self.lat0) * METRES_PER_DEGREE_LAT
        return x, y

    def index(self, lon, lat) -> tuple[np.ndarray, np.ndarray]:
        x, y = self.xy(lon, lat)
        col = np.clip((x // self.cell).astype(np.int64), 0, self.nx - 1)
        row = np.clip((y // self.cell).astype(np.int64), 0, self.ny - 1)
        return row, col


@dataclass
class StationFit:
    name: str
    lon: float
    lat: float
    measured: float
    modelled: float
    left_out: float  # prediction with this station left out of the fit


@dataclass
class ScenarioExposure:
    name: str
    vehicle_km: float
    nox_kg_per_hour: float
    population_weighted_no2: float
    residents_no2_over_40: float
    residents_no2_over_20: float
    residents_no2_over_10: float
    population_weighted_noise_db: float
    residents_noise_over_55: float
    residents_noise_over_65: float
    residents_noise_over_70: float


@dataclass
class EnvironmentResult:
    background_no2: float
    near_coefficient: float
    urban_coefficient: float
    stations: list[StationFit]
    r_squared: float
    rmse: float
    leave_one_out_rmse: float
    residents: float
    scenarios: dict[str, ScenarioExposure]
    # Residents whose home gets clearly better or worse against today.
    no2_better_residents: dict[str, float] = field(default_factory=dict)
    no2_worse_residents: dict[str, float] = field(default_factory=dict)
    noise_better_residents: dict[str, float] = field(default_factory=dict)
    noise_worse_residents: dict[str, float] = field(default_factory=dict)
    # Fields on the grid, for maps: NO2 (ug/m3) and peak-hour LAeq (dB).
    no2: dict[str, np.ndarray] = field(default_factory=dict)
    noise: dict[str, np.ndarray] = field(default_factory=dict)
    grid: _Grid | None = None
    limitations: list[str] = field(default_factory=list)


def _flows(link: SimulatedLink, scenario: str) -> tuple[float, float]:
    if scenario == "today":
        return link.baseline_vph, link.baseline_speed_kph or link.free_speed_kph
    if scenario == "plan":
        return link.planned_vph, link.planned_speed_kph or link.free_speed_kph
    volume = link.planned_elastic_vph if link.planned_elastic_vph is not None else link.planned_vph
    speed = link.planned_elastic_speed_kph or link.planned_speed_kph or link.free_speed_kph
    return volume, speed


def _samples(
    links: Sequence[SimulatedLink], grid: _Grid, step: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Points every ``step`` metres along every link: link index, x, y."""
    owner: list[np.ndarray] = []
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    for number, link in enumerate(links):
        coords = np.asarray(link.coordinates, dtype=float)
        if coords.shape[0] < 2:
            continue
        x, y = grid.xy(coords[:, 0], coords[:, 1])
        seg = np.hypot(np.diff(x), np.diff(y))
        total = float(seg.sum())
        if total <= 0:
            continue
        count = max(1, round(total / step))
        at = (np.arange(count) + 0.5) * total / count
        cumulative = np.concatenate([[0.0], np.cumsum(seg)])
        xs.append(np.interp(at, cumulative, x))
        ys.append(np.interp(at, cumulative, y))
        owner.append(np.full(count, number))
    if not owner:
        return np.array([], dtype=np.int64), np.array([]), np.array([])
    return np.concatenate(owner), np.concatenate(xs), np.concatenate(ys)


def _kernel(cell: float, radius: float, weight) -> np.ndarray:
    half = math.ceil(radius / cell)
    offsets = np.arange(-half, half + 1) * cell
    distance = np.hypot(offsets[None, :], offsets[:, None])
    kernel = weight(distance)
    kernel[distance > radius] = 0.0
    return kernel


def _rasterize(rows, cols, values, shape) -> np.ndarray:
    out = np.zeros(shape)
    np.add.at(out, (rows, cols), values)
    return out


class EnvironmentModel:
    def __init__(
        self,
        links: Iterable[SimulatedLink],
        stations: Sequence[AirQualityStation],
        population: PopulationGrid | None,
        settings: EnvironmentSettings | None = None,
    ) -> None:
        self.links = [link for link in links if len(link.coordinates) >= 2]
        self.stations = list(stations)
        self.population = population
        self.settings = settings or EnvironmentSettings()

    def run(self, scenarios: Sequence[str] = SCENARIOS) -> EnvironmentResult:
        settings = self.settings
        if not self.links:
            raise ValueError("no simulated links to model")
        if len(self.stations) < 3:
            raise ValueError("at least three NO2 stations are needed to calibrate")
        coords = np.vstack([np.asarray(link.coordinates) for link in self.links])
        lon_all = [coords[:, 0], np.array([s.lon for s in self.stations])]
        lat_all = [coords[:, 1], np.array([s.lat for s in self.stations])]
        if self.population is not None:
            lon_all.append(self.population.lon)
            lat_all.append(self.population.lat)
        lon = np.concatenate(lon_all)
        lat = np.concatenate(lat_all)
        margin_deg = 0.01
        lon0, lat0 = float(lon.min()) - margin_deg, float(lat.min()) - margin_deg
        cos_lat = math.cos(math.radians(float(lat.mean())))
        width = (float(lon.max()) + margin_deg - lon0) * METRES_PER_DEGREE_LON * cos_lat
        height = (float(lat.max()) + margin_deg - lat0) * METRES_PER_DEGREE_LAT
        grid = _Grid(
            lon0=lon0,
            lat0=lat0,
            cell=settings.cell_m,
            cos_lat=cos_lat,
            nx=int(width // settings.cell_m) + 1,
            ny=int(height // settings.cell_m) + 1,
        )
        owner, sx, sy = _samples(self.links, grid, settings.sample_step_m)
        rows = np.clip((sy // grid.cell).astype(np.int64), 0, grid.ny - 1)
        cols = np.clip((sx // grid.cell).astype(np.int64), 0, grid.nx - 1)
        lengths = np.array([link.length_m for link in self.links])
        per_sample = np.bincount(owner, minlength=len(self.links)).astype(float)
        # Each sample stands for its share of the link's length.
        sample_length = (np.maximum(lengths, 1.0) / np.maximum(per_sample, 1.0))[owner]
        heavy = np.array(
            [HEAVY_SHARE.get(link.highway, DEFAULT_HEAVY_SHARE) for link in self.links]
        )

        near_kernel = _kernel(
            grid.cell,
            settings.near_radius_m,
            lambda d: np.exp(-d / settings.near_decay_m),
        )
        near_kernel /= near_kernel.sum()
        factor = max(1, round(settings.urban_cell_m / grid.cell))
        coarse_shape = (grid.ny // factor + 1, grid.nx // factor + 1)
        urban_kernel = _kernel(
            grid.cell * factor,
            settings.urban_radius_m,
            lambda d: np.exp(-d / settings.urban_decay_m),
        )
        urban_kernel /= urban_kernel.sum()
        noise_kernel = _kernel(
            grid.cell,
            settings.noise_radius_m,
            lambda d: 13.5 / (math.pi * np.maximum(d, settings.noise_min_distance_m) ** 2),
        )

        fields: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        totals: dict[str, tuple[float, float]] = {}
        for scenario in scenarios:
            volume, speed = np.array([_flows(link, scenario) for link in self.links]).T
            # g per hour per metre of street; totals in kg per hour.
            emission = volume * nox_g_per_km(speed, heavy) / 1000.0
            emitted = _rasterize(rows, cols, emission[owner] * sample_length, (grid.ny, grid.nx))
            near = fftconvolve(emitted, near_kernel, mode="same")
            coarse = _rasterize(
                rows // factor, cols // factor, emission[owner] * sample_length, coarse_shape
            )
            urban = fftconvolve(coarse, urban_kernel, mode="same")
            level = crtn_laeq(volume, speed, heavy)
            power = _rasterize(
                rows,
                cols,
                10.0 ** (level[owner] / 10.0) * sample_length,
                (grid.ny, grid.nx),
            )
            intensity = np.maximum(fftconvolve(power, noise_kernel, mode="same"), 0.0)
            fields[scenario] = (near, urban, intensity)
            totals[scenario] = (
                float((volume * lengths).sum() / 1000.0),
                float((emission * lengths).sum() / 1000.0),
            )

        # Calibrate on today's air.
        near0, urban0, _ = fields["today"]
        s_row, s_col = grid.index([s.lon for s in self.stations], [s.lat for s in self.stations])
        measured = np.array([s.no2_ugm3 for s in self.stations])
        design = np.column_stack(
            [
                np.ones(len(self.stations)),
                near0[s_row, s_col],
                urban0[s_row // factor, s_col // factor],
            ]
        )
        scale = design.max(axis=0)
        scale[scale <= 0] = 1.0
        coefficients, _ = nnls(design / scale, measured)
        coefficients = coefficients / scale
        modelled = design @ coefficients
        left_out = np.zeros_like(measured)
        for i in range(len(measured)):
            keep = np.arange(len(measured)) != i
            fit, _ = nnls(design[keep] / scale, measured[keep])
            left_out[i] = float(design[i] @ (fit / scale))
        residual = measured - modelled
        total_ss = float(((measured - measured.mean()) ** 2).sum())
        r_squared = 1.0 - float((residual**2).sum()) / total_ss if total_ss > 0 else 0.0

        background, a_near, a_urban = (float(c) for c in coefficients)
        no2_fields: dict[str, np.ndarray] = {}
        noise_fields: dict[str, np.ndarray] = {}
        for scenario, (near, urban, intensity) in fields.items():
            urban_fine = np.repeat(np.repeat(urban, factor, axis=0), factor, axis=1)[
                : grid.ny, : grid.nx
            ]
            no2_fields[scenario] = background + a_near * near + a_urban * urban_fine
            noise_fields[scenario] = 10.0 * np.log10(
                intensity + 10.0 ** (settings.ambient_db / 10.0)
            )

        result = EnvironmentResult(
            background_no2=background,
            near_coefficient=a_near,
            urban_coefficient=a_urban,
            stations=[
                StationFit(s.name, s.lon, s.lat, s.no2_ugm3, float(m), float(lo))
                for s, m, lo in zip(self.stations, modelled, left_out, strict=True)
            ],
            r_squared=r_squared,
            rmse=float(np.sqrt((residual**2).mean())),
            leave_one_out_rmse=float(np.sqrt(((measured - left_out) ** 2).mean())),
            residents=0.0,
            scenarios={},
            no2=no2_fields,
            noise=noise_fields,
            grid=grid,
            limitations=[
                "NO2 is fitted to measured annual means at the stations; the emission "
                "factors set only how traffic is spread by speed and road type.",
                "Noise is peak-hour LAeq by CRTN with no screening by buildings, so it is "
                "loudest-façade exposure; changes are more reliable than absolute levels.",
            ],
        )
        self._exposure(result, totals)
        return result

    def _exposure(self, result: EnvironmentResult, totals: Mapping[str, tuple[float, float]]):
        grid = result.grid
        if self.population is None or self.population.residents.size == 0 or grid is None:
            for scenario, (vkm, nox) in totals.items():
                result.scenarios[scenario] = ScenarioExposure(scenario, vkm, nox, *([0.0] * 8))
            result.limitations.append("No population grid: exposure counts are zero.")
            return
        row, col = grid.index(self.population.lon, self.population.lat)
        people = self.population.residents
        result.residents = float(people.sum())
        values = {
            scenario: (result.no2[scenario][row, col], result.noise[scenario][row, col])
            for scenario in result.no2
        }
        for scenario, (no2, noise) in values.items():
            vkm, nox = totals[scenario]
            result.scenarios[scenario] = ScenarioExposure(
                name=scenario,
                vehicle_km=vkm,
                nox_kg_per_hour=nox,
                population_weighted_no2=float(np.average(no2, weights=people)),
                residents_no2_over_40=float(people[no2 > 40].sum()),
                residents_no2_over_20=float(people[no2 > 20].sum()),
                residents_no2_over_10=float(people[no2 > 10].sum()),
                population_weighted_noise_db=float(np.average(noise, weights=people)),
                residents_noise_over_55=float(people[noise >= 55].sum()),
                residents_noise_over_65=float(people[noise >= 65].sum()),
                residents_noise_over_70=float(people[noise >= 70].sum()),
            )
        today_no2, today_noise = values["today"]
        for scenario, (no2, noise) in values.items():
            if scenario == "today":
                continue
            result.no2_better_residents[scenario] = float(people[no2 <= today_no2 - 1].sum())
            result.no2_worse_residents[scenario] = float(people[no2 >= today_no2 + 1].sum())
            result.noise_better_residents[scenario] = float(people[noise <= today_noise - 3].sum())
            result.noise_worse_residents[scenario] = float(people[noise >= today_noise + 3].sum())
