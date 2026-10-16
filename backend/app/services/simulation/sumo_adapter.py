"""Export a legal, class-aware plan network and execute SUMO when available."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree as ET

import networkx as nx

from app.models.schemas import (
    DemandModel,
    SimulationRun,
    SimulationStatus,
    StreetModification,
    TurnRestriction,
    VehicleClass,
)
from app.services.network.turn_graph import TurnGraphBuilder

type EdgeRef = tuple[int, int, int]

SUMO_VEHICLE_CLASS = {
    VehicleClass.ALL_MOTOR: "passenger",
    VehicleClass.CAR: "passenger",
    VehicleClass.MOTORCYCLE: "motorcycle",
    VehicleClass.LIGHT_GOODS: "delivery",
    VehicleClass.HEAVY_GOODS: "truck",
    VehicleClass.BUS: "bus",
    VehicleClass.TAXI: "taxi",
    VehicleClass.REFUSE: "truck",
    VehicleClass.EMERGENCY: "emergency",
    VehicleClass.PERMIT: "passenger",
}


def _edge_id(ref: EdgeRef) -> str:
    return f"e_{ref[0]}_{ref[1]}_{ref[2]}"


def _seconds(value: str) -> int:
    parts = value.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0
    except (TypeError, ValueError):
        return 0
    return max(0, hour * 3_600 + minute * 60 + second)


def _first(value, fallback):
    if isinstance(value, (list, tuple, set)):
        return next(iter(value), fallback)
    return value if value is not None else fallback


class SumoAdapter:
    """Produce deterministic SUMO inputs and optionally execute the toolchain."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        restrictions: Iterable[TurnRestriction] = (),
    ) -> None:
        self.graph = graph
        self.restrictions = list(restrictions)

    @staticmethod
    def available() -> bool:
        return all(shutil.which(binary) for binary in ("netconvert", "duarouter", "sumo"))

    def export(
        self,
        demand: DemandModel,
        modifications: Iterable[StreetModification],
        output_directory: Path,
        *,
        network_state: str = "planned",
        random_seed: int = 42,
    ) -> SimulationRun:
        if network_state not in {"baseline", "planned"}:
            raise ValueError("network_state must be baseline or planned")
        modifications = list(modifications)
        output_directory = output_directory.resolve()
        output_directory.mkdir(parents=True, exist_ok=True)
        classes = sorted(
            {entry.vehicle_class for entry in demand.matrix}, key=lambda item: item.value
        )
        permitted: dict[EdgeRef, set[VehicleClass]] = defaultdict(set)
        connections: dict[tuple[EdgeRef, EdgeRef], set[VehicleClass]] = defaultdict(set)
        class_graphs = {}
        edge_attributes: dict[EdgeRef, dict] = {}
        for vehicle_class in classes:
            turn_graph = TurnGraphBuilder(self.graph, self.restrictions).build(
                vehicle_class,
                modifications,
            )
            class_graphs[vehicle_class] = turn_graph
            for ref, data in turn_graph.edge_data.items():
                permitted[ref].add(vehicle_class)
                # Planned two-way repairs add reverse edges that exist only in
                # the turn graph, so export attributes from there.
                edge_attributes.setdefault(ref, data)
            for incoming, outgoing in turn_graph.graph.edges:
                connections[(incoming, outgoing)].add(vehicle_class)

        nodes_path = output_directory / "network.nod.xml"
        edges_path = output_directory / "network.edg.xml"
        connections_path = output_directory / "network.con.xml"
        demand_path = output_directory / "demand.rou.xml"
        config_path = output_directory / "simulation.sumocfg"
        network_path = output_directory / "network.net.xml"
        routes_path = output_directory / "routes.rou.xml"
        summary_path = output_directory / "summary.xml"
        tripinfo_path = output_directory / "tripinfo.xml"

        used_nodes = {node for ref in permitted for node in ref[:2]}
        nodes_root = ET.Element("nodes")
        for node_id in sorted(used_nodes):
            data = self.graph.nodes[node_id]
            ET.SubElement(
                nodes_root,
                "node",
                id=str(node_id),
                x=str(float(data.get("x", 0.0))),
                y=str(float(data.get("y", 0.0))),
            )
        self._write_xml(nodes_path, nodes_root)

        edges_root = ET.Element("edges")
        for ref, allowed_classes in sorted(permitted.items()):
            data = edge_attributes[ref]
            attributes = {
                "id": _edge_id(ref),
                "from": str(ref[0]),
                "to": str(ref[1]),
                "numLanes": str(max(1, int(float(_first(data.get("lanes"), 1))))),
                "speed": str(self._speed_mps(data)),
                "allow": " ".join(sorted({SUMO_VEHICLE_CLASS[item] for item in allowed_classes})),
            }
            geometry = data.get("geometry")
            if geometry is not None and hasattr(geometry, "coords"):
                attributes["shape"] = " ".join(f"{x},{y}" for x, y in geometry.coords)
            ET.SubElement(edges_root, "edge", **attributes)
        self._write_xml(edges_path, edges_root)

        connections_root = ET.Element("connections")
        for (incoming, outgoing), allowed_classes in sorted(connections.items()):
            ET.SubElement(
                connections_root,
                "connection",
                **{
                    "from": _edge_id(incoming),
                    "to": _edge_id(outgoing),
                    "allow": " ".join(
                        sorted({SUMO_VEHICLE_CLASS[item] for item in allowed_classes})
                    ),
                },
            )
        self._write_xml(connections_path, connections_root)

        demand_root = ET.Element("routes")
        for vehicle_class in classes:
            ET.SubElement(
                demand_root,
                "vType",
                id=f"type_{vehicle_class.value}",
                vClass=SUMO_VEHICLE_CLASS[vehicle_class],
            )
        zone_connectors = self._zone_connectors(demand)
        slices = {item.time_slice_id: item for item in demand.time_slices}
        for index, entry in enumerate(demand.matrix):
            if entry.trips <= 0 or entry.origin_zone_id == entry.destination_zone_id:
                continue
            turn_graph = class_graphs[entry.vehicle_class]
            source_edges = sorted(
                set().union(
                    *(
                        turn_graph.outgoing_states(node)
                        for node in zone_connectors[entry.origin_zone_id]
                    )
                )
            )
            target_edges = sorted(
                set().union(
                    *(
                        turn_graph.incoming_states(node)
                        for node in zone_connectors[entry.destination_zone_id]
                    )
                )
            )
            if not source_edges or not target_edges:
                continue
            time_slice = slices[entry.time_slice_id]
            begin = _seconds(time_slice.start_time)
            end = begin + time_slice.duration_minutes * 60
            ET.SubElement(
                demand_root,
                "flow",
                id=f"flow_{index}",
                type=f"type_{entry.vehicle_class.value}",
                begin=str(begin),
                end=str(end),
                number=str(max(1, round(entry.trips))),
                **{"from": _edge_id(source_edges[0]), "to": _edge_id(target_edges[0])},
            )
        self._write_xml(demand_path, demand_root)

        config_root = ET.Element("configuration")
        input_element = ET.SubElement(config_root, "input")
        ET.SubElement(input_element, "net-file", value=network_path.name)
        ET.SubElement(input_element, "route-files", value=routes_path.name)
        time_element = ET.SubElement(config_root, "time")
        ET.SubElement(time_element, "begin", value="0")
        ET.SubElement(time_element, "end", value="172800")
        report_element = ET.SubElement(config_root, "report")
        ET.SubElement(report_element, "summary-output", value=summary_path.name)
        ET.SubElement(report_element, "tripinfo-output", value=tripinfo_path.name)
        self._write_xml(config_path, config_root)

        simulation_id = hashlib.sha256(
            repr((demand.model_id, demand.version, network_state, random_seed)).encode()
        ).hexdigest()[:24]
        files = [nodes_path, edges_path, connections_path, demand_path, config_path]
        return SimulationRun(
            simulation_id=simulation_id,
            status=SimulationStatus.EXPORTED,
            network_state=network_state,
            random_seed=random_seed,
            output_directory=str(output_directory),
            generated_files=[str(path) for path in files],
            limitations=[
                "The export requires calibrated signals, lane connections, and demand before engineering use."
            ],
        )

    def run(
        self,
        demand: DemandModel,
        modifications: Iterable[StreetModification],
        output_directory: Path,
        *,
        network_state: str = "planned",
        random_seed: int = 42,
        timeout_seconds: int = 3_600,
    ) -> SimulationRun:
        exported = self.export(
            demand,
            modifications,
            output_directory,
            network_state=network_state,
            random_seed=random_seed,
        )
        if not self.available():
            return exported.model_copy(
                update={
                    "status": SimulationStatus.TOOL_UNAVAILABLE,
                    "limitations": [
                        *exported.limitations,
                        "SUMO, netconvert, and duarouter are not all installed on this worker.",
                    ],
                }
            )
        output = Path(exported.output_directory or ".")
        network_command = [
            shutil.which("netconvert") or "netconvert",
            "--node-files",
            str(output / "network.nod.xml"),
            "--edge-files",
            str(output / "network.edg.xml"),
            "--connection-files",
            str(output / "network.con.xml"),
            "--output-file",
            str(output / "network.net.xml"),
            "--simple-projection",
            "--no-turnarounds",
        ]
        route_command = [
            shutil.which("duarouter") or "duarouter",
            "--net-file",
            str(output / "network.net.xml"),
            "--route-files",
            str(output / "demand.rou.xml"),
            "--output-file",
            str(output / "routes.rou.xml"),
            "--seed",
            str(random_seed),
        ]
        simulation_command = [
            shutil.which("sumo") or "sumo",
            "--configuration-file",
            str(output / "simulation.sumocfg"),
            "--seed",
            str(random_seed),
        ]
        completed_commands: list[str] = []
        stdout = ""
        stderr = ""
        return_code = 0
        try:
            version_result = subprocess.run(
                [shutil.which("sumo") or "sumo", "--version"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            version = (version_result.stdout or version_result.stderr).splitlines()[0][:200]
            for command in (network_command, route_command, simulation_command):
                result = subprocess.run(
                    command,
                    cwd=output,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                completed_commands.extend(command)
                stdout += result.stdout
                stderr += result.stderr
                return_code = result.returncode
                if result.returncode != 0:
                    return exported.model_copy(
                        update={
                            "status": SimulationStatus.FAILED,
                            "engine_version": version,
                            "command": completed_commands,
                            "return_code": return_code,
                            "stdout_tail": stdout[-4_000:],
                            "stderr_tail": stderr[-4_000:],
                        }
                    )
        except (OSError, subprocess.SubprocessError) as exc:
            return exported.model_copy(
                update={
                    "status": SimulationStatus.FAILED,
                    "command": completed_commands,
                    "return_code": return_code or None,
                    "stdout_tail": stdout[-4_000:] or None,
                    "stderr_tail": (stderr + f"\n{exc}")[-4_000:],
                }
            )
        generated = [
            *exported.generated_files,
            *(
                str(output / name)
                for name in ("network.net.xml", "routes.rou.xml", "summary.xml", "tripinfo.xml")
            ),
        ]
        return exported.model_copy(
            update={
                "status": SimulationStatus.COMPLETED,
                "engine_version": version,
                "generated_files": generated,
                "command": completed_commands,
                "return_code": return_code,
                "stdout_tail": stdout[-4_000:] or None,
                "stderr_tail": stderr[-4_000:] or None,
            }
        )

    def _zone_connectors(self, demand: DemandModel) -> dict[str, tuple[int, ...]]:
        graph_nodes = list(self.graph.nodes(data=True))
        if not graph_nodes:
            raise ValueError("cannot export an empty network")
        result = {}
        for zone in demand.zones:
            connectors = tuple(node for node in zone.connector_node_ids if node in self.graph)
            if not connectors:
                nearest = min(
                    graph_nodes,
                    key=lambda item: (
                        (float(item[1].get("x", 0)) - zone.centroid.lon) ** 2
                        + (float(item[1].get("y", 0)) - zone.centroid.lat) ** 2
                    ),
                )[0]
                connectors = (int(nearest),)
            result[zone.zone_id] = tuple(int(item) for item in connectors)
        return result

    @staticmethod
    def _speed_mps(data: dict) -> float:
        value = _first(data.get("maxspeed"), None)
        try:
            speed_kph = float(str(value).lower().replace("km/h", "").strip())
        except (TypeError, ValueError):
            speed_kph = {
                "motorway": 90.0,
                "trunk": 70.0,
                "primary": 50.0,
                "secondary": 50.0,
                "tertiary": 40.0,
                "living_street": 20.0,
                "service": 20.0,
            }.get(str(_first(data.get("highway"), "residential")), 30.0)
        return max(1.0, speed_kph / 3.6)

    @staticmethod
    def _write_xml(path: Path, root: ET.Element) -> None:
        ET.indent(root, space="  ")
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
