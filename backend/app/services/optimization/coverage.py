"""Rank superblocks by benefit per boundary-road burden and keep the best share.

A city-wide plan treats every cell alike, but the benefit of a superblock
(through traffic taken off its interior streets) adds up roughly linearly,
while delay on the boundary roads grows with the fourth power of their load
in the BPR curve. So the last superblocks cost far more than the first. This
module scores each superblock from a full-plan simulation and builds plans
that keep only the best-ranked share, so a coverage frontier can be traced:
benefit against boundary delay and exposure from a few superblocks to all.

Score = interior vehicle-km removed per peak hour / (1 + vehicle-km added on
the boundary roads that touch it, shared between the superblocks that touch
each road). Both come from one assignment of the full plan, so the score is a
screening ranking, not a re-optimisation of each subset.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from shapely.geometry import LineString, shape
from shapely.strtree import STRtree

from app.models.schemas import CityPartition

# Boundary links within this distance (degrees, about 25 m) of a superblock
# polygon count as its boundary roads.
_TOUCH_DEG = 0.00025


def superblock_scores(
    partition: CityPartition, links: Iterable[Mapping]
) -> dict[str, dict[str, float]]:
    """Per superblock: interior vehicle-km removed, boundary vehicle-km added, score."""
    polygons = []
    ids = []
    for superblock in partition.superblocks:
        try:
            polygons.append(shape(superblock.geometry))
            ids.append(superblock.id)
        except (ValueError, TypeError, AttributeError):
            continue
    tree = STRtree(polygons)
    removed: dict[str, float] = defaultdict(float)
    added_share: dict[str, float] = defaultdict(float)
    for link in links:
        coordinates = link.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        line = LineString(coordinates)
        km = float(link.get("length_m") or 0.0) / 1000.0 or line.length * 80.0
        before = float(link.get("baseline_vph") or 0.0)
        after = float(link.get("planned_vph") or 0.0)
        if link.get("is_boundary_road"):
            touching = [ids[i] for i in tree.query(line, predicate="dwithin", distance=_TOUCH_DEG)]
            if touching and after > before:
                share = (after - before) * km / len(touching)
                for sid in touching:
                    added_share[sid] += share
            continue
        middle = line.interpolate(0.5, normalized=True)
        inside = tree.query(middle, predicate="within")
        if len(inside) and before > after:
            removed[ids[inside[0]]] += (before - after) * km
    return {
        sid: {
            "interior_vkm_removed": removed[sid],
            "boundary_vkm_added": added_share[sid],
            "score": removed[sid] / (1.0 + added_share[sid]),
        }
        for sid in ids
    }


def select_coverage(
    partition: CityPartition,
    scores: Mapping[str, Mapping[str, float]],
    share: float,
) -> CityPartition:
    """A copy of ``partition`` keeping the best-scored ``share`` of its superblocks.

    Superblocks left out keep today's streets: their filters, one-way changes
    and gates are dropped. Dropping changes only reopens streets, so no
    junction loses access; some kept superblocks may regain a crossing route
    through a dropped neighbour, which the simulator's routing then shows.
    """
    if not 0 < share <= 1:
        raise ValueError("share must be in (0, 1]")
    ranked = sorted(
        partition.superblocks,
        key=lambda sb: (-scores.get(sb.id, {}).get("score", 0.0), sb.id),
    )
    keep = max(1, round(share * len(ranked)))
    return partition.model_copy(update={"superblocks": ranked[:keep]})
