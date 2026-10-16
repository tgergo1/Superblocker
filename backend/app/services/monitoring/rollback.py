"""Evaluate pre-registered pilot thresholds without changing street state."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from app.models.schemas import (
    MonitoringEvaluation,
    MonitoringMetricDefinition,
    MonitoringObservation,
)


def evaluate_monitoring(
    *,
    plan_id: str,
    evaluated_at: str,
    definitions: Iterable[MonitoringMetricDefinition],
    observations: Iterable[MonitoringObservation],
) -> MonitoringEvaluation:
    """Return continue/adapt/rollback from sample-weighted measurements."""
    definitions = list(definitions)
    by_metric: dict[str, list[MonitoringObservation]] = defaultdict(list)
    for observation in observations:
        by_metric[observation.metric_id].append(observation)
    changes: dict[str, float] = {}
    adapt_ids: list[str] = []
    rollback_ids: list[str] = []
    missing: list[str] = []
    reasons: list[str] = []
    for definition in definitions:
        matching = by_metric.get(definition.metric_id, [])
        sample_count = sum(item.sample_count for item in matching)
        if sample_count < definition.minimum_samples:
            missing.append(definition.metric_id)
            continue
        observed = sum(item.value * item.sample_count for item in matching) / sample_count
        if abs(definition.baseline_value) <= 1e-9:
            change = observed - definition.baseline_value
            reasons.append(
                f"{definition.metric_id}: baseline is zero; thresholds are applied to absolute change"
            )
        else:
            change = (observed - definition.baseline_value) / abs(definition.baseline_value) * 100
        changes[definition.metric_id] = change
        harm = (
            change
            if definition.direction == "increase_is_harm"
            else -change
            if definition.direction == "decrease_is_harm"
            else abs(change)
        )
        if harm >= definition.rollback_threshold_percent:
            rollback_ids.append(definition.metric_id)
            reasons.append(f"{definition.name} crossed its pre-registered rollback threshold")
        elif harm >= definition.adapt_threshold_percent:
            adapt_ids.append(definition.metric_id)
            reasons.append(f"{definition.name} crossed its pre-registered adapt threshold")
    if missing:
        action = "insufficient_data"
        reasons.append("Insufficient samples for: " + ", ".join(sorted(missing)))
    elif rollback_ids:
        action = "rollback"
    elif adapt_ids:
        action = "adapt"
    else:
        action = "continue"
        reasons.append("No pre-registered adapt or rollback threshold was crossed")
    return MonitoringEvaluation(
        plan_id=plan_id,
        evaluated_at=evaluated_at,
        action=action,
        metric_changes_percent=changes,
        triggered_metric_ids=sorted(set(rollback_ids or adapt_ids)),
        reasons=reasons,
    )
