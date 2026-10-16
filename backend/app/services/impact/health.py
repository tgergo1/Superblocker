"""Screening estimate of noise and NO2 changes from a superblock plan.

Removing through traffic and moving to a 30 km/h limit lowers interior noise
and traffic-related NO2. The diverted traffic raises both on the boundary
roads, so the estimate reports the two sides together.

Noise uses the energy rule that halving traffic lowers the level by about
3 dB, plus a range for the lower speed. NO2 scales the interior traffic cut by
an assumed local-traffic share of street-level NO2. Both are relative changes,
not dispersion or noise-map results, and stay outside the implementation gate.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import networkx as nx

from app.models.schemas import (
    HealthEstimate,
    HealthSettings,
    SuperblockHealth,
    TrafficImpactEstimate,
)
from app.services.impact.boundary_impact import SuperblockImpactInput
from app.services.osm_service import normalize_maxspeed

# Below this remaining share the energy rule overstates the change, since
# access traffic, buses and deliveries keep running.
MIN_REMAINING_TRAFFIC_SHARE = 0.05

# Hungarian built-up areas default to 50 km/h unless signed otherwise.
DEFAULT_URBAN_LIMIT_KPH = 50
CALM_STREET_TYPES = {"living_street", "service", "pedestrian", "track"}
CALMED_LIMIT_KPH = 30

# A road with almost no modelled traffic still has some; this floor keeps a
# new flow on it from reading as an unbounded increase.
MIN_BASELINE_PEAK_VPH = 10.0


def volume_noise_change_db(remaining_share: float) -> float:
    """Level change in dB when traffic is scaled to ``remaining_share``."""
    return 10.0 * math.log10(max(remaining_share, MIN_REMAINING_TRAFFIC_SHARE))


def boundary_noise_increase_db(baseline_vph: float, added_vph: float) -> float:
    """Level increase on a road whose peak flow grows by ``added_vph``."""
    baseline = max(baseline_vph, MIN_BASELINE_PEAK_VPH)
    return volume_noise_change_db(1.0 + max(added_vph, 0.0) / baseline)


class HealthEstimator:
    def __init__(
        self, graph: nx.MultiDiGraph | None = None, settings: HealthSettings | None = None
    ):
        self.graph = graph
        self.settings = settings or HealthSettings()

    def _share_slowed(self, block: SuperblockImpactInput | None) -> float:
        """Share of interior street length whose limit drops to 30 km/h.

        Streets already at 30 km/h or below, and living or service streets,
        get no speed benefit. Without the graph every street is assumed to
        change.
        """
        if self.graph is None or block is None:
            return 1.0
        total = slowed = 0.0
        for u, v, key in block.interior_edges:
            if not self.graph.has_edge(u, v, key):
                continue
            data = self.graph[u][v][key]
            length = float(data.get("length", 0) or 0)
            highway = data.get("highway", "residential")
            if isinstance(highway, (list, tuple, set)):
                highway = next(iter(highway), "residential")
            limit = normalize_maxspeed(data.get("maxspeed"))
            if limit is None:
                limit = (
                    CALMED_LIMIT_KPH if highway in CALM_STREET_TYPES else DEFAULT_URBAN_LIMIT_KPH
                )
            total += length
            if limit > CALMED_LIMIT_KPH:
                slowed += length
        return slowed / total if total > 0 else 0.0

    def estimate(
        self,
        traffic_impact: TrafficImpactEstimate,
        superblocks_in: Iterable[SuperblockImpactInput] = (),
    ) -> HealthEstimate:
        settings = self.settings
        inputs = {block.superblock_id: block for block in superblocks_in}
        superblocks: list[SuperblockHealth] = []
        for item in traffic_impact.superblocks:
            reduction = item.interior_traffic_reduction_percent / 100.0
            volume_db = volume_noise_change_db(1.0 - reduction)
            share = self._share_slowed(inputs.get(item.superblock_id))
            superblocks.append(
                SuperblockHealth(
                    superblock_id=item.superblock_id,
                    interior_noise_change_db_low=round(
                        volume_db - share * settings.speed_noise_reduction_db_low, 1
                    ),
                    interior_noise_change_db_high=round(
                        volume_db - share * settings.speed_noise_reduction_db_high, 1
                    ),
                    interior_no2_change_percent_low=round(
                        -100.0 * reduction * settings.local_traffic_no2_share_low, 1
                    ),
                    interior_no2_change_percent_high=round(
                        -100.0 * reduction * settings.local_traffic_no2_share_high, 1
                    ),
                )
            )

        scenarios = traffic_impact.settings.scenarios
        names = [scenario.name for scenario in scenarios]
        central = (
            traffic_impact.settings.central_scenario
            if traffic_impact.settings.central_scenario in names
            else (names[0] if names else "")
        )
        # Only main roads; minor boundary streets are reported by the traffic
        # estimate itself.
        boundary_increases = [
            boundary_noise_increase_db(
                road.baseline_peak_vph, road.added_peak_vph.get(central, 0.0)
            )
            for road in traffic_impact.boundary_roads
            if road.arterial
        ]
        count = max(len(superblocks), 1)
        return HealthEstimate(
            settings=settings,
            mean_interior_noise_change_db_low=round(
                sum(item.interior_noise_change_db_low for item in superblocks) / count, 1
            ),
            mean_interior_noise_change_db_high=round(
                sum(item.interior_noise_change_db_high for item in superblocks) / count, 1
            ),
            mean_interior_no2_change_percent_low=round(
                sum(item.interior_no2_change_percent_low for item in superblocks) / count, 1
            ),
            mean_interior_no2_change_percent_high=round(
                sum(item.interior_no2_change_percent_high for item in superblocks) / count, 1
            ),
            boundary_roads_noticeably_louder=sum(1 for db in boundary_increases if db >= 3.0),
            maximum_boundary_noise_increase_db=round(max(boundary_increases, default=0.0), 1),
            superblocks=superblocks,
            limitations=[
                "Noise uses the traffic-energy rule (half the traffic, about 3 dB quieter) "
                f"plus {settings.speed_noise_reduction_db_low:.0f}-"
                f"{settings.speed_noise_reduction_db_high:.0f} dB for 30 km/h on streets "
                "whose limit is above 30 km/h (untagged urban streets are taken as 50 km/h); "
                "it is not a noise map.",
                "NO2 assumes local traffic causes "
                f"{settings.local_traffic_no2_share_low:.0%}-"
                f"{settings.local_traffic_no2_share_high:.0%} of street-level NO2; check it "
                "against the OKIR monitoring stations before quoting a figure.",
                "Main roads are 'noticeably louder' at 3 dB or more, roughly a doubling of "
                "traffic, relative to their modelled peak load.",
            ],
        )
