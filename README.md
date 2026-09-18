# Scout Tree

## Quick Start

```bash
# Build the Docker image
make build

# Place your PMTiles files in data/
# (or symlink existing ones — see Data Ingestion below)

# Set the HTTP Basic Auth password — required, there is no default
export GIS_VIEWER_PASSWORD=...

# Start the viewer
make run

# Open http://localhost:8080
```

## Data Ingestion

The ingestion pipeline converts raw imagery and shapefiles into PMTiles:

```
raw_data/ --> scripts/ingest.py --> data/
```

### Raster (satellite imagery)

```bash
# Build all raster layers (RGB, NED, NDVI)
docker compose run --rm viewer python3 scripts/ingest.py raster \
    --type all --input-dir raw_data/imagery/ --output-dir data/

# Or build individually
docker compose run --rm viewer python3 scripts/ingest.py raster \
    --type rgb --input-dir raw_data/imagery/ --output-dir data/
```

### Vector (detection polygons)

```bash
docker compose run --rm viewer python3 scripts/ingest.py vector \
    --input raw_data/merged.shp --output data/detections_full.pmtiles
```

### Standalone scripts

```bash
python scripts/build_raster.py --all --input-dir raw_data/imagery/ --output-dir data/
python scripts/build_vector.py --input raw_data/merged.shp --output data/detections.pmtiles
python scripts/make_extent_kml.py --gml-dir /datasets/airbus_pull --output data/imagery_extent.kml
```

## Development (DooD)

For live-editing from the dev container using Docker-outside-of-Docker:

```bash
make dev
# or:
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

This mounts the entire repo into the container, so changes to `frontend/index.html` are visible on browser refresh.

## Deployment

### Local / LAN

```bash
make run
# Access at http://localhost:8080 or http://<server-ip>:8080
```

### Production (nginx)

The included `nginx.conf` can be used as a drop-in for production deployments serving static files with proper CORS headers and caching.

### Deployment configuration

`frontend/config.js` holds the site-specific settings — default grove, initial
map centre, coordinate example. It ships blank; fill it in at deploy time
alongside `data/groves.geojson`. Left blank, the viewer fits the map to
whatever imagery is present and selects no grove.

### Static hosting

Since the viewer is entirely static files (HTML + PMTiles), it can be deployed to:
- Cloudflare R2 / Pages
- AWS S3 + CloudFront
- Any static file host supporting HTTP Range Requests

## URL Parameters

- `?dev=1` — Use small development subset PMTiles (faster loading for testing)
- Default (no param) — Use full-extent `*_full.pmtiles` files
