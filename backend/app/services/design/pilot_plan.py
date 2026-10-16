"""Rank superblocks for a pilot-first rollout and estimate interior safety.

Barcelona, Paris and the London low-traffic neighbourhoods started with cheap,
reversible trials (planters, removable bollards, signs) and made the
successful ones permanent. This module splits each superblock's measures into
a trial stage and a build stage with indicative costs, estimates the change in
interior injury risk from removed through traffic and 30 km/h speeds, and
ranks superblocks by benefit so a city knows where to start.

Every figure here is a screening estimate, outside the implementation gate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from app.models.schemas import (
    EmergencyAccessSummary,
    ModificationType,
    PilotCandidate,
    PilotPlanSummary,
    PublicSpaceSummary,
    SchoolStreetSummary,
    StreetModification,
    TrafficImpactEstimate,
)
from app.services.impact.boundary_impact import SuperblockImpactInput

# Indicative 2026 EUR unit costs for central European cities. Trial measures
# are removable; build measures are kerbed and landscaped.
TRIAL_COST_EUR = {
    "planter": 2_000.0,
    "emergency_bollard": 2_500.0,
    "bus_gate": 40_000.0,
    "camera_gate": 40_000.0,
    "school_street": 3_000.0,
    ModificationType.MODAL_FILTER: 2_000.0,
    ModificationType.ONE_WAY: 1_500.0,
    ModificationType.TWO_WAY: 2_500.0,
    ModificationType.TURN_RESTRICTION: 1_000.0,
    ModificationType.FULL_CLOSURE: 3_000.0,
}
BUILD_COST_EUR = {
    "planter": 20_000.0,
    "emergency_bollard": 8_000.0,
    "bus_gate": 40_000.0,
    "camera_gate": 40_000.0,
    "school_street": 3_000.0,
    ModificationType.MODAL_FILTER: 20_000.0,
    ModificationType.ONE_WAY: 1_500.0,
    ModificationType.TWO_WAY: 2_500.0,
    ModificationType.TURN_RESTRICTION: 1_000.0,
    ModificationType.FULL_CLOSURE: 30_000.0,
}

# Injury crashes scale roughly with the square of mean speed (Nilsson power
# model). Moving a 50 km/h street to a 30 km/h limit lowers mean speed by
# roughly 8-20%, so the speed factor on injury risk is 0.64-0.85.
SPEED_FACTOR_STRONG = 0.64
SPEED_FACTOR_WEAK = 0.85


def _cost_key(modification: StreetModification):
    return modification.filter_type or modification.modification_type


def trial_cost(modification: StreetModification) -> float:
    return TRIAL_COST_EUR.get(_cost_key(modification), 2_000.0)


def build_cost(modification: StreetModification) -> float:
    if modification.estimated_capital_cost is not None:
        return float(modification.estimated_capital_cost)
    return BUILD_COST_EUR.get(_cost_key(modification), 20_000.0)


def _normalized(values: dict[str, float]) -> dict[str, float]:
    """Percentile rank in [0, 1]; zero stays zero so absent benefits score nothing.

    Ranks rather than value / max keep one outlier, such as a forest cell with
    hectares of calmed carriageway, from flattening every other score.
    """
    positive = sorted(value for value in values.values() if value > 0)
    if not positive:
        return dict.fromkeys(values, 0.0)
    count = len(positive)
    first_index: dict[float, int] = {}
    last_index: dict[float, int] = {}
    for index, value in enumerate(positive):
        first_index.setdefault(value, index)
        last_index[value] = index
    return {
        key: (((first_index[value] + last_index[value]) / 2 + 1) / count if value > 0 else 0.0)
        for key, value in values.items()
    }


class PilotPlanner:
    def __init__(self, top_n: int = 25):
        self.top_n = top_n

    def plan(
        self,
        superblocks: Iterable[SuperblockImpactInput],
        traffic_impact: TrafficImpactEstimate | None = None,
        public_space: PublicSpaceSummary | None = None,
        school_streets: SchoolStreetSummary | None = None,
        emergency_access: EmergencyAccessSummary | None = None,
        centroids: dict[str, tuple[float, float]] | None = None,
        areas_ha: dict[str, float] | None = None,
        residents: dict[str, float] | None = None,
    ) -> PilotPlanSummary:
        """Rank superblocks for a trial.

        With ``residents`` from a population grid, residents per hectare
        replaces calmed street length per hectare as the density measure.
        """
        blocks = list(superblocks)
        centroids = centroids or {}
        areas_ha = areas_ha or {}
        traffic = {
            item.superblock_id: item
            for item in (traffic_impact.superblocks if traffic_impact else [])
        }
        space = {
            item.superblock_id: item for item in (public_space.superblocks if public_space else [])
        }
        schools: dict[str, int] = defaultdict(int)
        for school in school_streets.schools if school_streets else []:
            if school.setting == "interior" and school.superblock_id:
                schools[school.superblock_id] += 1
        emergency_delay: dict[str, float] = {}
        threshold = emergency_access.threshold_seconds if emergency_access else 60.0
        for result in emergency_access.results if emergency_access else []:
            if result.planned_seconds is not None:
                emergency_delay[result.superblock_id] = max(
                    0.0, result.planned_seconds - result.baseline_seconds
                )

        displaced = {
            b.superblock_id: (
                traffic[b.superblock_id].displaced_peak_vph if b.superblock_id in traffic else 0.0
            )
            for b in blocks
        }
        reclaim = {
            b.superblock_id: (
                space[b.superblock_id].reclaimable_m2 if b.superblock_id in space else 0.0
            )
            for b in blocks
        }
        detour = {
            b.superblock_id: (
                traffic[b.superblock_id].mean_detour_seconds if b.superblock_id in traffic else 0.0
            )
            for b in blocks
        }
        school_counts = {b.superblock_id: float(schools.get(b.superblock_id, 0)) for b in blocks}
        reduction_share = {
            b.superblock_id: (
                traffic[b.superblock_id].interior_traffic_reduction_percent / 100.0
                if b.superblock_id in traffic
                else 0.0
            )
            for b in blocks
        }
        # Calmed street length per hectare separates dense urban grids, where
        # most people live, from hillside and industrial cells.
        density = {
            b.superblock_id: (
                space[b.superblock_id].calmed_street_km / areas_ha[b.superblock_id]
                if b.superblock_id in space and areas_ha.get(b.superblock_id, 0) > 0
                else 0.0
            )
            for b in blocks
        }
        n_reduction = _normalized(reduction_share)
        if residents:
            density = {
                b.superblock_id: (
                    residents.get(b.superblock_id, 0.0) / areas_ha[b.superblock_id]
                    if areas_ha.get(b.superblock_id, 0) > 0
                    else 0.0
                )
                for b in blocks
            }
        n_density = _normalized(density)
        n_displaced = _normalized(displaced)
        n_reclaim = _normalized(reclaim)
        n_detour = _normalized(detour)
        n_schools = _normalized(school_counts)

        candidates: list[PilotCandidate] = []
        for block in blocks:
            sid = block.superblock_id
            reduction = reduction_share[sid]
            remaining = 1.0 - reduction
            injury_low = (1.0 - remaining * SPEED_FACTOR_WEAK) * 100.0
            injury_high = (1.0 - remaining * SPEED_FACTOR_STRONG) * 100.0
            benefit = (
                0.25 * n_reduction[sid]
                + 0.15 * n_displaced[sid]
                + 0.15 * n_reclaim[sid]
                + 0.15 * n_density[sid]
                + 0.15 * n_schools[sid]
                + 0.15 * ((injury_low + injury_high) / 200.0)
            )
            delay = emergency_delay.get(sid, 0.0)
            score = benefit * (1.0 - 0.15 * n_detour[sid]) * (0.5 if delay > threshold else 1.0)
            trial = sum(trial_cost(item) for item in block.modifications)
            build = sum(build_cost(item) for item in block.modifications)
            lat, lon = centroids.get(sid, (None, None))
            candidates.append(
                PilotCandidate(
                    superblock_id=sid,
                    score=round(score * 100.0, 1),
                    trial_measures=len(block.modifications),
                    trial_cost_eur=round(trial),
                    build_cost_eur=round(build),
                    displaced_peak_vph=round(displaced[sid], 1),
                    interior_injury_reduction_low_percent=round(injury_low, 1),
                    interior_injury_reduction_high_percent=round(injury_high, 1),
                    school_streets=int(school_counts[sid]),
                    reclaimable_m2=round(reclaim[sid]),
                    mean_detour_seconds=round(detour[sid], 1),
                    emergency_delay_seconds=round(delay, 1) if sid in emergency_delay else None,
                    residents=round(residents[sid]) if residents and sid in residents else None,
                    centroid_lat=lat,
                    centroid_lon=lon,
                )
            )

        candidates.sort(key=lambda item: (-item.score, item.trial_cost_eur, item.superblock_id))
        for rank, item in enumerate(candidates, start=1):
            item.rank = rank
        top = candidates[: self.top_n]
        return PilotPlanSummary(
            total_trial_cost_eur=round(sum(item.trial_cost_eur for item in candidates)),
            total_build_cost_eur=round(sum(item.build_cost_eur for item in candidates)),
            top_trial_cost_eur=round(sum(item.trial_cost_eur for item in top)),
            mean_injury_reduction_low_percent=round(
                sum(item.interior_injury_reduction_low_percent for item in candidates)
                / max(len(candidates), 1),
                1,
            ),
            mean_injury_reduction_high_percent=round(
                sum(item.interior_injury_reduction_high_percent for item in candidates)
                / max(len(candidates), 1),
                1,
            ),
            candidates=top,
            limitations=[
                "Costs are indicative central European unit rates, not quotes.",
                "Injury change combines removed through traffic with a 30 km/h limit using "
                "the speed power model; it is a relative range, not a crash forecast.",
                "The ranking weighs the interior traffic cut (25%), traffic removed, "
                "reclaimable space, "
                + ("residents per hectare" if residents else "street density")
                + ", school streets and safety (15% each) by percentile, and is lowered "
                "for long detours or slower emergency response.",
            ],
        )
