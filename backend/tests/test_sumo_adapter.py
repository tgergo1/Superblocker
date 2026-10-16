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
