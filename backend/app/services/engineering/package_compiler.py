"""Compile graph modifications into a costed draft field package."""

from __future__ import annotations

import hashlib
from collections import defaultdict

import networkx as nx

from app.models.schemas import (
    BillOfQuantityItem,
    Coordinates,
    EngineeringInstruction,
    EngineeringPackage,
    ModificationType,
    StreetModification,
)
from app.services.optimization.scenario_optimizer import (
    DEFAULT_CAPITAL_COST,
    DEFAULT_MAINTENANCE_COST,
)

TREATMENTS = {
    ModificationType.MODAL_FILTER: "Removable physical modal filter with signed exemptions",
    ModificationType.ONE_WAY: "Traffic-order and signing change to one-way operation",
    ModificationType.TWO_WAY: "Traffic-order, marking, and junction change to two-way operation",
    ModificationType.FULL_CLOSURE: "Kerbed or landscaped full motor-vehicle closure",
    ModificationType.TURN_RESTRICTION: "Signed and marked prohibited turning movement",
}
SIGNS = {
    ModificationType.MODAL_FILTER: [
        "motor vehicle prohibition",
        "exemption plate",
        "advance warning",
    ],
    ModificationType.ONE_WAY: ["one-way", "no entry", "direction arrows"],
    ModificationType.TWO_WAY: [
        "two-way traffic",
        "centre/edge markings",
        "junction priority review",
    ],
    ModificationType.FULL_CLOSURE: [
        "road closed",
        "advance diversion",
        "reflective hazard markers",
    ],
    ModificationType.TURN_RESTRICTION: ["prohibited turn", "advance lane/movement warning"],
}


class EngineeringPackageCompiler:
    """Generate reviewable instructions without claiming construction approval."""

    def __init__(self, graph: nx.MultiDiGraph) -> None:
        self.graph = graph

    def compile(
        self,
        plan_id: str,
        modifications: list[StreetModification],
        *,
        jurisdiction: str,
        currency: str = "EUR",
    ) -> EngineeringPackage:
        instructions = [self._instruction(index, item) for index, item in enumerate(modifications)]
        grouped: dict[str, list[EngineeringInstruction]] = defaultdict(list)
        for instruction in instructions:
            grouped[instruction.treatment].append(instruction)
        bill = []
        for index, (treatment, group) in enumerate(sorted(grouped.items())):
            total = sum(item.capital_cost for item in group)
            bill.append(
                BillOfQuantityItem(
                    item_code=f"SB-{index + 1:03d}",
                    description=treatment,
                    quantity=float(len(group)),
                    unit="location",
                    unit_cost=total / len(group),
                    total_cost=total,
                    cost_basis=(
                        "Scenario-specific estimate"
                        if all(
                            modifications[instructions.index(item)].estimated_capital_cost
                            is not None
                            for item in group
                        )
                        else "Transparent screening default; replace with local schedule of rates"
                    ),
                )
            )
        features = [
            {
                "type": "Feature",
                "geometry": (
                    {
                        "type": "Point",
                        "coordinates": [instruction.coordinates.lon, instruction.coordinates.lat],
                    }
                    if instruction.coordinates
                    else None
                ),
                "properties": {
                    "instruction_id": instruction.instruction_id,
                    "osm_id": instruction.osm_id,
                    "street_name": instruction.street_name,
                    "affected_movement": instruction.affected_movement,
                    "treatment": instruction.treatment,
                    "field_verification_required": instruction.field_verification_required,
                },
            }
            for instruction in instructions
        ]
        blockers = []
        if any(instruction.coordinates is None for instruction in instructions):
            blockers.append("At least one action has no exact mapped intervention coordinate")
        if any(
            modification.estimated_capital_cost is None
            or modification.annual_maintenance_cost is None
            for modification in modifications
        ):
            blockers.append("Local bills of quantity and schedule-of-rates costs are missing")
        blockers.extend(
            [
                "Field survey and utility/drainage clash checks are required",
                "Swept-path, visibility, accessibility, and temporary traffic management designs are required",
                "Applicable traffic orders and road-manager approvals are not part of this draft",
            ]
        )
        package_material = repr((plan_id, [item.instruction_id for item in instructions])).encode()
        return EngineeringPackage(
            package_id=hashlib.sha256(package_material).hexdigest()[:24],
            plan_id=plan_id,
            jurisdiction=jurisdiction,
            currency=currency,
            instructions=instructions,
            bill_of_quantities=bill,
            total_capital_cost=sum(item.capital_cost for item in instructions),
            total_annual_maintenance_cost=sum(
                item.annual_maintenance_cost for item in instructions
            ),
            gis_feature_collection={"type": "FeatureCollection", "features": features},
            blockers=blockers,
        )

    def _instruction(
        self,
        index: int,
        modification: StreetModification,
    ) -> EngineeringInstruction:
        edge_data = (
            self.graph[modification.u][modification.v].get(modification.key, {})
            if self.graph.has_edge(modification.u, modification.v)
            else {}
        )
        location = modification.filter_location
        geometry = edge_data.get("geometry")
        if location is None and geometry is not None:
            midpoint = geometry.interpolate(0.5, normalized=True)
            location = Coordinates(lat=midpoint.y, lon=midpoint.x)
        if location is None:
            u_data = self.graph.nodes.get(modification.u, {})
            v_data = self.graph.nodes.get(modification.v, {})
            if {"x", "y"}.issubset(u_data) and {"x", "y"}.issubset(v_data):
                location = Coordinates(
                    lat=(float(u_data["y"]) + float(v_data["y"])) / 2,
                    lon=(float(u_data["x"]) + float(v_data["x"])) / 2,
                )
        name = modification.name or str(edge_data.get("name") or f"OSM way {modification.osm_id}")
        treatment = (
            modification.construction_treatment or TREATMENTS[modification.modification_type]
        )
        movement = (
            f"{modification.turn_from_osm_id} via {modification.via_node_id} to {modification.turn_to_osm_id}"
            if modification.modification_type == ModificationType.TURN_RESTRICTION
            else modification.direction or f"edge {modification.u} to {modification.v}"
        )
        emergency_operation = None
        if modification.emergency_override_required:
            emergency_operation = "Engineer a fail-safe removable/controlled access device and agree its operating protocol with emergency services."
        elif modification.allowed_vehicle_classes:
            emergency_operation = (
                "Emergency access is permitted only if listed in the verified vehicle-class design."
            )
        instruction_id = hashlib.sha256(
            repr((index, modification.u, modification.v, modification.key, treatment)).encode()
        ).hexdigest()[:20]
        return EngineeringInstruction(
            instruction_id=instruction_id,
            osm_id=modification.osm_id,
            street_name=name,
            coordinates=location,
            affected_movement=movement,
            treatment=treatment,
            signs_and_markings=SIGNS[modification.modification_type],
            allowed_modes=modification.allowed_modes,
            allowed_vehicle_classes=modification.allowed_vehicle_classes,
            time_windows=modification.time_windows,
            emergency_operation=emergency_operation,
            construction_notes=[
                "Confirm exact chainage and available public-highway extent on site",
                "Check drainage, utilities, frontage, loading, parking, and cycle/footway continuity",
                "Prepare staged temporary traffic management and an accessible diversion",
            ],
            inspection_checks=[
                "Signs and markings match the approved traffic order",
                "Required clear width and swept path are unobstructed",
                "Visibility, lighting, drainage, and emergency operation have been field tested",
            ],
            capital_cost=(
                modification.estimated_capital_cost
                if modification.estimated_capital_cost is not None
                else DEFAULT_CAPITAL_COST[modification.modification_type.value]
            ),
            annual_maintenance_cost=(
                modification.annual_maintenance_cost
                if modification.annual_maintenance_cost is not None
                else DEFAULT_MAINTENANCE_COST[modification.modification_type.value]
            ),
        )
