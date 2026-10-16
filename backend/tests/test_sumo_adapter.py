from xml.etree import ElementTree as ET

from app.models.schemas import SimulationStatus
from app.services.simulation.sumo_adapter import SumoAdapter
from tests.test_network_assignment import assignment_graph, demand_model


def test_sumo_export_writes_network_connections_and_demand(tmp_path):
    result = SumoAdapter(assignment_graph()).export(demand_model(), [], tmp_path)

    assert result.status == SimulationStatus.EXPORTED
    assert len(result.generated_files) == 5
    edges = ET.parse(tmp_path / "network.edg.xml").getroot()
    connections = ET.parse(tmp_path / "network.con.xml").getroot()
    demand = ET.parse(tmp_path / "demand.rou.xml").getroot()
    assert len(edges.findall("edge")) == 4
    assert connections.findall("connection")
    assert demand.find("flow").attrib["number"] == "100"


def test_sumo_run_reports_missing_toolchain_without_fabricating_results(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.simulation.sumo_adapter.shutil.which", lambda _name: None)

    result = SumoAdapter(assignment_graph()).run(demand_model(), [], tmp_path)

    assert result.status == SimulationStatus.TOOL_UNAVAILABLE
    assert result.return_code is None
    assert not (tmp_path / "summary.xml").exists()
    assert any("not all installed" in item for item in result.limitations)


def test_sumo_export_includes_reverse_edges_from_two_way_repairs(tmp_path):
    from app.models.schemas import ModificationType, StreetModification

    repair = StreetModification(u=1, v=2, osm_id=10, modification_type=ModificationType.TWO_WAY)
    SumoAdapter(assignment_graph()).export(demand_model(), [repair], tmp_path)

    edges = ET.parse(tmp_path / "network.edg.xml").getroot()
    pairs = {(edge.attrib["from"], edge.attrib["to"]) for edge in edges.findall("edge")}
    assert ("2", "1") in pairs
