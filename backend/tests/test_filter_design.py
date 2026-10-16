import httpx

from app.models.schemas import (
    BoundingBox,
    FilterDesignSettings,
    ModificationType,
    OsmContext,
    StreetModification,
    TransitRoute,
    VehicleClass,
)
from app.services.context import osm_context
from app.services.context.osm_context import build_overpass_query, parse_overpass_context
from app.services.design.filter_design import FilterDesigner
from app.services.network.turn_graph import TurnGraphBuilder
from tests.test_boundary_impact import cell_graph

BUS_7 = TransitRoute(relation_id=9, mode="bus", ref="7", name="7 bus", way_ids=[201])


def mod(u, v, osm_id, kind=ModificationType.MODAL_FILTER, **extra):
    return StreetModification(u=u, v=v, osm_id=osm_id, modification_type=kind, **extra)


def test_filters_on_bus_routes_become_bus_gates_and_others_emergency_bollards():
    designer = FilterDesigner(cell_graph(), OsmContext(transit_routes=[BUS_7]))
    designed = designer.design(
        [
            mod(6, 7, 201, ModificationType.FULL_CLOSURE),
            mod(6, 8, 203),
            mod(5, 6, 200, ModificationType.ONE_WAY, direction="u_to_v"),
        ]
    )
    gate, bollard, one_way = designed
    assert gate.modification_type == ModificationType.MODAL_FILTER
    assert gate.filter_type == "bus_gate"
    assert gate.transit_route_refs == ["bus 7"]
    assert VehicleClass.BUS in gate.allowed_vehicle_classes
    assert VehicleClass.CAR not in gate.allowed_vehicle_classes
    assert bollard.filter_type == "emergency_bollard"
    assert bollard.allowed_vehicle_classes == [VehicleClass.EMERGENCY]
    assert bollard.emergency_override_required is True
    assert one_way.transit_route_refs == []  # 200 is not on the route

    summary = designer.finish()
    assert summary.bus_gates == 1
    assert summary.closures_converted_to_gates == 1
    assert summary.emergency_bollards == 1
    assert summary.protected_route_refs == ["bus 7"]

    # Cars are blocked exactly as before; buses keep the direct link.
    builder = TurnGraphBuilder(cell_graph())
    car = builder._prepare_graph(VehicleClass.CAR, designed)
    bus = builder._prepare_graph(VehicleClass.BUS, designed)
    assert not car.has_edge(6, 7) and not car.has_edge(7, 6)
    assert bus.has_edge(6, 7) and bus.has_edge(7, 6)


def test_one_way_on_transit_gets_contraflow_bus_lane_and_planters_when_configured():
    designer = FilterDesigner(
        cell_graph(),
        OsmContext(transit_routes=[BUS_7]),
        FilterDesignSettings(emergency_passable_filters=False),
    )
    one_way, planter = designer.design(
        [mod(6, 7, 201, ModificationType.ONE_WAY, direction="u_to_v"), mod(6, 8, 203)]
    )
    assert "contraflow bus lane" in one_way.construction_treatment
    assert planter.filter_type == "planter"
    assert planter.allowed_vehicle_classes == []
    assert designer.finish().one_way_changes_on_transit == 1


def test_missing_context_is_reported():
    designer = FilterDesigner(cell_graph(), None)
    assert designer.design([mod(6, 7, 201)])[0].filter_type == "emergency_bollard"
    assert designer.finish().limitations


def test_overpass_parsing_and_query():
    payload = {
        "elements": [
            {
                "type": "relation",
                "id": 1,
                "tags": {"type": "route", "route": "trolleybus", "ref": "75"},
                "members": [
                    {"type": "way", "ref": 11, "role": ""},
                    {"type": "way", "ref": 12, "role": "platform"},
                    {"type": "node", "ref": 13, "role": "stop"},
                ],
            },
            {"type": "node", "id": 2, "lat": 47.5, "lon": 19.0, "tags": {"amenity": "school"}},
            {
                "type": "way",
                "id": 3,
                "center": {"lat": 47.51, "lon": 19.01},
                "tags": {"emergency": "ambulance_station", "name": "Mentőállomás"},
            },
            {"type": "node", "id": 4, "lat": 47.5, "lon": 19.0, "tags": {"amenity": "cafe"}},
        ]
    }
    context = parse_overpass_context(payload)
    assert context.transit_routes[0].way_ids == [11]
    assert context.transit_routes[0].ref == "75"
    assert [item.kind for item in context.facilities] == ["school", "ambulance_station"]
    assert context.transit_way_ids() == {11}
    query = build_overpass_query(BoundingBox(north=47.6, south=47.4, east=19.2, west=18.9))
    assert "trolleybus" in query and "47.4,18.9,47.6,19.2" in query


def test_fetch_failure_returns_limitation(monkeypatch, tmp_path):
    class NoCache:
        def get(self, *_args):
            return None

        def set(self, *_args, **_kwargs):
            return True

    def refuse(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(osm_context, "get_cache_service", lambda: NoCache())
    monkeypatch.setattr(osm_context.httpx, "post", refuse)
    context = osm_context.fetch_osm_context(
        BoundingBox(north=47.6, south=47.4, east=19.2, west=18.9)
    )
    assert context.transit_routes == []
    assert context.limitations
