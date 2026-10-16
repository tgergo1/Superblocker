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

from app.models.schemas import (
    HealthEstimate,
    HealthSettings,
    SuperblockHealth,
    TrafficImpactEstimate,
)

# Below this remaining share the energy rule overstates the change, since
# access traffic, buses and deliveries keep running.
MIN_REMAINING_TRAFFIC_SHARE = 0.05


def volume_noise_change_db(remaining_share: float) -> float:
    """Level change in dB when traffic is scaled to ``remaining_share``."""
    return 10.0 * math.log10(max(remaining_share, MIN_REMAINING_TRAFFIC_SHARE))


class HealthEstimator:
    def __init__(self, settings: HealthSettings | None = None):
        self.settings = settings or HealthSettings()

    def estimate(self, traffic_impact: TrafficImpactEstimate) -> HealthEstimate:
        settings = self.settings
        superblocks: list[SuperblockHealth] = []
        for item in traffic_impact.superblocks:
            reduction = item.interior_traffic_reduction_percent / 100.0
            volume_db = volume_noise_change_db(1.0 - reduction)
            superblocks.append(
                SuperblockHealth(
                    superblock_id=item.superblock_id,
                    interior_noise_change_db_low=round(
                        volume_db - settings.speed_noise_reduction_db_low, 1
                    ),
                    interior_noise_change_db_high=round(
                        volume_db - settings.speed_noise_reduction_db_high, 1
                    ),
                    interior_no2_change_percent_low=round(
                        -100.0 * reduction * settings.local_traffic_no2_share_low, 1
                    ),
                    interior_no2_change_percent_high=round(
                        -100.0 * reduction * settings.local_traffic_no2_share_high, 1
                    ),
                )
            )

        boundary_increases = [
            volume_noise_change_db(1.0 + road.percent_increase_central / 100.0)
            for road in traffic_impact.boundary_roads
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
                f"{settings.speed_noise_reduction_db_high:.0f} dB for 30 km/h; it is not a "
                "noise map.",
                "NO2 assumes local traffic causes "
                f"{settings.local_traffic_no2_share_low:.0%}-"
                f"{settings.local_traffic_no2_share_high:.0%} of street-level NO2; check it "
                "against the OKIR monitoring stations before quoting a figure.",
                "Boundary roads are 'noticeably louder' at 3 dB or more, roughly a doubling "
                "of traffic; the change on main roads is relative to their modelled load.",
            ],
        )
