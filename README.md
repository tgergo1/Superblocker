# Superblocker

A web application for identifying and visualizing potential superblocks in any city worldwide. Combines OpenStreetMap data with traffic modeling to help urban planners, researchers, and citizens explore pedestrian-friendly urban transformations.

## Features

- **City Search**: Search and select any city or area worldwide using Nominatim geocoding
- **Street Network Visualization**: View the complete road network with classification-based coloring
- **Traffic Estimation**: Estimate traffic capacity and load based on road classification
- **Multiple Color Modes**: View roads by type (hierarchy) or by estimated traffic intensity
- **Interactive Map**: Pan, zoom, and hover over roads for detailed information

### Coming Soon

- Superblock detection algorithms (Barcelona-style grid, graph-based bounded areas)
- Street direction reorientation planning
- Export to PDF/GeoJSON
- Real traffic data import

## Tech Stack

### Backend
- Python 3.11+ with FastAPI
- OSMnx for street network analysis
- NetworkX for graph algorithms
- GeoPandas/Shapely for geospatial operations

### Frontend
- React 18 with TypeScript
- deck.gl for high-performance map visualization
- react-map-gl for Mapbox integration
- TanStack Query for data fetching

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- npm or yarn

### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Run the server
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Copy environment file
cp .env.example .env

# Start development server
npm run dev
```

The app will be available at `http://localhost:5173`.

### Docker Setup (Alternative)

```bash
docker-compose up --build
```

This will start both backend and frontend services.

## Usage

1. **Search for a city**: Type a city name (e.g., "Barcelona", "Budapest") in the search box
2. **Select from results**: Click on a search result to zoom to that location
3. **Load street network**: Click "Load Street Network" to fetch road data from OpenStreetMap
4. **Explore the map**: Hover over roads to see details (name, type, capacity, traffic estimates)
5. **Toggle color mode**: Switch between "Road Type" and "Traffic" coloring

## API Endpoints

### Search
- `GET /api/v1/search?q={query}` - Search for places

### Analysis
- `POST /api/v1/network` - Fetch street network for a bounding box
- `POST /api/v1/analyze` - Analyze area for superblock candidates (coming soon)

## Project Structure

```
superblocker/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app entry
│   │   ├── api/routes/          # API endpoints
│   │   ├── core/                # Configuration
│   │   ├── models/              # Pydantic schemas
│   │   ├── services/            # Business logic
│   │   └── utils/               # Utilities
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/          # React components
│   │   ├── hooks/               # Custom hooks
│   │   ├── services/            # API client
│   │   └── types/               # TypeScript types
│   └── package.json
└── docker-compose.yml
```

## Configuration

### Backend (.env)
- `DEBUG` - Enable debug mode
- `CORS_ORIGINS` - Allowed CORS origins
- `NOMINATIM_USER_AGENT` - User agent for Nominatim requests

### Frontend (.env)
- `VITE_API_URL` - Backend API URL
- `VITE_MAPBOX_TOKEN` - Optional Mapbox token for premium basemaps

## License

GPL-3.0 - See [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
