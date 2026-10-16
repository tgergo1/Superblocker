import json
import zipfile

from app.models.schemas import VehicleClass
from app.services.importers.evidence import (
    import_access_targets,
    import_authoritative_roads,
    import_demand_model,
    import_gtfs_operational_requirements,
    import_traffic_observations,
)


def test_demand_and_directional_traffic_importers():
    demand = import_demand_model(
        zones_csv=(
            "zone_id,name,lat,lon,connector_node_ids,external\n"
            "A,Inner,47.0,19.0,1;2,false\n"
            "B,Cordon,47.1,19.1,3,true\n"
        ),
        time_slices_csv=("time_slice_id,start_time,end_time,duration_minutes\nam,08:00,09:00,60\n"),
        matrix_csv=(
            "origin_zone_id,destination_zone_id,time_slice_id,vehicle_class,trips\n"
            "A,B,am,heavy_goods,12.5\n"
        ),
        model_id="efm-test",
        version="1",
        source="Model authority",
    )
    observations = import_traffic_observations(
        "osm_id,volume_vph,source,interval_start,direction,lane,vehicle_class,speed_kph,dataset_id\n"
        "10,250,Counter,am,u_to_v,2,heavy_goods,31.5,traffic-v1\n"
    )

    assert demand.zones[0].connector_node_ids == [1, 2]
    assert demand.zones[1].external
    assert demand.matrix[0].vehicle_class == VehicleClass.HEAVY_GOODS
    assert observations[0].lane == 2
    assert observations[0].speed_kph == 31.5


def test_geojson_access_and_authoritative_road_importers():
    access = import_access_targets(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "id": "door-1",
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [19.0, 47.0]},
                        "properties": {
                            "kind": "delivery",
                            "permitted_vehicle_classes": ["light_goods"],
                            "critical": True,
                        },
                    }
                ],
            }
        ),
        source="Entrance survey",
    )
    roads = import_authoritative_roads(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"stable_id": "road-1", "lanes_forward": 2},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[19.0, 47.0], [19.001, 47.0]],
                        },
                    }
                ],
            }
        ),
        source="Road authority",
        rule_version="2026-09-02",
    )

    assert access[0].critical
    assert access[0].permitted_vehicle_classes == [VehicleClass.LIGHT_GOODS]
    assert roads[0].stable_id == "road-1"
    assert roads[0].lanes_forward == 2


def test_gtfs_import_creates_route_level_hard_requirements(tmp_path):
    archive_path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "stops.txt",
            "stop_id,stop_name,stop_lat,stop_lon\nA,Origin,47.0,19.0\nB,Destination,47.1,19.1\n",
        )
        archive.writestr("trips.txt", "route_id,service_id,trip_id\nR1,weekday,T1\n")
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,08:00:00,08:00:00,A,1\n"
            "T1,08:30:00,08:30:00,B,2\n",
        )

    requirements = import_gtfs_operational_requirements(
        archive_path,
        source="Operator GTFS 2026-09-02",
    )

    assert len(requirements) == 1
    assert requirements[0].vehicle_class == VehicleClass.BUS
    assert requirements[0].maximum_travel_time_minutes == 31.5
