from app.models.schemas import StreetNetworkResponse

# Road capacity estimates (vehicles per hour per lane)
# Based on Highway Capacity Manual and urban planning literature
ROAD_CAPACITY = {
    "motorway": 2000,
    "motorway_link": 1800,
    "trunk": 1600,
    "trunk_link": 1400,
    "primary": 800,
    "primary_link": 700,
    "secondary": 600,
    "secondary_link": 500,
    "tertiary": 400,
    "tertiary_link": 350,
    "residential": 200,
    "living_street": 100,
    "unclassified": 300,
    "service": 150,
    "pedestrian": 0,
}

# Estimated average load factors (typical congestion levels)
# 0 = empty, 1 = at capacity
DEFAULT_LOAD_FACTORS = {
    "motorway": 0.70,
    "motorway_link": 0.65,
    "trunk": 0.65,
    "trunk_link": 0.60,
    "primary": 0.60,
    "primary_link": 0.55,
    "secondary": 0.50,
    "secondary_link": 0.45,
    "tertiary": 0.40,
    "tertiary_link": 0.35,
    "residential": 0.30,
    "living_street": 0.20,
    "unclassified": 0.35,
    "service": 0.25,
    "pedestrian": 0.0,
}


def estimate_traffic(network: StreetNetworkResponse) -> StreetNetworkResponse:
    """
    Add traffic capacity and load estimates to street network features.

    Uses road type classification to estimate:
    - capacity: maximum vehicles per hour
    - estimated_load: typical load factor (0-1)
    - volume: estimated vehicles per hour (capacity * load)

    Args:
        network: Street network response with features

    Returns:
        Network with added traffic properties
    """
    # Batch process all features for better performance
    max_intensity_volume = 1500  # vehicles/hour considered "intense"
    total_capacity = 0
    total_volume = 0
    
    # Pre-compute all traffic properties in a single pass
    for feature in network.features:
        props = feature["properties"]
        highway = props.get("highway", "unclassified")
        lanes = props.get("lanes", 1)

        # Calculate capacity
        base_capacity = ROAD_CAPACITY.get(highway, 200)
        capacity = base_capacity * lanes

        # Get load factor
        load_factor = DEFAULT_LOAD_FACTORS.get(highway, 0.3)

        # Estimate volume
        volume = int(capacity * load_factor)

        # Add to properties (batch update)
        props["capacity"] = capacity
        props["estimated_load"] = load_factor
        props["estimated_volume"] = volume
        props["traffic_intensity"] = min(100, int((volume / max_intensity_volume) * 100))
        
        # Accumulate totals in same pass
        total_capacity += capacity
        total_volume += volume

    # Update metadata
    network.metadata["total_capacity"] = total_capacity
    network.metadata["total_estimated_volume"] = total_volume
    network.metadata["average_load"] = round(total_volume / total_capacity, 3) if total_capacity > 0 else 0

    return network


def apply_real_traffic_data(
    network: StreetNetworkResponse,
    traffic_counts: dict[int, int],  # osmid -> actual volume
) -> StreetNetworkResponse:
    """
    Apply real traffic count data to the network.

    Args:
        network: Street network with estimated traffic
        traffic_counts: Dictionary mapping OSM way IDs to actual traffic volumes

    Returns:
        Network with updated traffic data where real counts are available
    """
    for feature in network.features:
        props = feature["properties"]
        osmid = props.get("osmid")

        if osmid and osmid in traffic_counts:
            real_volume = traffic_counts[osmid]
            props["estimated_volume"] = real_volume
            props["is_real_data"] = True

            # Recalculate load factor based on real data
            capacity = props.get("capacity", 1)
            props["estimated_load"] = min(1.0, real_volume / capacity) if capacity > 0 else 0

            # Update intensity
            max_intensity_volume = 1500
            props["traffic_intensity"] = min(100, int((real_volume / max_intensity_volume) * 100))
        else:
            props["is_real_data"] = False

    return network
